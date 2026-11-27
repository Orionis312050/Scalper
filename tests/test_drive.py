import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from scalper.drive import export_stamp, folder_run_export, is_dev, upload_folder_to_drive


def _make_out(tmp: Path) -> Path:
    """out/ factice sans images : 2 chapitres avec export/ (md/json/html) + metadata."""
    out = tmp / "out"
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"done": ["ch1", "ch2"]}), encoding="utf-8")
    (out / "glossary.json").write_text("{}", encoding="utf-8")
    for cid in ("ch1", "ch2"):
        ch = out / "raw" / "mangadex" / "ser1" / cid
        (ch / "export").mkdir(parents=True)
        (ch / "export" / f"{cid}.md").write_text(f"# {cid}\n", encoding="utf-8")
        (ch / "export" / f"{cid}.json").write_text(json.dumps({"blocks": []}), encoding="utf-8")
        (ch / "meta.json").write_text(json.dumps({"title": cid}), encoding="utf-8")
        (ch / "qa.fr.json").write_text(json.dumps({"score": 100, "pass": True, "issues": []}), encoding="utf-8")
    return out


class TestIsDev(unittest.TestCase):
    def test_only_explicit_dev(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_dev())
        with mock.patch.dict(os.environ, {"SCALPER_ENV": "prod"}):
            self.assertFalse(is_dev())
        with mock.patch.dict(os.environ, {"SCALPER_ENV": "dev"}):
            self.assertTrue(is_dev())
        with mock.patch.dict(os.environ, {"SCALPER_ENV": "DEV"}):
            self.assertTrue(is_dev())

    def test_export_stamp(self):
        self.assertEqual(export_stamp(datetime(2026, 11, 24, 13, 45)), "export-2026-11-24-13-45")


class TestFolderRunExport(unittest.TestCase):
    def test_folder_name_contents_and_no_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            when = datetime(2026, 11, 24, 9, 5)
            folder = folder_run_export(out, "mangadex", "ser1", "fr", ["ch1", "ch2"], when=when)
            self.assertEqual(folder.name, "export-2026-11-24-09-05")
            self.assertTrue(folder.is_dir())
            # contenu de l'export présent dans le dossier
            self.assertEqual((folder / "export" / "ch1" / "ch1.md").read_text(encoding="utf-8"), "# ch1\n")
            self.assertTrue((folder / "export" / "ch2" / "ch2.json").exists())
            self.assertTrue((folder / "metadata" / "ch1" / "meta.json").exists())
            self.assertTrue((folder / "metadata" / "ch1" / "qa.fr.json").exists())
            # pas de zip, et le reste de out/ est conservé (dev : pas de clean)
            self.assertFalse(list(out.glob("export-*.zip")))
            self.assertTrue((out / "state.json").exists())
            self.assertTrue((out / "raw").is_dir())

    def test_chapter_ids_default_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            folder = folder_run_export(out, "mangadex", "ser1", "fr", when=datetime(2026, 1, 2, 3, 4))
            self.assertTrue((folder / "export" / "ch1").is_dir())
            self.assertTrue((folder / "export" / "ch2").is_dir())


class TestPipelineDevExport(unittest.TestCase):
    def test_run_zip_export_becomes_folder_in_dev(self):
        from scalper import pipeline

        with tempfile.TemporaryDirectory() as tmp:
            steps = []
            with mock.patch.dict(os.environ, {"SCALPER_ENV": "dev", "GOOGLE_DRIVE_ACCESS_TOKEN": ""}):
                res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True,
                                            zip_export=True, on_step=steps.append)
            self.assertEqual(res["status"], "done")
            self.assertIn("export_folder", res)
            self.assertNotIn("export_zip", res)
            folder = Path(res["export_folder"])
            self.assertRegex(folder.name, r"^export-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}$")
            self.assertTrue(folder.is_dir())
            # les exports md/json/html des chapitres sont dans le dossier
            self.assertTrue(list((folder / "export" / "demo-1-ch1").glob("*.md")))
            self.assertTrue(list((folder / "export" / "demo-1-ch2").glob("*.html")))
            # pas de token Drive -> upload ignoré, mais pas d'échec
            self.assertTrue(str(res["export_drive"]).startswith("skipped:"))
            # dev : out/ non nettoyé
            self.assertTrue((Path(tmp) / "state.json").exists())
            self.assertIn("drive-export", steps)

    def test_run_zip_export_stays_zip_outside_dev(self):
        from scalper import pipeline

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SCALPER_ENV": "prod"}):
                res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True, zip_export=True)
            self.assertIn("export_zip", res)
            self.assertNotIn("export_folder", res)
            self.assertTrue(Path(res["export_zip"]).exists())

    def test_cli_pack_folder_in_dev(self):
        from scalper.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            with mock.patch.dict(os.environ, {"SCALPER_ENV": "dev"}):
                rc = main(["pack", "--out", str(out), "--source", "mangadex", "--series", "ser1", "--lang", "fr"])
            self.assertEqual(rc, 0)
            folders = [p for p in out.iterdir() if p.is_dir() and re.match(r"export-\d{4}-", p.name)]
            self.assertEqual(len(folders), 1)
            self.assertFalse(list(out.glob("export-*.zip")))
            self.assertTrue((out / "state.json").exists())


class TestDriveUpload(unittest.TestCase):
    def test_missing_token_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"GOOGLE_DRIVE_ACCESS_TOKEN": ""}):
                with self.assertRaises(RuntimeError):
                    upload_folder_to_drive(tmp)


if __name__ == "__main__":
    unittest.main()
