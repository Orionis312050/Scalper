import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scalper.inpaint import (
    LAMA_INPUT_SIZE,
    LamaInpainter,
    SimpleInpainter,
    _download_model,
    default_inpainter,
)

try:
    from PIL import Image, ImageDraw

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class TestEngineSelection(unittest.TestCase):
    def test_simple_via_env(self):
        with mock.patch.dict(os.environ, {"SCALPER_INPAINT_ENGINE": "simple"}):
            self.assertIsInstance(default_inpainter(), SimpleInpainter)

    def test_lama_via_env(self):
        with mock.patch.dict(os.environ, {"SCALPER_INPAINT_ENGINE": "lama"}):
            self.assertIsInstance(default_inpainter(), LamaInpainter)

    def test_explicit_engine_wins_over_env(self):
        with mock.patch.dict(os.environ, {"SCALPER_INPAINT_ENGINE": "lama"}):
            self.assertIsInstance(default_inpainter("simple"), SimpleInpainter)

    def test_auto_falls_back_to_simple_without_onnxruntime(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.dict(sys.modules, {"onnxruntime": None}):
                self.assertIsInstance(default_inpainter(), SimpleInpainter)

    def test_auto_picks_lama_when_onnxruntime_available(self):
        fake_mod = types.ModuleType("onnxruntime")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.dict(sys.modules, {"onnxruntime": fake_mod}):
                self.assertIsInstance(default_inpainter(), LamaInpainter)

    def test_unknown_engine_raises(self):
        with self.assertRaises(ValueError):
            default_inpainter("bogus")


class TestModelResolution(unittest.TestCase):
    def test_explicit_model_path_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as cm:
                LamaInpainter(model_path=Path(tmp) / "nope.onnx")._resolve_model_path()
            self.assertIn("SCALPER_LAMA_MODEL", str(cm.exception))

    def test_env_model_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "lama_fp32.onnx"
            model.write_bytes(b"fake")
            with mock.patch.dict(os.environ, {"SCALPER_LAMA_MODEL": str(model)}):
                self.assertEqual(LamaInpainter()._resolve_model_path(), model)

    def test_cache_hit_does_not_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            (cache / "lama_fp32.onnx").write_bytes(b"fake")
            with mock.patch.dict(os.environ, {"SCALPER_LAMA_CACHE": str(cache)}):
                with mock.patch("scalper.inpaint._download_model", side_effect=AssertionError("ne doit pas télécharger")):
                    self.assertEqual(LamaInpainter()._resolve_model_path(), cache / "lama_fp32.onnx")

    def test_cache_miss_downloads_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            with mock.patch.dict(os.environ, {"SCALPER_LAMA_CACHE": str(cache)}):
                with mock.patch("scalper.inpaint._download_model") as dl:
                    def fake_download(url, dest):
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(b"fake")
                    dl.side_effect = fake_download
                    p1 = LamaInpainter()._resolve_model_path()
                    p2 = LamaInpainter()._resolve_model_path()
            self.assertEqual(p1, cache / "lama_fp32.onnx")
            self.assertEqual(dl.call_count, 1, "un second appel doit réutiliser le cache")
            self.assertEqual(p2, p1)


class _FakeResp:
    def __init__(self, data: bytes):
        self._io = io.BytesIO(data)

    def __enter__(self):
        return self._io

    def __exit__(self, *a):
        return False


class TestDownloadModel(unittest.TestCase):
    def test_download_verifies_sha256_and_renames(self):
        payload = b"x" * 1000
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "lama_fp32.onnx"
            with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
                with mock.patch("scalper.inpaint.LAMA_MODEL_SIZE", 1000), \
                     mock.patch("scalper.inpaint.LAMA_MODEL_SHA256", hashlib.sha256(payload).hexdigest()):
                    _download_model("https://example.invalid/model.onnx", dest)
            self.assertEqual(dest.read_bytes(), payload)
            self.assertFalse((Path(tmp) / "lama_fp32.onnx.part").exists(), "le .part doit être renommé")

    def test_download_bad_sha256_raises_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "lama_fp32.onnx"
            with mock.patch("urllib.request.urlopen", return_value=_FakeResp(b"y" * 1000)):
                with mock.patch("scalper.inpaint.LAMA_MODEL_SIZE", 1000), \
                     mock.patch("scalper.inpaint.LAMA_MODEL_SHA256", "0" * 64):
                    with self.assertRaises(RuntimeError):
                        _download_model("https://example.invalid/model.onnx", dest)
            self.assertFalse(dest.exists())
            self.assertFalse((Path(tmp) / "lama_fp32.onnx.part").exists())


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestSimpleInpainter(unittest.TestCase):
    def _img(self, size=(300, 120), bg=(200, 220, 255)) -> Image.Image:
        img = Image.new("RGB", size, bg)
        ImageDraw.Draw(img).text((20, 20), "Texte noir sur fond clair", fill=(0, 0, 0))
        return img

    def test_ring_fill_not_text_average(self):
        from scalper.inpaint import _inpaint

        img = self._img()
        fill = _inpaint(img, [10, 10, 280, 60])
        # la couleur estimée doit rester proche du fond (200,220,255), pas assombrie par le texte
        self.assertGreater(fill[0], 150)
        self.assertGreater(fill[1], 170)
        self.assertGreater(fill[2], 200)
        self.assertGreater(img.getpixel((150, 35))[0], 150)

    def test_inpaint_image_fills_each_box(self):
        img = self._img()
        out = SimpleInpainter().inpaint_image(img, [
            {"box": [10, 10, 280, 60]},
            {"box": [0, 0, 0, 0]},        # invalide -> ignorée
            {"box": [200, 80, 50, 20]},
        ])
        self.assertIs(out, img)
        self.assertGreater(img.getpixel((150, 35))[0], 150)
        self.assertGreater(img.getpixel((220, 90))[0], 150)


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestCropAndMask(unittest.TestCase):
    def test_crop_rect_centered_with_context_and_clamped(self):
        inp = LamaInpainter()
        cx0, cy0, cx1, cy1 = inp._crop_rect((100, 100, 50, 30), 1000, 800)
        self.assertEqual(cx1 - cx0, cy1 - cy0)  # carré
        self.assertGreaterEqual(cx1 - cx0, 256)  # contexte min
        self.assertLessEqual(cx0, 100)
        self.assertGreaterEqual(cx1, 150)
        # boîte collée au bord : crop borné à l'image
        cx0, cy0, cx1, cy1 = inp._crop_rect((0, 0, 40, 40), 300, 300)
        self.assertEqual((cx0, cy0), (0, 0))
        self.assertLessEqual(cx1, 300)
        self.assertLessEqual(cy1, 300)

    def test_draw_region_mask_rect_and_poly(self):
        inp = LamaInpainter()
        size = LAMA_INPUT_SIZE
        mask = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(mask)
        inp._draw_region_mask(d, {"box": [100, 100, 50, 30]}, (100, 100, 50, 30), 0, 0, 1.0, 1.0)
        self.assertEqual(mask.getpixel((125, 115)), 255)
        self.assertEqual(mask.getpixel((10, 10)), 0)
        # avec poly : UNION du rectangle paddé (pad = 10 % de min(w,h) = 10) et du polygone
        mask2 = Image.new("L", (size, size), 0)
        d2 = ImageDraw.Draw(mask2)
        poly = [[100, 100], [200, 100], [200, 200], [100, 200]]
        inp._draw_region_mask(d2, {"box": [100, 100, 100, 100], "poly": poly}, (100, 100, 100, 100), 0, 0, 1.0, 1.0)
        self.assertEqual(mask2.getpixel((150, 150)), 255)   # dans le poly
        self.assertEqual(mask2.getpixel((95, 95)), 255)     # dans le rectangle paddé [90..210]
        self.assertEqual(mask2.getpixel((50, 50)), 0)       # hors des deux

    def test_draw_region_mask_rect_saves_stale_poly(self):
        """Robustesse : si le polygone OCR est hors du crop (coordonnées non normalisées),
        le rectangle paddé garantit quand même un masque non vide -> le texte est effacé."""
        inp = LamaInpainter()
        size = LAMA_INPUT_SIZE
        mask = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(mask)
        stale_poly = [[10000, 10000], [20000, 10000], [20000, 20000], [10000, 20000]]  # hors image
        inp._draw_region_mask(d, {"box": [100, 100, 50, 30], "poly": stale_poly}, (100, 100, 50, 30), 0, 0, 1.0, 1.0)
        import numpy as np

        self.assertGreater(int((np.asarray(mask) > 0).sum()), 0, "le masque ne doit jamais être vide (rect de secours)")
        self.assertEqual(mask.getpixel((125, 115)), 255)


def _fake_onnxruntime_module(fill_value=200.0):
    """Module onnxruntime factice : session qui renvoie `fill_value` dans le masque et
    recopopie l'entrée (x255) hors masque, comme le vrai modèle."""
    mod = types.ModuleType("onnxruntime")

    class FakeSession:
        instances = []

        def __init__(self, path, sess_options=None, providers=None):
            self.path = path
            self.providers = providers
            self.calls = []
            FakeSession.instances.append(self)

        def run(self, output_names, feeds):
            assert output_names == ["output"]
            self.calls.append(feeds)
            image, mask = feeds["image"], feeds["mask"]
            out = image * 255.0 * (1.0 - mask) + fill_value * mask
            return [out]

    class SessionOptions:
        pass

    mod.InferenceSession = FakeSession
    mod.SessionOptions = SessionOptions
    return mod, FakeSession


@unittest.skipUnless(HAS_PIL and HAS_NUMPY, "Pillow + numpy requis")
class TestLamaInpainter(unittest.TestCase):
    def _make_page(self, path: Path, size=(400, 300)) -> None:
        img = Image.new("RGB", size, (240, 240, 245))
        d = ImageDraw.Draw(img)
        d.rectangle([50, 40, 250, 110], fill=(255, 255, 255))
        d.text((70, 60), "Texte original", fill=(0, 0, 0))
        img.save(path)

    def test_missing_onnxruntime_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "lama_fp32.onnx"
            model.write_bytes(b"fake")
            inp = LamaInpainter(model_path=model)
            with mock.patch.dict(sys.modules, {"onnxruntime": None}):
                with self.assertRaises(RuntimeError) as cm:
                    inp._get_session()
            self.assertIn("inpaint-lama", str(cm.exception))

    def test_inpaint_image_composites_only_masked_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "lama_fp32.onnx"
            model.write_bytes(b"fake")
            src = Path(tmp) / "p.png"
            self._make_page(src)
            fake_mod, fake_session_cls = _fake_onnxruntime_module(fill_value=200.0)
            with mock.patch.dict(sys.modules, {"onnxruntime": fake_mod}):
                inp = LamaInpainter(model_path=model)
                img = Image.open(src).convert("RGB")
                regions = [{"index": 0, "box": [60, 50, 180, 50], "text": "Texte original"}]
                out = inp.inpaint_image(img, regions)
            sess = fake_session_cls.instances[-1]
            self.assertEqual(len(sess.calls), 1)
            feeds = sess.calls[0]
            self.assertEqual(feeds["image"].shape, (1, 3, LAMA_INPUT_SIZE, LAMA_INPUT_SIZE))
            self.assertEqual(feeds["mask"].shape, (1, 1, LAMA_INPUT_SIZE, LAMA_INPUT_SIZE))
            self.assertLessEqual(float(feeds["image"].max()), 1.0, "image normalisée en [0,1]")
            self.assertTrue(set(np.unique(feeds["mask"]).tolist()) <= {0.0, 1.0}, "masque binaire")
            out_arr = np.asarray(out)
            # zone masquée remplie par la valeur factice
            self.assertTrue(np.all(out_arr[60:90, 100:200] == 200), "pixels masqués remplacés")
            # hors masque : image d'origine inchangée
            self.assertEqual(tuple(out_arr[250, 350]), (240, 240, 245))
            self.assertEqual(tuple(out_arr[10, 10]), (240, 240, 245))

    def test_mask_is_dilated_for_full_text_removal(self):
        """Le masque doit être dilaté (style pinceau magique) : le texte est effacé
        entièrement, y compris ses bords anti-crénelés."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "lama_fp32.onnx"
            model.write_bytes(b"fake")
            img = Image.new("RGB", (512, 512), (240, 240, 245))
            fake_mod, fake_session_cls = _fake_onnxruntime_module(fill_value=200.0)
            region = [{"index": 0, "box": [100, 100, 50, 30], "text": "x"}]
            with mock.patch.dict(sys.modules, {"onnxruntime": fake_mod}):
                LamaInpainter(model_path=model).inpaint_image(img, region)  # mask_dilate=2 par défaut
                mask = fake_session_cls.instances[-1].calls[0]["mask"][0, 0]
                # crop 256x256 en (0,0), échelle x2 -> rect 512 : [194,194]..[306,266]
                self.assertEqual(mask[230, 250], 1.0)  # centre de la boîte
                self.assertEqual(mask[192, 192], 1.0)  # dilaté de 2 px (hors rectangle strict)
                self.assertEqual(mask[190, 190], 0.0)  # au-delà de la dilatation
                LamaInpainter(model_path=model, mask_dilate=0).inpaint_image(img, region)
                mask0 = fake_session_cls.instances[-1].calls[0]["mask"][0, 0]
                self.assertEqual(mask0[230, 250], 1.0)
                self.assertEqual(mask0[192, 192], 0.0, "sans dilatation, le bord reste à 0")

    def test_inpaint_image_uses_poly_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "lama_fp32.onnx"
            model.write_bytes(b"fake")
            src = Path(tmp) / "p.png"
            self._make_page(src)
            fake_mod, fake_session_cls = _fake_onnxruntime_module(fill_value=200.0)
            with mock.patch.dict(sys.modules, {"onnxruntime": fake_mod}):
                inp = LamaInpainter(model_path=model)
                img = Image.open(src).convert("RGB")
                poly = [[60, 50], [240, 50], [240, 100], [60, 100]]
                regions = [{"index": 0, "box": [60, 50, 180, 50], "text": "x", "poly": poly}]
                inp.inpaint_image(img, regions)
            mask = fake_session_cls.instances[-1].calls[0]["mask"][0, 0]
            self.assertGreater(mask.sum(), 0)
            # le masque doit suivre le polygone : point intérieur masqué, extérieur non
            self.assertEqual(mask[128, 200], 1.0)   # (x=200, y=128) dans le poly [60..240]x[50..100]
            self.assertEqual(mask[LAMA_INPUT_SIZE // 2, LAMA_INPUT_SIZE // 2], 0.0)  # (256,256) hors poly


@unittest.skipUnless(HAS_PIL, "Pillow requis")
class TestTypesetWithInpainter(unittest.TestCase):
    def _make_image(self, path: Path, size=(300, 120)) -> None:
        img = Image.new("RGB", size, (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 290, 70], fill=(200, 220, 255))
        d.text((20, 20), "Hello world", fill=(0, 0, 0))
        img.save(path)

    def test_replace_texts_uses_inpainter_then_draws(self):
        from scalper.typeset import replace_texts

        calls = []

        class FakeInpainter:
            def inpaint_image(self, img, regions):
                calls.append([r["index"] for r in regions])
                d = ImageDraw.Draw(img)
                for r in regions:
                    x, y, w, h = r["box"]
                    d.rectangle([x, y, x + w, y + h], fill=(255, 255, 255))
                return img

        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "p.png", Path(tmp) / "out.png"
            self._make_image(src)
            regions = [
                {"index": 0, "box": [10, 10, 280, 60], "text": "Hello world"},
                {"index": 1, "box": [5, 5, 4, 4], "text": "x"},  # trop petit -> ignoré
            ]
            replace_texts(src, regions, ["Bonjour le monde", "y"], out, inpainter=FakeInpainter())
            self.assertEqual(calls, [[0]], "seules les régions valides sont inpaintées")
            res = Image.open(out).convert("RGB")
            dark = any(res.getpixel((px, py))[0] < 100 for px in range(10, 290) for py in range(10, 70))
            self.assertTrue(dark, "la traduction doit être redessinée dans la boîte")

    def test_typeset_chapter_forwards_inpainter(self):
        from scalper.typeset import typeset_chapter

        seen = []

        class FakeInpainter:
            def inpaint_image(self, img, regions):
                seen.append(len(regions))
                return img

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "images").mkdir()
            self._make_image(d / "images" / "p1.png")
            (d / "ocr.json").write_text(
                json.dumps({"lang": "eng", "regions": [{"index": 0, "image": "p1.png", "box": [10, 10, 280, 60], "text": "Hello world"}]}),
                encoding="utf-8",
            )
            outputs = typeset_chapter(d, "fr", translations=["Bonjour le monde"], inpainter=FakeInpainter())
            self.assertEqual(len(outputs), 1)
            self.assertEqual(seen, [1])
            self.assertTrue((d / "export" / "images-fr" / "p1.png").exists())


@unittest.skipUnless(HAS_PIL and HAS_NUMPY, "Pillow + numpy requis")
class TestLamaRealModel(unittest.TestCase):
    """Test réel (optionnel) : lance le vrai big-lama ONNX si SCALPER_TEST_LAMA_MODEL pointe dessus."""

    def test_real_model_inpaints_text(self):
        model = os.environ.get("SCALPER_TEST_LAMA_MODEL")
        if not model or not Path(model).exists():
            self.skipTest("SCALPER_TEST_LAMA_MODEL non renseigné")
        from scalper.inpaint import LamaInpainter

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "p.png"
            img = Image.new("RGB", (512, 512), (235, 235, 240))
            d = ImageDraw.Draw(img)
            d.text((50, 50), "Texte a supprimer", fill=(0, 0, 0))
            img.save(src)
            inp = LamaInpainter(model_path=model)
            out = inp.inpaint_image(Image.open(src).convert("RGB"),
                                    [{"index": 0, "box": [40, 40, 300, 40], "text": "Texte a supprimer"}])
            arr = np.asarray(out)
            before = np.asarray(Image.open(src).convert("RGB"))
            zone = arr[40:80, 40:340]
            self.assertGreater(float(zone.mean()), 150.0, "le texte noir doit être remplacé par du fond clair")
            self.assertEqual(tuple(arr[400, 400]), tuple(before[400, 400]), "hors masque : inchangé")


if __name__ == "__main__":
    unittest.main()
