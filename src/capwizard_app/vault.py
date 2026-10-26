"""Clients and rules from an Obsidian vault ("VIDEO EDIT BRAIN"), the way Daniel works.

Next to the profile skills (`reel-<cliente>`), a client can live in an Obsidian vault:

    VIDEO EDIT BRAIN/
      01 Agenzie/<AGENZIA>.md            who the agency is
      02 Clienti/<AGENZIA>/<CLIENTE>.md  colours, font, "Base tecnica CapCut: <project>", lessons
      03 Stili/Regole generali di montaggio.md, Stile <…>.md, Video cine.md
      04 Audio/…, 05 Video/…, 06 Tecnica/…, 08 Feedback/Feedback e correzioni.md

The style of a video comes from general rules → the agency's style → the client's note (the most
specific wins). The "base tecnica" is a real CapCut project that is only cloned for its structure.
The vault is used when it exists: `CAPWIZARD_VAULT`, or `~/Desktop/VIDEO EDIT BRAIN`.

This module also holds what the vault workflow needs around Claude's run: music rotation, clip info
(fps → slow motion), the person cut-out for the title behind the head, copying media where the
sandboxed CapCut can open them, installing the project, editing a client's colour/font.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path

from capwizard_app.capcut import CAPCUT_PROJECTS_DIR

HOME = Path.home()
VAULT = Path(os.environ.get("CAPWIZARD_VAULT") or HOME / "Desktop/VIDEO EDIT BRAIN")
TOOLS = Path(__file__).parent / "tools"
MEDIA = HOME / "Movies/CapCut Editor"          # media copied for CapCut's sandbox, one folder per project
CAPCUT_APP = "/Applications/CapCut.app"
BUNDLE = "com.lemon.lvoverseas"
PREFIX = "vault:"


def enabled() -> bool:
    return (VAULT / "02 Clienti").is_dir()


# ---------------------------------------------------------------------------
# Tools: ffmpeg and a Python with mlx-whisper / Vision
# ---------------------------------------------------------------------------

def ffmpeg() -> str | None:
    cands = [os.environ.get("CAPWIZARD_FFMPEG"), shutil.which("ffmpeg"),
             str(HOME / "Desktop/VIDEO CLAUDE/720 STUDI0/tools/ffmpeg"), str(HOME / "video-editor-mcp/work/tools/ffmpeg")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


_TOOLS_PY: str | None = None


def tools_python() -> str:
    """An interpreter that has mlx_whisper (transcription) and pyobjc Vision (cut-out)."""
    global _TOOLS_PY
    if _TOOLS_PY:
        return _TOOLS_PY
    # in the packaged app sys.executable is the backend binary, not a Python: the user's python3
    # (where the skill's installa.sh puts mlx-whisper) stands in for it
    own = shutil.which("python3") if getattr(sys, "frozen", False) else sys.executable
    cands = [os.environ.get("CAPWIZARD_TOOLS_PYTHON"), str(HOME / "Desktop/VIDEO CLAUDE/720 STUDI0/tools/venv/bin/python"),
             str(HOME / "video-editor-mcp/work/tools/venv/bin/python"), str(HOME / "video-editor-mcp/.venv/bin/python"),
             own, *_system_pythons()]
    cands = [c for c in dict.fromkeys(cands) if c and os.path.exists(c)]
    for need in ("import mlx_whisper, Vision", "import mlx_whisper"):   # the cut-out also needs Vision
        for c in cands:
            if subprocess.run([c, "-c", need], capture_output=True).returncode == 0:
                _TOOLS_PY = c
                return c
    _TOOLS_PY = cands[0] if cands else "python3"
    return _TOOLS_PY


def _system_pythons() -> list[str]:
    found = [shutil.which("python3"), "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
             *sorted(glob.glob("/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"), reverse=True), "/usr/bin/python3"]
    if not getattr(sys, "frozen", False):
        found.insert(0, sys.executable)
    return [p for p in dict.fromkeys(found) if p]


def probe(path: str) -> tuple[float | None, int | None]:
    """(duration s, fps) from ffmpeg's header dump."""
    ff = ffmpeg()
    if not ff:
        return None, None
    e = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", e)
    f = re.search(r"Video:.*?([\d.]+) fps", e)
    d = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2) if m else None
    return d, (round(float(f.group(1))) if f else None)


