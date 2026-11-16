import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scalper.server import Handler


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, json.loads(r.read())

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_healthz(self):
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_web_ui_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            body = r.read().decode("utf-8")
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers.get("Content-Type", ""))
            self.assertIn("Scalper", body)
            self.assertIn("/run", body)

    def test_run_demo_mock_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, body = self._post("/run", {"source": "demo", "series": "demo-1", "lang": "fr", "mock_llm": True, "out": tmp})
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "done")
            self.assertEqual(len(body["chapters"]), 2)

    def test_run_missing_field_400(self):
        status, body = self._post("/run", {"source": "demo"})
        self.assertEqual(status, 400)

    def test_run_background_job_with_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, body = self._post("/run", {"source": "demo", "series": "demo-1", "lang": "fr", "mock_llm": True, "out": tmp, "background": True})
            self.assertEqual(status, 202)
            job = body["job"]
            final = None
            for _ in range(50):
                s, j = self._get(f"/jobs/{job}")
                self.assertEqual(s, 200)
                if j["status"] != "running":
                    final = j
                    break
                time.sleep(0.2)
            self.assertIsNotNone(final, "job toujours running après 10s")
            self.assertEqual(final["status"], "done")
            self.assertEqual(final["result"]["status"], "done")
            self.assertEqual(final["progress"]["total"], 2)
            self.assertEqual(final["progress"]["done"], 2)

    def test_api_chapter_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post("/run", {"source": "demo", "series": "demo-1", "lang": "fr", "mock_llm": True, "out": tmp, "max_chapters": 1})
            q = urllib.parse.urlencode({"out": tmp, "source": "demo", "series": "demo-1", "chapter": "demo-1-ch1", "lang": "fr"})
            status, data = self._get(f"/api/chapter?{q}")
            self.assertEqual(status, 200)
            self.assertTrue(data["source"]["blocks"])
            self.assertEqual(data["translated"]["meta"]["language"], "fr")
            self.assertEqual(Path(data["dir"]).as_posix(), "raw/demo/demo-1/demo-1-ch1")
            md = next((Path(tmp) / "raw" / "demo" / "demo-1" / "demo-1-ch1" / "export").glob("*.md"))
            rel = md.relative_to(tmp).as_posix()
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/file?out={urllib.parse.quote(tmp)}&path={urllib.parse.quote(rel)}") as r:
                self.assertEqual(r.status, 200)
                self.assertIn("markdown", r.headers.get("Content-Type", ""))
                self.assertTrue(r.read())
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(f"/file?out={urllib.parse.quote(tmp)}&path=../pyproject.toml")
            self.assertEqual(cm.exception.code, 404)

    def test_run_force_bypasses_qa_gate(self):
        fake = {"score": 10, "pass": False, "issues": [{"type": "x", "critical": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scalper.pipeline.qa_chapter", return_value=fake):
                status, body = self._post("/run", {"source": "demo", "series": "demo-1", "lang": "fr", "mock_llm": True, "out": tmp, "max_chapters": 1, "force": True})
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "done")
            self.assertEqual(body["chapters"][0]["status"], "ok")
            self.assertTrue(body["chapters"][0].get("forced"))


if __name__ == "__main__":
    unittest.main()
