"""Smistamento clip: the Kora "scarico" (the `scarico` skill) done from the app.

Two steps, like the skill wants:
1. analyse: a script lists the clips in camera order, finds the black separator
   clips and transcribes everything; Claude then splits the clips into videos
   and matches them to the PDP. Nothing is touched: the result is a plan.
2. execute: once the plan is confirmed in the app, the app itself renames the
   clips `CODICE - 0001.ext` (one sequence over the whole shooting, black clips
   included) into one folder per video under the output folder. It never
   overwrites and never deletes: it copies, or moves only within the same disk.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from capwizard_app import vault
from capwizard_app.agent import SKILLS_DIR, WORK_DIR, _run_claude, _slug
from capwizard_app.capcut import find_claude_cli

SKILL = "scarico"
TOOL = vault.TOOLS / "scarico_analizza.py"


def skill_md() -> Path | None:
    """The scarico skill: the user's own copy first (Claude Code, then the desktop app's), then the one shipped with the app."""
    cands = [SKILLS_DIR / SKILL / "SKILL.md", *sorted(SKILLS_DIR.glob(f"synced/*/{SKILL}/SKILL.md")),
             *sorted(Path.home().glob(f"Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills/{SKILL}/SKILL.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)]
    if os.environ.get("CAPWIZARD_SKILL_DIR"):
        cands.append(Path(os.environ["CAPWIZARD_SKILL_DIR"]).parent / SKILL / "SKILL.md")
    cands.append(Path(__file__).resolve().parents[2] / "skill" / SKILL / "SKILL.md")
    return next((p for p in cands if p.exists()), None)


def client_codes() -> list[dict]:
    """The «Codici cliente» table of the skill."""
    md = skill_md()
    if not md:
        return []
    return [{"codice": c, "cliente": n.strip()} for c, n in re.findall(r"^\|\s*([A-Z]{4})\s*\|\s*([^|]+?)\s*\|", md.read_text(), re.M)]


PROMPT = """Smistamento del girato di uno shooting per l'agenzia Kora. Lavori in automatico dall'app CapWizard: NON fare domande
e NON toccare le clip (niente rinomina, niente spostamenti): l'app mostra il tuo piano a Nicola e lo esegue lei dopo il suo ok.

Segui la skill **{skill}**: {skill_md}
Fai solo la parte di analisi del passo 3 (smistamento per PDP) e prepara quello che servirebbe per la tabella del passo 4.
L'inventario, la ricerca delle clip nere e la trascrizione li ha già fatti l'app: sono in `{inventario}`
(una riga per clip in ordine di numero camera: "n", "nome", "durata", "yavg", "nera", "mano" = forse obiettivo coperto con la mano, "testo").

Dati:
- Cliente: {codice}
- PDP / piano editoriale: {pdp}
- Note di Nicola: {note}

Per guardare i frame di un gruppo dubbio: `{ffmpeg} -ss <t> -i <clip> -frames:v 1 -vf scale=-2:360 <file>` in `{work}`, poi affiancali con hstack.
Se non c'è il PDP, dividi lo stesso con le clip nere e dai a ogni video un titolo breve dal contenuto (segnalalo come dubbio).

Alla fine scrivi `{result}` con questo JSON (le clip si indicano con il loro "n"):
{{"gruppi": [{{"titolo": "<titolo del video come nel PDP>", "clip": [<n>, ...], "estratto": "<frase chiave della trascrizione>",
              "dubbio": "<perché non sei sicuro, o stringa vuota>"}}],
 "nere": [<n delle clip nere separatrici, anche quelle con la mano>],
 "mancanti": ["<video del PDP che non trovi nel girato>"],
 "fuori_pdp": ["<titolo provvisorio di un video girato che nel PDP non c'è>"],
 "note": "<riassunto per Nicola, in italiano, 3-6 righe>"}}