def is_slowmo(fps: int | None) -> bool:
    """Talking clips are shot at 60 fps; b-roll meant for slow motion at 50p or 100 fps."""
    return bool(fps) and (45 <= fps <= 52 or fps >= 90)


def clip_info(c: str) -> dict:
    d, f = probe(c)
    return {"file": c, "durata": d, "fps": f}


def clip_line(c: dict, root: str | None = None) -> str:
    name = os.path.relpath(c["file"], root) if root else os.path.basename(c["file"])
    slow = (f" · {c['fps']} fps 🐢 girata per lo slow motion (rallentala)" if is_slowmo(c.get("fps"))
            else (f" · {c['fps']} fps" if c.get("fps") else ""))
    return f"- {name} — {c['durata']} s{slow}"


def videos_in(folder: str, recursive: bool = False) -> list[str]:
    pat = folder + ("/**/*" if recursive else "/*")
    return sorted(p for p in glob.glob(pat, recursive=recursive)
                  if p.lower().endswith((".mov", ".mp4", ".m4v")) and not os.path.basename(p).startswith("._"))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def vault_notes():
    for p in glob.glob(str(VAULT) + "/**/*.md", recursive=True):
        if "/.obsidian/" not in p:
            yield os.path.basename(p)[:-3], p


def note(name: str) -> str | None:
    for n, p in vault_notes():
        if norm(n) == norm(name):
            return p
    return None


def read_note(name: str) -> str:
    p = note(name)
    if not p:
        return ""
    s = open(p).read()
    if s.startswith("---"):
        s = s.split("---", 2)[2]
    return s.strip()


def stili() -> dict:
    """Styles = the "Stile …" notes in 03 Stili, label → note name."""
    names = sorted(os.path.basename(p)[:-3] for p in glob.glob(str(VAULT) + "/03 Stili/Stile *.md"))
    return {n[6:]: n for n in names}


def stile_for(cliente: str) -> str:
    """The client note's [[Stile …]] link, else the style whose name starts with the agency name."""
    m = re.search(r"\[\[(Stile [^\]|]+)", read_note(cliente.split(" / ")[-1]) or "")
    st = stili()
    if m and m.group(1)[6:] in st:
        return m.group(1)[6:]
    ag = norm(cliente.split(" / ")[0])
    return next((k for k in st if norm(k).startswith(ag) or ag.startswith(norm(k))), next(iter(st), ""))


def base_for(cliente: str) -> str:
    """Technical CapCut base project: the client's note, else the agency's."""
    for name in (cliente.split(" / ")[-1], cliente.split(" / ")[0] if " / " in cliente else ""):
        m = re.search(r"Base tecnica CapCut:\*\*\s*([^\n(]+)", read_note(name) or "") if name else None
        if m and (CAPCUT_PROJECTS_DIR / m.group(1).strip() / "draft_info.json").exists():
            return m.group(1).strip()
    return ""


def colore(name: str) -> str:
    """The client's main colour: the first #RRGGBB of its "Colori" line, else of the note."""
    txt = read_note(name) or ""
    line = re.search(r"^- \*\*Colori:\*\*(.*)$", txt, re.M)
    m = re.search(r"#[0-9A-Fa-f]{6}", line.group(1) if line else txt) or re.search(r"#[0-9A-Fa-f]{6}", txt)
    return m.group(0).upper() if m else "#8b919e"


def cid(agenzia: str, cliente: str) -> str:
    return f"{PREFIX}{agenzia} / {cliente}"


def split_id(client_id: str) -> tuple[str, str]:
    """"vault:EXODIA / ALBA HAIRLAB" → ("EXODIA", "ALBA HAIRLAB")."""
    ag, _, cl = client_id[len(PREFIX):].partition(" / ")
    return ag, cl


