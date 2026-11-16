import unittest
from importlib import resources

from scalper.cleaner import clean_chapter, detect_language


class TestCleaner(unittest.TestCase):
    def setUp(self):
        raw = (resources.files("scalper") / "data" / "demo" / "chapter-1.html").read_text(encoding="utf-8")
        self.chapter = clean_chapter(raw, {"source": "demo", "license": "fixture"})

    def test_boilerplate_removed(self):
        text = self.chapter.full_text().lower()
        self.assertNotIn("soutenez-nous", text)
        self.assertNotIn("scanlation", text)
        self.assertNotIn("suivant", text)

    def test_blocks_typed(self):
        types = [b.type for b in self.chapter.blocks]
        self.assertIn("narration", types)
        self.assertIn("dialogue", types)
        self.assertIn("onomatopoeia", types)
        self.assertIn("image", types)

    def test_image_placeholder(self):
        ph = self.chapter.placeholders()
        self.assertEqual(len(ph), 1)
        self.assertTrue(ph[0].startswith("[IMG:"))

    def test_language_detected(self):
        self.assertEqual(self.chapter.meta["language"], "en")

    def test_detect_language(self):
        self.assertEqual(detect_language("Bonjour le monde, ceci est un test"), "fr")
        self.assertEqual(detect_language("Hello the world this is a test"), "en")
        self.assertEqual(detect_language("これはテストです"), "ja")


if __name__ == "__main__":
    unittest.main()
