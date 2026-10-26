"""The client's CapCut template, built by the app when a client is created.

Every template the app makes has the same universal layout, so the app's checklist reads it
the same way every time and Claude knows where each piece is when it reuses or edits it:

    0,0 - 3,0 s   TITOLO       riga piccola bianca (in alto), riga grande nel colore del cliente, emoji
    3,2 - 5,2 s   TITOLETTO    una riga nel colore del cliente: capitoli, dati da far leggere
    5,4 - 9,0 s   ELENCO       tre voci una sotto l'altra, entrano una dopo l'altra (5,4 / 6,0 / 6,6 s)
    9,2 - 13,2 s  SOTTOTITOLI  quattro sottotitoli da 1 s, bianchi con la parola chiave nel colore del cliente
    sempre        MUSICA       la base dell'agenzia per tutta la durata
    sulle entrate SUONI        un effetto all'entrata del titolo, del titoletto e di ogni voce dell'elenco
    sempre        COLORE       filtri / regolazione / effetti dell'agenzia per tutta la durata

A template never holds clips. Every client's template has the same pieces at the same times and the same
sounds as «TEMPLATE SOLUZIONI IMMOBILIARE» (subtitles, labels and numbers, titles in compound clips with their
motion, emojis); only the graphics are the client's: subtitle preset, title/label box and font, filters and
music, taken from a video edited for that client (`con_stile()`, the app's «Usa come template»). The layout
below is only the starting point for a client with no edited video yet.

The words are placeholders (Claude rewrites them for every video); what matters is font, colour,
size, position, animation and the sound that starts with each text. Font, shadow, animations,
music, sounds and looks come from a "master" project: the client's or the agency's "Base tecnica
CapCut" in the vault, else `CAPWIZARD_TEMPLATE_MASTER`, else "0831 (1)", else the first CapCut
project with a subtitle. Only the colour is the client's. To change a client's look afterwards,
open «TEMPLATE <CLIENTE>» in CapCut and edit it: the app reads whatever is there.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from capwizard_app.capcut import (CAPCUT_PROJECTS_DIR, _classify_audio, _is_caption_seg, _materials_index,
                                  _shown_text, _text_of, load_template)

DEFAULT_MASTER = "0831 (1)"
WHITE = [1.0, 1.0, 1.0]

# (text, start s, duration s, scale, y, colour: "white" | "client" | "key" = white with the last word in colour)
TITOLO = [("RIGA PICCOLA DEL TITOLO", 0.0, 3.0, 1.2, 0.36, "white"),
          ("RIGA GRANDE", 0.0, 3.0, 1.9, 0.22, "client")]
TITOLETTO = [("TITOLETTO DI ESEMPIO", 3.2, 2.0, 1.5, 0.28, "client")]
ELENCO = [("PRIMA VOCE", 5.4, 3.6, 1.25, 0.32, "key"),
          ("SECONDA VOCE", 6.0, 3.0, 1.25, 0.20, "key"),
          ("TERZA VOCE", 6.6, 2.4, 1.25, 0.08, "key")]
SOTTOTITOLI = [("QUESTO È UN", 9.2, 1.0), ("SOTTOTITOLO DI ESEMPIO", 10.2, 1.0),
               ("CON LA PAROLA CHIAVE", 11.2, 1.0), ("NEL COLORE GIUSTO", 12.2, 1.0)]
EMOJI = (0.0, 3.0, 1.6, 0.50)          # start, duration, scale, y
TOTAL = 13.2


def us(s: float) -> int:
    return int(round(s * 1e6))


def nid() -> str:
    return str(uuid.uuid4()).upper()


def utf16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def rgb(hex_colour: str) -> list[float]:
    h = hex_colour.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _has_caption(name: str) -> bool:
    try:
        info = load_template(name)
    except ValueError:
        return False
    ix = _materials_index(info)
    return any(_is_caption_seg(ix, s) and _text_of(ix, s)[0] is None
               for t in info["tracks"] if t["type"] == "text" for s in t["segments"])


def pick_master(bases: list[str]) -> str:
    """The project whose font, sounds and looks the new template inherits."""
    for name in [*bases, os.environ.get("CAPWIZARD_TEMPLATE_MASTER", ""), DEFAULT_MASTER]:
        if name and (CAPCUT_PROJECTS_DIR / name / "draft_info.json").exists() and _has_caption(name):
            return name
    for p in sorted(CAPCUT_PROJECTS_DIR.glob("*/draft_info.json"), key=lambda p: -p.stat().st_mtime)[:80]:
        if _has_caption(p.parent.name):
            return p.parent.name
    raise RuntimeError("Nessun progetto CapCut con sottotitoli da cui partire: creane uno a mano una volta.")


def build(master: str, colore: str, emoji: str = "⭐") -> dict:
    """The new draft_info: the universal layout dressed with the master's style and the client's colour."""
    T = load_template(master)
    ix = _materials_index(T)
    D = copy.deepcopy(T)
    M = D["materials"]
    for k in list(M):
        if isinstance(M[k], list):
            M[k] = []

    def clone_mat(mid: str) -> dict:
        kind, m = ix[mid]
        c = copy.deepcopy(m)
        c["id"] = nid()
        M.setdefault(kind, []).append(c)
        return c

    def clone_refs(seg: dict, dur: int) -> list[str]:
        out = []
        for r in seg.get("extra_material_refs") or []:
            if r not in ix:
                continue
            m = clone_mat(r)
            for a in m.get("animations") or []:
                if a.get("type") in ("caption", "group"):
                    a["duration"] = dur
            out.append(m["id"])
        return out

    segs = [(t, s) for t in T["tracks"] for s in t["segments"]]
    plain_texts = [s for t, s in segs if t["type"] == "text" and _text_of(ix, s)[0] is None]
    captions = [s for s in plain_texts if _is_caption_seg(ix, s)]
    if not captions:
        raise RuntimeError(f"«{master}» non ha sottotitoli da usare come modello.")
    P_CAP = captions[0]
    emojis = [s for s in plain_texts if not _is_caption_seg(ix, s)
              and not any(c.isalpha() for c in _shown_text(ix[s["material_id"]][1]))]
    P_EMO = emojis[0] if emojis else P_CAP
    client = rgb(colore)

    def text_seg(proto: dict, text: str, start: float, dur: float, scale: float, y: float, colour: str,
                 caption: bool = False) -> dict:
        s = copy.deepcopy(proto)
        s["id"] = nid()
        m = clone_mat(proto["material_id"])
        c = json.loads(m["content"])
        st = c["styles"][0]
        words = text.split()
        if colour == "key" and len(words) > 1:
            head = " ".join(words[:-1]) + " "
            parts = [(head, WHITE), (words[-1], client)]
        else:
            parts = [(text, client if colour in ("client", "key") else WHITE)]
        styles, pos = [], 0
        for part, col in parts:
            x = copy.deepcopy(st)
            x["range"] = [pos, pos + utf16len(part)]
            x.setdefault("fill", {}).setdefault("content", {})["solid"] = {"color": col}
            x["fill"]["content"]["render_type"] = "solid"
            styles.append(x)
            pos += utf16len(part)
        c["text"], c["styles"] = text, styles
        m["content"] = json.dumps(c, ensure_ascii=False)
        m["recognize_text"] = text.lower() if caption else text
        if caption:          # per-word timing: what makes CapCut (and the app) treat it as a subtitle
            ws, total, st_, en, tx, cur = text.lower().split(), 0, [], [], [], 0.0
            total = sum(len(w) for w in ws) or 1
            for i, w in enumerate(ws):
                wd = dur * 1000 * len(w) / total
                st_.append(int(cur)); en.append(int(cur + wd)); tx.append(w); cur += wd
                if i < len(ws) - 1:
                    st_.append(int(cur)); en.append(int(cur)); tx.append(" ")
            m["words"] = {"start_time": st_, "end_time": en, "text": tx}
        else:
            m["words"] = {"start_time": [], "end_time": [], "text": []}
        s["material_id"] = m["id"]
        s["extra_material_refs"] = clone_refs(proto, us(dur))
        s["target_timerange"] = {"start": us(start), "duration": us(dur)}
        s["clip"]["scale"] = {"x": scale, "y": scale}
        s["clip"]["transform"] = {"x": 0.0, "y": y}
        if "uniform_scale" in s:
            s["uniform_scale"] = {"on": True, "value": scale}
        return s

    blocks = [text_seg(P_CAP, *row) for row in TITOLO + TITOLETTO]
    lista = [text_seg(P_CAP, *row) for row in ELENCO]
    e0, ed, esc, ey = EMOJI
    emo = [text_seg(P_EMO, emoji, e0, ed, esc, ey, "white")]
    cap_y = (P_CAP.get("clip") or {}).get("transform", {}).get("y", -0.35)
    subs = [text_seg(P_CAP, t, a, d, 1.0, cap_y, "key", caption=True) for t, a, d in SOTTOTITOLI]

    # audio: the master's music for the whole template, one effect on every text that comes in
    music, effects = None, []
    for t, s in segs:
        if t["type"] != "audio" or ix.get(s["material_id"], ("",))[0] != "audios":
            continue
        kind = _classify_audio(ix[s["material_id"]][1], (s["target_timerange"]["duration"] or 0) / 1e6)
        if kind == "music" and music is None:
            music = s
        elif kind in ("emphasis", "transition") and len(effects) < 4:
            effects.append(s)
    audio_mats: dict[str, str] = {}

    def audio_seg(proto: dict, start: float, dur: int | None = None) -> dict:
        s = copy.deepcopy(proto)
        s["id"] = nid()
        if proto["material_id"] not in audio_mats:
            audio_mats[proto["material_id"]] = clone_mat(proto["material_id"])["id"]
        s["material_id"] = audio_mats[proto["material_id"]]
        s["extra_material_refs"] = clone_refs(proto, 0)
        d = dur if dur is not None else proto["target_timerange"]["duration"]
        d = min(d, us(TOTAL) - us(start))
        s["target_timerange"] = {"start": us(start), "duration": d}
        s["source_timerange"] = {"start": (proto.get("source_timerange") or {}).get("start", 0), "duration": d}
        return s

    sounds = []
    if effects:
        for i, start in enumerate([0.0, 3.2] + [row[1] for row in ELENCO]):
            sounds.append(audio_seg(effects[min(i, len(effects) - 1)], start))
    music_seg = [audio_seg(music, 0.0, us(TOTAL))] if music else []

    # looks: the master's filters / adjustments / effects stretched over the whole template
    looks = []
    for t, s in segs:
        if t["type"] in ("filter", "adjust", "effect"):
            x = copy.deepcopy(s)
            x["id"] = nid()
            x["material_id"] = clone_mat(s["material_id"])["id"] if s["material_id"] in ix else s["material_id"]
            x["extra_material_refs"] = clone_refs(s, us(TOTAL))
            x["target_timerange"] = {"start": 0, "duration": us(TOTAL)}
            looks.append((t["type"], x))

    def track(kind: str, segments: list, flag: int = 0) -> dict:
        return {"id": nid(), "type": kind, "flag": flag, "attribute": 0, "name": "", "is_default_name": True,
                "segments": sorted(segments, key=lambda s: s["target_timerange"]["start"])}

    tracks = [track("video", [])]
    tracks += [track(kind, [x]) for kind, x in looks]
    tracks += [track("text", subs, flag=1), track("text", blocks)]
    tracks += [track("text", [x]) for x in lista]          # the list items overlap: one lane each
    tracks += [track("text", emo)]
    if music_seg:
        tracks.append(track("audio", music_seg))
    lanes: list[list] = []
    for s in sounds:
        for lane in lanes:
            last = lane[-1]["target_timerange"]
            if last["start"] + last["duration"] <= s["target_timerange"]["start"]:
                lane.append(s)
                break
        else:
            lanes.append([s])
    tracks += [track("audio", lane) for lane in lanes]
    ri = 15000
    for i, tr in enumerate(tracks):
        for s in tr["segments"]:
            s["track_render_index"] = i
            if tr["type"] == "text":
                ri += 1
                s["render_index"] = ri
    D["tracks"], D["duration"], D["id"], D["name"] = tracks, us(TOTAL), nid(), ""
    return D


def _register(dst: Path, nome: str, draft_id: str, duration: int) -> str:
    """Put the new project at the top of CapCut's home list (only while CapCut is closed)."""
    from capwizard_app import vault
    root = CAPCUT_PROJECTS_DIR / "root_meta_info.json"
    if vault.capcut_running() or not root.exists():
        return "CapCut è aperto: chiudilo e riaprilo per vedere il template nella lista."
    r = json.load(open(root))
    shutil.copy(root, Path(str(root) + f".{time.strftime('%m%d-%H%M')}.bak"))
    st = r.get("all_draft_store") or []
    if not st:
        return "Riapri CapCut per vederlo nella lista."
    e = json.loads(json.dumps(st[0]))
    old = st[0].get("draft_fold_path", "")
    for k, v in list(e.items()):
        if isinstance(v, str) and old and old in v:
            e[k] = v.replace(old, str(dst))
    now = int(time.time() * 1e6)
    e.update(draft_id=draft_id, draft_name=nome, tm_duration=duration, tm_draft_create=now, tm_draft_modified=now,
             draft_cover="")
    st.insert(0, e)
    json.dump(r, open(root, "w"), ensure_ascii=False)
    return "È il primo in alto nella home di CapCut."


