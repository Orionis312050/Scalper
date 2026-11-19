"""Remplacement du texte dans les images, en deux étapes :

3. inpainting (voir inpaint.py : remplissage simple ou LaMa) du texte original sur
   l'image brute → `export/images-clean/` ;
4. retypographie de la traduction (Pillow) par-dessus, centrée dans la boîte
   d'origine, à la taille du texte d'origine (hauteur de boîte OCR) → `export/images-<lang>/`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

# sentinel interne : replace_texts sans inpainting (image déjà nettoyée, ex. images-clean)
_DRAW_ONLY = object()


def _load_font(size: int):
    from PIL import ImageFont

    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        t = (cur + " " + w).strip()
        if not cur or draw.textlength(t, font=font) <= max_width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_font(draw, text: str, max_w: int, max_h: int) -> tuple[Any, int, list[str]]:
    """Plus grande police (>= 8) tenant dans max_w x max_h : renvoie (font, taille, lignes).
    On part de la hauteur max (la boîte OCR ≈ la hauteur du texte d'origine), puis on
    réduit jusqu'à tenir en largeur ET en hauteur → la bonne taille pour chaque bulle."""
    font_size = max(8, max_h - 2)
    font = _load_font(font_size)
    lines = wrap_text(draw, text, font, max(10, max_w - 8))
    while font_size > 8:
        too_tall = (font_size + 2) * len(lines) > max_h
        too_wide = max(draw.textlength(ln, font=font) for ln in lines) > max_w - 8
        if not too_tall and not too_wide:
            break
        font_size -= 1
        font = _load_font(font_size)
        lines = wrap_text(draw, text, font, max(10, max_w - 8))
    return font, font_size, lines


def _text_colors(fill_rgb: tuple[int, int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Couleur de texte + contour contrastées avec le fond (luminance)."""
    lum = 0.299 * fill_rgb[0] + 0.587 * fill_rgb[1] + 0.114 * fill_rgb[2]
    if lum > 140:
        return (0, 0, 0), (255, 255, 255)
    return (255, 255, 255), (0, 0, 0)


def _clamp_regions(regions: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    """Clamp défensif des boîtes dans l'image : si une boîte OCR dépasse du canvas,
    on la ramène dans l'image (évite de dessiner / inpainter hors image)."""
    out = []
    for r in regions:
        box = r.get("box")
        if not box or len(box) != 4:
            continue
        try:
            x, y, w, h = (int(v) for v in box)
        except (TypeError, ValueError):
            continue
        x0 = max(0, min(x, max(0, width - 1)))
        y0 = max(0, min(y, max(0, height - 1)))
        x1 = min(width, x + w)
        y1 = min(height, y + h)
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        r2 = {**r, "box": [x0, y0, x1 - x0, y1 - y0]}
        poly = r.get("poly")
        if poly:
            # clamp aussi le polygone dans l'image (coordonnées OCR qui dépassent)
            try:
                r2["poly"] = [
                    [min(max(int(float(px)), 0), width), min(max(int(float(py)), 0), height)]
                    for px, py in poly
                ]
            except (TypeError, ValueError):
                r2["poly"] = poly
        out.append(r2)
    return out


def _todo_regions(regions: list[dict[str, Any]], translations: list[str]) -> list[tuple[dict[str, Any], str]]:
    """Régions à retyper : index valide, traduction non vide et différente du texte
    original, boîte assez grande (sinon c'est du bruit OCR)."""
    todo: list[tuple[dict[str, Any], str]] = []
    for r in regions:
        i = r.get("index")
        if i is None or i >= len(translations):
            continue
        text = translations[i].strip()
        if not text or text == r.get("text", "").strip():
            continue
        x, y, w, h = r["box"]
        if w < 8 or h < 8:
            continue  # mini-région = bruit OCR
        todo.append((r, text))
    return todo


def inpaint_image_file(image_path: Path, regions: list[dict[str, Any]], out_path: Path, inpainter) -> Path:
    """Étape 3 : efface le texte original de `regions` sur l'image brute via `inpainter`
    (LaMa...) et sauve l'image « nettoyée ». Toutes les régions valides sont effacées,
    même sans traduction (le texte d'origine doit disparaître entièrement)."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(image_path).convert("RGB")
    valid = [r for r in _clamp_regions(regions, img.width, img.height) if r["box"][2] >= 8 and r["box"][3] >= 8]
    if valid:
        img = inpainter.inpaint_image(img, valid).convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def replace_texts(
    image_path: Path,
    regions: list[dict[str, Any]],
    translations: list[str],
    out_path: Path,
    inpainter=None,
) -> Path:
    """Remplace le texte des `regions` (index → translations[index]) dans l'image.

    Phase 1 : `inpainter` (défaut `default_inpainter()`) efface le texte original de
    toutes les régions à traduire (remplissage simple ou inpainting LaMa) ; phase 2 :
    la traduction est redessinée par-dessus, centrée dans la boîte d'origine, à la
    taille du texte d'origine (hauteur de boîte OCR). `inpainter=_DRAW_ONLY` : retypographie
    seule (l'image est déjà nettoyée)."""
    from PIL import Image, ImageDraw

    from .inpaint import default_inpainter, estimate_bg_color

    # Les pages manga peuvent être très grandes : on désactive la limite
    # anti-decompression-bomb de PIL (les images viennent de nos propres chapitres).
    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(image_path).convert("RGB")
    if inpainter is None:
        inpainter = default_inpainter()
    regions = _clamp_regions(regions, img.width, img.height)
    todo = _todo_regions(regions, translations)
    if todo and inpainter is not _DRAW_ONLY:
        img = inpainter.inpaint_image(img, [r for r, _ in todo]).convert("RGB")
    draw = ImageDraw.Draw(img)
    for r, text in todo:
        x, y, w, h = r["box"]
        fill_rgb = estimate_bg_color(img, [x, y, w, h])
        text_fill, stroke_fill = _text_colors(fill_rgb)
        # même taille que le texte d'origine : on part de la hauteur de la boîte OCR
        font, font_size, lines = _fit_font(draw, text, w, h)
        line_h = font_size + 2
        ty = y + max(0, (h - line_h * len(lines)) // 2)
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            tx = x + max(0, int((w - tw) // 2))
            draw.text((tx, ty), ln, font=font, fill=text_fill, stroke_width=1, stroke_fill=stroke_fill)
            ty += line_h
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def typeset_chapter(
    chapter_dir: Path,
    lang: str,
    ocr_file: str = "ocr.json",
    translations: list[str] | None = None,
    inpainter=None,
    clean_subdir: str = "export/images-clean",
    on_step=None,
) -> list[str]:
    """Étapes 3+4 sur tout le chapitre → `export/images-<lang>/`.

    Étape 3 : le texte original est effacé sur les images brutes (inpainting) dans
    `clean_subdir` (défaut `export/images-clean/`) ; étape 4 : la traduction est
    redessinée par-dessus. Si `translations` est fourni (une traduction par région
    OCR, dans l'ordre), il est utilisé directement ; sinon les blocs texte de
    `chapter.<lang>.json` servent de traductions (indexés par ordre des régions).
    """
    from .inpaint import default_inpainter

    ocr = json.loads((chapter_dir / ocr_file).read_text(encoding="utf-8"))
    if translations is None:
        from .models import Chapter

        chapter = Chapter.from_dict(json.loads((chapter_dir / f"chapter.{lang}.json").read_text(encoding="utf-8")))
        translations = [b.text for b in chapter.text_blocks()]
    inpainter = inpainter if inpainter is not None else default_inpainter()
    images_dir = chapter_dir / "images"
    out_dir = chapter_dir / "export" / f"images-{lang}"
    clean_dir = chapter_dir / clean_subdir
    outputs: list[str] = []
    if not images_dir.exists():
        return outputs
    by_image: dict[str, list[dict[str, Any]]] = {}
    for r in ocr.get("regions", []):
        by_image.setdefault(r["image"], []).append(r)
    for name, regions in sorted(by_image.items()):
        src = images_dir / name
        if src.exists():
            # étape 3 : effacement du texte original sur l'image brute
            if on_step is not None:
                on_step("inpaint")
            clean_path = inpaint_image_file(src, regions, clean_dir / name, inpainter)
            # étape 4 : retypographie de la traduction sur l'image nettoyée
            if on_step is not None:
                on_step("typeset")
            outputs.append(str(replace_texts(clean_path, regions, translations, out_dir / name, inpainter=_DRAW_ONLY)))
    # pages sans texte détecté : copiées telles quelles (clean + export) pour un export complet
    for p in sorted(images_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.name not in by_image:
            for dest_dir in (clean_dir, out_dir):
                out = dest_dir / p.name
                if not out.exists():
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, out)
            out = out_dir / p.name
            if out.exists():
                outputs.append(str(out))
    return outputs
