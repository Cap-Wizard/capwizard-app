"""Costruisce un progetto CapCut da un TEMPLATE A CATALOGO.

Un template a catalogo (formato di Gabriele, novembre 2026) contiene ogni pezzo UNA volta sola, uno dopo
l'altro, e lo script ne riconosce il ruolo dal tipo, non dal tempo:
  - TITOLO        la clip composta che parte a 0 s (righe nel colore del cliente, riga bianca, emoji dietro)
  - SOTTOTITOLO   il preset dei sottotitoli (text_template)
  - PAROLA        il testo semplice grande (es. «125M») con la sua animazione d'entrata
  - DATO          clip composta con un testo (es. «€479.000») e la sua animazione di gruppo
  - EMOJI         clip composte con solo emoji, ognuna con un'animazione diversa (Sway In, Fall Right…)
  - TRANSIZIONI   le clip composte «TRANSITION»: le transizioni attaccate ai loro segmenti
  - FILTRI        ogni filtro / regolazione / effetto, per tutta la durata
  - MUSICHE       le tracce musica: lo stile del cliente, se ne usa UNA per video
  - SUONI         la traccia dei suoni, ognuno col suo volume
Regola: gli effetti non si ripetono di fila (transizioni, animazioni delle emoji e suoni girano).

uso:
  python3 costruisci_catalogo.py --catalogo "TEMPLATE X"        stampa il catalogo (cosa c'è e cosa prendere)
  python3 costruisci_catalogo.py CARTELLA_OUT "NOME" config.json  scrive draft_info.json, draft_meta_info.json, plan.json

Config: come per costruisci_draft.py (template, modello, clipdir, ext, cuts, trans_after, auto_subs, words, fix,
regia) più «musica» (inizio del nome, facoltativo), «music_db» (volume della musica in dB, es. -21),
«vol» ({inizio del nome del suono: volume}), «woosh_prima» (secondi prima della transizione, 0.2) e
«taglia_testa» ({inizio del nome: secondi da togliere all'inizio del suono}). Funzioni della regia:
  titolo("RIGA\\nRIGA", "riga bianca", start, end)   parola("TESTO", start, end)   dato("9-14 ANNI", start, end)
  elenco([(voce, start), …], end, "etichetta")        emoji("📱", start, secondi)    cta(start)
"""
import copy, json, os, re, subprocess, sys, time, uuid

ROOT_DEF = "~/Movies/CapCut/User Data/Projects/com.lveditor.draft"
UUIDRE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")
PX_PER_SIZE = 6.0        # 1 punto di grandezza CapCut ~ 6 px su una tela 1080x1920 (misurato, 6/11/2026)
SAFE_W = 940             # larghezza massima di un testo in px (margini di 70 px)


def nid():
    return str(uuid.uuid4()).upper()


def us(s):
    return int(round(round(s * 50) / 50 * 1e6))


def utf16len(s):
    return len(s.encode("utf-16-le")) // 2


def index(d):
    return {m["id"]: (k, m) for k, v in d.get("materials", {}).items() if isinstance(v, list)
            for m in v if isinstance(m, dict) and "id" in m}


def jl(d, f):
    for name in (f, f + ".bak", "template-2.tmp") if f == "draft_info.json" else (f,):
        if os.path.exists(os.path.join(d, name)):
            return json.load(open(os.path.join(d, name)))
    raise FileNotFoundError(os.path.join(d, f))


def is_emoji(t):
    return bool(t.strip()) and not any(c.isalnum() and ord(c) < 0x250 for c in t)


def text_of(ix, seg):
    k, m = ix.get(seg["material_id"], (None, None))
    if k == "text_templates":
        m = ix[m["text_info_resources"][0]["text_material_id"]][1]
    return json.loads(m["content"])["text"] if k in ("texts", "text_templates") else None


def width_ratio(text, font_path):
    """larghezza della riga in em (1 em = grandezza del font): dal file del font se c'è, se no stimata."""
    try:
        from PIL import ImageFont
        f = ImageFont.truetype(font_path, 1000)
        return f.getlength(text) / 1000
    except Exception:  # noqa: BLE001  (PIL o font mancanti: stima prudente)
        return 0.68 * len(text)


# ---------------------------------------------------------------------------
# il catalogo
# ---------------------------------------------------------------------------

