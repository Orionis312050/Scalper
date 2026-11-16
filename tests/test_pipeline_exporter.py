import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scalper import pipeline
from scalper.exporter import export_chapter, slugify
from scalper.models import Block, Chapter
from scalper.sources import get_adapter


class TestPipeline(unittest.TestCase):
    def test_demo_end_to_end_mock_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True)
            self.assertEqual(res["status"], "done")
            self.assertEqual(len(res["chapters"]), 2)
            for ch in res["chapters"]:
                self.assertEqual(ch["status"], "ok", ch)
                self.assertGreaterEqual(ch["qa"]["score"], 80)
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["done"]), 2)
            raw1 = Path(tmp) / "raw" / "demo" / "demo-1" / "demo-1-ch1"
            self.assertTrue((raw1 / "chapter.fr.json").exists())
            self.assertTrue((raw1 / "export" / (raw1.name + ".md").replace("demo-1-ch1", "demo-1-1-fr")).exists() or list((raw1 / "export").glob("*.md")))

    def test_resume_skips_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True, max_chapters=1)
            res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True)
            self.assertTrue(any(c.get("skipped") for c in res["chapters"]))

    def test_qa_failure_blocks_export_without_force(self):
        fake = {"score": 10, "pass": False, "issues": [{"type": "x", "critical": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scalper.pipeline.qa_chapter", return_value=fake):
                res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True, max_chapters=1)
            self.assertEqual(res["chapters"][0]["status"], "qa_failed")
            raw1 = Path(tmp) / "raw" / "demo" / "demo-1" / "demo-1-ch1"
            self.assertFalse((raw1 / "export").exists())

    def test_force_exports_despite_qa_failure(self):
        fake = {"score": 10, "pass": False, "issues": [{"type": "x", "critical": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scalper.pipeline.qa_chapter", return_value=fake):
                res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True, max_chapters=1, force=True)
            self.assertEqual(res["status"], "done")
            ch = res["chapters"][0]
            self.assertEqual(ch["status"], "ok")
            self.assertTrue(ch.get("forced"))
            self.assertFalse(ch["qa"]["pass"])
            raw1 = Path(tmp) / "raw" / "demo" / "demo-1" / "demo-1-ch1"
            self.assertTrue(list((raw1 / "export").glob("*.md")))


class TestExporter(unittest.TestCase):
    def test_exports(self):
        ch = Chapter(
            meta={"source": "demo", "fetched_at": "2026-01-01", "url": "demo://x", "title": "T", "language": "fr"},
            blocks=[Block(index=0, type="narration", text="Bonjour"), Block(index=1, type="image", image="images/p.png")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            md = export_chapter(ch, "md", Path(tmp))
            self.assertIn("Bonjour", md.read_text(encoding="utf-8"))
            self.assertIn("[IMG:1]", md.read_text(encoding="utf-8"))
            js = export_chapter(ch, "json", Path(tmp))
            self.assertEqual(json.loads(js.read_text(encoding="utf-8"))["blocks"][1]["image"], "images/p.png")
            ht = export_chapter(ch, "html", Path(tmp))
            self.assertIn("Bonjour", ht.read_text(encoding="utf-8"))

    def test_slugify(self):
        self.assertEqual(slugify("Chapitre 12 : L'orage !"), "chapitre-12-l-orage")

    def test_adapter_registry(self):
        self.assertEqual(get_adapter("demo").id, "demo")
        with self.assertRaises(KeyError):
            get_adapter("inconnu")


if __name__ == "__main__":
    unittest.main()
