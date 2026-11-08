"""Trascrizione con Voz (Desert Ant Labs), sul chip Apple. L'audio resta sul Mac; al caricamento del modello
l'SDK manda a Desert Ant un controllo di licenza (ID installazione, app, modello: niente audio né testo).

Un solo punto per tutti gli strumenti che prima usavano Whisper (07/11/2026).
Dà i risultati nel formato che quegli strumenti già leggono:
- trascrivi(file)            -> dizionario come mlx_whisper.transcribe (text, segments, words)
- trascrivi_array(x, sr)     -> idem, da un array numpy float (mono)
- parole(file)               -> [[parola, inizio, fine], ...]
- formato_whispercpp(r)      -> dizionario come il JSON di whisper-cli (-oj / -ojf)
Uso da terminale: voz-trascrivi <file> [--formato json|whispercpp|txt|srt] [-o uscita]

Voz riconosce da solo la lingua (25 lingue europee, italiano compreso) e non ha un prompt.
Non segnala musica cantata né rumori: chi ne ha bisogno lo deve sapere.

Le altre AI di Desert Ant (08/11/2026), con l'audio dei video (anche Sony) convertito da solo:
- pensate(file)              -> [{start, end, type, confidence}]  (Uhm: «eee», «ehm», «mmm»)
- chi_parla(file)            -> {speakerCount, speakers, turns}   (Who, in prova: tende a contare più persone)
- momenti(file, quanti)      -> [{start, end, score, text}]        (Clips: i pezzi migliori di un parlato)
- pulisci_voce(file, uscita) -> percorso del .wav pulito            (Clear: rumore, eco, volume)
- valuta_foto(file)          -> {scores, faces, people, tags...}   (Eye: nitidezza, copertina, volti)
- titolo(testo), argomento(testo), emoji(testo)                    (Title, Gist, Emo)
Uso da terminale: ant <modello> <file|testo> [opzioni di desertant]"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

CANDIDATI = [os.environ.get("VOZ_BIN", ""), str(Path.home() / ".local/bin/desertant"),
             str(Path.home() / ".local/share/desertant/desertant"), "/opt/homebrew/bin/desertant"]
FFMPEG_CANDIDATI = [os.environ.get("FFMPEG", ""), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]


class VozError(RuntimeError):
    pass


def _bin(candidati, nome):
    for c in candidati:
        if c and os.path.exists(c):
            return c
    w = shutil.which(nome)
    if w:
        return w
    raise VozError(f"{nome} non trovato")


def disponibile() -> bool:
    try:
        _bin(CANDIDATI, "desertant")
        return True
    except VozError:
        return False


def _voz_json(wav_path: str) -> dict:
    env = dict(os.environ, DESERTANT_NO_UPDATE_CHECK="1")
    r = subprocess.run([_bin(CANDIDATI, "desertant"), "voz", wav_path, "--json", "--quiet", "--no-color"],
                       capture_output=True, text=True, env=env, timeout=3600)
    if "no speech found" in (r.stderr + r.stdout):
        return {"text": "", "sentences": [], "words": []}
    if r.returncode != 0 or not r.stdout.strip():
        raise VozError(f"Voz non è riuscito: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout)


def lingua(file) -> str | None:
    """Lingua parlata con Ear di Desert Ant (102 lingue), codice tipo "it". None se non la trova."""
    env = dict(os.environ, DESERTANT_NO_UPDATE_CHECK="1")
    try:
        r = subprocess.run([_bin(CANDIDATI, "desertant"), "ear", str(file), "--json", "--quiet", "--no-color"],
                           capture_output=True, text=True, env=env, timeout=600)
        return json.loads(r.stdout).get("language") if r.returncode == 0 else None
    except (VozError, ValueError, subprocess.TimeoutExpired):
        return None


def _a_whisper(v: dict) -> dict:
    """Frasi di Voz -> segments di Whisper; parole assegnate alla frase in cui cadono."""
    words = v.get("words", [])
    sents = v.get("sentences")
    if sents is None:
        sents = [{"start": 0, "end": v.get("durationSec", 0), "text": v.get("text", "")}]
    segs, k = [], 0
    for i, s in enumerate(sents):
        ws = []
        while k < len(words) and (words[k]["start"] + words[k]["end"]) / 2 <= s["end"] + 0.05:
            w = words[k]
            ws.append({"word": " " + w["text"], "start": w["start"], "end": w["end"], "probability": 1.0})
            k += 1
        segs.append({"id": i, "start": s["start"], "end": s["end"], "text": " " + s["text"].strip(), "words": ws})
    if k < len(words) and segs:  # parole rimaste dopo l'ultima frase
        segs[-1]["words"] += [{"word": " " + w["text"], "start": w["start"], "end": w["end"], "probability": 1.0}
                              for w in words[k:]]
    return {"text": " " + v.get("text", "").strip(), "segments": segs, "language": None, "motore": "voz"}


def _a_wav(src: str, dst: str):
    r = subprocess.run([_bin(FFMPEG_CANDIDATI, "ffmpeg"), "-v", "error", "-y", "-i", src, "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", dst], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        raise VozError(f"ffmpeg non legge {src}: {r.stderr.strip()[-200:]}")


def trascrivi(file, con_lingua: bool = False, **_ignorati) -> dict:
    """Come mlx_whisper.transcribe(file, word_timestamps=True). Gli altri argomenti di Whisper si ignorano.
    con_lingua=True riempie "language" con Ear (qualche secondo in più)."""
    if not isinstance(file, (str, os.PathLike)):
        return trascrivi_array(file)
    with tempfile.TemporaryDirectory(prefix="voz-") as td:
        w = os.path.join(td, "audio.wav")
        _a_wav(str(file), w)
        r = _a_whisper(_voz_json(w))
        if con_lingua:
            r["language"] = lingua(w)
        return r


def trascrivi_array(x, sr: int = 16000, **_ignorati) -> dict:
    import numpy as np
    a = np.asarray(x, dtype=np.float32).reshape(-1)
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
    with tempfile.TemporaryDirectory(prefix="voz-") as td:
        w = os.path.join(td, "audio.wav")
        with wave.open(w, "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(pcm)
        if sr != 16000:
            w2 = os.path.join(td, "audio16.wav"); _a_wav(w, w2); w = w2
        return _a_whisper(_voz_json(w))


def parole(file) -> list:
    r = trascrivi(file)
    return [[w["word"].strip(), round(w["start"], 2), round(w["end"], 2)] for s in r["segments"] for w in s["words"]]


def formato_whispercpp(r: dict) -> dict:
    """Come il JSON di whisper-cli: transcription[].offsets in millisecondi, text, tokens (vuoti)."""
    return {"result": {"language": r.get("language")}, "motore": "voz",
            "transcription": [{"offsets": {"from": int(s["start"] * 1000), "to": int(s["end"] * 1000)},
                               "timestamps": {}, "text": s["text"], "tokens": []} for s in r["segments"]]}


# ------------------------------------------------------------------ altre AI di Desert Ant (08/11/2026)

def _da(args, timeout=1800) -> str:
    env = dict(os.environ, DESERTANT_NO_UPDATE_CHECK="1")
    r = subprocess.run([_bin(CANDIDATI, "desertant"), *args, "--json", "--quiet", "--no-color"],
                       capture_output=True, text=True, env=env, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise VozError(f"desertant {args[0]} non è riuscito: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def _audio(file, td, sr=16000) -> str:
    w = os.path.join(td, "audio.wav")
    r = subprocess.run([_bin(FFMPEG_CANDIDATI, "ffmpeg"), "-v", "error", "-y", "-i", str(file), "-vn", "-ac", "1",
                        "-ar", str(sr), "-c:a", "pcm_s16le", w], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(w):
        raise VozError(f"ffmpeg non legge {file}: {r.stderr.strip()[-200:]}")
    return w


def pensate(file) -> list:
    with tempfile.TemporaryDirectory(prefix="ant-") as td:
        return json.loads(_da(["uhm", _audio(file, td)])).get("fillers", [])


def chi_parla(file, nomi: str | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="ant-") as td:
        return json.loads(_da(["who", _audio(file, td)] + (["--names", nomi] if nomi else [])))


def momenti(file, quanti: int | None = None) -> list:
    with tempfile.TemporaryDirectory(prefix="ant-") as td:
        a = ["clips", _audio(file, td), "--select-only"] + (["--count", str(quanti)] if quanti else [])
        return json.loads(_da(a)).get("clips", [])


def pulisci_voce(file, uscita) -> str:
    with tempfile.TemporaryDirectory(prefix="ant-") as td:
        _da(["clear", _audio(file, td, 48000), "--output", str(uscita), "--force"])
    return str(uscita)


def valuta_foto(file) -> dict:
    return json.loads(_da(["eye", str(file)], timeout=600))


def titolo(testo: str) -> dict:
    return json.loads(_da(["title", testo], timeout=300))


def argomento(testo: str) -> list:
    return json.loads(_da(["gist", testo], timeout=300))


def emoji(testo: str) -> list:
    return json.loads(_da(["emo", testo], timeout=300))


AUDIO = {"voz", "clips", "clear", "uhm", "who", "ear"}


def ant(argv) -> int:
    """ant <modello> <file|testo> [opzioni]: come `da`, ma l'audio di video e file non WAV lo prepara da solo."""
    if not argv or argv[0] in ("-h", "--help"):
        print("ant <modello> <file|testo> [opzioni di desertant]\nmodelli: voz clips clear uhm who ear eye title gist emo")
        return 0
    m, rest = argv[0], list(argv[1:])
    env = dict(os.environ, DESERTANT_NO_UPDATE_CHECK="1")
    da = _bin(CANDIDATI, "desertant")
    if m in AUDIO and rest and os.path.isfile(rest[0]) and not rest[0].lower().endswith(".wav"):
        with tempfile.TemporaryDirectory(prefix="ant-") as td:
            src = rest[0]
            rest[0] = _audio(src, td, 48000 if m == "clear" else 16000)
            if m == "clear" and "--output" not in rest:
                rest += ["--output", str(Path(src).with_name(Path(src).stem + "_clear.wav"))]
            if m == "clips" and "--select-only" not in rest:
                rest.append("--select-only")  # i pezzi del video si tagliano con ffmpeg dai tempi
            return subprocess.run([da, m, *rest], env=env).returncode
    return subprocess.run([da, m, *rest], env=env).returncode


