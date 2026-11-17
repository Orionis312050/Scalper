import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scalper import pipeline
from scalper.ocr import FakeOCR, ocr_chapter
from scalper.translator import FakeLLM
from scalper.typeset import _inpaint, replace_texts, typeset_chapter, wrap_text

try:
    from PIL import Image, ImageDraw

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class TestEngineSelection(unittest.TestCase):
    def test_default_engine_is_tesseract(self):
        from scalper.ocr import TesseractOCR, default_ocr_client

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(default_ocr_client(), TesseractOCR)

    def test_paddle_engine_via_env(self):
        from scalper.ocr import PaddleOCR, default_ocr_client

        with mock.patch.dict(os.environ, {"SCALPER_OCR_ENGINE": "paddle"}):
            self.assertIsInstance(default_ocr_client(), PaddleOCR)


class TestPaddleOCRMapping(unittest.TestCase):
    def test_recognize_maps_polys_to_xywh_and_filters_low_conf(self):
        from scalper.ocr import PaddleOCR

        class FakeEngine:
            def __init__(self, **kw):
                pass

            def predict(self, path):
                return [{
                    "rec_texts": ["Hello", "lowconf"],
                    "rec_scores": [0.95, 0.2],
                    "dt_polys": [
                        [[100, 50], [130, 50], [130, 70], [100, 70]],
                        [[10, 10], [20, 10], [20, 20], [10, 20]],
                    ],
                }]

        fake_mod = types.ModuleType("paddleocr")
        fake_mod.PaddleOCR = FakeEngine
        with mock.patch.dict(sys.modules, {"paddleocr": fake_mod}):
            regions = PaddleOCR().recognize(Path("p.png"))
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["box"], [100, 50, 30, 20])  # x, y, w, h
        self.assertEqual(regions[0]["text"], "Hello")
        self.assertEqual(regions[0]["index"], 0)

    def test_missing_paddle_raises_clear_error(self):
        from scalper.ocr import PaddleOCR

        with mock.patch.dict(sys.modules, {"paddleocr": None}):
            with self.assertRaises(RuntimeError) as cm:
                PaddleOCR().recognize(Path("p.png"))
            self.assertIn("ocr-paddle", str(cm.exception))


