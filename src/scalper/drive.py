"""Export en environnement de DEV uniquement : dossier horodaté + upload Google Drive.

Actif UNIQUEMENT si ``SCALPER_ENV=dev`` (voir :func:`is_dev`). En prod, rien ne change :
le comportement historique reste le zip (`packager.zip_run_export`).

En dev, quand un export zip est demandé (`--zip-export` / `SCALPER_ZIP_EXPORT=true` /
`"zip_export": true` dans `POST /run`, ou `scalper pack`) :

- :func:`folder_run_export` crée ``out/export-<AAAA-MM-JJ-HH-MM>/`` (date + heure/minutes
  locales) avec le contenu de l'export : ``export/<chapitre>/`` (md/json/html/images
  re-traduites), ``images/<chapitre>/`` + ``pdf/<chapitre>.pdf``, ``metadata/<chapitre>/*.json``
  et ``cleaned/<chapitre>/`` si demandé. **Contrairement au zip, le reste de ``out/`` est
  conservé** (state.json, glossary.json, raw/…) pour pouvoir débugger ;
- :func:`upload_folder_to_drive` envoie ce dossier vers Google Drive (dossier
  ``export-<AAAA-MM-JJ-HH-MM>``, parent ``GOOGLE_DRIVE_PARENT_ID`` ou racine) si
  ``GOOGLE_DRIVE_ACCESS_TOKEN`` est renseigné (token OAuth, scope ``drive.file``).
  Sans token, l'upload est ignoré : le dossier local suffit à inspecter le résultat.

Aucun secret n'est écrit dans le code : tout passe par variables d'environnement.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DRIVE_API = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"


def is_dev() -> bool:
    """True uniquement en environnement de dev explicite : ``SCALPER_ENV=dev``."""
    return os.environ.get("SCALPER_ENV", "").strip().lower() == "dev"


def export_stamp(when: datetime) -> str:
    """Nom d'export horodaté : ``export-<AAAA-MM-JJ-HH-MM>`` (date + heure/minutes)."""
    return f"export-{when:%Y-%m-%d-%H-%M}"


def folder_run_export(
    out_root: Path | str,
    source_id: str,
    series_id: str,
    lang: str,
    chapter_ids: list[str] | None = None,
    include_clean: bool = False,
    when: datetime | None = None,
) -> Path:
    """Crée ``out_root/export-<AAAA-MM-JJ-HH-MM>/`` avec le contenu de l'export du run
    (mêmes entrées que le zip, plus ``export/<chapitre>/`` : md/json/html/images traduites).
    Le reste de ``out_root`` est conservé (pas de clean en dev)."""
    from .packager import IMAGE_SUFFIXES, build_chapter_pdf, chapter_images, chapter_metadata_files
    from .scraper import chapter_dir

    out_root = Path(out_root)
    if chapter_ids is None:
        base = out_root / "raw" / source_id / series_id
        chapter_ids = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    when = when or datetime.now()
    dest = out_root / export_stamp(when)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for cid in chapter_ids:
        cdir = chapter_dir(out_root, source_id, series_id, cid)
        if not cdir.is_dir():
            continue
        # export/ du chapitre : md/json/html + images re-traduites (images-<lang>/) + images-clean/
        cexp = cdir / "export"
        if cexp.is_dir():
            shutil.copytree(cexp, dest / "export" / cid, dirs_exist_ok=True)
        # images + pdf (identique au zip)
        pages = chapter_images(cdir, lang)
        if pages:
            for p in pages:
                target = dest / "images" / cid / p.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
            build_chapter_pdf(pages, dest / "pdf" / f"{cid}.pdf")
        for meta in chapter_metadata_files(cdir):
            target = dest / "metadata" / cid / meta.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(meta, target)
        if include_clean:
            clean_dir = cdir / "export" / "images-clean"
            if clean_dir.is_dir():
                for p in sorted(clean_dir.iterdir()):
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
                        target = dest / "cleaned" / cid / p.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, target)
    return dest


def _drive_request(url: str, token: str, method: str = "GET", body: bytes | None = None,
                   content_type: str | None = None, timeout: int = 60) -> Any:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if content_type:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _drive_find_folder_id(name: str, token: str, timeout: int = 60) -> str | None:
    """Retourne l'id du premier dossier Drive nommé `name` (non dans la corbeille), ou None."""
    import urllib.parse

    q = urllib.parse.quote(f"mimeType='{FOLDER_MIME}' and name='{name}' and trashed=false")
    data = _drive_request(f"{DRIVE_API}?q={q}&fields=files(id,name)&pageSize=1", token, timeout=timeout)
    files = data.get("files") or []
    return files[0]["id"] if files else None


def _drive_create_folder(name: str, parent: str, token: str, timeout: int) -> str:
    payload = json.dumps({"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}).encode("utf-8")
    folder = _drive_request(DRIVE_API, token, method="POST", body=payload,
                            content_type="application/json; charset=UTF-8", timeout=timeout)
    return folder["id"]


def _drive_upload_file(path: Path, rel_parts: list[str], folder_ids: dict[str, str], token: str, timeout: int) -> str:
    """Upload un fichier dans le sous-dossier Drive correspondant à rel_parts[:-1]
    (sous-dossiers créés à la volée, mis en cache dans folder_ids)."""
    parent_id = folder_ids[""]
    key = ""
    for part in rel_parts[:-1]:
        key = f"{key}/{part}" if key else part
        if key not in folder_ids:
            folder_ids[key] = _drive_create_folder(part, parent_id, token, timeout)
        parent_id = folder_ids[key]
    boundary = "scalper-boundary"
    meta = json.dumps({"name": rel_parts[-1], "parents": [parent_id]}).encode("utf-8")
    mime = mimetypes.guess_type(rel_parts[-1])[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8")
        + meta
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8")
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    created = _drive_request(f"{DRIVE_UPLOAD}?uploadType=multipart", token, method="POST",
                             body=body, content_type=f"multipart/related; boundary={boundary}", timeout=timeout)
    return created["id"]


def upload_folder_to_drive(
    local_dir: Path | str,
    parent_id: str | None = None,
    token: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Crée un dossier ``<nom de local_dir>`` dans Google Drive (parent
    ``GOOGLE_DRIVE_PARENT_ID`` ou racine) et y upload le contenu de ``local_dir``.
    Nécessite ``GOOGLE_DRIVE_ACCESS_TOKEN`` (OAuth, scope drive.file) : lève
    RuntimeError sinon. Retourne {"folder_id", "folder_name", "files": [...]}."""
    token = token or os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "GOOGLE_DRIVE_ACCESS_TOKEN manquant : en dev, renseigne un token OAuth Google Drive "
            "(scope drive.file) pour uploader l'export ; sinon le dossier local suffit."
        )
    parent = parent_id or os.environ.get("GOOGLE_DRIVE_PARENT_ID")
    if not parent:
        # dossier parent par nom (ex. GOOGLE_DRIVE_PARENT_NAME=Scalper), sinon racine
        parent_name = os.environ.get("GOOGLE_DRIVE_PARENT_NAME")
        parent = (_drive_find_folder_id(parent_name, token, timeout) if parent_name else None) or "root"
    local_dir = Path(local_dir)
    folder_id = _drive_create_folder(local_dir.name, parent, token, timeout)
    folder_ids = {"": folder_id}
    uploaded: list[str] = []
    for p in sorted(local_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(local_dir).parts
            _drive_upload_file(p, list(rel), folder_ids, token, timeout)
            uploaded.append("/".join(rel))
    return {"folder_id": folder_id, "folder_name": local_dir.name, "files": uploaded}
