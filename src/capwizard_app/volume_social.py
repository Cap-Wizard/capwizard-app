"""Social videos must sound loud enough (Gabriele, 15/11/2026, after comparing «ETOILE - 2» at -19.4 and at -14
LUFS on the phone: «quella a -14»). CapCut cannot export to a loudness target, so CapWizard watches the usual
export folders and, when a video named like a CapCut project appears there, writes next to it
«<name> (-14 LUFS).mp4»: same picture (copied, not re-encoded), audio at -14 LUFS integrated with true peak at
-1.5 dBTP (ffmpeg loudnorm, two passes, linear). The original export is never touched or deleted.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from capwizard_app.capcut import CAPCUT_PROJECTS_DIR

TARGET_I, TARGET_TP = -14.0, -1.5
SUFFIX = " (-14 LUFS)"
FOLDERS = [Path.home() / "Desktop", Path.home() / "Movies", Path.home() / "Downloads",
           Path.home() / "Movies/CapCut/Exports"]
EXT = {".mp4", ".mov"}
STATE = Path.home() / "Library/Application Support/CapWizard/volume_social.json"
NEW_FOR = 6 * 3600            # only exports from the last 6 hours (older files are not new exports)


def misura(path: str) -> dict:
    """First loudnorm pass: input_i, input_tp, input_lra, input_thresh, target_offset (strings)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-af",
                          f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA=11:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return json.loads(out[out.rindex("{"):out.rindex("}") + 1])


def normalizza(src: str, dst: str) -> dict:
    """Write dst with the audio at -14 LUFS / -1.5 dBTP and the video copied. Returns the first-pass numbers."""
    m = misura(src)
    af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          f"linear=true,aresample=48000")
    tmp = Path(dst).with_name("." + Path(dst).name + ".part.mp4")
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0",
                        "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart",
                        str(tmp)], capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(r.stderr.strip()[-300:])
    tmp.rename(dst)
    return m


def gia_a_posto(m: dict) -> bool:
    return abs(float(m["input_i"]) - TARGET_I) <= 1.0 and float(m["input_tp"]) <= TARGET_TP + 0.2


def _progetti() -> set[str]:
    try:
        return {d.name for d in CAPCUT_PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.upper().startswith("TEMPLATE")}
    except OSError:
        return set()


def candidati(fatti: set[str]) -> list[Path]:
    nomi, now, out = _progetti(), time.time(), []
    for d in FOLDERS:
        try:
            files = list(d.iterdir())
        except OSError:
            continue
        for f in files:
            if (f.suffix.lower() in EXT and not f.name.startswith(".") and not f.stem.endswith(SUFFIX)
                    and f.stem in nomi and str(f) not in fatti and now - f.stat().st_mtime < NEW_FOR
                    and not f.with_name(f.stem + SUFFIX + ".mp4").exists()):
                out.append(f)
    return out


def _carica() -> set[str]:
    try:
        return set(json.loads(STATE.read_text()))
    except (OSError, ValueError):
        return set()


def _salva(fatti: set[str]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(fatti)[-500:], ensure_ascii=False))


def avvia(notify=None, log=print, every: float = 15.0) -> threading.Thread:
    """Background watcher. notify(title, body, ok) is called for each copy made (or failed)."""
    def loop():
        fatti = _carica()
        sizes: dict[str, int] = {}
        while True:
            try:
                for f in candidati(fatti):
                    size = f.stat().st_size
                    if sizes.get(str(f)) != size:        # still being written: wait for a stable size
                        sizes[str(f)] = size
                        continue
                    dst = f.with_name(f.stem + SUFFIX + ".mp4")
                    try:
                        m = misura(str(f))
                        if gia_a_posto(m):
                            log(f"volume social: «{f.name}» è già a {m['input_i']} LUFS, nessuna copia")
                        else:
                            normalizza(str(f), str(dst))
                            msg = f"«{dst.name}»: da {m['input_i']} a {TARGET_I:g} LUFS, picco {TARGET_TP:g} dBTP"
                            log("volume social: " + msg)
                            if notify:
                                notify("Audio pronto per i social", msg, True)
                    except Exception as e:  # noqa: BLE001
                        log(f"volume social: «{f.name}» non normalizzato: {e}")
                        if notify:
                            notify("Audio social non fatto", f"«{f.name}»: {e}", False)
                    fatti.add(str(f))
                    sizes.pop(str(f), None)
                    _salva(fatti)
            except Exception as e:  # noqa: BLE001  (never stop the watcher)
                log(f"volume social: {e}")
            time.sleep(every)
    t = threading.Thread(target=loop, daemon=True, name="volume-social")
    t.start()
    return t
