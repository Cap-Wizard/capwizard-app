"""The shared client brain: a private GitHub repo with every client's notes.

`Cap-Wizard/video-edit-brain` holds what Nicola and Daniel know about their clients:

    skills/reel-<cliente>/SKILL.md      client profiles, as Claude Code skills
    01 Agenzie/ 02 Clienti/ 03 Stili/…  the Obsidian vault (Daniel's VIDEO EDIT BRAIN)

Every Mac keeps a clone (`CAPWIZARD_BRAIN`, default ~/Documents/Github/video-edit-brain).
When the app starts it pulls the clone (or makes it), links its client skills into
~/.claude/skills — a client profile that only exists there is moved into the brain first — and
reads the vault from it once `02 Clienti` holds a client. After a job that may have changed a
client (a lesson, a colour, a new client) it commits and pushes. Git does all of it with the
user's own GitHub login; a failure is logged and never stops the app.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

REPO = "https://github.com/Cap-Wizard/video-edit-brain.git"
BRAIN = Path(os.environ.get("CAPWIZARD_BRAIN") or Path.home() / "Documents/Github/video-edit-brain")
SKILLS_DIR = Path.home() / ".claude/skills"

_lock = threading.Lock()          # one git operation at a time
_status = {"stato": "non ancora sincronizzato", "quando": None}


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    # never wait on a password prompt: a missing login must fail, not hang the app
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", str(BRAIN), *args], capture_output=True, text=True,
                          timeout=timeout, env=env)


def _note(text: str) -> str:
    _status.update(stato=text, quando=time.strftime("%H:%M"))
    return text


def status() -> dict:
    return {"cartella": str(BRAIN), "repo": REPO, **_status}


def available() -> bool:
    return (BRAIN / ".git").is_dir()


def vault_dir() -> Path | None:
    """The brain, when it already holds the vault (at least one client note)."""
    return BRAIN if any((BRAIN / "02 Clienti").glob("*/*.md")) else None


VAULT_FOLDERS = ("01 Agenzie", "02 Clienti", "03 Stili")


def choose_vault(other_vault_ok: bool) -> Path | None:
    """Which vault to use, from what's on disk (no network, so it's instant at start):
    the brain once it holds clients; otherwise a vault already in use elsewhere (Daniel's on the
    Desktop, until he moves it here); otherwise the brain, made ready to receive the first client."""
    if vault_dir():
        return BRAIN
    if other_vault_ok or not available():
        return None
    for name in VAULT_FOLDERS:
        (BRAIN / name).mkdir(exist_ok=True)
        keep = BRAIN / name / ".gitkeep"
        if not any((BRAIN / name).iterdir()):
            keep.touch()
    return BRAIN


def pull() -> str:
    """Make the clone if it's missing, otherwise bring it up to date."""
    with _lock:
        try:
            if not available():
                if BRAIN.exists() and any(BRAIN.iterdir()):
                    return _note(f"{BRAIN} esiste ma non è la copia della repo: non la tocco")
                BRAIN.parent.mkdir(parents=True, exist_ok=True)
                env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
                r = subprocess.run(["git", "clone", "-q", REPO, str(BRAIN)], capture_output=True,
                                   text=True, timeout=120, env=env)
                return _note("copia creata" if r.returncode == 0
                             else f"copia non creata: {(r.stderr or r.stdout).strip()[:160]}")
            before = _git("rev-parse", "HEAD").stdout.strip()
            r = _git("pull", "--rebase", "--autostash", "-q", timeout=90)
            if r.returncode != 0:
                _git("rebase", "--abort")
                return _note(f"aggiornamento non riuscito: {(r.stderr or r.stdout).strip()[:160]}")
            after = _git("rev-parse", "HEAD").stdout.strip()
            if before == after:
                return _note("già aggiornato")
            n = _git("rev-list", "--count", f"{before}..{after}").stdout.strip()
            return _note(f"scaricate {n} novità")
        except (OSError, subprocess.TimeoutExpired) as e:
            return _note(f"aggiornamento non riuscito: {e}")


