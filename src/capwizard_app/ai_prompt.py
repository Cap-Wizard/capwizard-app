"""Claude between the user and the AI models: it writes the prompts and learns from the results.

Before a generation Claude reads the `ai-prompt` skill, looks at the frames, video or references
and turns the user's few words into the prompt for the chosen model; the user sees it and can
change it before paying. After a result (thumb up or down, a «Modifica») Claude writes what it
learned into the skill. The skill lives in the shared brain repo (`skills/ai-prompt/`), pulled
before each prompt and pushed after each lesson, so everyone on the team writes better prompts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from capwizard_app import brain, comfy_ai, vault
from capwizard_app.capcut import claude_launch, find_claude_cli

SEED = Path(__file__).resolve().parent / "tools" / "ai-prompt"
NAME = "ai-prompt"
MODEL = "opus"


class PromptError(Exception):
    pass


def skill_dir() -> Path:
    """The skill's folder: in the shared brain when there is one (linked into ~/.claude/skills),
    otherwise in ~/.claude/skills only. Made from the copy that ships with the app if missing."""
    local = brain.SKILLS_DIR / NAME
    if brain.available():
        shared = brain.BRAIN / "skills" / NAME
        if not (shared / "SKILL.md").exists():
            shutil.copytree(SEED, shared, dirs_exist_ok=True)
            brain.push_soon("Skill dei prompt AI creata")
        if not local.exists() and not local.is_symlink():
            local.parent.mkdir(parents=True, exist_ok=True)
            local.symlink_to(shared, target_is_directory=True)
        return shared
    if not (local / "SKILL.md").exists():
        shutil.copytree(SEED, local, dirs_exist_ok=True)
    return local


def status() -> dict:
    d = skill_dir()
    return {"cartella": str(d), "condivisa": brain.available() and brain.BRAIN in d.parents,
            "lezioni": sorted(p.stem for p in (d / "modelli").glob("*.md"))}


# ---------------------------------------------------------------------------
# What Claude looks at
# ---------------------------------------------------------------------------

VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _look(src: Path, out: Path, n: int = 3) -> list[Path]:
    """Small JPEGs Claude can read: the picture itself, or `n` frames spread over a video."""
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{len(list(out.iterdir())):02d}_{re.sub(r'[^\w-]', '_', src.stem)[:30]}"
    if src.suffix.lower() in VIDEO:
        secs = max(0.5, comfy_ai._seconds(src))
        frames = []
        for k in range(n):
            f = out / f"{stem}_{k + 1}.jpg"
            subprocess.run([vault.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-ss", f"{secs * (k + 0.5) / n:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", str(f)], capture_output=True, timeout=60)
            if f.exists():
                frames.append(f)
        return frames
    f = out / f"{stem}.jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1024", str(src), "--out", str(f)], capture_output=True, timeout=60)
    return [f] if f.exists() else []


def _claude(prompt: str, dirs: list[Path], edit: bool = False, timeout: int = 300) -> str:
    cli = find_claude_cli()
    if not cli:
        raise PromptError("Non trovo Claude Code sul Mac: installalo e fai il login una volta (`claude` → `/login`).")
    extra, env, _ = claude_launch()
    tools = ["Read", "Edit", "Write"] if edit else ["Read"]
    cmd = [cli, "-p", "--output-format", "json", *extra, "--model", MODEL, "--allowedTools", *tools,
           *(["--permission-mode", "acceptEdits"] if edit else []), "--add-dir", *map(str, dirs)]
    try:
        r = subprocess.run(cmd, input=prompt, cwd=dirs[0], env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PromptError("Claude ci ha messo troppo.") from None
    try:
        res = json.loads(r.stdout)
    except ValueError:
        low = (r.stdout + r.stderr).lower()
        raise PromptError("Claude non è collegato: apri il Terminale, scrivi `claude`, poi `/login`."
                          if "login" in low else f"Claude non ha risposto: {(r.stderr or r.stdout).strip()[:200]}") from None
    if res.get("is_error"):
        raise PromptError(f"Claude si è fermato: {str(res.get('result', ''))[:200]}")
    return res.get("result") or ""


def _lessons(d: Path, model: str) -> str:
    f = d / "modelli" / f"{model}.md"
    return f.read_text() if f.exists() else "(ancora nessuna lezione per questo modello)"


# ---------------------------------------------------------------------------
# The prompt, before paying
# ---------------------------------------------------------------------------

def _task(kind: str, d: dict, look) -> tuple[str, str, str]:
    """What to ask Claude for this kind of job: (model id, the job described, the JSON to answer with)."""
    fmt = d.get("formato", "9:16")
    if kind == "trans":
        m = comfy_ai._model(d["modello"])
        t = comfy_ai._template("transizioni", d.get("template", "automatico"))
        frames = [look(Path(f), 1) for f in d["frames"]]
        notes = d.get("note") or []
        legs = []
        for i in range(len(frames) - 1):
            a, b = chr(65 + i), chr(66 + i)
            legs.append(f"- Tratta {a} → {b}, {(d.get('durate') or [5] * 99)[i]} s: frame {a} = {frames[i][0]}, frame {b} = {frames[i + 1][0]}"
                        + (f"; richiesta per questa tratta: «{notes[i]}»" if i < len(notes) and notes[i].strip() else ""))
        job = (f"Transizione A, B to Z con {m['nome']} (primo e ultimo frame, formato {fmt}, {d.get('risoluzione', '')}).\n"
               f"Preset scelto: {t['nome']}. Il suo prompt di base è: «{t['prompt']}»\n" + "\n".join(legs))
        return m["id"], job, '{"tratte": ["prompt della tratta 1", "prompt della tratta 2", ...]}  (una voce per tratta, in ordine)'
    if kind == "img":
        m = comfy_ai._pick("immagini", d["modello"])
        parts = []
        if d.get("main"):
            parts.append(f"- foto di partenza (da modificare): {look(Path(d['main']), 1)[0]}")
        for i, r in enumerate(d.get("refs") or []):
            parts.append(f"- reference {i + 1}: {look(Path(r), 1)[0]}")
        job = (f"Immagine da prompt con {m['nome']}, formato {fmt}.\nRichiesta dell'utente: «{d.get('testo', '').strip() or '(nessuna)'}»\n"
               + ("Immagini, nell'ordine in cui le riceve il modello:\n" + "\n".join(parts) if parts else "Nessuna immagine: solo il prompt."))
        return m["id"], job, '{"prompt": "..."}'
    if kind == "amb":
        m = comfy_ai._pick("ambientazione_video", d["modello"])
        person = look(Path(d["soggetto"]), 3)
        places = [look(Path(c), 1)[0] for c in (d.get("contesti") or [])]
        spunto = d.get("riferimento") == "spunto"
        job = (f"Ambientazione personaggio con {m['nome']}, formato {fmt}. Il video della persona (fotogrammi): {', '.join(map(str, person))}.\n"
               + (f"«Solo spunto»: prima Nano Banana Pro crea un posto vuoto ispirato a queste immagini: {', '.join(map(str, places))}; "
                  "poi il modello mette la persona in quel posto.\n" if spunto else
                  f"«Sfondo esatto»: lo sfondo è questa immagine così com'è: {places[0] if places else '(manca)'}.\n")
               + f"Richiesta dell'utente: «{d.get('testo', '').strip() or '(nessuna)'}»"
               + ("\nQuesto modello non usa il prompt della scena: scrivi \"prompt\": \"\"." if m["nodo"] == "BriaVideoReplaceBackground" else ""))
        shape = '{"prompt": "prompt per il modello della scena", "posto": "prompt per creare il posto vuoto"}' if spunto else '{"prompt": "..."}'
        return m["id"], job, shape
    if kind == "modifica":
        m = comfy_ai._edit_model(d["modello"])
        folder = comfy_ai.folder_of(d["id"])
        src = comfy_ai.inside(str(folder / d["source"]))
        job = (f"Modifica di un video con {m['nome']}. Il video di partenza (fotogrammi): {', '.join(map(str, look(src, 3)))}.\n"
               f"Cosa vuole cambiare l'utente: «{d.get('testo', '').strip()}»")
        return m["id"], job, '{"prompt": "..."}'
    raise PromptError("Tipo di lavoro sconosciuto.")


def write(kind: str, d: dict) -> dict:
    """Claude's prompt(s) for a job, from the skill and what it sees. Nothing is generated or paid."""
    brain.pull()
    sd = skill_dir()
    with tempfile.TemporaryDirectory(prefix="capwizard-prompt-") as tmp:
        tmp = Path(tmp)
        look = lambda p, n: _look(p, tmp, n) or [p]        # noqa: E731 — a picture it can't convert is read as it is
        model, job, shape = _task(kind, d, look)
        ask = (f"Sei il regista che scrive i prompt per i modelli AI di CapWizard. Leggi prima la skill: {sd / 'SKILL.md'}\n"
               f"Lezioni per questo modello ({model}):\n{_lessons(sd, model)}\n\n"
               f"Il lavoro:\n{job}\n\nGuarda le immagini con Read, poi scrivi il prompt seguendo la skill e le lezioni. "
               f"Rispondi SOLO con questo JSON, senza altro testo:\n{shape}")
        text = _claude(ask, [tmp, sd])
    found = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(found.group(0)) if found else None
    except ValueError:
        out = None
    if not isinstance(out, dict):
        raise PromptError("Claude non ha scritto il prompt nel formato giusto.")
    if kind == "trans":
        legs = len(d["frames"]) - 1
        out["tratte"] = [str(x).strip() for x in (out.get("tratte") or [])][:legs]
        if len(out["tratte"]) < legs:
            raise PromptError("Claude non ha scritto un prompt per ogni tratta.")
    return {k: v for k, v in out.items() if k in ("prompt", "posto", "tratte")}


