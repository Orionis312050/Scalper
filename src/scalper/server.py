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

INDEX_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scalper</title>
<style>body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#222}input,button{padding:.45rem;margin:.2rem}input{width:100%;box-sizing:border-box}fieldset{border:1px solid #ccc;border-radius:8px;margin-bottom:1rem}legend{font-weight:600}button{cursor:pointer;background:#222;color:#fff;border:0;border-radius:6px}pre{background:#f4f4f4;padding:1rem;border-radius:8px;overflow:auto;white-space:pre-wrap}.warn{color:#a00;font-size:.85rem}</style>
</head><body>
<h1>Scalper</h1>
<p>Pipeline légal de scraping + traduction de chapitres. <span class="warn">Sources non déclarées ou bloquées (mangas-origines, keiyoushi.github.io) refusées.</span></p>
<fieldset><legend>1. Vérifier une source</legend>
<input id="cs-source" placeholder="https://... ou demo ou mangadex"><br>
<button onclick="checkSource()">check-source</button>
</fieldset>
<fieldset><legend>2. Lancer un pipeline (check → scrape → clean → translate → QA → export)</legend>
<input id="r-source" placeholder="source (demo, mangadex)" value="demo">
<input id="r-series" placeholder="id série" value="demo-1">
<input id="r-lang" placeholder="langue cible (fr, en, ...)" value="fr">
<input id="r-group" placeholder="groupe optionnel (ex. Keiyoushi, mangadex uniquement)">
<input id="r-max" placeholder="max chapitres (optionnel)">
<label><input type="checkbox" id="r-mock" style="width:auto"> mock-llm (démo hors ligne)</label><br>
<button onclick="runPipeline()">Lancer le pipeline</button>
</fieldset>
<pre id="out">résultats ici…</pre>
<script>
async function checkSource(){const s=document.getElementById('cs-source').value;const r=await fetch('/check-source?source='+encodeURIComponent(s));document.getElementById('out').textContent=r.status+'\\n'+JSON.stringify(await r.json(),null,2);}
async function runPipeline(){const g=document.getElementById('r-group').value;const m=document.getElementById('r-max').value;const body={source:document.getElementById('r-source').value,series:document.getElementById('r-series').value,lang:document.getElementById('r-lang').value,mock_llm:document.getElementById('r-mock').checked};if(g)body.group=g;if(m)body.max_chapters=Number(m);document.getElementById('out').textContent='en cours…';const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});document.getElementById('out').textContent=r.status+'\\n'+JSON.stringify(await r.json(),null,2);}
</script>
</body></html>
"""


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

    def _html(self, code: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/":
            return self._html(200, INDEX_HTML)
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
