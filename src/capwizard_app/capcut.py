"""What the app needs to know about CapCut and Claude Code on this Mac.

These used to come from the `capwizard` engine (capwizard-mcp). Since reels are
edited by Claude Code with the reel-capcut-mac skill, the app only keeps the
pieces it uses itself: where CapCut keeps its projects, how to find and start
`claude`, and a quick read of what a client's template offers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CAPCUT_PROJECTS_DIR = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def is_video(p: Path) -> bool:
    # Skip macOS AppleDouble sidecars (e.g. "._C0867.MP4") that share a video
    # suffix but hold no media.
    return not p.name.startswith("._") and p.suffix.lower() in VIDEO_EXTS


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

_CLAUDE_CLI_FALLBACKS = (
    Path.home() / ".claude/local/claude",
    Path("/usr/local/bin/claude"),
    Path("/opt/homebrew/bin/claude"),
)


def find_claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in _CLAUDE_CLI_FALLBACKS:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    # the CLI that comes with the Claude desktop app (no `claude` in PATH)
    bundled = sorted(Path.home().glob("Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"),
                     key=lambda p: p.stat().st_mtime)
    return str(bundled[-1]) if bundled else None


def clean_env() -> dict[str, str]:
    """Environment for spawning `claude` without inheriting a host session.

    When this app is launched from a terminal inside the Claude desktop app,
    variables like CLAUDECODE / CLAUDE_CODE_* / ANTHROPIC_BASE_URL leak in and
    make the nested CLI act as a child session: it ignores the Keychain login
    and reports "Not logged in". Stripping them lets it use the user's own login.
    """
    env = dict(os.environ)
    hosted = "CLAUDECODE" in env
    for key in list(env):
        if key.startswith("CLAUDE") or key in ("AI_AGENT", "BAGGAGE"):
            env.pop(key, None)
    if hosted:
        env.pop("ANTHROPIC_BASE_URL", None)   # host's proxy, not the user's
    return env


def ai_status() -> dict:
    """Whether Claude Code is there — cheap, doesn't call the model."""
    cli = find_claude_cli()
    return {"available": bool(cli), "method": "claude-cli" if cli else None,
            "claude_cli": cli, "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def ai_ping(model: str = "haiku") -> dict:
    """Send Claude Code a trivial request to check it answers with the user's login."""
    cli = find_claude_cli()
    if not cli:
        return {"ok": False, "method": None,
                "message": "Non trovo Claude Code: installalo e fai il login una volta (`claude` → `/login`)."}
    try:
        r = subprocess.run([cli, "-p", "--model", model, "--no-session-persistence", "Rispondi solo: OK"],
                           env=clean_env(), capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": "claude-cli", "message": "Claude non ha risposto entro 90 secondi."}
    out = (r.stdout or r.stderr).strip()
    if r.returncode != 0:
        if "login" in out.lower():
            out = "Claude non è collegato: apri il Terminale, scrivi `claude`, poi `/login`."
        return {"ok": False, "method": "claude-cli", "message": out[:300]}
    return {"ok": True, "method": "claude-cli", "message": out[:120] or "OK"}


# ---------------------------------------------------------------------------
# What a template offers (a quick read of its draft, for the app's checklist)
# ---------------------------------------------------------------------------

# While CapCut has a project open, or is saving it, `draft_info.json` can be
# missing or half-written for a moment: CapCut works on `template-2.tmp` and
# keeps the last saved copy in `draft_info.json.bak`.
_DRAFT_FILES = ("draft_info.json", "draft_info.json.bak", "template-2.tmp")


def load_template(template_name: str, projects_dir: Path = CAPCUT_PROJECTS_DIR) -> dict:
    """The template's draft as a dict. Rides out CapCut touching the file:
    retries for a few seconds and falls back on CapCut's own copies."""
    folder = projects_dir / template_name
    for attempt in range(8):
        for name in _DRAFT_FILES:
            try:
                return json.loads((folder / name).read_text())
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
                continue
        time.sleep(0.4)
    if folder.is_dir():
        raise ValueError(f"Non riesco a leggere il template «{template_name}»: CapCut lo sta "
                         f"usando o salvando in questo momento. Chiudi quel progetto in CapCut "
                         f"e rilancia la generazione.")
    raise ValueError(f"Il template «{template_name}» non esiste più nella cartella progetti di "
                     f"CapCut ({projects_dir}). Scegline un altro.")


# substring → category, checked in order
_TRANSITION_HINTS = ("whoosh", "woosh", "swish", "swoosh", "transition", "riser", "swipe")


_EMPHASIS_HINTS = ("ding", "explosion", "boom", "chakin", "money", "ka-ching",
                   "kaching", "notification", "alert", "pop", "tap", "click",
                   "magic", "reveal", "tada", "ta-da")


def _seg_seconds(seg: dict) -> float:
    return ((seg.get("target_timerange") or {}).get("duration", 0) or 0) / 1_000_000


def _classify_audio(material: dict, seg_seconds: float = 0.0) -> str:
    """Return 'transition' | 'emphasis' | 'music' | 'leftover' for a template
    audio segment.

    Judged on how the template *uses* it, not on the length of the source file:
    a 1-second snippet of a 2-minute file is not a music bed.
      * `video_original_sound` is audio detached from a video of the project the
        template was copied from (someone else's voice/ambience) → leftover;
      * music library tracks, or anything laid for 10 s or more → music;
      * the rest are short effects, told apart by name.
    """
    name = (material.get("name") or "").lower()
    typ = material.get("type", "")

    if typ == "video_original_sound":
        return "leftover"
    if typ == "music" or seg_seconds >= 10:
        return "music"
    if any(k in name for k in _TRANSITION_HINTS):
        return "transition"
    if any(k in name for k in _EMPHASIS_HINTS):
        return "emphasis"
    # default: short clip with unclear name → treat as emphasis (less disruptive)
    return "emphasis"


_CATEGORY_LABEL = {"transition": "transizione", "emphasis": "enfasi / effetto"}


def _is_emoji_like(text: str) -> bool:
    """True if the text has no Latin letters — likely an emoji-only overlay."""
    s = text.strip()
    if not s:
        return True
    return not any(c.isalpha() and ord(c) < 128 for c in s)


def _materials_index(info: dict) -> dict[str, tuple[str, dict]]:
    """material id → (bucket name, material)."""
    out: dict[str, tuple[str, dict]] = {}
    for bucket, items in info.get("materials", {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and "id" in it:
                out[it["id"]] = (bucket, it)
    return out


def _text_of(index: dict, seg: dict) -> tuple[dict | None, dict | None]:
    """(text-template wrapper or None, inner text material or None) of a text segment."""
    bucket, mat = index.get(seg.get("material_id"), (None, None))
    if bucket == "texts":
        return None, mat
    if bucket == "text_templates":
        tir = (mat.get("text_info_resources") or [{}])[0]
        b2, inner = index.get(tir.get("text_material_id"), (None, None))
        return mat, inner if b2 == "texts" else None
    return None, None


def _shown_text(inner: dict) -> str:
    try:
        return json.loads(inner["content"]).get("text", "") or ""
    except (KeyError, TypeError, json.JSONDecodeError):
        return inner.get("recognize_text") or ""


def _is_caption_seg(index: dict, seg: dict) -> bool:
    """A subtitle is a text CapCut made from speech: built on a subtitle text
    template, or carrying per-word timing. (The track's flag doesn't tell: a
    title typed on what used to be a caption track is still a title.)"""
    wrapper, inner = _text_of(index, seg)
    if wrapper is not None and "subtitle" in (wrapper.get("type") or ""):
        return True
    return bool(inner and any(t.strip() for t in (inner.get("words") or {}).get("text") or []))


@dataclass
class KitSlot:
    """One text of the template, reusable as a styled line."""
    id: str                 # "T1", "T2"…
    text: str
    start: float            # seconds in the template
    duration: float
    y: float                # CapCut vertical position: +1 top … -1 bottom
    size: float             # how big it reads: font size × scale
    track_id: str
    seg: dict
    room: int = 0           # characters that fit: the longest line among same-size siblings


@dataclass
class KitGroup:
    """Texts that are on screen together: a title, a label, a list."""
    id: str                 # "G1", "G2"…
    slots: list[KitSlot]    # top line first

    @property
    def start(self) -> float:
        return min(s.start for s in self.slots)

    @property
    def end(self) -> float:
        return max(s.start + s.duration for s in self.slots)


def _kit_groups(info: dict, index: dict) -> list[KitGroup]:
    """Every non-subtitle text of the draft, grouped by being on screen together."""
    found: list[KitSlot] = []
    for tr in info["tracks"]:
        if tr["type"] != "text":
            continue
        for s in tr["segments"]:
            if _is_caption_seg(index, s):
                continue
            _, inner = _text_of(index, s)
            text = _shown_text(inner).strip() if inner else ""
            if not text:
                continue
            clip = s.get("clip") or {}
            try:
                font_size = float(json.loads(inner["content"])["styles"][0].get("size") or 10)
            except (KeyError, TypeError, IndexError, ValueError, json.JSONDecodeError):
                font_size = 10.0
            tt = s.get("target_timerange") or {}
            found.append(KitSlot(
                id="", text=text, start=(tt.get("start", 0) or 0) / 1e6, duration=_seg_seconds(s),
                y=float((clip.get("transform") or {}).get("y", 0.0)),
                size=font_size * float((clip.get("scale") or {}).get("x", 1.0)),
                track_id=tr["id"], seg=s))
    found.sort(key=lambda k: (k.start, -k.y))
    groups: list[list[KitSlot]] = []
    group_end = 0.0
    for slot in found:
        if groups and slot.start < group_end - 0.3:
            groups[-1].append(slot)
            group_end = max(group_end, slot.start + slot.duration)
        else:
            groups.append([slot])
            group_end = slot.start + slot.duration
    out, n = [], 0
    for g, slots in enumerate(groups, 1):
        slots.sort(key=lambda k: (-k.y, k.start))            # top line first
        for slot in slots:
            n += 1
            slot.id = f"T{n}"
            slot.room = max(len(line) for k in slots if abs(k.size - slot.size) <= 0.05 * slot.size
                            for line in k.text.splitlines() or [""])
        out.append(KitGroup(id=f"G{g}", slots=slots))
    return out


def _sounds_with_times(info: dict, index: dict) -> list[tuple[float, float, str, str]]:
    """(start, seconds, name, kind) of every usable sound of the draft."""
    out = []
    for tr in info["tracks"]:
        if tr["type"] != "audio":
            continue
        for s in tr["segments"]:
            bucket, mat = index.get(s.get("material_id"), (None, None))
            if bucket != "audios":
                continue
            cat = _classify_audio(mat, _seg_seconds(s))
            if cat != "leftover":
                start = ((s.get("target_timerange") or {}).get("start", 0) or 0) / 1e6
                out.append((start, _seg_seconds(s), mat.get("name") or "suono",
                            "musica" if cat == "music" else _CATEGORY_LABEL.get(cat, cat)))
    return sorted(out)


_LOOK_TRACKS = ("adjust", "filter", "effect")


def template_kit(template_name: str, projects_dir: Path = CAPCUT_PROJECTS_DIR) -> dict:
    """Everything reusable a template offers, described for the AI and for the
    app's checklist: text blocks (with the sounds the template starts along
    with them), subtitle samples, sounds, music, colour and effect layers."""
    info = load_template(template_name, projects_dir)
    index = _materials_index(info)
    sounds = _sounds_with_times(info, index)
    effects = [(t, n) for t, _, n, kind in sounds if kind != "musica"]
    biggest = max((s.size for g in _kit_groups(info, index) for s in g.slots), default=1.0)
    groups = []
    for g in _kit_groups(info, index):
        along = []
        for slot in g.slots:
            for t, name in effects:
                if -0.6 <= t - slot.start <= 0.5 and name not in along:
                    along.append(name)
        groups.append({
            "id": g.id, "start": g.start, "duration": g.end - g.start, "sounds": along,
            "slots": [{
                "id": s.id, "text": " ".join(s.text.split()), "after": s.start - g.start,
                "duration": s.duration, "ends_with_group": g.end - (s.start + s.duration) < 0.3,
                "from_top": round((1 - s.y) * 50), "size": round(100 * s.size / biggest),
                "chars": s.room,
                "emoji": _is_emoji_like(s.text),
            } for s in g.slots],
        })
    looks = []
    for tr in info["tracks"]:
        if tr["type"] in _LOOK_TRACKS:
            for s in tr["segments"]:
                mat = index.get(s.get("material_id"), (None, {}))[1]
                name = mat.get("name") or mat.get("type") or tr["type"]
                if name not in looks:
                    looks.append(name)
    captions = sum(1 for t in info["tracks"] if t["type"] == "text"
                   for s in t["segments"] if _is_caption_seg(index, s))
    return {
        "name": template_name, "groups": groups, "captions": captions,
        "sounds": sorted({n for _, _, n, kind in sounds if kind != "musica"}),
        "music": sorted({n for _, _, n, kind in sounds if kind == "musica"}),
        "looks": looks,
    }