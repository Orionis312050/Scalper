"""Remplacement du texte dans les images : inpainting de la bulle + retypographie
de la traduction (Pillow uniquement).
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


def _inpaint(img, box: list[int]) -> tuple[int, int, int]:
    """Inpainting : la boîte (légèrement élargie pour couvrir les bords du texte) est
    remplie par la couleur de fond estimée sur l'anneau de pixels juste autour (donc hors
    du texte), et non par la moyenne de la boîte elle-même (assombrie par le texte).
    Retourne la couleur RGB de remplissage. Repli : moyenne de la boîte si l'anneau est
    vide (bord d'image)."""
    from PIL import Image

    x, y, w, h = box
    pad = max(1, min(w, h) // 10)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(img.width, x + w + pad), min(img.height, y + h + pad)
    w2, h2 = x1 - x0, y1 - y0
    if w2 <= 0 or h2 <= 0:
        return (255, 255, 255)
    margin = max(2, min(w2, h2) // 8)
    rx0, ry0 = max(0, x0 - margin), max(0, y0 - margin)
    rx1, ry1 = min(img.width, x1 + margin), min(img.height, y1 + margin)
    ring = img.crop((rx0, ry0, rx1, ry1))
    rw, rh = ring.size
    if rw > 2 and rh > 2:
        px = ring.load()
        samples = []
        step = max(1, (rw + rh) // 400)
        for i in range(0, rw, step):
            samples.append(px[i, 0])
            samples.append(px[i, rh - 1])
        for j in range(0, rh, step):
            samples.append(px[0, j])
            samples.append(px[rw - 1, j])
        n = len(samples)
        fill_rgb = (
            sum(p[0] for p in samples) // n,
            sum(p[1] for p in samples) // n,
            sum(p[2] for p in samples) // n,
        )
    else:
        fill_rgb = img.crop((x0, y0, x1, y1)).resize((1, 1)).getpixel((0, 0))[:3]
    img.paste(Image.new("RGB", (w2, h2), fill_rgb), (x0, y0))
    return fill_rgb


def _text_colors(fill_rgb: tuple[int, int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Couleur de texte + contour contrastées avec le fond (luminance)."""
    lum = 0.299 * fill_rgb[0] + 0.587 * fill_rgb[1] + 0.114 * fill_rgb[2]
    if lum > 140:
        return (0, 0, 0), (255, 255, 255)
    return (255, 255, 255), (0, 0, 0)


def replace_texts(image_path: Path, regions: list[dict[str, Any]], translations: list[str], out_path: Path) -> Path:
    """Remplace le texte des `regions` (index → translations[index]) dans l'image."""
    from PIL import Image, ImageDraw

    # Les pages manga peuvent être très grandes : on désactive la limite
    # anti-decompression-bomb de PIL (les images viennent de nos propres chapitres).
    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(image_path).convert("RGB")
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
        fill_rgb = _inpaint(img, [x, y, w, h])
        text_fill, stroke_fill = _text_colors(fill_rgb)
        draw = ImageDraw.Draw(img)
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


def typeset_chapter(chapter_dir: Path, lang: str, ocr_file: str = "ocr.json", translations: list[str] | None = None) -> list[str]:
    """Recrée les images avec le texte traduit → `export/images-<lang>/`.

    Si `translations` est fourni (une traduction par région OCR, dans l'ordre), il est
    utilisé directement ; sinon les blocs texte de `chapter.<lang>.json` servent de
    traductions (indexés par ordre des régions)."""
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
            outputs.append(str(replace_texts(src, regions, translations, out_dir / name)))
    # pages sans texte détecté : copiées telles quelles pour un export complet
    for p in sorted(images_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.name not in by_image:
            out = out_dir / p.name
            if not out.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, out)
                outputs.append(str(out))
    return outputs
