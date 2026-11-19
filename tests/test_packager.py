import json
import re
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from scalper.packager import build_chapter_pdf, chapter_images, page_sort_key, zip_run_export

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _png(path: Path, color=(200, 220, 255), size=(60, 80)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _make_out(tmp: Path) -> Path:
    """out/ factice : 2 chapitres (ch1 avec images traduites, ch2 avec images seules)."""
    out = tmp / "out"
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"done": ["ch1", "ch2"]}), encoding="utf-8")
    (out / "glossary.json").write_text("{}", encoding="utf-8")
    ch1 = out / "raw" / "mangadex" / "ser1" / "ch1"
    for name in ("1-a.png", "2-b.png", "10-c.png"):
        _png(ch1 / "images" / name, color=(255, 255, 255))          # originales
        _png(ch1 / "export" / "images-fr" / name, color=(200, 220, 255))  # traduites
        _png(ch1 / "export" / "images-clean" / name, color=(250, 250, 250))  # nettoyées
    (ch1 / "ocr.json").write_text(json.dumps({"lang": "en", "regions": []}), encoding="utf-8")
    (ch1 / "meta.json").write_text(json.dumps({"title": "ch1"}), encoding="utf-8")
    (ch1 / "chapter.fr.json").write_text(json.dumps({"blocks": []}), encoding="utf-8")
    ch2 = out / "raw" / "mangadex" / "ser1" / "ch2"
    _png(ch2 / "images" / "1.png")
    return out


