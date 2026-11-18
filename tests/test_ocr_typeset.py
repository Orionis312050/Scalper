import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scalper import pipeline
from scalper.inpaint import _inpaint
from scalper.ocr import FakeOCR, ocr_chapter
from scalper.translator import FakeLLM
from scalper.typeset import replace_texts, typeset_chapter, wrap_text

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

    def test_explicit_engine_param_wins_over_env(self):
        from scalper.ocr import PaddleOCR, TesseractOCR, default_ocr_client

        with mock.patch.dict(os.environ, {"SCALPER_OCR_ENGINE": "paddle"}):
            self.assertIsInstance(default_ocr_client(engine="tesseract"), TesseractOCR)
        with mock.patch.dict(os.environ, {"SCALPER_OCR_ENGINE": "tesseract"}):
            self.assertIsInstance(default_ocr_client(engine="paddle"), PaddleOCR)


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
        # quadrilatère conservé pour le masque d'inpainting LaMa
        self.assertEqual(regions[0]["poly"], [[100, 50], [130, 50], [130, 70], [100, 70]])

    def test_recognize_tolerates_array_like_fields(self):
        """rec_boxes/dt_polys peuvent être des ndarray : `x or y` crashe (truth value ambigu)."""
        from scalper.ocr import PaddleOCR

        class FakeArray(list):
            def __bool__(self):
                raise ValueError("The truth value of an empty array is ambiguous")

        class FakeEngine:
            def __init__(self, **kw):
                pass

            def predict(self, path):
                if path == "empty.png":
                    return [{"rec_texts": [], "rec_scores": [], "dt_polys": FakeArray(), "rec_boxes": FakeArray()}]
                return [{
                    "rec_texts": ["Hello", "World"],
                    "rec_scores": [0.95, 0.9],
                    "dt_polys": FakeArray([
                        [[100, 50], [130, 50], [130, 70], [100, 70]],
                        [[10, 10], [40, 10], [40, 30], [10, 30]],
                    ]),
                    "rec_boxes": FakeArray(),
                }]

        fake_mod = types.ModuleType("paddleocr")
        fake_mod.PaddleOCR = FakeEngine
        with mock.patch.dict(sys.modules, {"paddleocr": fake_mod}):
            client = PaddleOCR()
            empty = client.recognize(Path("empty.png"))
            full = client.recognize(Path("multi.png"))
        self.assertEqual(empty, [])
        self.assertEqual([r["text"] for r in full], ["Hello", "World"])
        self.assertEqual(full[0]["box"], [100, 50, 30, 20])

    def test_missing_paddle_raises_clear_error(self):
        from scalper.ocr import PaddleOCR

        with mock.patch.dict(sys.modules, {"paddleocr": None}):
            with self.assertRaises(RuntimeError) as cm:
                PaddleOCR().recognize(Path("p.png"))
            self.assertIn("ocr-paddle", str(cm.exception))

    def test_pipeline_ocr_error_is_surfaced_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            with mock.patch("scalper.ocr.default_ocr_client", side_effect=RuntimeError("boom")):
                out = pipeline._run_ocr_typeset(d, {"language": "en"}, "fr", FakeLLM(), {})
        self.assertTrue(out.startswith("erreur"), out)
        self.assertIn("boom", out)

    def test_pipeline_forwards_ocr_engine_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            seen = {}

            def fake_client(lang="eng", engine=None):
                seen["lang"] = lang
                seen["engine"] = engine
                return FakeOCR({"p1.png": []})

            with mock.patch("scalper.ocr.default_ocr_client", side_effect=fake_client):
                out = pipeline._run_ocr_typeset(d, {"language": "en"}, "fr", FakeLLM(), {}, ocr_engine="paddle")
        self.assertEqual(seen["engine"], "paddle")
        self.assertTrue(out.startswith("skipped"), out)  # aucune région -> skipped, pas d'erreur


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

    def test_fit_font_uses_full_original_box_height(self):
        img = Image.new("RGB", (10, 10))
        d = ImageDraw.Draw(img)
        from scalper.typeset import _fit_font
        font, size, lines = _fit_font(d, "Bonjour", 380, 40)
        self.assertEqual(size, 38, "taille max = hauteur de boîte OCR - 2 (même taille que l'original)")
        self.assertEqual(lines, ["Bonjour"])
        _, size2, lines2 = _fit_font(d, "Une traduction beaucoup plus longue que l'original", 120, 30)
        self.assertLess(size2, 28)
        self.assertGreater(len(lines2), 1)

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

    def test_typeset_chapter_ecrit_images_clean_puis_traduites(self):
        """Flux en deux étapes : 3/4 inpainting -> export/images-clean, 4/4 retypographie
        -> export/images-<lang>."""
        from scalper.inpaint import SimpleInpainter

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            (d / "ocr.json").write_text(
                json.dumps({"lang": "eng", "regions": [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]}),
                encoding="utf-8",
            )
            outputs = typeset_chapter(d, "fr", translations=["Bonjour le monde"], inpainter=SimpleInpainter())
            self.assertEqual(len(outputs), 1)
            clean = d / "export" / "images-clean" / "p1.png"
            trad = d / "export" / "images-fr" / "p1.png"
            self.assertTrue(clean.exists(), "étape 3 : image nettoyée (inpainting)")
            self.assertTrue(trad.exists(), "étape 4 : image re-traduite")
            c = Image.open(clean).convert("RGB")
            dark = any(c.getpixel((px, py))[0] < 100 for px in range(10, 290) for py in range(10, 70))
            self.assertFalse(dark, "le texte original doit être entièrement effacé dans images-clean")
            t = Image.open(trad).convert("RGB")
            dark_t = any(t.getpixel((px, py))[0] < 100 for px in range(10, 290) for py in range(10, 70))
            self.assertTrue(dark_t, "la traduction doit être redessinée dans images-<lang>")

    def test_replace_texts_draw_only_ne_inpaint_pas(self):
        """inpainter=_DRAW_ONLY : retypographie seule, l'image d'entrée n'est pas inpaintée
        (utilisé sur les images déjà nettoyées d'export/images-clean)."""
        from scalper.typeset import _DRAW_ONLY

        class RedInpainter:
            def inpaint_image(self, img, regions):
                d = ImageDraw.Draw(img)
                for r in regions:
                    x, y, w, h = r["box"]
                    d.rectangle([x, y, x + w, y + h], fill=(255, 0, 0))
                return img

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "p.png"
            out1, out2 = Path(tmp) / "o1.png", Path(tmp) / "o2.png"
            self._make_image(src)
            regions = [{"index": 0, "box": [10, 10, 280, 60], "text": "Hello world"}]
            replace_texts(src, regions, ["x"], out1, inpainter=RedInpainter())
            replace_texts(src, regions, ["x"], out2, inpainter=_DRAW_ONLY)
            r1 = Image.open(out1).convert("RGB")
            r2 = Image.open(out2).convert("RGB")
            self.assertEqual(r1.getpixel((12, 12)), (255, 0, 0), "inpainter explicite : boîte remplie")
            self.assertEqual(r2.getpixel((12, 12)), (200, 220, 255), "_DRAW_ONLY : boîte non inpaintée")

    def test_typeset_chapter_explicit_translations(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            (d / "ocr.json").write_text(
                json.dumps({"lang": "eng", "regions": [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]}),
                encoding="utf-8",
            )
            self._make_image(d / "images" / "p2.png", size=(200, 100))  # sans région OCR
            outputs = typeset_chapter(d, "fr", translations=["Bonjour le monde"])
            self.assertEqual(len(outputs), 2)
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists())
            self.assertTrue((d / "export" / "images-fr" / "p2.png").exists(), "page sans texte copiée telle quelle")

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
            self.assertTrue((d / "export" / "images-clean" / "p1.png").exists(), "étape 3 : inpainting -> images-clean")
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists(), "étape 4 : retypographie -> images-fr")

    def test_pipeline_ocr_typeset_reports_steps_in_order(self):
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

            steps = []
            with mock.patch("scalper.ocr.ocr_chapter", side_effect=fake_ocr):
                out = pipeline._run_ocr_typeset(d, {"language": "en"}, "fr", FakeLLM(), {},
                                                inpaint_engine="simple", on_step=steps.append)
            self.assertTrue(out.startswith("ok"), out)
            self.assertEqual(steps, ["ocr", "translate", "inpaint", "typeset"], steps)


if __name__ == "__main__":
    unittest.main()
