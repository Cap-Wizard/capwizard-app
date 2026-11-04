"""Controllo audio di un progetto CapCut, con le "orecchie" di earshot.

Ricostruisce l'audio del progetto dal draft (voce, musica, sfx, con i volumi), poi controlla:
  - musica rispetto alla voce (regola di Daniel: ~18 dB sotto, misurata in LUFS con earshot)
  - parole tagliate e code corte nei tagli, pause lasciate (sulle clip originali), solo se si SENTONO sopra musica e sfx
  - sfx che partono DOPO il taglio (in ritardo: non va mai bene; prima del taglio può essere voluto)
Le tracce audio lunghe le ascolta earshot (AudioSet) per capire se sono voce (voiceover) o musica, e se nessuno
parla (video solo musica) la regola della musica e i controlli dei tagli vengono saltati.

Gira con il python di earshot. Uso:
  python audiocheck.py <cartella progetto> --ffmpeg <ffmpeg> --out report.json [--fix]
--fix sistema da solo il volume della musica (in tutte le copie del draft). Il resto viene solo segnalato.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

SR = 24000
US = 1_000_000
FR = 0.01                    # energy frame: 10 ms
GAP_TARGET = 18.0            # music this many LU under the voice
GAP_OK = (14.0, 22.0)
VOL_MIN, VOL_MAX = 0.02, 1.0
LEAD_MAX, TAIL_MAX = 0.25, 0.35   # silence allowed at the start / end of a spoken clip
SFX_LATE = (0.07, 0.35)
TAIL_MIN = 0.06              # the word must end at least this long before the cut
VOICE_RANGE = 30             # dB under the clip's loud voice: below that it is room echo, not a word
MASK_DB = 3                  # a bit of voice is heard only if this much louder than music + sfx there
SPEECH_MIN = 0.6             # earshot AudioSet 'Speech' on the voice: under this nobody is really talking
LONG_SEG = 5.0               # s: a piece this long on an audio track is music or voice, shorter = sfx
CHOP_MIN = 3                 # frames (30 ms) of voice lost at a cut = word chopped


def decode(ff, path, cache={}):
    if path in cache:
        return cache[path]
    p = subprocess.run([ff, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    a = np.frombuffer(p.stdout, dtype=np.float32) if p.returncode == 0 else np.zeros(0, np.float32)
    cache[path] = a
    return a


def db_frames(a):
    n = int(SR * FR)
    k = len(a) // n
    if k == 0:
        return np.full(1, -120.0)
    f = a[:k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((f ** 2).mean(1)) + 1e-9)


def cut(a, s0, dur, speed):
    """the played part of a source (seconds), resampled for speed"""
    i0 = int(round(s0 * SR))
    x = a[i0:i0 + int(round(dur * speed * SR))]
    if len(x) and abs(speed - 1) > 1e-3:
        n_out = max(1, int(round(len(x) / speed)))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)
    return x


def run(mask):
    """how many True in a row from the start"""
    k = 0
    for v in mask:
        if not v:
            break
        k += 1
    return k


def place(track, x, t0, vol):
    i = int(round(t0 * SR))
    if i >= len(track) or len(x) == 0:
        return
    x = x[:len(track) - i]
    track[i:i + len(x)] += x * vol


class Ears:
    """earshot's AudioSet model, loaded once: is this talking or music ?"""
    def __init__(self):
        self.a = None

    def probs(self, x):
        try:
            if self.a is None:
                from earshot.models import ast
                self.a = ast.get()
            from earshot import audio
            y = audio.resample(np.asarray(x, np.float32), SR, audio.SR)
            p = self.a.probs([y], reduce="mean")[0]
            g = lambda l: float(p[self.a.index[l]]) if l in self.a.index else 0.0  # noqa: E731
            return {"speech": max(g("Speech"), g("Narration, monologue")), "music": g("Music"), "singing": g("Singing")}
        except Exception as e:  # noqa: BLE001
            print("earshot AudioSet:", e, file=sys.stderr)
            return None


