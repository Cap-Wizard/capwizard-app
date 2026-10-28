"""The team's board: the same four columns as the Home, shared through a private GitHub Project.

Any team can use it: from the app one person creates (or picks) a GitHub Projects board of their
organisation or account, and everyone who has access sees the same videos — on the Home, on
github.com and in GitHub's phone app. A video gets there only when someone shares it from their
Home: it becomes a draft item with its client, deadline, notes and who is on it (the assignee), and
a hidden line that ties it to the CapCut project on that person's Mac.

Everything goes through the GitHub CLI (`gh api graphql`) with each user's own login, which needs
the `project` scope. The board's ids live in a small JSON in the app's data folder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time

from capwizard_app import registry

STATUSES = [("da_fare", "Da fare", "GRAY"), ("in_lavorazione", "Da finire", "PURPLE"),
            ("da_revisionare", "Da revisionare", "YELLOW"), ("fatto", "Fatto", "GREEN")]
# GitHub's own column names, for a board that wasn't made by the app
_ALIASES = {"todo": "da_fare", "in progress": "in_lavorazione", "done": "fatto"}
DEFAULT_TITLE = "CapWizard · Punto della situazione"
_PATH = registry.app_data_dir() / "team_board.json"
_REF = re.compile(r"<!--\s*capwizard:(?P<id>[^>]*?)\s*-->")
_LOCK = threading.Lock()
_CACHE: dict = {"when": 0.0, "items": None}


class TeamBoardError(Exception):
    pass


# ---------------------------------------------------------------------------
# gh
# ---------------------------------------------------------------------------

def _gh() -> str | None:
    return shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
                                       if os.path.exists(p)), None)


def _q(query: str, **variables) -> dict:
    """One GraphQL call with the user's gh login."""
    gh = _gh()
    if not gh:
        raise TeamBoardError("Manca GitHub CLI: installala con `brew install gh` e fai `gh auth login`.")
    r = subprocess.run([gh, "api", "graphql", "--input", "-"], input=json.dumps({"query": query, "variables": variables}),
                       capture_output=True, text=True, timeout=60)
    try:
        out = json.loads(r.stdout or "{}")
    except ValueError:
        out = {}
    if out.get("errors") or r.returncode != 0:
        msg = "; ".join(e.get("message", "") for e in out.get("errors", [])) or (r.stderr or r.stdout).strip()
        if "scope" in msg.lower() or "INSUFFICIENT_SCOPES" in r.stdout:
            msg = "A gh manca il permesso per le bacheche: lancia `gh auth refresh -h github.com -s project`."
        raise TeamBoardError(msg[:300])
    return out.get("data") or {}


def _settings() -> dict:
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def _scopes() -> set[str]:
    gh = _gh()
    if not gh:
        return set()
    r = subprocess.run([gh, "auth", "status", "-h", "github.com"], capture_output=True, text=True, timeout=20)
    m = re.search(r"Token scopes:\s*(.*)", r.stdout + r.stderr)
    return {s.strip(" '\"") for s in m.group(1).split(",")} if m else set()


# ---------------------------------------------------------------------------
# Setup: which board
# ---------------------------------------------------------------------------

def status() -> dict:
    """What the app needs to show: is gh there, may it touch boards, which board is connected."""
    s = _settings()
    gh = bool(_gh())
    scopes = _scopes() if gh else set()
    return {"gh": gh, "permesso": "project" in scopes, "collegata": bool(s.get("project_id")),
            "titolo": s.get("title", ""), "url": s.get("url", ""), "owner": s.get("owner", ""),
            "io": s.get("me", {}).get("login", "")}


def owners() -> dict:
    """Where a board can live: the user's account and their organisations, each with its boards."""
    d = _q("""query { viewer { login id organizations(first: 50) { nodes { login id } } } }""")["viewer"]
    out = [{"login": d["login"], "id": d["id"], "tipo": "account"}]
    out += [{"login": o["login"], "id": o["id"], "tipo": "organizzazione"} for o in d["organizations"]["nodes"]]
    for o in out:
        kind = "user" if o["tipo"] == "account" else "organization"
        p = _q("query($l: String!) { %s(login: $l) { projectsV2(first: 50) { nodes { id number title url closed } } } }" % kind,
               l=o["login"])[kind]["projectsV2"]["nodes"]
        o["bacheche"] = [x for x in p if not x["closed"]]
        o["team"] = [] if kind == "user" else _q(
            "query($l: String!) { organization(login: $l) { teams(first: 50) { nodes { slug name } } } }",
            l=o["login"])["organization"]["teams"]["nodes"]
    return {"owners": out}


def _fields(project_id: str) -> dict:
    return _q("""query($id: ID!) { node(id: $id) { ... on ProjectV2 { id number title url fields(first: 50) { nodes {
                  ... on ProjectV2FieldCommon { id name dataType }
                  ... on ProjectV2SingleSelectField { id name dataType options { id name } } } } } } }""",
              id=project_id)["node"]


