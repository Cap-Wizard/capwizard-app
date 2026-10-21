"""Funzioni comuni: audio in cache, livelli di voce, trascrizione (mlx-whisper sul chip Apple,
con faster-whisper come riserva). Importato dagli altri script."""
from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

CACHE = Path(os.environ.get("REEL_CACHE", Path.home() / ".cache" / "reel-capcut"))
CACHE.mkdir(parents=True, exist_ok=True)
SR = 16000
MODELLO_MLX = os.environ.get("REEL_WHISPER", "mlx-community/whisper-medium-mlx")


def wav(clip: str) -> np.ndarray:
    """Audio mono 16 kHz della clip, estratto una volta e tenuto in cache (.npy)."""
    p = Path(clip)
    dest = CACHE / f"{abs(hash(str(p.resolve())))}_{p.stem}.npy"
    if dest.exists():
        return np.load(dest)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(p), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    np.save(dest, a)
    return a


def banda_voce(a: np.ndarray) -> np.ndarray:
    """Filtro 120-4000 Hz (taglia rimbombi e fruscio) via FFT."""
    if len(a) == 0:
        return a
    f = np.fft.rfft(a)
    fr = np.fft.rfftfreq(len(a), 1 / SR)
    f[(fr < 120) | (fr > 4000)] = 0
    return np.fft.irfft(f, len(a)).astype(np.float32)


@lru_cache(maxsize=64)
def db(clip: str) -> np.ndarray:
    """Livello in dB ogni 10 ms nella banda della voce."""
    a = banda_voce(wav(clip))
    n = len(a) // 160
    if n == 0:
        return np.zeros(0)
    return 20 * np.log10(np.sqrt((a[: n * 160].reshape(n, 160) ** 2).mean(axis=1)) + 1e-9)


def probe(clip: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", clip],
                       capture_output=True, text=True)
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    rot = None
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if rot is None and "rotate" in v.get("tags", {}):
        rot = ((-int(v["tags"]["rotate"]) + 180) % 360) - 180   # tag rotate=270 -> 90, rotate=90 -> -90
    num, den = v["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    nb = int(v.get("nb_frames") or 0)
    return {"durata": float(j["format"]["duration"]), "fps": round(fps, 3), "fotogrammi": nb,
            "durata_us": int(nb * 1e6 / fps) if nb else int(float(j["format"]["duration"]) * 1e6),
            "larghezza": v["width"], "altezza": v["height"], "rotazione": rot,
            "audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def rotazione_capcut(info: dict) -> dict:
    """Come mettere la clip in CapCut: rotation 90 = normale; -90 = capovolta; nessun tag = orizzontale."""
    r = info["rotazione"]
    if r in (90, -270):
        return {"rot": 0, "orizzontale": False, "nota": "normale"}
    if r in (-90, 270):
        return {"rot": 180, "orizzontale": False, "nota": "capovolta: rotation 180"}
    if r is None and info["larghezza"] > info["altezza"]:
        return {"rot": 270, "orizzontale": True, "nota": "orizzontale senza tag: 1920x1080, rotation 270, scala x1.778 (verificare il verso)"}
    return {"rot": 0, "orizzontale": False, "nota": f"rotazione {r}: controllare"}


_modello = None


def trascrivi(clip: str, a: float | None = None, b: float | None = None) -> list[list]:
    """Parole [parola, inizio, fine] (secondi della clip) nella finestra a-b."""
    global _modello
    x = wav(clip)
    s0 = int((a or 0) * SR)
    s1 = int(b * SR) if b is not None else len(x)
    seg = x[s0:s1]
    if len(seg) < SR * 0.15:
        return []
    off = s0 / SR
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(seg, path_or_hf_repo=MODELLO_MLX, language="it", word_timestamps=True,
                                   condition_on_previous_text=False, verbose=None)
        return [[w["word"].strip(), round(off + w["start"], 2), round(off + w["end"], 2)]
                for s in r["segments"] for w in s.get("words", [])]
    except ImportError:
        from faster_whisper import WhisperModel
        if _modello is None:
            _modello = WhisperModel("medium", device="cpu", compute_type="int8")
        segs, _ = _modello.transcribe(seg, language="it", word_timestamps=True, vad_filter=False,
                                      condition_on_previous_text=False)
        return [[w.word.strip(), round(off + w.start, 2), round(off + w.end, 2)] for s in segs for w in s.words or []]


def livello(d: np.ndarray, a: float, b: float, q: int = 90) -> float:
    s, e = int(a * 100), max(int(a * 100) + 1, int(b * 100))
    v = d[s:e]
    return float(np.percentile(v, q)) if len(v) else -99.0


def tratti_forti(clip: str, a: float, b: float, soglia: float | None = None, unisci: float = 0.35,
                 minimo: float = 0.15) -> list[tuple[float, float]]:
    """Tratti di voce piena (chi parla col microfono addosso) nella finestra, con 0,06s prima e 0,08s dopo.
    soglia: dB; di default 14 dB sotto il livello forte della clip, mai sotto -32."""
    d = db(clip)
    if soglia is None:
        forte = np.percentile(d[d > -60], 95) if (d > -60).any() else -20
        soglia = max(-32.0, forte - 14)
    s, e = int(a * 100), min(int(b * 100), len(d))
    on = np.convolve((d[s:e] > soglia).astype(float), np.ones(8) / 8, "same") >= 0.5
    runs, st = [], None
    for k, v in enumerate(on):
        if v and st is None:
            st = k
        if not v and st is not None:
            runs.append([st, k]); st = None
    if st is not None:
        runs.append([st, len(on)])
    mg = []
    for r in runs:
        if mg and r[0] - mg[-1][1] < unisci * 100:
            mg[-1][1] = r[1]
        else:
            mg.append(r)
    return [(round(a + r0 / 100 - 0.06, 2), round(a + r1 / 100 + 0.08, 2)) for r0, r1 in mg if r1 - r0 >= minimo * 100]


def fondo(d: np.ndarray) -> float:
    """Rumore di fondo della clip (10° percentile)."""
    return float(np.percentile(d, 10)) if len(d) else -70.0
