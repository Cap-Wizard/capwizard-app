"""The AI section: videos and images made by ComfyUI's partner nodes, paid with the Comfy account's credits.

CapWizard builds the workflow itself, sends it to the ComfyUI on this Mac (starting it when it is off),
waits and keeps the result in ~/Movies/CapWizard/AI, where the app shows it.

The first part is «Transizioni A e B»: from frame A to frame B (with stops in between, one generation per
leg) with Kling 3.0 First-Last-Frame, then the legs joined with ffmpeg. The movement templates (Drone FPV…)
live in a JSON so new ones don't need code.

The Comfy API key is read from the macOS Keychain (account «capwizard», service «comfy-org-api-key») and
only ever goes in the request to the local ComfyUI: never in a file, a log or a response.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from capwizard_app import registry, vault

COMFY_URL = "http://127.0.0.1:8188"
COMFY_DIR = Path.home() / "ComfyUI-Installs/ComfyUI/ComfyUI"
OUT_ROOT = Path.home() / "Movies/CapWizard/AI"
CATS = ("Transizioni", "Ambientazioni", "Immagini")      # one folder per section, a result in each subfolder
KEYCHAIN = ("capwizard", "comfy-org-api-key")
TEMPLATES = Path(__file__).resolve().parent / "tools" / "ai_templates.json"
MODELS = Path(__file__).resolve().parent / "tools" / "ai_models.json"
USER_TEMPLATES = registry.app_data_dir() / "ai_templates.json"     # the user's own, when there is one

# Comfy credits: «$1 = 211 credits» (support.comfy.org, «How credits work in Comfy», checked 30/10/2026)
CREDITS_PER_USD = 211
ECB = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_FX: dict = {}


class AIError(Exception):
    pass


# ---------------------------------------------------------------------------
# ComfyUI and the account
# ---------------------------------------------------------------------------

def _key() -> str:
    r = subprocess.run(["security", "find-generic-password", "-a", KEYCHAIN[0], "-s", KEYCHAIN[1], "-w"],
                       capture_output=True, text=True, timeout=20)
    return r.stdout.strip() if r.returncode == 0 else ""


def _get(path: str, timeout: float = 10):
    with urllib.request.urlopen(COMFY_URL + path, timeout=timeout) as r:
        return json.load(r)


def running() -> bool:
    try:
        _get("/system_stats", 2)
        return True
    except (OSError, ValueError):
        return False


def status() -> dict:
    return {"comfy": running(), "installato": (COMFY_DIR / "main.py").exists(), "chiave": bool(_key())}


def start(log=print) -> None:
    """Start the ComfyUI of this Mac in the background, when it is off."""
    if running():
        return
    py = COMFY_DIR / ".venv/bin/python"
    if not py.exists():
        raise AIError("ComfyUI non è installato in ~/ComfyUI-Installs: aprilo una volta con Comfy Desktop.")
    log("Avvio ComfyUI…")
    out = open(registry.app_data_dir() / "comfyui.log", "ab")
    subprocess.Popen([str(py), "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"],
                     cwd=COMFY_DIR, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(90):
        if running():
            log("ComfyUI è acceso.")
            return
        time.sleep(2)
    raise AIError("ComfyUI non è partito in 3 minuti: guarda comfyui.log nella cartella dati di CapWizard.")


# ---------------------------------------------------------------------------
# Templates and cost
# ---------------------------------------------------------------------------

def templates() -> dict:
    for f in (USER_TEMPLATES, TEMPLATES):
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            continue
    return {"transizioni": []}


def models() -> dict:
    """The video models that go from frame A to frame B, their prices for each provider, and the providers."""
    try:
        return json.loads(MODELS.read_text())
    except (OSError, ValueError):
        return {"provider": [], "modelli": []}


def _model(mid: str) -> dict:
    m = next((m for m in models()["modelli"] if m["id"] == mid), None)
    if not m:
        raise AIError(f"Modello «{mid}» non previsto.")
    return m


def _template(kind: str, tid: str) -> dict:
    t = next((t for t in templates().get(kind, []) if t["id"] == tid), None)
    if not t:
        raise AIError(f"Template «{tid}» non trovato.")
    return t


def eur_per_usd() -> dict:
    """Today's ECB reference rate (cached for the day): how many dollars one euro buys, and its date."""
    today = datetime.now().strftime("%Y-%m-%d")
    if _FX.get("letto") == today:
        return _FX
    try:
        # macOS curl: the system's certificates (the app's own Python has none for HTTPS)
        xml = subprocess.run(["curl", "-sf", "-m", "10", ECB], capture_output=True, text=True, timeout=15).stdout
        rate = float(re.search(r"currency='USD' rate='([\d.]+)'", xml).group(1))
        day = re.search(r"time='([\d-]+)'", xml).group(1)
        _FX.update(usd=rate, data=day, letto=today)
    except (OSError, AttributeError, ValueError):
        if not _FX.get("usd"):
            return {}
    return _FX