def list_clients() -> list[dict]:
    if not enabled():
        return []
    out = []
    for p in sorted(glob.glob(str(VAULT) + "/02 Clienti/*/*.md")):
        ag, cl = os.path.basename(os.path.dirname(p)), os.path.basename(p)[:-3]
        full = f"{ag} / {cl}"
        base = base_for(full)
        out.append({"id": cid(ag, cl), "nome": cl, "agenzia": ag, "template": base, "template_ok": bool(base),
                    "colore": colore(cl), "stile": stile_for(full), "descrizione": f"Cliente di {ag} (Obsidian)",
                    "path": p, "source": "vault"})
    return out


def rules_text(client_id: str) -> str:
    ag, cl = split_id(client_id)
    return read_note(cl) or ""


# ---------------------------------------------------------------------------
# The brief Claude gets for a new video (rules + clips + b-roll + music)
# ---------------------------------------------------------------------------

def musiche_progetti(n: int = 80) -> list[dict]:
    """Background music of the latest CapCut projects (newest first): the audio covering most of the timeline."""
    out = []
    if not CAPCUT_PROJECTS_DIR.is_dir():
        return out
    items = sorted(((p.stat().st_mtime, p.parent.name) for p in CAPCUT_PROJECTS_DIR.glob("*/draft_info.json")), reverse=True)[:n]
    for mt, d in items:
        try:
            j = json.load(open(CAPCUT_PROJECTS_DIR / d / "draft_info.json"))
        except (ValueError, OSError):
            continue
        dur = j.get("duration") or 1
        auds = {a["id"]: a for a in j["materials"].get("audios", [])}
        best = {}
        for t in j["tracks"]:
            if t["type"] != "audio":
                continue
            for sg in t["segments"]:
                a = auds.get(sg["material_id"])
                if a and sg["target_timerange"]["duration"] >= 0.5 * dur and sg["target_timerange"]["duration"] > 8e6:
                    name = (a.get("name") or os.path.basename(a.get("path", ""))).split("##")[0]
                    if re.search(r"voice|esv2|snaptik|\.mov$|\.mp4$", name, re.I) or "VLOG" in a.get("path", "").upper():
                        continue        # voiceovers, ripped clips and the YouTube vlogs are not reel music
                    best[name] = a.get("path", "")
        for name, path in best.items():
            out.append({"progetto": d, "data": time.strftime("%d/%m", time.localtime(mt)), "brano": name,
                        "path": path, "esiste": os.path.exists(path)})
    return out


def sezione_musica(cliente: str, progetti_cliente: set[str]) -> list[str]:
    usate = musiche_progetti()
    recenti, seen = [], set()
    for u in usate:
        if u["brano"] not in seen:
            seen.add(u["brano"])
            recenti.append(u)
    del_cliente = [u for u in usate if u["progetto"] in progetti_cliente]
    conta: dict[str, int] = {}
    for u in usate:
        conta[u["brano"]] = conta.get(u["brano"], 0) + 1
    vietati = {u["brano"] for u in recenti[:10]} | {u["brano"] for u in del_cliente}
    disp: dict[str, dict] = {}
    for u in usate:
        if u["esiste"] and u["brano"] not in vietati:
            disp.setdefault(u["brano"], u)
    mus_dir = "/Volumes/SSD Daniel/MUSICHE/"
    extra = sorted(glob.glob(mus_dir + "*.wav") + glob.glob(mus_dir + "*.mp3")) if os.path.isdir(mus_dir) else []
    extra = [p for p in extra if os.path.basename(p) not in vietati]
    lines = ["", "## Musica — ALTERNALA (i video avevano musiche troppo uguali)",
             "NON usare la stessa musica del progetto base né uno dei brani qui sotto. Scegli un brano diverso ma dello stesso genere dello stile.",
             "### Usate negli ultimi video (da evitare)"]
    lines += [f"- {u['brano']} — {u['progetto']} ({u['data']})" for u in recenti[:10]]
    if del_cliente:
        lines += ["### Già usate per questo cliente (mai ripeterle)"] + [f"- {u['brano']} — {u['progetto']}" for u in del_cliente]
    lines += ["### Disponibili sul Mac, usate poco (scegli da qui; inseriscile come audio locale dal path)"]
    lines += [f"- {b} — usata {conta.get(b, 0)}× · `{u['path']}`" for b, u in sorted(disp.items(), key=lambda x: conta.get(x[0], 0))[:25]]
    if extra:
        lines += ["### Cartella MUSICHE (Artlist, tutte libere)"] + [f"- `{p}`" for p in extra]
    return lines