def catalogo(T):
    ix = index(T)
    cat = {"titolo": None, "sottotitolo": None, "parola": None, "dati": [], "emoji": [], "transizioni": [],
           "filtri": [], "musiche": [], "suoni": []}
    seen_tr = set()
    for t in T["tracks"]:
        for s in t["segments"]:
            k, m = ix.get(s["material_id"], (None, {}))
            if t["type"] == "text" and k == "text_templates" and cat["sottotitolo"] is None:
                cat["sottotitolo"] = s
            elif t["type"] == "text" and k == "texts" and not is_emoji(text_of(ix, s) or "") and cat["parola"] is None:
                cat["parola"] = s
            elif t["type"] in ("filter", "adjust", "effect"):
                cat["filtri"].append(s)
            elif t["type"] == "audio" and k == "audios":
                (cat["musiche"] if m.get("type") == "music" else cat["suoni"]).append(s)
            elif t["type"] == "video" and t.get("flag"):
                dr = next((ix[r][1] for r in s.get("extra_material_refs") or [] if ix.get(r, ("",))[0] == "drafts"), None)
                if not dr or not isinstance(dr.get("draft"), dict):
                    continue
                for r in s.get("extra_material_refs") or []:
                    if ix.get(r, ("",))[0] == "transitions" and ix[r][1].get("name") not in seen_tr:
                        seen_tr.add(ix[r][1].get("name"))
                        cat["transizioni"].append(ix[r][1])
                texts = [json.loads(x["content"])["text"] for x in dr["draft"]["materials"].get("texts", [])]
                if texts and all(x.strip().upper() == "TRANSITION" for x in texts):
                    continue
                if texts and all(is_emoji(x) for x in texts):
                    cat["emoji"].append(s)
                elif s["target_timerange"]["start"] < 1_000_000 and cat["titolo"] is None:
                    cat["titolo"] = s
                else:
                    cat["dati"].append(s)
    cat["suoni"].sort(key=lambda s: s["target_timerange"]["start"])
    return cat, ix


def anims(ix, seg):
    return [a.get("name") for r in seg.get("extra_material_refs") or [] if ix.get(r, ("",))[0] == "material_animations"
            for a in ix[r][1].get("animations") or []]


def combo_texts(ix, seg):
    dr = next(ix[r][1] for r in seg["extra_material_refs"] if ix.get(r, ("",))[0] == "drafts")
    return [json.loads(x["content"])["text"] for x in dr["draft"]["materials"].get("texts", [])]


def mostra(name, root):
    T = jl(os.path.join(root, name), "draft_info.json")
    cat, ix = catalogo(T)
    print(f"Catalogo di «{name}»")
    if cat["titolo"]:
        print("  TITOLO      ", combo_texts(ix, cat["titolo"]), anims(ix, cat["titolo"]))
    if cat["sottotitolo"]:
        print("  SOTTOTITOLO ", ix[cat["sottotitolo"]["material_id"]][1].get("name"), repr(text_of(ix, cat["sottotitolo"])))
    if cat["parola"]:
        print("  PAROLA      ", repr(text_of(ix, cat["parola"])), anims(ix, cat["parola"]))
    for s in cat["dati"]:
        print("  DATO        ", combo_texts(ix, s), anims(ix, s))
    for s in cat["emoji"]:
        print("  EMOJI       ", combo_texts(ix, s), anims(ix, s))
    print("  TRANSIZIONI ", [m.get("name") for m in cat["transizioni"]])
    print("  FILTRI      ", [ix[s["material_id"]][1].get("name") or ix[s["material_id"]][1].get("type") for s in cat["filtri"]])
    print("  MUSICHE     ", [ix[s["material_id"]][1].get("name") for s in cat["musiche"]])
    print("  SUONI       ", [(ix[s["material_id"]][1].get("name"), round(s.get("volume", 1.0), 3)) for s in cat["suoni"]])
    missing = [k for k in ("titolo", "sottotitolo") if not cat[k]] + [k for k in ("transizioni", "musiche", "suoni") if not cat[k]]
    if missing:
        print("  MANCANO     ", missing)


if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--catalogo":
    mostra(sys.argv[2], os.path.expanduser(ROOT_DEF))
    sys.exit(0)

# ---------------------------------------------------------------------------
# montaggio
# ---------------------------------------------------------------------------

OUT, NAME, CFG = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
ROOT = os.path.expanduser(CFG.get("projects_root", ROOT_DEF))
CLIPDIR, EXT = CFG["clipdir"], CFG.get("ext", ".MP4")
TPL, MOD = os.path.join(ROOT, CFG["template"]), os.path.join(ROOT, CFG["modello"])
T, TM = jl(TPL, "draft_info.json"), jl(TPL, "draft_meta_info.json")
L, LM = jl(MOD, "draft_info.json"), jl(MOD, "draft_meta_info.json")
CAT, TIX = catalogo(T)
LIX = index(L)
assert CAT["titolo"] and CAT["sottotitolo"], "il template non è un catalogo: manca il titolo o il preset dei sottotitoli"
D = copy.deepcopy(T)
M = D["materials"]
for k in list(M):
    if isinstance(M[k], list):
        M[k] = []


def add(kind, m):
    M.setdefault(kind, []).append(m)
    return m


def clone_mat(ix, mid):
    kind, m = ix[mid]
    c = copy.deepcopy(m)
    c["id"] = nid()
    add(kind, c)
    return kind, c


