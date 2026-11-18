"""Empaquetage du résultat d'un run en un seul zip.

`zip_run_export` crée dans le dossier `out` un zip nommé
`export-<AAAA-MM-JJ-HH-MM>.zip` (date/heure/minutes locales) contenant :

- `images/<chapitre>/<page>` : les images du résultat — les pages re-traduites
  `export/images-<lang>/` quand l'OCR/typeset a tourné, sinon les images d'origine ;
- `pdf/<chapitre>.pdf` : un PDF par chapitre, pages dans l'ordre (tri naturel :
  1, 2, …, 10), redimensionnées à PDF_MAX_SIDE px max pour alléger le PDF.

Ensuite, sauf `clean=False`, **tout le reste du dossier `out` est supprimé**
(y compris `state.json` et `glossary.json` : pas de reprise après un export zippé).
"""

from __future__ import annotations

import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

PDF_MAX_SIDE = 1600  # les pages du PDF sont redimensionnées à cette taille max (lecture, pas print)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def page_sort_key(name: str) -> tuple:
    """Tri naturel des pages : « 1-… », « 2-… », « 10-… » → 1, 2, 10 (et non 1, 10, 2)."""
    m = re.match(r"\s*(\d+)", name)
    return (int(m.group(1)) if m else 1 << 30, name)


def chapter_images(chapter_dir: Path, lang: str) -> list[Path]:
    """Pages du chapitre à embarquer : `export/images-<lang>/` si présent (résultat
    traduit), sinon `images/` (original). Triées dans l'ordre des pages."""
    for candidate in (chapter_dir / "export" / f"images-{lang}", chapter_dir / "images"):
        if candidate.is_dir():
            pages = [p for p in candidate.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
            return sorted(pages, key=lambda p: page_sort_key(p.name))
    return []


def build_chapter_pdf(pages: list[Path], pdf_path: Path) -> Path:
    """Construit un PDF multi-pages (Pillow, aucune dépendance externe) à partir des
    images, en RGB et redimensionnées à PDF_MAX_SIDE px max."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    imgs = []
    for p in pages:
        im = Image.open(p).convert("RGB")
        side = max(im.size)
        if side > PDF_MAX_SIDE:
            im = im.resize((int(im.width * PDF_MAX_SIDE / side), int(im.height * PDF_MAX_SIDE / side)), Image.LANCZOS)
        imgs.append(im)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(pdf_path, "PDF", save_all=True, append_images=imgs[1:], resolution=100.0)
    for im in imgs:
        im.close()
    return pdf_path


def zip_run_export(
    out_root: Path | str,
    source_id: str,
    series_id: str,
    lang: str,
    chapter_ids: list[str] | None = None,
    clean: bool = True,
    when: datetime | None = None,
) -> Path:
    """Crée `out_root/export-<AAAA-MM-JJ-HH-MM>.zip` avec les images + PDF des chapitres
    du run, puis (clean=True) supprime tout le reste du dossier `out_root`."""
    from .scraper import chapter_dir

    out_root = Path(out_root)
    if chapter_ids is None:
        base = out_root / "raw" / source_id / series_id
        chapter_ids = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    when = when or datetime.now()
    zip_path = out_root / f"export-{when:%Y-%m-%d-%H-%M}.zip"
    out_root.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for cid in chapter_ids:
            cdir = chapter_dir(out_root, source_id, series_id, cid)
            pages = chapter_images(cdir, lang)
            if not pages:
                continue
            for p in pages:
                z.write(p, f"images/{cid}/{p.name}")
            pdf_tmp = out_root / ".packager-tmp" / f"{cid}.pdf"
            build_chapter_pdf(pages, pdf_tmp)
            z.write(pdf_tmp, f"pdf/{cid}.pdf")
            shutil.rmtree(pdf_tmp.parent, ignore_errors=True)
    if clean:
        for p in out_root.iterdir():
            if p == zip_path:
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    return zip_path
