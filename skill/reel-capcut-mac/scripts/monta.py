"""Passi 5 e 6 in un comando solo: controlla i tagli, costruisce il progetto, lo mette tra i progetti di
CapCut, livella la voce e lo verifica. Le code («CODA») le corregge controlla_tagli da solo; gli
altri «!» (attacchi sporchi, parole perse, voce bassa, ripetizioni) li elenca alla fine: decidi tu se
contano, e in quel caso correggi il config e rilancia con --rifai.

uso: python3 monta.py CARTELLA_VIDEO "NOME PROGETTO" config.json [--profilo CARTELLA_PROFILO]
                     [--costruttore SCRIPT] [--rifai]

Costruttore: --costruttore, se no `costruisci.py` nella cartella del profilo del cliente (quello
scritto per il suo template), se no costruisci_draft.py. --rifai sostituisce il progetto costruito
da un giro precedente (solo se CapCut non lo ha aperto).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# CAPWIZARD_CAPCUT_PROJECTS: build somewhere else, to try a builder without CapCut seeing the project
PROJECTS = Path(os.environ.get("CAPWIZARD_CAPCUT_PROJECTS") or Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft")


def run(title: str, args: list) -> tuple[int, str]:
    print(f"\n=== {title}", flush=True)
    r = subprocess.run([sys.executable, *map(str, args)], cwd=HERE, capture_output=True, text=True)
    out = (r.stdout + r.stderr).rstrip()
    print(out, flush=True)
    return r.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path, help="la cartella <lavoro>/<video> (out/ va lì dentro)")
    ap.add_argument("nome")
    ap.add_argument("config", type=Path)
    ap.add_argument("--profilo", type=Path)
    ap.add_argument("--costruttore", type=Path)
    ap.add_argument("--rifai", action="store_true")
    a = ap.parse_args()
    config = a.config.resolve()

    outdir = a.video.resolve() / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    # controlla_tagli --applica lengthens every tail a little each time it runs: on a second run with the
    # cuts untouched since the last check it is skipped (its report is kept from that run)
    seen, report = outdir / ".tagli_controllati", outdir / "controllo_tagli.txt"
    digest = lambda: hashlib.sha1(config.read_bytes()).hexdigest()
    if seen.is_file() and seen.read_text() == digest() and report.is_file():
        out = report.read_text()
        print("\n=== controllo dei tagli: già fatto su questo config, salto")
    else:
        code, out = run("controllo dei tagli", [HERE / "controlla_tagli.py", config, "--applica", "--parole"])
        if code:
            print("\n✗ controlla_tagli non è andato a buon fine: guarda l'errore qui sopra.")
            return 1
        report.write_text(out)
        seen.write_text(digest())
    # which cut each «!» belongs to: the line before it is the cut («  7 C4256 0.38-11.13 | …»)
    bangs, cut = [], ""
    for l in out.splitlines():
        if l.lstrip().startswith("!"):
            if not l.lstrip().startswith("! CODA"):          # already fixed in the config (--applica)
                bangs.append(f"{cut}: {l.strip()}")
        elif l.strip():
            cut = " ".join(l.split()[:2])

    builder = a.costruttore or (a.profilo / "costruisci.py" if a.profilo and (a.profilo / "costruisci.py").is_file() else None) \
        or HERE / "costruisci_draft.py"
    code, _ = run(f"costruisco con {builder.name}", [builder, outdir, a.nome, config])
    if code or not (outdir / "draft_info.json").is_file():
        print(f"\n✗ {builder.name} non ha costruito il progetto.")
        return 1

    project = PROJECTS / a.nome
    if project.exists():
        if not a.rifai:
            print(f"\n✗ Esiste già il progetto CapCut «{a.nome}»: se è quello di un giro precedente rilancia con --rifai.")
            return 1
        if (project / ".locked").exists():
            print(f"\n✗ «{a.nome}» è aperto in CapCut: non lo sostituisco.")
            return 1
        shutil.rmtree(project)
    project.mkdir(parents=True)
    for f in ("draft_info.json", "draft_meta_info.json"):     # solo questi due: mai la cartella del template
        shutil.copy2(outdir / f, project / f)
    print(f"\n=== progetto: {project}")

    code, _ = run("livello della voce", [HERE / "livella_voce.py", project, "--applica"])
    if code:
        print("\n✗ livella_voce non è andato a buon fine.")
        return 1
    code, out = run("verifica", [HERE / "verifica.py", project, config])
    m = re.search(r"ERRORI (\d+)", out)
    if code or not m or int(m.group(1)):
        print("\n✗ La verifica ha trovato problemi: correggi e rilancia con --rifai.")
        return 3
    print(f"\n✓ Fatto: «{a.nome}» costruito, voce livellata, verifica pulita. Riepilogo: {outdir / 'plan.json'}")
    if bangs:
        print(f"\n! Da guardare ({len(bangs)}): se uno conta davvero, correggi il config e rilancia con --rifai.")
        print("\n".join(f"  {b}" for b in bangs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
