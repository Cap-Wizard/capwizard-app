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
import shutil
import subprocess
import threading
import time
from pathlib import Path

from capwizard_app import registry, vault
from capwizard_app.capcut import CAPCUT_PROJECTS_DIR, claude_launch, find_claude_cli

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


def _version(skill_md: Path) -> tuple:
    v = _frontmatter(skill_md.read_text())["metadata"].get("version", "") if skill_md.exists() else ""
    return tuple(int(n) if n.isdigit() else n for n in re.split(r"[.\-]", v)) if v else ()


def install_editor_skill(bundled: Path | None) -> str:
    """Put the skill that ships with the app in ~/.claude/skills, unless a newer one is there.
    An older copy is kept in the app's backup folder first."""
    if not bundled or not (bundled / "SKILL.md").exists():
        return "nessuna skill nell'app"
    target = SKILLS_DIR / EDITOR_SKILL
    new, old = _version(bundled / "SKILL.md"), _version(target / "SKILL.md")
    try:
        current = bool(old) and old >= new
    except TypeError:               # e.g. "0.2.0-alpha.1" against "0.2.0-1"
        current = False
    if (target / "SKILL.md").exists() and current:
        return f"skill già aggiornata ({'.'.join(map(str, old))})"
    if target.exists():
        keep = registry.app_data_dir() / "backup" / f"{EDITOR_SKILL} {time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(target, keep, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.rmtree(target)
    shutil.copytree(bundled, target, ignore=shutil.ignore_patterns("__pycache__"))
    return f"skill {EDITOR_SKILL} installata ({'.'.join(map(str, new)) or 'senza versione'})"


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
    out.sort(key=lambda c: c["nome"].lower())
    # clients kept in an Obsidian vault (Daniel's VIDEO EDIT BRAIN), after the profile skills
    return out + [c for c in vault.list_clients() if c["nome"] not in seen]


def get_client(client_id: str | None) -> dict | None:
    if not client_id:
        return None
    return next((c for c in list_clients() if c["id"] == client_id), None)


def client_for(settings: dict) -> dict | None:
    """The client of a project: the one chosen when it was made, else the one whose template it used."""
    return get_client(settings.get("client")) or next(
        (c for c in list_clients() if settings.get("template") and c["template"] == settings.get("template")), None)


def reference_section(paths: list[str], work: Path) -> str:
    """What Claude must do with the reference videos dropped in «Nuovo video»."""
    refs = [p for p in paths or [] if Path(p).is_file()]
    if not refs:
        return ""
    ff = vault.ffmpeg() or "ffmpeg"
    lines = ["", "## Video di riferimento: monta in questo stile",
             "Prima di decidere il montaggio studia questi video (non sono clip da usare):"]
    lines += [f"- `{p}`" for p in refs]
    lines += [
        f"Come: fotogrammi ogni 0,5 s in un mosaico (`{ff} -i <video> -vf fps=2,scale=270:-1,tile=8x4 "
        f"\"{work}/ref_<n>_%d.jpg\"`) e guardali con Read; audio con `{ff} -i <video> -ac 1 -ar 22050 \"{work}/ref_<n>.wav\"` "
        "e trova dove cadono gli effetti (picchi brevi sopra la voce, soprattutto acuti) e quanto è alta la musica rispetto alla voce.",
        "Prendi dal riferimento: ritmo dei tagli, dove e quanti suoni (ai cambi scena, sui titoletti, sulla CTA), "
        "transizioni, come entrano titoli e titoletti, lunghezza e posizione dei sottotitoli, b-roll e zoom.",
        "Restano del cliente: colori, font, preset dei sottotitoli e i suoni della libreria CapCut del template. "
        "Nel risultato scrivi in una riga cosa hai preso dal riferimento.",
    ]
    return "\n".join(lines)


def client_rules(client_id: str) -> str:
    """The profile's text, without its frontmatter: what the app shows as the client's rules."""
    c = get_client(client_id)
    if not c:
        return ""
    if c.get("source") == "vault":
        return vault.rules_text(client_id)
    text = Path(c["path"]).read_text()
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


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
Report, registro dei progetti e messaggio a Nicola li fa l'app dal risultato: non usare `registra.py`.

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
    if "verifica.py" in cmd or "livella_voce.py" in cmd:
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


