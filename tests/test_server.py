import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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

    def test_check_source_demo_allowed(self):
        status, body = self._get("/check-source?source=demo")
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "allowed")

    def test_check_source_blocked(self):
        status, body = self._get("/check-source?source=https://mangas-origines.fr")
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "blocked")

    def test_run_demo_mock_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, body = self._post("/run", {"source": "demo", "series": "demo-1", "lang": "fr", "mock_llm": True, "out": tmp})
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "done")
            self.assertEqual(len(body["chapters"]), 2)

    def test_run_blocked_source_returns_403(self):
        status, body = self._post("/run", {"source": "https://keiyoushi.github.io", "series": "x", "lang": "fr", "mock_llm": True})
        self.assertEqual(status, 403)
        self.assertEqual(body["status"], "blocked")

    def test_run_missing_field_400(self):
        status, body = self._post("/run", {"source": "demo"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
