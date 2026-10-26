"""FastAPI backend — lets the desktop UI (Electron) start and follow Claude Code runs.

Design
------
* Generation is long-running and blocking, so each request spins a background
  thread. The pipeline's `log` callback appends lines to the job's buffer.
* A WebSocket streams those log lines live (plus a backlog for late joiners)
  and finishes with a status frame.
* File selection happens in the Electron layer via native dialogs; the UI sends
  us plain filesystem paths.
* A reel is edited by Claude Code itself (see agent.py); the job also carries
  the step it has reached, which the app shows as progress.

Run standalone for development:
    capwizard-server            # binds 127.0.0.1:8765
    capwizard-server --port 9000
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import os
import platform
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from capwizard_app import registry
from capwizard_app.capcut import CAPCUT_PROJECTS_DIR, ai_ping, ai_status, find_claude_cli, is_video, template_kit

from capwizard_app import agent, aiservices, board, brain, scarico, vault


# ---------------------------------------------------------------------------
# Job manager
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    status: str = "pending"           # pending | running | done | error
    logs: list[str] = field(default_factory=list)
    result_path: str | None = None    # single-project result
    report_path: str | None = None    # AI decision report of the single project
    results: list[dict] = field(default_factory=list)  # batch results
    error: str | None = None
    step: int = 0                     # progress step reached (agent.STEPS, from 1)
    proc: object = None               # the running Claude Code process
    stopped: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def set_step(self, step: int) -> None:
        self.step = max(self.step, step)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "status": self.status,
                "logs": list(self.logs),
                "result_path": self.result_path, "report_path": self.report_path,
                "results": list(self.results), "error": self.error,
                "step": self.step,
            }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


manager = JobManager()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    input_path: str
    name: str
    template: str | None = None
    client: str | None = None         # id of the client's profile skill
    notes: str | None = None          # free instructions for Claude
    script_path: str | None = None
    model: str = "large-v3"
    language: str = "it"
    pacing: str = "fast"
    stabilize_clips: bool = False
    redistribute_sfx_enabled: bool = True
    add_emphasis: bool = True
    emphasis_count: int = 4
    drop_fillers: bool = True
    aggressive_fillers: bool = False
    intro_title: str | None = None
    clear_template_texts: bool = False
    ai_model: str = "opus"
    use_ai_review: bool = True
    ai_target_duration: float | None = None
    subtitle_max_words: int = 4
    subtitle_max_duration: float = 1.4
    subtitle_like_template: bool = True
    ai_zoom: bool = False
    sentence_transitions: bool = True
    fmt: str = "vertical"
    projects_dir: str | None = None
    broll_path: str | None = None     # b-roll the client gave in another folder
    reference: list[str] = []         # videos to imitate (rhythm, sounds, titles, subtitles)
    cine: bool = False                # "video cine": per-clip colour, b-roll on the cuts (vault clients)
    ai_media: bool = True             # Claude may use the AI services chosen in the app (b-roll, images…)


class BatchRequest(GenerateRequest):
    # In batch mode input_path/name are derived per subfolder, so make them
    # optional and add the batch-specific fields.
    input_path: str = ""
    name: str = ""
    parent_folder: str
    name_prefix: str = ""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="CapWizard API", version="0.2.0-alpha.2")

# Only the CapWizard window may drive this API. 127.0.0.1 alone is not enough: any web page open
# in a browser on this Mac can call it. Electron hands the backend a random token at launch
# (CAPWIZARD_TOKEN) and the renderer sends it on every request; a web page can't know it.
# The renderer loads from file://, whose origin is "null".
TOKEN = os.environ.get("CAPWIZARD_TOKEN", "")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"], allow_methods=["*"], allow_headers=["*"],
)


def _token_ok(given: str | None) -> bool:
    return not TOKEN or hmac.compare_digest(given or "", TOKEN)


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/") and path != "/api/health" and request.method != "OPTIONS"
            and not _token_ok(request.headers.get("x-capwizard-token"))):
        return JSONResponse({"detail": "non autorizzato"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": app.version}


def _whisper_cached_models() -> list[str]:
    """Best-effort list of already-downloaded faster-whisper models."""
    hub = Path.home() / ".cache/huggingface/hub"
    if not hub.exists():
        return []
    found = []
    for d in hub.iterdir():
        n = d.name.lower()
        if "faster-whisper" in n or "whisper" in n:
            # models--Systran--faster-whisper-large-v3 → large-v3
            found.append(d.name.split("faster-whisper-")[-1] if "faster-whisper-" in d.name else d.name)
    return found


@app.get("/api/system")
def system() -> dict:
    return {
        "os": platform.system().lower(),          # darwin | windows | linux
        "os_version": platform.version(),
        "ffmpeg": bool(vault.ffmpeg()),
        "ffprobe": bool(shutil.which("ffprobe")),
        "whisper_models_cached": _whisper_cached_models(),
        "ai": {
            "claude_cli": find_claude_cli(),
            "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        },
        "projects_dir": str(CAPCUT_PROJECTS_DIR),
        "projects_dir_exists": CAPCUT_PROJECTS_DIR.exists(),
    }


@app.get("/api/templates")
def templates(projects_dir: str | None = None) -> dict:
    base = Path(projects_dir) if projects_dir else CAPCUT_PROJECTS_DIR
    items: list[dict] = []
    if base.exists():
        for d in sorted(base.iterdir()):
            # (a project open in CapCut keeps its draft under another name for a while)
            if d.is_dir() and any((d / f).exists() for f in
                                  ("draft_info.json", "draft_info.json.bak", "template-2.tmp")):
                items.append({"name": d.name})
    default = next((t["name"] for t in items if t["name"].upper() == "CASA RIFUGIO"), None)
    return {"projects_dir": str(base), "default": default, "templates": items}


@app.get("/api/template-kit")
def template_kit_summary(name: str, projects_dir: str | None = None) -> dict:
    """What a template offers, as a checklist for the app: which reusable
    pieces it has and what to add to make it a complete kit for a client.
    (A quick read of the draft; what each text block is *for* is decided by the
    AI at generation time.)"""
    base = Path(projects_dir) if projects_dir else CAPCUT_PROJECTS_DIR
    try:
        kit = template_kit(name, base)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    def words(group: dict) -> str:
        return " · ".join(s["text"] for s in group["slots"])

    groups = kit["groups"]
    title = groups[0] if groups and groups[0]["start"] < 1.0 else None
    combo_title = next((c for c in kit.get("combos", []) if c["start"] < 1.0), None)
    if not title and combo_title:        # title built as compound clips (one or more, back to back)
        title = {"slots": [{"text": t} for c in kit["combos"] if c["start"] < 4.0 for t in c["texts"]]}
    rest = [g for g in groups if g is not title]
    lists = [g for g in rest if len({round(s["after"], 1) for s in g["slots"]}) >= 3]
    labels = [g for g in rest if g not in lists]
    checks = [
        {"key": "title", "label": "Titolo d'apertura", "ok": bool(title),
         "detail": words(title) if title else "",
         "hint": "Metti all'inizio del progetto i testi del titolo come li vuoi sempre: "
                 "anche su più righe, con la riga grande e un'emoji."},
        {"key": "labels", "label": "Titoletti", "ok": bool(labels),
         "detail": "  |  ".join(words(g) for g in labels[:3]),
         "hint": "Aggiungi dopo il titolo un titoletto di esempio, a una o due righe: "
                 "servirà per i capitoli e per i dati da far leggere."},
        {"key": "list", "label": "Elenco puntato", "ok": bool(lists),
         "detail": words(lists[0]) if lists else "",
         "hint": "Aggiungi un elenco: tre o più testi uno sotto l'altro che entrano "
                 "uno dopo l'altro. Verrà usato quando chi parla elenca più cose."},
        {"key": "captions", "label": "Sottotitoli", "ok": kit["captions"] > 0,
         "detail": f"{kit['captions']} di esempio" if kit["captions"] else "",
         "hint": "Aggiungi almeno un sottotitolo con lo stile del cliente (meglio tre o "
                 "più: l'app impara anche quanto farli lunghi)."},
        {"key": "sounds", "label": "Suoni", "ok": bool(kit["sounds"]),
         "detail": ", ".join(kit["sounds"][:6]) + ("…" if len(kit["sounds"]) > 6 else ""),
         "hint": "Metti sulla timeline i suoni che usi sempre, vicino ai testi con cui li "
                 "fai partire: l'AI impara l'abbinamento."},
        {"key": "music", "label": "Musica", "ok": bool(kit["music"]),
         "detail": ", ".join(kit["music"][:2]),
         "hint": "Aggiungi la base musicale che usi di solito per questo cliente."},
        {"key": "looks", "label": "Colore ed effetti", "ok": bool(kit["looks"]),
         "detail": ", ".join(kit["looks"][:4]),
         "hint": "Aggiungi dall'inizio la regolazione colore o i filtri del cliente: "
                 "vengono estesi a tutto il video."},
    ]
    return {"ok": True, "name": name, "checks": checks,
            "found": sum(c["ok"] for c in checks), "total": len(checks)}


def _do_generate(job: Job, req: GenerateRequest) -> tuple[str, str]:
    """Edit one reel with Claude Code. Returns (CapCut project path, report path)."""
    return agent.run_agent(job, req)


def _run_job(job: Job, req: GenerateRequest) -> None:
    job.status = "running"
    try:
        job.result_path, job.report_path = _do_generate(job, req)
        job.status = "done"
    except Exception as e:  # noqa: BLE001 — surface everything to the UI
        job.error = str(e)
        job.status = "error"
        job.log(f"ERRORE: {e}")
    brain.push_soon(f"Lezioni dal reel «{req.name}»")


def _project_subfolders(parent: Path) -> list[Path]:
    """Immediate subfolders of `parent` that contain at least one video."""
    if not parent.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(parent.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            if any(is_video(p) for p in d.iterdir()):
                out.append(d)
        except OSError:
            continue
    return out


def _run_batch(job: Job, req: BatchRequest) -> None:
    job.status = "running"
    try:
        parent = Path(req.parent_folder)
        subs = _project_subfolders(parent)
        if not subs:
            job.error = "Nessuna sottocartella con video trovata."
            job.status = "error"
            job.log(f"ERRORE: {job.error}")
            return

        job.log(f"▶ Modalità blocco: {len(subs)} progetti da generare")
        base = req.model_dump()
        base.pop("parent_folder", None)
        base.pop("name_prefix", None)

        for i, sub in enumerate(subs, 1):
            name = (f"{req.name_prefix} {sub.name}".strip()
                    if req.name_prefix else sub.name)
            job.log(f"\n▶▶ PROGETTO [{i}/{len(subs)}] {name}  ←  {sub.name}")
            if job.stopped:
                break
            job.step = 0
            try:
                one = GenerateRequest(**{**base, "input_path": str(sub), "name": name})
                out, report = _do_generate(job, one)
                job.results.append({"name": name, "path": out, "report": report, "ok": True})
            except Exception as e:  # one folder failing must not abort the batch
                job.log(f"ERRORE su «{name}»: {e}")
                job.results.append({"name": name, "path": None, "ok": False, "error": str(e)})

        ok = sum(1 for r in job.results if r["ok"])
        job.log(f"\n✓ Blocco completato: {ok}/{len(subs)} progetti creati")
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        job.error = str(e)
        job.status = "error"
        job.log(f"ERRORE: {e}")


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    job = manager.create()
    t = threading.Thread(target=_run_job, args=(job, req), daemon=True)
    t.start()
    return {"job_id": job.id}


@app.get("/api/clients")
def clients() -> dict:
    """Client profiles (skills with one client's rules) and the progress steps."""
    return {"clients": agent.list_clients(), "steps": agent.STEPS,
            "editor_skill": bool(agent.editor_skill()),
            "vault": {"enabled": vault.enabled(), "path": str(vault.VAULT), "agenzie": vault.agenzie()}}


class NewClientRequest(BaseModel):
    agenzia: str
    nome: str
    colore: str
    font: str = ""
    note: str = ""


class ClientParams(BaseModel):
    colore: str = ""
    font: str = ""
    base: str = ""
    regola: str = ""


class StudyRequest(BaseModel):
    agenzia: str
    cliente: str = ""
    colore: str = ""
    font: str = ""
    video: list[str] = []
    link: str = ""
    messaggio: str = ""
    screenshot: list[str] = []
    note: str = ""
    ai_model: str = "opus"


def _run_study(job: Job, req: StudyRequest) -> None:
    job.status = "running"
    try:
        agent.run_study(job, req.model_dump())
        if req.cliente.strip():       # the study wrote the client's note: now its CapCut template
            vault.assicura_template(req.agenzia.strip().upper(), req.cliente.strip().upper(), job.log)
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        job.error = str(e)
        job.status = "error"
        job.log(f"ERRORE: {e}")
    brain.push_soon("Studio di un'agenzia o di un cliente")


@app.post("/api/study")
def study(req: StudyRequest) -> dict:
    """New agency/client with material to study (reference videos, Instagram links, the agency's message, chat screenshots)."""
    job = manager.create()
    threading.Thread(target=_run_study, args=(job, req), daemon=True).start()
    return {"job_id": job.id}


class ScaricoRequest(BaseModel):
    clip: list[str] = []
    output: str = ""
    codice: str = ""
    pdp: str = ""
    note: str = ""
    ai_model: str = "sonnet"


class ScaricoExecRequest(BaseModel):
    piano: dict
    output: str
    shooting: str = ""
    mmaa: str = ""
    modo: str = "copia"           # copia | sposta


def _run_scarico(job: Job, fn, d: dict) -> None:
    job.status = "running"
    try:
        job.results = [fn(job, d)]
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        job.error = str(e)
        job.status = "error"
        job.log(f"ERRORE: {e}")


@app.get("/api/scarico/codici")
def scarico_codici() -> dict:
    return {"codici": scarico.client_codes(), "skill": str(scarico.skill_md() or "")}


@app.post("/api/scarico/analizza")
def scarico_analizza(req: ScaricoRequest) -> dict:
    """Smistamento clip, step 1: inventory + transcription, then Claude's plan. Touches no clip."""
    job = manager.create()
    threading.Thread(target=_run_scarico, args=(job, scarico.analyse, req.model_dump()), daemon=True).start()
    return {"job_id": job.id}


@app.post("/api/scarico/esegui")
def scarico_esegui(req: ScaricoExecRequest) -> dict:
    """Smistamento clip, step 2: rename and file the clips as in the confirmed plan."""
    job = manager.create()
    threading.Thread(target=_run_scarico, args=(job, scarico.execute, req.model_dump()), daemon=True).start()
    return {"job_id": job.id}


class NewAgencyRequest(BaseModel):
    nome: str
    campi: dict[str, str] = {}


@app.post("/api/agencies")
def agency_new(req: NewAgencyRequest) -> dict:
    """A new agency and how it edits, so Claude knows it before the first client."""
    if not vault.enabled():
        return {"ok": False, "msg": "Nessun vault Obsidian trovato."}
    out = vault.crea_agenzia(req.nome, req.campi)
    if out["ok"]:
        brain.push_soon(f"Nuova agenzia {out['agenzia']}")
    return out


@app.post("/api/clients")
def client_new(req: NewClientRequest) -> dict:
    """A new client of an agency already in the vault: colour (and font only if the client wants one)."""
    if not vault.enabled():
        return {"ok": False, "msg": "Nessun vault Obsidian trovato."}
    out = vault.crea_cliente(req.agenzia, req.nome, req.colore, req.font, req.note)
    brain.push_soon(f"Nuovo cliente {req.nome} ({req.agenzia})")
    return out


@app.get("/api/clients/{client_id:path}/params")
def client_params(client_id: str) -> dict:
    return vault.dati_cliente(client_id) if client_id.startswith(vault.PREFIX) else {"ok": False}


@app.put("/api/clients/{client_id:path}/params")
def client_params_save(client_id: str, req: ClientParams) -> dict:
    if not client_id.startswith(vault.PREFIX):
        return {"ok": False, "msg": "Solo i clienti del vault si modificano da qui: per un profilo skill apri il file."}
    out = vault.salva_cliente(client_id, req.colore, req.font, req.base, req.regola)
    brain.push_soon(f"Aggiorna {client_id[len(vault.PREFIX):]}")
    return out


@app.get("/api/brain")
def brain_status() -> dict:
    """Where the shared client repo is and how its last sync went."""
    return brain.status()


@app.post("/api/brain/sync")
def brain_sync() -> dict:
    """Pull now, link the client skills, then push what's pending."""
    notes = [brain.pull(), *brain.link_skills()]
    if brain.vault_dir():
        vault.VAULT = brain.vault_dir()
    notes.append(brain.push("Sincronizzazione manuale") or "niente da caricare")
    return {**brain.status(), "note": notes}


@app.get("/api/clients/{client_id:path}/rules")
def client_rules(client_id: str) -> dict:
    return {"text": agent.client_rules(client_id)}


VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".mts")
THUMBS = registry.app_data_dir() / "thumbs"