def estimate(durate: list[int], modello: str, risoluzione: str, provider: str = "comfy") -> dict:
    """The cost of the legs (one duration each) with a model at a resolution, on one provider."""
    m = _model(modello)
    if risoluzione not in m["risoluzioni"]:
        risoluzione = m["risoluzioni"][-1] if risoluzione == "4k" else m["risoluzioni"][0]
    # each leg at the nearest length the model allows (Veo: 4, 6, 8 s; Kling 2.5: 5, 10 s)
    durate = [min(m["durate"], key=lambda x: (abs(x - int(d)), -x)) for d in durate] or [5]
    secs = sum(durate)
    fx = eur_per_usd()
    out = {"modello": m["id"], "risoluzione": risoluzione, "provider": provider, "secondi": secs, "tratte": len(durate),
           "durate": durate,
           "cambio": {"usd_per_eur": fx.get("usd"), "data": fx.get("data")} if fx else None}
    rates = m.get(provider) or {}
    rate = rates.get(risoluzione)
    if rate is None:
        return {**out, "ok": False, "msg": f"{m['nome']} a {risoluzione} non ha un prezzo noto su questo provider."}
    prov = next((p for p in models()["provider"] if p["id"] == provider), {})
    if provider == "comfy":
        usd = round(rate * secs, 4)
        return {**out, "ok": True, "tariffa": rate, "unita": "$/s", "usd": usd, "crediti": round(usd * CREDITS_PER_USD, 1),
                "eur": round(usd / fx["usd"], 2) if fx else None, "crediti_per_usd": CREDITS_PER_USD}
    credits = round(rate * secs, 1)
    if prov.get("eur_per_credito"):
        eur = round(credits * prov["eur_per_credito"], 2)
        usd = round(eur * fx["usd"], 4) if fx else None
    else:
        usd = round(credits * prov.get("usd_per_credito", 0), 4)
        eur = round(usd / fx["usd"], 2) if fx else None
    return {**out, "ok": True, "tariffa": rate, "unita": "crediti/s", "crediti": credits, "usd": usd, "eur": eur}


# ---------------------------------------------------------------------------
# One generation on ComfyUI
# ---------------------------------------------------------------------------

def _format(fid: str) -> dict:
    f = next((f for f in models().get("formati", []) if f["id"] == fid), None)
    if not f:
        raise AIError(f"Formato «{fid}» non previsto.")
    return f


def _fitvf(fid: str | None, pad: bool = False) -> list[str]:
    """ffmpeg filter that brings a picture to a format: cropped in the middle, or (pad) fitted with bars."""
    if not fid:
        return []
    f = _format(fid)
    w, h = f["w"], f["h"]
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black" if pad
          else f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
    return ["-vf", vf + ",setsar=1"]