def crea(cliente: str, colore: str, bases: list[str] = (), emoji: str = "⭐") -> dict:
    """Create «TEMPLATE <CLIENTE>» in CapCut. Never overwrites an existing project."""
    nome = f"TEMPLATE {cliente.strip().upper()}"
    dst = CAPCUT_PROJECTS_DIR / nome
    if dst.exists():
        return {"ok": True, "nome": nome, "msg": f"«{nome}» c'è già in CapCut: lo lascio com'è."}
    master = pick_master([b for b in bases if b])
    D = build(master, colore, emoji)
    meta = json.load(open(CAPCUT_PROJECTS_DIR / master / "draft_meta_info.json"))
    did, now = nid(), int(time.time() * 1e6)
    meta.update(draft_name=nome, draft_fold_path=str(dst), draft_root_path=str(CAPCUT_PROJECTS_DIR), draft_id=did,
                tm_duration=D["duration"], tm_draft_create=now, tm_draft_modified=now, draft_cover="",
                draft_timeline_materials_size_=0)
    for g in meta.get("draft_materials") or []:
        g["value"] = []
    dst.mkdir()
    json.dump(D, open(dst / "draft_info.json", "w"), ensure_ascii=False)
    json.dump(meta, open(dst / "draft_meta_info.json", "w"), ensure_ascii=False)
    listed = _register(dst, nome, did, D["duration"])
    return {"ok": True, "nome": nome, "master": master,
            "msg": f"Creato in CapCut «{nome}» (stile preso da «{master}», colore {colore}). {listed}"}