def _thumb(src: Path, height: int, at: float | None = None) -> str:
    """A small JPEG of a video frame (or of an image) as a data URL, cached."""
    THUMBS.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src}|{src.stat().st_mtime}|{height}|{at}".encode()).hexdigest()[:16]
    out = THUMBS / f"{key}.jpg"
    if not out.exists():
        args = [vault.ffmpeg() or "ffmpeg", "-v", "error", "-y"] + (["-ss", str(at)] if at is not None else []) + \
               ["-i", str(src), "-frames:v", "1", "-vf", f"scale=-2:{height}", str(out)]
        subprocess.run(args, capture_output=True, timeout=30)
    if not out.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()


def _duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


@app.get("/api/folder-info")
def folder_info(path: str) -> dict:
    """The clips of a folder (and of its subfolders, e.g. CLIP/ and BROLL/): count, total length, a few frames."""
    base = Path(path)
    if not base.is_dir():
        return {"ok": False}
    clips = sorted(p for p in list(base.iterdir()) + [q for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
                                                     for q in d.iterdir()]
                   if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith("._"))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:      # all clips in parallel: a big folder must not freeze the page
        info = list(ex.map(lambda c: vault.probe(str(c)), clips))
        lengths = [d or 0.0 for d, _ in info]
        thumbs = list(ex.map(lambda cd: _thumb(cd[0], 160, min(1.0, cd[1] / 2)), list(zip(clips, lengths))[:8]))
    return {"ok": True, "n": len(clips), "durata": round(sum(lengths)), "thumbs": [t for t in thumbs if t],
            "lenti": sum(1 for _, f in info if vault.is_slowmo(f))}


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str) -> dict:
    job = manager.get(job_id)
    if job:
        job.stopped = True
        agent.stop(job)
    return {"ok": bool(job)}


