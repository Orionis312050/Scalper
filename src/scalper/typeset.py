"""Remplacement du texte dans les images : inpainting de la bulle + retypographie
de la traduction (Pillow uniquement).
"""

from __future__ import annotations

import json
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


def _inpaint(img, box: list[int]) -> None:
    """Approximation d'inpainting : remplissage par la couleur moyenne locale floutée."""
    from PIL import ImageFilter

    x, y, w, h = box
    x, y = max(0, x), max(0, y)
    w, h = max(1, w), max(1, h)
    patch = img.crop((x, y, x + w, y + h)).resize((1, 1)).resize((w, h))
    patch = patch.filter(ImageFilter.GaussianBlur(radius=max(1, min(w, h) // 10)))
    img.paste(patch, (x, y))


def replace_texts(image_path: Path, regions: list[dict[str, Any]], translations: list[str], out_path: Path) -> Path:
    """Remplace le texte des `regions` (index → translations[index]) dans l'image."""
    from PIL import Image, ImageDraw

    img = Image.open(image_path).convert("RGB")
    for r in regions:
        i = r.get("index")
        if i is None or i >= len(translations):
            continue
        text = translations[i].strip()
        if not text or text == r.get("text", "").strip():
            continue
        x, y, w, h = r["box"]
        _inpaint(img, [x, y, w, h])
        draw = ImageDraw.Draw(img)
        font_size = max(10, int(h * 0.8))
        font = _load_font(font_size)
        lines = wrap_text(draw, text, font, max(10, w - 8))
        while font_size > 8 and (font_size + 2) * len(lines) > h:
            font_size -= 2
            font = _load_font(font_size)
            lines = wrap_text(draw, text, font, max(10, w - 8))
        line_h = font_size + 2
        ty = y + max(0, (h - line_h * len(lines)) // 2)
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            tx = x + max(0, int((w - tw) // 2))
            draw.text((tx, ty), ln, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
            ty += line_h
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def typeset_chapter(chapter_dir: Path, lang: str, ocr_file: str = "ocr.json") -> list[str]:
    """Recrée les images avec le texte traduit → `export/images-<lang>/`."""
    from .models import Chapter

    ocr = json.loads((chapter_dir / ocr_file).read_text(encoding="utf-8"))
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
    return outputs
