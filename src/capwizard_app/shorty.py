"""Edits of a property's renders with ShortyEdit: a whole video from the renders, or b-roll clips
for a talking reel.

ShortyEdit is a connector of the user's Claude (claude.ai). It edits with Remotion and renders the
MP4 inside its own panel, which opens in a Claude chat: it can't run in the app's headless montages.
So the app prepares the work — the client's style and rules, the property's details, the list of
renders, what to make and where it goes — and hands it to a new Claude chat, where the user drops
the renders into Shorty's panel and saves the MP4s it renders. B-roll clips saved in a folder are
then used by a montage like any other b-roll.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from capwizard_app import agent, aiservices, vault

CHAT_URL = "https://claude.ai/new"
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff")
VIDEOS = (".mp4", ".mov", ".m4v", ".webm")
_URL_MAX = 6000          # longer prompts go through the clipboard only


def status() -> dict:
    """Is ShortyEdit connected to the user's Claude (claude.ai → Impostazioni → Connettori)?"""
    srv = next((s for s in aiservices._servers() if "shorty" in s["nome"].lower()), None)
    return {"collegato": bool(srv and srv["collegato"]), "da_autorizzare": bool(srv and srv["da_autorizzare"]),
            "connettori": aiservices.CONNECTORS_URL}


def renders(folder: str) -> list[str]:
    """The renders of a folder (and of its first-level subfolders), images and videos."""
    root = Path(folder)
    if not root.is_dir():
        return []
    natural = lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]   # 2 before 11
    found = [p for p in sorted(root.iterdir(), key=natural) if p.is_file()]
    for d in sorted((x for x in root.iterdir() if x.is_dir() and not x.name.startswith(".")), key=natural):
        found += [p for p in sorted(d.iterdir(), key=natural) if p.is_file()]
    return [str(p.relative_to(root)) for p in found if p.suffix.lower() in IMAGES + VIDEOS and not p.name.startswith("._")]


def _rules(client: dict | None) -> str:
    """The client's style and rules, as text (the chat can't read files on this Mac)."""
    if not client:
        return ""
    if client.get("source") == "vault":
        return vault.rules_text(client["id"])
    try:
        text = Path(client["path"]).read_text()
    except OSError:
        return ""
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


def prompt(mode: str, client_id: str, folder: str, out: str, dati: str = "", durata: float = 0,
           clip: int = 0, note: str = "") -> dict:
    client = agent.get_client(client_id) if client_id else None
    files = renders(folder)
    if not files:
        return {"ok": False, "msg": "Nella cartella non ci sono render (immagini o video)."}
    name = client["nome"] if client else "il cliente"
    rules = _rules(client)
    if len(rules) > 7000:
        rules = rules[:7000].rsplit("\n", 1)[0] + "\n[…]"
    stills = [f for f in files if f.lower().endswith(IMAGES)]
    moving = [f for f in files if f.lower().endswith(VIDEOS)]
    lines = [f"Usa il connettore **ShortyEdit** per montare "
             + ("**un video verticale intero fatto solo con i render**" if mode == "video"
                else f"**{clip or 'alcune'} clip di b-roll** dai render, da mettere dentro un reel parlato montato in CapCut")
             + f" di un immobile per **{name}**. Ti preparo tutto io da CapWizard.", ""]
    lines += [f"## I render ({len(files)}, cartella `{folder}` sul mio Mac)",
              "Li importo io nel pannello di Shorty appena lo apri: dimmi tu quando.",
              *[f"- {f}" for f in files[:60]], *(["- …"] if len(files) > 60 else []), ""]
    if dati.strip():
        lines += ["## L'immobile", dati.strip(), ""]
    lines += ["## Cosa fare"]
    if mode == "video":
        lines += [f"- Video 9:16 (1080×1920, 30 fps) di circa **{durata:g} s**." if durata else "- Video 9:16 (1080×1920, 30 fps), 20-40 s.",
                  "- Ritmo: un render ogni 2-4 s, l'ordine di una visita (esterno, ingresso, zona giorno, cucina, camere, bagni, terrazzi).",
                  "- Testi nello stile del cliente (colore, font, maiuscole come nelle sue regole qui sotto): gancio iniziale, "
                  "poi prezzo, metrature e dati dell'immobile quando c'entrano, chiusura con l'invito a contattare.",
                  "- Musica: scegli con choose_soundtrack (gratis); niente musica generata se non te lo chiedo."]
    else:
        lines += [f"- {clip or 'Alcune'} clip 9:16 (1080×1920, 30 fps) da 3-6 s, una per stanza o per inquadratura, "
                  "senza testi né musica: le monta poi CapWizard sotto il parlato.",
                  "- Un movimento solo per clip, già in corso dal primo fotogramma e uguale fino all'ultimo."]
    lines += ["- Movimenti credibili, come una ripresa vera. Se le regole del cliente qui sotto vietano lo zoom o il pan "
              "su un render fermo, i movimenti falli solo sui render che sono già video; se sono tutte immagini ferme, "
              "dimmelo prima di iniziare (si possono animare con generate_video_shot di Shorty, a pagamento, o con Higgsfield).",
              f"- Niente persone aggiunte, niente scritta «rendering indicativo», niente dati inventati.",
              "- Prima del render finale fai un'anteprima (source_render) e guardala; poi renderizza."]
    if moving:
        lines += [f"- {len(moving)} render sono già video animati, {len(stills)} sono immagini ferme."]
    if note.strip():
        lines += ["", "## Note (vincolanti)", note.strip()]
    lines += ["", "## Crediti", "Montaggio e render nel pannello non costano crediti. Gli strumenti a pagamento di Shorty "
              "(stabilizzazione, slow motion, upscale, musica o riprese generate) usali solo se te lo chiedo, dicendomi prima quanti crediti costano.",
              "", "## Alla fine",
              f"Dimmi di salvare gli MP4 in `{out}`" + (" con nomi brevi che dicono la stanza (es. `cucina.mp4`): "
                                                        "li userò come cartella b-roll in CapWizard." if mode != "video" else ".")]
    if rules:
        lines += ["", f"## Stile e regole di {name} (dal suo profilo in CapWizard)", rules]
    text = "\n".join(lines)
    url = CHAT_URL + ("?q=" + urllib.parse.quote(text) if len(urllib.parse.quote(text)) <= _URL_MAX else "")
    return {"ok": True, "prompt": text, "url": url, "nel_link": "?q=" in url, "render": len(files), **status()}