def earshot_measure(files, ff="ffmpeg"):
    """earshot's loudness of each stem (LUFS, true peak). earshot wants ffmpeg/ffprobe on PATH."""
    out = {}
    env = dict(os.environ)
    if os.path.dirname(ff):
        env["PATH"] = os.path.dirname(os.path.abspath(ff)) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    p = subprocess.run([sys.executable, "-m", "earshot", "measure", "--light", "--json", *files],
                       capture_output=True, text=True, env=env)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        print("earshot measure:", (p.stderr or "").strip()[-300:], file=sys.stderr)
        return out
    rows = data if isinstance(data, list) else data.get("results") or data.get("files") or [data]
    for f, r in zip(files, rows):
        m = r.get("measurements", r) if isinstance(r, dict) else {}
        out[f] = {"lufs": m.get("lufs"), "true_peak": m.get("true_peak_dbtp")}
    return out


def fmt(t):
    return f"{t:.1f}".replace(".", ",") + " s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out")
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args()
    folder = Path(a.project)
    d = json.load(open(folder / "draft_info.json"))
    dur = d["duration"] / US
    mats = {}
    for k, v in d["materials"].items():
        if isinstance(v, list):
            for m in v:
                if isinstance(m, dict) and "id" in m:
                    mats[m["id"]] = (k, m)

    def src_of(s):
        k, m = mats.get(s["material_id"], ("", {}))
        p = m.get("path") or ""
        return k, m, (p if p and os.path.isfile(p) else None)

    n = int(dur * SR) + SR
    voice, music, sfx = (np.zeros(n, np.float32) for _ in range(3))
    problems, skipped, ears = [], 0, Ears()

    def stem(segs):
        x = np.zeros(n, np.float32)
        nonlocal skipped
        for s in segs:
            k, m, p = src_of(s)
            vol = s.get("volume") or 0
            if vol <= 0.01 or m.get("type") == "photo":
                continue
            if not p:
                skipped += 1
                continue
            st, tt = s["source_timerange"], s["target_timerange"]
            place(x, cut(decode(a.ffmpeg, p), st["start"] / US, tt["duration"] / US, s.get("speed") or 1.0), tt["start"] / US, vol)
        return x

    # ---- voice from the clips --------------------------------------------------------------------------------
    vtracks = [t for t in d["tracks"] if t["type"] == "video"]

    def voiced(t):
        return sum(s["target_timerange"]["duration"] for s in t["segments"] if (s.get("volume") or 0) > 0.01)
    main = max(vtracks, key=voiced) if vtracks else None
    picture = max(vtracks, key=lambda t: len(t["segments"])) if vtracks else None
    cuts = sorted({s["target_timerange"]["start"] / US for s in (picture["segments"] if picture else [])} - {0.0})
    voice_tracks = []
    for t in vtracks:
        voice += stem(t["segments"])
    if main and voiced(main) > 0:
        voice_tracks.append(main)

    # ---- audio tracks: long ones are voice or music (earshot listens), short ones are sfx ---------------------
    music_ids, kinds = set(), []
    for t in [t for t in d["tracks"] if t["type"] == "audio"]:
        long_segs = [s for s in t["segments"] if s["target_timerange"]["duration"] / US >= LONG_SEG]
        short_segs = [s for s in t["segments"] if s["target_timerange"]["duration"] / US < LONG_SEG]
        cover = sum(s["target_timerange"]["duration"] for s in long_segs) / US
        if cover >= 0.3 * dur:
            x = stem(long_segs)
            library = all(src_of(s)[1].get("type") == "music" and "/music/" in (src_of(s)[1].get("path") or "") for s in long_segs)
            pr = None if library else ears.probs(x[:int(dur * SR)])
            is_voice = bool(pr and pr["speech"] >= 0.5 and pr["speech"] > pr["music"])
            kinds.append({"traccia": len(kinds), "durata": round(cover, 1), "voce": is_voice,
                          "parlato": None if pr is None else round(pr["speech"], 2), "musica": None if pr is None else round(pr["music"], 2)})
            if is_voice:
                voice += x
                voice_tracks.append({"segments": long_segs})
            else:
                music += x
                music_ids.update(s["id"] for s in long_segs)
        else:
            short_segs = t["segments"]
        x = stem(short_segs)
        sfx += x
        for s in short_segs:          # a short sfx that starts AFTER the cut it is meant for = late
            k, m, p = src_of(s)
            tt, st = s["target_timerange"], s["source_timerange"]
            if not p or (s.get("volume") or 0) <= 0.01 or tt["duration"] / US >= 2.5 or not cuts:
                continue
            ex = db_frames(cut(decode(a.ffmpeg, p), st["start"] / US, tt["duration"] / US, s.get("speed") or 1.0))
            on = np.nonzero(ex > ex.max() - 20)[0]
            if len(on):
                onset = tt["start"] / US + on[0] * FR
                prev_cut = max((c for c in cuts if c <= onset), default=None)
                if prev_cut is not None and SFX_LATE[0] < onset - prev_cut <= SFX_LATE[1]:
                    problems.append({"tipo": "sfx in ritardo", "t": round(onset, 2),
                                     "testo": f"«{Path(p).stem}» parte {fmt(onset - prev_cut)} dopo il taglio a {fmt(prev_cut)}"})

    # ---- is anybody talking ? --------------------------------------------------------------------------------
    pr = ears.probs(voice[:int(dur * SR)]) if voice.any() else {"speech": 0.0}
    speech_p = None if pr is None else pr["speech"]
    speech = speech_p is None or speech_p >= SPEECH_MIN

    # ---- chopped words, short tails, pauses: on every voice track, read on the original sources -----------
    # a sound counts only if you would HEAR it: above the clip's noise, not 30+ dB under its own voice (room
    # echo), and louder than the music + sfx playing at that moment
    bed = db_frames(music + sfx)

    def bed_at(t):
        i = int(t / FR)
        return float(np.median(bed[i:i + 10])) if i < len(bed) else -120.0

    for vt in (voice_tracks if speech else []):
        segs = sorted(vt["segments"], key=lambda s: s["target_timerange"]["start"])
        for i, s in enumerate(segs):
            k, m, p = src_of(s)
            if (s.get("volume") or 0) <= 0.01 or not p or m.get("type") == "photo":
                continue
            src = decode(a.ffmpeg, p)
            if len(src) == 0:
                continue
            e = db_frames(src)
            noise = float(np.percentile(e, 10))
            thr = max(noise + 10, float(np.percentile(e, 95)) - VOICE_RANGE)
            vol_db = 20 * np.log10(max(s.get("volume") or 0, 1e-4))
            sp = s.get("speed") or 1.0
            s0 = s["source_timerange"]["start"] / US
            s1 = s0 + s["target_timerange"]["duration"] / US * sp
            t0 = s["target_timerange"]["start"] / US
            t1 = t0 + s["target_timerange"]["duration"] / US
            f0, f1 = int(s0 / FR), int(s1 / FR)
            inside = e[f0:f1]
            loud = np.nonzero(inside > thr)[0]
            if len(loud) == 0:
                continue          # no speech in this clip (b-roll / silent shot)
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            prv = segs[i - 1] if i else None
            same_next = nxt is not None and src_of(nxt)[2] == p and abs(nxt["source_timerange"]["start"] / US - s1) < 0.02
            same_prev = prv is not None and src_of(prv)[2] == p and abs(
                (prv["source_timerange"]["start"] + prv["target_timerange"]["duration"] * (prv.get("speed") or 1)) / US - s0) < 0.02
            end_e = e[max(f0, f1 - 3):f1].max(initial=-120)
            run_after = run(e[f1:f1 + 40] > thr)              # voice still going right after the cut
            heard_after = float(np.median(e[f1:f1 + max(run_after, 1)])) + vol_db > bed_at(t1) + MASK_DB
            if not same_next and end_e > thr and run_after >= CHOP_MIN and heard_after:
                problems.append({"tipo": "parola tagliata", "t": round(t1, 2),
                                 "testo": f"a {fmt(t1)} il taglio mozza la fine della parola ({run_after * 10} ms di voce persi, «{Path(p).name}»)"})
            run_before = run(e[max(0, f0 - 40):f0][::-1] > thr)  # voice already going before the clip starts
            heard_before = float(np.median(e[max(0, f0 - max(run_before, 1)):f0])) + vol_db > bed_at(t0) + MASK_DB
            if not same_prev and e[f0:f0 + 3].max(initial=-120) > thr and run_before >= CHOP_MIN and heard_before:
                problems.append({"tipo": "parola tagliata", "t": round(t0, 2),
                                 "testo": f"a {fmt(t0)} la clip parte a metà parola ({run_before * 10} ms persi, «{Path(p).name}»)"})
            lead = loud[0] * FR / sp
            tail = (len(inside) - 1 - loud[-1]) * FR / sp
            last = e[max(f0, f1 - 15):f1].max(initial=-120)
            if not same_next and nxt is not None and run_after < CHOP_MIN and tail < TAIL_MIN and last > thr \
                    and last + vol_db > bed_at(t1 - 0.1) + MASK_DB:
                problems.append({"tipo": "coda corta", "t": round(t1, 2),
                                 "testo": f"a {fmt(t1)} la parola finisce a filo del taglio ({int(tail * 1000)} ms): lascia 0,10–0,12 s"})
            if not same_prev and prv is not None and lead > LEAD_MAX:
                problems.append({"tipo": "pausa", "t": round(t0, 2),
                                 "testo": f"a {fmt(t0)} {fmt(lead)} di silenzio prima che parli"})
            if not same_next and nxt is not None and tail > TAIL_MAX:
                problems.append({"tipo": "pausa", "t": round(t1 - tail, 2),
                                 "testo": f"a {fmt(t1 - tail)} {fmt(tail)} di silenzio prima del taglio"})

    # ---- loudness with earshot --------------------------------------------------------------------------------
    import soundfile as sf
    tmp = Path(tempfile.mkdtemp(prefix="audiocheck-"))
    files = {}
    for name, arr in (("voce", voice), ("musica", music), ("mix", voice + music + sfx)):
        f = str(tmp / f"{name}.wav")
        sf.write(f, arr[:int(dur * SR)], SR, subtype="FLOAT")
        files[name] = f
    meas = earshot_measure([files["voce"], files["musica"], files["mix"]], a.ffmpeg)
    v_l = (meas.get(files["voce"]) or {}).get("lufs")
    m_l = (meas.get(files["musica"]) or {}).get("lufs")
    mix = meas.get(files["mix"]) or {}
    rep = {"progetto": folder.name, "durata": round(dur, 2), "parlato": None if speech_p is None else round(speech_p, 2),
           "tracce_lunghe": kinds, "voce_lufs": v_l, "musica_lufs": m_l, "mix_lufs": mix.get("lufs"),
           "musica_fix": None, "distanza_musica_voce": None, "clip_non_trovate": skipped, "file_audio": files}

    fixes = []
    if not speech:
        rep["nota"] = "nessuno parla (earshot): regola della musica e controllo dei tagli saltati"
    elif music_ids and v_l is not None and m_l is not None and m_l > -70 and v_l > -70:
        gap = v_l - m_l
        rep["distanza_musica_voce"] = round(gap, 1)
        if not (GAP_OK[0] <= gap <= GAP_OK[1]):
            factor = 10 ** ((gap - GAP_TARGET) / 20)
            what = "troppo bassa" if gap > GAP_OK[1] else "troppo alta"
            rep["musica_fix"] = {"fattore": round(factor, 3), "era": what, "distanza_era": round(gap, 1)}
            if a.fix:
                for f in [folder / "draft_info.json", folder / "template-2.tmp", *map(Path, glob.glob(str(folder / "Timelines/*/draft_info.json"))),
                          *map(Path, glob.glob(str(folder / "Timelines/*/template-2.tmp")))]:
                    if not f.exists() or f.stat().st_size == 0:
                        continue
                    try:
                        dd = json.load(open(f))
                    except ValueError:
                        continue
                    changed = False
                    for t in dd.get("tracks", []):
                        for s in t.get("segments", []):
                            if s.get("id") in music_ids:
                                s["volume"] = round(min(VOL_MAX, max(VOL_MIN, (s.get("volume") or 0) * factor)), 3)
                                changed = True
                    if changed:
                        json.dump(dd, open(f, "w"), ensure_ascii=False)
                fixes.append(f"musica {what} ({gap:.0f} dB sotto la voce): volume ×{factor:.2f} → ~18 dB sotto")
            else:
                problems.append({"tipo": "musica", "t": 0,
                                 "testo": f"musica {what}: {gap:.0f} dB sotto la voce, deve stare ~18 (volume ×{factor:.2f})"})
    rep["sistemato"] = fixes
    rep["problemi"] = sorted(problems, key=lambda p: p["t"])
    txt = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