@app.get("/api/subfolders")
def subfolders(path: str) -> dict:
    subs = _project_subfolders(Path(path))
    return {"folders": [{"name": s.name, "path": str(s)} for s in subs]}


@app.post("/api/batch")
def batch(req: BatchRequest) -> dict:
    job = manager.create()
    t = threading.Thread(target=_run_batch, args=(job, req), daemon=True)
    t.start()
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Queue: several reels added one after the other, edited one at a time. When the
# last one ends the Mac plays a sound and shows a notification.
# ---------------------------------------------------------------------------

@dataclass
class QueueItem:
    id: str
    req: GenerateRequest
    cliente: str = ""
    status: str = "in coda"           # in coda | in corso | fatto | errore | tolto
    job_id: str | None = None
    result_path: str | None = None
    error: str | None = None
    added: float = field(default_factory=lambda: __import__("time").time())

    def view(self) -> dict:
        job = manager.get(self.job_id) if self.job_id else None
        last = ""
        if job:
            for line in reversed(job.logs):
                if line.strip() and not line.startswith("⚙"):
                    last = line.strip()[:160]
                    break
        return {"id": self.id, "name": self.req.name, "cliente": self.cliente, "status": self.status,
                "job_id": self.job_id, "step": job.step if job else 0, "last": last,
                "result_path": self.result_path, "error": self.error}