def durata_reale(path):
    """durata in microsecondi = fotogrammi / fps (CapCut non trova i file con durata diversa)"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets",
                        "-show_entries", "stream=nb_read_packets,r_frame_rate", "-of", "csv=p=0", path],
                       capture_output=True, text=True).stdout.strip().split(",")
    num, den = r[0].split("/")
    return int(int(r[1]) * 1e6 * int(den) / int(num))


class Giro:
    """sceglie dalla tavolozza senza ripetere di fila lo stesso pezzo"""
    def __init__(self, items, key):
        self.items, self.key, self.i, self.last = list(items), key, 0, None

    def next(self, prefer=None):
        if not self.items:
            return None
        rot = self.items[self.i:] + self.items[:self.i]
        # prefer(x) = posizione nella lista delle preferenze (più bassa = meglio) o None
        order = (sorted((x for x in rot if prefer(x) is not None), key=prefer) + rot) if prefer else rot
        for x in order:
            if self.key(x) != self.last or len(self.items) == 1:
                self.last = self.key(x)
                self.i = (self.items.index(x) + 1) % len(self.items)
                return x
        return order[0]


# ---------------- spezzoni ----------------
lv = [t for t in L["tracks"] if t["type"] == "video" and t["flag"] == 0][0]["segments"]
P_VSEG = next((s for s in lv if any(LIX.get(r, ("",))[0] == "transitions" for r in s["extra_material_refs"])), lv[0])
P_VMAT = LIX[P_VSEG["material_id"]][1]
CUTS, TRANS_AFTER, CLOSE = CFG["cuts"], set(CFG.get("trans_after", [])), set(CFG.get("close", []))
ROT, CLIPDUR = CFG.get("rot", {}), CFG.get("clipdur", {})
for c in {x[0] for x in CUTS}:
    CLIPDUR.setdefault(c, durata_reale(f"{CLIPDIR}/{c}{EXT}") / 1e6)

segs, subs_v, t = [], [], 0
for n, cut in enumerate(CUTS, 1):
    c, a, b = cut[0], cut[1], cut[2]
    gaps = cut[4] if len(cut) > 4 else []
    sa, sb = us(a), us(b)
    assert sb <= int(CLIPDUR[c] * 1e6), c
    pieces, cur = [], sa
    for g0, g1 in gaps:
        pieces.append((cur, us(g0)))
        cur = us(g1)
    pieces.append((cur, sb))
    t0 = t
    for k, (p0, p1) in enumerate(pieces):
        subs_v.append((n, c, p0, p1, t, p1 - p0, k == len(pieces) - 1))
        t += p1 - p0
    segs.append((n, c, sa, sb, t0, t - t0))
TOTAL = t


def tl(n, src_s):
    x = us(src_s)
    _, c, sa, sb, ts, d = segs[n - 1]
    assert sa - 300_000 <= x <= sb + 300_000, (n, src_s, sa, sb)
    x = min(max(x, sa), sb)
    for (m, c2, p0, p1, pt, pd, _) in subs_v:
        if m == n and x <= p1:
            return pt + max(0, x - p0)
    return ts + d


def seg_end(n):
    return segs[n - 1][4] + segs[n - 1][5]


def seg_start(n):
    return segs[n - 1][4]


vmat = {}
for c in sorted({x[1] for x in segs}):
    m = copy.deepcopy(P_VMAT)
    m.update(id=nid(), path=f"{CLIPDIR}/{c}{EXT}", media_path="", unique_id="", local_material_id="",
             material_name=f"{c}{EXT}", duration=int(CLIPDUR[c] * 1e6), width=1080, height=1920, has_audio=True)
    if c in CFG.get("landscape", []):
        m["width"], m["height"] = 1920, 1080
    m["stable"] = {"stable_level": 0, "matrix_path": "", "time_range": {"start": 0, "duration": 0}}
    add("videos", m)
    vmat[c] = m


def kf(prop, pts):
    return {"id": nid(), "material_id": "", "property_type": prop, "keyframe_list": [
        {"id": nid(), "curveType": "Line", "time_offset": int(o), "left_control": {"x": 0.0, "y": 0.0},
         "right_control": {"x": 0.0, "y": 0.0}, "values": [v], "string_value": "", "graphID": ""} for o, v in pts]}


trans_giro = Giro(CAT["transizioni"], key=lambda m: m.get("name"))
trans_used = []
vsegs = []
for n, c, sa, sb, ts, d, is_last in subs_v:
    s = copy.deepcopy(P_VSEG)
    s["id"] = nid()
    refs = []
    for r in P_VSEG["extra_material_refs"]:
        if LIX[r][0] != "transitions":
            refs.append(clone_mat(LIX, r)[1]["id"])
    if n in TRANS_AFTER and is_last and CAT["transizioni"]:
        tr = copy.deepcopy(trans_giro.next())
        tr["id"] = nid()
        add("transitions", tr)
        refs.append(tr["id"])
        trans_used.append((round((ts + d) / 1e6, 2), tr.get("name")))
    s["extra_material_refs"] = refs
    s["material_id"] = vmat[c]["id"]
    s["source_timerange"] = {"start": sa, "duration": d}
    s["target_timerange"] = {"start": ts, "duration": d}
    s["volume"] = s["last_nonzero_volume"] = 1.0
    s["track_attribute"] = s["render_index"] = 0
    base = 1.4 if n in CLOSE else float(CFG.get("scale_override", {}).get(str(n), 1.0))
    if c in CFG.get("landscape", []):
        base = round(base * 1920 / 1080, 4)
    s["clip"]["transform"] = {"x": 0.0, "y": -0.38 if n in CLOSE else 0.0}
    s["clip"]["rotation"] = float(ROT.get(c, 0))
    s["common_keyframes"], final = [], base
    if n in TRANS_AFTER and is_last:
        final = round(base * 1.15, 4)
        s["common_keyframes"] = [kf("KFTypeScaleX", [(max(sa, sb - 1_000_000), base), (sb - 20000, final)])]
    s["clip"]["scale"] = {"x": final, "y": final}
    s["uniform_scale"] = {"on": True, "value": 1.0}
    vsegs.append(s)

# ---------------- testi e clip composte ----------------


def anim_clone(ix, proto_seg, dur):
    out = []
    for r in proto_seg["extra_material_refs"]:
        if r not in ix:
            continue
        kind, m = clone_mat(ix, r)
        if kind == "material_animations":
            for a in m["animations"]:
                if a["type"] in ("caption", "group"):
                    a["duration"] = dur
                else:
                    a["duration"] = min(a["duration"], dur)
        out.append(m["id"])
    return out


def set_text(m, text):
    c = json.loads(m["content"])
    st = c["styles"][0]
    st["range"] = [0, utf16len(text)]
    c["styles"], c["text"] = [st], text
    m["content"] = json.dumps(c, ensure_ascii=False)
    m["recognize_text"] = text.strip()


def size_of(m):
    try:
        return float(json.loads(m["content"])["styles"][0].get("size") or m.get("font_size") or 10)
    except (ValueError, KeyError, IndexError):
        return 10.0


def caption(text, start, dur, scale=1.0, y=None):
    P = CAT["sottotitolo"]
    s = copy.deepcopy(P)
    s["id"] = nid()
    _, tt = clone_mat(TIX, P["material_id"])
    txt = copy.deepcopy(TIX[tt["text_info_resources"][0]["text_material_id"]][1])
    txt["id"] = nid()
    add("texts", txt)
    set_text(txt, text)
    txt["recognize_text"] = text.lower()
    words = text.lower().split()
    tot = sum(len(w) for w in words) or 1
    st, en, tx, cur = [], [], [], 0.0
    for i, w in enumerate(words):
        wd = dur / 1000 * len(w) / tot
        st.append(int(cur)); en.append(int(cur + wd)); tx.append(w); cur += wd
        if i < len(words) - 1:
            st.append(int(cur)); en.append(int(cur)); tx.append(" ")
    txt["words"] = {"start_time": st, "end_time": en, "text": tx}
    refs = anim_clone(TIX, P, dur)
    s["extra_material_refs"] = refs
    tir = tt["text_info_resources"][0]
    tir["id"], tir["text_material_id"] = nid(), txt["id"]
    tir["attach_info"]["duration"], tir["attach_info"]["start_time"] = dur, 0
    kinds = {TIX[r][0]: i for i, r in enumerate(P["extra_material_refs"]) if r in TIX}
    tir["extra_material_refs"] = [refs[kinds[k]] for k in ("effects", "material_animations") if k in kinds]
    s["material_id"] = tt["id"]
    s["target_timerange"] = {"start": start, "duration": dur}
    s["clip"]["scale"] = {"x": scale, "y": scale}
    if y is not None:
        s["clip"]["transform"]["y"] = y
    return s


def combo_clone(proto, start, dur, name, edit):
    """copia la clip composta «proto» con id nuovi; edit(draft, testi) cambia i testi dentro."""
    _, dm = TIX[[r for r in proto["extra_material_refs"] if TIX.get(r, ("",))[0] == "drafts"][0]]
    old = dm["draft"]["id"]
    raw = json.dumps(dm["draft"], ensure_ascii=False)
    mp = {}
    nd = json.loads(UUIDRE.sub(lambda m_: mp.setdefault(m_.group(0), nid()), raw))
    nd["duration"], nd["name"] = dur, name
    for tr in nd["tracks"]:
        for sg in tr["segments"]:
            sg["target_timerange"] = {"start": 0, "duration": dur}
    edit(nd, {m["id"]: m for m in nd["materials"].get("texts", [])})
    s = copy.deepcopy(proto)
    s["id"], refs = nid(), []
    for r in proto["extra_material_refs"]:
        if r not in TIX:
            continue
        kind, m = clone_mat(TIX, r)
        if kind == "drafts":
            m["draft"], m["combination_id"] = nd, nid()
            for f in ("draft_file_path", "draft_cover_path", "draft_config_path"):
                if isinstance(m.get(f), str):
                    m[f] = m[f].replace(old, nd["id"])
        if kind == "material_animations":
            for a in m["animations"]:
                a["duration"] = dur if a["type"] == "group" else min(a["duration"], dur)
        if kind == "transitions":
            M["transitions"].remove(m)
            continue
        refs.append(m["id"])
    s["extra_material_refs"] = refs
    _, vm = clone_mat(TIX, proto["material_id"])
    vm["duration"], vm["material_name"] = dur, name
    s["material_id"] = vm["id"]
    s["source_timerange"] = {"start": 0, "duration": dur}
    s["target_timerange"] = {"start": start, "duration": dur}
    return s


combo_segs, blocks, report, texts_free = [], [], [], []
moments = []      # (inizio, ruolo) per i suoni


def titolo(righe, bianca, start, end):
    """righe nel colore del cliente (con \\n), riga bianca sotto; le emoji dietro seguono la lunghezza delle righe."""
    dur = end - start
    lines = righe.split("\n")

    def edit(nd, tx):
        segs_in = [(tr, sg) for tr in nd["tracks"] if tr["type"] == "text" for sg in tr["segments"]]
        words = [(sg, tx[sg["material_id"]]) for _, sg in segs_in if not is_emoji(json.loads(tx[sg["material_id"]]["content"])["text"])]
        emos = [(sg, tx[sg["material_id"]]) for _, sg in segs_in if is_emoji(json.loads(tx[sg["material_id"]]["content"])["text"])]
        colour = max(words, key=lambda w: size_of(w[1]) * w[0]["clip"]["scale"]["x"])
        white = [w for w in words if w is not colour]
        sg, m = colour
        old_lines = json.loads(m["content"])["text"].split("\n")
        size, font = size_of(m), m.get("font_path", "")
        em_px = PX_PER_SIZE * size * sg["clip"]["scale"]["x"]
        widest = max(width_ratio(x, font) for x in lines) * em_px
        k = min(1.0, SAFE_W / widest) if widest else 1.0
        if k < 1.0:
            sg["clip"]["scale"] = {"x": round(sg["clip"]["scale"]["x"] * k, 4), "y": round(sg["clip"]["scale"]["y"] * k, 4)}
            em_px *= k
        set_text(m, righe)
        for sgw, mw in white[:1]:
            set_text(mw, bianca)
            ww = width_ratio(bianca, mw.get("font_path", "")) * PX_PER_SIZE * size_of(mw) * sgw["clip"]["scale"]["x"]
            if ww > SAFE_W:
                f = SAFE_W / ww
                sgw["clip"]["scale"] = {"x": round(sgw["clip"]["scale"]["x"] * f, 4), "y": round(sgw["clip"]["scale"]["y"] * f, 4)}
        # emoji: quella a sinistra resta accanto alla prima riga, quella a destra dopo l'ultima
        old_w = [width_ratio(x, font) * PX_PER_SIZE * size * (sg["clip"]["scale"]["x"] / k if k else 1) for x in old_lines]
        new_w = [width_ratio(x, font) * em_px for x in lines]
        for es, em in emos:
            x = es["clip"]["transform"]["x"] * 540
            if x < 0:
                gap = -x - old_w[0] / 2
                es["clip"]["transform"]["x"] = round(-(new_w[0] / 2 + gap) / 540, 4)
            else:
                gap = x - old_w[-1] / 2
                es["clip"]["transform"]["x"] = round(min(0.9, (new_w[-1] / 2 + gap) / 540), 4)
        report.append(f"| Titolo | {righe!r} + {bianca!r} | scala {round(k, 3)} |")

    combo_segs.append(combo_clone(CAT["titolo"], start, dur, "Titolo", edit))
    blocks.append((start, end, "Titolo"))
    moments.append((start, "apertura"))


def parola(text, start, end, y=None):
    """momento forte: il testo grande del catalogo, ridotto se non entra"""
    P = CAT["parola"]
    s = copy.deepcopy(P)
    s["id"] = nid()
    _, m = clone_mat(TIX, P["material_id"])
    set_text(m, text)
    s["material_id"] = m["id"]
    s["extra_material_refs"] = anim_clone(TIX, P, end - start)
    s["target_timerange"] = {"start": start, "duration": end - start}
    sc = P["clip"]["scale"]["x"]
    w = max(width_ratio(x, m.get("font_path", "")) for x in text.split("\n")) * PX_PER_SIZE * size_of(m) * sc
    if w > SAFE_W:
        sc = round(sc * SAFE_W / w, 4)
    s["clip"]["scale"] = {"x": sc, "y": sc}
    if y is not None:
        s["clip"]["transform"]["y"] = y
    texts_free.append(s)
    blocks.append((start, end, "Parola " + text))
    moments.append((start, "parola"))
    report.append(f"| Parola forte | {text} | scala {sc} |")


dati_giro = Giro(CAT["dati"], key=lambda s: tuple(anims(TIX, s)))


def dato(text, start, end):
    """numero o dato nella clip composta del catalogo (con la sua animazione di gruppo)"""
    proto = dati_giro.next()
    if proto is None:
        return parola(text, start, end)

    def edit(nd, tx):
        for tr in nd["tracks"]:
            for sg in tr["segments"]:
                m = tx.get(sg["material_id"])
                if m and not is_emoji(json.loads(m["content"])["text"]):
                    set_text(m, text)
                    w = width_ratio(text, m.get("font_path", "")) * PX_PER_SIZE * size_of(m) * sg["clip"]["scale"]["x"]
                    if w > SAFE_W:
                        f = SAFE_W / w
                        sg["clip"]["scale"] = {"x": round(sg["clip"]["scale"]["x"] * f, 4), "y": round(sg["clip"]["scale"]["y"] * f, 4)}
    combo_segs.append(combo_clone(proto, start, end - start, "Dato " + text, edit))
    blocks.append((start, end, "Dato " + text))
    moments.append((start, "parola"))
    report.append(f"| Dato | {text} | {', '.join(anims(TIX, proto))} |")


emoji_giro = Giro(CAT["emoji"], key=lambda s: tuple(anims(TIX, s)))


def emoji(ch, start, secondi=1.4):
    """emoji in una clip composta del catalogo; l'animazione cambia ogni volta"""
    proto = emoji_giro.next()
    if proto is None:
        return
    end = min(start + us(secondi), TOTAL)
    for a, b, _ in blocks:
        if a <= start < b:
            return
        if start < a < end:
            end = a
    if end - start < 500_000:
        return

    def edit(nd, tx):
        for m in tx.values():
            if is_emoji(json.loads(m["content"])["text"]):
                set_text(m, ch)
    combo_segs.append(combo_clone(proto, start, end - start, "Emoji " + ch, edit))
    moments.append((start, "emoji"))
    report.append(f"| Emoji | {ch} | {', '.join(anims(TIX, proto))} |")


