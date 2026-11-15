"""Anteprima di un progetto CapCut costruito, PRIMA che Gabriele lo esporti: serve al critico (agente
critico-video), che guarda solo video finiti. Regola di Gabriele (15/11/2026): il critico controlla prima
dell'export; si corregge e si rifà l'anteprima finché il critico non ha più niente da dire, solo allora si esporta.

uso: python3 anteprima.py CARTELLA_PROGETTO OUT.mp4 [--larghezza 720]

Ricostruisce con ffmpeg (720x1280, 30 fps), più OUT.voce.wav con la sola voce (per misurare le pause):
  - la traccia principale: tagli, rotazione, zoom e posizione, volume di ogni spezzone, LUT (con la sua
    intensità, dal file .cube) e bianco e nero (saturazione −100); i vuoti in nero;
  - le sovrapposizioni con un file vero (foto, scontorni .mov con alfa), con posizione, scala e opacità;
  - i testi: sottotitoli, parole grandi, titoli e dati dentro le clip composte (font, colore, grandezza e
    posizione circa giusti); le emoji come «[emoji 🧵]» in giallo, perché ffmpeg non le disegna;
  - l'audio: voce degli spezzoni + musica e suoni della timeline ai loro volumi.
NON ricostruisce: transizioni, animazioni dei testi e delle clip composte, effetti e filtri di CapCut, la grafica
dei preset dei sottotitoli (riquadri, contorni). Scrive accanto OUT.json con cosa c'è e cosa manca: il critico
mette in «Non verificato» quello che l'anteprima non mostra.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FPS = 30
PX_PER_SIZE = 6.0          # 1 punto di grandezza CapCut ~ 6 px su 1080x1920 (come costruisci_catalogo.py)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(" ".join(cmd[:6]) + " …\n" + r.stderr[-1500:])


def _fc_arg(script: Path) -> list[str]:
    """Il grafo da file: «-/filter_complex FILE» da ffmpeg 7, «-filter_complex_script FILE» prima."""
    h = subprocess.run(["ffmpeg", "-hide_banner", "-h", "long"], capture_output=True, text=True).stdout
    return ["-filter_complex_script", str(script)] if "filter_complex_script" in h else ["-/filter_complex", str(script)]


_PH = __import__("re").compile(r"##_draftpath_placeholder_[0-9A-Fa-f-]+_##")
PROJ = [""]


def fix_path(p: str) -> str:
    """CapCut scrive i file dentro il progetto come «##_draftpath_placeholder_<id>_##/…»."""
    return _PH.sub(lambda _m: PROJ[0], p or "")


def index(mats: dict) -> dict:
    return {m["id"]: (k, m) for k, v in mats.items() if isinstance(v, list)
            for m in v if isinstance(m, dict) and "id" in m}


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return bool(out.strip())


def is_emoji(t: str) -> bool:
    return t.strip() != "" and not any(c.isalnum() and ord(c) < 0x250 for c in t)


def hexcol(rgb) -> str:
    try:
        return "0x%02X%02X%02X" % tuple(int(max(0, min(1, float(c))) * 255) for c in rgb[:3])
    except (TypeError, ValueError):
        return "0xFFFFFF"


def text_info(m: dict) -> tuple[str, str, float, str]:
    """(testo, colore, grandezza CapCut, font) di un materiale di testo."""
    try:
        c = json.loads(m.get("content") or "{}")
    except ValueError:
        c = {}
    st = (c.get("styles") or [{}])[0]
    col = st.get("fill", {}).get("content", {}).get("solid", {}).get("color") or [1, 1, 1]
    font = (st.get("font") or {}).get("path") or m.get("font_path") or ""
    size = float(st.get("size") or m.get("font_size") or 8)
    return c.get("text", ""), hexcol(col), size, font


def colore(s: dict, ix: dict, rep: dict, tag: str) -> str:
    """Catena ffmpeg per LUT (con intensità) e saturazione di un segmento; '' se non ne ha."""
    lut = sat = None
    for r in s.get("extra_material_refs") or []:
        rm = ix.get(r, ("", {}))[1]
        if rm.get("type") == "lut" and rm.get("value", 0) > 0.001:
            cube = rm.get("path", "").split(";")[0]
            if os.path.exists(cube):
                lut = (cube, float(rm["value"]))
            else:
                rep["mancanti"].append(cube)
        elif rm.get("type") == "saturation":
            sat = float(rm.get("value", 0))
    chain = ""
    if lut:
        cube = lut[0].replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        chain += (f",split[ca{tag}][cb{tag}];[cb{tag}]lut3d=file='{cube}'[cl{tag}];"
                  f"[ca{tag}][cl{tag}]blend=all_expr='A*(1-{lut[1]:.3f})+B*{lut[1]:.3f}'")
    if sat is not None and sat < 0:
        chain += f",hue=s={max(0.0, 1 + sat):.3f}"
    s["_lut"], s["_bn"] = (round(lut[1], 2) if lut else None), bool(sat is not None and sat <= -0.99)
    return chain