class Queue:
    def __init__(self) -> None:
        self.items: list[QueueItem] = []
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.current: QueueItem | None = None

    def add(self, req: GenerateRequest) -> QueueItem:
        c = agent.get_client(req.client)
        item = QueueItem(id=uuid.uuid4().hex[:10], req=req, cliente=c["nome"] if c else (req.template or ""))
        with self.lock:
            self.items.append(item)
            if not (self.worker and self.worker.is_alive()):
                self.worker = threading.Thread(target=self._loop, daemon=True)
                self.worker.start()
        return item

    def _next(self) -> QueueItem | None:
        with self.lock:
            return next((i for i in self.items if i.status == "in coda"), None)

    def _loop(self) -> None:
        done = []
        while True:
            item = self._next()
            if not item:
                break
            job = manager.create()
            item.job_id, item.status, self.current = job.id, "in corso", item
            _run_job(job, item.req)
            item.result_path, item.error = job.result_path, job.error
            item.status = "fatto" if job.status == "done" else ("tolto" if job.stopped else "errore")
            self.current = None
            done.append(item)
        if done:
            _all_done([i for i in done if i.status == "fatto"], [i for i in done if i.status == "errore"])

    def remove(self, item_id: str) -> bool:
        with self.lock:
            for i in self.items:
                if i.id == item_id:
                    if i.status == "in coda":
                        i.status = "tolto"
                        return True
                    if i.status == "in corso" and i.job_id:
                        job = manager.get(i.job_id)
                        if job:
                            job.stopped = True
                            agent.stop(job)
                        return True
                    if i.status in ("fatto", "errore", "tolto"):
                        self.items.remove(i)
                        return True
        return False

    def clear_finished(self) -> None:
        with self.lock:
            self.items = [i for i in self.items if i.status in ("in coda", "in corso")]