def _run_claude(job, prompt: str, dirs: set[str], model: str, cwd: Path, transcript: list[str], step_of=None,
                ai_media: bool = False) -> str | None:
    """Run Claude Code on `prompt`, streaming its messages into the job's log.
    `ai_media` lets Claude use the AI services chosen in the app (b-roll, images…).
    Returns why it stopped badly, if it did."""
    extra, env, note = claude_launch()
    if note:
        job.log(f"⚠ {note}")
    ai_tools: list[str] = []
    if ai_media:
        from capwizard_app import aiservices
        ai_tools, ai_text = aiservices.for_run()
        prompt += ai_text
        if ai_tools:
            job.log("✦ Servizi AI a disposizione di Claude: "
                    + ", ".join(t.removeprefix("mcp__claude_ai_").removeprefix("mcp__") for t in ai_tools))
    cmd = [find_claude_cli(), "-p", "--output-format", "stream-json", "--verbose", *extra,
           "--model", model, "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill", *ai_tools,
           "--add-dir", *sorted(dirs)]
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, bufsize=1,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    job.proc = proc
    proc.stdin.write(prompt)
    proc.stdin.close()

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
                    job.set_step((step_of or _step_of)(tool, inp))
                    job.log(f"⚙ {_describe(tool, inp)}")
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                error = error or f"Claude si è fermato: {str(ev.get('result', ''))[:300]}"
            cost = ev.get("total_cost_usd")
            mins = (ev.get("duration_ms") or 0) / 60000
            job.log(f"⏱ {mins:.0f} min" + (f" · ${cost:.2f}" if cost else ""))
    proc.wait()
    job.proc = None
    return error


def check_request(req) -> str | None:
    """Why this reel can't start, or None."""
    if not find_claude_cli():
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
        try:
            vault.libera(tdir)
        except RuntimeError as e:
            return str(e)
    if (CAPCUT_PROJECTS_DIR / req.name).exists():
        return f"Esiste già un progetto CapCut «{req.name}»: scegli un altro nome."
    return None


def run_agent(job, req) -> tuple[str, str]:
    """Edit one reel with Claude Code. Returns (project path, report path)."""
    client = get_client(getattr(req, "client", None))
    if client and client.get("source") == "vault":
        return run_vault(job, req, client)
    problem = check_request(req)
    if problem:
        raise RuntimeError(problem)
    skill_md = editor_skill()
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
    if getattr(req, "exclude", None):
        extra.append("- Clip tolte da Nicola nell'app: NON usarle e non trascriverle: "
                     + ", ".join(str(Path(p).relative_to(req.input_path)) if Path(p).is_relative_to(req.input_path) else p for p in req.exclude))
    if getattr(req, "reference", None):
        extra.append(reference_section(req.reference, work))
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
    dirs.update(str(Path(p).parent) for p in getattr(req, "reference", None) or [] if Path(p).is_file())
    job.log(f"🎬 Avvio Claude Code ({req.ai_model}) con la skill {EDITOR_SKILL}"
            + (f" e il profilo di {client['nome']}" if client else ""))
    job.set_step(1)
    transcript = [f"# {req.name}\n"]
    error = _run_claude(job, prompt, dirs, req.ai_model or "opus", work, transcript,
                        ai_media=getattr(req, "ai_media", True))
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


# ---------------------------------------------------------------------------
# Changes to a reel already made, and the lesson they leave
# ---------------------------------------------------------------------------

MODS_DIR = registry.app_data_dir() / "modifiche"
BACKUP_DIR = registry.app_data_dir() / "backup"
ATTACH_DIR = registry.app_data_dir() / "allegati"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")

MOD_PROMPT = """Devi applicare delle MODIFICHE a un reel che hai già montato, direttamente sul progetto CapCut
esistente (in place: niente copie v2). Lavori in automatico dall'app CapWizard: NON fare domande.

Progetto: «{name}» → `{folder}`
Copia di sicurezza fatta dall'app prima di iniziare: `{backup}`
Cliente: {client}
Template: «{template}» · cartella delle clip: `{clips}`
Lavoro del montaggio (config.json, piano, script usati): {work}
Cosa avevi fatto: {summary}
Modifiche già fatte in passato su questo video:
{history}

MODIFICHE RICHIESTE ORA (da Nicola o dal cliente):
{text}
{images}
Procedura: segui la skill **{skill}** ({skill_md}), sezione «8. Dopo che Nicola l'ha aperto: correzioni».
La copia di sicurezza l'ha già fatta l'app e lo storico lo aggiorna lei: salta il passo 4 e non usare `registra.py`.
{lesson}
Alla fine scrivi il file `{result}` con: {{"riassunto": "<cosa hai cambiato, in italiano, 2-5 righe>",
   "lezione": "<la lezione per i prossimi video, una riga, o null>"}}.
Scrivi messaggi brevi in italiano su cosa stai facendo, passo per passo."""

LESSON = "La lezione va nel profilo del cliente `{profile}` (passo 8 della skill), con la data {day}."


def _mods_file(project_id: str) -> Path:
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    return MODS_DIR / f"{project_id}.json"


def list_mods(project_id: str) -> list[dict]:
    try:
        return json.loads(_mods_file(project_id).read_text())
    except (OSError, ValueError):
        return []


def _save_mods(project_id: str, mods: list[dict]) -> None:
    _mods_file(project_id).write_text(json.dumps(mods, ensure_ascii=False, indent=1))


def image_thumb(src: Path, size: int = 240) -> str:
    """A small JPEG of an image as a data URL (sips also reads HEIC)."""
    out = src.with_name(src.name + ".thumb.jpg")
    if not out.exists():
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(size), str(src), "--out", str(out)], capture_output=True)
    if not out.exists():
        return ""
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()