SUB_CH = int(CFG.get("sub_ch", 22))
subs, lists = [], []
LIST_Y = [-0.12, -0.205, -0.29, -0.375]


def elenco(items, end, label):
    for i, (txt, st) in enumerate(items):
        n = len(txt.strip())
        sc = 1.0 if n <= 0.9 * SUB_CH else round(0.9 * SUB_CH / n, 4)
        lists.append(caption(txt, st, end - st, scale=sc, y=LIST_Y[i]))
        moments.append((st, "voce"))
        report.append(f"| {label} voce {i + 1} | {txt} | scala {sc} |")
    blocks.append((items[0][1], end, label))


def cta(start):
    moments.append((start, "cta"))


env = dict(tl=tl, seg_start=seg_start, seg_end=seg_end, TOTAL=TOTAL, titolo=titolo, parola=parola, dato=dato,
           emoji=emoji, elenco=elenco, cta=cta)
exec(CFG["regia"], env)

# ---------------- sottotitoli dalle parole ----------------


def norm_tokens(words):
    toks = []
    for w, a, b in words:
        t = w.strip().upper().strip(".,;:!?…\"«»")
        if not t:
            continue
        if t.startswith("'") and toks:
            toks[-1][0] += t
            continue
        toks.append([t, a, b, w.strip()[-1:] in ".,;:!?"])
    fixes = [(k.upper().split(), v.upper()) for k, v in CFG.get("fix", {}).items()]
    out, i = [], 0
    while i < len(toks):
        for k, v in fixes:
            if [x[0] for x in toks[i:i + len(k)]] == k:
                parts = v.split()
                a0, b0 = toks[i][1], toks[i + len(k) - 1][2]
                for q, part in enumerate(parts):
                    out.append([part, a0 + (b0 - a0) * q / len(parts), a0 + (b0 - a0) * (q + 1) / len(parts),
                                toks[i + len(k) - 1][3] if q == len(parts) - 1 else False])
                i += len(k)
                break
        else:
            out.append(toks[i])
            i += 1
    return [t for t in out if t[0]]