def da_video(video: str, template: str) -> dict:
    """Turn a hand-edited video into the client's template: a copy of the project (compound clips need their
    subdraft/ folders) with the main track's clips and their materials removed. The template keeps its draft
    id, so it stays where it is in CapCut's list; the old one is backed up first. Both must be closed in CapCut."""
    src, dst = CAPCUT_PROJECTS_DIR / video, CAPCUT_PROJECTS_DIR / template
    for p in (src, dst):
        if (p / ".locked").exists():
            raise RuntimeError(f"«{p.name}» è aperto in CapCut: torna alla home dei progetti e riprova.")
    old_id = None
    if dst.exists():
        old_id = json.load(open(dst / "draft_meta_info.json")).get("draft_id")
        backup = Path.home() / "Library/Application Support/CapWizard/backup" / f"{template} {time.strftime('%m%d-%H%M%S')}"
        shutil.copytree(dst, backup)
    tmp = dst.with_name(dst.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".locked"))
    D = json.load(open(tmp / "draft_info.json"))
    main = next(t for t in D["tracks"] if t["type"] == "video" and not t.get("flag"))
    gone = {r for s in main["segments"] for r in [s["material_id"], *(s.get("extra_material_refs") or [])]}
    gone -= {r for t in D["tracks"] if t is not main for s in t["segments"]
             for r in [s["material_id"], *(s.get("extra_material_refs") or [])]}
    for k, v in D["materials"].items():
        if isinstance(v, list):
            D["materials"][k] = [m for m in v if not (isinstance(m, dict) and m.get("id") in gone)]
    clips = len(main["segments"])
    main["segments"], main["attribute"], D["id"] = [], 1, nid()
    meta = json.load(open(tmp / "draft_meta_info.json"))
    meta.update(draft_name=template, draft_fold_path=str(dst), draft_root_path=str(CAPCUT_PROJECTS_DIR),
                draft_id=old_id or nid())
    for g in meta.get("draft_materials") or []:
        if g.get("type") == 0:
            g["value"] = []
    for f in [tmp / "draft_info.json", tmp / "draft_info.json.bak", tmp / "template.tmp", tmp / "template-2.tmp",
              *tmp.glob("Timelines/*/draft_info.json")]:
        if f.exists():
            json.dump(D, open(f, "w"), ensure_ascii=False)
    json.dump(meta, open(tmp / "draft_meta_info.json", "w"), ensure_ascii=False)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    listed = "" if old_id else " " + _register(dst, template, meta["draft_id"], D["duration"])
    return {"ok": True, "nome": template,
            "msg": f"«{template}» rifatto da «{video}»: tolte {clips} clip, restano testi, animazioni, suoni e musica.{listed}"}