def _frame(src: Path, dest: Path, fmt: str | None = None, pad: bool = False) -> Path:
    """A frame as PNG (HEIC and WebP too), in the chosen format, checked against Kling's limits: 300 px, between
    1:2.5 and 2.5:1."""
    r = subprocess.run([vault.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", *_fitvf(fmt, pad), str(dest)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode or not dest.exists():
        raise AIError(f"Non riesco a leggere l'immagine {src.name}.")
    w, h = _size(dest)
    if min(w, h) < 300:
        raise AIError(f"{src.name} è troppo piccola ({w}x{h}): servono almeno 300 px per lato.")
    if not (1 / 2.5 <= w / h <= 2.5):
        raise AIError(f"{src.name} è troppo stretta o troppo larga ({w}x{h}): serve tra 1:2,5 e 2,5:1.")
    return dest


def _size(p: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=30)
    w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
    return w, h


def _upload(p: Path, kind: str = "image/png") -> str:
    boundary = "----capwizard" + uuid.uuid4().hex
    name = f"capwizard_{uuid.uuid4().hex[:8]}_{p.name}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: {kind}\r\n\r\n").encode() + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(COMFY_URL + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["name"]


def _run(prompt: dict, log, label: str) -> dict:
    """Queue a workflow with the account's key and wait for it; returns its outputs."""
    key = _key()
    if not key:
        raise AIError("Manca la API key di Comfy nel Portachiavi (account capwizard, servizio comfy-org-api-key).")
    data = json.dumps({"prompt": prompt, "client_id": "capwizard", "extra_data": {"api_key_comfy_org": key}}).encode()
    del key
    try:
        with urllib.request.urlopen(urllib.request.Request(COMFY_URL + "/prompt", data=data,
                                                           headers={"Content-Type": "application/json"}), timeout=30) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise AIError(f"ComfyUI ha rifiutato il workflow: {e.read().decode(errors='replace')[:300]}") from None
    pid, t0 = res["prompt_id"], time.time()
    while time.time() - t0 < 1800:
        h = _get(f"/history/{pid}").get(pid)
        if h:
            st = h.get("status", {})
            if st.get("status_str") != "success":
                msg = next((m[1].get("exception_message", "") for m in st.get("messages", []) if m[0] == "execution_error"), "")
                if "Payment Required" in msg:
                    msg = "Crediti Comfy finiti: ricarica l'account su platform.comfy.org."
                elif "Unauthorized" in msg:
                    msg = "La API key di Comfy non è valida: salvane una nuova nel Portachiavi."
                raise AIError(msg.strip() or "La generazione non è riuscita.")
            return h.get("outputs", {})
        mins = int(time.time() - t0) // 60
        if mins and int(time.time() - t0) % 60 < 5:
            log(f"{label}: in corso da {mins} min…")
        time.sleep(5)
    raise AIError("Dopo 30 minuti la generazione non è finita.")


def _download(outputs: dict, dest: Path) -> Path:
    """The file the workflow saved; an input shown back by a loader (type «input») is not the result."""
    for node in outputs.values():
        for f in node.get("images", []) + node.get("videos", []) + node.get("gifs", []):
            if f.get("type", "output") != "output":
                continue
            q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
            with urllib.request.urlopen(f"{COMFY_URL}/view?{q}", timeout=300) as r, open(dest, "wb") as out:
                shutil.copyfileobj(r, out)
            return dest
    raise AIError("ComfyUI non ha restituito nessun file.")


def _leg(a: Path, b: Path, text: str, modello: str, durata: int, risoluzione: str, audio: bool, dest: Path, log, label: str) -> dict:
    """One leg from frame a to frame b with the chosen model."""
    m = _model(modello)
    ia, ib = _upload(a), _upload(b)
    seed = random.randint(1, 2_000_000_000)
    w, h = _size(a)
    ratio = "9:16" if h > w else "16:9"
    negative = "cuts, morphing, distorted architecture, text, captions, watermark, people"
    if m["nodo"] == "KlingFirstLastFrameNode":
        node = {"prompt": text[:2500], "duration": durata, "first_frame": ["1", 0], "end_frame": ["2", 0],
                "generate_audio": audio, "model": "kling-v3", "model.resolution": risoluzione, "seed": seed}
    elif m["nodo"] == "Veo3FirstLastFrameNode":
        node = {"prompt": text, "negative_prompt": negative, "resolution": risoluzione, "aspect_ratio": ratio,
                "duration": durata, "seed": seed, "first_frame": ["1", 0], "last_frame": ["2", 0],
                "model": m["veo"], "generate_audio": audio}
    elif m["nodo"] == "KlingStartEndFrameNode":
        node = {"start_frame": ["1", 0], "end_frame": ["2", 0], "prompt": text[:2500], "negative_prompt": negative,
                "cfg_scale": 0.5, "aspect_ratio": ratio, "mode": f"pro mode / {durata}s duration / kling-v2-5-turbo"}
    else:
        raise AIError(f"Il nodo {m['nodo']} non è ancora gestito.")
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": ia}},
        "2": {"class_type": "LoadImage", "inputs": {"image": ib}},
        "3": {"class_type": m["nodo"], "inputs": node},
        "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0], "filename_prefix": "capwizard/ai", "format": "auto", "codec": "auto"}},
    }
    log(f"{label}: genero con {m['nome']} ({risoluzione}, {durata} s)…")
    t0 = time.time()
    _download(_run(prompt, log, label), dest)
    log(f"{label}: pronta in {int(time.time() - t0) // 60} min {int(time.time() - t0) % 60} s.")
    return {"seed": seed, "secondi": int(time.time() - t0)}


# ---------------------------------------------------------------------------
# Transizioni A e B
# ---------------------------------------------------------------------------

def _gen_len(modello: str, want: int) -> int:
    """How long the model really makes a leg: what was asked, or its shortest when that is shorter."""
    allowed = _model(modello)["durate"]
    return want if want in allowed else min(allowed, key=lambda x: (abs(x - want), -x))


def _speed(clip: Path, secs: float) -> None:
    """Speed a leg up to last «secs» (a model can't make it that short)."""
    tmp = clip.with_name(clip.stem + "_lento.mp4")
    clip.rename(tmp)
    k = secs / max(0.1, _seconds(tmp))
    r = subprocess.run([vault.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-filter:v", f"setpts={k}*PTS",
                        "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-t", f"{secs}", str(clip)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        tmp.rename(clip)
        raise AIError(f"Non riesco ad accelerare la tratta: {r.stderr.strip()[:200]}")
    tmp.unlink(missing_ok=True)


def _slug(s: str) -> str:
    return re.sub(r"[^\w\- ]+", "", s).strip().replace(" ", "-")[:50] or "transizione"


def _join(folder: Path, meta: dict) -> Path:
    legs = [folder / t["file"] for t in meta["tratte"] if (folder / t["file"]).exists()]
    final = folder / f"{_slug(meta['nome'])}.mp4"
    if len(legs) == 1:
        shutil.copyfile(legs[0], final)
        return final
    lst = folder / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in legs))
    ff = vault.ffmpeg() or "ffmpeg"
    r = subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", final.name],
                       cwd=folder, capture_output=True, text=True, timeout=600)
    if r.returncode:              # legs that don't match exactly: one encode
        subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c:v", "libx264", "-crf", "16",
                        "-pix_fmt", "yuv420p", "-an", final.name], cwd=folder, capture_output=True, text=True, timeout=1800, check=True)
    lst.unlink(missing_ok=True)
    return final


def _meta(folder: Path) -> dict:
    return json.loads((folder / "capwizard-ai.json").read_text())