def brief(job: dict, progetti_cliente: set[str] = frozenset()) -> str:
    """Everything Claude needs for a new video: clips (with fps), b-roll, general → agency → client rules, music."""
    ag, cl = split_id(job["client"])
    cliente = f"{ag} / {cl}"
    stile = job.get("stile") or stile_for(cliente)
    clips = [clip_info(c) for c in videos_in(job["input_path"]) + videos_in(job["input_path"] + "/*")]
    parts = [f"# Lavoro: {job['name']}", f"- Cliente: **{cliente}** · Stile: **{stile}** · Musica: **{'sì' if job.get('musica', True) else 'no'}**"
             + (" · **🎬 VIDEO CINE**" if job.get("cine") else ""),
             f"- Cartella clip: `{job['input_path']}`", f"- Note di Daniel: {job.get('notes') or '—'}", "",
             "## Clip",
             "Se più clip dicono le stesse cose sono take rifatte da Daniel finché non andava bene: tieni l'ULTIMA take completa "
             "(numero/ora più alti), a meno che sia chiaramente peggiore; mai due take della stessa frase nel video."]
    parts += [clip_line(c) for c in clips] or ["- (nessuna clip direttamente nella cartella: guarda nelle sottocartelle, es. CLIP/)"]
    br = job.get("broll_path")
    if br and os.path.isdir(br):
        parts += ["", f"## B-roll dati a parte (`{br}`)",
                  "Usali SOLO quando servono (quando si parla di ciò che mostrano, o per coprire un taglio/una pausa), non per forza tutti e non sempre: "
                  "muti, sopra il parlato, 1,5–3 s l'uno, mai sopra titolo e CTA. Quelli a 50p o 100 fps (🐢) sono girati per lo slow motion: "
                  "mettili rallentati (50p → velocità 0,5; 100 fps → 0,3) clonando la velocità da un segmento della base."]
        parts += [clip_line(clip_info(c), br) for c in videos_in(br, recursive=True)]
    if job.get("musica", True):
        parts += sezione_musica(cliente, set(progetti_cliente))
    if job.get("cine"):
        parts += ["", "## 🎬 VIDEO CINE — applica anche la nota \"Video cine\" (colori clip per clip, b-roll sui tagli, zoom leggeri)",
                  read_note("Video cine")]
    parts += ["", "> Lo STILE viene da queste regole (generali → agenzia → cliente). Il progetto base serve solo come base tecnica da clonare."]
    for title, name in [("Regole generali", "Regole generali di montaggio"), ("Stile dell'agenzia", stili().get(stile, "")),
                        ("Agenzia", ag), ("Regole del cliente", cl), ("Workflow", "Workflow e regole"), ("Suoni", "Libreria SFX"),
                        ("Musica", "Musica"), ("Problemi già risolti", "Problemi risolti"), ("Come si costruisce", "Come costruire un progetto CapCut")]:
        txt = read_note(name) if name else ""
        if txt:
            parts += ["", f"## {title} (dal vault: {name})", txt]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Installing a project built in a staging folder
# ---------------------------------------------------------------------------

def capcut_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "/Applications/CapCut.app/Contents/MacOS"], capture_output=True)
    return r.returncode == 0


def _draft_docs(folder: str) -> dict:
    """Every copy of the timeline CapCut keeps (main file, template-2.tmp, Timelines/<id>/…)."""
    out = {}
    for f in [folder + "/draft_info.json", folder + "/template-2.tmp"] + glob.glob(folder + "/Timelines/*/draft_info.json") \
            + glob.glob(folder + "/Timelines/*/template*.tmp"):
        try:
            d = json.load(open(f))
            if isinstance(d, dict) and "tracks" in d and "materials" in d:
                out[f] = d
        except (ValueError, OSError):
            pass
    return out


