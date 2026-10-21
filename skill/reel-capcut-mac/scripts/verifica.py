"""Verifica un progetto CapCut costruito con costruisci_draft.py, anche dopo che Nicola l'ha aperto.

uso: python3 verifica.py CARTELLA_PROGETTO [config.json] [--correggi]

Controlla: id, riferimenti, traccia principale contigua, tempi dentro i file, sovrapposizioni,
suoni dentro la durata, media_path/percorsi estranei, file esistenti, durata dei materiali uguale
a quella reale. Con il config controlla anche che CapCut non abbia riportato a 0 l'inizio degli
spezzoni (succede alla prima apertura) e con --correggi lo rimette giusto in TUTTE le copie
(draft_info.json, .bak, template-2.tmp e Timelines/<id>/). Il progetto deve essere chiuso.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sys

import audio


def copie(cart):
    pats = ["draft_info.json", "draft_info.json.bak", "template-2.tmp",
            "Timelines/*/draft_info.json", "Timelines/*/draft_info.json.bak", "Timelines/*/template-2.tmp"]
    return [f for p in pats for f in glob.glob(os.path.join(cart, p))]


def attesi(cfg):
    out = []
    for cut in cfg["cuts"]:
        c, a, b = cut[0], cut[1], cut[2]
        cur = a
        for g0, g1 in (cut[4] if len(cut) > 4 else []):
            out.append((c, cur)); cur = g1
        out.append((c, cur))
    return out


def main():
    cart = sys.argv[1]
    cfg = json.load(open(sys.argv[2])) if len(sys.argv) > 2 and sys.argv[2].endswith(".json") else None
    correggi = "--correggi" in sys.argv
    if os.path.exists(os.path.join(cart, ".locked")):
        sys.exit("PROGETTO APERTO in CapCut: chiedi a Nicola di tornare alla home dei progetti")
    D = json.load(open(os.path.join(cart, "draft_info.json")))
    M = D["materials"]
    err = 0
    ids = collections.Counter(m["id"] for v in M.values() if isinstance(v, list) for m in v if isinstance(m, dict) and "id" in m)
    if any(c > 1 for c in ids.values()):
        print("id duplicati"); err += 1
    for t in D["tracks"]:
        for s in t["segments"]:
            for r in [s["material_id"]] + s["extra_material_refs"]:
                if r not in ids:
                    print("riferimento mancante", t["type"], r); err += 1
    vm = {m["id"]: m for m in M["videos"]}
    v = [t for t in D["tracks"] if t["type"] == "video" and t["flag"] == 0][0]
    if v["attribute"] != 0:
        print("traccia principale silenziata"); err += 1
    pos = 0
    for s in v["segments"]:
        if s["target_timerange"]["start"] != pos:
            print("buco/sovrapposizione a", pos / 1e6); err += 1
        pos = s["target_timerange"]["start"] + s["target_timerange"]["duration"]
    for t in D["tracks"]:
        if t["type"] in ("text", "audio", "filter"):
            ss = sorted(t["segments"], key=lambda s: s["target_timerange"]["start"])
            for x, y in zip(ss, ss[1:]):
                if x["target_timerange"]["start"] + x["target_timerange"]["duration"] > y["target_timerange"]["start"]:
                    print("sovrapposti in una traccia", t["type"]); err += 1
            for x in ss:
                if x["target_timerange"]["start"] + x["target_timerange"]["duration"] > D["duration"] + 1000:
                    print("oltre la fine", t["type"]); err += 1
    for m in M["videos"]:
        if not m["path"]:
            continue
        if m.get("media_path"):
            print("media_path non vuoto", m["path"]); err += 1
        if not os.path.exists(m["path"]):
            print("FILE MANCANTE", m["path"]); err += 1
        elif "/BROLL/" in m["path"] or m["path"].lower().endswith("_ai.mp4"):
            reale = audio.probe(m["path"])["durata_us"]
            if abs(reale - m["duration"]) > 20000:
                print(f"durata materiale {m['duration']} diversa dal file {reale}: CapCut non lo trova", m["path"]); err += 1
    # inizi azzerati da CapCut
    if cfg:
        exp = attesi(cfg)
        for f in copie(cart):
            d = json.load(open(f))
            vmf = {m["id"]: m for m in d["materials"]["videos"]}
            tr = [t for t in d["tracks"] if t["type"] == "video" and t["flag"] == 0][0]
            fix = []
            for i, s in enumerate(tr["segments"]):
                c = os.path.splitext(os.path.basename(vmf[s["material_id"]]["path"]))[0]
                ec, ea = exp[i]
                assert ec == c, (i, c, ec)
                want = int(round(ea * 50)) * 20000
                st = s["source_timerange"]
                if abs(st["start"] - want) > 30000:
                    fix.append((i + 1, c, st["start"] / 1e6, ea))
                    if correggi:
                        for k in s.get("common_keyframes", []):
                            for x in k["keyframe_list"]:
                                x["time_offset"] += want - st["start"]
                        st["start"] = want
            if fix:
                print(("CORRETTI " if correggi else "INIZI SBAGLIATI ") + os.path.relpath(f, cart), fix)
                if correggi:
                    json.dump(d, open(f, "w"), ensure_ascii=False)
                else:
                    err += 1
    print(f"durata {D['duration'] / 1e6:.2f}s  spezzoni {len(v['segments'])}  ERRORI {err}")


if __name__ == "__main__":
    main()
