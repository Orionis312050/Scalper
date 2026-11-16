"""Serveur HTTP léger (stdlib uniquement) pour déployer Scalper (ex. Coolify).

Endpoints :
- GET  /healthz
- GET  /check-source?source=<url|domaine>
- POST /run  {"source","series","lang","group"?,"max_chapters"?,"mock_llm"?,"out"?}

Toutes les requêtes passent par legal-source-check : une source bloquée
(mangas-origines, keiyoushi.github.io, domaine non déclaré) n'est jamais traitée.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import legal, pipeline


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence les logs par requête
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._json(200, {"status": "ok"})
        if u.path == "/check-source":
            source = parse_qs(u.query).get("source", [""])[0]
            if not source:
                return self._json(400, {"error": "paramètre 'source' requis"})
            return self._json(200, legal.check_source(source).to_dict())
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path != "/run":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            summary = pipeline.run_pipeline(
                body["source"],
                body["series"],
                body["lang"],
                out_root=body.get("out", "out"),
                mock_llm=bool(body.get("mock_llm", False)),
                max_chapters=body.get("max_chapters"),
                group=body.get("group"),
            )
        except KeyError as e:
            return self._json(400, {"error": f"champ requis manquant: {e}"})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        code = 200 if summary.get("status") == "done" else 403
        return self._json(code, summary)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    cfg = os.environ.get("SCALPER_CONFIG")
    if cfg:
        if Path(cfg).exists():
            legal.load_config(cfg)
            print(f"registre de sources chargé: {cfg}")
        else:
            print(f"attention: SCALPER_CONFIG={cfg} introuvable, ignoré")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"scalper serve sur http://{host}:{port} (GET /healthz, GET /check-source, POST /run)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
