"""Remplacement du texte dans les images : inpainting de la bulle (voir inpaint.py :
remplissage simple ou LaMa) + retypographie de la traduction (Pillow).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


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


def _text_colors(fill_rgb: tuple[int, int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Couleur de texte + contour contrastées avec le fond (luminance)."""
    lum = 0.299 * fill_rgb[0] + 0.587 * fill_rgb[1] + 0.114 * fill_rgb[2]
    if lum > 140:
        return (0, 0, 0), (255, 255, 255)
    return (255, 255, 255), (0, 0, 0)


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
    la traduction est redessinée par-dessus, centrée dans la boîte d'origine.
    """
    from PIL import Image, ImageDraw

    from .inpaint import default_inpainter, estimate_bg_color

    # Les pages manga peuvent être très grandes : on désactive la limite
    # anti-decompression-bomb de PIL (les images viennent de nos propres chapitres).
    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(image_path).convert("RGB")
    inpainter = inpainter or default_inpainter()
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
    if todo:
        img = inpainter.inpaint_image(img, [r for r, _ in todo]).convert("RGB")
    draw = ImageDraw.Draw(img)
    for r, text in todo:
        x, y, w, h = r["box"]
        fill_rgb = estimate_bg_color(img, [x, y, w, h])
        text_fill, stroke_fill = _text_colors(fill_rgb)
        # taille de départ proche du texte original (hauteur de boîte ≈ hauteur de ligne)
        font_size = max(8, int(h * 0.8))
        font = _load_font(font_size)
        lines = wrap_text(draw, text, font, max(10, w - 8))
        # on réduit jusqu'à tenir dans la boîte, en largeur ET en hauteur
        while font_size > 8:
            too_tall = (font_size + 2) * len(lines) > h
            too_wide = max(draw.textlength(ln, font=font) for ln in lines) > w - 8
            if not too_tall and not too_wide:
                break
            font_size -= 1
            font = _load_font(font_size)
            lines = wrap_text(draw, text, font, max(10, w - 8))
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
) -> list[str]:
    """Recrée les images avec le texte traduit → `export/images-<lang>/`.

    Si `translations` est fourni (une traduction par région OCR, dans l'ordre), il est
    utilisé directement ; sinon les blocs texte de `chapter.<lang>.json` servent de
    traductions (indexés par ordre des régions). `inpainter` (défaut
    `default_inpainter()`) gère la suppression du texte original avant retypographie.
    """
    ocr = json.loads((chapter_dir / ocr_file).read_text(encoding="utf-8"))
    if translations is None:
        from .models import Chapter

        chapter = Chapter.from_dict(json.loads((chapter_dir / f"chapter.{lang}.json").read_text(encoding="utf-8")))
        translations = [b.text for b in chapter.text_blocks()]
    images_dir = chapter_dir / "images"
    out_dir = chapter_dir / "export" / f"images-{lang}"
    outputs: list[str] = []
    if not images_dir.exists():
        return outputs
    by_image: dict[str, list[dict[str, Any]]] = {}
    for r in ocr.get("regions", []):
        by_image.setdefault(r["image"], []).append(r)
    for name, regions in sorted(by_image.items()):
        src = images_dir / name
        if src.exists():
            outputs.append(str(replace_texts(src, regions, translations, out_dir / name, inpainter=inpainter)))
    # pages sans texte détecté : copiées telles quelles pour un export complet
    for p in sorted(images_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.name not in by_image:
            out = out_dir / p.name
            if not out.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, out)
                outputs.append(str(out))
    return outputs
