"""Whisper transcription with word-level timestamps.

Whisper is good at *what* was said and loose about *when* a word ends: the last
word before a pause is routinely stretched over the silence that follows, by
half a second or more. Cuts and pause detection are made on those edges, so
after transcribing we re-fit every word to the audio itself (`snap_to_speech`).
"""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np

from .models import SourceClip, Word
from .probe import extract_audio_wav


_MODEL_CACHE: dict[str, object] = {}


def _get_model(name: str):
    if name not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        # int8 on CPU — fastest reasonable default on Mac without GPU
        _MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL_CACHE[name]


def speech_envelope(wav: Path) -> tuple[np.ndarray, float]:
    """Loudness of the audio in dB, one value every 10 ms, and the level above
    which a frame counts as voice: a quarter of the way from the room's noise
    floor to the speaker's loud level, never less than 28 dB under the latter
    (so a take with no pauses to measure the floor on doesn't lose quiet words).
    """
    with wave.open(str(wav), "rb") as f:
        rate, raw = f.getframerate(), f.readframes(f.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    hop = rate // 100
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32), 0.0
    rms = np.sqrt((audio[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    return db, float(min(floor + 0.25 * (loud - floor), loud - 28))


def snap_to_speech(words: list[Word], db: np.ndarray, threshold: float,
                   min_silence: float = 0.15, margin: float = 0.03) -> float:
    """Re-fit each word's start and end to where its sound really is.

    A word that carries `min_silence` or more of silence at either edge is
    trimmed to the voice (plus a small margin); a word whose sound runs on past
    its end is extended to where the voice stops, without touching the next
    word. Words with no audible sound at all are left alone. Returns the
    seconds of silence removed from the words' edges.
    """
    if len(db) == 0:
        return 0.0
    voiced = db > threshold
    removed = 0.0
    for i, w in enumerate(words):
        a, b = max(0, int(w.start * 100)), min(len(db), int(round(w.end * 100)))
        inside = np.flatnonzero(voiced[a:b])
        if len(inside) == 0:
            continue
        first, last = (a + inside[0]) / 100, (a + inside[-1] + 1) / 100
        new_start, new_end = w.start, w.end
        if first - w.start >= min_silence:
            new_start = first - margin
        if w.end - last >= min_silence:
            new_end = last + margin
        elif w.end - last < 0.02:
            # the voice is still going when Whisper says the word is over:
            # follow it to where it stops (never into the next word)
            limit = words[i + 1].start if i + 1 < len(words) else len(db) / 100
            stop = b
            while stop < min(len(db), int(limit * 100), b + 30) and voiced[stop]:
                stop += 1
            new_end = max(new_end, stop / 100)
        removed += (new_start - w.start) + max(0.0, w.end - new_end)
        w.start, w.end = new_start, max(new_end, new_start + 0.02)
    return removed


def transcribe(clip: SourceClip, model_name: str = "large-v3", language: str | None = "it") -> float:
    """Populate clip.words with word-level timestamps fitted to the audio.
    Returns the seconds of silence trimmed off the words' edges."""
    model = _get_model(model_name)

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        extract_audio_wav(clip.path, wav)
        segments, _ = model.transcribe(
            str(wav),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        words: list[Word] = []
        for seg in segments:
            if not seg.words:
                continue
            for w in seg.words:
                txt = w.word.strip()
                if not txt:
                    continue
                words.append(Word(start=float(w.start), end=float(w.end), text=txt))
        removed = snap_to_speech(words, *speech_envelope(wav)) if words else 0.0
        clip.words = words
        return removed
