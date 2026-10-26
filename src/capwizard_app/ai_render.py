"""AI nel video: animated renders cut into a reel, like the Feeling Home «Torre de Roveri» reference.

1. propose: Claude looks at the finished CapCut project (what is said and when) and at the folder of
   render photos, and proposes where an animated render fits (start/end, phrase, render, camera move).
2. Daniel ticks the points he wants in the app.
3. generate, through the Higgsfield API (pay-per-use, key HF_KEY in a local env file, never printed):
   render -> vertical 9:16 photo (Qwen Image 3 Edit) -> 5 s video (Kling 3.0 Pro image-to-video, no sound)
   -> muted overlay track in the CapCut project, hard cut in and out, the voice keeps running below.
"""
from __future__ import annotations

import copy
import glob
import json
import os
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from capwizard_app import registry, vault
from capwizard_app.agent import WORK_DIR, _run_claude, _slug

IMG_MODEL = "alibaba/qwen-image-3/edit"
VID_MODEL = "kling-video/v3.0/pro/image-to-video"
VID_PRICE_S = 0.112          # $/s, Kling 3.0 Pro image-to-video list price (open.higgsfield.ai/pricing, 26/10/2026)
VID_SECONDS = 5
SKIP = 0.8                    # the clip starts a bit into the camera move
US = 1_000_000
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# where the key may live: the app's data folder first, then the setup Daniel made with Claude
KEY_FILES = [os.environ.get("CAPWIZARD_HF_ENV", ""), str(registry.app_data_dir() / "higgsfield.env"),
             str(Path.home() / "Desktop/VIDEO CLAUDE/720 STUDI0/higgsfield/.env.local")]


def load_key() -> bool:
    """Put HF_KEY in the environment for the SDK. The value is never logged or returned."""
    if os.environ.get("HF_KEY"):
        return True
    from dotenv import load_dotenv
    for f in KEY_FILES:
        if f and Path(f).is_file():
            load_dotenv(f, override=False)
            if os.environ.get("HF_KEY"):
                return True
    return False


def renders_in(folder: str) -> list[Path]:
    p = Path(folder or "")
    if not p.is_dir():
        return []
    return sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT and not f.name.startswith("."))


def cost(n: int) -> dict:
    video = round(n * VID_SECONDS * VID_PRICE_S, 2)
    return {"video": video, "testo": f"{n} video × {VID_SECONDS} s Kling 3.0 Pro ≈ ${video:.2f} + {n} foto Qwen Image 3"}


# ---------------------------------------------------------------------------
# 1. Proposal
# ---------------------------------------------------------------------------