def overlap(a, b, c, d):
    return max(0, min(b, d) - max(a, c))


if CFG.get("auto_subs"):
    W = CFG["words"]
    for n, cut in enumerate(CUTS, 1):
        key = [k for k in W if k.startswith(f"{cut[0]}:{cut[1]:.2f}:")]
        assert key, cut
        gaps_c = cut[4] if len(cut) > 4 else []
        ws = [w for w in W[key[0]] if w[1] < cut[2] and not any(g0 - 0.05 <= w[1] < g1 - 0.05 for g0, g1 in gaps_c)]
        toks = norm_tokens(ws)
        N = len(toks)
        Lc = lambda i, j: len(" ".join(x[0] for x in toks[i:j]))  # noqa: E731
        best = {N: (0, [])}
        for i in range(N - 1, -1, -1):
            cand = []
            for j in range(i + 1, N + 1):
                if Lc(i, j) > SUB_CH:
                    break
                cost = (Lc(i, j) - 16) ** 2 + (0 if j == N or toks[j - 1][3] else 40) + (300 if j - i == 1 and Lc(i, j) < 6 else 0)
                cand.append((cost + best[j][0], [(i, j)] + best[j][1]))
            best[i] = min(cand) if cand else (10 ** 9, [(i, i + 1)] + best[i + 1][1])
        chunks = [toks[i:j] for i, j in best[0][1]] if N else []
        for i, ch in enumerate(chunks):
            st = seg_start(n) if i == 0 else tl(n, ch[0][1])
            en = seg_end(n) if i == len(chunks) - 1 else tl(n, chunks[i + 1][0][1])
            if en - st < 200_000 or sum(overlap(st, en, a, b) for a, b, _ in blocks) > 0.5 * (en - st):
                continue
            for a, b, _ in sorted(blocks):
                if overlap(st, en, a, b):
                    if a <= st:
                        st = b
                    else:
                        en = a
            if en - st >= 300_000:
                subs.append(caption(" ".join(x[0] for x in ch), st, en - st))

