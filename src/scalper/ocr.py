"""OCR des images d'un chapitre (texte dans les bulles).

Deux moteurs (protocol OCRClient) :
- TesseractOCR : tesseract (léger, dans l'image Docker), moteur par défaut ;
- PaddleOCR    : PaddleOCR (gratuit, local, meilleur sur texte stylisé/bulles),
  à installer via l'extra ".[ocr-paddle]" et à activer via SCALPER_OCR_ENGINE=paddle.
"""

from __future__ import annotations

import json
import os
import sys
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


def _image_size(path: Path) -> tuple[int, int] | None:
    """Taille (w, h) réelle de l'image, ou None si illisible (tests avec chemins factices)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _normalize_polys(polys: list, img_size: tuple[int, int] | None) -> list:
    """Ramène les polygones dans le référentiel de l'image.

    Sur les très grandes images, le modèle de détection de PaddleOCR redimensionne
    l'entrée en interne (max_side_limit) et peut renvoyer des coordonnées dans un
    espace plus grand que l'image : on rescale alors uniformément (préserve l'aspect)
    par 1/k avec k = max dépassement relatif, puis on clamp dans l'image. Sans
    dépassement (coordonnées déjà dans l'image), rien ne change."""
    polys = _as_list(polys)
    if not img_size or not polys:
        return polys
    img_w, img_h = img_size
    if img_w <= 0 or img_h <= 0:
        return polys
    try:
        max_x = max(float(p[0]) for poly in polys for p in poly)
        max_y = max(float(p[1]) for poly in polys for p in poly)
        k = max(max_x / img_w, max_y / img_h, 1.0)
        s = 1.0 / k
        return [
            [
                [min(max(float(p[0]) * s, 0.0), float(img_w)), min(max(float(p[1]) * s, 0.0), float(img_h))]
                for p in poly
            ]
            for poly in polys
        ]
    except (TypeError, ValueError, IndexError):
        return polys  # format inattendu : on ne touche à rien


_PADDLE_TIERS = ("tiny", "small", "medium")


def _paddle_tier(paddle_lang: str) -> str:
    """Tier PP-OCRv6 : SCALPER_PADDLE_OCR_TIER (défaut `medium`, le défaut de PaddleOCR 3.7).
    `tiny` ne supporte pas le japonais (pas de kana dans son dictionnaire) -> on remonte à `small`."""
    tier = os.environ.get("SCALPER_PADDLE_OCR_TIER", "medium").strip().lower()
    if tier not in _PADDLE_TIERS:
        raise ValueError(f"SCALPER_PADDLE_OCR_TIER inconnu : {tier!r} (tiny|small|medium)")
    if tier == "tiny" and paddle_lang == "japan":
        print("[scalper][ocr] PP-OCRv6_tiny ne lit pas le japonais (pas de kana) -> tier small", file=sys.stderr)
        tier = "small"
    return tier


class PaddleOCR:
    """OCR via PaddleOCR (gratuit, 100 % local, meilleur sur texte stylisé/bulles).

    Nécessite : pip install -e ".[ocr-paddle]" (paddlepaddle + paddleocr).
    Par défaut : PP-OCRv6_medium (défaut PaddleOCR 3.7) ; tier réglable via
    SCALPER_PADDLE_OCR_TIER (tiny|small|medium).
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
            tier = _paddle_tier(paddle_lang)
            self._engine = _PaddleEngine(
                lang=paddle_lang,
                use_textline_orientation=True,  # texte vertical (manga JP)
                text_detection_model_name=f"PP-OCRv6_{tier}_det",
                text_recognition_model_name=f"PP-OCRv6_{tier}_rec",
            )
        results = self._engine.predict(str(image_path))
        if results is None:
            return []
        if not isinstance(results, (list, tuple)):
            results = [results]
        raw: list[tuple[str, float, list]] = []
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
                raw.append((text, score, [list(p) for p in poly]))
        # coordonnées -> référentiel de l'image réelle (voir _normalize_polys)
        norm = _normalize_polys([poly for _, _, poly in raw], _image_size(image_path))
        regions: list[dict[str, Any]] = []
        for (text, _score, _poly), poly in zip(raw, norm):
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
