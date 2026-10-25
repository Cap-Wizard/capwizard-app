"""The homepage board: where every video stands.

Four columns — da fare, da finire, da revisionare, fatto. A card is either a CapCut project (made
with the app, or worked on in CapCut lately) or a video still to make: a title, the client, the
folder of clips, a deadline. Clients' templates are CapCut projects too, but never videos: they
stay off the board unless someone gives one a status by hand. When a project with the same name as a to-do appears, the two
become one card. Statuses and to-dos live in a small JSON in the app's data folder.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from capwizard_app import registry

STATUSES = [
    ("da_fare", "Da fare"),
    ("in_lavorazione", "Da finire"),
    ("da_revisionare", "Da revisionare"),
    ("fatto", "Fatto"),
]
_KEYS = {k for k, _ in STATUSES}
_PATH = registry.app_data_dir() / "board.json"
_LOCK = threading.Lock()

RECENT_APP = 45 * 86400        # a project made with the app stays on the board this long by itself
RECENT_CAPCUT = 7 * 86400      # a project only touched in CapCut, this long
DONE_SHOWN = 30 * 86400        # done cards stay visible this long


def _load() -> dict:
    try:
        data = json.loads(_PATH.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("status", {})
    data.setdefault("todos", [])
    return data


def _save(data: dict) -> None:
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(_PATH)


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def set_status(item_id: str, stato: str) -> dict:
    if stato not in _KEYS:
        return {"ok": False, "msg": "stato sconosciuto"}
    with _LOCK:
        data = _load()
        data["status"][item_id] = {"stato": stato, "quando": time.time()}
        _save(data)
    return {"ok": True}


def add_todo(d: dict) -> dict:
    titolo = (d.get("titolo") or "").strip()
    if not titolo:
        return {"ok": False, "msg": "Scrivi il titolo del video."}
    todo = {"id": f"todo:{uuid.uuid4().hex[:10]}", "titolo": titolo,
            "cliente": (d.get("cliente") or "").strip(), "cartella": (d.get("cartella") or "").strip(),
            "scadenza": (d.get("scadenza") or "").strip(), "note": (d.get("note") or "").strip(),
            "creato": time.time()}
    with _LOCK:
        data = _load()
        data["todos"].append(todo)
        _save(data)
    return {"ok": True, "todo": todo}


def update_todo(todo_id: str, d: dict) -> dict:
    with _LOCK:
        data = _load()
        for t in data["todos"]:
            if t["id"] == todo_id:
                for k in ("titolo", "cliente", "cartella", "scadenza", "note"):
                    if k in d and d[k] is not None:
                        t[k] = str(d[k]).strip()
                _save(data)
                return {"ok": True, "todo": t}
    return {"ok": False, "msg": "non trovato"}


def remove_todo(todo_id: str) -> dict:
    with _LOCK:
        data = _load()
        before = len(data["todos"])
        data["todos"] = [t for t in data["todos"] if t["id"] != todo_id]
        data["status"].pop(todo_id, None)
        _save(data)
    return {"ok": len(data["todos"]) < before}


def is_template(name: str, templates: set[str]) -> bool:
    n = _norm(name)
    return n.startswith("template") or n in templates


def board(projects: list[dict], clients: list[dict], templates: set[str] = frozenset()) -> dict:
    """Every card with its column. `projects` is what the projects list returns, `templates` the
    (normalised) names of the CapCut projects used as templates."""
    templates = {_norm(t) for t in templates}
    data = _load()
    status = data["status"]
    now = time.time()
    colour = {c["nome"]: c.get("colore") or "#6b7180" for c in clients}
    by_id = {c["id"]: c for c in clients}
    todos = {_norm(t["titolo"]): t for t in data["todos"]}
    items = []
    for p in projects:
        when = p.get("created_at") or 0
        own = status.get(p["id"], {}).get("stato")
        if not own and is_template(p["name"], templates):
            continue
        todo = todos.pop(_norm(p["name"]), None)
        if todo and not own:
            own = status.get(todo["id"], {}).get("stato")
            if own == "da_fare":
                own = None                  # it exists now: it's no longer just an idea
        made_here = p.get("source") != "capcut"
        if not own:
            recent = now - when < (RECENT_APP if made_here else RECENT_CAPCUT)
            if not recent or not p.get("exists", True):
                continue
            own = "da_revisionare" if made_here else "in_lavorazione"
        if own == "fatto" and now - status.get(p["id"], {}).get("quando", when) > DONE_SHOWN:
            continue
        items.append({"id": p["id"], "tipo": "progetto", "titolo": p["name"], "cliente": p.get("cliente", ""),
                      "colore": p.get("colore") or "#6b7180", "cover": p.get("cover", ""), "quando": when,
                      "stato": own, "scadenza": (todo or {}).get("scadenza", ""), "note": (todo or {}).get("note", ""),
                      "esiste": p.get("exists", True), "todo_id": (todo or {}).get("id", "")})
    for t in todos.values():
        own = status.get(t["id"], {}).get("stato") or "da_fare"
        if own == "fatto" and now - status.get(t["id"], {}).get("quando", t["creato"]) > DONE_SHOWN:
            continue
        client = by_id.get(t["cliente"])
        name = client["nome"] if client else t["cliente"]
        items.append({"id": t["id"], "tipo": "da_fare", "titolo": t["titolo"], "cliente": name,
                      "cliente_id": t["cliente"], "colore": colour.get(name, "#6b7180"), "cover": "",
                      "quando": t["creato"], "stato": own, "scadenza": t["scadenza"], "note": t["note"],
                      "cartella": t["cartella"], "esiste": True})
    items.sort(key=lambda i: (i["scadenza"] or "9999", -(i["quando"] or 0)))
    return {"stati": [{"id": k, "nome": n} for k, n in STATUSES], "items": items}
