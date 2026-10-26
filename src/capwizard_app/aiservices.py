"""AI services Claude can use while editing: b-roll, images, video from a frame, upscale.

The services aren't wired into the app: they are MCP servers connected to the user's Claude
(claude.ai connectors such as Higgsfield and Magnific, or local servers added with
`claude mcp add`). The app finds them in `claude mcp list`, lets the user pick which ones Claude
may use during a montage, allows their tools on the `claude -p` command line and tells Claude
in the prompt what each is for. Choices live in a small JSON in the app's data folder.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time

from capwizard_app import registry
from capwizard_app.capcut import clean_env, find_claude_cli

# services we know how to describe; any other connected server whose name matches is shown too
KNOWN = [
    {"key": "higgsfield", "nome": "Higgsfield", "sito": "https://higgsfield.ai",
     "usi": "video da un fotogramma (Kling, Seedance), immagini (Nano Banana), upscale, voci"},
    {"key": "magnific", "nome": "Magnific", "sito": "https://magnific.ai",
     "usi": "b-roll video (Kling 2.5), immagini, upscale, foto stock"},
    {"key": "freepik", "nome": "Freepik", "sito": "https://freepik.com",
     "usi": "immagini, video e foto stock"},
    {"key": "runway", "nome": "Runway", "sito": "https://runwayml.com", "usi": "video generati e montaggio AI"},
    {"key": "elevenlabs", "nome": "ElevenLabs", "sito": "https://elevenlabs.io", "usi": "voci, doppiaggio, musica"},
    {"key": "fal", "nome": "fal", "sito": "https://fal.ai", "usi": "modelli di immagini e video"},
    {"key": "replicate", "nome": "Replicate", "sito": "https://replicate.com", "usi": "modelli di immagini e video"},
]
CONNECTORS_URL = "https://claude.ai/settings/connectors"
_PATH = registry.app_data_dir() / "ai_services.json"
_CACHE: dict = {"when": 0.0, "servers": []}
_LOCK = threading.Lock()
_LINE = re.compile(r"^(?P<label>[^:]+):\s+(?P<target>.+?)\s+-\s+(?P<mark>\S)\s+(?P<state>.+)$")


def _settings() -> dict:
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def _servers(refresh: bool = False) -> list[dict]:
    """MCP servers Claude Code knows, with their state. `claude mcp list` checks every server's
    health, so it takes a few seconds: the result is kept for 10 minutes."""
    with _LOCK:
        if not refresh and time.time() - _CACHE["when"] < 600:
            return _CACHE["servers"]
        cli = find_claude_cli()
        out: list[dict] = []
        if cli:
            try:
                r = subprocess.run([cli, "mcp", "list"], capture_output=True, text=True,
                                   env=clean_env(), timeout=60, cwd="/tmp")
                for line in r.stdout.splitlines():
                    m = _LINE.match(line.strip())
                    if not m:
                        continue
                    label = m["label"].strip()
                    remote = label.startswith("claude.ai ")
                    name = label[len("claude.ai "):] if remote else label
                    if name.startswith("plugin:"):
                        continue
                    prefix = ("mcp__claude_ai_" + re.sub(r"[^A-Za-z0-9]", "_", name)) if remote \
                        else ("mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", name))
                    state = m["state"].strip().lower()
                    out.append({"nome": name, "remoto": remote, "prefix": prefix,
                                "collegato": m["mark"] == "✔" or "connected" == state,
                                "da_autorizzare": "auth" in state})
            except (OSError, subprocess.TimeoutExpired):
                pass
        _CACHE.update(when=time.time(), servers=out)
        return out


def services(refresh: bool = False) -> list[dict]:
    """The AI services to show: every known one (connected or not) plus any other connected
    server that looks like one of them."""
    servers = _servers(refresh)
    chosen = _settings().get("attivi", {})
    out = []
    for k in KNOWN:
        srv = next((s for s in servers if k["key"] in s["nome"].lower()), None)
        connected = bool(srv and srv["collegato"])
        out.append({**k, "server": srv["nome"] if srv else "", "prefix": srv["prefix"] if srv else "",
                    "stato": "collegato" if connected else ("da autorizzare" if srv and srv["da_autorizzare"]
                                                            else "non collegato"),
                    "attivo": connected and chosen.get(k["key"], True)})
    return out


def set_active(key: str, active: bool) -> dict:
    data = _settings()
    data.setdefault("attivi", {})[key] = bool(active)
    _save(data)
    return {"ok": True}


def for_run() -> tuple[list[str], str]:
    """(tool patterns to allow, prompt section) for a montage that may use AI services."""
    active = [s for s in services() if s["attivo"] and s["prefix"]]
    if not active:
        return [], ""
    lines = "\n".join(f"- **{s['nome']}** (strumenti `{s['prefix']}__…`): {s['usi']}" for s in active)
    text = (
        "\n\n## Servizi AI a disposizione\n"
        f"{lines}\n"
        "Usali solo quando servono davvero al video e come dice la skill: b-roll quando il girato è "
        "solo parlato o il copione lo chiede, cambiare una ripresa partendo dal fotogramma reale e "
        "aggiungendo solo ciò che serve. Ogni generazione consuma i crediti del servizio: pochi "
        "contenuti mirati. Scarica i file nella cartella del video (BROLL/), guardali prima di usarli, "
        "scarta quelli venuti male e scrivi nel report cosa hai generato e con quale servizio.\n")
    return [s["prefix"] for s in active], text