def timeline(folder: Path) -> dict:
    """What Claude needs to place the renders: video cuts (source clip and times) and the on-screen captions."""
    d = json.load(open(folder / "draft_info.json"))
    m = d["materials"]
    byid = {x["id"]: x for v in m.values() if isinstance(v, list) for x in v if isinstance(x, dict) and "id" in x}
    main = next(t for t in d["tracks"] if t["type"] == "video")
    cuts = [{"inizio": round(s["target_timerange"]["start"] / US, 2),
             "fine": round((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US, 2),
             "clip": byid[s["material_id"]].get("path", ""),
             "da": round((s.get("source_timerange") or {}).get("start", 0) / US, 2)} for s in main["segments"]]
    texts = []
    for t in d["tracks"]:
        if t["type"] != "text":
            continue
        for s in t["segments"]:
            mat = byid.get(s["material_id"], {})
            if mat.get("type") == "text_template_subtitle":
                mat = byid.get(mat["text_info_resources"][0]["text_material_id"], {})
            try:
                txt = json.loads(mat.get("content", "{}")).get("text", "")
            except ValueError:
                txt = ""
            if txt.strip():
                texts.append({"inizio": round(s["target_timerange"]["start"] / US, 2),
                              "fine": round((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US, 2),
                              "testo": txt})
    return {"durata": round(d["duration"] / US, 2), "tagli": cuts, "scritte": sorted(texts, key=lambda x: x["inizio"])}


PROPOSE_PROMPT = """Sei l'assistente di montaggio di Daniel. Lavori in automatico dall'app CapWizard: NON fare domande e NON modificare niente.

Devi proporre dove inserire dei RENDER ANIMATI nel reel CapCut «{name}» (`{folder}`), come nel video di riferimento
«SOLUZIONI 2 - TORRE DE ROVERI»: quando chi parla nomina un posto o un ambiente (il palazzo, la zona, il soggiorno, la cucina,
la camera, il giardino, i terrazzi…), per 2,5–3 s si vede il render di quel posto, animato con un movimento di camera lento,
a TAGLIO NETTO in entrata e in uscita; la voce continua sotto.

Regole:
- Pochi: circa 1 ogni 10–12 s di video (3 in un video da 35 s, 4 in 72 s). Mai sul titolo/gancio iniziale né sulla CTA finale.
- Solo dove c'è un render VERO di quel posto nella cartella: non inventare ambienti (se dice "piscina" e non c'è un render della piscina, niente).
- Inizio e fine sui confini delle frasi o dei tagli, durata 2,4–3,0 s. Non sovrapporre due inserti.
- Ogni render al massimo una volta. Preferisci il render più bello e leggibile per quell'ambiente.
{regole_cliente}

Cosa si dice e quando (tagli del video e scritte a schermo, in secondi sulla timeline):
{timeline}

Se le scritte non bastano per capire cosa si dice, trascrivi le clip con
`{py} {trascrivi} <cartella delle clip> {work}/parole.json` (parole con i tempi della clip: usa "da" dei tagli per riportarli sulla timeline).

Render disponibili (guardali TUTTI con Read prima di scegliere):
{renders}

Alla fine scrivi `{result}` con questo JSON:
{{"punti": [{{"inizio": <s>, "fine": <s>, "frase": "<cosa dice in quel momento>", "render": "<percorso del render>",
             "ambiente": "<es. palazzine, soggiorno>", "perche": "<1 riga>",
             "prompt": "<prompt INGLESE per Kling: movimento di camera lento e stabile adatto all'ambiente + 'no morphing of the architecture, no people'>"}}],
 "note": "<2-4 righe in italiano per Daniel: cosa hai scelto e cosa manca (render che servirebbero)>"}}
Scrivi messaggi brevi in italiano su cosa stai facendo."""


def client_rules(client_id: str | None) -> str:
    if not client_id or not client_id.startswith(vault.PREFIX):
        return ""
    txt = vault.read_note(vault.split_id(client_id)[1]) or ""
    i = txt.find("Video IA dai render")
    return ("- Regole del cliente:\n" + txt[i:i + 2500]) if i >= 0 else ""


def propose(job, project: dict, renders_dir: str, model: str = "opus") -> dict:
    folder = Path(project.get("result_path") or "")
    if not (folder / "draft_info.json").exists():
        raise RuntimeError("Il progetto non c'è più tra i progetti CapCut.")
    renders = renders_in(renders_dir)
    if not renders:
        raise RuntimeError("Nella cartella dei render non ci sono foto (jpg, png, webp).")
    work = WORK_DIR / f"{_slug(project['name'])} AI {time.strftime('%m%d-%H%M')}"
    work.mkdir(parents=True, exist_ok=True)
    result = work / "punti.json"
    prompt = PROPOSE_PROMPT.format(
        name=project["name"], folder=folder, timeline=json.dumps(timeline(folder), ensure_ascii=False, indent=1),
        regole_cliente=client_rules((project.get("settings") or {}).get("client")), py=vault.tools_python(),
        trascrivi=vault.TOOLS / "trascrivi.py", work=work, renders="\n".join(f"- {r}" for r in renders), result=result)
    dirs = {str(folder), str(work), str(Path(renders_dir)), str(vault.TOOLS)}
    dirs |= {str(Path(c["clip"]).parent) for c in timeline(folder)["tagli"] if c["clip"]}
    job.log(f"🏗️ Analizzo «{project['name']}» e {len(renders)} render…")
    error = _run_claude(job, prompt, dirs, model, work, [])
    if job.stopped:
        raise RuntimeError("Analisi fermata.")
    try:
        res = json.loads(result.read_text())
    except (OSError, ValueError):
        raise RuntimeError(error or "Claude ha finito senza proporre i punti.")
    from capwizard_app.agent import image_thumb
    for p in res.get("punti", []):
        p["thumb"] = image_thumb(Path(p["render"])) if Path(p.get("render", "")).exists() else ""
    res.update(cost(len(res.get("punti", []))), lavoro=str(work), renders=[str(r) for r in renders])
    job.log(f"✅ {len(res.get('punti', []))} punti proposti. {res.get('note', '')}")
    return res


# ---------------------------------------------------------------------------
# 2. Photos and videos through the Higgsfield API
# ---------------------------------------------------------------------------

PHOTO_PROMPT = ("Extend this architectural render to a vertical 9:16 frame. Keep the building, materials, colours, light "
                "and camera angle exactly the same; do not add or remove architectural elements. Fill the new top with "
                "matching sky or ceiling and the new bottom with matching ground or floor. Photorealistic, sharp, no text, "
                "no people. Keep the {ambiente} centred.")


def _jpg(src: Path, dst: Path) -> Path:
    """A light JPG: the API uploads big PNGs unreliably."""
    subprocess.run([vault.ffmpeg() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-q:v", "2", str(dst)], check=True)
    return dst


def _check(result, last: dict, what: str) -> None:
    import higgsfield_client as hf
    status = last.get("s")
    if isinstance(status, (hf.Failed, hf.NSFW, hf.Cancelled)):
        raise RuntimeError(f"{what}: terminato come {type(status).__name__}")
    if status is not None and not isinstance(status, hf.Completed):
        raise RuntimeError(f"{what}: stato {type(status).__name__}")


def _one(job, n: int, p: dict, out: Path) -> Path:
    import higgsfield_client as hf
    render = Path(p["render"])
    job.log(f"[{n}] 📷 foto verticale da {render.name}…")
    src = hf.upload_file(_jpg(render, out / f"render {n}.jpg"))
    last: dict = {}
    res = hf.subscribe(IMG_MODEL, arguments={"prompt": PHOTO_PROMPT.format(ambiente=p.get("ambiente") or "subject"),
                                             "image_urls": [src], "aspect_ratio": "9:16", "resolution": "2k"},
                       on_queue_update=lambda s: last.update(s=s))
    _check(res, last, f"[{n}] foto")
    url = ((res or {}).get("images") or [{}])[0].get("url")
    if not url:
        raise RuntimeError(f"[{n}] foto: nessuna immagine nel risultato")
    photo = out / f"foto verticale {n}.png"
    subprocess.run(["curl", "-sfL", url, "-o", str(photo)], check=True)
    job.log(f"[{n}] 🎬 video Kling 3.0 Pro…")
    img = hf.upload_file(_jpg(photo, out / f"foto verticale {n}.jpg"))
    last = {}
    res = hf.subscribe(VID_MODEL, arguments={"image_url": img, "prompt": p["prompt"], "duration": VID_SECONDS,
                                             "sound": "off", "cfg_scale": 0.5},
                       on_queue_update=lambda s: last.update(s=s))
    _check(res, last, f"[{n}] video")
    url = ((res or {}).get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"[{n}] video: nessun video nel risultato")
    video = out / f"{n}.mp4"
    subprocess.run(["curl", "-sfL", url, "-o", str(video)], check=True)
    job.log(f"[{n}] ✅ {p.get('ambiente', '')} pronto")
    return video


def generate(job, project: dict, points: list[dict], renders_dir: str) -> dict:
    folder = Path(project.get("result_path") or "")
    if not (folder / "draft_info.json").exists():
        raise RuntimeError("Il progetto non c'è più tra i progetti CapCut.")
    if not points:
        raise RuntimeError("Spunta almeno un punto.")
    if not load_key():
        raise RuntimeError("Manca la chiave dell'API Higgsfield (HF_KEY). Mettila in "
                           f"{registry.app_data_dir() / 'higgsfield.env'} come HF_KEY=key-id:key-secret.")
    points = sorted(points, key=lambda p: p["inizio"])
    out = Path(renders_dir) / f"AI {project['name']} {time.strftime('%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    job.log(f"🏗️ {len(points)} render → foto verticali → video. Stima: {cost(len(points))['testo']}")
    videos: dict[int, Path] = {}
    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:     # the API allows 2 generations at a time on a new account
        futs = {pool.submit(_one, job, i + 1, p, out): i for i, p in enumerate(points)}
        for f, i in futs.items():
            try:
                videos[i] = f.result()
            except Exception as e:  # noqa: BLE001 — keep the clips that worked
                errors.append(str(e))
                job.log(f"⚠ {e}")
    if not videos:
        raise RuntimeError("Nessun video generato: " + "; ".join(errors))
    clips = [(videos[i], points[i]["inizio"], points[i]["fine"]) for i in sorted(videos)]
    job.log("📦 Inserisco i video nel progetto CapCut…")
    wait_closed(job, folder)
    insert(folder, project["name"], clips)
    job.log(f"✅ {len(clips)} video IA inseriti in «{project['name']}» (traccia sopra, muti, taglio netto). "
            f"File in {out}. Riapri il progetto in CapCut e controlla." + (f" Non riusciti: {len(errors)}." if errors else ""))
    return {"cartella": str(out), "inseriti": len(clips), "errori": errors}


# ---------------------------------------------------------------------------
# 3. Into the CapCut project
# ---------------------------------------------------------------------------

def wait_closed(job, folder: Path, minutes: int = 30) -> None:
    """CapCut overwrites a project that is open, and keeps saving for a moment after closing it."""
    if (folder / ".locked").exists():
        job.log("⏸ Il progetto è aperto in CapCut: torna alla home dei progetti di CapCut, inserisco i video appena lo chiudi.")
    end = time.time() + minutes * 60
    while (folder / ".locked").exists():
        if time.time() > end or job.stopped:
            raise RuntimeError("Il progetto è rimasto aperto in CapCut: i video sono pronti nella cartella, inseriscili a mano.")
        time.sleep(3)
    time.sleep(6)


def insert(folder: Path, name: str, clips: list[tuple[Path, float, float]]) -> None:
    media = vault.HOME / "Movies/CapCut Editor" / name            # inside ~/Movies: CapCut's sandbox can read it
    media.mkdir(parents=True, exist_ok=True)
    d = json.load(open(folder / "draft_info.json"))
    m = d["materials"]
    byid = {x["id"]: (k, x) for k, v in m.items() if isinstance(v, list) for x in v if isinstance(x, dict) and "id" in x}
    main = next(t for t in d["tracks"] if t["type"] == "video")
    proto = main["segments"][0]
    proto_mat = byid[proto["material_id"]][1]
    meta = json.load(open(folder / "draft_meta_info.json"))
    group = next((g for g in meta.get("draft_materials", []) if g.get("type") == 0), None)
    segs = []
    for i, (src, a, b) in enumerate(clips, 1):
        dst = media / f"AI {i} {time.strftime('%H%M%S')}.mp4"
        shutil.copy2(src, dst)
        w, h, dur = _probe(dst)
        mat = copy.deepcopy(proto_mat)
        mat.update(id=_nid(), unique_id=uuid.uuid4().hex, local_material_id=_nid(), path=str(dst), material_name=dst.name,
                   duration=dur, width=w, height=h, has_audio=False)
        m["videos"].append(mat)
        if group is not None and group.get("value"):
            e = copy.deepcopy(group["value"][0])
            e["file_Path"] = str(dst)
            e.update(id=_nid(), extra_info=dst.name, metetype="video", duration=dur, width=w, height=h,
                     roughcut_time_range={"duration": dur, "start": 0})
            group["value"].append(e)
        refs = []
        for r in proto.get("extra_material_refs", []):
            k, x = byid[r]
            x = copy.deepcopy(x)
            x["id"] = _nid()
            m[k].append(x)
            refs.append(x["id"])
        s = copy.deepcopy(proto)
        length = int(round((b - a) * US))
        s.update(id=_nid(), material_id=mat["id"], extra_material_refs=refs, volume=0.0, last_nonzero_volume=1.0,
                 target_timerange={"start": int(round(a * US)), "duration": length},
                 source_timerange={"start": int(SKIP * US), "duration": min(length, dur - int(SKIP * US))},
                 render_index=1, track_render_index=1, common_keyframes=[], keyframe_refs=[])
        s["clip"] = {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0},
                     "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0}
        segs.append(s)
    track = copy.deepcopy(main)
    track.update(id=_nid(), segments=segs)
    d["tracks"].insert(d["tracks"].index(main) + 1, track)
    vault.drop_patch(folder)
    json.dump(meta, open(folder / "draft_meta_info.json", "w"), ensure_ascii=False)
    for f in [folder / "draft_info.json", folder / "template-2.tmp", *map(Path, glob.glob(str(folder / "Timelines/*/draft_info.json"))),
              *map(Path, glob.glob(str(folder / "Timelines/*/template-2.tmp")))]:
        if f.exists() or f.name == "draft_info.json":
            json.dump(d, open(f, "w"), ensure_ascii=False)


def _nid() -> str:
    return str(uuid.uuid4()).upper()


def _probe(path: Path) -> tuple[int, int, int]:
    import re
    out = subprocess.run([vault.ffmpeg() or "ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    w, h = map(int, re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", out).groups())
    hh, mm, ss = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return w, h, int((int(hh) * 3600 + int(mm) * 60 + float(ss)) * US)
