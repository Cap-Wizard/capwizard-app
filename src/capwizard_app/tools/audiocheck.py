"""Controllo audio di un progetto CapCut, con le "orecchie" di earshot.

Ricostruisce l'audio del progetto dal draft (voce delle clip, musica, sfx, con i volumi), poi controlla:
  - musica rispetto alla voce (regola di Daniel: ~18 dB sotto, misurata in LUFS con earshot)
  - parole tagliate a metà e pause lasciate nei tagli (energia della voce sulle clip originali)
  - sfx che partono DOPO il taglio (in ritardo: non va mai bene; prima del taglio può essere voluto)

Gira con il python di earshot (ha numpy). Uso:
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
TAIL_MIN = 0.06               # the word must end at least this long before the cut
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
    n_src = int(round(dur * speed * SR))
    x = a[i0:i0 + n_src]
    if len(x) == 0:
        return x
    if abs(speed - 1) > 1e-3:
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
    problems, skipped = [], 0

    # ---- voice: video tracks with sound -------------------------------------------------------------------------
    vtracks = [t for t in d["tracks"] if t["type"] == "video"]

    def voiced(t):
        return sum(s["target_timerange"]["duration"] for s in t["segments"] if (s.get("volume") or 0) > 0.01)
    main = max(vtracks, key=voiced) if vtracks else None
    cuts = sorted({s["target_timerange"]["start"] / US for s in (main["segments"] if main else [])}
                  | {(s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US
                     for s in (main["segments"] if main else [])})
    for t in vtracks:
        for s in t["segments"]:
            vol = s.get("volume") or 0
            k, m, p = src_of(s)
            if vol <= 0.01 or k != "videos" or m.get("type") == "photo":
                continue
            if not p:
                skipped += 1
                continue
            st, tt = s["source_timerange"], s["target_timerange"]
            place(voice, cut(decode(a.ffmpeg, p), st["start"] / US, tt["duration"] / US, s.get("speed") or 1.0),
                  tt["start"] / US, vol)

    # ---- words chopped and pauses, on the main track, read on the original clips ----------------------------
    if main:
        segs = sorted(main["segments"], key=lambda s: s["target_timerange"]["start"])
        for i, s in enumerate(segs):
            k, m, p = src_of(s)
            if (s.get("volume") or 0) <= 0.01 or not p or m.get("type") == "photo":
                continue
            src = decode(a.ffmpeg, p)
            if len(src) == 0:
                continue
            e = db_frames(src)
            noise = float(np.percentile(e, 10))
            thr = noise + 10
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
            run_after = run(e[f1:f1 + 40] > thr)              # voice still going right after the cut
            if not same_next and e[max(f0, f1 - 3):f1].max(initial=-120) > thr and run_after >= CHOP_MIN:
                problems.append({"tipo": "parola tagliata", "t": round(t1, 2),
                                 "testo": f"a {fmt(t1)} il taglio mozza la fine della parola ({run_after * 10} ms di voce persi, «{Path(p).name}»)"})
            run_before = run(e[max(0, f0 - 40):f0][::-1] > thr)  # voice already going before the clip starts
            if not same_prev and e[f0:f0 + 3].max(initial=-120) > thr and run_before >= CHOP_MIN:
                problems.append({"tipo": "parola tagliata", "t": round(t0, 2),
                                 "testo": f"a {fmt(t0)} la clip parte a metà parola ({run_before * 10} ms persi, «{Path(p).name}»)"})
            lead = loud[0] * FR / sp
            tail = (len(inside) - 1 - loud[-1]) * FR / sp
            if not same_prev and lead > LEAD_MAX:
                problems.append({"tipo": "pausa", "t": round(t0, 2),
                                 "testo": f"a {fmt(t0)} {fmt(lead)} di silenzio prima che parli"})
            if not same_next and run_after < CHOP_MIN and tail < TAIL_MIN and e[max(f0, f1 - 15):f1].max(initial=-120) > thr:
                problems.append({"tipo": "coda corta", "t": round(t1, 2),
                                 "testo": f"a {fmt(t1)} la parola finisce a filo del taglio ({int(tail * 1000)} ms): lascia 0,10–0,12 s"})
            if not same_next and nxt is not None and tail > TAIL_MAX:
                problems.append({"tipo": "pausa", "t": round(t1 - tail, 2),
                                 "testo": f"a {fmt(t1 - tail)} {fmt(tail)} di silenzio prima del taglio"})

    # ---- music and sfx ----------------------------------------------------------------------------------------
    atracks = [t for t in d["tracks"] if t["type"] == "audio"]
    music_ids = set()
    for t in atracks:
        cover = sum(s["target_timerange"]["duration"] for s in t["segments"]) / US
        is_music = cover >= 0.5 * dur
        for s in t["segments"]:
            k, m, p = src_of(s)
            vol = s.get("volume") or 0
            if not p:
                skipped += 1
                continue
            st, tt = s["source_timerange"], s["target_timerange"]
            x = cut(decode(a.ffmpeg, p), st["start"] / US, tt["duration"] / US, s.get("speed") or 1.0)
            if is_music:
                music_ids.add(s["id"])
                place(music, x, tt["start"] / US, vol)
                continue
            place(sfx, x, tt["start"] / US, vol)
            # a short sfx that starts AFTER a cut it is clearly meant for = late
            if len(x) and tt["duration"] / US < 2.5 and cuts:
                ex = db_frames(x)
                on = np.nonzero(ex > ex.max() - 20)[0]
                if len(on):
                    onset = tt["start"] / US + on[0] * FR
                    prev_cut = max((c for c in cuts if c <= onset), default=None)
                    if prev_cut is not None and SFX_LATE[0] < onset - prev_cut <= SFX_LATE[1]:
                        problems.append({"tipo": "sfx in ritardo", "t": round(onset, 2),
                                         "testo": f"«{Path(p).stem}» parte {fmt(onset - prev_cut)} dopo il taglio a {fmt(prev_cut)}"})

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
    rep = {"progetto": folder.name, "durata": round(dur, 2), "voce_lufs": v_l, "musica_lufs": m_l,
           "mix_lufs": mix.get("lufs"), "mix_true_peak": mix.get("true_peak"), "musica_fix": None,
           "clip_non_trovate": skipped, "file_audio": files}

    fixes = []
    if music_ids and v_l is not None and m_l is not None and m_l > -70 and v_l > -70:
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
    elif not music_ids:
        rep["distanza_musica_voce"] = None
    rep["sistemato"] = fixes
    rep["problemi"] = sorted(problems, key=lambda p: p["t"])
    txt = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