class TestPageSortKey(unittest.TestCase):
    def test_natural_order(self):
        names = ["10-c.png", "2-b.png", "1-a.png"]
        self.assertEqual(sorted(names, key=page_sort_key), ["1-a.png", "2-b.png", "10-c.png"])


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestChapterImages(unittest.TestCase):
    def test_prefers_translated_and_sorts_naturally(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            pages = chapter_images(out / "raw" / "mangadex" / "ser1" / "ch1", "fr")
            self.assertEqual([p.name for p in pages], ["1-a.png", "2-b.png", "10-c.png"])
            self.assertIn("images-fr", str(pages[0]))
            # sans export traduit : repli sur les images d'origine
            pages2 = chapter_images(out / "raw" / "mangadex" / "ser1" / "ch2", "fr")
            self.assertEqual([p.name for p in pages2], ["1.png"])
            self.assertIn(f"{Path('images')}", str(pages2[0]))
            self.assertNotIn("images-fr", str(pages2[0]))

    def test_no_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "ch"
            d.mkdir()
            self.assertEqual(chapter_images(d, "fr"), [])


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestBuildPdf(unittest.TestCase):
    def test_pdf_multipage(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = []
            for i in range(3):
                p = Path(tmp) / f"{i + 1}-p.png"
                _png(p, size=(100 + i, 50))
                pages.append(p)
            pdf = build_chapter_pdf(pages, Path(tmp) / "ch.pdf")
            data = pdf.read_bytes()
            self.assertTrue(data.startswith(b"%PDF"))
            n_pages = len(re.findall(rb"/Type\s*/Page(?![s])", data))
            self.assertEqual(n_pages, 3, f"3 pages attendues, trouvé {n_pages}")

    def test_pdf_downscales_big_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "1-big.png"
            _png(p, size=(4000, 3000))
            pdf = build_chapter_pdf([p], Path(tmp) / "big.pdf")
            data = pdf.read_bytes()
            self.assertIn(b"%PDF", data[:8])
            # MediaBox en points (resolution=100 -> 4000px = 2880pt) : la page doit être réduite
            m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
            self.assertIsNotNone(m)
            w_pt = float(m.group(1))
            self.assertLessEqual(w_pt, 4000 * 72 / 100 + 1, "page redimensionnée à PDF_MAX_SIDE")


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestZipRunExport(unittest.TestCase):
    def test_zip_name_contents_and_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            when = datetime(2026, 11, 17, 19, 30)
            z = zip_run_export(out, "mangadex", "ser1", "fr", ["ch1", "ch2"], when=when)
            self.assertEqual(z.name, "export-2026-11-17-19-30.zip")
            self.assertTrue(z.exists())
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
            # images traduites de ch1 dans l'ordre naturel + pdf par chapitre
            self.assertEqual(
                [n for n in names if n.startswith("images/ch1/")],
                ["images/ch1/1-a.png", "images/ch1/2-b.png", "images/ch1/10-c.png"],
            )
            self.assertIn("images/ch2/1.png", names)  # ch2 : pas de traduction -> originales
            self.assertIn("pdf/ch1.pdf", names)
            self.assertIn("pdf/ch2.pdf", names)
            # metadata/ embarquée par défaut (ocr.json, meta.json, chapter.<lang>.json…)
            self.assertIn("metadata/ch1/ocr.json", names)
            self.assertIn("metadata/ch1/meta.json", names)
            self.assertIn("metadata/ch1/chapter.fr.json", names)
            # cleaned/ PAS embarquée par défaut
            self.assertFalse(any(n.startswith("cleaned/") for n in names))
            self.assertNotIn("state.json", names)
            self.assertNotIn("glossary.json", names)
            with zipfile.ZipFile(z) as zf:
                pdf_data = zf.read("pdf/ch1.pdf")
            self.assertEqual(len(re.findall(rb"/Type\s*/Page(?![s])", pdf_data)), 3)
            # clean : il ne reste que le zip dans out/
            self.assertEqual([p.name for p in out.iterdir()], [z.name])

    def test_no_clean_keeps_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            z = zip_run_export(out, "mangadex", "ser1", "fr", ["ch1"], clean=False, when=datetime(2026, 1, 2, 3, 4))
            self.assertEqual(z.name, "export-2026-01-02-03-04.zip")
            remaining = {p.name for p in out.iterdir()}
            self.assertIn(z.name, remaining)
            self.assertIn("state.json", remaining)
            self.assertIn("raw", remaining)

    def test_include_clean_embeds_cleaned_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            z = zip_run_export(out, "mangadex", "ser1", "fr", ["ch1"], clean=False,
                               when=datetime(2026, 3, 4, 5, 6), include_clean=True)
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
            self.assertEqual(
                sorted(n for n in names if n.startswith("cleaned/ch1/")),
                ["cleaned/ch1/1-a.png", "cleaned/ch1/10-c.png", "cleaned/ch1/2-b.png"],
            )
            self.assertIn("metadata/ch1/ocr.json", names)  # metadata toujours là

    def test_chapter_ids_default_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            z = zip_run_export(out, "mangadex", "ser1", "fr", when=datetime(2026, 5, 6, 7, 8))
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
            self.assertTrue(any(n.startswith("images/ch1/") for n in names))
            self.assertTrue(any(n.startswith("images/ch2/") for n in names))

    def test_pipeline_zip_export(self):
        from scalper import pipeline

        with tempfile.TemporaryDirectory() as tmp:
            res = pipeline.run_pipeline("demo", "demo-1", "fr", out_root=tmp, mock_llm=True, zip_export=True)
            self.assertEqual(res["status"], "done")
            self.assertIn("export_zip", res)
            z = Path(res["export_zip"])
            self.assertTrue(z.exists())
            self.assertRegex(z.name, r"^export-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}\.zip$")
            # le reste de out/ a été supprimé
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [z.name])

    def test_cli_pack(self):
        from scalper.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = _make_out(Path(tmp))
            rc = main(["pack", "--out", str(out), "--source", "mangadex", "--series", "ser1", "--lang", "fr"])
            self.assertEqual(rc, 0)
            zips = list(out.glob("export-*.zip"))
            self.assertEqual(len(zips), 1)
            self.assertEqual([p.name for p in out.iterdir()], [zips[0].name])


if __name__ == "__main__":
    unittest.main()
