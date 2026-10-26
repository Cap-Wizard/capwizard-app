"""Porta la voce di tutti gli spezzoni parlati allo stesso livello, regolando il volume di ogni spezzone.

uso: python3 livella_voce.py CARTELLA_PROGETTO [--applica] [--lufs -21]

Misura la loudness integrata (LUFS, ebur128 di ffmpeg) del tratto di clip usato da ogni spezzone
video con audio acceso (salta b-roll, AI e drone), aggiunge il volume già messo e mostra di quanto
si scosta dall'obiettivo: di default la mediana del progetto, così il livello generale scelto da
Nicola resta quello. Con --applica scrive il nuovo volume in TUTTE le copie della timeline
(draft_info.json, .bak, template-2.tmp e Timelines/<id>/). Scarti sotto 1 dB restano come sono;
la correzione è limitata a ±9 dB. Il progetto deve essere chiuso.
Segnala anche gli spezzoni con voce molto variabile all'interno (più di 6 dB tra i tratti forti e
deboli della loudness su 3 secondi): lì un volume unico non basta, va diviso lo spezzone.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys

from verifica import copie

SALTA = ("/BROLL/", "/MAGNIFIC/", "DRONE", "_AI.mp4", "_render.mp4")
TOLLERANZA = 1.0
MASSIMO = 9.0


def loudness(path: str, a: float, durata: float) -> tuple[float, float] | None:
    """(loudness integrata, scarto interno in dB) del tratto a, a+durata della clip."""
    r = subprocess.run(["ffmpeg", "-nostats", "-ss", f"{a:.3f}", "-t", f"{durata:.3f}", "-i", path, "-vn",
                        "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.S", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    if not i or float(i[-1]) < -60:
        return None
    breve = sorted(float(x) for x in re.findall(r"lavfi.r128.S=(-?[\d.]+)", r))
    voce = [x for x in breve if x > float(i[-1]) - 12]
    scarto = voce[int(len(voce) * .9)] - voce[int(len(voce) * .1)] if len(voce) > 10 else 0.0
    return float(i[-1]), scarto


def parlati(d: dict):
    vm = {m["id"]: m for m in d["materials"]["videos"]}
    for t in d["tracks"]:
        if t["type"] != "video" or t.get("attribute", 0) & 1:
            continue
        for s in t["segments"]:
            m = vm.get(s["material_id"])
            if not m or not m.get("path") or s.get("volume", 1) <= 0 or any(x in m["path"] for x in SALTA):
                continue
            yield s, m


def main():
    cart = sys.argv[1]
    applica = "--applica" in sys.argv
    lufs = float(sys.argv[sys.argv.index("--lufs") + 1]) if "--lufs" in sys.argv else None
    if os.path.exists(os.path.join(cart, ".locked")):
        sys.exit("PROGETTO APERTO in CapCut: chiedi a Nicola di tornare alla home dei progetti")
    d = json.load(open(os.path.join(cart, "draft_info.json")))
    misure = []
    for s, m in parlati(d):
        st = s["source_timerange"]
        l = loudness(m["path"], st["start"] / 1e6, st["duration"] / 1e6)
        if l is None:
            continue
        vol = s.get("volume", 1.0)
        misure.append((s["id"], s["target_timerange"]["start"] / 1e6, os.path.basename(m["path"]), vol,
                       l[0] + 20 * math.log10(vol), l[1]))
    if not misure:
        sys.exit("nessuno spezzone parlato con audio")
    eff = sorted(x[4] for x in misure)
    obiettivo = lufs if lufs is not None else eff[len(eff) // 2]
    nuovi = {}
    for sid, t, clip, vol, e, lra in misure:
        g = max(-MASSIMO, min(MASSIMO, obiettivo - e))
        nota = ""
        if abs(obiettivo - e) <= TOLLERANZA:
            g = 0.0
        else:
            nuovi[sid] = round(vol * 10 ** (g / 20), 3)
        if lra > 6:
            nota = f"  voce variabile dentro lo spezzone ({lra:.0f} dB): da dividere"
        print(f"{t:7.2f}s {clip:12} vol {vol:4.2f} -> {nuovi.get(sid, vol):4.2f}  voce {e:6.1f} LUFS  {g:+5.1f} dB{nota}")
    print(f"obiettivo {obiettivo:.1f} LUFS, scarto prima {eff[-1] - eff[0]:.1f} dB, spezzoni da correggere {len(nuovi)}")
    if not applica or not nuovi:
        return
    for f in copie(cart):
        x = json.load(open(f))
        n = 0
        for t in x["tracks"]:
            for s in t["segments"]:
                if s["id"] in nuovi:
                    s["volume"] = nuovi[s["id"]]
                    s["last_nonzero_volume"] = nuovi[s["id"]]
                    n += 1
        json.dump(x, open(f, "w"), ensure_ascii=False)
        print("aggiornato", os.path.relpath(f, cart), n)


if __name__ == "__main__":
    main()