queue = Queue()


def _all_done(ok: list, failed: list) -> None:
    """The queue has emptied: a sound and a notification, so the user knows without watching the app."""
    msg = f"{len(ok)} video pront{'o' if len(ok) == 1 else 'i'}" + (f" · {len(failed)} con errore" if failed else "")
    try:
        subprocess.Popen(["/bin/sh", "-c", "afplay /System/Library/Sounds/Glass.aiff; afplay /System/Library/Sounds/Hero.aiff"])
        subprocess.Popen(["osascript", "-e", f'display notification "{msg}" with title "CapWizard" subtitle "Coda finita" sound name "Glass"'])
    except OSError:
        pass


@app.post("/api/queue")
def queue_add(req: GenerateRequest) -> dict:
    if not req.name.strip():
        return {"error": "Manca il nome del progetto."}
    if not Path(req.input_path).is_dir():
        return {"error": "La cartella delle clip non esiste."}
    if (CAPCUT_PROJECTS_DIR / req.name).exists():
        return {"error": f"Esiste già un progetto CapCut «{req.name}»."}
    if any(i.req.name == req.name and i.status in ("in coda", "in corso") for i in queue.items):
        return {"error": f"«{req.name}» è già in coda."}
    return {"ok": True, "item": queue.add(req).view()}


@app.get("/api/queue")
def queue_list() -> dict:
    return {"items": [i.view() for i in queue.items if i.status != "tolto" or i.job_id]}


@app.delete("/api/queue/{item_id}")
def queue_remove(item_id: str) -> dict:
    return {"ok": queue.remove(item_id)}


@app.post("/api/queue/clear")
def queue_clear() -> dict:
    queue.clear_finished()
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = manager.get(job_id)
    if not job:
        return {"error": "job non trovato"}
    return job.snapshot()


def _summary(report: str | None) -> str:
    """"Cosa ha fatto Claude" from a project's report."""
    try:
        text = Path(report).read_text() if report else ""
    except OSError:
        return ""
    m = re.search(r"## Cosa ha fatto Claude\n(.*?)(?:\n## |\Z)", text, re.S)
    return m.group(1).strip() if m else ""