def add_attachment(path: str | None = None, data_url: str | None = None) -> dict | None:
    """Keep a copy of a screenshot (a file, or one pasted as a data URL) for a change request."""
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%m%d-%H%M%S")
    if data_url:
        import base64
        head, _, data = data_url.partition(",")
        dst = ATTACH_DIR / f"{stamp}_incollata.{'png' if 'png' in head else 'jpg'}"
        dst.write_bytes(base64.b64decode(data))
    elif path and Path(path).is_file() and path.lower().endswith(IMAGE_EXT):
        dst = ATTACH_DIR / f"{stamp}_{Path(path).name}"
        shutil.copy2(path, dst)
    else:
        return None
    return {"path": str(dst), "thumb": image_thumb(dst)}


def writable_profile(client: dict) -> Path:
    """The client's profile to write lessons into. A profile synced from claude.ai is copied
    into the shared brain repo (or, without it, into ~/.claude/skills): the copy then wins, so a
    sync can't drop the lessons."""
    p = Path(client["path"])
    if "synced" not in p.parts:
        return p
    from capwizard_app import brain
    shared = brain.adopt_profile(p.parent)
    if shared:
        return shared
    local = SKILLS_DIR / p.parent.name
    if not (local / "SKILL.md").exists():
        shutil.copytree(p.parent, local, dirs_exist_ok=True)
    return local / "SKILL.md"


def run_modify(job, project: dict, text: str, images: list[str], model: str = "opus") -> dict:
    """Apply a change request to a project in place, then save the lesson in the client's profile."""
    folder = Path(project.get("result_path") or "")
    if not find_claude_cli():
        raise RuntimeError("Non trovo Claude Code sul Mac.")
    if not folder.is_dir():
        raise RuntimeError("Il progetto non c'è più tra i progetti CapCut.")
    vault.libera(folder, job.log)
    settings = project.get("settings") or {}
    client = client_for(settings)
    if client and client.get("source") == "vault":
        return run_vault_modify(job, project, client, text, images, model)
    profile = writable_profile(client) if client else None
    skill_md = editor_skill()

    mods = list_mods(project["id"])
    history = "\n".join(f"- {m['data']}: {m['testo']} → {m.get('riassunto') or m['stato']}" for m in mods) or "- nessuna"
    entry = {"data": time.strftime("%Y-%m-%d %H:%M"), "testo": text, "immagini": images, "stato": "in corso", "riassunto": ""}
    mods.append(entry)
    _save_mods(project["id"], mods)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{folder.name} {time.strftime('%m%d-%H%M%S')}"
    shutil.copytree(folder, backup, ignore=shutil.ignore_patterns(".locked"))
    job.log(f"🗂 Copia di sicurezza del progetto: {backup}")

    report = Path(project.get("report_path") or "")
    work = WORK_DIR / f"{_slug(project['name'])} modifica {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)
    result = work / "risultato.json"
    old_work = report.parent if report.is_file() else None
    try:
        summary = re.search(r"## Cosa ha fatto Claude\n(.*?)(?:\n## |\Z)", report.read_text(), re.S).group(1).strip()
    except (OSError, AttributeError):
        summary = "—"
    prompt = MOD_PROMPT.format(
        name=project["name"], folder=folder, backup=backup,
        client=(f"{client['nome']} — profilo `{profile}` (leggilo: prevale sulla skill generale)" if client else "nessun profilo"),
        template=settings.get("template") or "—", clips=settings.get("input_path") or "—",
        work=f"`{old_work}`" if old_work else "non disponibile", summary=summary, history=history,
        text=text or "(vedi gli screenshot)",
        images=("Screenshot allegati (di solito la chat del cliente): guardali TUTTI con Read, contengono le modifiche:\n"
                + "\n".join(f"- {p}" for p in images)) if images else "",
        skill=EDITOR_SKILL, skill_md=skill_md,
        lesson=LESSON.format(profile=profile, day=time.strftime("%d/%m/%Y")) if profile
        else "Non c'è un profilo cliente: scrivi la lezione solo nel risultato.",
        result=result,
    )
    dirs = {str(folder), str(CAPCUT_PROJECTS_DIR), str(skill_md.parent), str(work), str(backup), str(ATTACH_DIR)}
    for d in (old_work, profile.parent if profile else None, settings.get("input_path")):
        if d:
            dirs.add(str(d))

    job.log("🎬 Avvio Claude Code sulle modifiche" + (f" · profilo di {client['nome']}" if client else ""))
    transcript: list[str] = []
    try:
        error = _run_claude(job, prompt, dirs, model, work, transcript, ai_media=True)
        if job.stopped:
            raise RuntimeError("Modifiche fermate.")
        try:
            res = json.loads(result.read_text())
        except (OSError, ValueError):
            raise RuntimeError(error or "Claude ha finito senza scrivere il risultato.")
    except Exception as e:
        entry.update(stato="errore", riassunto=str(e))
        _save_mods(project["id"], mods)
        raise
    entry.update(stato="fatto", riassunto=res.get("riassunto", ""), lezione=res.get("lezione") or "",
                 profilo=client["nome"] if client else "")
    _save_mods(project["id"], mods)
    job.log("✓ " + (res.get("riassunto") or "Modifiche fatte.").replace("\n", " "))
    if res.get("lezione"):
        job.log(f"📒 Lezione salvata{' nel profilo di ' + client['nome'] if client else ''}: {res['lezione']}")
    if not vault.capcut_running():
        vault.open_capcut()          # back on CapCut's home, the changed project in the list
    return res


