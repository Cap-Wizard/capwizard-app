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
