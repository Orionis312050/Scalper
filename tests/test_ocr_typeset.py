import json
import tempfile
import unittest
from pathlib import Path

from scalper import legal
from scalper.ocr import FakeOCR, ocr_chapter
from scalper.typeset import replace_texts, typeset_chapter, wrap_text

try:
    from PIL import Image, ImageDraw

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class TestDerivativeGate(unittest.TestCase):
    def test_demo_license_allows_derivatives(self):
        self.assertTrue(legal.allows_derivatives("fixture de test interne (texte inventé, domaine public)"))

    def test_mangadex_license_refuses_derivatives(self):
        self.assertFalse(legal.allows_derivatives("API officielle MangaDex (données publiques ; images selon licence de l'œuvre)"))

    def test_cc_license_allows_derivatives(self):
        self.assertTrue(legal.allows_derivatives("Creative Commons CC BY 4.0"))


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


if __name__ == "__main__":
    unittest.main()