# ---------------------------------------------------------------------------
# Clients from the Obsidian vault: Daniel's workflow (rules general → agency → client, base tecnica cloned,
# b-roll, video cine, music rotation, automatic cut-out, media copied for CapCut's sandbox)
# ---------------------------------------------------------------------------

VAULT_PROMPT = """Sei l'assistente di montaggio di Daniel (videomaker). Lavori in automatico dall'app CapWizard: NON fare domande,
decidi tu seguendo le regole. Qui sotto c'è il BRIEF del lavoro con le clip e TUTTE le regole dal vault Obsidian
VIDEO EDIT BRAIN (`{vault}`): regole generali → stile dell'agenzia → regole del cliente (vince la più specifica).
Il "progetto base" «{base}» è solo una base tecnica da clonare (font, preset, formati): NON copiarne contenuti o scelte.

{brief}

Procedura:
1. Trascrivi le clip (parola per parola):
   "{py}" "{tools}/trascrivi.py" "{clips}" "{work}/words.json"{escluse}
   (se c'è la cartella b-roll, guarda i fotogrammi dei b-roll con ffmpeg `{ff}` per capire cosa mostrano).
2. Costruisci il progetto CapCut clonando la base tecnica «{base}» (`{capcut}/{base}`) in `{staging}` (cartella NUOVA, copia tutta la
   cartella della base e poi riscrivi la timeline), applicando le regole. Scrivi gli script in `{work}/`. Se in
   ~/Desktop/VIDEO CLAUDE/720 STUDI0 ci sono script di video simili (work_*/), riusali e adattali.
   Tagli, titolo, sottotitoli, suoni, musica, b-roll, foto: tutto secondo le regole del cliente.
3. Controlla il risultato (JSON valido, nessun riferimento rotto, durata giusta) e in particolare:
   - tagli STRETTI: ogni clip finisce ~0,05–0,1 s dopo l'ultima parola (niente code di silenzio) e INIZIA sull'attacco reale
     della voce misurato sull'energia dell'audio (0–0,05 s prima), non sull'inizio della parola di Whisper, che dopo il "vai!" è 0,2–0,4 s in anticipo;
   - clip senza parlato vero nella cartella delle clip (trascrizione vuota, assurda o "Grazie a tutti"): spesso sono B-ROLL del posto,
     guarda i fotogrammi prima di scartarle; quando si nomina il posto mettine uno (esterno poi interno); b-roll solo pertinenti e leggibili;
   - QUANTITÀ dei suoni: quella dello stile dell'agenzia, che vince (Exodia dal 22/10: pochi e con senso — parole grandi animate
     all'inizio dell'animazione, swish su transizioni e zoom, pochi ding, niente bong né riser in mezzo);
   - ogni suono ha il colpo all'INIZIO di un sottotitolo (quando compare la parola chiave), riser che finiscono su un sottotitolo o su un taglio;
   - niente transizione subito dopo il titolo;
   - titolo dietro la testa: copia della clip su una traccia video SOPRA i testi del titolo, stesso tempo e stessa sorgente; lo scontorno
     lo fa l'app quando installa il progetto (non scrivere a Daniel di premere "Rimuovi sfondo");
   - take ripetute: tieni l'ultima take completa, mai doppioni;
   - b-roll solo quando servono, quelli 🐢 rallentati; se è un VIDEO CINE segui anche la nota "Video cine";
   - musica SEMPRE dalla libreria Musica di CapCut (mai mp3 scaricati o brani commerciali), DIVERSA dagli ultimi video (lista nel brief).
4. NON aprire né chiudere CapCut e NON installare il progetto: lo fa l'app. Alla fine scrivi il file `{result}` con:
   {{"staging": "{staging}", "nome": "{name}", "durata": <secondi>, "riassunto": "<cosa hai fatto, in italiano, 5-8 righe>",
    "da_fare_a_mano": ["<cosa resta da fare in CapCut>", ...]}}
   Se non riesci scrivi lo stesso il file con "staging": null e il motivo nel riassunto.
Scrivi messaggi brevi in italiano su cosa stai facendo, passo per passo."""

