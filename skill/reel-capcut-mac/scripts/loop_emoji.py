"""Animazioni delle emoji a estrazione con i pesi (Gabriele, 20/11/2026).

Ogni emoji non ha sempre la stessa animazione: c'è quella predefinita (di solito il 60% delle volte), una meno
invadente (30%) e una terza (10%). Vale per tutti i template; i nomi e i pesi li dà il profilo del cliente.
Un'animazione può essere un loop (l'emoji si muove finché resta) o un'entrata (entra e poi sta ferma): un loop
prende il posto del loop dell'emoji, un'entrata prende il posto dell'entrata e toglie il loop.

Le animazioni non si inventano: si prendono, con id veri, dai progetti CapCut di questo Mac in cui sono già state
usate almeno una volta su un testo o un'emoji (basta metterle una volta a mano in CapCut). Una che non si trova
cede la sua parte alla predefinita, e lo si dice.

uso da script:   import loop_emoji
                 sc = loop_emoji.Scelta("Gushing:60,Pixel Bounce:30,Pulse:10", seme="nome del progetto")
                 anim = sc.prossima()            # dict pronto per material_animations, o None se non c'è niente
                 m["animations"] = loop_emoji.applica(m["animations"], anim)
                 sc.resoconto()                  # «Gushing 4, Pixel Bounce 2 (mancano: Pulse)»
uso da terminale: python3 loop_emoji.py           # elenca le animazioni di testo che si trovano sul Mac
"""
from __future__ import annotations

import copy
import glob
import json
import os
import random
from collections import Counter

PROJECTS = os.environ.get("CAPWIZARD_CAPCUT_PROJECTS") or os.path.expanduser(
    "~/Movies/CapCut/User Data/Projects/com.lveditor.draft")

# il predefinito dei preset di Gabriele, sempre disponibile anche se nessun progetto lo usa più
GUSHING = {"anim_adjust_params": None, "category_id": "xunhuan", "category_name": "Loop", "duration": 1300000,
           "id": "7145435416810754562", "material_type": "sticker", "name": "Gushing", "panel": "",
           "path": os.path.expanduser("~/Movies/CapCut/User Data/Cache/effect/7145435416810754562/76cc53a7bb20385d208c858a13cf06fd"),
           "platform": "all", "request_id": "20250319180957F427D785C15F550D5F42", "resource_id": "7145435416810754562",
           "source_platform": 1, "start": 0, "third_resource_id": "7145435416810754562", "type": "loop"}

_LIB: dict[str, dict] | None = None
CACHE = os.path.expanduser("~/Movies/CapCut/User Data/Cache/effect")


def _qui(a: dict) -> dict:
    a = copy.deepcopy(a)
    d = os.path.join(CACHE, str(a["resource_id"]))
    if os.path.isdir(d) and (sub := sorted(os.listdir(d))):
        a["path"] = os.path.join(d, sub[0])
    return a


def applica(animazioni: list[dict], a: dict | None) -> list[dict]:
    """Le animazioni di un'emoji con la scelta dentro: un loop sostituisce il loop; un'entrata sostituisce l'entrata
    e toglie il loop (entra e poi sta ferma). Uscite e il resto restano."""
    if not a:
        return animazioni
    if a["type"] == "loop":
        return [x for x in animazioni if x.get("type") != "loop"] + [a]
    return [x for x in animazioni if x.get("type") not in ("in", "loop")] + [a]


def gia_scelta(animazioni: list[dict], nomi: list[str]) -> bool:
    """L'emoji ha già la sua animazione estratta (un loop, o un'entrata della lista che non sia quella di base)."""
    low = {n.lower() for n in nomi}
    return any(x.get("type") == "loop" or (x.get("type") == "in" and x.get("name", "").lower() in low) for x in animazioni)


def libreria() -> dict[str, dict]:
    """nome (minuscolo) -> animazione di testo (loop o entrata) trovata nei progetti CapCut, anche dentro le clip
    composte, col percorso della cache di QUESTO Mac (i progetti arrivati da un altro Mac hanno il suo)."""
    global _LIB
    if _LIB is not None:
        return _LIB
    lib = {"gushing": GUSHING}

    def walk(d):
        for m in d.get("materials", {}).get("material_animations", []):
            for a in m.get("animations", []):
                if a.get("type") in ("loop", "in") and a.get("material_type") == "sticker" and a.get("name") \
                        and a.get("resource_id") and a["name"].lower() not in lib:
                    lib[a["name"].lower()] = _qui(a)
        for m in d.get("materials", {}).get("drafts", []):
            if isinstance(m.get("draft"), dict):
                walk(m["draft"])

    for f in sorted(glob.glob(os.path.join(PROJECTS, "*", "draft_info.json")), key=os.path.getmtime, reverse=True):
        try:
            with open(f) as fh:
                raw = fh.read()
            walk(json.loads(raw))
        except (OSError, ValueError):
            continue
    _LIB = lib
    return lib


def pesi(testo: str) -> list[tuple[str, float]]:
    """«Gushing:60,Pixel Bounce:30,Pulse:10» -> [(nome, peso), ...]; il primo è il predefinito."""
    out = []
    for parte in (testo or "").split(","):
        if ":" in parte:
            nome, p = parte.rsplit(":", 1)
            out.append((nome.strip(), float(p)))
        elif parte.strip():
            out.append((parte.strip(), 0.0))
    return out or [("Gushing", 100.0)]


class Scelta:
    def __init__(self, testo: str = "Gushing:60", seme: str = ""):
        self.lista = pesi(testo)
        lib = libreria()
        self.mancano = [n for n, _ in self.lista if n.lower() not in lib]
        default = self.lista[0][0] if self.lista[0][0].lower() in lib else "Gushing"
        tot = Counter()
        for n, p in self.lista:
            tot[n if n.lower() in lib else default] += p
        self.nomi = [n for n in tot if tot[n] > 0] or [default]
        self.p = [tot[n] for n in self.nomi] if any(tot.values()) else [1.0]
        self.rng = random.Random(seme)
        self.usati = Counter()

    def prossima(self) -> dict | None:
        lib = libreria()
        nome = self.rng.choices(self.nomi, weights=self.p)[0]      # le percentuali sono quelle del profilo
        self.usati[nome] += 1
        a = lib.get(nome.lower())
        return copy.deepcopy(a) if a else None

    def resoconto(self) -> str:
        s = ", ".join(f"{n} {c}" for n, c in self.usati.most_common()) or "nessuna emoji"
        return s + (f" (non trovati sul Mac, sostituiti dal predefinito: {', '.join(self.mancano)})" if self.mancano else "")


if __name__ == "__main__":
    for n, a in sorted(libreria().items()):
        print(f"{a['type']:<5} {a['name']:<34} {a['resource_id']}  {a.get('duration', 0) / 1e6:.2f} s")
