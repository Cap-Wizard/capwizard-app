"""Higgsfield API from the command line, for Claude during a montage (key HF_KEY from the local env file, never printed).

usage:
  python higgsfield_api.py image --image <render or frame> --prompt "<edit instruction>" [--ratio 9:16] --out <file.png>
  python higgsfield_api.py video --image <photo> --prompt "<camera move>" [--seconds 5] --out <file.mp4>
image = Qwen Image 3 Edit (edits/extends a picture), video = Kling 3.0 Pro image-to-video without sound
(~$0.112 per second). Prints the saved file, or an error starting with ERRORE.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KEY_FILES = [os.environ.get("CAPWIZARD_HF_ENV", ""),
             str(Path.home() / "Library/Application Support/CapWizard/higgsfield.env"),
             str(Path.home() / "Desktop/VIDEO CLAUDE/720 STUDI0/higgsfield/.env.local")]
FF = os.environ.get("CAPWIZARD_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def key() -> bool:
    from dotenv import load_dotenv
    for f in KEY_FILES:
        if not os.environ.get("HF_KEY") and f and Path(f).is_file():
            load_dotenv(f, override=False)
    return bool(os.environ.get("HF_KEY"))


def upload(path: str) -> str:
    import higgsfield_client as hf
    tmp = Path(tempfile.mkdtemp()) / "in.jpg"           # a light JPG uploads reliably
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-q:v", "2", str(tmp)], check=True)
    return hf.upload_file(tmp)


def run(model: str, args: dict) -> dict:
    import higgsfield_client as hf
    last = {}
    res = hf.subscribe(model, arguments=args, on_queue_update=lambda s: last.update(s=s))
    s = last.get("s")
    if isinstance(s, (hf.Failed, hf.NSFW, hf.Cancelled)):
        raise RuntimeError(f"terminato come {type(s).__name__}")
    return res or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["image", "video"])
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not key():
        print("ERRORE: manca la chiave HF_KEY dell'API Higgsfield", file=sys.stderr)
        return 2
    try:
        src = upload(a.image)
        if a.kind == "image":
            res = run("alibaba/qwen-image-3/edit", {"prompt": a.prompt, "image_urls": [src], "aspect_ratio": a.ratio,
                                                    "resolution": "2k"})
            url = (res.get("images") or [{}])[0].get("url")
        else:
            res = run("kling-video/v3.0/pro/image-to-video", {"image_url": src, "prompt": a.prompt,
                                                              "duration": a.seconds, "sound": "off", "cfg_scale": 0.5})
            url = (res.get("video") or {}).get("url")
        if not url:
            raise RuntimeError(f"nessun file nel risultato: {res}")
        subprocess.run(["curl", "-sfL", url, "-o", a.out], check=True)
    except Exception as e:  # noqa: BLE001
        print(f"ERRORE: {e}", file=sys.stderr)
        return 1
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
