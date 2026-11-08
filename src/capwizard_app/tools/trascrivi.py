"""Word-by-word transcription of every clip of a folder (Voz by Desert Ant on the Apple chip, since 07/11/2026;
mlx-whisper only as a fallback on a Mac without Voz).
usage: python trascrivi.py <clip folder> <out words.json> [file with the clips to skip, one path per line]
Writes {clip name: [[word, start, end], ...]} and prints the text with start times.
Run with vault.tools_python()."""
import glob, json, os, shutil, subprocess, sys

import voz

FF = os.environ.get("CAPWIZARD_FFMPEG") or shutil.which("ffmpeg")
os.environ["PATH"] = os.path.dirname(FF) + os.pathsep + os.environ.get("PATH", "")   # whisper calls "ffmpeg"
MODEL = os.environ.get("REEL_WHISPER", "mlx-community/whisper-large-v3-turbo")


def main(folder, out, skip=None):
    res = {}
    tolte = {os.path.normpath(l) for l in open(skip).read().split("\n") if l} if skip else set()
    clips = sorted(f for f in glob.glob(folder + "/*") + glob.glob(folder + "/*/*")
                   if f.lower().endswith((".mov", ".mp4", ".m4v")) and not os.path.basename(f).startswith("._") and os.path.normpath(f) not in tolte)
    for f in clips:
        w = out + "." + os.path.basename(f) + ".wav"
        subprocess.run([FF, "-v", "error", "-y", "-i", f, "-ac", "1", "-ar", "16000", w], check=True)
        if voz.disponibile():
            r = voz.trascrivi(w)
        else:
            import mlx_whisper
            r = mlx_whisper.transcribe(w, path_or_hf_repo=MODEL, language="it", word_timestamps=True)
        res[os.path.relpath(f, folder).rsplit(".", 1)[0]] = [(x["word"].strip(), round(float(x["start"]), 2), round(float(x["end"]), 2))
                                                             for s in r["segments"] for x in s.get("words", [])]
        os.remove(w)
        print(f"**{os.path.relpath(f, folder)}**: " + " ".join(f"{a}[{b:.1f}]" for a, b, _ in res[os.path.relpath(f, folder).rsplit('.', 1)[0]]), flush=True)
    json.dump(res, open(out, "w"), ensure_ascii=False)
    print(f"Salvato: {out} (clip → [parola, inizio, fine])")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
