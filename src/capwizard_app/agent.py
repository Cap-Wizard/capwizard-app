"""Editing done by Claude Code itself, the way the reel-capcut-mac skill does it.

The app no longer runs the Python engine for a new reel: it starts the user's
own Claude Code (`claude -p`, logged in with their Claude account) with the
skill and the client's profile, and Claude analyses the clips, decides the
cuts, writes the CapCut draft and checks it. Its stream of messages becomes the
job's log and a step counter the app shows as progress.

Client profiles are skills too: a `SKILL.md` that holds one client's rules, so
the same file works in the app and as a standalone skill in Claude Code
(e.g. `reel-andrea-leidi`). A skill counts as a client profile when its
frontmatter says so (`metadata: capwizard-template: …`) or when it is a
`reel-*` skill that names one of the CapCut templates.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from capwizard import registry
from capwizard.ai import _clean_env, _find_claude_cli
from capwizard.draft import CAPCUT_PROJECTS_DIR

SKILLS_DIR = Path.home() / ".claude/skills"
EDITOR_SKILL = "reel-capcut-mac"
WORK_DIR = registry.app_data_dir() / "lavori"

# Progress shown in the app, in the order the skill works.
STEPS = ["Preparazione", "Analisi delle clip", "Decisioni di montaggio",
         "Controllo dei tagli", "Costruzione del progetto", "Verifica"]


# ---------------------------------------------------------------------------
# Skills and client profiles
# ---------------------------------------------------------------------------

def _frontmatter(text: str) -> dict:
    """name, description and the `metadata` map of a SKILL.md (flat YAML only)."""
    out: dict = {"metadata": {}}
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return out
    in_meta = False
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and in_meta and ":" in line:
            k, v = line.strip().split(":", 1)
            out["metadata"][k.strip()] = v.strip().strip("\"'")
            continue
        in_meta = False
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip().strip("\"'")
            if k == "metadata" and not v:
                in_meta = True
            else:
                out[k] = v
    return out


def _skill_files() -> list[Path]:
    """Every SKILL.md Claude Code loads: local skills, then the ones synced from claude.ai."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(SKILLS_DIR.glob("*/SKILL.md")) + sorted(SKILLS_DIR.glob("synced/*/*/SKILL.md"))


def editor_skill() -> Path | None:
    p = SKILLS_DIR / EDITOR_SKILL / "SKILL.md"
    return p if p.exists() else None


def _templates() -> list[str]:
    if not CAPCUT_PROJECTS_DIR.is_dir():
        return []
    return [d.name for d in CAPCUT_PROJECTS_DIR.iterdir() if d.is_dir()]


