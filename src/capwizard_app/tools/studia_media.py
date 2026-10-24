"""Prepares reference videos of a new agency/client so Claude can study their editing style.
For every video (dropped files/folders, or Instagram/TikTok links downloaded with yt-dlp):
duration, cut times (scene changes), speech transcript with word timings, loudness, a contact sheet
(1 frame every 0.5 s) and a few big frames of the first seconds (where the title is).
usage: python studia_media.py <out dir> <inputs json: {"file": [...], "link": [...]}>
Run with a Python that has mlx_whisper and yt-dlp (vault.tools_python())."""
import glob, json, os, re, shutil, subprocess, sys

FF = os.environ.get("CAPWIZARD_FFMPEG") or shutil.which("ffmpeg") or os.path.expanduser("~/Desktop/VIDEO CLAUDE/720 STUDI0/tools/ffmpeg")
YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
os.environ["PATH"] = os.path.dirname(FF) + os.pathsep + os.environ.get("PATH", "")  # whisper calls "ffmpeg"
VIDEO = (".mov", ".mp4", ".m4v", ".webm", ".mkv")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def scarica(links, out):
    got, errs = [], []
    os.makedirs(out, exist_ok=True)
    for i, url in enumerate(links):
        url = url.strip()
        if not url:
            continue
        # a profile link → its latest reels; a single reel/post → that video
        r = run([YTDLP, "--no-warnings", "--playlist-items", "1-8", "-f", "mp4/best", "-o", f"{out}/link{i + 1}_%(id)s.%(ext)s", url])
        new = sorted(glob.glob(f"{out}/link{i + 1}_*"))
        got += [p for p in new if p.lower().endswith(VIDEO)]
        if not new:
            errs.append({"link": url, "errore": (r.stderr or r.stdout).strip().splitlines()[-1:] or ["nessun video"]})
    return got, errs


def durata(p):
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", run([FF, "-hide_banner", "-i", p]).stderr)
    return round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2) if m else 0


def tagli(p):
    r = run([FF, "-hide_banner", "-i", p, "-vf", "scale=270:-2,select='gt(scene,0.32)',showinfo", "-an", "-f", "null", "-"]).stderr
    return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", r)]


def loudness(p):
    r = run([FF, "-hide_banner", "-i", p, "-af", "ebur128", "-vn", "-f", "null", "-"]).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    return float(m[-1]) if m else None


def trascrivi(p):
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(p, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True, language="it")
        words = [{"w": w["word"].strip(), "s": round(w["start"], 2), "e": round(w["end"], 2)} for s in r["segments"] for w in s.get("words", [])]
        return r.get("text", "").strip(), words
    except Exception as e:
        return f"(trascrizione non riuscita: {e})", []


def immagini(p, out, d):
    sheet = f"{out}_foglio.jpg"
    run([FF, "-v", "error", "-y", "-i", p, "-vf", "fps=2,scale=216:-2,tile=6x5", "-frames:v", "1", "-q:v", "4", sheet])
    sheets = [sheet]
    if d > 15:  # second sheet for 15–30 s
        s2 = f"{out}_foglio2.jpg"
        run([FF, "-v", "error", "-y", "-ss", "15", "-i", p, "-vf", "fps=2,scale=216:-2,tile=6x5", "-frames:v", "1", "-q:v", "4", s2])
        sheets.append(s2)
    big = []
    for t in (0.4, 1.5, 3.0, d / 2):
        if t < d:
            f = f"{out}_t{t:.1f}.jpg"
            run([FF, "-v", "error", "-y", "-ss", str(t), "-i", p, "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", f])
            big.append(f)
    return [s for s in sheets if os.path.exists(s)], [b for b in big if os.path.exists(b)]


def main(out, inp):
    os.makedirs(out, exist_ok=True)
    files = []
    for f in inp.get("file", []):
        if os.path.isdir(f):
            files += sorted(p for p in glob.glob(f + "/**/*", recursive=True) if p.lower().endswith(VIDEO) and "/._" not in p)
        elif f.lower().endswith(VIDEO):
            files.append(f)
    dl, errs = scarica(inp.get("link", []), out + "/scaricati") if inp.get("link") else ([], [])
    files = (files + dl)[:12]
    res = {"video": [], "link_non_scaricati": errs}
    for i, p in enumerate(files):
        base = f"{out}/v{i + 1}"
        d = durata(p)
        cuts = tagli(p)
        testo, words = trascrivi(p)
        sheets, big = immagini(p, base, d)
        res["video"].append({"file": p, "durata": d, "tagli": cuts, "n_tagli": len(cuts), "secondi_per_taglio": round(d / (len(cuts) + 1), 2),
                             "loudness_lufs": loudness(p), "parlato": testo, "parole": words[:400],
                             "fogli_provini (1 frame ogni 0,5 s)": sheets, "fotogrammi grandi": big})
        print(f"[{i + 1}/{len(files)}] {os.path.basename(p)} · {d}s · {len(cuts)} tagli", flush=True)
    json.dump(res, open(out + "/analisi.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "video": len(files), "analisi": out + "/analisi.json", "link_non_scaricati": errs}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]))