def scontorna(folder: str, nome: str, log=print) -> str:
    """'Rimuovi sfondo' done here: every copy of a clip laid on an upper video track (the layer that puts the person in front of
    the title) is replaced by a person cut-out with alpha (tools/cutout.py, macOS Vision), so the title is already behind the head."""
    docs = _draft_docs(folder)
    main = docs.get(folder + "/draft_info.json")
    if not main:
        return ""
    vids = {m["id"]: m for m in main["materials"]["videos"]}
    vtracks = [t for t in main["tracks"] if t["type"] == "video"]
    base_paths = {vids[s["material_id"]]["path"] for s in vtracks[0]["segments"] if s["material_id"] in vids} if vtracks else set()
    jobs = {}
    for t in vtracks[1:]:
        for sg in t["segments"]:
            m = vids.get(sg["material_id"])
            if not m or m.get("type") != "video" or m.get("path") not in base_paths or (m.get("matting") or {}).get("flag"):
                continue
            jobs[sg["id"]] = (m["path"], sg["source_timerange"]["start"], sg["source_timerange"]["duration"])
    if not jobs:
        return ""
    (MEDIA / nome).mkdir(parents=True, exist_ok=True)
    made = {}
    for i, (path, st, du) in enumerate(sorted(set(jobs.values()))):
        out = str(MEDIA / nome / f"scontornato {i + 1} - {os.path.splitext(os.path.basename(path))[0]}.mov")
        log(f"✂️ Scontorno la persona ({du / 1e6:.1f} s di {os.path.basename(path)})…")
        r = subprocess.run([tools_python(), str(TOOLS / "cutout.py"), path, str(st / 1e6), str(du / 1e6), out],
                           capture_output=True, text=True, env={**os.environ, "CAPWIZARD_FFMPEG": ffmpeg() or ""})
        if os.path.exists(out) and '"ok": true' in r.stdout:
            made[(path, st, du)] = out
        else:
            log("Scontorno non riuscito: " + (r.stderr or r.stdout)[-300:])
    for f, d in docs.items():
        mats = d["materials"]["videos"]
        byid = {m["id"]: m for m in mats}
        for t in d["tracks"]:
            for sg in t["segments"]:
                key = jobs.get(sg["id"])
                if key not in made or sg["material_id"] not in byid:
                    continue
                m = json.loads(json.dumps(byid[sg["material_id"]]))
                m.update(id=str(uuid.uuid4()).upper(), path=made[key], material_name=os.path.basename(made[key]),
                         duration=key[2], width=1080, height=1920)
                mats.append(m)
                byid[m["id"]] = m
                sg["material_id"] = m["id"]
                sg["source_timerange"]["start"] = 0
        json.dump(d, open(f, "w"), ensure_ascii=False)
    return f"✂️ Persona scontornata in automatico ({len(made)} clip): il titolo è già dietro la testa." if made else ""


def _capcut_bookmarks() -> set:
    import plistlib
    prefs = HOME / f"Library/Containers/{BUNDLE}/Data/Library/Preferences/{BUNDLE}.plist"
    try:
        return {k.lstrip("/") for k, v in plistlib.load(open(prefs, "rb")).items() if isinstance(v, bytes)}
    except Exception:  # noqa: BLE001
        return set()


