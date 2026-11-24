"""Serveur HTTP léger (stdlib uniquement) pour déployer Scalper (ex. Coolify).

Endpoints :
- GET  /healthz
- GET  /            interface web (barre de progression + previews source/traduit)
- POST /run  {"source","series","lang","group"?,"max_chapters"?,"mock_llm"?,"with_ocr"?,"ocr_engine"?,"inpaint_engine"?,"zip_export"?,"zip_include_clean"?,"force"?,"out"?,"background"?}
             avec "background": true -> 202 {"job": id} et le run tourne en thread
- GET  /jobs/<id>  état du job {status, progress{done,total,current}, result, error}
- GET  /api/chapter?out=&source=&series=&chapter=&lang=  données de preview (meta, blocs source/traduits, images)
- GET  /file?out=&path=  sert un fichier sous le répertoire `out` (anti path-traversal)
- POST /login {username, password} -> {"token": ...} : session 8 h, à passer en header
  "Authorization: Bearer <token>" sur tous les endpoints (sauf /, /healthz, /login, /setup).
- GET|POST /setup?token=...  création du compte AU PREMIER DÉMARRAGE uniquement : si aucun
  compte n'existe, un lien à usage unique est affiché dans les logs ; il est invalidé dès le
  compte créé. Fichier compte : SCALPER_AUTH_FILE (défaut data/auth.json), hash PBKDF2.
"""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import pipeline

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# Étapes du pipeline affichées dans la barre de progression de l'UI (clé -> libellé)
STEP_LABELS = {
    "fetch": "Téléchargement des images",
    "ocr": "Récupération des textes (OCR)",
    "translate": "Traduction des textes",
    "inpaint": "Effacement des bulles de texte sur les images (LaMa)",
    "typeset": "Mise en place du texte traduit sur les images cleaned",
    "zip": "Compression du rendu (zip)",
    "clean": "Suppression du reste de out/",
}
STEP_ORDER = list(STEP_LABELS)

AUTH_FILE = Path(os.environ.get("SCALPER_AUTH_FILE", "data/auth.json"))
SETUP_TOKEN: str | None = None
SESSIONS: dict[str, float] = {}
SESSION_TTL = 8 * 3600


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()


