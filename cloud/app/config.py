"""Configuration from environment variables. Never hard-code secrets."""
from __future__ import annotations

import os


class Settings:
    # Auth
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "")
    JWT_ALG: str = "HS256"
    JWT_TTL_DAYS: int = int(os.environ.get("JWT_TTL_DAYS", "30"))

    # Database — sqlite for dev, Postgres (DATABASE_URL) in prod
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./capwizard_cloud.db")

    # Stripe
    STRIPE_SECRET_KEY: str = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_ID: str = os.environ.get("STRIPE_PRICE_ID", "")

    # URLs Stripe redirects to after checkout / portal. For a desktop app these
    # can be simple hosted pages that tell the user to return to the app.
    CHECKOUT_SUCCESS_URL: str = os.environ.get(
        "CHECKOUT_SUCCESS_URL", "https://example.com/success")
    CHECKOUT_CANCEL_URL: str = os.environ.get(
        "CHECKOUT_CANCEL_URL", "https://example.com/cancel")
    PORTAL_RETURN_URL: str = os.environ.get(
        "PORTAL_RETURN_URL", "https://example.com/account")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.STRIPE_SECRET_KEY and self.STRIPE_PRICE_ID)


settings = Settings()
# a default secret would let anyone who reads this repo sign tokens: refuse to start without one
if not settings.JWT_SECRET:
    raise RuntimeError("JWT_SECRET non impostato: mettilo nell'ambiente (vedi .env.example)")