OLD_PANEL_JOBS = Path.home() / "video-editor-mcp/jobs"   # Daniel's first panel (CapCut Editor), if it was used on this Mac


def _old_job(name: str) -> tuple[dict, dict]:
    """job.json and result.json the old panel kept for a project of this name, if any."""
    d = OLD_PANEL_JOBS / re.sub(r"[^\w\- ]", "_", name)
    out = []
    for f in ("job.json", "result.json"):
        try:
            out.append(__import__("json").loads((d / f).read_text()))
        except (OSError, ValueError):
            out.append({})
    return out[0], out[1]


def _video_notes() -> dict[str, str]:
    """CapCut project name → its video note in the vault ("**Progetto CapCut:** NAME" or a note named like the project)."""
    out: dict[str, str] = {}
    if not vault.enabled():
        return out
    for f in (vault.VAULT / "05 Video").rglob("*.md"):
        try:
            txt = f.read_text()
        except OSError:
            continue
        out.setdefault(f.stem, str(f))
        for m in re.finditer(r"Progetto CapCut:\*\*\s*([^·\n*]+)", txt):
            out[m.group(1).strip()] = str(f)
    return out


def _guess_client(name: str, clients: list[dict], notes: dict | None = None) -> dict | None:
    """The client of a CapCut project not made by this app: from the old panel's job, else a client name inside the project name."""
    job, _ = _old_job(name)
    if job.get("cliente"):
        cid = vault.PREFIX + job["cliente"]
        c = next((x for x in clients if x["id"] == cid or x["nome"] == job["cliente"].split(" / ")[-1]), None)
        if c:
            return c
    # the video's note in the vault ("05 Video/<client or agency>/<project>.md" with "Cliente [[X]]" or a [[client]] link)
    vn = (notes if notes is not None else _video_notes()).get(name)
    if vn:
        txt = open(vn).read()
        names = re.findall(r"\[\[([^\]|#]+)", txt) + [Path(vn).parent.name]
        for n in names:
            c = next((x for x in clients if x["nome"].upper() == n.strip().upper()), None)
            if c:
                return c
    up = name.upper()
    hits = [c for c in clients if len(c["nome"]) >= 3 and c["nome"].upper() in up]
    return max(hits, key=lambda c: len(c["nome"])) if hits else None


def _capcut_drafts(known: set[str]) -> list[dict]:
    """Every project in this Mac's CapCut that the app's registry doesn't list yet (each user sees only their own CapCut)."""
    out = []
    if not CAPCUT_PROJECTS_DIR.is_dir():
        return out
    for d in CAPCUT_PROJECTS_DIR.iterdir():
        info = next((d / f for f in ("draft_info.json", "template-2.tmp", "draft_info.json.bak") if (d / f).exists()), None)
        if not d.is_dir() or d.name.startswith(".") or not info or str(d) in known:
            continue
        job, res = _old_job(d.name)
        out.append({"id": "capcut:" + d.name, "name": d.name, "result_path": str(d), "report_path": None, "exists": True,
                    "created_at": info.stat().st_mtime, "source": "capcut", "riassunto_old": res.get("riassunto", ""),
                    "settings": {"client": None, "template": job.get("progetto_reference"), "input_path": job.get("cartella_clip"),
                                 "broll_path": job.get("cartella_broll"), "staging": res.get("staging")}})
    return out


def _shared_space(folder: str) -> str:
    """Group of a project whose client is unknown: downloaded from a shared (team) space of CapCut, or just CapCut."""
    try:
        meta = __import__("json").loads((Path(folder) / "draft_meta_info.json").read_text())
        if meta.get("tm_draft_cloud_space_id"):
            return "Dallo spazio condiviso di CapCut"
    except (OSError, ValueError):
        pass
    return "Altri progetti CapCut"


def _project(project_id: str) -> dict | None:
    if project_id.startswith("capcut:"):
        name = project_id[len("capcut:"):]
        return next((p for p in _capcut_drafts(set()) if p["name"] == name), None)
    return registry.get_project(project_id)


def _register_draft(p: dict) -> dict:
    """A CapCut project picked in "I miei progetti" gets its own record the first time it is changed from the app."""
    clients = agent.list_clients()
    c = _guess_client(p["name"], clients)
    settings = {**p["settings"], "client": c["id"] if c else None}
    rec = registry.add_project(name=p["name"], result_path=p["result_path"], settings=settings, report_path=None)
    if p.get("riassunto_old"):
        rep = registry.app_data_dir() / "lavori" / f"{p['name']} importato"
        rep.mkdir(parents=True, exist_ok=True)
        (rep / "report.md").write_text(f"# {p['name']}\n\n## Cosa ha fatto Claude\n{p['riassunto_old']}\n")
        rec = {**rec, "report_path": str(rep / "report.md")}
        projects = registry._load()
        for x in projects:
            if x["id"] == rec["id"]:
                x["report_path"] = rec["report_path"]
        registry._save(projects)
    return rec


