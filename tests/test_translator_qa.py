import unittest
from importlib import resources

from scalper.cleaner import clean_chapter
from scalper.models import Block, Chapter
from scalper.qa import qa_chapter
from scalper.translator import FakeLLM, chunk_blocks, translate_chapter


def demo_chapter():
    raw = (resources.files("scalper") / "data" / "demo" / "chapter-1.html").read_text(encoding="utf-8")
    return clean_chapter(raw, {"source": "demo", "language": "en"})


class TestTranslator(unittest.TestCase):
    def test_translate_preserves_structure(self):
        src = demo_chapter()
        glossary = {"hero": "héros"}
        tr = translate_chapter(src, "fr", glossary, FakeLLM())
        self.assertEqual(len(tr.blocks), len(src.blocks))
        self.assertEqual(tr.placeholders(), src.placeholders())
        self.assertEqual(tr.meta["language"], "fr")
        self.assertNotEqual(tr.full_text(), src.full_text())

    def test_chunking(self):
        blocks = [Block(index=i, type="narration", text="x" * 100) for i in range(100)]
        chunks = chunk_blocks(blocks, max_chars=1000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(len(c) for c in chunks), 100)


class TestQA(unittest.TestCase):
    def test_good_translation_passes(self):
        src = demo_chapter()
        tr = translate_chapter(src, "fr", {}, FakeLLM())
        report = qa_chapter(src, tr, {}, "fr")
        self.assertTrue(report["pass"], report)
        self.assertGreaterEqual(report["score"], 80)

    def test_glossary_violation_detected(self):
        src = Chapter(meta={"language": "en"}, blocks=[Block(index=0, type="narration", text="the hero wins")])
        tr = Chapter(meta={"language": "fr"}, blocks=[Block(index=0, type="narration", text="le champion gagne")])
        report = qa_chapter(src, tr, {"hero": "héros"}, "fr")
        self.assertFalse(report["pass"])
        self.assertTrue(any(i["type"] == "glossary" for i in report["issues"]))

    def test_block_count_mismatch_critical(self):
        src = Chapter(meta={}, blocks=[Block(index=0, type="narration", text="a"), Block(index=1, type="narration", text="b")])
        tr = Chapter(meta={}, blocks=[Block(index=0, type="narration", text="a")])
        report = qa_chapter(src, tr, {}, "fr")
        self.assertFalse(report["pass"])
        self.assertTrue(any(i.get("critical") for i in report["issues"]))

    def test_wrong_language_critical(self):
        src = Chapter(meta={"language": "en"}, blocks=[Block(index=0, type="narration", text="the hero wins today")])
        tr = Chapter(meta={"language": "fr"}, blocks=[Block(index=0, type="narration", text="the hero wins today")])
        report = qa_chapter(src, tr, {}, "fr")
        self.assertFalse(report["pass"])
        self.assertTrue(any(i["type"] == "wrong_language" for i in report["issues"]))


if __name__ == "__main__":
    unittest.main()