def _prepare(project_id: str, owner: str) -> dict:
    """Make the board speak the Home's language: the four columns in Status, plus Cliente and Scadenza."""
    p = _fields(project_id)
    fields = {f["name"]: f for f in p["fields"]["nodes"] if f}
    st = fields.get("Status")
    wanted = [n for _, n, _ in STATUSES]
    if st and [o["name"] for o in st.get("options", [])] != wanted:
        have = {o["name"].lower() for o in st.get("options", [])}
        if not have - {"todo", "in progress", "done"} or not have:     # GitHub's default columns: rename them
            _q("""mutation($f: ID!, $o: [ProjectV2SingleSelectFieldOptionInput!]!) {
                      updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: $o}) { projectV2Field { __typename } } }""",
               f=st["id"], o=[{"name": n, "color": c, "description": ""} for _, n, c in STATUSES])
    for name, kind in (("Cliente", "TEXT"), ("Scadenza", "DATE")):
        if name not in fields:
            _q("""mutation($p: ID!, $n: String!, $t: ProjectV2CustomFieldType!) {
                      createProjectV2Field(input: {projectId: $p, dataType: $t, name: $n}) { projectV2Field { __typename } } }""",
               p=project_id, n=name, t=kind)
    p = _fields(project_id)
    fields = {f["name"]: f for f in p["fields"]["nodes"] if f}
    opts = {}
    for o in fields.get("Status", {}).get("options", []):
        key = next((k for k, n, _ in STATUSES if n.lower() == o["name"].lower()), _ALIASES.get(o["name"].lower()))
        if key:
            opts[key] = o["id"]
    me = _q("query { viewer { login id } }")["viewer"]
    data = {"owner": owner, "project_id": p["id"], "number": p["number"], "title": p["title"], "url": p["url"],
            "status_field": fields.get("Status", {}).get("id", ""), "options": opts,
            "cliente_field": fields.get("Cliente", {}).get("id", ""), "scadenza_field": fields.get("Scadenza", {}).get("id", ""),
            "me": me, "links": _settings().get("links", {}) if _settings().get("project_id") == p["id"] else {}}
    _save(data)
    _CACHE.update(when=0.0, items=None)
    return data


def create(owner_login: str, owner_id: str, title: str = "", team_slug: str = "") -> dict:
    """A new private board, ready for the Home; optionally opened to one of the organisation's teams."""
    p = _q("""mutation($o: ID!, $t: String!) { createProjectV2(input: {ownerId: $o, title: $t}) { projectV2 { id } } }""",
           o=owner_id, t=title.strip() or DEFAULT_TITLE)["createProjectV2"]["projectV2"]
    data = _prepare(p["id"], owner_login)
    if team_slug:
        team = _q("query($o: String!, $s: String!) { organization(login: $o) { team(slug: $s) { id } } }",
                  o=owner_login, s=team_slug)["organization"]["team"]
        if team:
            _q("mutation($p: ID!, $t: ID!) { linkProjectV2ToTeam(input: {projectId: $p, teamId: $t}) { team { id } } }",
               p=p["id"], t=team["id"])
            # linking alone only lets the team read it: they must move cards too
            _q("""mutation($p: ID!, $c: [ProjectV2Collaborator!]!) {
                      updateProjectV2Collaborators(input: {projectId: $p, collaborators: $c}) { clientMutationId } }""",
               p=p["id"], c=[{"teamId": team["id"], "role": "WRITER"}])
    return {"ok": True, "titolo": data["title"], "url": data["url"]}


def connect(owner_login: str, project_id: str) -> dict:
    data = _prepare(project_id, owner_login)
    return {"ok": True, "titolo": data["title"], "url": data["url"]}