@app.get("/api/projects")
def projects_list() -> dict:
    """The projects of this Mac's CapCut: the ones made with the app (with their record) and all the others, each with its
    client when it can be told, summary and CapCut cover. Only this computer's CapCut, never someone else's."""
    clients_l = agent.list_clients()
    clients = {c["id"]: c for c in clients_l}
    out = []
    regs = registry.list_projects()
    known = {p.get("result_path") for p in regs if p.get("result_path")}
    notes = _video_notes()
    for p in _capcut_drafts(known):
        c = _guess_client(p["name"], clients_l, notes)
        cover = Path(p["result_path"]) / "draft_cover.jpg"
        out.append({**p, "cliente": c["nome"] if c else _shared_space(p["result_path"]), "colore": c["colore"] if c else "#6b7180",
                    "riassunto": p.pop("riassunto_old", ""), "cover": ""})
        out[-1]["_cover"] = str(cover) if cover.exists() else ""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        covers = list(ex.map(lambda q: _thumb(Path(q["_cover"]), 320) if q["_cover"] else "", out))
    for q, cv in zip(out, covers):
        q["cover"] = cv
        q.pop("_cover", None)
    for p in regs:
        s = p.get("settings") or {}
        c = clients.get(s.get("client") or "") or next(
            (x for x in clients.values() if s.get("template") and x["template"] == s.get("template")), None)
        cover = Path(p.get("result_path") or "") / "draft_cover.jpg"
        out.append({**p, "cliente": c["nome"] if c else (s.get("template") or "Senza cliente"),
                    "colore": c["colore"] if c else "#6b7180", "riassunto": _summary(p.get("report_path")),
                    "cover": _thumb(cover, 320) if p.get("result_path") and cover.exists() else ""})
    return {"projects": out}


# ---------------------------------------------------------------------------
# Homepage board: where every video stands
# ---------------------------------------------------------------------------

class BoardStatus(BaseModel):
    id: str
    stato: str


class TodoRequest(BaseModel):
    titolo: str | None = None
    cliente: str | None = None
    cartella: str | None = None
    scadenza: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# AI services Claude can use during a montage (MCP servers connected to Claude)
# ---------------------------------------------------------------------------

class ServiceToggle(BaseModel):
    attivo: bool


@app.get("/api/ai-services")
def ai_services(refresh: bool = False) -> dict:
    return {"services": aiservices.services(refresh), "connettori": aiservices.CONNECTORS_URL}


@app.put("/api/ai-services/{key}")
def ai_service_toggle(key: str, req: ServiceToggle) -> dict:
    return aiservices.set_active(key, req.attivo)


@app.get("/api/board")
def board_get() -> dict:
    clients = agent.list_clients()
    templates = {c["template"] for c in clients if c.get("template")}
    templates |= {(p.get("settings") or {}).get("template") or "" for p in registry.list_projects()}
    return board.board(projects_list()["projects"], clients, templates - {"", "—"})


@app.put("/api/board/status")
def board_status(req: BoardStatus) -> dict:
    return board.set_status(req.id, req.stato)


@app.post("/api/board/todos")
def board_todo_add(req: TodoRequest) -> dict:
    return board.add_todo(req.model_dump())


@app.put("/api/board/todos/{todo_id:path}")
def board_todo_edit(todo_id: str, req: TodoRequest) -> dict:
    return board.update_todo(todo_id, req.model_dump())


@app.delete("/api/board/todos/{todo_id:path}")
def board_todo_delete(todo_id: str) -> dict:
    return board.remove_todo(todo_id)


class ModifyRequest(BaseModel):
    testo: str = ""
    immagini: list[str] = []
    ai_model: str = "opus"


class AttachmentRequest(BaseModel):
    path: str | None = None
    data_url: str | None = None


@app.post("/api/attachments")
def attachment(req: AttachmentRequest) -> dict:
    """A screenshot for a change request: kept in the app's folder, with a thumbnail."""
    a = agent.add_attachment(req.path, req.data_url)
    return a or {"error": "Non è un'immagine."}


@app.get("/api/projects/{project_id:path}/mods")
def project_mods(project_id: str) -> dict:
    mods = [] if project_id.startswith("capcut:") else agent.list_mods(project_id)
    for m in mods:
        m["thumbs"] = [agent.image_thumb(Path(p)) for p in m.get("immagini", []) if Path(p).exists()]
    return {"mods": mods}


def _run_modify(job: Job, project: dict, req: ModifyRequest) -> None:
    job.status = "running"
    try:
        agent.run_modify(job, project, req.testo.strip(), req.immagini, req.ai_model)
        job.result_path = project.get("result_path")
        job.status = "done"
    except Exception as e:  # noqa: BLE001 — surface everything to the UI
        job.error = str(e)
        job.status = "error"
        job.log(f"ERRORE: {e}")
    brain.push_soon(f"Lezione dalla modifica di «{project.get('name', 'progetto')}»")


