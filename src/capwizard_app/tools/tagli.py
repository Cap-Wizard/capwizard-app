"""Ritocchi di tempo su un draft CapCut, come li farebbe un montatore: togliere un pezzo di timeline
(le parti morte) o allungare una clip (la coda di una parola), con tutto il resto che scorre dietro.

Lavora sul dict del draft (draft_info.json / template-2.tmp / Timelines/*). I tempi sono in secondi.
- remove(d, a, x): toglie [a, a+x] dalla timeline. Le clip lì dentro si accorciano, quello dopo scorre indietro,
  gli sfx corti non vengono mai tagliati in testa: si spostano sul nuovo taglio.
- extend(d, seg_id, x): allunga la clip di x alla fine (se la sorgente ha ancora girato), tutto dopo scorre avanti.
- split(d, seg_id, t): spezza una clip in due al tempo t (come la lama di CapCut), materiali propri per la seconda.
"""
import copy
import uuid

US = 1_000_000
EPS = 20_000        # 20 ms: stesso taglio
ON_CUT = 500_000    # uno sfx corto che parte entro 0,5 s dal taglio appartiene al taglio (whoosh prima, pick dopo)
LONG = 5 * US       # pezzi audio da 5 s in su = musica / voce, sotto = sfx


def _mats(d):
    out = {}
    for k, v in d.get("materials", {}).items():
        if isinstance(v, list):
            for m in v:
                if isinstance(m, dict) and "id" in m:
                    out[m["id"]] = (k, m)
    return out


def _speed(s):
    return s.get("speed") or 1.0


def _room_after(d, s):
    """quanto può ancora allungarsi la clip in timeline (sorgente rimasta), in µs"""
    k, m = _mats(d).get(s["material_id"], ("", {}))
    if k not in ("videos", "audios") or not m.get("duration"):
        return 0
    if m.get("type") == "photo":
        return 10 ** 12
    st = s["source_timerange"]
    return max(0, int((m["duration"] - st["start"] - st["duration"]) / _speed(s)))


def _set_dur(s, dur, text):
    s["target_timerange"]["duration"] = int(dur)
    if not text and s.get("source_timerange"):
        s["source_timerange"]["duration"] = int(dur * _speed(s))


def remove(d, a, x, cut=None):
    """cut = dov'era il taglio a cui appartengono gli sfx (di default la fine del pezzo tolto); dopo, quel
    taglio sta in a e gli sfx vicini restano alla stessa distanza da lui"""
    a, x = int(a * US), int(x * US)
    b = a + x
    cut = b if cut is None else int(cut * US)
    if x <= 0:
        return
    for tr in d.get("tracks", []):
        text = tr["type"] in ("text", "sticker", "effect", "filter", "adjust")
        keep = []
        for s in tr["segments"]:
            st = s["target_timerange"]
            s0, e = st["start"], st["start"] + st["duration"]
            short_sfx = tr["type"] == "audio" and st["duration"] < LONG
            if short_sfx and abs(s0 - cut) <= ON_CUT:
                st["start"] = max(0, a + (s0 - cut))                # lo sfx segue il suo taglio, intero
            elif short_sfx and a - EPS <= s0 < b:
                st["start"] = a
            elif e <= a + EPS:
                pass                                                # tutto prima
            elif s0 >= b - EPS:
                st["start"] = s0 - x                                # tutto dopo
            elif s0 < a and e > b:
                _set_dur(s, st["duration"] - x, text)               # passa sopra il buco
            elif s0 < a:
                _set_dur(s, a - s0, text)                           # finisce dentro il buco
            elif e > b:
                cut_head = b - s0                                   # comincia dentro il buco
                if not text and s.get("source_timerange"):
                    s["source_timerange"]["start"] += int(cut_head * _speed(s))
                st["start"] = a
                _set_dur(s, e - b, text)
            else:
                continue                                            # tutto dentro il buco: via
            keep.append(s)
        tr["segments"] = keep
    d["duration"] = max(0, d.get("duration", 0) - x)


def extend(d, seg_id, x):
    """allunga la clip seg_id di x secondi alla fine; ritorna di quanto l'ha allungata davvero"""
    tgt = next((s for tr in d.get("tracks", []) for s in tr["segments"] if s.get("id") == seg_id), None)
    if not tgt:
        return 0
    x = min(int(x * US), _room_after(d, tgt))
    if x <= 0:
        return 0
    c = tgt["target_timerange"]["start"] + tgt["target_timerange"]["duration"]
    for tr in d.get("tracks", []):
        text = tr["type"] in ("text", "sticker", "effect", "filter", "adjust")
        for s in tr["segments"]:
            st = s["target_timerange"]
            s0, e = st["start"], st["start"] + st["duration"]
            short_sfx = tr["type"] == "audio" and st["duration"] < LONG
            if s is tgt or (tr["type"] == "video" and abs(e - c) < EPS and s0 < c - EPS):
                _set_dur(s, st["duration"] + min(x, _room_after(d, s) if s is not tgt else x), False)
            elif (short_sfx and s0 >= c - ON_CUT) or s0 >= c - EPS:
                st["start"] = s0 + x
            elif s0 < c - EPS and e > c + EPS:
                grow = x if text else min(x, _room_after(d, s))
                _set_dur(s, st["duration"] + grow, text)
    d["duration"] = d.get("duration", 0) + x
    return x / US


def split(d, seg_id, t):
    """spezza la clip al tempo t (s); ritorna l'id della seconda metà"""
    t = int(t * US)
    mats = _mats(d)
    for tr in d.get("tracks", []):
        for i, s in enumerate(tr["segments"]):
            if s.get("id") != seg_id:
                continue
            st = s["target_timerange"]
            if not (st["start"] + EPS < t < st["start"] + st["duration"] - EPS):
                return None
            b = copy.deepcopy(s)
            ns = uuid.uuid5(uuid.NAMESPACE_URL, f"{seg_id}@{t}")      # stesso id in ogni copia del draft
            b["id"] = str(ns).upper()
            refs = []
            for r in s.get("extra_material_refs", []):
                k, m = mats.get(r, ("", None))
                if m is None:
                    refs.append(r)
                    continue
                nm = copy.deepcopy(m)
                nm["id"] = str(uuid.uuid5(ns, r)).upper()
                d["materials"][k].append(nm)
                refs.append(nm["id"])
            b["extra_material_refs"] = refs
            first = t - st["start"]
            b["target_timerange"] = {"start": t, "duration": st["duration"] - first}
            if s.get("source_timerange"):
                b["source_timerange"] = {"start": s["source_timerange"]["start"] + int(first * _speed(s)),
                                         "duration": int((st["duration"] - first) * _speed(s))}
            _set_dur(s, first, False)
            tr["segments"].insert(i + 1, b)
            return b["id"]
    return None
