"""Analisi completa di una cartella di clip, in un solo passaggio.

uso: python3 analizza.py CARTELLA_CLIP CARTELLA_LAVORO [--soglia -30] [--mosaico]

Per ogni clip scrive CARTELLA_LAVORO/res/<clip>.json con: durata reale (fotogrammi), fps,
rotazione e come metterla in CapCut, livelli, parole con tempi e livello (forte = voce di chi
parla; debole = suggeritore, conti alla rovescia, voci lontane), tratti di voce piena con la
loro trascrizione. Stampa un riassunto leggibile: [parole deboli] tra parentesi quadre.
Le clip gia' analizzate si saltano (rilancia lo stesso comando per riprendere).
Con --mosaico crea CARTELLA_LAVORO/mosaico.jpg (3 fotogrammi per clip, con il nome).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

import audio

EXT = {".mp4", ".mov", ".m4v", ".mts", ".mxf"}


def mosaico(files, out):
    tmp = out.parent / "prov"
    tmp.mkdir(exist_ok=True)
    tiles = []
    for f in files:
        info = audio.probe(str(f))
        t = [info["durata"] * k for k in (0.25, 0.5, 0.8)]
        dest = tmp / f"{f.stem}.jpg"
        if not dest.exists():
            ins = sum((["-ss", f"{x:.2f}", "-i", str(f)] for x in t), [])
            fc = "".join(f"[{i}:v]scale=-2:300[v{i}];" for i in range(3)) + "[v0][v1][v2]hstack=3," \
                 f"drawtext=text='{f.stem}':x=6:y=6:fontsize=26:fontcolor=yellow:box=1:boxcolor=black"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *ins, "-filter_complex", fc,
                            "-frames:v", "1", str(dest)], capture_output=True)
        tiles.append(dest)
    cols = 4
    args = sum((["-i", str(t)] for t in tiles), [])
    fc = "".join(f"[{i}:v]scale=-2:240[t{i}];" for i in range(len(tiles)))
    lay = "|".join(("0" if i % cols == 0 else "+".join(["w0"] * (i % cols))) + "_" +
                   ("0" if i // cols == 0 else "+".join(["h0"] * (i // cols))) for i in range(len(tiles)))
    fc += "".join(f"[t{i}]" for i in range(len(tiles))) + f"xstack=inputs={len(tiles)}:layout={lay}:fill=black"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex", fc, "-q:v", "4", str(out)],
                   capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--soglia", type=float, default=None, help="dB sopra cui una parola e' 'forte' (default: relativa alla clip)")
    ap.add_argument("--mosaico", action="store_true")
    a = ap.parse_args()
    (a.out / "res").mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in a.clips.iterdir() if f.suffix.lower() in EXT and not f.name.startswith("._"))
    for f in files:
        dest = a.out / "res" / f"{f.stem}.json"
        if dest.exists():
            r = json.loads(dest.read_text())
        else:
            info = audio.probe(str(f))
            r = {"file": f.name, "percorso": str(f), **info, "capcut": audio.rotazione_capcut(info)}
            if info["audio"]:
                d = audio.db(str(f))
                forte = float(np.percentile(d[d > -60], 95)) if (d > -60).any() else -20
                soglia = a.soglia if a.soglia is not None else max(-32.0, forte - 14)
                r["livello_forte_db"], r["soglia_db"], r["fondo_db"] = round(forte, 1), round(soglia, 1), round(audio.fondo(d), 1)
                parole = audio.trascrivi(str(f))
                for w in parole:
                    w.append(round(audio.livello(d, w[1], w[2]), 1))
                    w.append(w[3] > soglia)
                r["parole"] = parole   # [parola, inizio, fine, dB, forte]
                r["tratti_forti"] = [{"da": s, "a": e, "testo": " ".join(x[0] for x in audio.trascrivi(str(f), s, e))}
                                     for s, e in audio.tratti_forti(str(f), 0, info["durata"], soglia)]
            dest.write_text(json.dumps(r, ensure_ascii=False))
        rot = r["capcut"]["nota"]
        print(f"\n## {f.stem}  {r['durata']:.2f}s  {rot}")
        if r.get("parole"):
            print("   " + " ".join(w[0] if w[4] else f"[{w[0]}]" for w in r["parole"]))
            for t in r["tratti_forti"]:
                print(f"   forte {t['da']:.2f}-{t['a']:.2f} | {t['testo']}")
    if a.mosaico:
        mosaico(files, a.out / "mosaico.jpg")
        print(f"\nmosaico: {a.out / 'mosaico.jpg'}")


if __name__ == "__main__":
    main()