def _client_profile_dirs() -> set[Path]:
    """The local skills the app treats as client profiles (same rule as the client list)."""
    from capwizard_app import agent
    out = set()
    for c in agent.list_clients():
        p = Path(c.get("path", ""))
        if c.get("source") != "vault" and "synced" not in p.parts and p.parent.parent == SKILLS_DIR:
            out.add(p.parent)
    return out


def link_skills() -> list[str]:
    """Each client skill of the brain becomes a link in ~/.claude/skills. A client profile that
    exists only in ~/.claude/skills is moved into the brain first, so it is shared from now on."""
    notes: list[str] = []
    if not available():
        return notes
    skills = BRAIN / "skills"
    skills.mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    profiles = _client_profile_dirs()
    for local in sorted(SKILLS_DIR.iterdir()):
        md = local / "SKILL.md"
        if local.is_symlink() or not md.exists() or local not in profiles:
            continue
        dest = skills / local.name
        if dest.exists():
            same = (dest / "SKILL.md").exists() and (dest / "SKILL.md").read_bytes() == md.read_bytes()
            if not same:
                notes.append(f"{local.name}: diversa da quella della repo, resta la copia locale")
                continue
            shutil.rmtree(local)           # identical to the shared one: use that
            local.symlink_to(dest, target_is_directory=True)
            notes.append(f"{local.name}: ora usa la versione della repo")
            continue
        shutil.move(str(local), str(dest))
        local.symlink_to(dest, target_is_directory=True)
        notes.append(f"{local.name}: spostata nella repo condivisa")
    for shared in sorted(skills.iterdir()):
        if not (shared / "SKILL.md").exists():
            continue
        link = SKILLS_DIR / shared.name
        if link.is_symlink() and link.resolve() == shared.resolve():
            continue
        if link.exists() or link.is_symlink():
            if not link.is_symlink():
                continue                   # a real local copy wins (reported above)
            link.unlink()                  # a link pointing somewhere else
        link.symlink_to(shared, target_is_directory=True)
        notes.append(f"{shared.name}: collegata")
    return notes


def adopt_profile(src_dir: Path) -> Path | None:
    """Put a client profile (e.g. one synced from claude.ai) into the brain and link it.
    Returns the SKILL.md to write into, or None when there's no brain."""
    if not available():
        return None
    dest = BRAIN / "skills" / src_dir.name
    if not (dest / "SKILL.md").exists():
        shutil.copytree(src_dir, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    link = SKILLS_DIR / src_dir.name
    if not link.exists() and not link.is_symlink():
        link.symlink_to(dest, target_is_directory=True)
    return (link if link.is_symlink() else dest) / "SKILL.md"


def push(reason: str) -> str:
    """Commit what changed in the brain and send it to GitHub."""
    if not available():
        return ""
    with _lock:
        try:
            if not _git("status", "--porcelain").stdout.strip():
                return ""
            _git("add", "-A")
            msg = f"{reason} (da {socket.gethostname().split('.')[0]})"
            if _git("commit", "-q", "-m", msg).returncode != 0:
                return _note("commit non riuscito")
            r = _git("pull", "--rebase", "-q", timeout=90)
            if r.returncode != 0:
                _git("rebase", "--abort")
                return _note("conflitto con le modifiche di un altro: le tue sono salvate in locale, "
                             "va risolto a mano con git nella cartella della repo")
            r = _git("push", "-q", timeout=90)
            return _note("modifiche caricate su GitHub" if r.returncode == 0
                         else f"push non riuscito: {(r.stderr or r.stdout).strip()[:160]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            return _note(f"sincronizzazione non riuscita: {e}")


def push_soon(reason: str, log=print) -> None:
    """push() in the background, so a slow network never holds up a job."""
    def run() -> None:
        note = push(reason)
        if note:
            log(f"[brain] {note}")
    threading.Thread(target=run, daemon=True).start()


def start(on_vault=None, log=print) -> None:
    """At app start, in the background: pull, link the client skills, use the brain's vault."""
    def run() -> None:
        log(f"[brain] {pull()}")
        for n in link_skills():
            log(f"[brain] {n}")
        v = vault_dir()
        if v and on_vault:
            on_vault(v)
            log(f"[brain] vault letto da {v}")
        pending = push("Profili spostati nella repo condivisa")
        if pending:
            log(f"[brain] {pending}")
    threading.Thread(target=run, daemon=True).start()
