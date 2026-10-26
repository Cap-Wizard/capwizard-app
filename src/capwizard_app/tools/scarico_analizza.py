"""Inventory of a shooting for the scarico (Kora): clips in camera order, duration, black separator clips, transcript.
usage: python scarico_analizza.py <out dir> <inputs json: ["file or folder", ...]>
Writes <out>/inventario.json: [{"n", "path", "nome", "camera", "durata", "yavg", "bit10", "nera", "testo"}, ...]
and <out>/tx/<n>.txt (one per clip, so a stopped run resumes where it was).
Run with a Python that has mlx_whisper (vault.tools_python())."""
import glob, json, os, re, shutil, subprocess, sys

FF = os.environ.get("CAPWIZARD_FFMPEG") or shutil.which("ffmpeg") or os.path.expanduser("~/Desktop/VIDEO CLAUDE/720 STUDI0/tools/ffmpeg")
os.environ["PATH"] = os.path.dirname(FF) + os.pathsep + os.environ.get("PATH", "")  # whisper calls "ffmpeg"
VIDEO = (".mov", ".mp4", ".m4v", ".mkv", ".mts", ".webm")
MODELS = ("mlx-community/whisper-medium-mlx", "mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-large-v3-mlx")


def clips(inputs):
    out = []
    for p in inputs:
        files = [p] if os.path.isfile(p) else glob.glob(os.path.join(p, "**", "*"), recursive=True)
        out += [f for f in files if f.lower().endswith(VIDEO) and not os.path.basename(f).startswith((".", "._"))]
    seen, uniq = set(), []
    for f in out:
        if os.path.realpath(f) not in seen:
            seen.add(os.path.realpath(f))
            uniq.append(f)
    # camera order: C2751.MP4 → 2751 (the last number in the name)
    def key(f):
        nums = re.findall(r"\d+", os.path.splitext(os.path.basename(f))[0])
        return (int(nums[-1]) if nums else 10 ** 9, os.path.basename(f))
    return sorted(uniq, key=key)


def probe(f):
    """Duration and whether the video is 10 bit, from ffmpeg's header dump."""
    err = subprocess.run([FF, "-hide_banner", "-i", f], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Video: .*", err)
    return dur, bool(v and re.search(r"p10|10le|10be", v[0]))


def yavg(f):
    """Mean luma of the clip (signalstats). 10 bit black is ~64-90, normal clips stay above ~350; 8 bit black ~16-25."""
    err = subprocess.run([FF, "-hide_banner", "-i", f, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    vals = [float(x) for x in re.findall(r"YAVG=([\d.]+)", err)]
    return round(sum(vals) / len(vals), 1) if vals else None


def whisper_model():
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    for m in MODELS:
        if os.path.isdir(os.path.join(hub, "models--" + m.replace("/", "--"))):
            return m
    return MODELS[0]


def main(out, inputs):
    os.makedirs(os.path.join(out, "tx"), exist_ok=True)
    files = clips(inputs)
    print(f"{len(files)} clip trovate", flush=True)
    rows = []
    for i, f in enumerate(files, 1):
        dur, bit10 = probe(f)
        row = {"n": i, "path": f, "nome": os.path.basename(f), "camera": (re.findall(r"\d+", os.path.splitext(os.path.basename(f))[0]) or [""])[-1],
               "durata": round(dur, 2), "bit10": bit10, "yavg": None, "nera": False}
        if dur and dur < 1.2:   # separators are ~0.5 s; real b-roll is never under ~1.5 s
            row["yavg"] = yavg(f)
            dark = 120 if bit10 else 40
            row["nera"] = row["yavg"] is not None and row["yavg"] < dark
            row["mano"] = not row["nera"] and dur <= 0.8   # lens covered with a hand: maybe a separator
        rows.append(row)
    nere = [r["nome"] for r in rows if r["nera"]]
    print(f"Clip nere separatrici: {len(nere)} ({', '.join(nere[:12])}{'…' if len(nere) > 12 else ''})", flush=True)

    try:
        import mlx_whisper
        model = whisper_model()
        print(f"Trascrivo con {model.split('/')[-1]}…", flush=True)
    except ImportError:
        mlx_whisper = None
        print("⚠ mlx_whisper non c'è su questo Mac: niente trascrizione, divido solo con le clip nere", flush=True)
    for r in rows:
        tx = os.path.join(out, "tx", f"{r['n']:04d}.txt")
        if r["nera"] or r["durata"] < 1.2 or (not mlx_whisper and not os.path.exists(tx)):
            r["testo"] = ""
            continue
        if not os.path.exists(tx):
            wav = os.path.join(out, f"{r['n']:04d}.wav")
            subprocess.run([FF, "-v", "error", "-y", "-i", r["path"], "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
            try:
                res = mlx_whisper.transcribe(wav, path_or_hf_repo=model, language="it", condition_on_previous_text=False)
                text = (res.get("text") or "").strip()
            finally:
                os.remove(wav)
            with open(tx, "w") as fh:
                fh.write(text)
        r["testo"] = open(tx).read()
        print(f"[{r['n']}/{len(rows)}] {r['nome']}: {r['testo'][:90] or '(senza parlato)'}", flush=True)
    with open(os.path.join(out, "inventario.json"), "w") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"Salvato: {out}/inventario.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]))