VAULT_MOD_PROMPT = """Sei l'assistente di montaggio di Daniel (videomaker). Lavori in automatico dall'app CapWizard: NON fare domande.
Devi applicare delle MODIFICHE a un video già montato, direttamente sul progetto CapCut esistente (in place: niente copie v2).

Progetto: «{name}» → `{folder}`
Copia di sicurezza fatta dall'app: `{backup}`
Cliente: {cliente} — regole nel vault `{vault}` (nota del cliente: `{note}`)
Cosa avevi fatto quando l'hai montato: {summary}
Versione generata da te (se c'è ancora): `{staging}`
Modifiche già fatte in passato su questo video:
{history}

MODIFICHE RICHIESTE ORA (da Daniel o dal cliente):
{text}
{images}

Procedura:
1. Leggi la nota del cliente, lo stile della sua agenzia e "Regole generali di montaggio" nel vault: le modifiche richieste vincono,
   tutto il resto deve restare secondo le regole.
2. Leggi lo stato ATTUALE di draft_info.json: Daniel può averlo ritoccato a mano. Confrontalo con la versione generata per capire cosa
   ha cambiato lui e NON annullare le sue modifiche.
3. Applica SOLO le modifiche richieste. Se togli o sposti un taglio risincronizza sottotitoli e suoni. Scrivi lo stesso risultato in
   draft_info.json, template-2.tmp e Timelines/<id>/(draft_info.json, template.tmp, template-2.tmp) con lo stesso id timeline, poi elimina i *.bak.
4. Verifica: JSON valido, nessun riferimento rotto, durata coerente.
5. Aggiorna SEMPRE il vault: nella nota del cliente, sezione "## Lezioni" (creala in fondo se manca), aggiungi
   "- {day}: <cosa voleva e come l'hai risolto>"; se è una preferenza che vale anche per i prossimi video scrivila anche come regola
   nella nota del cliente (o nello stile dell'agenzia in 03 Stili se vale per tutta l'agenzia) e correggi le regole vecchie che contraddice.
   Aggiungi una riga anche in "08 Feedback/Feedback e correzioni.md".
6. NON aprire né chiudere CapCut. Alla fine scrivi `{result}` con: {{"riassunto": "<cosa hai cambiato, 2-5 righe>",
   "lezione": "<la lezione per i prossimi video, una riga, o null>"}}.
Scrivi messaggi brevi in italiano su cosa stai facendo, passo per passo."""


def _vault_step(tool: str, inp: dict) -> int:
    cmd, path = str(inp.get("command", "")), str(inp.get("file_path", ""))
    if "trascrivi.py" in cmd:
        return 2
    if "staging" in cmd + path or "draft_info" in cmd + path:
        return 5
    if tool in ("Write", "Edit") and path.endswith(".py"):
        return 3
    if "verific" in cmd or "json.load" in cmd:
        return 4
    return 1


def _vault_projects_of(client_id: str) -> set[str]:
    return {p["name"] for p in registry.list_projects() if (p.get("settings") or {}).get("client") == client_id}


