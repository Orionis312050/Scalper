"""Serveur HTTP léger (stdlib uniquement) pour déployer Scalper (ex. Coolify).

Endpoints :
- GET  /healthz
- GET  /            interface web (barre de progression + previews source/traduit)
- POST /run  {"source","series","lang","group"?,"max_chapters"?,"mock_llm"?,"with_ocr"?,"force"?,"out"?,"background"?}
             avec "background": true -> 202 {"job": id} et le run tourne en thread
- GET  /jobs/<id>  état du job {status, progress{done,total,current}, result, error}
- GET  /api/chapter?out=&source=&series=&chapter=&lang=  données de preview (meta, blocs source/traduits, images)
- GET  /file?out=&path=  sert un fichier sous le répertoire `out` (anti path-traversal)
"""

from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import pipeline

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _start_job(body: dict) -> str:
    """Lance run_pipeline dans un thread et retourne l'id du job."""
    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "progress": {"done": 0, "total": 0, "current": None}, "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[job_id] = job

    def work() -> None:
        try:
            def on_progress(done: int, total: int, ref) -> None:
                job["progress"] = {"done": done, "total": total, "current": ref.id}

            summary = pipeline.run_pipeline(
                body["source"], body["series"], body["lang"],
                out_root=body.get("out", "out"),
                mock_llm=bool(body.get("mock_llm", False)),
                max_chapters=body.get("max_chapters"),
                group=body.get("group"),
                with_ocr=bool(body.get("with_ocr", True)),
                force=bool(body.get("force", False)),
                on_progress=on_progress,
            )
            job["result"] = summary
            job["status"] = "done"
        except Exception as e:
            job["error"] = str(e)
            job["status"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return job_id


def _safe_out_path(out: str, rel: str) -> Path | None:
    """Résout `rel` sous le répertoire `out` ; None si hors de `out` (path traversal)."""
    base = Path(out).resolve()
    p = (base / rel).resolve()
    if p == base or base in p.parents:
        return p
    return None

INDEX_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scalper</title>
<style>
body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#222}
input,button{padding:.45rem;margin:.2rem}input{width:100%;box-sizing:border-box}
fieldset{border:1px solid #ccc;border-radius:8px;margin-bottom:1rem}legend{font-weight:600}
button{cursor:pointer;background:#222;color:#fff;border:0;border-radius:6px}
pre{background:#f4f4f4;padding:1rem;border-radius:8px;overflow:auto;white-space:pre-wrap}
#progress-wrap{display:none;height:1.2rem;background:#e5e5e5;border-radius:6px;overflow:hidden;margin:.6rem 0}
#bar{height:100%;width:0%;background:#2a7d4f;transition:width .3s}
#bar-label{font-size:.8rem;color:#555;margin-bottom:1rem}
.chapter{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}
.cols{display:flex;gap:1rem;flex-wrap:wrap}
.col{flex:1;min-width:16rem}
.col img{max-width:100%;display:block;margin:.3rem 0;border:1px solid #eee}
blockquote{margin:.3rem 0;padding:.2rem .6rem;border-left:3px solid #888;color:#333}
</style>
</head><body>
<h1>Scalper</h1>
<p>Pipeline de scraping + traduction de chapitres.</p>
<fieldset><legend>Lancer un pipeline (scrape → clean → translate → QA → export)</legend>
<input id="r-source" placeholder="source (demo, mangadex)" value="demo">
<input id="r-series" placeholder="id série" value="demo-1">
<input id="r-lang" placeholder="langue cible (fr, en, ...)" value="fr">
<input id="r-group" placeholder="groupe optionnel (mangadex uniquement)">
<input id="r-max" placeholder="max chapitres (optionnel)">
<label><input type="checkbox" id="r-mock" style="width:auto"> mock-llm (démo hors ligne)</label>
<label><input type="checkbox" id="r-force" style="width:auto"> forcer l'export même si la QA échoue</label>
<label><input type="checkbox" id="r-ocr" checked style="width:auto"> OCR + remplacement du texte dans les images (auto)</label><br>
<button onclick="runPipeline()">Lancer le pipeline</button>
</fieldset>
<div id="progress-wrap"><div id="bar"></div></div>
<div id="bar-label"></div>
<pre id="out">résultats ici…</pre>
<div id="previews"></div>
<script>
const enc = encodeURIComponent;
async function runPipeline(){
  const g=document.getElementById('r-group').value, m=document.getElementById('r-max').value;
  const body={source:document.getElementById('r-source').value, series:document.getElementById('r-series').value, lang:document.getElementById('r-lang').value, mock_llm:document.getElementById('r-mock').checked, force:document.getElementById('r-force').checked, with_ocr:document.getElementById('r-ocr').checked, background:true};
  if(g) body.group=g; if(m) body.max_chapters=Number(m);
  document.getElementById('out').textContent='en cours…';
  document.getElementById('previews').innerHTML='';
  const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!j.job){ document.getElementById('out').textContent=r.status+' '+JSON.stringify(j,null,2); return; }
  poll(j.job, body.out||'out');
}
function poll(jobId,out){
  const wrap=document.getElementById('progress-wrap'), bar=document.getElementById('bar'), label=document.getElementById('bar-label');
  wrap.style.display='block'; bar.style.width='0%';
  const t=setInterval(async()=>{
    const r=await fetch('/jobs/'+jobId); const j=await r.json();
    const p=j.progress||{done:0,total:0,current:''};
    const pct=p.total?Math.round(100*p.done/Math.max(1,p.total)):0;
    bar.style.width=pct+'%';
    label.textContent=pct+'% — chapitre '+p.done+'/'+p.total+(p.current?' ('+p.current+')':'');
    if(j.status!=='running'){ clearInterval(t); setTimeout(()=>{wrap.style.display='none';},1500); render(j,out); }
  },800);
}
async function render(j,out){
  const res=j.result||{};
  document.getElementById('out').textContent=(j.status==='error'?'ERREUR: '+j.error:j.status+' '+JSON.stringify(res,null,2));
  const prev=document.getElementById('previews'); prev.innerHTML='';
  for(const ch of (res.chapters||[])){
    if(!ch.chapter||ch.skipped) continue;
    const q='/api/chapter?out='+enc(out)+'&source='+enc(res.source||'')+'&series='+enc(res.series||'')+'&chapter='+enc(ch.chapter.id)+'&lang='+enc(res.lang||'');
    const r=await fetch(q); if(!r.ok) continue;
    prev.appendChild(chapterPanel(await r.json(), out, res.lang||''));
  }
}
function blockEl(b,dir,out,imgSub){
  if(b.type==='image'){
    const img=document.createElement('img');
    const name=(b.image||'').split('/').pop();
    img.src='/file?out='+enc(out)+'&path='+enc(dir+'/'+imgSub+'/'+name);
    img.alt=b.placeholder||'image';
    return img;
  }
  const p=document.createElement(b.type==='dialogue'?'blockquote':'p');
  p.textContent=b.text||'';
  return p;
}
function chapterPanel(data,out,lang){
  const panel=document.createElement('div'); panel.className='chapter';
  const h=document.createElement('h3'); h.textContent='Chapitre '+((data.meta&&data.meta.number)||'')+' — '+(data.dir||''); panel.appendChild(h);
  const cols=document.createElement('div'); cols.className='cols';
  const c1=document.createElement('div'); c1.className='col';
  const h1=document.createElement('h4'); h1.textContent='Récupéré (source)'; c1.appendChild(h1);
  if(data.source) for(const b of data.source.blocks) c1.appendChild(blockEl(b,data.dir,out,'images'));
  else c1.appendChild(document.createTextNode('(pas de chapter.json)'));
  const c2=document.createElement('div'); c2.className='col';
  const h2=document.createElement('h4'); h2.textContent='Traduit complet ('+lang+')'; c2.appendChild(h2);
  const trImg=(data.images_translated&&data.images_translated.length)?('export/images-'+lang):'images';
  if(data.translated) for(const b of data.translated.blocks) c2.appendChild(blockEl(b,data.dir,out,trImg));
  else c2.appendChild(document.createTextNode('(pas encore traduit)'));
  cols.appendChild(c1); cols.appendChild(c2); panel.appendChild(cols);
  return panel;
}
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
        if u.path.startswith("/jobs/"):
            job_id = u.path[len("/jobs/"):]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                return self._json(404, {"error": "job introuvable"})
            return self._json(200, {"status": job["status"], "progress": job["progress"], "result": job["result"], "error": job["error"]})
        if u.path == "/api/chapter":
            q = parse_qs(u.query)
            out = q.get("out", ["out"])[0]
            source = q.get("source", [""])[0]
            series = q.get("series", [""])[0]
            chapter = q.get("chapter", [""])[0]
            lang = q.get("lang", [""])[0]
            d = _safe_out_path(out, f"raw/{source}/{series}/{chapter}")
            if d is None or not d.is_dir():
                return self._json(404, {"error": "chapitre introuvable"})
            base = Path(out).resolve()
            data: dict = {"dir": str(d.relative_to(base)), "meta": {}, "source": None, "translated": None, "images": [], "images_translated": []}
            for name, key in (("meta.json", "meta"), ("chapter.json", "source")):
                f = d / name
                if f.exists():
                    data[key] = json.loads(f.read_text(encoding="utf-8"))
            if lang:
                f = d / f"chapter.{lang}.json"
                if f.exists():
                    data["translated"] = json.loads(f.read_text(encoding="utf-8"))
            img_dir = d / "images"
            if img_dir.is_dir():
                data["images"] = sorted(p.name for p in img_dir.iterdir() if p.is_file())
            itr = d / "export" / f"images-{lang}"
            if lang and itr.is_dir():
                data["images_translated"] = sorted(p.name for p in itr.iterdir() if p.is_file())
            return self._json(200, data)
        if u.path == "/file":
            q = parse_qs(u.query)
            out = q.get("out", ["out"])[0]
            p = _safe_out_path(out, q.get("path", [""])[0])
            if p is None or not p.is_file():
                return self._json(404, {"error": "fichier introuvable"})
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path != "/run":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("background"):
                job_id = _start_job(body)
                return self._json(202, {"job": job_id, "status": "running"})
            summary = pipeline.run_pipeline(
                body["source"],
                body["series"],
                body["lang"],
                out_root=body.get("out", "out"),
                mock_llm=bool(body.get("mock_llm", False)),
                max_chapters=body.get("max_chapters"),
                group=body.get("group"),
                with_ocr=bool(body.get("with_ocr", True)),
                force=bool(body.get("force", False)),
            )
        except KeyError as e:
            return self._json(400, {"error": f"champ requis manquant: {e}"})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        code = 200 if summary.get("status") == "done" else 403
        return self._json(code, summary)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"scalper serve sur http://{host}:{port} (GET /healthz, GET /jobs/<id>, GET /api/chapter, GET /file, POST /run)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
