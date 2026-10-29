"""A client's template as one zip: the client's skill and the CapCut project folder, linked by name.

«Esporta» on a client writes a .zip with one or both of:

    reel-<cliente>/SKILL.md     the client's profile for Claude; its frontmatter holds the same fields as
                                «Nuovo template» (metadata capwizard-cliente, -agenzia, -colore, -font) and
                                capwizard-template = the name of the CapCut folder next to it
    <TEMPLATE NAME>/            a copy of the CapCut project folder (CapCut's own stays where it is),
                                without CapCut's caches of clips (videoAlg, matting), plus
      capwizard-media/          the sounds, music and pictures it uses from outside the folder
      capwizard.json            where each of those files was, so the timeline finds them again

«Carica template» takes that zip, a .skill, or a dropped folder (a skill, a CapCut project, or a
folder holding both): the project goes into CapCut under its folder's name and the profile becomes a
skill (in the shared brain when there is one), with the fields of the form. A project with no skill
gets a profile made from the form, so the client shows up in «Clienti» either way.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path

from capwizard_app import agent, brain, teamshare, vault
from capwizard_app.capcut import CAPCUT_PROJECTS_DIR

FORMATO = "1"
MAX_FILES = 50000
MAX_BYTES = 4 * 1024 ** 3          # what a template can unpack to, at most
MEDIA_DIR, MAP = "capwizard-media", "capwizard.json"
COSE = ("completo", "skill", "cartella")


def slug(nome: str) -> str:
    s = vault.norm(nome)
    return "reel-" + (re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "cliente")


def _split(text: str) -> tuple[str, str]:
    """A SKILL.md: (frontmatter, body)."""
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _links(text: str) -> str:
    """Obsidian links as plain names: [[Stile X|x]] → Stile X."""
    return re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", r"\1", text)


def _plain(text: str) -> str:
    return _links(text).replace("**", "").strip()


def _q(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)          # a YAML double-quoted string


def _skill_md(name: str, description: str, fields: dict, body: str) -> str:
    meta = "".join(f"  capwizard-{k}: {_q(v)}\n" for k, v in fields.items() if v)
    return (f"---\nname: {name}\ndescription: {_q(description)}\nmetadata:\n  capwizard-formato: {_q(FORMATO)}\n"
            f"{meta}---\n\n{body.strip()}\n")


def _intro(cliente: str) -> str:
    return (f"# Reel di {cliente}\n\nRispondi in italiano. Segui la skill reel-capcut-mac per il processo generale "
            f"di montaggio; qui c'è solo ciò che vale per {cliente} e che prevale.\n\n")


# ---------------------------------------------------------------------------
# Esporta
# ---------------------------------------------------------------------------

def _copy_project(tpl: str, dest: Path) -> int:
    """A copy of the CapCut folder in dest/, with the media it uses from elsewhere in capwizard-media/.
    CapCut's own folder is only read."""
    src = CAPCUT_PROJECTS_DIR / tpl
    shutil.copytree(src, dest, ignore=teamshare._SKIP_TEMPLATE)
    inside = str(src) + "/"
    media = sorted(p for p in teamshare._paths(teamshare._docs(dest))
                   if p.lower().endswith(teamshare.MEDIA_EXT) and os.path.isfile(p) and not p.startswith(inside))
    names, used = {}, set()
    for p in media:
        b = os.path.basename(p)
        stem, ext, k = os.path.splitext(b)[0], os.path.splitext(b)[1], 2
        while b in used:
            b, k = f"{stem} ({k}){ext}", k + 1
        used.add(b)
        names[p] = b
        (dest / MEDIA_DIR).mkdir(exist_ok=True)
        shutil.copy2(p, dest / MEDIA_DIR / b)
    (dest / MAP).write_text(json.dumps({"formato": FORMATO, "nome": tpl, "cartella": str(src), "media": names},
                                       ensure_ascii=False, indent=1))
    return len(media)