def run_vault(job, req, client: dict) -> tuple[str, str]:
    """A new video for a client of the vault: Claude builds it in a staging folder with Daniel's rules, the app installs it."""
    if not find_claude_cli():
        raise RuntimeError("Non trovo Claude Code sul Mac: installalo e fai il login una volta (`claude` → `/login`).")
    if not Path(req.input_path).is_dir():
        raise RuntimeError(f"La cartella delle clip non esiste: {req.input_path}")
    if getattr(req, "broll_path", None) and not Path(req.broll_path).is_dir():
        raise RuntimeError(f"La cartella dei b-roll non esiste: {req.broll_path}")
    base = req.template or client["template"]
    if not base or not (CAPCUT_PROJECTS_DIR / base / "draft_info.json").exists():
        raise RuntimeError(f"Manca la base tecnica CapCut di {client['nome']}: scrivila nella sua nota (✏️ Modifica cliente).")
    if (CAPCUT_PROJECTS_DIR / req.name).exists():
        raise RuntimeError(f"Esiste già un progetto CapCut «{req.name}»: scegli un altro nome.")
    work = WORK_DIR / f"{_slug(req.name)} {time.strftime('%m%d-%H%M')}"
    staging = work / "staging" / req.name
    work.mkdir(parents=True, exist_ok=True)
    result = work / "risultato.json"
    job_data = {"client": client["id"], "name": req.name, "input_path": req.input_path, "broll_path": getattr(req, "broll_path", None),
                "cine": bool(getattr(req, "cine", False)), "musica": True, "notes": req.notes, "stile": client.get("stile"),
                "exclude": list(getattr(req, "exclude", None) or [])}
    escluse = ""
    if job_data["exclude"]:      # clips taken out in the app: not even transcribed
        (work / "escluse.txt").write_text("\n".join(job_data["exclude"]) + "\n")
        escluse = f' "{work}/escluse.txt"'
    job.log("📒 Leggo le regole dal vault (generali → agenzia → cliente) e preparo il brief…")
    brief = vault.brief(job_data, _vault_projects_of(client["id"]))
    (work / "brief.md").write_text(brief)
    if req.intro_title:
        brief += f"\n\nTitolo iniziale voluto da Daniel: «{req.intro_title}»"
    if req.ai_target_duration:
        brief += f"\nDurata voluta: {req.ai_target_duration:g} s"
    brief += reference_section(getattr(req, "reference", None) or [], work)
    prompt = VAULT_PROMPT.format(vault=vault.VAULT, base=base, brief=brief, py=vault.tools_python(), tools=vault.TOOLS,
                                 clips=req.input_path, work=work, ff=vault.ffmpeg() or "ffmpeg", capcut=CAPCUT_PROJECTS_DIR,
                                 staging=staging, result=result, name=req.name, escluse=escluse)
    dirs = {str(Path(req.input_path)), str(CAPCUT_PROJECTS_DIR), str(work), str(vault.VAULT), str(vault.TOOLS)}
    if getattr(req, "broll_path", None):
        dirs.add(str(Path(req.broll_path)))
    dirs.update(str(Path(p).parent) for p in getattr(req, "reference", None) or [] if Path(p).is_file())
    if getattr(req, "reference", None):
        job.log(f"🎯 Video di riferimento: {', '.join(Path(p).name for p in req.reference)}")
    old_scripts = Path.home() / "Desktop/VIDEO CLAUDE/720 STUDI0"
    if old_scripts.is_dir():
        dirs.add(str(old_scripts))
    job.log(f"🎬 Avvio Claude Code ({req.ai_model}) con le regole di {client['nome']} ({client['agenzia']})"
            + (" · 🎬 video cine" if job_data["cine"] else "") + (" · con b-roll" if job_data["broll_path"] else ""))
    job.set_step(1)
    transcript = [f"# {req.name}\n"]
    error = _run_claude(job, prompt, dirs, req.ai_model or "opus", work, transcript, step_of=_vault_step,
                        ai_media=getattr(req, "ai_media", True))
    if job.stopped:
        raise RuntimeError("Montaggio fermato.")
    try:
        res = json.loads(result.read_text())
    except (OSError, ValueError):
        raise RuntimeError(error or "Claude ha finito senza scrivere il risultato.")
    if not res.get("staging") or not Path(res["staging"]).is_dir():
        raise RuntimeError(res.get("riassunto") or error or "Progetto non creato.")
    job.set_step(6)
    msg = vault.install(res["staging"], req.name, job.log)
    job.log(msg)
    project = str(CAPCUT_PROJECTS_DIR / req.name)
    todo = [t for t in res.get("da_fare_a_mano") or [] if t]
    report = work / "report.md"
    report.write_text(
        f"# {req.name}\n\nCliente: {client['nome']} ({client['agenzia']}) · base tecnica «{base}» · durata {res.get('durata', '?')} s\n\n"
        f"## Cosa ha fatto Claude\n{res.get('riassunto', '')}\n\n## DA FARE A MANO IN CAPCUT\n"
        + ("\n".join(f"- {t}" for t in todo) or "- niente") + "\n\n## Messaggi di Claude\n\n" + "\n\n".join(transcript[1:]) + "\n")
    job.log("✓ " + res.get("riassunto", "Progetto creato.").replace("\n", " "))
    for t in todo:
        job.log(f"☐ {t}")
    try:
        settings = req.model_dump()
        settings.update(template=base, staging=res["staging"])
        registry.add_project(name=req.name, result_path=project, settings=settings, report_path=str(report))
    except Exception as reg_err:  # noqa: BLE001 — registry is best-effort
        job.log(f"⚠ impossibile salvare nel registro progetti: {reg_err}")
    try:
        vault.video_note(req.name, client["id"], client.get("stile", ""), res.get("riassunto", ""))
    except Exception as e:  # noqa: BLE001
        job.log(f"⚠ nota Obsidian non creata: {e}")
    if not vault.capcut_running():
        vault.open_capcut()
        job.log(f"🚀 CapCut aperto: «{req.name}» è il primo in alto nella lista.")
    return project, str(report)