# ---------------- suoni: ognuno col suo volume, mai lo stesso di fila ----------------
ROLE_WORDS = {"apertura": ("going up", "riser", "rise", "woosh"), "transizione": ("woosh", "swish", "whoosh"),
              "parola": ("bell", "impact", "ding"), "voce": ("ding", "click", "pop"),
              "emoji": ("glitter", "kirarin", "chime", "pop", "ding"), "cta": ("correct", "answer", "success")}
RISER = ("going up", "riser", "rise")
WOOSH_PRIMA = us(float(CFG.get("woosh_prima", 0.2)))   # il woosh parte prima della transizione (Sara: 0,8 s)
for n in TRANS_AFTER:
    moments.append((max(0, seg_end(n) - WOOSH_PRIMA), "transizione"))
moments.sort()
riser = [s for s in CAT["suoni"] if any(x in TIX[s["material_id"]][1].get("name", "").lower() for x in RISER)]
sound_giro = Giro([s for s in CAT["suoni"] if s not in riser] or CAT["suoni"], key=lambda s: TIX[s["material_id"]][1].get("name"))
audio_mats, sound_segs, sound_report, last = {}, [], [], -10 ** 9


def audio_seg(proto, start, dur):
    s = copy.deepcopy(proto)
    s["id"] = nid()
    s["extra_material_refs"] = [clone_mat(TIX, r)[1]["id"] for r in proto["extra_material_refs"] if r in TIX]
    s["target_timerange"] = {"start": start, "duration": dur}
    name = TIX[proto["material_id"]][1].get("name", "")
    head = next((us(v) for k, v in CFG.get("taglia_testa", {}).items() if name.startswith(k)), 0)
    s["source_timerange"] = {"start": proto["source_timerange"]["start"] + head, "duration": dur}
    for k, v in CFG.get("vol", {}).items():      # volumi del cliente (profilo), se no quelli del template
        if name.startswith(k):
            s["volume"] = s["last_nonzero_volume"] = float(v)
    mid = proto["material_id"]
    if mid not in audio_mats:
        audio_mats[mid] = add("audios", copy.deepcopy(TIX[mid][1]))
    s["material_id"] = audio_mats[mid]["id"]
    return s


