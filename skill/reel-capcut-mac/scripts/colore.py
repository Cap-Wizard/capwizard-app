"""Clip in S-Log3: riconoscimento e LUT di CapCut per i video social parlati (regola di Gabriele, 15/11/2026).

Le clip Sony in S-Log3 hanno dentro il file (e nel file M01.XML accanto) l'XML della camera con
`CaptureGammaEquation = "s-log3-cine"`. Su quelle clip, in CapCut, va la LUT
«SLog3SGamut3.CineToCine+709» (Regola > LUT), con un'intensità che dipende dalla luce:
  - posto buio (discoteca, locale, sera) e video parlato: 40 %, per recuperare un po' di sfondo;
  - all'esterno, con luce: 100 %.
La luce si misura sul girato log (luminanza media 0-1, misurata su tre fotogrammi): sotto 0,28 è buio
(discoteche misurate 0,15-0,20), sopra è luce (esterni e posti illuminati 0,31-0,44). Se il profilo del
cliente dice contesto «discoteca», è sempre 40 %. Solo CapCut e social: per DaVinci, aftermovie e
matrimoni il colore si fa altrove.

uso: python3 colore.py CLIP [CLIP...] [--contesto discoteca]    stampa gamma, luce e LUT per clip
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

LUT_NOME = "SLog3SGamut3.CineToCine+709"
BUIO = 0.28             # luminanza media sotto cui il posto è buio
LUT_BUIO, LUT_LUCE = 0.40, 1.00
LUT_DIRS = [Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Resources/Lut",
            Path.home() / "Movies/CapCut/User Data/Resources/Lut"]
ADJUST = "/Applications/CapCut.app/Contents/MacOS/../Resources/DefaultAdjustBundle/merge_all_adjust_color"
ADJUST_ID, ADJUST_NAME = "7501974767453474064", "26e574bcfa176c3c1a1f7fd5df0a4996"
GAMMA_RE =re.compile(rb'CaptureGammaEquation"\s+value="([^"]+)"')


def gamma(path: str) -> str:
    """Il gamma scritto dalla camera ('s-log3-cine', 'rec709', 'hlg'…), '' se il file non lo dice."""
    p = Path(path)
    for side in (p.with_name(p.stem + "M01.XML"), p.with_name(p.stem + "M01.xml")):
        if side.exists():
            m = GAMMA_RE.search(side.read_bytes())
            if m:
                return m.group(1).decode()
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            for start in (max(0, size - 2_000_000), 0):     # l'XML Sony sta in fondo al file
                f.seek(start)
                m = GAMMA_RE.search(f.read(2_000_000))
                if m:
                    return m.group(1).decode()
    except OSError:
        pass
    return ""


def is_slog3(path: str) -> bool:
    return "s-log3" in gamma(path).lower()


def luce(path: str) -> float | None:
    """Luminanza media 0-1 di tre fotogrammi (a 1 s, a metà e verso la fine)."""
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    path], capture_output=True, text=True).stdout.strip() or 0)
    except ValueError:
        dur = 0
    vals = []
    for t in sorted({min(1.0, dur / 4), dur / 2, dur * 0.8}):
        out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", "scale=320:-2,format=gray,signalstats,"
                                     "metadata=mode=print:key=lavfi.signalstats.YAVG:file=-",
                              "-f", "null", "-"], capture_output=True, text=True).stdout
        vals += [float(x) for x in re.findall(r"YAVG=([0-9.]+)", out)]
    return round(sum(vals) / len(vals) / 255, 3) if vals else None


def intensita(path: str, contesto: str | None = None) -> float | None:
    """Intensità della LUT per questa clip (0-1), None se la clip non è in S-Log3."""
    if not is_slog3(path):
        return None
    if (contesto or "").lower() in ("discoteca", "club", "locale", "serata"):
        return LUT_BUIO
    lum = luce(path)
    return LUT_BUIO if lum is None or lum < BUIO else LUT_LUCE


def cube() -> str | None:
    """Il file .cube della LUT importata in CapCut su questo Mac."""
    for d in LUT_DIRS:
        for f in sorted(d.glob(f"{LUT_NOME}*/*.cube")) + sorted(d.glob(f"{LUT_NOME}*.cube")):
            return str(f)
    return None


def materiale_lut(valore: float, cube_path: str) -> dict:
    """Il materiale «lut» di CapCut (come lo crea Regola > LUT), da mettere in materials.effects."""
    # effect_id e name: quelli che CapCut scrive per Regola > LUT (pacchetto di regolazione colore)
    return {"id": str(uuid.uuid4()).upper(), "effect_id": ADJUST_ID, "resource_id": ADJUST_ID, "third_resource_id": "",
            "name": ADJUST_NAME, "report_name": "", "type": "lut", "sub_type": "none",
            "path": f"{cube_path};{ADJUST}", "value": float(valore), "visible": True, "item_effect_type": 0,
            "category_id": "", "category_name": "", "category_key": "", "sub_category_id": "",
            "sub_category_name": "", "platform": "all", "apply_target_type": 0, "source_platform": 1,
            "version": "", "adjust_params": [], "time_range": None, "formula_id": "",
            "enable_skin_tone_correction": False, "algorithm_artifact_path": "", "intensity_key": "",
            "face_adjust_params": [], "exclusion_group": [], "panel_id": "", "bloom_params": None,
            "request_id": "", "multi_language_current": "", "lumi_hub_path": "",
            "covering_relation_change": 0, "smart_color_mode": 0, "is_from_intelligent_quality": False,
            "transparent_params": "", "is_aigc_filter": False, "aigc_current_artifact_path": ""}


def applica(seg: dict, materials: dict, path: str, contesto: str | None = None, forza=None) -> float | None:
    """Mette la LUT sul segmento video se la clip è in S-Log3. forza: numero (0-1) per imporre
    l'intensità, False per non metterla. Ritorna l'intensità usata, o None."""
    if forza is False:
        return None
    v = float(forza) if isinstance(forza, (int, float)) and not isinstance(forza, bool) else intensita(path, contesto)
    c = cube()
    if v is None or not c:
        return None
    m = materiale_lut(v, c)
    materials.setdefault("effects", []).append(m)
    seg.setdefault("extra_material_refs", []).append(m["id"])
    seg["enable_lut"] = seg["enable_adjust"] = True
    return v


if __name__ == "__main__":
    args = sys.argv[1:]
    ctx = None
    if "--contesto" in args:
        i = args.index("--contesto")
        ctx = args[i + 1]
        del args[i:i + 2]
    print(f"LUT in CapCut: {cube() or 'NON TROVATA (importa ' + LUT_NOME + ' in CapCut)'}")
    for a in args:
        g = gamma(a)
        lum = luce(a)
        v = intensita(a, ctx)
        print(f"{os.path.basename(a)}  gamma={g or '?'}  luce={lum}  LUT={'no' if v is None else f'{v:.0%}'}")