def _srt(r):
    def t(x):
        ms = int(round(x * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{t(s['start'])} --> {t(s['end'])}\n{s['text'].strip()}\n" for i, s in enumerate(r["segments"], 1))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="voz-trascrivi")
    ap.add_argument("file")
    ap.add_argument("--formato", default="json", choices=["json", "whispercpp", "txt", "srt"])
    ap.add_argument("-o", "--uscita")
    ap.add_argument("--lingua", action="store_true", help="riconosce anche la lingua con Ear")
    a = ap.parse_args(argv)
    try:
        r = trascrivi(a.file, con_lingua=a.lingua)
    except VozError as e:
        print(f"voz-trascrivi: {e}", file=sys.stderr); return 1
    out = {"json": lambda: json.dumps(r, ensure_ascii=False, indent=1),
           "whispercpp": lambda: json.dumps(formato_whispercpp(r), ensure_ascii=False, indent=1),
           "txt": lambda: r["text"].strip() + "\n",
           "srt": lambda: _srt(r)}[a.formato]()
    if a.uscita:
        Path(a.uscita).write_text(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    if Path(sys.argv[0]).name == "ant" or (len(sys.argv) > 1 and sys.argv[1] == "--ant"):
        sys.exit(ant([a for a in sys.argv[1:] if a != "--ant"]))
    sys.exit(main())