# ---------------------------------------------------------------------------
# The lesson, after a result
# ---------------------------------------------------------------------------

def _used(meta: dict) -> str:
    """The prompts the result was made with."""
    cl = meta.get("claude") or {}
    if meta.get("tipo") == "transizione":
        legs = cl.get("tratte") or [meta.get("prompt", "") + (f" {t['nota']}" if t.get("nota") else "") for t in meta["tratte"]]
        return "\n".join(f"- tratta {i + 1}: {p}" for i, p in enumerate(legs))
    return "\n".join(f"- {k}: {v}" for k, v in (("prompt", cl.get("prompt") or meta.get("testo", "")), ("posto", cl.get("posto"))) if v)


def learn(job_id: str, voto: str, nota: str = "", log=print) -> None:
    """In the background: Claude looks at the result and the feedback and writes the lesson into the skill."""
    folder = comfy_ai.folder_of(job_id)
    meta = comfy_ai._meta(folder)
    meta.setdefault("voti", []).append({"voto": voto, "nota": nota})
    comfy_ai._save(folder, meta)

    def run() -> None:
        try:
            brain.pull()
            sd = skill_dir()
            (sd / "modelli").mkdir(exist_ok=True)
            model = meta.get("modello", "")
            result = meta.get("video") or meta.get("immagine")
            with tempfile.TemporaryDirectory(prefix="capwizard-lesson-") as tmp:
                frames = _look(folder / result, Path(tmp), 4) if result and (folder / result).exists() else []
                what = {"su": "Il risultato è piaciuto.", "giu": "Il risultato NON è piaciuto.",
                        "modifica": "Il risultato andava cambiato: l'utente ha chiesto una «Modifica»."}.get(voto, voto)
                ask = (f"Sei il regista che scrive i prompt per i modelli AI di CapWizard e impari dai risultati.\n"
                       f"La skill è {sd / 'SKILL.md'}; le lezioni di ogni modello sono in {sd / 'modelli'}/<modello>.md.\n\n"
                       f"Lavoro: {meta.get('tipo')} con {meta.get('modello_nome', model)} (id {model}), formato {meta.get('formato', '')}.\n"
                       f"Richiesta dell'utente: «{meta.get('testo') or ''}»\nPrompt usati:\n{_used(meta)}\n"
                       f"Fotogrammi del risultato: {', '.join(map(str, frames)) or '(non disponibili)'}\n\n"
                       f"{what}" + (f" Le sue parole: «{nota}»" if nota.strip() else "") + "\n\n"
                       f"Guarda i fotogrammi. Se ne esce una lezione utile per i prossimi prompt, scrivila in {sd / 'modelli' / (model + '.md')} "
                       "(crealo se manca, con un titolo «# Lezioni per <nome del modello>»), oppure in «Lezioni generali» di SKILL.md "
                       "se vale per tutti i modelli. Regole: in italiano, una riga per lezione, concreta e generale (non legata a questo "
                       "video), con cosa scrivere o evitare nel prompt. Se c'è già una lezione simile, correggi quella invece di aggiungerne "
                       "un'altra; se una lezione è smentita, toglila. Massimo 25 lezioni per file. Non toccare altri file. "
                       "Se non c'è niente da imparare, non scrivere niente. Alla fine rispondi con una riga: la lezione scritta o «niente».")
                said = _claude(ask, [Path(tmp), sd], edit=True, timeout=400).strip()
            log(f"[ai-prompt] {said[:300]}")
            brain.push_soon(f"Lezione AI su {meta.get('modello_nome', model)}", log)
        except Exception as e:  # noqa: BLE001 — a lesson that fails never stops the app
            log(f"[ai-prompt] lezione non salvata: {e}")
    threading.Thread(target=run, daemon=True).start()