# ---------------------------------------------------------------------------
# Same pieces, the client's graphics
# ---------------------------------------------------------------------------

STRUTTURA = "TEMPLATE SOLUZIONI IMMOBILIARE"     # the reference layout: what a template holds and when
_LOOK_KEYS = ("background_alpha", "background_color", "background_fill", "background_height",
              "background_horizontal_offset", "background_round_radius", "background_style",
              "background_vertical_offset", "background_width", "border_alpha", "border_color", "border_width",
              "has_shadow", "shadow_alpha", "shadow_angle", "shadow_color", "shadow_distance", "shadow_point",
              "shadow_smoothing", "font_path", "font_name", "font_title", "font_id", "font_resource_id",
              "font_category_id", "font_category_name", "font_source_platform", "font_team_id",
              "font_third_resource_id", "font_url", "fonts", "font_size", "text_color", "text_alpha",
              "line_spacing", "letter_spacing", "alignment", "bold_width", "italic_degree", "underline",
              "check_flag")      # check_flag: which parts of the look are on (the box among them)


def _is_emoji(text: str) -> bool:
    """Only pictographs: no letters and no digits (a price like €479.000 is text)."""
    return not any(c.isalnum() and ord(c) < 0x250 for c in text)


def _dress(m: dict, look: dict) -> None:
    """Give a text material the look of another one (font, colours, box, shadow), keeping its words."""
    c = json.loads(m["content"])
    if _is_emoji(c.get("text", "")):
        return
    for k in _LOOK_KEYS:
        if k in look:
            m[k] = copy.deepcopy(look[k])
    shown = json.loads(look["content"]).get("text", "")
    if shown == shown.upper():               # the client writes its titles in capitals
        c["text"] = c["text"].upper()
    st = copy.deepcopy(json.loads(look["content"])["styles"][0])
    st["range"] = [0, utf16len(c["text"])]
    c["styles"] = [st]
    m["content"] = json.dumps(c, ensure_ascii=False)