def export(client_id: str, dest: str, cosa: str = "completo", log=print) -> dict:
    """Write the client's template to dest (a .zip): the skill, the CapCut folder, or both."""
    if cosa not in COSE:
        return {"ok": False, "msg": "Scegli cosa esportare."}
    c = agent.get_client(client_id)
    if not c:
        return {"ok": False, "msg": "Cliente non trovato."}
    out = Path(dest)
    if out.suffix.lower() != ".zip":
        out = out.with_name(out.name + ".zip")
    tpl = c.get("template", "")
    if cosa != "skill" and not (tpl and (CAPCUT_PROJECTS_DIR / tpl / "draft_meta_info.json").exists()):
        return {"ok": False, "msg": f"Il template CapCut «{tpl or '?'}» di {c['nome']} non c'è su questo Mac."}
    colore = (c.get("colore") or "").upper()
    fields = {"cliente": c["nome"], "agenzia": c.get("agenzia", ""),
              "colore": colore if re.fullmatch(r"#[0-9A-F]{6}", colore) else "", "font": c.get("font", ""),
              "template": tpl}
    n = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if cosa != "cartella":
            if c.get("source") == "vault":
                d = vault.dati_cliente(client_id)
                fields["font"] = _plain(d.get("font", "")) if d.get("ok") else ""
                name = slug(c["nome"])
                description = (f"Monta e corregge i reel di {c['nome']}" + (f" ({fields['agenzia']})" if fields["agenzia"] else "")
                               + f" in CapCut con {tpl}: colore, font, regole e lezioni del cliente.")
                stile = vault.read_note(f"Stile {c['stile']}") if c.get("stile") else ""
                body = (_intro(c["nome"]) + re.sub(r"^# .*\n", "", _links(vault.rules_text(client_id)), count=1)
                        + (f"\n\n## Stile dell'agenzia\n\n{re.sub(r'^#+ .*\n', '', _links(stile), count=1).strip()}" if stile else ""))
                (root / name).mkdir()
            else:
                md = Path(c["path"])
                fm = agent._frontmatter(md.read_text())
                name = fm.get("name") or md.parent.name
                description = fm.get("description", "")
                body = _split(md.read_text())[1]
                shutil.copytree(md.parent, root / name, ignore=shutil.ignore_patterns(
                    "SKILL.md", ".git", "__pycache__", ".DS_Store", "*.pyc"))
            (root / name / "SKILL.md").write_text(_skill_md(name, description, fields, body))
        if cosa != "skill":
            n = _copy_project(tpl, root / tpl)
        part = out.with_name(out.name + ".parziale")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(root.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    z.write(f, f.relative_to(root))
        part.replace(out)
    mb = out.stat().st_size / 1e6
    what = {"completo": "Template completo", "skill": "Skill", "cartella": "Cartella CapCut"}[cosa]
    log(f"[template] {what} di {c['nome']} esportato in {out} ({mb:.1f} MB)")
    return {"ok": True, "path": str(out), "msg": f"{what} di {c['nome']} esportato: {out.name} ({mb:.1f} MB"
                                                 + (f", con {n} file di suoni e grafica" if n else "") + ")."}


# ---------------------------------------------------------------------------
# Carica
# ---------------------------------------------------------------------------

def _skip(name: str) -> bool:
    return name.startswith("__MACOSX/") or "/__MACOSX/" in name or name.endswith(".DS_Store")


def _depth(d: str) -> int:
    return d.count("/") if d else -1


class _Source:
    """A zip (or .skill) or a folder, read the same way: relative posix paths of its files."""

    def __init__(self, path: str):
        p = Path(path)
        self.zip = None
        if p.is_dir():
            self.dir = p
            self.names = []
            for f in p.rglob("*"):
                rel = f.relative_to(p).as_posix()
                if f.is_file() and not f.is_symlink() and not _skip(rel):
                    self.names.append(rel)
                    if len(self.names) > MAX_FILES:
                        raise ValueError("La cartella ha troppi file per essere un template.")
            self.label = p.name
            return
        try:
            self.zip = zipfile.ZipFile(p)
        except (zipfile.BadZipFile, OSError) as e:
            raise ValueError("Non è uno zip né una cartella.") from e
        infos = [i for i in self.zip.infolist() if not i.is_dir() and not _skip(i.filename)]
        if not infos or len(infos) > MAX_FILES:
            raise ValueError("Lo zip è vuoto o ha troppi file per essere un template.")
        if sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError("Lo zip è troppo grande per essere un template.")
        for i in infos:
            parts = Path(i.filename).parts
            if i.filename.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise ValueError(f"Lo zip contiene un percorso non valido: {i.filename}")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError(f"Lo zip contiene un collegamento: {i.filename}")
        self.infos = {i.filename: i for i in infos}
        self.names = list(self.infos)
        self.label = p.stem

    def read(self, name: str) -> bytes:
        return self.zip.read(name) if self.zip else (self.dir / name).read_bytes()

    def extract(self, prefix: str, dest: Path) -> None:
        """Every file under prefix/ (the whole source for "") into dest/."""
        lead = f"{prefix}/" if prefix else ""
        for name in self.names:
            if not name.startswith(lead):
                continue
            out = dest / name[len(lead):]
            out.parent.mkdir(parents=True, exist_ok=True)
            if self.zip:
                with self.zip.open(self.infos[name]) as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
            else:
                shutil.copy2(self.dir / name, out)

    def size(self) -> int:
        if self.zip:
            return sum(i.file_size for i in self.infos.values())
        return sum((self.dir / n).stat().st_size for n in self.names)


def _layout(src: _Source) -> tuple[str | None, str | None]:
    """Where the skill and the CapCut project are (their folder, "" = the source itself, None = missing).
    Each is the shallowest folder with its marker: a project's compound clips have their own, deeper."""
    def shallowest(marker: str) -> str | None:
        found = sorted({n[: -len(marker)].rstrip("/") for n in src.names if n == marker or n.endswith("/" + marker)},
                       key=lambda d: (_depth(d), d))
        if not found:
            return None
        top = [d for d in found if _depth(d) == _depth(found[0])]
        if len(top) > 1:
            raise ValueError(f"Ci sono più {'skill' if marker == 'SKILL.md' else 'progetti CapCut'} insieme "
                             f"({', '.join(top[:3])}): caricane uno alla volta.")
        return found[0]
    skill, proj = shallowest("SKILL.md"), shallowest("draft_meta_info.json")
    if skill is None and proj is None:
        raise ValueError("Non trovo né una skill (SKILL.md) né un progetto CapCut (draft_meta_info.json).")
    return skill, proj


def _project_name(src: _Source, proj: str) -> str:
    name = proj.rsplit("/", 1)[-1] if proj else ""
    if not name:
        try:
            name = json.loads(src.read(MAP)).get("nome", "")
        except (KeyError, OSError, ValueError):
            name = ""
    name = name or src.label
    if teamshare.safe(name) != name:
        raise ValueError(f"Il nome del progetto CapCut «{name}» non è valido.")
    return name


def _under(path: str, folder: str) -> str | None:
    """path relative to folder, when it is inside it."""
    if not folder:
        return path
    return path[len(folder) + 1:] if path.startswith(folder + "/") else None


def read(path: str) -> dict:
    """What a template zip or folder holds, without installing anything (for the «Carica template» form)."""
    try:
        src = _Source(path)
        skill, proj = _layout(src)
        tpl = _project_name(src, proj) if proj is not None else ""
        fm = agent._frontmatter(src.read(f"{skill}/SKILL.md" if skill else "SKILL.md").decode("utf-8")) \
            if skill is not None else {"metadata": {}}
        media = 0
        if proj is not None:
            try:
                media = len(json.loads(src.read(f"{proj}/{MAP}" if proj else MAP)).get("media", {}))
            except (KeyError, OSError, ValueError):
                pass
        size = src.size()
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return {"ok": False, "msg": str(e)}
    meta = fm["metadata"]
    tpl = (tpl or meta.get("capwizard-template", "")).upper()     # templates and clients are named in capitals
    cliente = (meta.get("capwizard-cliente") or re.sub(r"^TEMPLATE\b[\s_-]*", "", tpl, flags=re.I).strip()
               or (skill.rsplit("/", 1)[-1] if skill else src.label)).upper()
    return {"ok": True, "ha_skill": skill is not None, "ha_cartella": proj is not None,
            "skill": (skill.rsplit("/", 1)[-1] if skill else src.label) if skill is not None else "",
            "descrizione": fm.get("description", ""), "cliente": cliente,
            "agenzia": meta.get("capwizard-agenzia", ""), "colore": meta.get("capwizard-colore", ""),
            "font": meta.get("capwizard-font", ""), "template": tpl, "file": media, "byte": size,
            "template_qui": bool(tpl and (CAPCUT_PROJECTS_DIR / tpl).exists()),
            "cliente_esiste": any(vault.norm(c["nome"]) == vault.norm(cliente) for c in agent.list_clients())}


def _relink(project: Path, target: str, media_dir: Path) -> None:
    """The copied media back into the timeline: each file where it was on the other Mac → its copy in
    media_dir, and paths inside the old project folder → the folder it gets in CapCut."""
    try:
        m = json.loads((project / MAP).read_text())
    except (OSError, ValueError):
        m = {}
    media = {old: str(media_dir / name) for old, name in (m.get("media") or {}).items()
             if "/" not in name and (media_dir / name).is_file()}
    old_dir = (m.get("cartella") or "").rstrip("/")
    new_dir = str(CAPCUT_PROJECTS_DIR / target)

    def fix(s: str):
        if s in media:
            return media[s]
        if old_dir and s.startswith(old_dir + "/"):
            return new_dir + s[len(old_dir):]
        return None
    teamshare._rewrite(teamshare._docs(project), fix)


def install(path: str, cliente: str, agenzia: str, colore: str, font: str, log=print) -> dict:
    """Install a template zip or folder: the CapCut project under its folder's name and the profile as a
    skill with the fields of the form (the same as «Nuovo template»)."""
    info = read(path)
    if not info["ok"]:
        return info
    cliente, agenzia, font = cliente.strip().upper(), agenzia.strip().upper(), font.strip()
    colore = (colore or "").strip().upper()
    if not cliente:
        return {"ok": False, "msg": "Scrivi il nome del cliente."}
    if not re.fullmatch(r"#[0-9A-F]{6}", colore):
        return {"ok": False, "msg": "Colore non valido: usa #RRGGBB."}
    src = _Source(path)
    skill, proj = _layout(src)
    tpl = info["template"]
    name = info["skill"] if (info["ha_skill"] and info["skill"].startswith("reel-")
                             and vault.norm(cliente) == vault.norm(info["cliente"])) else slug(cliente)
    msgs = []
    with tempfile.TemporaryDirectory() as tmp:
        if proj is not None:
            staged, media_dir = Path(tmp) / "progetto", Path(tmp) / MEDIA_DIR
            src.extract(proj, staged)
            if (staged / MEDIA_DIR).is_dir():
                (staged / MEDIA_DIR).rename(media_dir)   # outside the project: porta_media copies them for CapCut
            _relink(staged, tpl, media_dir)
            (staged / MAP).unlink(missing_ok=True)
            msgs.append(teamshare.install_staged(staged, tpl, log))
        prof = Path(tmp) / "skill"
        if skill is not None:
            src.extract(skill, prof)
            inner = _under(proj, skill) if proj is not None else None
            if inner:                                    # a project kept inside the skill folder: it is in CapCut now
                shutil.rmtree(prof / inner, ignore_errors=True)
            body = _split((prof / "SKILL.md").read_text())[1]
            old = (info["colore"] or "").upper()
            if old and old != colore:
                body = re.sub(re.escape(old), colore, body, flags=re.I)
            description = info["descrizione"] or f"Monta e corregge i reel di {cliente} in CapCut con {tpl}."
        else:
            prof.mkdir()
            description = f"Monta e corregge i reel di {cliente}" + (f" ({agenzia})" if agenzia else "") + f" in CapCut con {tpl}."
            body = (_intro(cliente) + "## Cliente\n" + (f"- **Agenzia:** {agenzia}\n" if agenzia else "")
                    + f"- **Colore:** {colore} (parole chiave e riga colorata del titolo) · Bianco\n"
                    + (f"- **Font:** {font}\n" if font else "") + f"- **Template CapCut:** {tpl}\n")
        fields = {"cliente": cliente, "agenzia": agenzia, "colore": colore, "font": font, "template": tpl}
        (prof / "SKILL.md").write_text(_skill_md(name, description, fields, body))
        home = brain.BRAIN / "skills" if brain.available() else agent.SKILLS_DIR
        dest, link = home / name, agent.SKILLS_DIR / name
        if dest.exists() or link.is_symlink() or link.exists():
            teamshare.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup = teamshare.BACKUP_DIR / f"{name} {time.strftime('%m%d-%H%M%S')}"
            shutil.move(str(dest if dest.exists() else link.resolve()), str(backup))
            if link.is_symlink():
                link.unlink()
            msgs.append(f"Il profilo di prima è in {backup}.")
        home.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prof, dest)
        if home != agent.SKILLS_DIR:
            agent.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            link.symlink_to(dest, target_is_directory=True)
            brain.push_soon(f"Template di {cliente} caricato", log)
    if proj is None and not info["template_qui"]:
        msgs.append(f"Il template CapCut «{tpl}» non c'è su questo Mac: caricalo con la sua cartella.")
    log(f"[template] caricato {cliente} da {path}")
    return {"ok": True, "id": name, "msg": " ".join([f"{cliente} è tra i clienti.", *msgs])}
