"""OCR des images d'un chapitre (texte dans les bulles).

Deux moteurs (protocol OCRClient) :
- TesseractOCR : tesseract (léger, dans l'image Docker), moteur par défaut ;
- PaddleOCR    : PaddleOCR (gratuit, local, meilleur sur texte stylisé/bulles),
  à installer via l'extra ".[ocr-paddle]" et à activer via SCALPER_OCR_ENGINE=paddle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol


def _as_list(value: Any) -> list:
    """Normalise list/ndarray/None en list (évite `or` sur un ndarray : truth value ambigu)."""
    if value is None:
        return []
    return list(value)


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


# Codes langue (style Tesseract) -> code langue PaddleOCR
_PADDLE_LANGS = {
    "eng": "en", "fra": "fr", "deu": "german", "spa": "es", "ita": "it", "por": "pt",
    "jpn": "japan", "chi_sim": "ch", "kor": "korean",
}


class PaddleOCR:
    """OCR via PaddleOCR (gratuit, 100 % local, meilleur sur texte stylisé/bulles).

    Nécessite : pip install -e ".[ocr-paddle]" (paddlepaddle + paddleocr).
    """

    def __init__(self, lang: str = "eng") -> None:
        self.lang = lang
        self._engine = None

    def recognize(self, image_path: Path, lang: str | None = None) -> list[dict[str, Any]]:
        try:
            from paddleocr import PaddleOCR as _PaddleEngine
        except ImportError as e:
            raise RuntimeError(
                'PaddleOCR non installé : pip install -e ".[ocr-paddle]" '
                "(ou SCALPER_OCR_ENGINE=tesseract pour rester sur Tesseract)"
            ) from e
        if self._engine is None:
            paddle_lang = _PADDLE_LANGS.get((lang or self.lang), "en")
            self._engine = _PaddleEngine(lang=paddle_lang, use_textline_orientation=True)
        results = self._engine.predict(str(image_path))
        if results is None:
            return []
        if not isinstance(results, (list, tuple)):
            results = [results]
        regions: list[dict[str, Any]] = []
        for res in results:
            texts = _as_list(res.get("rec_texts"))
            scores = _as_list(res.get("rec_scores"))
            polys = res.get("dt_polys")
            if polys is None or len(polys) == 0:
                polys = res.get("rec_boxes")
            for text, score, poly in zip(texts, scores, _as_list(polys)):
                text = (text or "").strip()
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    score = -1.0
                if not text or score < 0.6:
                    continue
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                regions.append({
                    "image": image_path.name,
                    "box": [int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0))],
                    "text": text,
                    # quadrilatère du texte : masque plus fidèle pour l'inpainting LaMa
                    "poly": [[int(px), int(py)] for px, py in poly],
                })
        for i, r in enumerate(regions):
            r["index"] = i
        return regions


def default_ocr_client(lang: str = "eng", engine: str | None = None) -> OCRClient:
    """Moteur OCR : `engine` explicite, sinon SCALPER_OCR_ENGINE=paddle -> PaddleOCR,
    sinon Tesseract (défaut)."""
    engine = (engine or os.environ.get("SCALPER_OCR_ENGINE", "tesseract")).strip().lower()
    if engine == "paddle":
        return PaddleOCR(lang=lang)
    return TesseractOCR(lang=lang)


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