def con_stile(stile: str, template: str, struttura: str = STRUTTURA) -> dict:
    """«template» = every piece of «struttura» at its time (texts, compound clips and their motion, sounds),
    dressed with the graphics of «stile», a video or template of the client: its subtitle preset, the look of
    its titles and labels, its filters and its music. Emojis and the structure's sounds stay as they are."""
    src, sty, dst = (CAPCUT_PROJECTS_DIR / n for n in (struttura, stile, template))
    for p in (src, sty, dst):
        if (p / ".locked").exists():
            raise RuntimeError(f"«{p.name}» è aperto in CapCut: torna alla home dei progetti e riprova.")
    S = load_template(stile)
    six = _materials_index(S)
    texts = [(t, s) for t in S["tracks"] if t["type"] == "text" for s in t["segments"]]
    cap = next((s for t, s in texts if six[s["material_id"]][0] == "text_templates"), None)
    labels = [s for t, s in texts if not t.get("flag") and six[s["material_id"]][0] == "texts"]
    look_label = six[labels[0]["material_id"]][1] if labels else None
    drafts = [d for d in S["materials"].get("drafts", []) if isinstance(d.get("draft"), dict)]
    look_title = next((m for d in drafts for m in d["draft"]["materials"].get("texts", [])
                       if not _is_emoji(json.loads(m["content"]).get("text", ""))), None) or look_label
    music = next((s for t in S["tracks"] if t["type"] == "audio" for s in t["segments"]
                  if six[s["material_id"]][1].get("type") == "music"
                  and s["target_timerange"]["duration"] >= 6e6), None)
    looks = [s for t in S["tracks"] if t["type"] in ("filter", "adjust", "effect") for s in t["segments"]]

    old_id = None
    if dst.exists():
        old_id = json.load(open(dst / "draft_meta_info.json")).get("draft_id")
        shutil.copytree(dst, Path.home() / "Library/Application Support/CapWizard/backup"
                        / f"{template} {time.strftime('%m%d-%H%M%S')}")
    tmp = dst.with_name(dst.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".locked", "crypto_key_store.dat*"))
    D = json.load(open(tmp / "draft_info.json"))
    M = D["materials"]
    ix = _materials_index(D)

    def take(mid: str) -> dict:           # copy a material from the style project
        kind, m = six[mid]
        c = copy.deepcopy(m)
        c["id"] = nid()
        M.setdefault(kind, []).append(c)
        return c

    def clone_seg(seg: dict, start: int, dur: int) -> dict:
        s = copy.deepcopy(seg)
        s["id"] = nid()
        s["material_id"] = take(seg["material_id"])["id"]
        s["extra_material_refs"] = [take(r)["id"] for r in seg.get("extra_material_refs") or [] if r in six]
        s["target_timerange"] = {"start": start, "duration": dur}
        if "source_timerange" in s and s["source_timerange"]:
            s["source_timerange"] = {"start": seg["source_timerange"]["start"], "duration": dur}
        return s

    changed = {"sottotitoli": 0, "testi": 0}
    text_tracks = [t for t in D["tracks"] if t["type"] == "text"]
    cap_track = max(text_tracks, key=lambda t: len(t["segments"]), default=None)   # the subtitles: the busiest text track
    label_scale = (labels[0].get("clip") or {}).get("scale") if labels else None
    for t in text_tracks:
        new = []
        for s in t["segments"]:
            m = ix[s["material_id"]][1]
            txt = _shown_text(m)
            if t is cap_track and cap is not None and not _is_emoji(txt):
                # the client's subtitle preset, same words and time
                c = clone_seg(cap, s["target_timerange"]["start"], s["target_timerange"]["duration"])
                tt = next(x for x in M["text_templates"] if x["id"] == c["material_id"])
                inner = copy.deepcopy(six[six[cap["material_id"]][1]["text_info_resources"][0]["text_material_id"]][1])
                inner["id"] = nid()
                M["texts"].append(inner)
                body = txt.upper().replace("\n", " ")
                cc = json.loads(inner["content"])
                st = cc["styles"][0]
                st["range"] = [0, utf16len(body)]
                cc["text"], cc["styles"] = body, [st]
                inner["content"] = json.dumps(cc, ensure_ascii=False)
                inner["recognize_text"] = body.lower()
                ws, dur = body.lower().split(), s["target_timerange"]["duration"]
                tot, cur, a, b, x = sum(len(w) for w in ws) or 1, 0.0, [], [], []
                for i, w in enumerate(ws):
                    wd = dur / 1000 * len(w) / tot
                    a.append(int(cur)); b.append(int(cur + wd)); x.append(w); cur += wd
                    if i < len(ws) - 1:
                        a.append(int(cur)); b.append(int(cur)); x.append(" ")
                inner["words"] = {"start_time": a, "end_time": b, "text": x}
                tir = tt["text_info_resources"][0]
                tir["id"], tir["text_material_id"] = nid(), inner["id"]
                tir["attach_info"]["duration"] = dur
                kinds = {six[r][0]: i for i, r in enumerate(cap["extra_material_refs"]) if r in six}
                tir["extra_material_refs"] = [c["extra_material_refs"][kinds[k]] for k in ("effects", "material_animations")
                                              if k in kinds]
                c["clip"] = copy.deepcopy(cap["clip"])
                c["render_index"], c["track_render_index"] = s.get("render_index"), s.get("track_render_index")
                new.append(c)
                changed["sottotitoli"] += 1
            else:
                if look_label is not None and not _is_emoji(txt):
                    _dress(m, look_label)       # numbers and labels: the client's label box
                    m["words"] = {"start_time": [], "end_time": [], "text": []}
                    if label_scale:
                        s["clip"]["scale"] = copy.deepcopy(label_scale)
                    changed["testi"] += 1
                new.append(s)
        t["segments"] = new
        if any(_text_of(_materials_index(D), s)[0] is not None for s in new):
            t["flag"] = 1

    # titles inside the compound clips: the client's title look (in the timeline and in subdraft/)
    def dress_draft(dd: dict) -> int:
        n = 0
        for m in dd.get("materials", {}).get("texts", []):
            if look_title is not None and not _is_emoji(_shown_text(m)):
                _dress(m, look_title)
                n += 1
        for sub in dd.get("materials", {}).get("drafts", []):
            if isinstance(sub.get("draft"), dict):
                n += dress_draft(sub["draft"])
        return n
    for d in M.get("drafts", []):
        if isinstance(d.get("draft"), dict):
            changed["testi"] += dress_draft(d["draft"])
            f = tmp / d.get("draft_file_path", "").split("##/")[-1]
            if f.is_file():
                try:
                    sub = json.load(open(f))
                    dress_draft(sub)
                    json.dump(sub, open(f, "w"), ensure_ascii=False)
                except (ValueError, OSError):
                    pass

    # the client's colour look and music for the whole length
    total = D["duration"]
    D["tracks"] = [t for t in D["tracks"] if t["type"] not in ("filter", "adjust", "effect")]
    at = 1
    for s in looks[:1] + [x for x in looks[1:] if six[x["material_id"]][1].get("name") != six[looks[0]["material_id"]][1].get("name")]:
        D["tracks"].insert(at, {"id": nid(), "type": "filter" if six[s["material_id"]][0] == "effects" else "adjust",
                                "flag": 0, "attribute": 0, "name": "", "is_default_name": True,
                                "segments": [clone_seg(s, 0, total)]})
        at += 1
    if music is not None:
        for t in D["tracks"]:
            if t["type"] == "audio" and any(ix.get(s["material_id"], ("", {}))[1].get("type") == "music"
                                            and s["target_timerange"]["duration"] >= 6e6 for s in t["segments"]):
                t["segments"] = [clone_seg(music, 0, min(total, int(six[music["material_id"]][1].get("duration") or total)))]
                t["segments"][0]["volume"] = music.get("volume", 0.14)
                break

    D["id"] = nid()
    meta = json.load(open(tmp / "draft_meta_info.json"))
    meta.update(draft_name=template, draft_fold_path=str(dst), draft_root_path=str(CAPCUT_PROJECTS_DIR),
                draft_id=old_id or nid())
    for g in meta.get("draft_materials") or []:   # the structure's own imported files: not ours ("Link media")
        g["value"] = []
    for f in [tmp / "draft_info.json", tmp / "draft_info.json.bak", tmp / "template.tmp", tmp / "template-2.tmp",
              *tmp.glob("Timelines/*/draft_info.json")]:
        if f.exists():
            json.dump(D, open(f, "w"), ensure_ascii=False)
    json.dump(meta, open(tmp / "draft_meta_info.json", "w"), ensure_ascii=False)
    (tmp / "draft_virtual_store.json").unlink(missing_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    listed = "" if old_id else " " + _register(dst, template, meta["draft_id"], total)
    return {"ok": True, "nome": template,
            "msg": (f"«{template}»: struttura e suoni di «{struttura}», grafica di «{stile}» "
                    f"({changed['sottotitoli']} sottotitoli col preset, {changed['testi']} testi restilizzati, "
                    f"filtri e musica del cliente).{listed}")}