def _load_account() -> dict | None:
    if not AUTH_FILE.exists():
        return None
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def init_auth(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Premier démarrage : génère un lien de création de compte à usage unique, affiché dans les logs."""
    global SETUP_TOKEN
    if _load_account() is None and SETUP_TOKEN is None:
        SETUP_TOKEN = secrets.token_urlsafe(24)
        display = host if host not in ("0.0.0.0", "::", "") else "<hote-du-serveur>"
        print("[scalper] aucun compte trouvé (premier démarrage).")
        print(f"[scalper] lien de création de compte (usage unique) : http://{display}:{port}/setup?token={SETUP_TOKEN}")
        if display == "<hote-du-serveur>":
            print(f"[scalper] remplacez <hote-du-serveur> par l'IP ou le nom d'hôte réel (port {port} publié par docker-compose).")
        print("[scalper] ce lien est invalidé dès que le compte est créé.")


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL
    return token


def _authorized(headers) -> bool:
    if _load_account() is None:
        return False
    header = headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    exp = SESSIONS.get(header[len("Bearer "):].strip())
    return exp is not None and exp > time.time()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _run_kwargs(body: dict, on_progress=None, on_step=None):
    """(source, series, lang) + kwargs pour run_pipeline : champs du body, sinon défauts
    env (SCALPER_DEFAULT_SOURCE / _SERIES / _LANG / _SRC_LANG, SCALPER_ZIP_EXPORT,
    SCALPER_ZIP_INCLUDE_CLEAN). source/series/lang restent requis (body ou env)."""
    def field(key: str, env: str):
        v = body.get(key)
        if v in (None, ""):
            v = os.environ.get(env)
        return v

    zip_export = body.get("zip_export")
    if zip_export is None:
        zip_export = _env_bool("SCALPER_ZIP_EXPORT", False)
    zip_clean = body.get("zip_include_clean")
    if zip_clean is None:
        zip_clean = _env_bool("SCALPER_ZIP_INCLUDE_CLEAN", False)
    kw = dict(
        out_root=body.get("out", "out"),
        mock_llm=bool(body.get("mock_llm", False)),
        max_chapters=body.get("max_chapters"),
        group=body.get("group"),
        with_ocr=bool(body.get("with_ocr", True)),
        force=bool(body.get("force", False)),
        src_lang=field("src_lang", "SCALPER_DEFAULT_SRC_LANG"),
        inpaint_engine=body.get("inpaint_engine"),
        ocr_engine=body.get("ocr_engine"),
        zip_export=bool(zip_export),
        zip_include_clean=bool(zip_clean),
    )
    if on_progress is not None:
        kw["on_progress"] = on_progress
    if on_step is not None:
        kw["on_step"] = on_step
    source = field("source", "SCALPER_DEFAULT_SOURCE")
    series = field("series", "SCALPER_DEFAULT_SERIES")
    lang = field("lang", "SCALPER_DEFAULT_LANG")
    if not source:
        raise KeyError("source")
    if not series:
        raise KeyError("series")
    if not lang:
        raise KeyError("lang")
    return (source, series, lang), kw



def _start_job(body: dict) -> str:
    """Lance run_pipeline dans un thread et retourne l'id du job."""
    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "progress": {"done": 0, "total": 0, "current": None, "step": None}, "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[job_id] = job

    def work() -> None:
        try:
            def on_progress(done: int, total: int, ref) -> None:
                job["progress"].update({"done": done, "total": total, "current": ref.id})

            def on_step(step: str) -> None:
                job["progress"].update({"step": step})

            pos, kw = _run_kwargs(body, on_progress=on_progress, on_step=on_step)
            summary = pipeline.run_pipeline(*pos, **kw)
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
@property --a{syntax:'<angle>';initial-value:0deg;inherits:false}
:root{--bg:#030303;--card:rgba(255,255,255,.04);--border:rgba(255,255,255,.1);--txt:#e4e4e7;--muted:#a1a1aa;--indigo:#6366f1;--fuchsia:#d946ef;--cyan:#22d3ee}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:100vh}
/* fond façon Aceternity : grille + aurora dégradée */
.bg-grid{position:fixed;inset:0;z-index:-2;background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse 90% 65% at 50% 0%,#000 55%,transparent 100%);mask-image:radial-gradient(ellipse 90% 65% at 50% 0%,#000 55%,transparent 100%)}
.bg-aurora{position:fixed;inset:0;z-index:-3;overflow:hidden;background:var(--bg)}
.bg-aurora::before,.bg-aurora::after{content:"";position:absolute;width:55vmax;height:55vmax;border-radius:50%;filter:blur(130px);opacity:.22;animation:drift 28s ease-in-out infinite alternate}
.bg-aurora::before{background:radial-gradient(circle,#6366f1,transparent 62%);top:-22vmax;left:-12vmax}
.bg-aurora::after{background:radial-gradient(circle,#d946ef,transparent 62%);bottom:-26vmax;right:-14vmax;animation-delay:-14s}
@keyframes drift{from{transform:translate(0,0) scale(1)}to{transform:translate(7vmax,4vmax) scale(1.18)}}
.wrap{max-width:62rem;margin:0 auto;padding:2rem 1rem 4rem}
header.top{text-align:center;margin-bottom:2rem}
h1.logo{margin:0;font-size:2.4rem;font-weight:800;letter-spacing:-.03em;background:linear-gradient(90deg,#fff,#a5b4fc,#e879f9,#67e8f9,#fff);background-size:300% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shine 9s linear infinite}
@keyframes shine{to{background-position:300% 0}}
header.top p{margin:.4rem 0 0;color:var(--muted);font-size:.9rem}
/* carte + bord dégradé animé (style Aceternity moving-border) */
.gborder{border-radius:1.1rem;padding:1px;background:conic-gradient(from var(--a),#6366f1,#d946ef,#22d3ee,#6366f1);animation:spin 5s linear infinite;box-shadow:0 0 40px rgba(99,102,241,.15)}
@keyframes spin{to{--a:360deg}}
.card{border:1px solid var(--border);border-radius:1rem;background:var(--card);backdrop-filter:blur(10px);padding:1.2rem 1.4rem}
.gborder .card{border:0;background:rgba(10,10,10,.92);border-radius:1rem}
#login{display:none;max-width:24rem;margin:5rem auto 0}
#login h2{margin:.2rem 0 1rem;font-size:1.2rem}
#l-err{color:#f87171;font-size:.85rem;min-height:1.2em;margin-top:.5rem}
label.f{display:block;font-size:.78rem;color:var(--muted);margin:.6rem 0 .25rem}
input[type=text],input:not([type]),select{width:100%;padding:.55rem .75rem;border-radius:.6rem;border:1px solid var(--border);background:rgba(0,0,0,.55);color:var(--txt);font-size:.9rem;outline:none;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus{border-color:var(--indigo);box-shadow:0 0 0 3px rgba(99,102,241,.25)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 1rem}
@media(max-width:40rem){.grid2{grid-template-columns:1fr}}
.switch{display:inline-flex;align-items:center;gap:.55rem;cursor:pointer;font-size:.84rem;color:var(--muted);margin:.35rem 1.2rem .35rem 0;user-select:none}
.switch input{position:absolute;opacity:0;width:0;height:0}
.switch .track{flex:none;width:36px;height:20px;border-radius:999px;background:rgba(255,255,255,.12);border:1px solid var(--border);position:relative;transition:.2s}
.switch .track::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:.2s}
.switch input:checked + .track{background:linear-gradient(90deg,var(--indigo),var(--fuchsia));border-color:transparent;box-shadow:0 0 10px rgba(217,70,239,.5)}
.switch input:checked + .track::after{left:18px}
.btn{border:0;cursor:pointer;border-radius:.65rem;padding:.65rem 1.3rem;font-weight:700;font-size:.92rem;color:#fff;background:linear-gradient(90deg,var(--indigo),var(--fuchsia));box-shadow:0 0 26px rgba(99,102,241,.45);transition:transform .15s,box-shadow .2s;margin-top:1rem}
.btn:hover{transform:translateY(-1px);box-shadow:0 0 38px rgba(217,70,239,.55)}
.btn:active{transform:translateY(0)}
#progress-wrap{display:none;height:.85rem;border-radius:999px;background:rgba(255,255,255,.08);border:1px solid var(--border);overflow:hidden;margin:1rem 0 .35rem}
#bar{height:100%;width:0%;border-radius:999px;background:linear-gradient(90deg,var(--indigo),var(--fuchsia),var(--cyan));box-shadow:0 0 14px rgba(99,102,241,.9);transition:width .3s}
#bar-label{font-size:.8rem;color:var(--muted);margin-bottom:1rem;min-height:1.1em}
.steps{display:flex;gap:.4rem;flex-wrap:wrap;margin:.9rem 0 .4rem}
.step{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .65rem;border-radius:999px;border:1px solid var(--border);font-size:.72rem;color:var(--muted);background:rgba(255,255,255,.03);transition:.2s}
.step .dot{width:.55rem;height:.55rem;border-radius:50%;background:rgba(255,255,255,.18);flex:none}
.step.active{color:#fff;border-color:transparent;background:linear-gradient(90deg,rgba(99,102,241,.3),rgba(217,70,239,.3));box-shadow:0 0 16px rgba(99,102,241,.4)}
.step.active .dot{background:linear-gradient(90deg,var(--indigo),var(--fuchsia));box-shadow:0 0 8px var(--fuchsia);animation:pulse 1s ease-in-out infinite alternate}
.step.done{color:#a7f3d0;border-color:rgba(52,211,153,.35)}
.step.done .dot{background:#34d399}
@keyframes pulse{from{transform:scale(1)}to{transform:scale(1.35)}}
pre#out{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.78rem;line-height:1.45;background:rgba(0,0,0,.65);border:1px solid var(--border);border-radius:.9rem;padding:1rem;overflow:auto;white-space:pre-wrap;max-height:26rem;margin:0}
#previews{margin-top:1.4rem}
.chapter{border:1px solid var(--border);border-radius:1rem;background:var(--card);backdrop-filter:blur(8px);padding:1.1rem 1.2rem;margin:1rem 0;transition:border-color .2s}
.chapter:hover{border-color:rgba(255,255,255,.25)}
.chapter h3{margin:.1rem 0 .6rem;font-size:1.02rem}
.chapter .lang{color:var(--muted);font-size:.8rem;margin:.2rem 0}
.cols{display:flex;gap:1.1rem;flex-wrap:wrap}
.col{flex:1;min-width:16rem}
.col h4{margin:.2rem 0 .5rem;font-size:.85rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.col img{max-width:100%;display:block;margin:.35rem 0;border-radius:.6rem;border:1px solid var(--border)}
blockquote{margin:.3rem 0;padding:.25rem .7rem;border-left:3px solid var(--indigo);color:#d4d4d8;background:rgba(255,255,255,.03);border-radius:0 .4rem .4rem 0}
.col p{margin:.3rem 0;color:#d4d4d8}
</style>
</head><body>
<div class="bg-aurora"></div><div class="bg-grid"></div>
<div id="login"><div class="gborder"><div class="card">
<h2>Connexion</h2>
<label class="f" for="l-user">identifiant</label>
<input id="l-user" type="text" placeholder="identifiant" autocomplete="username">
<label class="f" for="l-pass">mot de passe</label>
<input id="l-pass" type="password" placeholder="mot de passe" autocomplete="current-password">
<button class="btn" onclick="doLogin()">Se connecter</button>
<div id="l-err"></div>
</div></div></div>
<div id="app" class="wrap">
<header class="top"><h1 class="logo">Scalper</h1><p>Pipeline de scraping + traduction de chapitres manga/webtoon</p></header>
<div class="card">
<h3 style="margin:.1rem 0 .4rem">Lancer un pipeline <span style="color:var(--muted);font-weight:400;font-size:.8rem">scrape → clean → translate → QA → OCR/inpainting → export</span></h3>
<div class="grid2">
<div><label class="f" for="r-source">source (demo, mangadex)</label><input id="r-source" type="text" value="__DEFAULT_SOURCE__"></div>
<div><label class="f" for="r-series">id série</label><input id="r-series" type="text" value="__DEFAULT_SERIES__"></div>
<div><label class="f" for="r-lang">langue cible (fr, en…)</label><input id="r-lang" type="text" value="__DEFAULT_LANG__"></div>
<div><label class="f" for="r-srclang">langue d'entrée du chapitre (en, ja, fr…)</label><input id="r-srclang" type="text" value="__DEFAULT_SRCLANG__"></div>
<div><label class="f" for="r-group">groupe optionnel (mangadex uniquement)</label><input id="r-group" type="text"></div>
<div><label class="f" for="r-max">max chapitres (optionnel)</label><input id="r-max" type="text"></div>
<div><label class="f" for="r-ocrengine">moteur OCR</label><select id="r-ocrengine"><option value="">par défaut (env)</option><option value="paddle">paddle (PP-OCRv6)</option><option value="tesseract">tesseract</option></select></div>
<div><label class="f" for="r-inpaint">moteur d'inpainting</label><select id="r-inpaint"><option value="">par défaut (env)</option><option value="lama">lama (big-lama)</option><option value="simple">simple</option><option value="auto">auto</option></select></div>
</div>
<div style="margin-top:.6rem">
<label class="switch"><input type="checkbox" id="r-mock"><span class="track"></span>mock-llm (démo hors ligne)</label>
<label class="switch"><input type="checkbox" id="r-force"><span class="track"></span>forcer l'export même si la QA échoue</label>
<label class="switch"><input type="checkbox" id="r-ocr" checked><span class="track"></span>OCR + remplacement du texte dans les images</label>
<label class="switch"><input type="checkbox" id="r-zip" __ZIP_ATTR__><span class="track"></span>zip export-&lt;date&gt;-&lt;heure&gt;-&lt;minutes&gt;.zip à la fin (images + PDF + metadata/, le reste de out/ est supprimé)</label>
<label class="switch"><input type="checkbox" id="r-zip-clean" __ZIPCLEAN_ATTR__><span class="track"></span>aussi embarquer cleaned/ dans le zip (images nettoyées avant retypographie — debug inpainting)</label>
</div>
<button class="btn" onclick="runPipeline()">Lancer le pipeline</button>
</div>
<div id="steps" class="steps"></div>
<div id="progress-wrap"><div id="bar"></div></div>
<div id="bar-label"></div>
<div class="card" style="margin-top:1rem"><pre id="out">résultats ici…</pre></div>
<div id="previews"></div>
</div>
<script>
const enc = encodeURIComponent;
  __DEFAULTS_JS__
let TOKEN = localStorage.getItem('scalper_token') || '';
function showLogin(){ document.getElementById('login').style.display='block'; document.getElementById('app').style.display='none'; }
function hideLogin(){ document.getElementById('login').style.display='none'; document.getElementById('app').style.display='block'; }
async function api(path, opts){ opts=opts||{}; opts.headers=Object.assign({'Content-Type':'application/json'}, opts.headers||{}); if(TOKEN) opts.headers['Authorization']='Bearer '+TOKEN; const r=await fetch(path,opts); if(r.status===401){ TOKEN=''; localStorage.removeItem('scalper_token'); showLogin(); } return r; }
async function doLogin(){ const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('l-user').value,password:document.getElementById('l-pass').value})}); const j=await r.json().catch(()=>({})); if(r.ok){ TOKEN=j.token; localStorage.setItem('scalper_token',TOKEN); hideLogin(); } else document.getElementById('l-err').textContent=j.error||('erreur '+r.status); }
if(TOKEN) hideLogin(); else showLogin();
async function runPipeline(){
  const g=document.getElementById('r-group').value, m=document.getElementById('r-max').value, sl=document.getElementById('r-srclang').value;
  const oe=document.getElementById('r-ocrengine').value, ie=document.getElementById('r-inpaint').value;
  const body={source:document.getElementById('r-source').value, series:document.getElementById('r-series').value, lang:document.getElementById('r-lang').value, mock_llm:document.getElementById('r-mock').checked, force:document.getElementById('r-force').checked, with_ocr:document.getElementById('r-ocr').checked, zip_export:document.getElementById('r-zip').checked, zip_include_clean:document.getElementById('r-zip-clean').checked, background:true};
  if(g) body.group=g; if(m) body.max_chapters=Number(m); if(sl) body.src_lang=sl; if(oe) body.ocr_engine=oe; if(ie) body.inpaint_engine=ie;
  document.getElementById('out').textContent='en cours…';
  document.getElementById('previews').innerHTML='';
  const r=await api('/run',{method:'POST',body:JSON.stringify(body)});
  const j=await r.json();
  if(!j.job){ document.getElementById('out').textContent=r.status+' '+JSON.stringify(j,null,2); return; }
  poll(j.job, body.out||'out');
}
const STEPS=[["fetch","Téléchargement des images"],["ocr","Récupération des textes (OCR)"],["translate","Traduction des textes"],["inpaint","Effacement des bulles (LaMa)"],["typeset","Texte traduit sur images cleaned"],["zip","Compression (zip)"],["clean","Suppression du reste"]];
function renderSteps(cur){
  const el=document.getElementById('steps'); if(!el) return; el.innerHTML='';
  const idx=STEPS.findIndex(s=>s[0]===cur);
  STEPS.forEach(([k,label],i)=>{
    const d=document.createElement('div');
    d.className='step'+(cur?(i<idx?' done':(i===idx?' active':'')):'');
    const dot=document.createElement('span'); dot.className='dot'; d.appendChild(dot);
    d.appendChild(document.createTextNode((cur&&i<idx?'✓ ':'')+label));
    el.appendChild(d);
  });
}
function poll(jobId,out){
  const wrap=document.getElementById('progress-wrap'), bar=document.getElementById('bar'), label=document.getElementById('bar-label');
  wrap.style.display='block'; bar.style.width='0%'; renderSteps(null);
  const t=setInterval(async ()=>{
    const r=await api('/jobs/'+jobId); const j=await r.json();
    const p=j.progress||{done:0,total:0,current:'',step:null};
    const pct=p.total?Math.round(100*p.done/Math.max(1,p.total)):0;
    bar.style.width=pct+'%';
    renderSteps(p.step||null);
    const sl=p.step?(((STEPS.find(s=>s[0]===p.step)||[])[1])||p.step)+' — ':'';
    label.textContent=sl+pct+'% — chapitre '+p.done+'/'+p.total+(p.current?' ('+p.current+')':'');
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
    const r=await api(q); if(!r.ok) continue;
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
  if(data.meta&&data.meta.language){ const lp=document.createElement('p'); lp.className='lang'; lp.textContent="Langue d'entrée : "+data.meta.language; panel.appendChild(lp); }
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



SETUP_HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scalper — premier démarrage</title>
<style>
@property --a{syntax:'<angle>';initial-value:0deg;inherits:false}
:root{--bg:#030303;--border:rgba(255,255,255,.1);--txt:#e4e4e7;--muted:#a1a1aa;--indigo:#6366f1;--fuchsia:#d946ef}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--txt);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center}
.bg-grid{position:fixed;inset:0;z-index:-2;background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse 90% 65% at 50% 0%,#000 55%,transparent 100%);mask-image:radial-gradient(ellipse 90% 65% at 50% 0%,#000 55%,transparent 100%)}
.bg-aurora{position:fixed;inset:0;z-index:-3;overflow:hidden;background:var(--bg)}
.bg-aurora::before{content:"";position:absolute;width:55vmax;height:55vmax;border-radius:50%;filter:blur(130px);opacity:.22;background:radial-gradient(circle,#6366f1,transparent 62%);top:-22vmax;left:-12vmax}
.gborder{max-width:24rem;width:calc(100% - 2rem);border-radius:1.1rem;padding:1px;background:conic-gradient(from var(--a),#6366f1,#d946ef,#22d3ee,#6366f1);animation:spin 5s linear infinite;box-shadow:0 0 40px rgba(99,102,241,.15)}
@keyframes spin{to{--a:360deg}}
.card{border-radius:1rem;background:rgba(10,10,10,.92);padding:1.4rem}
h1{margin:.1rem 0 .3rem;font-size:1.3rem;font-weight:800;background:linear-gradient(90deg,#fff,#a5b4fc,#e879f9);-webkit-background-clip:text;background-clip:text;color:transparent}
p{color:var(--muted);font-size:.85rem;margin:.2rem 0 1rem}
input{width:100%;padding:.55rem .75rem;margin:.35rem 0;border-radius:.6rem;border:1px solid var(--border);background:rgba(0,0,0,.55);color:var(--txt);font-size:.9rem;outline:none}
input:focus{border-color:var(--indigo);box-shadow:0 0 0 3px rgba(99,102,241,.25)}
button{width:100%;margin-top:.8rem;border:0;cursor:pointer;border-radius:.65rem;padding:.65rem;font-weight:700;color:#fff;background:linear-gradient(90deg,var(--indigo),var(--fuchsia));box-shadow:0 0 26px rgba(99,102,241,.45)}
#err{color:#f87171;font-size:.85rem;min-height:1.2em;margin-top:.5rem}
</style>
</head><body>
<div class="bg-aurora"></div><div class="bg-grid"></div>
<div class="gborder"><div class="card">
<h1>Scalper — création du compte</h1>
<p>Lien de premier démarrage valide.</p>
<input id="u" type="text" placeholder="identifiant" autocomplete="username">
<input id="p" type="password" placeholder="mot de passe (8 caractères min.)" autocomplete="new-password">
<button onclick="go()">Créer le compte</button>
<div id="err"></div>
</div></div>
<script>
async function go(){
  const r=await fetch('/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:new URLSearchParams(location.search).get('token'),username:document.getElementById('u').value,password:document.getElementById('p').value})});
  const j=await r.json();
  if(r.ok){ document.body.innerHTML='<div class="gborder"><div class="card"><h1>Compte créé</h1><p>Connectez-vous : <a style="color:#a5b4fc" href="/">/</a></p></div></div>'; }
  else document.getElementById('err').textContent=j.error||('erreur '+r.status);
}
</script></body></html>
"""


def _index_html() -> str:
    """INDEX_HTML avec les valeurs par défaut env pré-remplies (source, série, langues,
    moteurs, zip)."""
    def esc(v: str | None) -> str:
        return html.escape(v or "", quote=True)

    defaults_js = (
        "document.getElementById('r-ocrengine').value="
        + json.dumps(os.environ.get("SCALPER_DEFAULT_OCR_ENGINE") or "") + ";"
        + "document.getElementById('r-inpaint').value="
        + json.dumps(os.environ.get("SCALPER_DEFAULT_INPAINT_ENGINE") or "") + ";"
    )
    page = INDEX_HTML
    page = page.replace("__DEFAULT_SOURCE__", esc(os.environ.get("SCALPER_DEFAULT_SOURCE") or "demo"))
    page = page.replace("__DEFAULT_SERIES__", esc(os.environ.get("SCALPER_DEFAULT_SERIES") or "demo-1"))
    page = page.replace("__DEFAULT_LANG__", esc(os.environ.get("SCALPER_DEFAULT_LANG") or "fr"))
    page = page.replace("__DEFAULT_SRCLANG__", esc(os.environ.get("SCALPER_DEFAULT_SRC_LANG") or ""))
    page = page.replace("__ZIP_ATTR__", "checked" if _env_bool("SCALPER_ZIP_EXPORT", True) else "")
    page = page.replace("__ZIPCLEAN_ATTR__", "checked" if _env_bool("SCALPER_ZIP_INCLUDE_CLEAN", False) else "")
    page = page.replace("__DEFAULTS_JS__", defaults_js)
    return page


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

    def _setup_get(self, u) -> None:
        if _load_account() is not None:
            return self._json(403, {"error": "compte déjà créé : connectez-vous via /login"})
        token = parse_qs(u.query).get("token", [""])[0]
        if SETUP_TOKEN is None or not secrets.compare_digest(token, SETUP_TOKEN):
            return self._json(403, {"error": "lien de setup invalide ou expiré"})
        return self._html(200, SETUP_HTML)

    def _setup_post(self, body: dict) -> None:
        global SETUP_TOKEN
        if _load_account() is not None:
            return self._json(403, {"error": "compte déjà créé"})
        token = str(body.get("token", ""))
        if SETUP_TOKEN is None or not secrets.compare_digest(token, SETUP_TOKEN):
            return self._json(403, {"error": "lien de setup invalide ou expiré"})
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or len(password) < 8:
            return self._json(400, {"error": "identifiant requis, mot de passe >= 8 caractères"})
        salt = secrets.token_hex(16)
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text(json.dumps({"username": username, "salt": salt, "hash": _hash_password(password, salt)}, indent=2), encoding="utf-8")
        SETUP_TOKEN = None
        print("[scalper] compte créé : le lien de setup est invalidé. Connectez-vous via /login.")
        return self._json(200, {"status": "ok", "token": _new_session()})

    def _login(self, body: dict) -> None:
        account = _load_account()
        if account is None:
            return self._json(401, {"error": "aucun compte : utilisez le lien /setup affiché dans les logs au premier démarrage"})
        ok = str(body.get("username", "")) == account.get("username") and secrets.compare_digest(
            _hash_password(str(body.get("password", "")), account.get("salt", "")), account.get("hash", "")
        )
        if not ok:
            return self._json(401, {"error": "identifiants invalides"})
        return self._json(200, {"token": _new_session()})

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/":
            return self._html(200, _index_html())
        if u.path == "/healthz":
            return self._json(200, {"status": "ok"})
        if u.path == "/setup":
            return self._setup_get(u)
        if u.path != "/" and not _authorized(self.headers):
            return self._json(401, {"error": "authentification requise (Authorization: Bearer) ; si aucun compte n'existe, utilisez le lien /setup affiché dans les logs"})
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
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "JSON invalide"})
        if u.path == "/login":
            return self._login(body)
        if u.path == "/setup":
            return self._setup_post(body)
        if u.path != "/run":
            return self._json(404, {"error": "not found"})
        if not _authorized(self.headers):
            return self._json(401, {"error": "authentification requise (Authorization: Bearer)"})
        try:
            if body.get("background"):
                job_id = _start_job(body)
                return self._json(202, {"job": job_id, "status": "running"})
            pos, kw = _run_kwargs(body)
            summary = pipeline.run_pipeline(*pos, **kw)
        except KeyError as e:
            return self._json(400, {"error": f"champ requis manquant: {e}"})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        code = 200 if summary.get("status") == "done" else 403
        return self._json(code, summary)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    init_auth(host, port)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"scalper serve sur http://{host}:{port} (POST /login, GET|POST /setup, GET /healthz, GET /jobs/<id>, GET /api/chapter, GET /file, POST /run — Bearer token requis sauf /, /healthz, /login, /setup)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