def run_vault_modify(job, project: dict, client: dict, text: str, images: list[str], model: str = "opus") -> dict:
    folder = Path(project["result_path"])
    settings = project.get("settings") or {}
    mods = list_mods(project["id"])
    history = "\n".join(f"- {m['data']}: {m['testo']} → {m.get('riassunto') or m['stato']}" for m in mods) or "- nessuna"
    entry = {"data": time.strftime("%Y-%m-%d %H:%M"), "testo": text, "immagini": images, "stato": "in corso", "riassunto": ""}
    mods.append(entry)
    _save_mods(project["id"], mods)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{folder.name} {time.strftime('%m%d-%H%M%S')}"
    shutil.copytree(folder, backup, ignore=shutil.ignore_patterns(".locked"))
    job.log(f"🗂 Copia di sicurezza del progetto: {backup}")
    work = WORK_DIR / f"{_slug(project['name'])} modifica {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)
    result = work / "risultato.json"
    report = Path(project.get("report_path") or "")
    try:
        summary = re.search(r"## Cosa ha fatto Claude\n(.*?)(?:\n## |\Z)", report.read_text(), re.S).group(1).strip()
    except (OSError, AttributeError):
        summary = "—"
    prompt = VAULT_MOD_PROMPT.format(
        name=project["name"], folder=folder, backup=backup, cliente=f"{client['nome']} ({client['agenzia']})", vault=vault.VAULT,
        note=client["path"], summary=summary, staging=settings.get("staging") or "non disponibile", history=history,
        text=text or "(vedi gli screenshot)", day=time.strftime("%d/%m"), result=result,
        images=("Screenshot allegati (di solito la chat del cliente): guardali TUTTI con Read, contengono le modifiche:\n"
                + "\n".join(f"- {p}" for p in images)) if images else "")
    dirs = {str(folder), str(CAPCUT_PROJECTS_DIR), str(work), str(backup), str(ATTACH_DIR), str(vault.VAULT)}
    if settings.get("staging"):
        dirs.add(str(Path(settings["staging"]).parent))
    for d in (settings.get("input_path"), settings.get("broll_path")):
        if d:
            dirs.add(str(d))
    job.log(f"🎬 Avvio Claude Code sulle modifiche · regole di {client['nome']} dal vault")
    transcript: list[str] = []
    try:
        error = _run_claude(job, prompt, dirs, model, work, transcript, ai_media=True)
        if job.stopped:
            raise RuntimeError("Modifiche fermate.")
        try:
            res = json.loads(result.read_text())
        except (OSError, ValueError):
            raise RuntimeError(error or "Claude ha finito senza scrivere il risultato.")
    except Exception as e:
        entry.update(stato="errore", riassunto=str(e))
        _save_mods(project["id"], mods)
        raise
    entry.update(stato="fatto", riassunto=res.get("riassunto", ""), lezione=res.get("lezione") or "", profilo=client["nome"])
    _save_mods(project["id"], mods)
    job.log(vault.porta_media(str(folder), project["name"]))
    try:
        vault.video_note(project["name"], client["id"], client.get("stile", ""), line=
                         f"- **{time.strftime('%d/%m')} · modifica:** {text or '(da screenshot)'}"
                         + (f" _({len(images)} screenshot)_" if images else "") + f"\n  → {res.get('riassunto', '').strip()}\n")
        job.log("📒 Obsidian aggiornato (storico del video + lezioni del cliente).")
    except Exception as e:  # noqa: BLE001
        job.log(f"⚠ storico Obsidian non aggiornato: {e}")
    job.log("✓ " + (res.get("riassunto") or "Modifiche fatte.").replace("\n", " "))
    if not vault.capcut_running():
        vault.open_capcut()
    return res


STUDY_PROMPT = """Sei l'assistente di montaggio di Daniel (videomaker). Lavori in automatico dall'app CapWizard: NON fare domande.
Daniel ha {cosa}. Devi STUDIARE i materiali e salvare tutto nel vault Obsidian VIDEO EDIT BRAIN (`{vault}`),
così i prossimi video per loro escono già nel loro stile.

Dati (JSON):
{dati}

Procedura:
1. Se ci sono video o link lancia
   "{py}" "{studia}" "{out}" '<JSON con "file": [...] e "link": [...]>'
   (scarica i link Instagram/TikTok con yt-dlp, trascrive, trova i tagli, crea i fogli provini). Poi leggi {out}/analisi.json e GUARDA con
   Read TUTTI i fogli provini e i fotogrammi grandi. Se un link non si scarica (Instagram a volte chiede il login) scrivilo nel riassunto e
   suggerisci a Daniel di trascinare i video nell'app.
2. Guarda con Read anche gli screenshot allegati (di solito la chat dell'agenzia con i dettagli del cliente) e leggi il messaggio incollato.
3. Ricava lo stile in modo concreto e misurabile, come nelle note che già esistono (leggi prima "03 Stili/Stile Exodia.md",
   una nota cliente in "02 Clienti/EXODIA/" e "01 Agenzie/EXODIA.md" come modello di formato):
   - titolo/gancio: c'è? dove (dietro la testa?), quanto dura, font (aspetto e quale font CapCut gli somiglia), colori (hex stimati), grandezza,
     animazione, emoji;
   - sottotitoli: posizione, font, colori, parole chiave colorate/più grandi, quante parole per volta, maiuscolo, animazioni;
   - ritmo: secondi per taglio, tagli stretti o no, zoom/punch-in, b-roll, transizioni;
   - audio: musica (genere, volume), suoni (quanti e dove), voce;
   - durata tipica, formato, chiusura/CTA.
   Distingui ciò che è SICURO da ciò che è stimato ("da verificare").
4. Scrivi/aggiorna le note (con frontmatter `cssclasses: [...]` come le altre, link [[ ]] tra loro):
   {note}
   Nella nota del cliente metti sempre: "Cliente di [[AGENZIA]]", "- **Base tecnica CapCut:** <progetto CapCut esistente più simile per
   struttura, tra quelli in `{capcut}`>", la sezione "## Scritte" (Font, Grandezza, "- **Colori:** #HEX …", Ombre/bordi) e
   "Stile generale: [[Stile <Agenzia>]]". Se l'agenzia c'è già NON riscrivere le sue regole: aggiungi solo ciò che è nuovo.
5. Aggiungi una riga in "08 Feedback/Feedback e correzioni.md" con la data di oggi: cosa hai imparato e da quali materiali.
6. Alla fine scrivi il file `{result}` con: {{"riassunto": "<cosa hai imparato e quali note hai creato, in italiano, 5-10 righe>"}}.
Scrivi messaggi brevi in italiano su cosa stai facendo, passo per passo."""


