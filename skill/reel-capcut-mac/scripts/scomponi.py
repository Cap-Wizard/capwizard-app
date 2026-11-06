"""Scompone un video già montato da altri nelle sue inquadrature, per rimontarlo da capo.

uso: python3 scomponi.py VIDEO CARTELLA_LAVORO [--soglia 0.30]

Trova i tagli (cambi di scena) con ffmpeg e scrive CARTELLA_LAVORO/inquadrature.json:
[{"n": 1, "inizio": 0.0, "fine": 3.42}, ...] (secondi nel file) e CARTELLA_LAVORO/inquadrature.jpg,
il primo fotogramma di ogni inquadratura col suo numero e tempo. Stampa l'elenco.
Ogni inquadratura poi è materiale grezzo: tagli e ordine li decidi tu con le regole di montaggio,
non quelli di chi l'ha montato (vedi SKILL.md, «Video già montato da altri»).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def durata(video: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def tagli(video: str, soglia: float) -> list[float]:
    """tempi dei cambi di scena (su una copia piccola: veloce anche coi 4K)"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-an", "-vf",
                        f"scale=270:-2,select='gt(scene,{soglia})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [float(t) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("lavoro")
    ap.add_argument("--soglia", type=float, default=0.30)
    ap.add_argument("--minimo", type=float, default=0.4, help="inquadrature più corte si uniscono alla precedente")
    a = ap.parse_args()
    out = Path(a.lavoro)
    out.mkdir(parents=True, exist_ok=True)
    tot = durata(a.video)
    cuts = [0.0]
    for t in tagli(a.video, a.soglia):
        if t - cuts[-1] >= a.minimo and tot - t >= a.minimo:
            cuts.append(round(t, 3))
    shots = [{"n": i + 1, "inizio": s, "fine": round(cuts[i + 1] if i + 1 < len(cuts) else tot, 3)}
             for i, s in enumerate(cuts)]
    (out / "inquadrature.json").write_text(json.dumps(shots, indent=1))
    # foglio: primo fotogramma (+0,1 s) di ogni inquadratura, con numero e tempo
    frames = out / "inq"
    frames.mkdir(exist_ok=True)
    for s in shots:
        f = str(frames / f"{s['n']:03d}.jpg")
        base = ["ffmpeg", "-v", "error", "-y", "-ss", f"{s['inizio'] + 0.1:.2f}", "-i", a.video, "-frames:v", "1", "-vf"]
        label = (f"scale=180:-2,drawtext=text='{s['n']} {s['inizio']:.1f}s':x=6:y=6:fontsize=18:"
                 "fontcolor=white:box=1:boxcolor=black@0.6")
        if subprocess.run(base + [label, f], capture_output=True).returncode:   # ffmpeg senza drawtext
            subprocess.run(base + ["scale=180:-2", f], capture_output=True)
    cols = min(8, len(shots)) or 1
    rows = (len(shots) + cols - 1) // cols
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-pattern_type", "glob", "-i", str(frames / "*.jpg"),
                    "-vf", f"tile={cols}x{rows}", "-frames:v", "1", str(out / "inquadrature.jpg")], capture_output=True)
    print(f"{a.video}: {len(shots)} inquadrature in {tot:.1f} s (taglio ogni {tot / max(1, len(shots)):.1f} s)")
    for s in shots:
        print(f"  {s['n']:3d}  {s['inizio']:7.2f} - {s['fine']:7.2f}  ({s['fine'] - s['inizio']:.2f} s)")


if __name__ == "__main__":
    main()