Ogni clip non nera deve stare in un gruppo, in ordine di "n". Scrivi messaggi brevi in italiano su cosa stai facendo."""


def _check_output(output: str) -> Path:
    out = Path(output).expanduser()
    if not output.strip():
        raise RuntimeError("Scegli la cartella dove mettere le clip smistate.")
    if not out.exists() and not out.parent.is_dir():
        raise RuntimeError(f"La cartella di destinazione non esiste: {out}")
    return out


def analyse(job, d: dict) -> dict:
    if not find_claude_cli():
        raise RuntimeError("Non trovo Claude Code sul Mac.")
    md = skill_md()
    if not md:
        raise RuntimeError("Manca la skill scarico.")
    codice = (d.get("codice") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,6}", codice):
        raise RuntimeError("Scrivi il codice del cliente (es. GISE).")
    if not d.get("clip"):
        raise RuntimeError("Trascina le clip o la cartella dello shooting.")
    _check_output(d.get("output") or "")
    work = WORK_DIR / f"scarico {_slug(codice)} {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)

    job.log(f"▶ 1/2 Inventario, clip nere e trascrizione ({len(d['clip'])} elementi trascinati)")
    env = dict(os.environ, CAPWIZARD_FFMPEG=vault.ffmpeg() or "")
    proc = subprocess.Popen([vault.tools_python(), str(TOOL), str(work), json.dumps(d["clip"])], env=env, text=True, bufsize=1,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    job.proc = proc
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-5:]
            job.log(line[:300])
    proc.wait()
    job.proc = None
    if job.stopped:
        raise RuntimeError("Smistamento fermato.")
    inv_path = work / "inventario.json"
    if proc.returncode or not inv_path.exists():
        raise RuntimeError("Analisi delle clip non riuscita: " + " / ".join(tail[-2:]))
    inv = json.loads(inv_path.read_text())
    if not inv:
        raise RuntimeError("Nessuna clip video trovata in quello che hai trascinato.")
    job.set_step(2)

    job.log(f"▶ 2/2 Claude divide le clip nei video{' e le abbina al PDP' if (d.get('pdp') or '').strip() else ''}")
    pdp = (d.get("pdp") or "").strip()
    if pdp:
        (work / "pdp.txt").write_text(pdp)
    result = work / "piano.json"
    prompt = PROMPT.format(skill=SKILL, skill_md=md, inventario=inv_path, codice=codice,
                           pdp=f"in `{work / 'pdp.txt'}`" if pdp else "nessuno",
                           note=(d.get("note") or "").strip() or "nessuna", ffmpeg=vault.ffmpeg() or "ffmpeg",
                           work=work, result=result)
    dirs = {str(work), str(md.parent)} | {str(Path(p) if Path(p).is_dir() else Path(p).parent) for p in d["clip"]}
    error = _run_claude(job, prompt, dirs, d.get("ai_model") or "sonnet", work, [],
                        step_of=lambda t, i: 2)
    if job.stopped:
        raise RuntimeError("Smistamento fermato.")
    try:
        plan = json.loads(result.read_text())
    except (OSError, ValueError):
        raise RuntimeError(error or "Claude ha finito senza scrivere il piano.")
    return _with_names(plan, inv, codice, work)


def _with_names(plan: dict, inv: list[dict], codice: str, work: Path) -> dict:
    """The plan as the app shows it: every clip with its old and new name; clips Claude left out go to _NON SMISTATE."""
    by_n = {r["n"]: r for r in inv}
    width = max(4, len(str(len(inv))))

    def clip(n):
        r = by_n[n]
        return {"n": n, "nome": r["nome"], "nuovo": f"{codice} - {n:0{width}d}{Path(r['nome']).suffix}"}

    used = set()
    gruppi = []
    for g in plan.get("gruppi") or []:
        ns = sorted({int(n) for n in g.get("clip") or [] if int(n) in by_n} - used)
        used |= set(ns)
        if ns:
            gruppi.append({"titolo": str(g.get("titolo") or "Senza titolo").strip(), "clip": [clip(n) for n in ns],
                           "estratto": g.get("estratto") or "", "dubbio": g.get("dubbio") or ""})
    nere = sorted({int(n) for n in plan.get("nere") or [] if int(n) in by_n} - used)
    used |= set(nere)
    rest = [n for n in by_n if n not in used]
    return {"lavoro": str(work), "codice": codice, "totale": len(inv), "gruppi": gruppi, "nere": [clip(n) for n in nere],
            "non_smistate": [clip(n) for n in rest], "mancanti": plan.get("mancanti") or [],
            "fuori_pdp": plan.get("fuori_pdp") or [], "note": plan.get("note") or ""}


def _folder_name(s: str) -> str:
    return re.sub(r"[/:]", "-", s).strip().strip(".") or "Senza titolo"


def execute(job, d: dict) -> dict:
    """Rename and file the clips as in the confirmed plan. Checks every target first: if one exists, nothing is touched."""
    plan = d["piano"]
    work = Path(plan["lavoro"])
    inv = {r["n"]: r for r in json.loads((work / "inventario.json").read_text())}
    root = _check_output(d.get("output") or "")
    shooting = (d.get("shooting") or "").strip().rstrip("°")
    if shooting:
        root = root / f"{plan['codice']} - {shooting}° Shooting - {(d.get('mmaa') or time.strftime('%m%y')).strip()}"
    move = d.get("modo") == "sposta"

    moves = []   # (src, dest, folder, title)
    for g in plan["gruppi"]:
        folder = _folder_name(g["titolo"])
        moves += [(inv[c["n"]]["path"], root / folder / c["nuovo"], folder, g["titolo"]) for c in g["clip"]]
    moves += [(inv[c["n"]]["path"], root / "_CLIP NERE" / c["nuovo"], "_CLIP NERE", "") for c in plan["nere"]]
    moves += [(inv[c["n"]]["path"], root / "_NON SMISTATE" / c["nuovo"], "_NON SMISTATE", "") for c in plan.get("non_smistate") or []]

    missing = [s for s, *_ in moves if not os.path.exists(s)]
    taken = [str(t) for _, t, *_ in moves if t.exists()]
    if missing:
        raise RuntimeError(f"{len(missing)} clip non ci sono più (es. {missing[0]}): non ho toccato niente.")
    if taken:
        raise RuntimeError(f"{len(taken)} file esistono già (es. {taken[0]}): non sovrascrivo, non ho toccato niente.")

    root.mkdir(parents=True, exist_ok=True)
    same_disk = os.stat(root).st_dev
    log_rows, copied = [], 0
    job.log(f"▶ {'Sposto' if move else 'Copio'} {len(moves)} clip in {root}")
    for i, (src, dest, folder, title) in enumerate(moves, 1):
        if job.stopped:
            raise RuntimeError(f"Fermato dopo {i - 1} clip: il registro è in {root / '_rinomina_log.csv'}.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move and os.stat(src).st_dev == same_disk:
            os.rename(src, dest)
        else:
            shutil.copy2(src, dest)
            copied += 1
        log_rows.append([folder, src, dest.name, title])
        if i % 10 == 0 or i == len(moves):
            job.log(f"  {i}/{len(moves)} · {dest.parent.name}/{dest.name}")
        with open(root / "_rinomina_log.csv", "w", newline="") as fh:   # rewritten as we go, so a stop still leaves it complete
            csv.writer(fh).writerows([["cartella", "originale", "nuovo", "titolo video"], *log_rows])

    summary = []
    for folder in sorted({f for _, _, f, _ in moves}):
        names = sorted(t.name for _, t, f, _ in moves if f == folder and t.exists())
        summary.append({"cartella": folder, "clip": len(names), "da": names[0] if names else "", "a": names[-1] if names else ""})
    done = sum(s["clip"] for s in summary)
    if move and copied:
        job.log(f"⚠ {copied} clip erano su un altro disco: le ho copiate, gli originali sono rimasti dove erano.")
    job.log(("✓" if done >= len(moves) else "⚠") + f" {done} file nelle cartelle, {len(moves)} previsti")
    return {"cartella": str(root), "riepilogo": summary, "previsti": len(moves), "trovati": done}