def _save(folder: Path, meta: dict) -> None:
    (folder / "capwizard-ai.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))


def new_transition(frames: list[str], template_id: str, nome: str, durate: list[int], modello: str, risoluzione: str,
                   note: list[str], provider: str = "comfy", formato: str = "9:16") -> Path:
    """The folder of a new transition, with its frames copied in and nothing generated yet."""
    if len(frames) < 2:
        raise AIError("Servono almeno il frame A e il frame B.")
    if provider != "comfy":
        raise AIError("Per ora genero solo con Comfy: Magnific e Higgsfield vanno ancora collegati.")
    t = _template("transizioni", template_id)
    m = _model(modello)
    legs = len(frames) - 1
    durate = [int(d) for d in (durate or [])][:legs] + [t.get("durata", 5)] * max(0, legs - len(durate or []))
    bad = [d for d in durate if d not in m["durate"] and not 1 <= d < min(m["durate"])]
    if bad:
        raise AIError(f"{m['nome']} accetta solo tratte da {', '.join(map(str, m['durate']))} secondi (o più corte, accelerate).")
    if risoluzione not in m["risoluzioni"]:
        raise AIError(f"{m['nome']} non fa video a {risoluzione}.")
    folder = OUT_ROOT / "Transizioni" / f"{datetime.now():%Y-%m-%d_%H%M}_{_slug(nome or t['nome'])}"
    (folder / "frame").mkdir(parents=True, exist_ok=True)
    copies = [str(_frame(Path(f), folder / "frame" / f"{i + 1:02d}.png", formato).name) for i, f in enumerate(frames)]
    meta = {"tipo": "transizione", "formato": formato, "nome": nome or t["nome"], "template": t["id"], "template_nome": t["nome"],
            "modello": m["id"], "modello_nome": m["nome"], "provider": provider,
            "risoluzione": risoluzione, "audio": bool(t.get("audio")), "prompt": t["prompt"],
            "frame": copies, "creato": datetime.now().isoformat(timespec="seconds"),
            "tratte": [{"file": f"tratta_{i + 1:02d}.mp4", "durata": durate[i],
                        "nota": (note[i] if i < len(note) else "").strip(), "stato": "da fare"} for i in range(legs)]}
    meta["stima"] = estimate(durate, m["id"], risoluzione, provider)
    _save(folder, meta)
    return folder


def generate(folder: Path, only: int | None = None, log=print) -> dict:
    """Generate the legs (all, or just one to redo it) and join them."""
    start(log)
    meta = _meta(folder)
    legs = range(len(meta["tratte"])) if only is None else [only]
    spent = 0.0
    for i in legs:
        t = meta["tratte"][i]
        label = f"Tratta {i + 1} di {len(meta['tratte'])}"
        text = meta["prompt"] + (f" {t['nota']}" if t["nota"] else "")
        try:
            want = t.get("durata", meta.get("durata", 5))
            made = _gen_len(meta.get("modello", "kling-v3"), want)
            info = _leg(folder / "frame" / meta["frame"][i], folder / "frame" / meta["frame"][i + 1], text,
                        meta.get("modello", "kling-v3"), made, meta["risoluzione"], meta["audio"], folder / t["file"], log, label)
            if want < made:                               # shorter than the model can make: the leg is sped up
                log(f"{label}: accelero da {made} s a {want} s…")
                _speed(folder / t["file"], want)
        except AIError:
            t["stato"] = "errore"
            _save(folder, meta)
            raise
        cost = estimate([t.get("durata", meta.get("durata", 5))], meta.get("modello", "kling-v3"), meta["risoluzione"])
        t.update(stato="fatta", **info, costo=cost, quando=datetime.now().isoformat(timespec="seconds"))
        spent += cost["usd"]
        _save(folder, meta)
    if all(t["stato"] == "fatta" for t in meta["tratte"]):
        log("Unisco le tratte…" if len(meta["tratte"]) > 1 else "Salvo il video…")
        meta["video"] = _join(folder, meta).name
    meta["speso_usd"] = round(meta.get("speso_usd", 0) + spent, 4)
    _save(folder, meta)
    return describe(folder)


def describe(folder: Path) -> dict:
    meta = _meta(folder)
    fx = eur_per_usd()
    spent = meta.get("speso_usd", 0)
    cat = folder.parent.name
    return {**meta, "cartella": str(folder), "id": folder.name if cat == "Transizioni" else f"{cat}/{folder.name}",
            "costo": {"usd": spent, "crediti": round(spent * CREDITS_PER_USD, 1),
                      "eur": round(spent / fx["usd"], 2) if fx else None, "stima": True}}


def history() -> list[dict]:
    out = []
    for cat in CATS:
        for f in (OUT_ROOT / cat).glob("*/capwizard-ai.json"):
            try:
                d = describe(f.parent)
                if not d.get("nascosto"):                       # taken off the list with its ✕
                    out.append(d)
            except (OSError, ValueError, KeyError):
                continue
    return sorted(out, key=lambda r: r.get("creato", ""), reverse=True)


def folder_of(job_id: str) -> Path:
    """A result's folder from its id («<name>» for a transition, «<section>/<name>» otherwise), never outside."""
    cat, _, name = job_id.rpartition("/")
    cat = cat or "Transizioni"
    root = (OUT_ROOT / cat).resolve()
    p = (OUT_ROOT / cat / name).resolve()
    if cat not in CATS or p.parent != root or not (p / "capwizard-ai.json").exists():
        raise AIError("Risultato non trovato.")
    return p


def inside(path: str) -> Path:
    """A file the app may show: only what is in the AI folder."""
    p = Path(path).resolve()
    if not p.is_file() or OUT_ROOT.resolve() not in p.parents:
        raise AIError("File non disponibile.")
    return p


# ---------------------------------------------------------------------------
# The account's key, and «Modifica» on a result
# ---------------------------------------------------------------------------

def save_key(key: str) -> dict:
    """Keep the Comfy API key in the Keychain (where everything else reads it from); never in a file."""
    key = (key or "").strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        return {"ok": False, "msg": "Non sembra una API key di Comfy: copiala intera da platform.comfy.org."}
    r = subprocess.run(["security", "add-generic-password", "-U", "-a", KEYCHAIN[0], "-s", KEYCHAIN[1], "-w", key],
                       capture_output=True, text=True, timeout=20)
    return {"ok": r.returncode == 0, "msg": "" if r.returncode == 0 else "Il Portachiavi non ha salvato la chiave."}


def _edit_model(mid: str) -> dict:
    m = next((m for m in models().get("modifica", []) if m["id"] == mid), None)
    if not m:
        raise AIError(f"Modello di modifica «{mid}» non previsto.")
    return m


def _seconds(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def edit_estimate(secs: float, modello: str, risoluzione: str) -> dict:
    m = _edit_model(modello)
    res = risoluzione if risoluzione in m["risoluzioni"] else m["risoluzioni"][-1]
    billed = round(secs * (2 if m.get("doppio") else 1), 2)
    usd = round(m["comfy"][res] * billed, 4)
    fx = eur_per_usd()
    return {"ok": True, "modello": m["id"], "risoluzione": res, "secondi": round(secs, 2), "pagati": billed,
            "tariffa": m["comfy"][res], "usd": usd, "crediti": round(usd * CREDITS_PER_USD, 1),
            "eur": round(usd / fx["usd"], 2) if fx else None, "troppo_lungo": secs > m["max"], "max": m["max"],
            "cambio": {"usd_per_eur": fx.get("usd"), "data": fx.get("data")} if fx else None}


def edit_estimates(folder: Path, source: str, risoluzione: str) -> dict:
    """What each edit model would cost on this result's video."""
    secs = _seconds(folder / source)
    return {"ok": True, "secondi": round(secs, 2),
            "stime": {m["id"]: edit_estimate(secs, m["id"], risoluzione) for m in models().get("modifica", [])}}


def edit(folder: Path, source: str, testo: str, modello: str, risoluzione: str, log=print) -> dict:
    """A new version of a result: its video changed as the prompt says, with the chosen model."""
    testo = (testo or "").strip()
    if not testo:
        raise AIError("Scrivi cosa cambiare.")
    m = _edit_model(modello)
    src = (folder / source).resolve()
    if src.parent != folder.resolve() or not src.is_file():
        raise AIError("Il video da modificare non c'è più.")
    est = edit_estimate(_seconds(src), m["id"], risoluzione)
    if est["troppo_lungo"]:
        raise AIError(f"{m['nome']} modifica video fino a {m['max']} secondi.")
    start(log)
    w, h = _size(src)
    name = f"capwizard_{uuid.uuid4().hex[:8]}_{src.name}"
    boundary = "----capwizard" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: video/mp4\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    with urllib.request.urlopen(urllib.request.Request(COMFY_URL + "/upload/image", data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=300) as r:
        uploaded = json.load(r)["name"]
    seed = random.randint(1, 2_000_000_000)
    res = est["risoluzione"]
    if m["nodo"] == "KlingOmniProEditVideoNode":
        node = {"model_name": "kling-v3-omni", "prompt": testo[:2500], "video": ["1", 0], "keep_original_sound": False,
                "resolution": res, "seed": seed}
    elif m["nodo"] == "Wan2VideoEditApi":
        node = {"model": "wan2.7-videoedit", "model.prompt": testo, "model.resolution": res.upper(),
                "model.ratio": "9:16" if h > w else "16:9", "model.duration": "auto", "video": ["1", 0],
                "seed": seed, "audio_setting": "origin", "watermark": False}
    elif m["nodo"] == "RunwayAleph2VideoToVideoNode":
        node = {"prompt": testo[:1000], "video": ["1", 0], "seed": seed, "public_figure_threshold": "low"}
    elif m["nodo"] == "FluxVideoEditNode":
        node = {"video": ["1", 0], "prompt": testo[:4096], "auto_downscale": True, "safety_tolerance": 4, "seed": seed}
    else:
        raise AIError(f"Il nodo {m['nodo']} non è ancora gestito.")
    meta = _meta(folder)
    n = len(meta.get("modifiche", [])) + 1
    dest = folder / f"modifica_{n:02d}.mp4"
    prompt = {"1": {"class_type": "LoadVideo", "inputs": {"file": uploaded}},
              "2": {"class_type": m["nodo"], "inputs": node},
              "3": {"class_type": "SaveVideo", "inputs": {"video": ["2", 0], "filename_prefix": "capwizard/ai", "format": "auto", "codec": "auto"}}}
    log(f"Modifica {n}: {m['nome']} ({res}) su {source}…")
    t0 = time.time()
    _download(_run(prompt, log, f"Modifica {n}"), dest)
    log(f"Modifica {n}: pronta in {int(time.time() - t0) // 60} min {int(time.time() - t0) % 60} s.")
    meta = _meta(folder)
    meta.setdefault("modifiche", []).append({"file": dest.name, "da": source, "testo": testo, "modello": m["id"],
                                             "modello_nome": m["nome"], "risoluzione": res, "costo": est,
                                             "quando": datetime.now().isoformat(timespec="seconds")})
    meta["speso_usd"] = round(meta.get("speso_usd", 0) + est["usd"], 4)
    _save(folder, meta)
    return describe(folder)


# ---------------------------------------------------------------------------
# Immagini da prompt, and «Ambientazione personaggio» (a photo or a video of someone, taken into a new place)
# ---------------------------------------------------------------------------

SEEDREAM_SIZES = {"9:16": "1440x2560 (9:16)", "16:9": "2560x1440 (16:9)", "1:1": "2048x2048 (1:1)",
                  "3:4": "1728x2304 (3:4)", "4:3": "2304x1728 (4:3)"}
XFADE = 0.4                     # seconds of dissolve where the new place starts in a video


def _pick(kind: str, mid: str) -> dict:
    m = next((m for m in models().get(kind, []) if m["id"] == mid), None)
    if not m:
        raise AIError(f"Modello «{mid}» non previsto.")
    return m


def _money(usd: float) -> dict:
    fx = eur_per_usd()
    usd = round(usd, 4)
    return {"usd": usd, "crediti": round(usd * CREDITS_PER_USD, 1), "eur": round(usd / fx["usd"], 2) if fx else None,
            "cambio": {"usd_per_eur": fx.get("usd"), "data": fx.get("data")} if fx else None}


def image_estimate(modello: str, formato: str) -> dict:
    m = _pick("immagini", modello)
    return {"ok": formato in m["formati"], "msg": "" if formato in m["formati"] else f"{m['nome']} non fa il formato {formato}.",
            "modello": m["id"], "tariffa": m["comfy"], **_money(m["comfy"])}


def _image(inputs: list[Path], text: str, modello: str, formato: str, dest: Path, log, label: str) -> dict:
    """One picture from a prompt and some images (the first is the one to work on, the others references)."""
    m = _pick("immagini", modello)
    if formato not in m["formati"]:
        raise AIError(f"{m['nome']} non fa il formato {formato}.")
    names = [_upload(p) for p in inputs]
    wf: dict = {}
    for i, n in enumerate(names):
        wf[str(10 + i)] = {"class_type": "LoadImage", "inputs": {"image": n}}
    ref = None
    if len(names) == 1:
        ref = ["10", 0]
    elif names:
        wf["9"] = {"class_type": "BatchImagesNode", "inputs": {f"images.image{i}": [str(10 + i), 0] for i in range(len(names))}}
        ref = ["9", 0]
    seed = random.randint(1, 2_000_000_000)
    if m["nodo"] in ("GeminiImage2Node", "GeminiNanoBanana2"):
        node = {"prompt": text, "model": m["modello"], "seed": seed, "aspect_ratio": formato, "resolution": m["risoluzione"],
                "response_modalities": "IMAGE"}
        if m["nodo"] == "GeminiNanoBanana2":
            node["thinking_level"] = "MINIMAL"
        if ref:
            node["images"] = ref
    elif m["nodo"] == "ByteDanceSeedreamNode":
        node = {"model": m["modello"], "prompt": text, "seed": seed, "watermark": False, "sequential_image_generation": "disabled",
                "max_images": 1, "fail_on_partial": True}
        if formato in SEEDREAM_SIZES:
            node["size_preset"] = SEEDREAM_SIZES[formato]
        else:                                                   # 4:5
            node.update(size_preset="Custom", width=1638, height=2048)
        if ref:
            node["image"] = ref
    elif m["nodo"] == "KlingOmniProImageNode":
        node = {"model_name": m["modello"], "prompt": text, "resolution": m["risoluzione"], "aspect_ratio": formato,
                "series_amount": "disabled", "seed": seed}
        if ref:
            node["reference_images"] = ref
    else:
        raise AIError(f"Il nodo {m['nodo']} non è ancora gestito.")
    wf["2"] = {"class_type": m["nodo"], "inputs": node}
    wf["3"] = {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "capwizard/ai"}}
    log(f"{label}: {m['nome']} ({formato})…")
    t0 = time.time()
    _download(_run(wf, log, label), dest)
    log(f"{label}: pronta in {int(time.time() - t0)} s.")
    return {"seed": seed, "secondi": int(time.time() - t0), **_money(m["comfy"])}


def _png(src: Path, dest: Path) -> Path:
    r = subprocess.run([vault.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", str(dest)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode or not dest.exists():
        raise AIError(f"Non riesco a leggere l'immagine {src.name}.")
    return dest


def _new_folder(cat: str, nome: str) -> Path:
    folder = OUT_ROOT / cat / f"{datetime.now():%Y-%m-%d_%H%M%S}_{_slug(nome)}"
    (folder / "input").mkdir(parents=True, exist_ok=True)
    return folder


def new_image(main: str, refs: list[str], testo: str, modello: str, formato: str, nome: str) -> Path:
    if not (testo or "").strip():
        raise AIError("Scrivi il prompt.")
    if not main and not refs:
        pass                                                    # a picture from the prompt alone is fine too
    est = image_estimate(modello, formato)
    if not est["ok"]:
        raise AIError(est["msg"])
    folder = _new_folder("Immagini", nome or "immagine")
    files = []
    if main:
        files.append(_png(Path(main), folder / "input" / "00_immagine.png").name)
    for i, r in enumerate(refs or []):
        files.append(_png(Path(r), folder / "input" / f"{i + 1:02d}_reference.png").name)
    _save(folder, {"tipo": "immagine", "nome": nome or "Immagine", "testo": testo.strip(), "modello": modello,
                   "modello_nome": _pick("immagini", modello)["nome"], "formato": formato, "input": files,
                   "con_immagine": bool(main), "creato": datetime.now().isoformat(timespec="seconds"), "stima": est})
    return folder


def generate_image(folder: Path, log=print) -> dict:
    start(log)
    meta = _meta(folder)
    inputs = [folder / "input" / f for f in meta["input"]]
    text = meta["testo"]
    if len(inputs) > 1:
        text = ("The first image is the one to work on; the other images are references to follow. " if meta.get("con_immagine")
                else "Use the images as references. ") + text
    info = _image(inputs, text, meta["modello"], meta["formato"], folder / "risultato.png", log, "Immagine")
    meta.update(immagine="risultato.png", speso_usd=round(meta.get("speso_usd", 0) + info["usd"], 4))
    _save(folder, meta)
    return describe(folder)


PLACE_MODEL = "nano-banana-pro"           # makes the place from several «spunto» images


def scene_estimate(durata: float, modello: str, risoluzione: str, fps: float = 24, riferimento: str = "esatto") -> dict:
    """«Ambientazione personaggio»: a video of someone, with the place behind them changed (from the start, or little by
    little up to the end). With «Solo spunto» the place is first drawn from the images."""
    m = _pick("ambientazione_video", modello)
    secs = max(0.0, float(durata))
    if m["nodo"] == "BeebleSwitchXVideoEdit":
        frames = int(secs * fps + 0.999)
        if frames > m["max_frames"]:
            return {"ok": False, "msg": f"{m['nome']} lavora al massimo {m['max_frames']} fotogrammi ({m['max_frames'] / fps:.1f} s)."}
        rate = m["comfy"]["1080p" if risoluzione == "1080p" else "720p"]
        usd, parts = rate * frames / 30, [f"{rate} $ × {frames} fotogrammi / 30"]
    else:
        if m.get("max") and secs > m["max"]:
            return {"ok": False, "msg": f"{m['nome']} lavora al massimo {m['max']} secondi."}
        rate = m["comfy"] if isinstance(m["comfy"], (int, float)) else m["comfy"]["1080p" if risoluzione == "1080p" else "720p"]
        usd, parts = rate * secs, [f"{rate} $/s × {secs:.1f} s"]
    if riferimento == "spunto":
        img = _pick("immagini", PLACE_MODEL)
        usd += img["comfy"]
        parts.insert(0, f"posto dalle immagini {img['comfy']} $")
    return {"ok": True, "secondi": round(secs, 2), "parti": parts, "labiale": m["labiale"], **_money(usd)}


def _fit_video(src: Path, dest: Path, fmt: str) -> Path:
    """The person's video in the chosen format (cropped in the middle), H.264 with its sound."""
    r = subprocess.run([vault.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-i", str(src), *_fitvf(fmt), "-c:v", "libx264", "-crf", "14",
                        "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(dest)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode or not dest.exists():
        raise AIError(f"Non riesco a preparare il video {src.name}.")
    return dest


def _fps(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, timeout=30)
    try:
        a, b = r.stdout.strip().split("/")
        return float(a) / float(b)
    except ValueError:
        return 24.0


def new_scene(soggetto: str, contesti: list[str], testo: str, modello: str, risoluzione: str, riferimento: str,
              arrivo: str, formato: str, nome: str) -> Path:
    if not soggetto:
        raise AIError("Metti a sinistra il video della persona.")
    contesti = [c for c in (contesti or []) if c]
    if not contesti:
        raise AIError("Metti a destra l'immagine del posto.")
    riferimento = "spunto" if riferimento == "spunto" else "esatto"
    if riferimento == "esatto":
        contesti = contesti[:1]
    folder = _new_folder("Ambientazioni", nome or "ambientazione")
    v = _fit_video(Path(soggetto), folder / "input" / "soggetto.mp4", formato)
    meta = {"tipo": "ambientazione", "modo": "video", "nome": nome or "Ambientazione", "testo": (testo or "").strip(),
            "modello": modello, "modello_nome": _pick("ambientazione_video", modello)["nome"], "risoluzione": risoluzione,
            "formato": formato, "riferimento": riferimento, "arrivo": "fine" if arrivo == "fine" else "inizio",
            "durata": round(_seconds(v), 2), "fps": _fps(v), "creato": datetime.now().isoformat(timespec="seconds"),
            "contesti": [_frame(Path(c), folder / "input" / f"contesto_{i + 1:02d}.png", None if riferimento == "spunto" else formato).name
                         for i, c in enumerate(contesti)]}
    meta["stima"] = scene_estimate(meta["durata"], modello, risoluzione, meta["fps"], riferimento)
    if not meta["stima"].get("ok"):
        raise AIError(meta["stima"].get("msg") or "Non riesco a stimare il costo.")
    _save(folder, meta)
    return folder


def generate_scene(folder: Path, log=print) -> dict:
    start(log)
    meta = _meta(folder)
    inp = folder / "input"
    user = meta["testo"]
    exact = meta.get("riferimento", "esatto") != "spunto"
    est = meta["stima"]
    src = inp / "soggetto.mp4"
    total = meta["durata"]
    ff = vault.ffmpeg() or "ffmpeg"
    # the place: your image as it is, or one drawn from all the «spunto» images
    if exact:
        place_png = inp / meta["contesti"][0]
    else:
        text = ("Create one empty place, with no people, inspired by these images: the kind of place, style, materials, colours and light. "
                "A realistic photo, eye level, as a background for a person talking to camera. ") + user
        _image([inp / c for c in meta["contesti"]], text, PLACE_MODEL, meta["formato"], folder / "posto.png", log, "Posto dalle immagini")
        place_png = _frame(folder / "posto.png", inp / "posto.png", meta["formato"])
    m = _pick("ambientazione_video", meta["modello"])
    vid = _upload(src, "video/mp4")
    place = _upload(place_png)
    seed = random.randint(1, 2_000_000_000)
    keep = "Keep the person exactly as in the original video: face, mouth and lip movements, expressions and gestures. "
    how = "Use the reference image as the new background and relight the person to match its light. "
    if m["nodo"] == "BriaVideoReplaceBackground":
        node = {"video": ["1", 0], "seed": seed, "background_image": ["2", 0]}
    elif m["nodo"] == "BeebleSwitchXVideoEdit":
        node = {"video": ["1", 0], "prompt": (how + user)[:2000], "alpha_mode": "auto",
                "max_resolution": "1080p" if meta["risoluzione"] == "1080p" else "720p", "seed": seed, "reference_image": ["2", 0]}
    elif m["nodo"] == "KlingOmniProEditVideoNode":
        node = {"model_name": "kling-v3-omni", "prompt": how + keep + user, "video": ["1", 0], "keep_original_sound": False,
                "reference_images": ["2", 0], "resolution": "1080p" if meta["risoluzione"] == "1080p" else "720p", "seed": seed}
    else:
        raise AIError(f"Il nodo {m['nodo']} non è ancora gestito.")
    wf = {"1": {"class_type": "LoadVideo", "inputs": {"file": vid}},
          "2": {"class_type": "LoadImage", "inputs": {"image": place}},
          "3": {"class_type": m["nodo"], "inputs": node},
          "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0], "filename_prefix": "capwizard/ai", "format": "auto", "codec": "auto"}}}
    log(f"Ambientazione: {m['nome']} su {total:g} s…")
    t0 = time.time()
    changed = _download(_run(wf, log, "Ambientazione"), folder / "video_cambiato.mp4")
    log(f"Ambientazione: pronta in {int(time.time() - t0)} s. "
        + ("La faccio arrivare a fine clip e rimetto la voce…" if meta.get("arrivo") == "fine" else "Rimetto la voce originale…"))
    _finish_scene(src, changed, folder / "ambientazione.mp4", total, meta.get("fps") or 24, meta.get("arrivo") == "fine", ff)
    meta.update(video="ambientazione.mp4", speso_usd=round(meta.get("speso_usd", 0) + est["usd"], 4))
    _save(folder, meta)
    return describe(folder)


def _finish_scene(src: Path, changed: Path, out: Path, total: float, fps: float, gradual: bool, ff: str) -> None:
    """The changed video with the original voice. «gradual»: it starts as the original and turns into the new place little
    by little, all the way to the last frame (the person is the same in both, so only the place changes)."""
    w, h = _size(src)
    norm = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,format=yuv420p"
    if gradual:
        # smoothstep over the whole clip: a slow start, a slow arrival
        p = f"(clip(T/{total},0,1)*clip(T/{total},0,1)*(3-2*clip(T/{total},0,1)))"
        graph = (f"[0:v]{norm},setpts=PTS-STARTPTS[a];[1:v]{norm},setpts=PTS-STARTPTS[b];"
                 f"[a][b]blend=all_expr='A*{p}+B*(1-{p})',format=yuv420p[v]")
    else:
        graph = f"[0:v]{norm}[v]"
    r = subprocess.run([ff, "-v", "error", "-y", "-i", str(changed), "-i", str(src), "-filter_complex", graph,
                        "-map", "[v]", "-map", "1:a?", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-t", f"{total}", str(out)], capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise AIError(f"Non riesco a finire il video: {r.stderr.strip()[:200]}")


def hide(job_id: str) -> dict:
    """The ✕ of a result: off the app's list; its folder and files stay where they are."""
    folder = folder_of(job_id)
    meta = _meta(folder)
    meta["nascosto"] = True
    _save(folder, meta)
    return {"ok": True}
