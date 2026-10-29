"""Passing a CapCut project to a teammate through the team's shared folder (a Samba server).

A project is a few MB, but its clips are gigabytes and live wherever the editor had them (an
external SSD, Downloads…). «Passa il progetto» copies the project and every clip, photo and sound
it uses into the team folder, under CapWizard/Progetti/<name>/, with the paths in the timeline
turned into "capwizard-media://<file>". On another Mac «Prendi il progetto» puts it in that
person's CapCut the same way the app installs a project it built: the clips are copied under
~/Movies/CapCut Editor/<name>/ (CapCut is sandboxed) and the timeline points there.
A client's CapCut template travels the same way, under CapWizard/Template/<name>/, and keeps its name on
every Mac (the client profile names it).

The folder is the share as mounted on this Mac (e.g. /Volumes/CapWizard), with its smb:// address
so the app can ask Finder to mount it. Settings live in a small JSON in the app's data folder.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from capwizard_app import registry, vault
from capwizard_app.capcut import CAPCUT_PROJECTS_DIR

_PATH = registry.app_data_dir() / "team_share.json"
PREFIX = "capwizard-media://"
MEDIA_EXT = (".mov", ".mp4", ".m4v", ".mts", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif",
             ".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac")
_SKIP = shutil.ignore_patterns("draft.extra", "crypto_key_store.dat*", ".locked", "*.bak", "__pycache__")
_JOBS: dict[str, dict] = {}          # team item id -> what the copy is doing


def _settings() -> dict:
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError):
        return {}


def config() -> dict:
    s = _settings()
    folder = s.get("cartella", "")
    return {"cartella": folder, "indirizzo": s.get("indirizzo", ""), "raggiungibile": bool(folder) and os.path.isdir(folder)}


def set_config(cartella: str = "", indirizzo: str = "") -> dict:
    _PATH.write_text(json.dumps({"cartella": cartella.strip(), "indirizzo": indirizzo.strip()}, ensure_ascii=False, indent=1))
    return config()


def mount() -> dict:
    """Ask Finder to mount the share (it asks for the password the first time and keeps it in the Keychain)."""
    addr = _settings().get("indirizzo", "")
    if not addr.startswith(("smb://", "afp://", "nfs://")):
        return {"ok": False, "msg": "Scrivi l'indirizzo del server, tipo smb://nas.local/CapWizard."}
    subprocess.run(["open", addr])
    return {"ok": True}


def _root(kind: str = "Progetti") -> Path:
    folder = _settings().get("cartella", "")
    if not folder:
        raise RuntimeError("Nessuna cartella del team: impostala in «Bacheca del team».")
    if not os.path.isdir(folder):
        raise RuntimeError(f"La cartella del team ({folder}) non è raggiungibile: collega il server e riprova.")
    return Path(folder) / "CapWizard" / kind


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", name).strip(" .") or "progetto"


def packages(kind: str = "Progetti") -> dict[str, dict]:
    """The projects (or templates) waiting in the team folder, by name (empty when the share isn't mounted)."""
    try:
        root = _root(kind)
    except RuntimeError:
        return {}
    out = {}
    for m in root.glob("*/manifest.json"):
        try:
            out[m.parent.name] = json.loads(m.read_text())
        except (OSError, ValueError):
            pass
    return out


def jobs() -> dict:
    return {k: dict(v) for k, v in _JOBS.items()}


# ---------------------------------------------------------------------------
# The timeline's files
# ---------------------------------------------------------------------------

def _docs(folder: Path) -> dict[str, object]:
    out = {}
    for f in glob.glob(str(folder) + "/**/*", recursive=True):
        if os.path.isfile(f) and f.endswith((".json", ".tmp")):
            try:
                out[f] = json.load(open(f))
            except (ValueError, UnicodeDecodeError, OSError):
                pass
    return out


def _paths(docs: dict) -> set[str]:
    found = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("path", "file_Path", "media_path") and isinstance(v, str) and v.startswith("/"):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for d in docs.values():
        walk(d)
    return found


def _rewrite(docs: dict, fn) -> None:
    """Replace every media path string the timeline holds (fn returns None to leave one alone)."""
    def rep(o):
        if isinstance(o, dict):
            return {k: rep(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rep(v) for v in o]
        if isinstance(o, str):
            new = fn(o)
            return o if new is None else new
        return o
    for f, d in docs.items():
        n = rep(d)
        if n != d:
            json.dump(n, open(f, "w"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Passa / Prendi (in the background: gigabytes over the network)
# ---------------------------------------------------------------------------

def _run(item_id: str, fase: str, work, log=print) -> dict:
    if _JOBS.get(item_id, {}).get("attivo"):
        return {"ok": False, "msg": "Ci sto già lavorando."}
    job = _JOBS[item_id] = {"attivo": True, "fase": fase, "pct": 0, "msg": "", "errore": ""}

    def run() -> None:
        try:
            job["msg"] = work(job)
        except Exception as e:  # noqa: BLE001
            job["errore"] = str(e)
            log(f"[team] {fase}: {e}")
        finally:
            job["attivo"] = False
            job["pct"] = 100
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def _pack(src: Path, dest: Path, manifest: dict, job: dict) -> tuple[int, int]:
    """Copy a CapCut project and every media file it uses to dest/ (project/, media/, manifest.json).
    It is written next to dest and renamed at the end, so a half copy never looks finished."""
    part = dest.with_name(dest.name + ".parziale")
    shutil.rmtree(part, ignore_errors=True)
    shutil.copytree(src, part / "progetto", ignore=_SKIP)
    docs = _docs(part / "progetto")
    media = sorted(p for p in _paths(docs) if p.lower().endswith(MEDIA_EXT) and os.path.isfile(p))
    total = sum(os.path.getsize(p) for p in media) or 1
    (part / "media").mkdir(parents=True, exist_ok=True)
    names, used, done = {}, set(), 0
    for p in media:
        b = os.path.basename(p)
        if b in used:
            b = f"{os.path.basename(os.path.dirname(p))} - {b}"
        stem, ext, k = os.path.splitext(b)[0], os.path.splitext(b)[1], 2
        while b in used:                    # same name in the same-named folder too: never overwrite a clip
            b, k = f"{stem} ({k}){ext}", k + 1
        used.add(b)
        names[p] = b
        shutil.copy2(p, part / "media" / b)
        done += os.path.getsize(p)
        job["pct"] = int(done * 100 / total)
    _rewrite(docs, lambda s: PREFIX + names[s] if s in names else None)
    (part / "manifest.json").write_text(json.dumps({
        **manifest, "quando": time.strftime("%Y-%m-%d %H:%M"), "file": len(media), "byte": total},
        ensure_ascii=False, indent=1))
    shutil.rmtree(dest, ignore_errors=True)
    part.rename(dest)
    return len(media), total


def _unpack(pkg: Path, staging: Path) -> None:
    """A package of the team folder as a CapCut project in staging/, its media read from the share."""
    shutil.copytree(pkg / "progetto", staging, ignore=_SKIP)
    media = pkg / "media"
    _rewrite(_docs(staging), lambda s: str(media / s[len(PREFIX):]) if s.startswith(PREFIX) else None)
    meta_p = staging / "draft_meta_info.json"
    meta = json.load(open(meta_p))
    for k in [k for k in meta if k.startswith("tm_draft_cloud")]:
        meta.pop(k)                   # a copy, not the one in CapCut's cloud space
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False)


def _me(chi: str = "") -> str:
    return chi or socket.gethostname().split(".")[0]


def send(item_id: str, folder: str, nome: str, chi: str = "", log=print) -> dict:
    """Put my project and all its media in the team folder."""
    src = Path(folder)
    if not (src / "draft_meta_info.json").exists():
        return {"ok": False, "msg": "Non trovo il progetto CapCut su questo Mac."}
    try:
        root = _root()
    except RuntimeError as e:
        return {"ok": False, "msg": str(e)}

    def work(job: dict) -> str:
        n, total = _pack(src, root / safe(nome), {"nome": nome, "item": item_id, "da": _me(chi)}, job)
        return f"«{nome}» è nella cartella del team con {n} file ({total / 1e9:.1f} GB)."
    return _run(item_id, "passo", work, log)


def take(item_id: str, nome: str, log=print) -> dict:
    """Put a teammate's project from the team folder into my CapCut."""
    try:
        pkg = _root() / safe(nome)
    except RuntimeError as e:
        return {"ok": False, "msg": str(e)}
    if not (pkg / "manifest.json").exists():
        return {"ok": False, "msg": "Il progetto non è (ancora) nella cartella del team."}
    man = json.loads((pkg / "manifest.json").read_text())

    def work(job: dict) -> str:
        # the name comes from the team card and the manifest from the share: a folder name, never a path
        base = safe(nome)
        target = base if not (CAPCUT_PROJECTS_DIR / base).exists() else safe(f"{base} - da {man.get('da', 'team')}")
        if (CAPCUT_PROJECTS_DIR / target).exists():
            raise RuntimeError(f"Hai già un progetto CapCut «{target}».")
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "progetto"
            _unpack(pkg, staging)
            job["fase"] = "copio i video"
            out = vault.install(str(staging), target, log, cutout=False)
        from capwizard_app import teamboard
        teamboard.link(f"capcut:{target}", item_id)       # the team card now opens it on this Mac
        return out
    return _run(item_id, "prendo", work, log)


# ---------------------------------------------------------------------------
# Client templates: the CapCut project itself, not only the client's profile
# ---------------------------------------------------------------------------

BACKUP_DIR = registry.app_data_dir() / "backup"


def templates() -> list[dict]:
    """The templates in the team folder, newest first, and whether this Mac already has each one."""
    out = []
    for folder, man in packages("Template").items():
        nome = man.get("nome") or folder
        out.append({"nome": nome, "da": man.get("da", ""), "quando": man.get("quando", ""),
                    "file": man.get("file", 0), "byte": man.get("byte", 0),
                    "qui": (CAPCUT_PROJECTS_DIR / safe(nome)).is_dir()})
    return sorted(out, key=lambda t: t["quando"], reverse=True)


def send_template(nome: str, chi: str = "", log=print) -> dict:
    """Put one of my CapCut templates, with its sounds, music and pictures, in the team folder."""
    src = CAPCUT_PROJECTS_DIR / safe(nome)
    if safe(nome) != nome or not (src / "draft_meta_info.json").exists():
        return {"ok": False, "msg": f"Non trovo il progetto CapCut «{nome}» su questo Mac."}
    try:
        root = _root("Template")
    except RuntimeError as e:
        return {"ok": False, "msg": str(e)}

    def work(job: dict) -> str:
        n, total = _pack(src, root / nome, {"nome": nome, "da": _me(chi)}, job)
        return f"Il template «{nome}» è nella cartella del team con {n} file ({total / 1e6:.0f} MB)."
    return _run(f"tpl:{nome}", "passo", work, log)


def take_template(nome: str, log=print) -> dict:
    """Put a template of the team folder in my CapCut, under the same name. One I already have is moved
    to the app's backup folder first (never deleted) and the new one takes its place in CapCut's list."""
    try:
        pkg = _root("Template") / safe(nome)
    except RuntimeError as e:
        return {"ok": False, "msg": str(e)}
    if not (pkg / "manifest.json").exists():
        return {"ok": False, "msg": "Il template non è (ancora) nella cartella del team."}
    target = safe(nome)

    def work(job: dict) -> str:
        dst = CAPCUT_PROJECTS_DIR / target
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "progetto"
            _unpack(pkg, staging)
            job["fase"] = "copio i file"
            if not dst.exists():
                return vault.install(str(staging), target, log, cutout=False)
            vault.libera(dst, log)
            meta_p = staging / "draft_meta_info.json"
            meta = json.load(open(meta_p))
            old_id = json.load(open(dst / "draft_meta_info.json")).get("draft_id")
            if old_id:
                meta["draft_id"] = old_id        # same place in CapCut's list
            meta.update(draft_name=target, draft_fold_path=str(dst), draft_root_path=str(CAPCUT_PROJECTS_DIR))
            json.dump(meta, open(meta_p, "w"), ensure_ascii=False)
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup = BACKUP_DIR / f"{target} {time.strftime('%m%d-%H%M%S')}"
            shutil.move(str(dst), str(backup))
            shutil.copytree(staging, dst)
            media = vault.porta_media(str(dst), target)
        return " ".join(x for x in (f"✅ Template «{target}» aggiornato (il tuo di prima è in {backup}).", media,
                                    "CapCut è aperto: chiudilo e riaprilo per vedere quello nuovo." if vault.capcut_running() else "") if x)
    return _run(f"tpl:{nome}", "prendo", work, log)
