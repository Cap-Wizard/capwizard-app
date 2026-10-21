"""Controlla (e corregge) i tagli del config prima di costruire il progetto.

uso: python3 controlla_tagli.py config.json [--applica] [--parole]

Per ogni spezzone [clip, da, a, testo, pause]:
  - ATTACCO: primo punto di voce piena; segnala se in testa c'e' piu' di 0,15s di altro
    (respiro, suggeritore, "vai"). Non sposta mai l'attacco da solo.
  - CODA: non taglia mai la fine della frase. Fine = max(fine dell'ultima parola trascritta su una
    finestra allungata di 0,5s, voce sopra il rumore di fondo) + 0,08s; ci si ferma alla prima
    pausa vera (>= 0,2s), cosi' suggeritore e conti alla rovescia che seguono restano fuori.
  - PAUSE TOLTE: ritrascrive ogni pezzo e controlla che le parole della finestra intera ci
    siano tutte (una parola detta piano dentro la "pausa" verrebbe persa).
  - SUGGERITORE: tratti di voce 9-24 dB piu' bassi del resto dello spezzone per >= 0,6s.
  - RIPETIZIONI: stessa coppia di parole due volte nello spezzone (suggerimento + ripetizione).
Con --applica riscrive nel config le code corrette. Con --parole salva in config["words"] le
parole di ogni spezzone trascritte pezzo per pezzo (servono ai sottotitoli automatici).
"""
from __future__ import annotations

import argparse
import json
import re

import numpy as np

import audio


def pezzi(a, b, gaps):
    out, cur = [], a
    for g0, g1 in gaps:
        out.append((cur, g0)); cur = g1
    out.append((cur, b))
    return out


def coda(clip, b, d, fondo):
    """Nuova fine dello spezzone che termina a b."""
    parole = audio.trascrivi(clip, max(0, b - 1.2), b + 0.5)
    fine_parola = max([w[2] for w in parole if w[1] < b - 0.02] or [b])
    soglia = fondo + 8
    k = int(max(b, fine_parola) * 100) - 5
    ultimo, silenzio = k, 0
    while k < len(d) and k < int(b * 100) + 60:
        if d[k] > soglia:
            ultimo, silenzio = k, 0
        else:
            silenzio += 1
            if silenzio >= 20:          # pausa vera: 0,2s
                break
        k += 1
    return round(max(b, fine_parola, (ultimo + 1) / 100) + 0.08, 2)


def norm(t):
    return re.sub(r"[^\w']", "", t.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--applica", action="store_true")
    ap.add_argument("--parole", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    ext = cfg.get("ext", ".MP4")
    words = cfg.setdefault("words", {})
    for i, cut in enumerate(cfg["cuts"], 1):
        c, s, e = cut[0], cut[1], cut[2]
        gaps = cut[4] if len(cut) > 4 else []
        clip = f"{cfg['clipdir']}/{c}{ext}"
        d = audio.db(clip)
        fondo = audio.fondo(d)
        note = []
        # attacco
        forti = audio.tratti_forti(clip, s, e)
        if forti and forti[0][0] - s > 0.15:
            note.append(f"ATTACCO: {forti[0][0] - s:.2f}s prima della voce piena (voce da {forti[0][0]:.2f})")
        # coda
        ne = coda(clip, e, d, fondo)
        if ne > e + 0.02:
            note.append(f"CODA: {e:.2f} -> {ne:.2f}")
            if a.applica:
                cut[2] = ne
                e = ne
        # pause tolte e parole per pezzo
        tutte = audio.trascrivi(clip, s, e)
        per_pezzo = []
        for p0, p1 in pezzi(s, e, gaps):
            per_pezzo += audio.trascrivi(clip, p0, p1)
        if gaps:
            mancano = [w[0] for w in tutte if norm(w[0]) and norm(w[0]) not in {norm(x[0]) for x in per_pezzo}]
            if mancano:
                note.append(f"PAUSA TOLTA: mancano {mancano}")
        if a.parole:
            words[f"{c}:{s:.2f}:{e:.2f}"] = per_pezzo
        # suggeritore
        idx = [j for j in range(int(s * 10), min(int(e * 10), len(d) // 10))
               if not any(g0 <= j / 10 < g1 for g0, g1 in gaps)]
        v = np.array([audio.livello(d, j / 10, j / 10 + 0.1, 50) for j in idx])
        if len(v):
            forte = np.percentile(v, 85)
            low = (v < forte - 9) & (v > forte - 24)
            run = 0
            for j, x in enumerate(list(low) + [False]):
                if x:
                    run += 1
                else:
                    if run >= 6:
                        note.append(f"VOCE PIU' BASSA {idx[j - run] / 10:.1f}-{idx[j - 1] / 10 + 0.1:.1f}s: suggeritore o fine frase piano? ascoltare")
                    run = 0
        # ripetizioni
        t = [norm(w[0]) for w in per_pezzo]
        bi = [" ".join(t[k:k + 2]) for k in range(len(t) - 1)]
        rep = sorted({x for x in bi if bi.count(x) > 1})
        if rep:
            note.append(f"RIPETIZIONE {rep}: suggerimento + ripetizione?")
        testo = " ".join(w[0] for w in per_pezzo)
        print(f"{i:>2} {c} {s:.2f}-{e:.2f} | {testo}")
        for n in note:
            print(f"     ! {n}")
    if a.applica or a.parole:
        json.dump(cfg, open(a.config, "w"), ensure_ascii=False, indent=1)
        print("config aggiornato")


if __name__ == "__main__":
    main()