@app.post("/api/projects/{project_id:path}/modify")
def project_modify(project_id: str, req: ModifyRequest) -> dict:
    project = _project(project_id)
    if not project:
        return {"error": "progetto non trovato"}
    if project_id.startswith("capcut:"):
        project = _register_draft(project)
    if not (req.testo.strip() or req.immagini):
        return {"error": "Scrivi cosa cambiare o allega uno screenshot."}
    job = manager.create()
    threading.Thread(target=_run_modify, args=(job, project, req), daemon=True).start()
    return {"job_id": job.id, "project_id": project["id"]}


@app.post("/api/projects/{project_id:path}/as-template")
def project_as_template(project_id: str) -> dict:
    """«Usa come template»: the client's template = the reference structure (texts, compound clips, sounds of
    TEMPLATE SOLUZIONI IMMOBILIARE) dressed with this video's graphics; without the reference, this video emptied of its clips."""
    from capwizard_app import template_maker
    p = _project(project_id)
    if not p or not p.get("result_path") or not Path(p["result_path"]).is_dir():
        return {"ok": False, "msg": "Il progetto non c'è più tra i progetti CapCut."}
    video = Path(p["result_path"]).name
    client = agent.client_for(p.get("settings") or {}) or _guess_client(p["name"], agent.list_clients())
    if not client:
        return {"ok": False, "msg": "Non so di quale cliente è questo video: rifallo da «Nuovo video» scegliendo il cliente."}
    template = client.get("template") or f"TEMPLATE {client['nome'].upper()}"
    if video == template or video.upper().startswith("TEMPLATE"):
        return {"ok": False, "msg": "Questo progetto è già un template."}
    try:
        if (CAPCUT_PROJECTS_DIR / template_maker.STRUTTURA / "draft_info.json").exists():
            out = template_maker.con_stile(video, template)
        else:
            out = template_maker.da_video(video, template)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": str(e)}
    if client.get("source") == "vault" and client.get("template") != template:
        vault.salva_cliente(client["id"], base=template)
    brain.push_soon(f"Template di {client['nome']} rifatto da {video}")
    if not vault.capcut_running():
        vault.open_capcut()              # back on CapCut's home, with the new template in the list
    return out


@app.get("/api/projects/{project_id:path}")
def project_get(project_id: str) -> dict:
    p = _project(project_id)
    return p or {"error": "progetto non trovato"}


@app.delete("/api/projects/{project_id:path}")
def project_delete(project_id: str) -> dict:
    # only the app's record: a CapCut project is never deleted from here
    return {"removed": False if project_id.startswith("capcut:") else registry.remove_project(project_id)}


@app.get("/api/ai/status")
def ai_status_endpoint() -> dict:
    return ai_status()


@app.post("/api/ai/test")
def ai_test_endpoint() -> dict:
    return ai_ping()


@app.websocket("/api/ws/jobs/{job_id}")
async def job_logs(websocket: WebSocket, job_id: str) -> None:
    if not _token_ok(websocket.query_params.get("t")):   # websockets skip CORS and the http middleware
        await websocket.close(code=1008)
        return
    await websocket.accept()
    job = manager.get(job_id)
    if not job:
        await websocket.send_json({"type": "error", "message": "job non trovato"})
        await websocket.close()
        return

    sent = 0
    step = -1
    try:
        while True:
            if job.step != step:
                step = job.step
                await websocket.send_json({"type": "step", "step": step})
            # flush any new log lines
            snapshot_logs = job.logs  # atomic read of the list reference
            while sent < len(snapshot_logs):
                await websocket.send_json({"type": "log", "i": sent, "line": snapshot_logs[sent]})
                sent += 1
            if job.status in ("done", "error"):
                await websocket.send_json({
                    "type": "status", "status": job.status,
                    "result_path": job.result_path,
                    "report_path": job.report_path,
                    "results": job.results,
                    "error": job.error, "step": job.step,
                })
                break
            await asyncio.sleep(0.15)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    parser = argparse.ArgumentParser(description="CapWizard backend server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skill-dir", default=os.environ.get("CAPWIZARD_SKILL_DIR"),
                        help="the reel-capcut-mac skill shipped with the app, installed in ~/.claude/skills")
    args = parser.parse_args()
    try:
        print(agent.install_editor_skill(Path(args.skill_dir) if args.skill_dir else None), flush=True)
    except OSError as e:
        print(f"skill non installata: {e}", flush=True)
    # the shared client brain: read the vault from it (decided now, from disk), then in the
    # background pull it, link its client skills and switch to it if it gained clients
    chosen = brain.choose_vault(vault.enabled())
    if chosen:
        vault.VAULT = chosen
        print(f"[brain] vault: {chosen}", flush=True)
    brain.start(on_vault=lambda v: setattr(vault, "VAULT", v), log=lambda m: print(m, flush=True))

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