def disconnect() -> dict:
    _save({})
    _CACHE.update(when=0.0, items=None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _need() -> dict:
    s = _settings()
    if not s.get("project_id"):
        raise TeamBoardError("Nessuna bacheca del team collegata.")
    return s


def items(refresh: bool = False) -> list[dict]:
    """The team's cards, in the board's order (cached a few seconds: the Home asks often)."""
    s = _need()
    with _LOCK:
        if not refresh and _CACHE["items"] is not None and time.time() - _CACHE["when"] < 15:
            return _CACHE["items"]
        key_of = {v: k for k, v in s.get("options", {}).items()}
        out, after = [], None
        while True:
            page = _q("""query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
                items(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { id type
                  content { ... on DraftIssue { title body assignees(first: 5) { nodes { login avatarUrl } } }
                            ... on Issue { title body url assignees(first: 5) { nodes { login avatarUrl } } }
                            ... on PullRequest { title url } }
                  fieldValues(first: 20) { nodes {
                    ... on ProjectV2ItemFieldSingleSelectValue { optionId field { ... on ProjectV2FieldCommon { id } } }
                    ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { id } } }
                    ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { id } } } } } } } } } }""",
                      id=s["project_id"], after=after)["node"]["items"]
            for n in page["nodes"]:
                c = n.get("content") or {}
                vals = {v["field"]["id"]: v for v in n["fieldValues"]["nodes"] if v and v.get("field")}
                body = c.get("body") or ""
                ref = _REF.search(body)
                st = vals.get(s.get("status_field"), {}).get("optionId")
                out.append({"id": n["id"], "titolo": c.get("title") or "(senza titolo)",
                            "stato": key_of.get(st, "da_fare"),
                            "cliente": vals.get(s.get("cliente_field"), {}).get("text", ""),
                            "scadenza": vals.get(s.get("scadenza_field"), {}).get("date", ""),
                            "note": _REF.sub("", body).strip(),
                            "chi": [{"login": a["login"], "avatar": a["avatarUrl"]}
                                    for a in (c.get("assignees") or {}).get("nodes", [])],
                            "locale": ref.group("id") if ref else "", "url": c.get("url") or s.get("url", "")})
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        _CACHE.update(when=time.time(), items=out)
        return out


def _set_field(s: dict, item_id: str, field: str, value: dict) -> None:
    if field:
        _q("""mutation($p: ID!, $i: ID!, $f: ID!, $v: ProjectV2FieldValue!) {
                  updateProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f, value: $v}) { projectV2Item { id } } }""",
           p=s["project_id"], i=item_id, f=field, v=value)


def share(card: dict) -> dict:
    """Put one of my Home cards on the team's board, with me as the one on it."""
    s = _need()
    local = card.get("id", "")
    if local and local in s.get("links", {}):
        return {"ok": True, "id": s["links"][local], "gia": True}
    body = ((card.get("note") or "").strip() + f"\n\n<!-- capwizard:{local} -->").strip()
    item = _q("""mutation($p: ID!, $t: String!, $b: String, $a: [ID!]) {
                     addProjectV2DraftIssue(input: {projectId: $p, title: $t, body: $b, assigneeIds: $a}) { projectItem { id } } }""",
              p=s["project_id"], t=card.get("titolo") or "Video", b=body, a=[s["me"]["id"]])["addProjectV2DraftIssue"]["projectItem"]
    opt = s.get("options", {}).get(card.get("stato") or "da_fare")
    if opt:
        _set_field(s, item["id"], s.get("status_field"), {"singleSelectOptionId": opt})
    if card.get("cliente"):
        _set_field(s, item["id"], s.get("cliente_field"), {"text": card["cliente"]})
    if card.get("scadenza"):
        _set_field(s, item["id"], s.get("scadenza_field"), {"date": card["scadenza"]})
    if local:
        s.setdefault("links", {})[local] = item["id"]
        _save(s)
    _CACHE.update(when=0.0, items=None)
    return {"ok": True, "id": item["id"]}


def set_status(item_id: str, stato: str, after_id: str | None = None) -> dict:
    """Move a team card to a column (and, with after_id, right below that card; "" = on top)."""
    s = _need()
    opt = s.get("options", {}).get(stato)
    if not opt:
        return {"ok": False, "msg": "colonna sconosciuta nella bacheca"}
    _set_field(s, item_id, s.get("status_field"), {"singleSelectOptionId": opt})
    if after_id is not None:
        _q("""mutation($p: ID!, $i: ID!, $a: ID) {
                  updateProjectV2ItemPosition(input: {projectId: $p, itemId: $i, afterId: $a}) { clientMutationId } }""",
           p=s["project_id"], i=item_id, a=after_id or None)
    with _LOCK:
        for it in _CACHE["items"] or []:
            if it["id"] == item_id:
                it["stato"] = stato
    return {"ok": True}


def unshare(item_id: str) -> dict:
    s = _need()
    _q("mutation($p: ID!, $i: ID!) { deleteProjectV2Item(input: {projectId: $p, itemId: $i}) { deletedItemId } }",
       p=s["project_id"], i=item_id)
    s["links"] = {k: v for k, v in s.get("links", {}).items() if v != item_id}
    _save(s)
    _CACHE.update(when=0.0, items=None)
    return {"ok": True}


def linked(local_id: str) -> str:
    """The team card of one of my Home cards, if I shared it."""
    return _settings().get("links", {}).get(local_id, "")


def links() -> dict:
    return dict(_settings().get("links", {}))


def follow_soon(local_id: str, stato: str, log=print) -> None:
    """A shared card moved on my Home: move it on the team's board too, in the background."""
    item = linked(local_id)
    if not item or stato == "tolto":
        return

    def run() -> None:
        try:
            set_status(item, stato)
        except TeamBoardError as e:
            log(f"[team] {e}")
    threading.Thread(target=run, daemon=True).start()
