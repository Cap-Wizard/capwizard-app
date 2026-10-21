"""Costruisce un progetto CapCut (draft) dal template del cliente.

uso: python3 costruisci_draft.py CARTELLA_OUT "NOME PROGETTO" config.json

Scrive in CARTELLA_OUT draft_info.json, draft_meta_info.json e plan.json (riepilogo).
CARTELLA_OUT di solito e' una cartella di lavoro: poi la copi in com.lveditor.draft/<NOME>/
(solo i due draft_*.json). Vedi SKILL.md, sezione "Il file di configurazione".
"""
import copy, json, uuid, time, sys, re, os, subprocess

OUT, NAME, CFG = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
ROOT = os.path.expanduser(CFG.get("projects_root", "~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
CLIPDIR = CFG["clipdir"]
EXT = CFG.get("ext", ".MP4")
KIT = {"big_text": "PIANO", "small_text": "QUINTO", "music": "The Driving", "impact": "Title impact",
       "woosh": "swish", "pop": "pop!", "glitter": "Glitter", "transition": "Bagliore II",
       "big_scale": 2.0, "small_scale": 1.5513, "small_y": -0.375, "big_y": None,
       "big_max": 9, "small_max": 17, "sub_ch": 23}
KIT.update(CFG.get("kit", {}))

TPL = os.path.join(ROOT, CFG["template"])
MOD = os.path.join(ROOT, CFG["modello"])     # progetto vero con parlato e transizione (struttura dei segmenti)
def jl(d, f):
    for name in (f, f + ".bak", "template-2.tmp") if f == "draft_info.json" else (f,):
        if os.path.exists(os.path.join(d, name)):
            return json.load(open(os.path.join(d, name)))
    raise FileNotFoundError(d)
T, TM = jl(TPL, "draft_info.json"), jl(TPL, "draft_meta_info.json")
L, LM = jl(MOD, "draft_info.json"), jl(MOD, "draft_meta_info.json")

def durata_reale(path):
    """durata in microsecondi = fotogrammi / fps (CapCut non trova i file con durata diversa)"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets",
                        "-show_entries", "stream=nb_read_packets,r_frame_rate", "-of", "csv=p=0", path],
                       capture_output=True, text=True).stdout.strip().split(",")
    num, den = r[0].split("/")
    return int(int(r[1]) * 1e6 * int(den) / int(num))

def nid():
    return str(uuid.uuid4()).upper()

def index(d):
    return {m["id"]: (k, m) for k, v in d["materials"].items() if isinstance(v, list)
            for m in v if isinstance(m, dict) and "id" in m}

TIX, LIX = index(T), index(L)
D = copy.deepcopy(T)
M = D["materials"]
for k in list(M):
    if isinstance(M[k], list):
        M[k] = []

def add(kind, m):
    M.setdefault(kind, []).append(m); return m

def clone_mat(ix, mid):
    kind, m = ix[mid]
    c = copy.deepcopy(m); c["id"] = nid(); add(kind, c)
    return kind, c

def us(s):
    return int(round(round(s * 50) / 50 * 1e6))

def utf16len(s):
    return len(s.encode("utf-16-le")) // 2

def txt_of(ix, seg):
    k, m = ix[seg["material_id"]]
    if k == "text_templates":
        m = ix[m["text_info_resources"][0]["text_material_id"]][1]
    return json.loads(m["content"])["text"] if k in ("texts", "text_templates") else None

# ---------------- prototipi dal template ----------------
segs_all = [(t, s) for t in T["tracks"] for s in t["segments"]]
P_BIG = [s for t, s in segs_all if t["type"] == "text" and txt_of(TIX, s) == KIT["big_text"]][0]
P_SUB = [s for t, s in segs_all if t["type"] == "text" and TIX[s["material_id"]][0] == "text_templates"][0]
P_BOKEH = [s for t, s in segs_all if t["type"] == "filter"][0]
P_COMBO = [s for t, s in segs_all if t["type"] == "video" and t["flag"] == 2][0]
def aud(name):
    return [s for t, s in segs_all if t["type"] == "audio" and TIX[s["material_id"]][1]["name"].startswith(name)][0]
P_MUSIC, P_IMPACT, P_WOOSH, P_POP, P_GLITTER = (aud(KIT[k]) for k in ("music", "impact", "woosh", "pop", "glitter"))

lv = [t for t in L["tracks"] if t["type"] == "video" and t["flag"] == 0][0]["segments"]
P_VSEG = [s for s in lv if any(LIX.get(r, ("",))[0] == "transitions" for r in s["extra_material_refs"])][0]
P_VMAT = LIX[P_VSEG["material_id"]][1]
P_TRANS = [LIX[r][1] for r in P_VSEG["extra_material_refs"] if LIX[r][0] == "transitions"][0]
assert P_TRANS["name"] == KIT["transition"], P_TRANS["name"]

# ---------------- montaggio ----------------
CUTS = CFG["cuts"]          # [clip, in, out, testo]
TRANS_AFTER = set(CFG["trans_after"])
CLOSE = set(CFG.get("close", []))  # inquadrature strette solo se richieste
CLOSE_SCALE, CLOSE_Y = 1.4, -0.38
ROT = CFG.get("rot", {})
CLIPDUR = CFG.get("clipdur", {})
for c in {x[0] for x in CFG["cuts"]}:
    if c not in CLIPDUR:
        CLIPDUR[c] = durata_reale(f"{CLIPDIR}/{c}{EXT}") / 1e6

segs, subs_v, t = [], [], 0   # segs: spezzone logico; subs_v: pezzi reali (senza le pause tolte)
for n, cut in enumerate(CUTS, 1):
    c, a, b = cut[0], cut[1], cut[2]
    gaps = cut[4] if len(cut) > 4 else []
    sa, sb = us(a), us(b)
    assert sb <= int(CLIPDUR[c] * 1e6), c
    pieces, cur = [], sa
    for g0, g1 in gaps:
        pieces.append((cur, us(g0))); cur = us(g1)
    pieces.append((cur, sb))
    t0 = t
    for k, (p0, p1) in enumerate(pieces):
        subs_v.append((n, c, p0, p1, t, p1 - p0, k == len(pieces) - 1)); t += p1 - p0
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
def seg_end(n): return segs[n - 1][4] + segs[n - 1][5]
def seg_start(n): return segs[n - 1][4]

vmat = {}
for c in sorted({x[1] for x in segs}):
    m = copy.deepcopy(P_VMAT)
    m.update(id=nid(), path=f"{CLIPDIR}/{c}{EXT}", media_path="", unique_id="", local_material_id="",
             material_name=f"{c}{EXT}", duration=int(CLIPDUR[c] * 1e6), width=1080, height=1920, has_audio=True)
    if c in CFG.get("landscape", []):
        m["width"], m["height"] = 1920, 1080
    m["stable"] = {"stable_level": 0, "matrix_path": "", "time_range": {"start": 0, "duration": 0}}
    add("videos", m); vmat[c] = m

def kf(prop, pts):
    return {"id": nid(), "material_id": "", "property_type": prop, "keyframe_list": [
        {"id": nid(), "curveType": "Line", "time_offset": int(o), "left_control": {"x": 0.0, "y": 0.0},
         "right_control": {"x": 0.0, "y": 0.0}, "values": [v], "string_value": "", "graphID": ""} for o, v in pts]}

vsegs = []
for n, c, sa, sb, ts, d, is_last in subs_v:
    s = copy.deepcopy(P_VSEG); s["id"] = nid()
    refs = []
    for r in P_VSEG["extra_material_refs"]:
        if LIX[r][0] == "transitions":
            if n in TRANS_AFTER and is_last:
                tr = copy.deepcopy(P_TRANS); tr["id"] = nid(); add("transitions", tr); refs.append(tr["id"])
            continue
        refs.append(clone_mat(LIX, r)[1]["id"])
    s["extra_material_refs"] = refs
    s["material_id"] = vmat[c]["id"]
    s["source_timerange"] = {"start": sa, "duration": d}
    s["target_timerange"] = {"start": ts, "duration": d}
    s["volume"] = 1.0; s["last_nonzero_volume"] = 1.0
    s["track_attribute"] = 0; s["render_index"] = 0
    base = CLOSE_SCALE if n in CLOSE else float(CFG.get("scale_override", {}).get(str(n), 1.0))
    if c in CFG.get("landscape", []):
        base = round(base * 1920 / 1080, 4)   # clip orizzontale ruotata: riempie il 9:16
    y = CLOSE_Y if n in CLOSE else 0.0
    s["clip"]["transform"] = {"x": 0.0, "y": y}
    s["clip"]["rotation"] = float(ROT.get(c, 0))
    s["common_keyframes"] = []; final = base
    if n in TRANS_AFTER and is_last:
        final = round(base * 1.15, 4)
        s["common_keyframes"] = [kf("KFTypeScaleX", [(max(sa, sb - 1_000_000), base), (sb - 20000, final)])]
    s["clip"]["scale"] = {"x": final, "y": final}
    s["uniform_scale"] = {"on": True, "value": 1.0}
    vsegs.append(s)

# ---------------- coperture b-roll ----------------
cover_segs, cover_report = [], []
def copertura(fname, start, end, bin_s=0.0):
    path = f"{CFG['brolldir']}/{fname}"
    dur_us = durata_reale(path)
    m = copy.deepcopy(P_VMAT)
    m.update(id=nid(), path=path, media_path="", unique_id="", local_material_id="",
             material_name=fname, duration=dur_us, width=1080, height=1920, has_audio=False)
    m["stable"] = {"stable_level": 0, "matrix_path": "", "time_range": {"start": 0, "duration": 0}}
    add("videos", m)
    s = copy.deepcopy(P_VSEG); s["id"] = nid()
    s["extra_material_refs"] = [clone_mat(LIX, r)[1]["id"] for r in P_VSEG["extra_material_refs"] if LIX[r][0] != "transitions"]
    s["material_id"] = m["id"]
    d = end - start
    assert us(bin_s) + d <= dur_us, fname
    s["source_timerange"] = {"start": us(bin_s), "duration": d}
    s["target_timerange"] = {"start": start, "duration": d}
    s["volume"] = 0.0; s["last_nonzero_volume"] = 1.0; s["track_attribute"] = 1; s["render_index"] = 1
    s["clip"]["scale"] = {"x": 1.0, "y": 1.0}; s["clip"]["transform"] = {"x": 0.0, "y": 0.0}; s["clip"]["rotation"] = 0.0
    s["common_keyframes"] = []
    cover_segs.append(s)
    cover_report.append((round(start/1e6, 2), round(end/1e6, 2), fname))

# ---------------- testi ----------------
def anim_clone(ix, proto_seg, dur):
    out = []
    for r in proto_seg["extra_material_refs"]:
        kind, m = clone_mat(ix, r)
        if kind == "material_animations":
            for a in m["animations"]:
                if a["type"] in ("caption", "group"):
                    a["duration"] = dur
        out.append(m["id"])
    return out

def set_text(m, text):
    c = json.loads(m["content"]); st = c["styles"][0]
    st["range"] = [0, utf16len(text)]; c["styles"] = [st]; c["text"] = text
    m["content"] = json.dumps(c, ensure_ascii=False)

def plain_text(proto, text, start, dur, scale, y, x=0.0):
    s = copy.deepcopy(proto); s["id"] = nid()
    _, m = clone_mat(TIX, proto["material_id"])
    set_text(m, text); m["recognize_text"] = text.strip()
    s["material_id"] = m["id"]
    s["extra_material_refs"] = anim_clone(TIX, proto, dur)
    s["target_timerange"] = {"start": start, "duration": dur}
    s["clip"]["scale"] = {"x": scale, "y": scale}
    s["clip"]["transform"] = {"x": x, "y": y}
    return s

def caption(text, start, dur, scale=1.0, y=None):
    s = copy.deepcopy(P_SUB); s["id"] = nid()
    _, tt = clone_mat(TIX, P_SUB["material_id"])
    txt = copy.deepcopy(TIX[tt["text_info_resources"][0]["text_material_id"]][1]); txt["id"] = nid(); add("texts", txt)
    set_text(txt, text); txt["recognize_text"] = text.lower()
    words = text.lower().split(); tot = sum(len(w) for w in words) or 1
    st, en, tx, cur = [], [], [], 0.0
    for i, w in enumerate(words):
        wd = dur / 1000 * len(w) / tot
        st.append(int(cur)); en.append(int(cur + wd)); tx.append(w); cur += wd
        if i < len(words) - 1:
            st.append(int(cur)); en.append(int(cur)); tx.append(" ")
    txt["words"] = {"start_time": st, "end_time": en, "text": tx}
    refs = anim_clone(TIX, P_SUB, dur)
    s["extra_material_refs"] = refs
    tir = tt["text_info_resources"][0]
    tir["id"] = nid(); tir["text_material_id"] = txt["id"]
    tir["attach_info"]["duration"] = dur; tir["attach_info"]["start_time"] = 0
    kinds = {TIX[r][0]: i for i, r in enumerate(P_SUB["extra_material_refs"])}
    tir["extra_material_refs"] = [refs[kinds["effects"]], refs[kinds["material_animations"]]]
    s["material_id"] = tt["id"]
    s["target_timerange"] = {"start": start, "duration": dur}
    s["clip"]["scale"] = {"x": scale, "y": scale}
    if y is not None:
        s["clip"]["transform"]["y"] = y
    return s

# titolo / titoletti = clip composta del template
BIG_SCALE, SMALL_SCALE, SMALL_Y = KIT["big_scale"], KIT["small_scale"], KIT["small_y"]
EMO_Y_TIT, EMO_Y_FREE, EMO_SCALE_FREE = -0.30, -0.24, 1.4
T_SMALL_CH, T_SMALL_SC, T_EMO_X, T_EMO_Y = 15, 1.7237, -0.6424, -0.2747
SMALL_Y_T, BIG_Y_T = -0.3383, -0.4292
UUIDRE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")

combo_segs, blocks, report = [], [], []
def titoletto(small, big, emo, start, end, label):
    dur = end - start
    assert len(big) <= KIT["big_max"] and len(small) <= KIT["small_max"], (small, big)
    small_s = SMALL_SCALE if len(small) <= 15 else round(SMALL_SCALE * 15 / len(small), 4)
    # bozza annidata con id tutti nuovi
    k, dm = TIX[[r for r in P_COMBO["extra_material_refs"] if TIX[r][0] == "drafts"][0]]
    old_nested = dm["draft"]["id"]
    raw = json.dumps(dm["draft"], ensure_ascii=False)
    mp = {}
    raw = UUIDRE.sub(lambda m_: mp.setdefault(m_.group(0), nid()), raw)
    nd = json.loads(raw)
    nd["duration"] = dur; nd["name"] = f"Titolo {big}"
    ntx = {m["id"]: m for m in nd["materials"]["texts"]}
    for tr in nd["tracks"]:
        for sg in tr["segments"]:
            sg["target_timerange"] = {"start": 0, "duration": dur}
            m = ntx[sg["material_id"]]
            cur = json.loads(m["content"])["text"]
            if cur == KIT["big_text"]:
                set_text(m, big); sg["clip"]["scale"] = {"x": BIG_SCALE, "y": BIG_SCALE}
                sg["clip"]["transform"] = {"x": 0.0, "y": KIT["big_y"] if KIT["big_y"] is not None else sg["clip"]["transform"]["y"]}
            elif cur.startswith(KIT["small_text"]):
                set_text(m, small + "\n"); sg["clip"]["scale"] = {"x": small_s, "y": small_s}
                sg["clip"]["transform"] = {"x": 0.0, "y": SMALL_Y}
            else:  # emoji
                set_text(m, emo)
                sg["clip"]["transform"] = {"x": 0.0, "y": EMO_Y_TIT}  # centrata, poco sopra il titoletto
            m["recognize_text"] = json.loads(m["content"])["text"].strip()
    newnested = nd["id"]
    # materiale video della clip composta
    s = copy.deepcopy(P_COMBO); s["id"] = nid(); refs = []
    for r in P_COMBO["extra_material_refs"]:
        kind, m = clone_mat(TIX, r)
        if kind == "drafts":
            m["draft"] = nd; m["combination_id"] = nid()
            for f in ("draft_file_path", "draft_cover_path", "draft_config_path"):
                m[f] = m[f].replace(old_nested, newnested)
        if kind == "material_animations":
            for a in m["animations"]:
                a["duration"] = dur
        refs.append(m["id"])
    s["extra_material_refs"] = refs
    _, vm = clone_mat(TIX, P_COMBO["material_id"])
    vm["duration"] = dur; vm["material_name"] = f"Titolo {big}"
    s["material_id"] = vm["id"]
    s["source_timerange"] = {"start": 0, "duration": dur}
    s["target_timerange"] = {"start": start, "duration": dur}
    combo_segs.append(s)
    blocks.append((start, end, label))
    report.append(f"| {label} | {emo} {small} / {big} | {len(small)} / {len(big)} car. | {small_s} / {BIG_SCALE} |")

SUB_CH = KIT["sub_ch"]
subs, lists, emojis = [], [], []
def sub(n, text, a, b):
    s, e = tl(n, a), tl(n, b)
    assert len(text) <= SUB_CH, text
    subs.append(caption(text, s, e - s))

LIST_Y = [-0.12, -0.205, -0.29, -0.375]
def fit(scale_t, ch_t, text):
    n = len(text.strip())
    return scale_t if n <= 0.9 * ch_t else round(scale_t * 0.9 * ch_t / n, 4)
def elenco(items, end, label):
    for i, (txt, st) in enumerate(items):
        sc = fit(1.0, SUB_CH, txt)
        lists.append(caption(txt, st, end - st, scale=sc, y=LIST_Y[i]))
        report.append(f"| {label} voce {i+1} | {txt} | {len(txt)} car. | {sc} |")
    blocks.append((items[0][1], end, label))

def emoji(ch, st, dur, side):
    e = min(st + us(dur), TOTAL)
    for a, b, _ in blocks:  # mai sopra un titoletto (che ha già la sua emoji)
        if a <= st < b:
            return
        if st < a < e:
            e = a
    if e - st >= 500_000:
        emojis.append(plain_text(P_BIG, ch, st, e - st, EMO_SCALE_FREE, EMO_Y_FREE, x=0.0))

# esegue la regia del config
env = dict(tl=tl, seg_start=seg_start, seg_end=seg_end, titoletto=titoletto, sub=sub, elenco=elenco, TOTAL=TOTAL, copertura=copertura)
exec(CFG["regia"], env)
# ---- sottotitoli automatici dalle parole trascritte ----
def norm_tokens(words):
    toks = []
    for w, a, b in words:
        t = w.strip().upper().strip(".,;:!?…\"«»")
        if not t:
            continue
        if t.startswith("'") and toks:
            toks[-1][0] += t; continue
        toks.append([t, a, b, w.strip()[-1:] in ".,;:!?"])
    fixes = [(k.upper().split(), v.upper()) for k, v in CFG.get("fix", {}).items()]
    out, i = [], 0
    while i < len(toks):
        for k, v in fixes:
            if [x[0] for x in toks[i:i+len(k)]] == k:
                parts = v.split()
                a0, b0 = toks[i][1], toks[i+len(k)-1][2]
                for q, part in enumerate(parts):
                    out.append([part, a0 + (b0 - a0) * q / len(parts), a0 + (b0 - a0) * (q + 1) / len(parts),
                                toks[i+len(k)-1][3] if q == len(parts) - 1 else False])
                i += len(k); break
        else:
            out.append(toks[i]); i += 1
    return [t for t in out if t[0]]
def overlap(a, b, c, d):
    return max(0, min(b, d) - max(a, c))
if CFG.get("auto_subs"):
    W = CFG["words"]
    for n, cut in enumerate(CUTS, 1):
        pref = f"{cut[0]}:{cut[1]:.2f}:"
        key = [k for k in W if k.startswith(pref)]
        assert key, pref
        gaps_c = cut[4] if len(cut) > 4 else []
        ws = []
        for w in W[key[0]]:
            if w[1] >= cut[2]:
                continue
            g = [(g0, g1) for g0, g1 in gaps_c if g0 - 0.05 <= w[1] < g1 - 0.05]
            if g:
                if CFG.get("gap_words") != "shift":
                    continue
                w = [w[0], g[0][1], max(w[2], g[0][1])]
            ws.append(w)
        toks = norm_tokens(ws)
        # divisione bilanciata: righe <= 23 caratteri, simili tra loro, a capo preferito dopo la punteggiatura
        N = len(toks)
        L = lambda i, j: len(" ".join(x[0] for x in toks[i:j]))
        best = {N: (0, [])}
        for i in range(N - 1, -1, -1):
            cand = []
            for j in range(i + 1, N + 1):
                if L(i, j) > SUB_CH: break
                cost = (L(i, j) - 16) ** 2 + (0 if j == N or toks[j-1][3] else 40) + (300 if j - i == 1 and L(i, j) < 6 else 0)
                cand.append((cost + best[j][0], [(i, j)] + best[j][1]))
            best[i] = min(cand) if cand else (10**9, [(i, i + 1)] + best[i + 1][1])
        chunks = [toks[i:j] for i, j in best[0][1]] if N else []
        for i, ch in enumerate(chunks):
            st = seg_start(n) if i == 0 else tl(n, ch[0][1])
            en = seg_end(n) if i == len(chunks) - 1 else tl(n, chunks[i+1][0][1])
            cov = sum(overlap(st, en, a, b) for a, b, _ in blocks)
            if en - st < 200_000 or cov > 0.5 * (en - st):
                continue
            for a, b, _ in sorted(blocks):
                if overlap(st, en, a, b):
                    if a <= st: st = b
                    else: en = a
            if en - st >= 300_000:
                txt = " ".join(x[0] for x in ch)
                assert len(txt) <= SUB_CH, txt
                subs.append(caption(txt, st, en - st))

for ch, n, src, side in CFG.get("emoji", []):
    emoji(ch, tl(n, src), 1.4, side)

emojis.sort(key=lambda x: x["target_timerange"]["start"])
for a_, b_ in zip(emojis, emojis[1:]):
    ta, tb = a_["target_timerange"], b_["target_timerange"]
    if ta["start"] + ta["duration"] > tb["start"] - 100_000:
        ta["duration"] = max(300_000, tb["start"] - 100_000 - ta["start"])
emojis = [e for e in emojis if e["target_timerange"]["duration"] >= 300_000]

# ---------------- suoni ----------------
audio_mats = {}
def amat(proto):
    mid = proto["material_id"]
    if mid not in audio_mats:
        m = copy.deepcopy(TIX[mid][1]); add("audios", m); audio_mats[mid] = m
    return audio_mats[mid]["id"]
def audio_seg(proto, start, dur=None):
    s = copy.deepcopy(proto); s["id"] = nid()
    s["extra_material_refs"] = [clone_mat(TIX, r)[1]["id"] for r in proto["extra_material_refs"]]
    d = dur if dur is not None else proto["target_timerange"]["duration"]
    s["target_timerange"] = {"start": start, "duration": d}
    s["source_timerange"] = {"start": proto["source_timerange"]["start"], "duration": d}
    s["material_id"] = amat(proto)
    return s

intro = [(P_IMPACT["target_timerange"]["start"], P_IMPACT), (P_WOOSH["target_timerange"]["start"], P_WOOSH),
         (P_GLITTER["target_timerange"]["start"], P_GLITTER), (P_POP["target_timerange"]["start"], P_POP)]
cand = []
for st, en, label in blocks[1:] if blocks and blocks[0][0] == 0 else blocks:
    if label.startswith("Titoletto"):
        cand.append((st, [P_IMPACT, P_WOOSH]))
    elif label.startswith("Elenco"):
        cand.append((st, [P_GLITTER]))
for s in lists:
    cand.append((s["target_timerange"]["start"], [P_POP]))
for n in TRANS_AFTER:
    cand.append((seg_end(n) - 200_000, [P_WOOSH]))
cand.sort(key=lambda x: x[0])
kept = list(intro); last = max(x[0] for x in intro)
for st, ps in cand:
    if st - last >= 1_000_000:
        for p in ps:
            kept.append((st, p))
        last = st
music = audio_seg(P_MUSIC, 0, TOTAL)
trans_w = {seg_end(n) - 200_000 for n in TRANS_AFTER}
sound_segs = []
for st, p in kept:
    a = audio_seg(p, st)
    over = st + a["target_timerange"]["duration"] - TOTAL
    if over > 0:
        a["target_timerange"]["duration"] -= over; a["source_timerange"]["duration"] -= over
    name = TIX[p["material_id"]][1]["name"]
    for k, v in CFG.get("vol", {}).items():
        if name.startswith(k):
            a["volume"] = v
    if name.startswith("swish") and st in trans_w and "woosh_trans_vol" in CFG:
        a["volume"] = CFG["woosh_trans_vol"]
    sound_segs.append(a)

bok = copy.deepcopy(P_BOKEH); bok["id"] = nid()
bok["material_id"] = clone_mat(TIX, P_BOKEH["material_id"])[1]["id"]
bok["target_timerange"] = {"start": 0, "duration": TOTAL}

# ---------------- tracce ----------------
def track(kind, segments, flag=0, attribute=0):
    return {"id": nid(), "type": kind, "flag": flag, "attribute": attribute, "name": "",
            "is_default_name": True, "segments": sorted(segments, key=lambda s: s["target_timerange"]["start"])}
def pack(segments):
    lanes = []
    for s in sorted(segments, key=lambda s: s["target_timerange"]["start"]):
        a = s["target_timerange"]["start"]
        for lane in lanes:
            l = lane[-1]["target_timerange"]
            if l["start"] + l["duration"] <= a:
                lane.append(s); break
        else:
            lanes.append([s])
    return lanes

tracks = [track("video", vsegs)]
if cover_segs:
    tracks.append(track("video", cover_segs, flag=2, attribute=1))
tracks += [track("filter", [bok]), track("video", combo_segs, flag=2), track("text", subs)]
for lane in pack(lists):
    tracks.append(track("text", lane))
if emojis:
    tracks.append(track("text", emojis))
tracks.append(track("audio", [music]))
for lane in pack(sound_segs):
    tracks.append(track("audio", lane))
ri = 15000
for i, tr in enumerate(tracks):
    for s in tr["segments"]:
        s["track_render_index"] = i
        if tr["type"] == "text":
            ri += 1; s["render_index"] = ri
        elif tr["type"] == "filter":
            s["render_index"] = 10000
        elif tr["type"] == "video" and tr["flag"] == 2:
            s["render_index"] = 1 if tr["attribute"] == 1 else 2
D["tracks"] = tracks; D["duration"] = TOTAL; D["id"] = nid(); D["name"] = ""

meta = copy.deepcopy(TM); did = nid(); now = int(time.time() * 1e6)
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
for g in meta["draft_materials"]:
    if g["type"] == 0:
        g["value"] = entries
json.dump(D, open(f"{OUT}/draft_info.json", "w"), ensure_ascii=False)
json.dump(meta, open(f"{OUT}/draft_meta_info.json", "w"), ensure_ascii=False)

TX = {m["id"]: m for m in M["texts"]}; TT = {m["id"]: m for m in M["text_templates"]}
def stxt(s):
    mid = s["material_id"]
    m = TX.get(mid) or TX[TT[mid]["text_info_resources"][0]["text_material_id"]]
    return json.loads(m["content"])["text"]
f = lambda x: f"{x/1e6:.2f}"
plan = {"total": TOTAL, "segs": [(n, c, sa/1e6, sb/1e6, ts/1e6, d/1e6, CUTS[n-1][3], n in CLOSE, n in TRANS_AFTER, ROT.get(c, 0))
                                for n, c, sa, sb, ts, d in segs],
        "report": report, "blocks": [(f(a), f(b), l) for a, b, l in blocks],
        "subs": [(f(s["target_timerange"]["start"]), f(s["target_timerange"]["duration"]), stxt(s)) for s in subs],
        "sounds": sorted([(f(st), TIX[p["material_id"]][1]["name"][:22]) for st, p in kept]),
        "emoji": [(f(s["target_timerange"]["start"]), stxt(s)) for s in emojis], "broll": cover_report, "draft_id": did}
json.dump(plan, open(f"{OUT}/plan.json", "w"), ensure_ascii=False, indent=1)
print("durata", TOTAL / 1e6, "tracce", len(tracks))