def main() -> None:
    args = sys.argv[1:]
    W = 720
    if "--larghezza" in args:
        i = args.index("--larghezza")
        W = int(args[i + 1]) // 2 * 2
        del args[i:i + 2]
    proj, out = Path(args[0]), Path(args[1])
    PROJ[0] = str(proj)
    H = W * 16 // 9
    k = W / 1080
    D = json.load(open(proj / "draft_info.json"))
    ix = index(D["materials"])
    total = D.get("duration") or max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                                       for t in D["tracks"] for s in t["segments"]), default=0)
    total_s = total / 1e6
    tmp = Path(tempfile.mkdtemp(prefix="anteprima_"))
    rep = {"progetto": str(proj), "durata": round(total_s, 2), "spezzoni": [], "sovrapposizioni": [],
           "testi": [], "audio": [], "mancanti": [], "transizioni": [], "nascosti": [],
           "buchi_traccia_principale": [],
           "non_ricostruito": ["transizioni", "animazioni dei testi e delle clip composte",
                               "filtri ed effetti di CapCut", "grafica dei preset dei sottotitoli"]}
    try:
        # ---------------- traccia principale ----------------
        main_tr = next((t for t in D["tracks"] if t["type"] == "video" and t.get("flag", 0) == 0 and t["segments"]),
                       None)
        pieces, cur = [], 0
        segs = sorted(main_tr["segments"], key=lambda s: s["target_timerange"]["start"]) if main_tr else []

        def black(dur_us: int) -> None:
            p = tmp / f"p{len(pieces):03d}.mkv"
            run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}",
                 "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{dur_us / 1e6:.4f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(p)])
            pieces.append(p)

        for s in segs:
            t0, dur = s["target_timerange"]["start"], s["target_timerange"]["duration"]
            if t0 - cur > 20_000:
                black(t0 - cur)
            kind, m = ix.get(s["material_id"], ("", {}))
            path = fix_path(m.get("path", ""))
            if kind != "videos" or not path or not os.path.exists(path):
                rep["mancanti"].append(path or s["material_id"])
                black(dur)
                cur = t0 + dur
                continue
            src = s.get("source_timerange") or {"start": 0, "duration": dur}
            speed = float(s.get("speed") or 1.0)
            clip = s.get("clip") or {}
            z = float((clip.get("scale") or {}).get("x", 1.0))
            tx, ty = (clip.get("transform") or {}).get("x", 0.0), (clip.get("transform") or {}).get("y", 0.0)
            rot = int(round(float(clip.get("rotation") or 0))) % 360
            vf = []
            if m.get("type") == "photo":
                vf.append("loop=-1:size=1:start=0")
            vf += {90: ["transpose=1"], 180: ["transpose=1,transpose=1"], 270: ["transpose=2"]}.get(rot, [])
            vf.append(f"scale={W}:{H}:force_original_aspect_ratio=increase")
            if abs(z - 1) > 0.01:
                vf.append(f"scale=iw*{z:.4f}:ih*{z:.4f}")
            vf.append(f"crop={W}:{H}:(iw-{W})/2-({tx:.4f})*{W}/2:(ih-{H})/2+({ty:.4f})*{H}/2")
            if speed != 1.0:
                vf.append(f"setpts=PTS/{speed}")
            vf.append(f"fps={FPS},format=yuv420p")
            chain = ",".join(vf) + colore(s, ix, rep, f"m{len(pieces)}")
            vol = float(s.get("volume", 1.0))
            p = tmp / f"p{len(pieces):03d}.mkv"
            cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{src['start'] / 1e6:.4f}", "-t",
                   f"{src['duration'] / 1e6:.4f}", "-i", path]
            if has_audio(path):
                af = f"volume={vol:.3f},atempo={speed}" if speed != 1.0 else f"volume={vol:.3f}"
                af += ",aresample=48000,aformat=channel_layouts=stereo,apad"   # audio lungo esatto come il video
                cmd += ["-filter_complex", f"[0:v]{chain}[v];[0:a]{af}[a]", "-map", "[v]", "-map", "[a]"]
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]", "-map", "1:a", "-shortest"]
            cmd += ["-t", f"{dur / 1e6:.4f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-c:a", "pcm_s16le", "-ar", "48000", str(p)]
            run(cmd)
            pieces.append(p)
            rep["spezzoni"].append({"da": round(t0 / 1e6, 2), "a": round((t0 + dur) / 1e6, 2),
                                    "file": os.path.basename(path), "zoom": round(z, 2), "volume": round(vol, 2),
                                    "lut": s["_lut"], "bianco_nero": s["_bn"],
                                    "transizione": next((ix[r][1].get("name") for r in s.get("extra_material_refs") or []
                                                         if ix.get(r, ("",))[0] == "transitions"), None)})
            for r in s.get("extra_material_refs") or []:
                if ix.get(r, ("",))[0] == "transitions":
                    rep["transizioni"].append({"a_fine_di": round((t0 + dur) / 1e6, 2), "nome": ix[r][1].get("name")})
            cur = t0 + dur
        if total - cur > 20_000:
            black(total - cur)
        # buchi della traccia principale non coperti da una clip piena sopra (lì si vede nero)
        def opaco(path: str) -> bool:
            pf = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=pix_fmt", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
            return not any(x in pf for x in ("yuva", "rgba", "argb", "bgra", "abgr", "gbrap"))
        cover = []
        for t in D["tracks"]:
            if t["type"] != "video" or t is main_tr:
                continue
            for s in t["segments"]:
                kind, m = ix.get(s["material_id"], ("", {}))
                cl = s.get("clip") or {}
                path = fix_path(m.get("path", ""))
                composta = any(ix.get(r, ("",))[0] == "drafts" for r in s.get("extra_material_refs") or [])
                if (kind == "videos" and m.get("type") == "video" and not composta and float(cl.get("alpha", 1)) > 0.98
                        and float((cl.get("scale") or {}).get("x", 1)) >= 0.99 and os.path.exists(path) and opaco(path)):
                    a0 = s["target_timerange"]["start"]
                    cover.append((a0, a0 + s["target_timerange"]["duration"]))
        spans = sorted([(x["target_timerange"]["start"], x["target_timerange"]["start"] + x["target_timerange"]["duration"])
                        for x in segs] + cover)
        edge = 0
        for a0, b0 in spans:
            if a0 - edge > 1000:
                rep["buchi_traccia_principale"].append({"da": round(edge / 1e6, 3), "a": round(a0 / 1e6, 3)})
            edge = max(edge, b0)
        if total - edge > 1000:
            rep["buchi_traccia_principale"].append({"da": round(edge / 1e6, 3), "a": round(total / 1e6, 3)})
        lst = tmp / "lista.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in pieces))
        base = tmp / "base.mkv"
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base)])

        # ---------------- sovrapposizioni, testi, audio ----------------
        inputs, fc, last, ain = [str(base)], [], "[0:v]", []
        nin = [1]          # quanti input ha ffmpeg (il primo è la base)
        texts = []

        def add_text(m, a, b, scale, x, y):
            txt, col, size, font = text_info(m)
            if not txt.strip() or txt.strip().upper() == "TRANSITION":
                return
            if is_emoji(txt):
                txt, col, font = f"[emoji {txt.strip()}]", "0xFFE600", ""
            texts.append((a, b, txt, col, max(10, size * PX_PER_SIZE * scale * k), x, y, font))

        voce = ["[0:a]"]   # voce: traccia principale + clip con audio sopra
        for ti, t in enumerate(D["tracks"]):
            for s in t["segments"]:
                a = s["target_timerange"]["start"] / 1e6
                b = a + s["target_timerange"]["duration"] / 1e6
                clip = s.get("clip") or {}
                sc = float((clip.get("scale") or {}).get("x", 1.0))
                x, y = (clip.get("transform") or {}).get("x", 0.0), (clip.get("transform") or {}).get("y", 0.0)
                alpha = float(clip.get("alpha", 1.0))
                kind, m = ix.get(s["material_id"], ("", {}))
                if t["type"] == "text":
                    if kind == "texts":
                        add_text(m, a, b, sc, x, y)
                    elif kind == "text_templates":
                        for tir in m.get("text_info_resources") or []:
                            tm = ix.get(tir.get("text_material_id"), ("", {}))[1]
                            if tm:
                                add_text(tm, a, b, sc, x, y)
                                break
                elif t["type"] == "video" and t.get("flag", 0) != 0 and t is not main_tr:
                    comp = next((ix[r][1] for r in s.get("extra_material_refs") or []
                                 if ix.get(r, ("",))[0] == "drafts"), None)
                    if comp and isinstance(comp.get("draft"), dict):
                        sub = comp["draft"]
                        six = index(sub["materials"])
                        off = (s.get("source_timerange") or {}).get("start", 0) / 1e6
                        for st in sub["tracks"]:
                            for ss in st["segments"]:
                                sa = ss["target_timerange"]["start"] / 1e6 - off
                                sb = sa + ss["target_timerange"]["duration"] / 1e6
                                lo, hi = max(a, a + sa), min(b, a + sb)
                                if hi <= lo:
                                    continue
                                sk, sm = six.get(ss["material_id"], ("", {}))
                                cl = ss.get("clip") or {}
                                csc = float((cl.get("scale") or {}).get("x", 1.0)) * sc
                                cx = x + (cl.get("transform") or {}).get("x", 0.0) * sc
                                cy = y + (cl.get("transform") or {}).get("y", 0.0) * sc
                                if sk == "texts":
                                    add_text(sm, lo, hi, csc, cx, cy)
                                elif sk == "text_templates":
                                    for tir in sm.get("text_info_resources") or []:
                                        tm = six.get(tir.get("text_material_id"), ("", {}))[1]
                                        if tm:
                                            add_text(tm, lo, hi, csc, cx, cy)
                                            break
                        continue
                    path = fix_path(m.get("path", ""))
                    if kind == "videos" and alpha < 0.02:
                        rep["nascosti"].append({"traccia": ti, "da": round(a, 2), "a": round(b, 2),
                                                "file": os.path.basename(path), "perche": "opacità 0"})
                        continue
                    if kind != "videos" or not path or not os.path.exists(path):
                        if path:
                            rep["mancanti"].append(path)
                        continue
                    n = nin[0]; nin[0] += 1
                    if m.get("type") == "photo":
                        inputs += ["-loop", "1", "-t", f"{total_s:.3f}", "-i", path]
                    else:
                        src = (s.get("source_timerange") or {}).get("start", 0) / 1e6
                        inputs += ["-ss", f"{src:.4f}", "-t", f"{b - a:.4f}", "-i", path]
                    # CapCut: la scala 1 = il file adattato dentro la tela (contain)
                    ow, oh = m.get("width") or 1080, m.get("height") or 1920
                    fit = min(1080 / ow, 1920 / oh)
                    w_px = max(2, int(ow * fit * sc * k) // 2 * 2)
                    h_px = max(2, int(oh * fit * sc * k) // 2 * 2)
                    pre = (f"[{n}:v]format=rgba,scale={w_px}:{h_px}{colore(s, ix, rep, f'o{n}')},format=rgba,"
                           f"colorchannelmixer=aa={alpha:.3f},setpts=PTS-STARTPTS+{a:.4f}/TB[o{n}]")
                    fc.append(pre)
                    px = f"{W / 2 + x * W / 2 - w_px / 2:.1f}"
                    py = f"{H / 2 - y * H / 2 - h_px / 2:.1f}"
                    fc.append(f"{last}[o{n}]overlay=x={px}:y={py}:enable='between(t,{a:.3f},{b:.3f})':eof_action=pass[v{n}]")
                    last = f"[v{n}]"
                    ovol = float(s.get("volume", 1.0))
                    if m.get("type") != "photo" and ovol > 0.001 and has_audio(path):
                        fc.append(f"[{n}:a]volume={ovol:.4f},aresample=48000,aformat=channel_layouts=stereo,"
                                  f"adelay={int(a * 1000)}|{int(a * 1000)}[va{n}]")
                        voce.append(f"[va{n}]")
                    rep["sovrapposizioni"].append({"traccia": ti, "da": round(a, 2), "a": round(b, 2),
                                                   "file": os.path.basename(path), "opacita": round(alpha, 2),
                                                   "volume": round(ovol, 2), "lut": s.get("_lut"),
                                                   "bianco_nero": s.get("_bn")})
                elif t["type"] == "audio" and kind == "audios":
                    path = fix_path(m.get("path", ""))
                    if not path or not os.path.exists(path):
                        rep["mancanti"].append(path or m.get("name", ""))
                        continue
                    n = nin[0]; nin[0] += 1
                    src = (s.get("source_timerange") or {}).get("start", 0) / 1e6
                    inputs += ["-ss", f"{src:.4f}", "-t", f"{b - a:.4f}", "-i", path]
                    vol = float(s.get("volume", 1.0))
                    fc.append(f"[{n}:a]volume={vol:.4f},aresample=48000,aformat=channel_layouts=stereo,"
                              f"adelay={int(a * 1000)}|{int(a * 1000)}[a{n}]")
                    ain.append(f"[a{n}]")
                    rep["audio"].append({"da": round(a, 2), "a": round(b, 2), "tipo": m.get("type"),
                                         "nome": m.get("name"), "volume": round(vol, 3)})
        # testi disegnati con Pillow in PNG trasparenti (molti ffmpeg non hanno drawtext)
        default_font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            texts, rep["non_ricostruito"] = [], rep["non_ricostruito"] + ["testi (manca Pillow)"]
        for i, (a, b, txt, col, size, x, y, font) in enumerate(texts):
            ff = font if font and os.path.exists(font) else default_font
            try:
                f = ImageFont.truetype(ff, int(size))
            except OSError:
                f = ImageFont.truetype(default_font, int(size))
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            box = probe.multiline_textbbox((0, 0), txt, font=f, align="center", stroke_width=2, spacing=4)
            box = [int(round(v)) for v in box]          # Pillow 12 dà misure decimali
            tw, th = box[2] - box[0] + 4, box[3] - box[1] + 4
            img = Image.new("RGBA", (max(2, tw), max(2, th)), (0, 0, 0, 0))
            rgb = tuple(int(col[i:i + 2], 16) for i in (2, 4, 6))
            ImageDraw.Draw(img).multiline_text((2 - box[0], 2 - box[1]), txt, font=f, fill=rgb + (255,),
                                               align="center", stroke_width=2, stroke_fill=(0, 0, 0, 180), spacing=4)
            png = tmp / f"t{i}.png"
            img.save(png)
            n = nin[0]; nin[0] += 1
            inputs += ["-loop", "1", "-t", f"{total_s:.3f}", "-i", str(png)]
            px = f"{W / 2 + x * W / 2 - tw / 2:.1f}"
            py = f"{H / 2 - y * H / 2 - th / 2:.1f}"
            fc.append(f"{last}[{n}:v]overlay=x={px}:y={py}:enable='between(t,{a:.3f},{b:.3f})'[x{i}]")
            last = f"[x{i}]"
            rep["testi"].append({"da": round(a, 2), "a": round(b, 2), "testo": txt})
        vsplit = []
        for i, lab in enumerate(voce):
            fc.append(f"{lab}asplit=2[vm{i}][vv{i}]")
            vsplit.append(f"[vm{i}]")
        fc.append(f"{''.join(f'[vv{i}]' for i in range(len(voce)))}amix=inputs={len(voce)}:normalize=0:duration=longest[voceout]")
        mix = vsplit + ain
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=longest[aout]")
        script = tmp / "fc.txt"
        script.write_text(";\n".join(fc) if last != "[0:v]" else ";\n".join(fc + ["[0:v]null[vout]"]))
        vmap = last if last != "[0:v]" else "[vout]"
        run(["ffmpeg", "-v", "error", "-y", "-i", inputs[0], *inputs[1:], *_fc_arg(script),
             "-map", vmap, "-map", "[aout]", "-t", f"{total_s:.3f}", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str(out), "-map", "[voceout]", "-t", f"{total_s:.3f}", "-c:a", "pcm_s16le",
             str(out.with_suffix(".voce.wav"))])
        Path(str(out) + ".json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
        print(f"anteprima: {out} ({total_s:.2f} s, {len(rep['spezzoni'])} spezzoni, {len(rep['testi'])} testi, "
              f"{len(rep['audio'])} audio, {len(rep['sovrapposizioni'])} sovrapposizioni)")
        if rep["buchi_traccia_principale"]:
            print("BUCHI nella traccia principale (sotto gli strati si vede nero):",
                  ", ".join(f"{x['da']}-{x['a']} s" for x in rep["buchi_traccia_principale"]))
        if rep["mancanti"]:
            print("MANCANTI:", ", ".join(sorted(set(rep["mancanti"])))[:600])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