def list_clients() -> list[dict]:
    templates = _templates()
    named = sorted((t for t in templates if t.upper().startswith("TEMPLATE")), key=len, reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for f in _skill_files():
        try:
            text = f.read_text()
        except OSError:
            continue
        fm = _frontmatter(text)
        meta = fm["metadata"]
        name = fm.get("name") or f.parent.name
        template = meta.get("capwizard-template", "")
        if not template:
            if not name.startswith("reel-") or name == EDITOR_SKILL:
                continue
            template = next((t for t in named if t in text), "")
            if not template:
                continue
        if name in seen:            # a local copy wins over the synced one
            continue
        seen.add(name)
        heading = re.search(r"^# (?:Reel di )?(.+)$", text, re.M)
        colour = meta.get("capwizard-colore") or next(iter(re.findall(r"#[0-9A-Fa-f]{6}\b", text)), "")
        out.append({
            "id": name,
            "nome": meta.get("capwizard-cliente") or (heading.group(1).strip() if heading else name),
            "template": template,
            "template_ok": template in templates,
            "colore": colour or "#7c6cff",
            "descrizione": fm.get("description", ""),
            "path": str(f),
        })
    return sorted(out, key=lambda c: c["nome"].lower())


def get_client(client_id: str | None) -> dict | None:
    if not client_id:
        return None
    return next((c for c in list_clients() if c["id"] == client_id), None)


# ---------------------------------------------------------------------------
# The editing run
# ---------------------------------------------------------------------------

PROMPT = """Monta un reel verticale in CapCut. Lavori in automatico dall'app CapWizard: NON fare domande,
decidi tu seguendo le regole. Se manca qualcosa di indispensabile, fermati e spiegalo nel risultato.

1. Usa la skill **{skill}** e seguila passo per passo: {skill_md}
   Gli script sono in `{skill_dir}/scripts` (SKILL_DIR = `{skill_dir}`).
{client}
Dati del lavoro:
- Cartella delle clip: `{clips}`
- Nome del progetto CapCut: «{name}» (non esiste ancora: crealo con questo nome esatto)
- Template del cliente: «{template}»
- Copione: {script}
- Durata: {duration}
- Titolo iniziale: {title}
{extra}
Cartella di lavoro (è la tua `<lavoro>`): `{work}`

Alla fine scrivi il file `{result}` con questo JSON:
{{"progetto": "<percorso completo della cartella del progetto in com.lveditor.draft>", "nome": "{name}",
 "durata": <secondi>, "riassunto": "<cosa hai fatto, in italiano, 5-8 righe>",
 "da_fare_a_mano": ["<cosa resta da fare in CapCut>", ...]}}
Se non riesci a creare il progetto scrivi lo stesso il file con "progetto": null e il motivo nel riassunto.
Scrivi messaggi brevi in italiano su cosa stai facendo, passo per passo."""


def _step_of(tool: str, inp: dict) -> int:
    """Which progress step a tool call belongs to (0 = no change)."""
    cmd = str(inp.get("command", ""))
    path = str(inp.get("file_path", ""))
    if "verifica.py" in cmd:
        return 6
    if "costruisci_draft.py" in cmd:
        return 5
    if "controlla_tagli.py" in cmd:
        return 4
    if tool in ("Write", "Edit") and path.endswith("config.json"):
        return 3
    if "analizza.py" in cmd:
        return 2
    return 1


def _describe(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return inp.get("description") or str(inp.get("command", ""))[:90]
    if tool in ("Read", "Write", "Edit"):
        verb = {"Read": "leggo", "Write": "scrivo", "Edit": "modifico"}[tool]
        return f"{verb} {Path(str(inp.get('file_path', ''))).name}"
    if tool == "Skill":
        return f"carico la skill {inp.get('skill', '')}"
    if tool in ("Glob", "Grep"):
        return f"cerco {inp.get('pattern', '')}"
    return tool


def _slug(s: str) -> str:
    return re.sub(r"[^\w\- ]", "_", s).strip() or "video"


def check_request(req) -> str | None:
    """Why this reel can't start, or None."""
    if not _find_claude_cli():
        return "Non trovo Claude Code sul Mac: installalo e fai il login una volta (`claude` → `/login`)."
    if not editor_skill():
        return f"Manca la skill {EDITOR_SKILL} in {SKILLS_DIR}."
    if not Path(req.input_path).exists():
        return f"La cartella delle clip non esiste: {req.input_path}"
    if not req.template:
        return "Scegli il cliente o il template."
    tdir = CAPCUT_PROJECTS_DIR / req.template
    if not tdir.is_dir():
        return f"Il template «{req.template}» non c'è tra i progetti CapCut."
    if (tdir / ".locked").exists():
        return f"Il template «{req.template}» è aperto in CapCut: torna alla home dei progetti e riprova."
    if (CAPCUT_PROJECTS_DIR / req.name).exists():
        return f"Esiste già un progetto CapCut «{req.name}»: scegli un altro nome."
    return None


def run_agent(job, req) -> tuple[str, str]:
    """Edit one reel with Claude Code. Returns (project path, report path)."""
    problem = check_request(req)
    if problem:
        raise RuntimeError(problem)
    skill_md = editor_skill()
    client = get_client(getattr(req, "client", None))
    work = WORK_DIR / f"{_slug(req.name)} {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)
    result = work / "risultato.json"

    extra = []
    if req.ai_zoom:
        extra.append("- Aggiungi zoom leggeri sulle frasi chiave.")
    if not req.sentence_transitions:
        extra.append("- Niente zoom, transizione e woosh a fine frase.")
    if req.aggressive_fillers:
        extra.append("- Togli anche gli intercalari cioè / tipo / diciamo.")
    if getattr(req, "notes", None):
        extra.append(f"- Note di Nicola (vincolanti): {req.notes}")
    prompt = PROMPT.format(
        skill=EDITOR_SKILL, skill_md=skill_md, skill_dir=skill_md.parent,
        client=(f"2. Profilo del cliente **{client['nome']}**: leggi `{client['path']}`. Prevale sulla skill "
                f"generale dove è più preciso.\n") if client else "",
        clips=req.input_path, name=req.name, template=req.template,
        script=f"`{req.script_path}` (indicazioni vincolanti)" if req.script_path else "nessuno",
        duration=f"{req.ai_target_duration:g} s" if req.ai_target_duration else "la decidi tu",
        title=f"«{req.intro_title}»" if req.intro_title else "lo decidi tu",
        extra="\n".join(extra), work=work, result=result,
    )

    dirs = {str(Path(req.input_path)), str(CAPCUT_PROJECTS_DIR), str(skill_md.parent), str(work)}
    if client:
        dirs.add(str(Path(client["path"]).parent))
    if req.script_path:
        dirs.add(str(Path(req.script_path).parent))
    cmd = [_find_claude_cli(), "-p", "--output-format", "stream-json", "--verbose",
           "--model", req.ai_model or "opus", "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill",
           "--add-dir", *sorted(dirs)]

    job.log(f"🎬 Avvio Claude Code ({req.ai_model}) con la skill {EDITOR_SKILL}"
            + (f" e il profilo di {client['nome']}" if client else ""))
    job.set_step(1)
    proc = subprocess.Popen(cmd, cwd=work, env=_clean_env(), text=True, bufsize=1,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    job.proc = proc
    proc.stdin.write(prompt)
    proc.stdin.close()

    transcript = [f"# {req.name}\n"]
    error = None
    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            low = line.lower()
            if "logged in" in low or "/login" in low:
                error = "Claude non è collegato: apri il Terminale, scrivi `claude`, poi `/login`."
            job.log(line[:300])
            continue
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    text = c["text"].strip()
                    transcript.append(text)
                    for ln in text.splitlines():
                        if ln.strip():
                            job.log(ln)
                elif c.get("type") == "tool_use":
                    tool, inp = c.get("name", ""), c.get("input") or {}
                    job.set_step(_step_of(tool, inp))
                    job.log(f"⚙ {_describe(tool, inp)}")
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                error = error or f"Claude si è fermato: {str(ev.get('result', ''))[:300]}"
            cost = ev.get("total_cost_usd")
            mins = (ev.get("duration_ms") or 0) / 60000
            job.log(f"⏱ {mins:.0f} min" + (f" · ${cost:.2f}" if cost else ""))
    proc.wait()
    job.proc = None
    if job.stopped:
        raise RuntimeError("Montaggio fermato.")

    try:
        res = json.loads(result.read_text())
    except (OSError, ValueError):
        raise RuntimeError(error or "Claude ha finito senza scrivere il risultato.")
    if not res.get("progetto"):
        raise RuntimeError(res.get("riassunto") or error or "Progetto non creato.")

    todo = [t for t in res.get("da_fare_a_mano") or [] if t]
    report = work / "report.md"
    report.write_text(
        f"# {req.name}\n\nCliente: {client['nome'] if client else '—'} · template «{req.template}» · "
        f"durata {res.get('durata', '?')} s\n\n## Cosa ha fatto Claude\n{res.get('riassunto', '')}\n\n"
        "## DA FARE A MANO IN CAPCUT\n" + ("\n".join(f"- {t}" for t in todo) or "- niente") +
        "\n\n## Messaggi di Claude\n\n" + "\n\n".join(transcript[1:]) + "\n")
    job.log("✓ " + res.get("riassunto", "Progetto creato.").replace("\n", " "))
    for t in todo:
        job.log(f"☐ {t}")
    try:
        registry.add_project(name=req.name, result_path=res["progetto"],
                             settings=req.model_dump(), report_path=str(report))
    except Exception as reg_err:  # noqa: BLE001 — registry is best-effort
        job.log(f"⚠ impossibile salvare nel registro progetti: {reg_err}")
    return res["progetto"], str(report)


def stop(job) -> None:
    proc = getattr(job, "proc", None)
    if proc and proc.poll() is None:
        job.stopped = True
        proc.terminate()
        threading.Timer(5, lambda: proc.poll() is None and proc.kill()).start()
