"""OCR des images d'un chapitre (texte dans les bulles)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class OCRClient(Protocol):
    def recognize(self, image_path: Path, lang: str = "eng") -> list[dict[str, Any]]: ...


class TesseractOCR:
    """OCR via Tesseract (pytesseract). Nécessite le binaire tesseract (voir Dockerfile)."""

    def __init__(self, lang: str = "eng") -> None:
        self.lang = lang

    def recognize(self, image_path: Path, lang: str | None = None) -> list[dict[str, Any]]:
        import pytesseract
        from pytesseract import Output

        data = pytesseract.image_to_data(str(image_path), lang=lang or self.lang, output_type=Output.DICT)
        regions: dict[tuple, dict[str, Any]] = {}
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if not text or conf < 60:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            r = regions.setdefault(
                key,
                {"image": image_path.name, "box": [data["left"][i], data["top"][i], 0, 0], "text": ""},
            )
            r["box"][2] = max(r["box"][2], data["left"][i] + data["width"][i])
            r["box"][3] = max(r["box"][3], data["top"][i] + data["height"][i])
            r["text"] = (r["text"] + " " + text).strip()
        out = []
        for i, v in enumerate(regions.values()):
            x0, y0, x1, y1 = v["box"]
            # format de sortie : [x, y, w, h] (et non [x0, y0, x1, y1])
            out.append(dict(v, index=i, box=[x0, y0, max(1, x1 - x0), max(1, y1 - y0)]))
        return out


class FakeOCR:
    """OCR factice hors ligne (tests / démo)."""

    def __init__(self, regions_by_image: dict[str, list[dict]] | None = None) -> None:
        self.regions_by_image = regions_by_image or {}

    def recognize(self, image_path: Path, lang: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(r, image=image_path.name, index=i)
            for i, r in enumerate(self.regions_by_image.get(image_path.name, []))
        ]


def ocr_chapter(chapter_dir: Path, client: OCRClient, lang: str = "eng") -> list[dict[str, Any]]:
    """OCR de toutes les images de `chapter_dir/images` → `chapter_dir/ocr.json`."""
    images_dir = chapter_dir / "images"
    images = sorted(p for p in images_dir.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")) if images_dir.exists() else []
    regions: list[dict[str, Any]] = []
    for img in images:
        regions.extend(client.recognize(img, lang))
    for i, r in enumerate(regions):
        r["index"] = i
    out = chapter_dir / "ocr.json"
    out.write_text(json.dumps({"lang": lang, "regions": regions}, ensure_ascii=False, indent=2), encoding="utf-8")
    return regions
