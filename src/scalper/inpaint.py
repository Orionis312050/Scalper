"""Inpainting des zones de texte avant retypographie (suppression « intelligente » du texte).

Deux moteurs (protocol `Inpainter`), sélection par `SCALPER_INPAINT_ENGINE` ou `--inpaint-engine` :

- `simple` : remplissage de la boîte (légèrement élargie) par la couleur de fond estimée sur
  l'anneau de pixels juste autour (hors texte) — l'ancien comportement du typeset ;
- `lama`  : big-lama (https://github.com/advimman/lama, licence Apache-2.0) en ONNX
  (port Carve/LaMa-ONNX, entrée fixe 512x512) via onnxruntime. Pour chaque région de texte,
  on découpe un crop carré avec contexte autour de la boîte, on le redimensionne en 512x512,
  on construit le masque (polygone OCR quand disponible, sinon rectangle dilaté), on infère,
  puis on ne recolle que les pixels masqués à la résolution d'origine : le reste de la page
  est inchangé. Le modèle (208 Mo) est téléchargé une seule fois, sha256 vérifié, dans
  `SCALPER_LAMA_CACHE` (défaut `~/.cache/scalper/lama` ; `/app/data/.lama` dans le conteneur).
  `SCALPER_LAMA_MODEL` permet de pointer directement vers un fichier .onnx local.
- `auto` (défaut) : `lama` si onnxruntime est installé, sinon `simple`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Protocol

# big-lama en ONNX (port Carve/LaMa-ONNX de advimman/lama), révision épinglée, sha256 vérifié.
LAMA_MODEL_URL = (
    "https://huggingface.co/gumka/lama-onnx/resolve/"
    "d979e5c755d09d8b146fc977c78ec3ea9cdc72ed/lama_fp32.onnx"
)
LAMA_MODEL_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
LAMA_MODEL_SIZE = 208_044_816
LAMA_MODEL_NAME = "lama_fp32.onnx"
LAMA_INPUT_SIZE = 512   # entrée fixe du graphe ONNX exporté
LAMA_MIN_CONTEXT = 256  # fenêtre de contexte minimale (px) autour d'une petite boîte de texte


class Inpainter(Protocol):
    def inpaint_image(self, img, regions: list[dict[str, Any]]):
        """Renvoie `img` (PIL, RGB) avec les zones de texte des `regions` remplies.

        `regions` : dicts OCR avec au moins `box` = [x, y, w, h] et optionnellement
        `poly` = [[x, y], ...] (quadrilatère PaddleOCR, masque plus fidèle que la boîte).
        """
        ...


def _box_of(region: dict[str, Any]) -> tuple[int, int, int, int] | None:
    box = region.get("box")
    if not box or len(box) != 4:
        return None
    try:
        x, y, w, h = (int(v) for v in box)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _ring_fill_color(img, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int]:
    """Couleur de fond estimée sur l'anneau de pixels juste autour du rectangle
    (x0, y0, x1, y1) — donc hors du texte. Repli : moyenne du rectangle si l'anneau
    est vide (bord d'image)."""
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
        return (
            sum(p[0] for p in samples) // n,
            sum(p[1] for p in samples) // n,
            sum(p[2] for p in samples) // n,
        )
    return img.crop((x0, y0, x1, y1)).resize((1, 1)).getpixel((0, 0))[:3]


def estimate_bg_color(img, box: list[int]) -> tuple[int, int, int]:
    """Couleur de fond estimée autour de la boîte [x, y, w, h] (boîte élargie d'un pad,
    puis anneau) — utilisée pour choisir une couleur de texte contrastée."""
    x, y, w, h = box
    pad = max(1, min(w, h) // 10)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(img.width, x + w + pad), min(img.height, y + h + pad)
    return _ring_fill_color(img, x0, y0, x1, y1)


def _inpaint(img, box: list[int]) -> tuple[int, int, int]:
    """Inpainting « simple » : la boîte (légèrement élargie pour couvrir les bords du
    texte) est remplie par la couleur de fond estimée sur l'anneau de pixels juste autour
    (donc hors du texte), et non par la moyenne de la boîte elle-même (assombrie par le
    texte). Retourne la couleur RGB de remplissage."""
    from PIL import Image

    x, y, w, h = box
    pad = max(1, min(w, h) // 10)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(img.width, x + w + pad), min(img.height, y + h + pad)
    w2, h2 = x1 - x0, y1 - y0
    if w2 <= 0 or h2 <= 0:
        return (255, 255, 255)
    fill_rgb = _ring_fill_color(img, x0, y0, x1, y1)
    img.paste(Image.new("RGB", (w2, h2), fill_rgb), (x0, y0))
    return fill_rgb


class SimpleInpainter:
    """Moteur d'inpainting par défaut (léger, sans dépendance ML) : remplissage par la
    couleur de fond estimée sur l'anneau autour de chaque boîte."""

    def inpaint_image(self, img, regions: list[dict[str, Any]]):
        for r in regions:
            box = _box_of(r)
            if box is not None:
                _inpaint(img, list(box))
        return img


def _default_cache_dir() -> Path:
    env = os.environ.get("SCALPER_LAMA_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "scalper" / "lama"


def _download_model(url: str, dest: Path) -> None:
    """Télécharge le modèle ONNX (temp .part + renommage atomique) et vérifie
    taille + sha256 avant de le valider."""
    import urllib.request

    part = dest.with_name(dest.name + ".part")
    print(f"[scalper][lama] téléchargement du modèle big-lama ONNX ({LAMA_MODEL_SIZE // (1 << 20)} Mo) : {url} -> {dest}", file=sys.stderr)
    with urllib.request.urlopen(url) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if part.stat().st_size != LAMA_MODEL_SIZE or h.hexdigest() != LAMA_MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise RuntimeError("modèle LaMa téléchargé invalide (taille ou sha256 incorrect) — réessayez")
    os.replace(part, dest)


class LamaInpainter:
    """Inpainting par big-lama (advimman/lama) en ONNX via onnxruntime (CPU).

    Le graphe exporté a une entrée fixe 512x512 : chaque région est donc traitée par un
    crop carré (boîte + contexte) redimensionné en 512x512 ; seuls les pixels masqués du
    crop sont recollés dans l'image à la résolution d'origine.
    """

    def __init__(self, model_path: str | Path | None = None, input_size: int = LAMA_INPUT_SIZE,
                 min_context: int = LAMA_MIN_CONTEXT) -> None:
        self._model_path = Path(model_path) if model_path else None
        self._input_size = input_size
        self._min_context = min_context
        self._session = None

    def _resolve_model_path(self) -> Path:
        for candidate in (self._model_path, Path(os.environ["SCALPER_LAMA_MODEL"]) if os.environ.get("SCALPER_LAMA_MODEL") else None):
            if candidate is not None:
                if not candidate.exists():
                    raise RuntimeError(f"modèle LaMa introuvable : {candidate} (SCALPER_LAMA_MODEL)")
                return candidate
        cache = _default_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        dest = cache / LAMA_MODEL_NAME
        if not dest.exists():
            _download_model(LAMA_MODEL_URL, dest)
        return dest

    def _get_session(self):
        if self._session is None:
            try:
                import onnxruntime as ort
            except ImportError as e:
                raise RuntimeError(
                    'onnxruntime non installé : pip install -e ".[inpaint-lama]" '
                    "(ou SCALPER_INPAINT_ENGINE=simple pour rester sur le remplissage simple)"
                ) from e
            model = self._resolve_model_path()
            self._session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        return self._session

    def _crop_rect(self, box: tuple[int, int, int, int], img_w: int, img_h: int) -> tuple[int, int, int, int]:
        """Crop carré centré sur la boîte, avec contexte (côté >= 2x la boîte et >= min_context),
        borné à l'image."""
        x, y, w, h = box
        pad = max(2, int(min(w, h) * 0.1))
        side = max(2 * max(w + 2 * pad, h + 2 * pad), self._min_context)
        cx, cy = x + w / 2, y + h / 2
        cx0 = min(max(0, round(cx - side / 2)), max(0, img_w - side))
        cy0 = min(max(0, round(cy - side / 2)), max(0, img_h - side))
        cx1 = min(img_w, cx0 + side)
        cy1 = min(img_h, cy0 + side)
        cx0, cy0 = max(0, cx1 - side), max(0, cy1 - side)
        return cx0, cy0, max(cx0 + 1, cx1), max(cy0 + 1, cy1)

    def _draw_region_mask(self, draw, region: dict[str, Any], box: tuple[int, int, int, int],
                          cx0: int, cy0: int, sx: float, sy: float) -> None:
        """Dessine le masque d'une région dans un ImageDraw : polygone OCR si disponible,
        sinon rectangle (boîte élargie d'un pad de 10 %)."""
        poly = region.get("poly")
        if poly and len(poly) >= 3:
            try:
                pts = [((float(px) - cx0) * sx, (float(py) - cy0) * sy) for px, py in poly]
                draw.polygon(pts, fill=255)
                return
            except (TypeError, ValueError):
                pass
        x, y, w, h = box
        pad = max(2, int(min(w, h) * 0.1))
        draw.rectangle(
            [(x - pad - cx0) * sx, (y - pad - cy0) * sy, (x + w + pad - cx0) * sx, (y + h + pad - cy0) * sy],
            fill=255,
        )

    def inpaint_image(self, img, regions: list[dict[str, Any]]):
        import numpy as np
        from PIL import Image, ImageDraw

        sess = self._get_session()
        size = self._input_size
        arr = np.array(img.convert("RGB"))
        for r in regions:
            box = _box_of(r)
            if box is None:
                continue
            cx0, cy0, cx1, cy1 = self._crop_rect(box, img.width, img.height)
            cw, ch = cx1 - cx0, cy1 - cy0
            crop = img.crop((cx0, cy0, cx1, cy1)).resize((size, size), Image.LANCZOS)
            sx, sy = size / cw, size / ch
            mask512 = Image.new("L", (size, size), 0)
            self._draw_region_mask(ImageDraw.Draw(mask512), r, box, cx0, cy0, sx, sy)
            x_in = (np.asarray(crop, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
            m_in = ((np.asarray(mask512, dtype=np.float32) / 255.0) > 0).astype(np.float32)[None, None]
            out = sess.run(["output"], {"image": x_in, "mask": m_in})[0][0]  # [3, S, S], RGB 0..255
            pred = np.clip(out.transpose(1, 2, 0), 0, 255).astype(np.uint8)
            pred_crop = Image.fromarray(pred).resize((cw, ch), Image.LANCZOS)
            mask_crop = Image.new("L", (cw, ch), 0)
            self._draw_region_mask(ImageDraw.Draw(mask_crop), r, box, cx0, cy0, 1.0, 1.0)
            m = np.asarray(mask_crop) > 0
            sub = arr[cy0:cy1, cx0:cx1]
            sub[m] = np.asarray(pred_crop)[m]
        return Image.fromarray(arr)


def default_inpainter(engine: str | None = None) -> Inpainter:
    """Moteur d'inpainting par défaut : `engine`, sinon SCALPER_INPAINT_ENGINE, sinon
    `auto` (lama si onnxruntime est installé, simple sinon)."""
    engine = (engine or os.environ.get("SCALPER_INPAINT_ENGINE", "auto")).strip().lower()
    if engine == "simple":
        return SimpleInpainter()
    if engine == "lama":
        return LamaInpainter()
    if engine == "auto":
        try:
            import onnxruntime  # noqa: F401

            return LamaInpainter()
        except ImportError:
            return SimpleInpainter()
    raise ValueError(f"moteur d'inpainting inconnu : {engine!r} (simple|lama|auto)")