def porta_media(folder: str, nome: str) -> str:
    """CapCut is sandboxed: it only opens files under ~/Movies or files it has a bookmark for. Copy every other media
    file of the project into ~/Movies/CapCut Editor/<nome>/ and point all the draft files there (clips open linked)."""
    files = [f for f in glob.glob(folder + "/**/*", recursive=True)
             if os.path.isfile(f) and f.endswith((".json", ".tmp")) and not f.endswith(".bak")]
    docs = {}
    for f in files:
        try:
            docs[f] = json.load(open(f))
        except (ValueError, UnicodeDecodeError, OSError):
            pass
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
    marks = _capcut_bookmarks()
    ok_roots = (f"{HOME}/Movies/", f"{HOME}/Library/Containers/{BUNDLE}/")
    exts = (".mov", ".mp4", ".m4v", ".mts", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif",
            ".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac")
    todo = sorted(p for p in found if p.lower().endswith(exts) and os.path.isfile(p)
                  and not p.startswith(ok_roots) and p.lstrip("/") not in marks)
    if not todo:
        return "Media già accessibili a CapCut."
    dst_dir = MEDIA / nome
    dst_dir.mkdir(parents=True, exist_ok=True)
    mapping, used = {}, set()
    for p in todo:
        b = os.path.basename(p)
        if b in used:
            b = os.path.basename(os.path.dirname(p)) + " - " + b
        used.add(b)
        dst = str(dst_dir / b)
        if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(p)):
            shutil.copy2(p, dst)
        mapping[p] = dst

    def rep(o):
        if isinstance(o, dict):
            return {k: (mapping[v] if isinstance(v, str) and v in mapping else rep(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [mapping[v] if isinstance(v, str) and v in mapping else rep(v) for v in o]
        return o
    for f, d in docs.items():
        n = rep(d)
        if n != d:
            json.dump(n, open(f, "w"), ensure_ascii=False)
    return f"📦 Copiati {len(mapping)} file in `{dst_dir}` (CapCut li apre già collegati)."


def install(staging: str, nome: str, log=print) -> str:
    """Copy a project built in `staging` into CapCut's projects, cut out the person for the title and link the media.
    With CapCut closed it is also added to the home list (root_meta_info); with CapCut open, restart CapCut to see it."""
    dst = CAPCUT_PROJECTS_DIR / nome
    if dst.exists():
        raise RuntimeError(f"Esiste già un progetto CapCut «{nome}».")
    shutil.copytree(staging, dst, ignore=shutil.ignore_patterns("draft.extra", "crypto_key_store.dat*", ".locked"))
    log("📦 Installo il progetto: scontorno la persona per il titolo e collego le clip…")
    cut = scontorna(str(dst), nome, log)
    media = porta_media(str(dst), nome)
    meta_p, info_p = dst / "draft_meta_info.json", dst / "draft_info.json"
    meta, d = json.load(open(meta_p)), json.load(open(info_p))
    meta.update(draft_name=nome, draft_fold_path=str(dst), draft_root_path=str(CAPCUT_PROJECTS_DIR))
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False)
    root = CAPCUT_PROJECTS_DIR / "root_meta_info.json"
    listed = ""
    if not capcut_running() and root.exists():
        r = json.load(open(root))
        backup = Path(str(root) + f".{time.strftime('%m%d-%H%M')}.bak")
        shutil.copy(root, backup)
        st = r.get("all_draft_store") or []
        if st:
            e = json.loads(json.dumps(st[0]))
            old = st[0].get("draft_fold_path", "")
            for k, v in list(e.items()):
                if isinstance(v, str) and old and old in v:
                    e[k] = v.replace(old, str(dst))
            now = int(time.time() * 1e6)
            e.update(draft_id=meta.get("draft_id") or str(uuid.uuid4()).upper(), draft_name=nome, tm_duration=d["duration"],
                     tm_draft_create=now, tm_draft_modified=now)
            st.insert(0, e)
            json.dump(r, open(root, "w"), ensure_ascii=False)
            listed = "È il primo in alto nella home di CapCut."
    else:
        listed = "CapCut è aperto: chiudilo e riaprilo per vederlo nella lista."
    return " ".join(x for x in (f"✅ Progetto «{nome}» installato.", cut, media, listed) if x)


def open_capcut() -> None:
    subprocess.run(["open", CAPCUT_APP])


# ---------------------------------------------------------------------------
# Notes the app writes: video history, new client, client's colour/font
# ---------------------------------------------------------------------------

def video_note(nome: str, client_id: str, stile: str = "", riassunto: str = "", line: str = "") -> None:
    """The video's note in 05 Video: created on first use, every change request appended to its history."""
    ag, cl = split_id(client_id)
    f = note(nome)
    if not f:
        d = VAULT / "05 Video" / (ag or cl or "Altro")
        d.mkdir(parents=True, exist_ok=True)
        f = str(d / (re.sub(r"[/:]", "-", nome) + ".md"))
        open(f, "w").write(f"---\ncssclasses: [video]\n---\n# {nome}\nCliente [[{cl}]] · [[{ag}]] · stile [[{stili().get(stile, '')}]] · "
                           f"montato da CapWizard il {time.strftime('%Y-%m-%d')}\n\n{riassunto}\n")
        cp = note(cl)
        if cp and f"[[{nome}]]" not in open(cp).read():
            open(cp, "a").write(f"\n- Video: [[{nome}]]\n")
    if line:
        s = open(f).read()
        if "## Modifiche richieste" not in s:
            s = s.rstrip() + "\n\n## Modifiche richieste (dall'app)\n"
        open(f, "w").write(s.rstrip("\n") + "\n" + line)


def feedback(line: str) -> None:
    fb = note("Feedback e correzioni")
    if fb:
        open(fb, "a").write(line if line.endswith("\n") else line + "\n")


def crea_cliente(ag: str, cl: str, col: str, font: str = "", nota: str = "") -> dict:
    """New client of an agency already in the vault: only its colour (and a font if the client wants one)."""
    ag, cl = ag.strip().upper(), re.sub(r"[/:]", "-", cl.strip().upper())
    col = (col or "").strip().upper()
    if not (ag and cl):
        return {"ok": False, "msg": "Scrivi agenzia e nome del cliente."}
    if not re.fullmatch(r"#[0-9A-F]{6}", col):
        return {"ok": False, "msg": "Colore non valido: usa #RRGGBB."}
    f = VAULT / "02 Clienti" / ag / f"{cl}.md"
    if f.exists() or note(cl):
        return {"ok": False, "msg": f"{cl} esiste già: per cambiare colore o font usa ✏️ Modifica cliente."}
    f.parent.mkdir(parents=True, exist_ok=True)
    stile = stile_for(f"{ag} / {cl}")
    base = base_for(f"{ag} / {cl}")
    txt = (f"---\ncssclasses: [cliente]\n---\n# {cl}\nCliente di [[{ag}]] · aggiunto da CapWizard il {time.strftime('%d/%m/%Y')}\n"
           + (f"- **Base tecnica CapCut:** {base}\n" if base else "")
           + "\n## Scritte\n"
           + (f"- **Font:** {font} (lo vuole il cliente)\n" if font else f"- **Font:** come gli altri clienti di [[{ag}]] (vedi [[Stile {stile}]])\n")
           + f"- **Colori:** {col} (parole chiave e riga colorata del titolo) · Bianco\n"
           + f"- **Grandezza / ombre:** come gli altri clienti di [[{ag}]]\n"
           + "\n## Regola\nColore del cliente = parole chiave e riga colorata del titolo; bianco = resto del testo. Tutto il resto segue lo stile dell'agenzia.\n"
           + (f"\n## Note\n{nota}\n" if nota else "")
           + f"\nStile generale: [[Stile {stile}]]\n")
    f.write_text(txt)
    an = note(ag)
    if an and f"[[{cl}]]" not in open(an).read():
        open(an, "a").write(f"\n- Cliente: [[{cl}]]\n")
    return {"ok": True, "id": cid(ag, cl), "msg": f"{cl} aggiunto a {ag} (colore {col}{', font ' + font if font else ''})."}


def dati_cliente(client_id: str) -> dict:
    ag, cl = split_id(client_id)
    f = note(cl)
    if not f:
        return {"ok": False}
    s = open(f).read()
    col = re.search(r"^- \*\*Colori:\*\*(.*)$", s, re.M)
    font = re.search(r"^- \*\*Font:\*\*\s*(.*)$", s, re.M)
    base = re.search(r"Base tecnica CapCut:\*\*\s*([^\n(]+)", s)
    hexm = re.search(r"#[0-9A-Fa-f]{6}", col.group(1)) if col else None
    return {"ok": True, "colore": hexm.group(0).upper() if hexm else "", "colori": col.group(1).strip() if col else "",
            "font": font.group(1).strip() if font else "", "base": base.group(1).strip() if base else ""}


def salva_cliente(client_id: str, col: str = "", font: str = "", base: str = "", regola: str = "") -> dict:
    """Change a client's colour / font / base / add a rule, straight in its note (old colour replaced everywhere)."""
    ag, cl = split_id(client_id)
    f = note(cl)
    if not f:
        return {"ok": False, "msg": "Nota del cliente non trovata."}
    old = dati_cliente(client_id)
    s = open(f).read()
    day = time.strftime("%d/%m")
    cambi = []
    col = (col or "").strip().upper()
    if col and not re.fullmatch(r"#[0-9A-F]{6}", col):
        return {"ok": False, "msg": "Colore non valido: usa #RRGGBB."}
    if col and col != old["colore"]:
        line = re.search(r"^- \*\*Colori:\*\*.*$", s, re.M)
        if line and old["colore"]:
            newline = re.sub(r"([A-Za-zÀ-ÿ]+\s)?" + re.escape(old["colore"]), col, line.group(0), count=1, flags=re.I)
            s = s.replace(line.group(0), newline)
            s = re.sub(re.escape(old["colore"]), col, s, flags=re.I)
        elif line:
            parts = line.group(0).split("·", 1)
            s = s.replace(line.group(0), f"- **Colori:** {col} " + ("· " + parts[1].strip() if len(parts) > 1 else "· Bianco"))
        elif "## Scritte" in s:
            s = s.replace("## Scritte\n", f"## Scritte\n- **Colori:** {col} · Bianco\n", 1)
        else:
            s = s.rstrip() + f"\n\n## Scritte\n- **Colori:** {col} · Bianco\n"
        cambi.append(f"colore {old['colore'] or '—'} → {col}")
    font = (font or "").strip()
    if font and font != old["font"]:
        if re.search(r"^- \*\*Font:\*\*.*$", s, re.M):
            s = re.sub(r"^- \*\*Font:\*\*.*$", f"- **Font:** {font}", s, count=1, flags=re.M)
        else:
            s = s.replace("## Scritte\n", f"## Scritte\n- **Font:** {font}\n", 1) if "## Scritte" in s else s.rstrip() + f"\n\n## Scritte\n- **Font:** {font}\n"
        cambi.append(f"font → {font}")
    base = (base or "").strip()
    if base and base != old["base"]:
        if re.search(r"Base tecnica CapCut:\*\*\s*[^\n]*", s):
            s = re.sub(r"(Base tecnica CapCut:\*\*)\s*[^\n]*", lambda m: m.group(1) + " " + base, s, count=1)
        else:
            s = re.sub(r"^(# .*\n.*\n)", lambda m: m.group(1) + f"- **Base tecnica CapCut:** {base}\n", s, count=1, flags=re.M)
        cambi.append(f"base tecnica → {base}")
    regola = (regola or "").strip()
    if regola:
        for head in ("## Regole del cliente\n", "## Regola\n"):
            if head in s:
                s = s.replace(head, f"{head}- {regola} ({day})\n", 1)
                break
        else:
            s = s.rstrip() + f"\n\n## Regole del cliente\n- {regola} ({day})\n"
        cambi.append(f"regola: {regola}")
    if not cambi:
        return {"ok": False, "msg": "Niente da cambiare."}
    if "## Lezioni" not in s:
        s = s.rstrip() + "\n\n## Lezioni\n"
    s = s.rstrip("\n") + f"\n- {day}: aggiornato da CapWizard (richiesta del cliente): " + "; ".join(cambi) + "\n"
    open(f, "w").write(s)
    feedback(f"- **{day} · [[{cl}]]:** " + "; ".join(cambi))
    return {"ok": True, "msg": "Aggiornato: " + "; ".join(cambi)}


def agenzie() -> list[str]:
    return sorted(os.path.basename(p) for p in glob.glob(str(VAULT) + "/02 Clienti/*") if os.path.isdir(p)) if enabled() else []