class TestTesseractBoxFormat(unittest.TestCase):
    def test_box_is_xywh_not_corners(self):
        from scalper.ocr import TesseractOCR

        data = {
            "text": ["Hello", "world"], "conf": [90, 85],
            "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 1],
            "left": [100, 140], "top": [50, 52], "width": [30, 40], "height": [20, 18],
        }
        with mock.patch("pytesseract.image_to_data", return_value=data):
            regions = TesseractOCR().recognize(Path("p.png"))
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["box"], [100, 50, 80, 20])  # x, y, w, h
        self.assertEqual(regions[0]["text"], "Hello world")


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestOCRTypeset(unittest.TestCase):
    def _make_image(self, path: Path, size=(300, 120)) -> None:
        img = Image.new("RGB", size, (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 290, 70], fill=(200, 220, 255))
        d.text((20, 20), "Hello world", fill=(0, 0, 0))
        img.save(path)

    def test_ocr_chapter_writes_ocr_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            fake = FakeOCR({"p1.png": [{"box": [10, 10, 280, 60], "text": "Hello world"}]})
            regions = ocr_chapter(d, fake, lang="eng")
            self.assertEqual(len(regions), 1)
            self.assertEqual(regions[0]["index"], 0)
            data = json.loads((d / "ocr.json").read_text(encoding="utf-8"))
            self.assertEqual(data["regions"][0]["text"], "Hello world")

    def test_replace_texts_draws_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "p1.png"
            out = Path(tmp) / "out.png"
            self._make_image(src)
            regions = [{"index": 0, "box": [10, 10, 280, 60], "text": "Hello world"}]
            replace_texts(src, regions, ["Bonjour le monde"], out)
            self.assertTrue(out.exists())
            img = Image.open(out)
            self.assertEqual(img.size, (300, 120))
            colors = img.getcolors(maxcolors=100000)
            self.assertGreater(len(colors), 2, "le texte redessiné doit modifier des pixels")

    def test_wrap_text(self):
        img = Image.new("RGB", (10, 10))
        d = ImageDraw.Draw(img)
        from scalper.typeset import _load_font

        lines = wrap_text(d, "un deux trois quatre cinq", _load_font(10), 40)
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines).split(), "un deux trois quatre cinq".split())

    def test_typeset_chapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            (d / "ocr.json").write_text(
                json.dumps({"lang": "eng", "regions": [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]}),
                encoding="utf-8",
            )
            (d / "chapter.fr.json").write_text(
                json.dumps({"meta": {"language": "fr"}, "blocks": [{"index": 0, "type": "dialogue", "text": "Bonjour le monde"}]}),
                encoding="utf-8",
            )
            outputs = typeset_chapter(d, "fr")
            self.assertEqual(len(outputs), 1)
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists())

    def test_typeset_chapter_explicit_translations(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            (d / "ocr.json").write_text(
                json.dumps({"lang": "eng", "regions": [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]}),
                encoding="utf-8",
            )
            outputs = typeset_chapter(d, "fr", translations=["Bonjour le monde"])
            self.assertEqual(len(outputs), 1)
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists())

    def test_inpaint_uses_background_ring_not_text(self):
        img = Image.new("RGB", (300, 120), (200, 220, 255))
        d = ImageDraw.Draw(img)
        d.text((20, 20), "Texte noir sur fond clair", fill=(0, 0, 0))
        box = [10, 10, 280, 60]
        fill = _inpaint(img, box)
        # la couleur estimée doit rester proche du fond (200,220,255), pas assombrie par le texte
        self.assertGreater(fill[0], 150)
        self.assertGreater(fill[1], 170)
        self.assertGreater(fill[2], 200)
        px = img.getpixel((150, 35))
        self.assertGreater(px[0], 150)

    def test_replace_texts_stays_inside_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "p.png", Path(tmp) / "out.png"
            img = Image.new("RGB", (400, 200), (200, 220, 255))
            ImageDraw.Draw(img).text((60, 60), "Hi", fill=(0, 0, 0))
            img.save(src)
            regions = [{"index": 0, "box": [50, 50, 120, 30], "text": "Hi"}]  # x, y, w, h
            replace_texts(src, regions, ["Une traduction beaucoup plus longue que l'original"], out)
            res = Image.open(out).convert("RGB")
            self.assertEqual(res.getpixel((10, 10)), (200, 220, 255))   # hors boîte : inchangé
            self.assertEqual(res.getpixel((300, 150)), (200, 220, 255))  # idem
            dark = any(res.getpixel((px, py))[0] < 100 for px in range(50, 170) for py in range(50, 80))
            self.assertTrue(dark, "le texte traduit doit être redessiné dans la boîte")

    def test_degenerate_region_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "p.png", Path(tmp) / "out.png"
            self._make_image(src)
            before = Image.open(src).convert("RGB").copy()
            regions = [{"index": 0, "box": [5, 5, 4, 4], "text": "x"}]  # trop petit = bruit
            replace_texts(src, regions, ["y"], out)
            from PIL import ImageChops

            self.assertIsNone(ImageChops.difference(before, Image.open(out).convert("RGB")).getbbox())

    def test_pipeline_ocr_typeset_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            regions = [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]

            def fake_ocr(chapter_dir, client, lang="eng"):
                (Path(chapter_dir) / "ocr.json").write_text(
                    json.dumps({"lang": lang, "regions": regions}), encoding="utf-8"
                )
                return regions

            with mock.patch("scalper.ocr.ocr_chapter", side_effect=fake_ocr):
                out = pipeline._run_ocr_typeset(d, {"language": "en"}, "fr", FakeLLM(), {})
            self.assertTrue(out.startswith("ok"), out)
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists())


if __name__ == "__main__":
    unittest.main()