for st, role in moments:
    if st - last < 900_000 and role not in ("apertura", "transizione"):   # ogni transizione ha il suo woosh
        continue
    words = ROLE_WORDS.get(role, ())
    if role == "apertura" and riser:          # il suono che sale solo sull'apertura
        p = riser[0]
    elif role == "transizione" and any(x in TIX[s["material_id"]][1].get("name", "").lower() for s in CAT["suoni"] for x in words):
        p = next(s for s in CAT["suoni"] if any(x in TIX[s["material_id"]][1].get("name", "").lower() for x in words))
        sound_giro.last = TIX[p["material_id"]][1].get("name")
    else:
        p = sound_giro.next(prefer=lambda s, w=words: next(
            (i for i, x in enumerate(w) if x in TIX[s["material_id"]][1].get("name", "").lower()), None))
    if p is None:
        break
    head = next((us(v) for k, v in CFG.get("taglia_testa", {}).items()
                 if TIX[p["material_id"]][1].get("name", "").startswith(k)), 0)
    dur = min(p["target_timerange"]["duration"] - head, TOTAL - st, 4_000_000 if role != "apertura" else 10 ** 12)
    if dur < 200_000:
        continue
    sound_segs.append(audio_seg(p, st, dur))
    sound_report.append((round(st / 1e6, 2), role, TIX[p["material_id"]][1].get("name")))
    last = st

# ---------------- musica: una sola, dallo stile del cliente ----------------
mus = CAT["musiche"]
pick = next((s for s in mus if TIX[s["material_id"]][1].get("name", "").lower().startswith(CFG.get("musica", "").lower())), None) \
    if CFG.get("musica") else (mus[0] if mus else None)
music = None
if pick is not None:
    full = int(TIX[pick["material_id"]][1].get("duration") or TOTAL)
    music = audio_seg(pick, 0, min(TOTAL, full))
    # volume: «music_db» del config (il profilo del cliente lo dice, es. −28 per Sara), se no quello del
    # template se è stato abbassato in CapCut, se no «music_vol» (0,15 ≈ −16,5 dB)
    if "music_db" in CFG:
        vol = round(10 ** (float(CFG["music_db"]) / 20), 4)
    elif float(pick.get("volume", 1.0)) < 0.999:
        vol = float(pick["volume"])
    else:
        vol = float(CFG.get("music_vol", 0.15))
    music["volume"] = music["last_nonzero_volume"] = vol

