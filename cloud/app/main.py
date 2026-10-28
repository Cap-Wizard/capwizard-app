"""CapWizard — cloud licensing backend.

Accounts (email/password) + Stripe subscriptions + license validation.
Deploy this separately (Railway / Fly / Render / a VPS). The desktop app calls
it to log in and to check whether the subscription is active.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from . import billing
from .auth import (current_user, hash_password, make_token, verify_password)
from .config import settings
from .db import get_session, init_db
from .models import User

app = FastAPI(title="CapWizard — Cloud", version="0.1.0")

# Desktop clients call from a file:// (Origin: null) or localhost origin.
# Auth is via Bearer tokens (no cookies), so a permissive origin policy is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Credentials(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    token: str


class MeOut(BaseModel):
    email: str
    subscription_status: str
    current_period_end: float
    active: bool


def _me(user: User) -> MeOut:
    return MeOut(
        email=user.email,
        subscription_status=user.subscription_status,
        current_period_end=user.current_period_end,
        active=user.subscription_active(),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"ok": True, "stripe": settings.stripe_enabled}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/auth/register", response_model=TokenOut)
def register(creds: Credentials, session: Session = Depends(get_session)) -> TokenOut:
    email = creds.email.lower().strip()
    if len(creds.password) < 8:
        raise HTTPException(status_code=400, detail="La password deve avere almeno 8 caratteri.")
    if len(creds.password.encode("utf-8")) > 72:     # bcrypt reads only the first 72 bytes
        raise HTTPException(status_code=400, detail="La password può avere al massimo 72 caratteri.")
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email già registrata.")
    user = User(email=email, password_hash=hash_password(creds.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return TokenOut(token=make_token(user.id))


# Failed logins per account and per address: guessing a password takes ages, not minutes.
_FAILS: dict[str, deque] = defaultdict(deque)
_FAILS_LOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 10, 15 * 60


def _too_many(*keys: str) -> bool:
    now = time.time()
    with _FAILS_LOCK:
        for k in keys:
            q = _FAILS[k]
            while q and now - q[0] > FAIL_WINDOW:
                q.popleft()
            if len(q) >= MAX_FAILS:
                return True
    return False


def _failed(*keys: str) -> None:
    with _FAILS_LOCK:
        for k in keys:
            _FAILS[k].append(time.time())


@app.post("/auth/login", response_model=TokenOut)
def login(creds: Credentials, request: Request, session: Session = Depends(get_session)) -> TokenOut:
    email = creds.email.lower().strip()
    keys = (f"e:{email}", f"ip:{request.client.host if request.client else '?'}")
    if _too_many(*keys):
        raise HTTPException(status_code=429, detail="Troppi tentativi: riprova tra un quarto d'ora.")
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(creds.password, user.password_hash):
        _failed(*keys)
        raise HTTPException(status_code=401, detail="Email o password errati.")
    return TokenOut(token=make_token(user.id))


@app.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    return _me(user)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

class UrlOut(BaseModel):
    url: str


@app.post("/billing/checkout", response_model=UrlOut)
def checkout(user: User = Depends(current_user), session: Session = Depends(get_session)) -> UrlOut:
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Stripe non configurato sul server.")
    return UrlOut(url=billing.create_checkout_session(user, session))


@app.post("/billing/portal", response_model=UrlOut)
def portal(user: User = Depends(current_user), session: Session = Depends(get_session)) -> UrlOut:
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Stripe non configurato sul server.")
    return UrlOut(url=billing.create_portal_session(user, session))


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, session: Session = Depends(get_session)) -> dict:
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook(payload, sig)
    except ValueError:
        raise HTTPException(status_code=400, detail="webhook non valido")
    billing.handle_event(event, session)
    return {"received": True}