def run_study(job, d: dict) -> dict:
    """A new agency and/or client: Claude studies reference videos, links, the agency's message and chat screenshots,
    then writes agency, style and client notes in the vault."""
    if not find_claude_cli():
        raise RuntimeError("Non trovo Claude Code sul Mac.")
    if not vault.enabled():
        raise RuntimeError("Nessun vault Obsidian trovato.")
    ag = (d.get("agenzia") or "").strip().upper()
    cl = re.sub(r"[/:]", "-", (d.get("cliente") or "").strip().upper())
    if not ag:
        raise RuntimeError("Scegli o scrivi l'agenzia.")
    links = [l.strip() for l in (d.get("link") or "").splitlines() if l.strip()]
    if not (d.get("video") or links or (d.get("messaggio") or "").strip() or d.get("screenshot")):
        raise RuntimeError("Metti almeno dei video, un link o il messaggio dell'agenzia.")
    work = WORK_DIR / f"studio {_slug(ag + ' ' + cl)} {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)
    shots = []
    for i, p in enumerate(d.get("screenshot") or []):
        if Path(p).is_file():
            dst = work / f"screenshot_{i + 1}_{Path(p).name}"
            shutil.copy2(p, dst)
            shots.append(str(dst))
    nuova = not (vault.VAULT / "01 Agenzie" / f"{ag}.md").exists()
    dati = {"agenzia": ag, "agenzia_nuova": nuova, "cliente": cl or "(nessuno: solo l'agenzia)", "colore_cliente": d.get("colore") or "",
            "font_cliente": d.get("font") or "", "video_di_riferimento": d.get("video") or [], "link": links,
            "messaggio_agenzia": (d.get("messaggio") or "").strip(), "screenshot": shots, "note_di_daniel": (d.get("note") or "").strip()}
    (work / "dati.json").write_text(json.dumps(dati, ensure_ascii=False, indent=1))
    note = []
    if nuova:
        note += [f'"01 Agenzie/{ag}.md" (cssclasses: [agenzia]) — chi sono, come lavorano, link, cartelle',
                 f'"03 Stili/Stile {ag.title()}.md" (cssclasses: [stile]) — lo stile completo dei loro video, regola per regola']
    note.append(f'"02 Clienti/{ag}/{cl}.md" (cssclasses: [cliente]) — font, colori, dettagli del cliente' if cl
                else "(nessun cliente nuovo: aggiorna solo agenzia e stile)")
    cosa = (f"una NUOVA agenzia, {ag}" if nuova else f"l'agenzia {ag}") + (f", con il nuovo cliente {cl}" if cl else "")
    result = work / "risultato.json"
    prompt = STUDY_PROMPT.format(cosa=cosa, vault=vault.VAULT, dati=json.dumps(dati, ensure_ascii=False, indent=1), py=vault.tools_python(),
                                 studia=vault.TOOLS / "studia_media.py", out=work / "analisi", capcut=CAPCUT_PROJECTS_DIR,
                                 note="\n   ".join("- " + n for n in note), result=result)
    dirs = {str(vault.VAULT), str(work), str(vault.TOOLS), str(CAPCUT_PROJECTS_DIR)}
    for v in d.get("video") or []:
        dirs.add(str(Path(v) if Path(v).is_dir() else Path(v).parent))
    job.log(f"✦ Studio: {cosa} · {len(d.get('video') or [])} video · {len(links)} link · {len(shots)} screenshot")
    transcript: list[str] = []
    error = _run_claude(job, prompt, dirs, d.get("ai_model") or "opus", work, transcript,
                        step_of=lambda t, i: 2 if "studia_media" in str(i.get("command", "")) else 4 if t in ("Write", "Edit") else 1)
    if job.stopped:
        raise RuntimeError("Studio fermato.")
    try:
        res = json.loads(result.read_text())
    except (OSError, ValueError):
        raise RuntimeError(error or "Claude ha finito senza scrivere il risultato.")
    job.log("✅ Studiato e salvato su Obsidian: " + res.get("riassunto", "").replace("\n", " "))
    return res


def stop(job) -> None:
    proc = getattr(job, "proc", None)
    if proc and proc.poll() is None:
        job.stopped = True
        proc.terminate()
        threading.Timer(5, lambda: proc.poll() is None and proc.kill()).start()