# ---------------- filtri ----------------
looks = []
for p in CAT["filtri"]:
    s = copy.deepcopy(p)
    s["id"] = nid()
    s["material_id"] = clone_mat(TIX, p["material_id"])[1]["id"]
    s["extra_material_refs"] = [clone_mat(TIX, r)[1]["id"] for r in p.get("extra_material_refs") or [] if r in TIX]
    s["target_timerange"] = {"start": 0, "duration": TOTAL}
    looks.append(s)

# ---------------- tracce ----------------


def track(kind, segments, flag=0, attribute=0):
    return {"id": nid(), "type": kind, "flag": flag, "attribute": attribute, "name": "", "is_default_name": True,
            "segments": sorted(segments, key=lambda s: s["target_timerange"]["start"])}


def pack(segments):
    lanes = []
    for s in sorted(segments, key=lambda s: s["target_timerange"]["start"]):
        a = s["target_timerange"]["start"]
        for lane in lanes:
            last_tr = lane[-1]["target_timerange"]
            if last_tr["start"] + last_tr["duration"] <= a:
                lane.append(s)
                break
        else:
            lanes.append([s])
    return lanes


tracks = [track("video", vsegs)]
tracks += [track(TIX[p["material_id"]][0] == "effects" and "filter" or "adjust", [s]) for p, s in zip(CAT["filtri"], looks)]
for lane in pack(combo_segs):
    tracks.append(track("video", lane, flag=2))
tracks.append(track("text", subs))
for lane in pack(lists):
    tracks.append(track("text", lane))
if texts_free:
    for lane in pack(texts_free):
        tracks.append(track("text", lane, flag=1))
if music:
    tracks.append(track("audio", [music]))
for lane in pack(sound_segs):
    tracks.append(track("audio", lane))
ri = 15000
for i, tr in enumerate(tracks):
    for s in tr["segments"]:
        s["track_render_index"] = i
        if tr["type"] == "text":
            ri += 1
            s["render_index"] = ri
        elif tr["type"] in ("filter", "adjust"):
            s["render_index"] = 10000
        elif tr["type"] == "video" and tr["flag"] == 2:
            s["render_index"] = 2
D["tracks"], D["duration"], D["id"], D["name"] = tracks, TOTAL, nid(), ""

meta = copy.deepcopy(TM)
did, now = nid(), int(time.time() * 1e6)
meta.update(draft_name=NAME, draft_fold_path=f"{ROOT}/{NAME}", draft_root_path=ROOT, draft_id=did,
            tm_duration=TOTAL, tm_draft_create=now, tm_draft_modified=now, draft_timeline_materials_size_=0)
proto_entry = [g for g in LM["draft_materials"] if g["type"] == 0][0]["value"][0]
entries = []
for c, m in vmat.items():
    e = copy.deepcopy(proto_entry)
    e.update(id=str(uuid.uuid4()), file_Path=m["path"], extra_info=f"{c}{EXT}", duration=m["duration"],
             width=1080, height=1920, metetype="video", create_time=int(time.time()),
             import_time=int(time.time()), import_time_ms=now, roughcut_time_range={"duration": m["duration"], "start": 0})
    entries.append(e)
for g in meta.get("draft_materials") or []:
    g["value"] = entries if g["type"] == 0 else []
os.makedirs(OUT, exist_ok=True)
json.dump(D, open(f"{OUT}/draft_info.json", "w"), ensure_ascii=False)
json.dump(meta, open(f"{OUT}/draft_meta_info.json", "w"), ensure_ascii=False)

f2 = lambda x: f"{x / 1e6:.2f}"  # noqa: E731
plan = {"total": TOTAL, "segs": [(n, c, sa / 1e6, sb / 1e6, ts / 1e6, d / 1e6, CUTS[n - 1][3], n in TRANS_AFTER)
                                 for n, c, sa, sb, ts, d in segs],
        "report": report, "blocks": [(f2(a), f2(b), lab) for a, b, lab in blocks],
        "subs": [(f2(s["target_timerange"]["start"]), f2(s["target_timerange"]["duration"]),
                  json.loads(next(m for m in M["texts"] if m["id"] == next(x for x in M["text_templates"] if x["id"] == s["material_id"])["text_info_resources"][0]["text_material_id"])["content"])["text"])
                 for s in subs],
        "transizioni": trans_used, "suoni": sound_report,
        "musica": TIX[pick["material_id"]][1].get("name") if pick is not None else None,
        "filtri": [TIX[p["material_id"]][1].get("name") for p in CAT["filtri"]], "draft_id": did}
json.dump(plan, open(f"{OUT}/plan.json", "w"), ensure_ascii=False, indent=1)
print("durata", TOTAL / 1e6, "tracce", len(tracks), "| musica", plan["musica"], "| transizioni", [x[1] for x in trans_used])
