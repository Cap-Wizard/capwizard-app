"""Registra nell'app CapWizard un reel montato o corretto da terminale, così compare in
«I miei progetti» con il suo cliente, il riassunto e lo storico delle modifiche.

Dall'app non serve: lo fa l'app stessa.

    python3 registra.py nuovo "<cartella progetto CapCut>" --nome "NOME" --template "TEMPLATE" \
        --cliente reel-chirone --clip "<cartella clip>" --report "<lavoro>/report.md"
    python3 registra.py modifica "<cartella progetto CapCut>" --testo "cosa ha chiesto" \
        --riassunto "cosa hai cambiato" [--lezione "una riga"] [--immagini a.png b.png]
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

APP_DIR = Path.home() / "Library/Application Support/CapWizard"
REGISTRY = APP_DIR / "projects.json"
MODS_DIR = APP_DIR / "modifiche"


def _load() -> list[dict]:
    try:
        data = json.loads(REGISTRY.read_text())
        return data.get("projects", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save(projects: list[dict]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2))
    tmp.replace(REGISTRY)


def nuovo(a) -> None:
    projects = _load()
    folder = str(Path(a.progetto).expanduser())
    if any(p.get("result_path") == folder for p in projects):
        print("già registrato")
        return
    projects.insert(0, {
        "id": uuid.uuid4().hex[:12], "name": a.nome, "result_path": folder,
        "report_path": str(Path(a.report).expanduser()) if a.report else None,
        "created_at": time.time(),
        "settings": {"input_path": a.clip, "name": a.nome, "template": a.template,
                     "client": a.cliente, "ai_model": "opus", "da": "terminale"},
    })
    _save(projects)
    print(f"registrato: {a.nome}")


def modifica(a) -> None:
    folder = str(Path(a.progetto).expanduser())
    project = next((p for p in _load() if p.get("result_path") == folder), None)
    if not project:
        print("progetto non registrato nell'app: niente storico")
        return
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    f = MODS_DIR / f"{project['id']}.json"
    try:
        mods = json.loads(f.read_text())
    except (OSError, ValueError):
        mods = []
    mods.append({"data": time.strftime("%Y-%m-%d %H:%M"), "testo": a.testo, "immagini": a.immagini,
                 "stato": "fatto", "riassunto": a.riassunto, "lezione": a.lezione or "", "da": "terminale"})
    f.write_text(json.dumps(mods, ensure_ascii=False, indent=1))
    print(f"modifica salvata nello storico di {project['name']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nuovo")
    n.add_argument("progetto")
    n.add_argument("--nome", required=True)
    n.add_argument("--template", required=True)
    n.add_argument("--cliente", help="nome della skill profilo del cliente, es. reel-chirone")
    n.add_argument("--clip", help="cartella delle clip")
    n.add_argument("--report", help="report.md del lavoro")
    m = sub.add_parser("modifica")
    m.add_argument("progetto")
    m.add_argument("--testo", required=True)
    m.add_argument("--riassunto", required=True)
    m.add_argument("--lezione")
    m.add_argument("--immagini", nargs="*", default=[])
    a = ap.parse_args()
    (nuovo if a.cmd == "nuovo" else modifica)(a)


if __name__ == "__main__":
    main()
