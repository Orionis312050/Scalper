# Scalper

Pipeline de scraping et de traduction de chapitres de mangas/webtoons :
`scrape → clean → translate (LLM) → QA → export` (Markdown / JSON / HTML).

## Installation

```bash
pip install -e ".[dev]"        # + pytest pour les tests
pip install -e ".[agents]"     # + sous-agents Mistral (mistralai-agents)
export MISTRAL_API_KEY=...     # traduction réelle (jamais committée)
```

## Démarrage rapide (démo hors ligne, source fixture « demo »)

```bash
scalper list-series demo
scalper list-chapters demo demo-1
scalper run --source demo --series demo-1 --lang fr --mock-llm --out out
```

## Commandes

| Commande | Description |
|---|---|
| `scalper list-series <source>` / `list-chapters <source> <series> [--group Keiyoushi]` | catalogage (filtre par groupe via API MangaDex) |
| `scalper list-groups mangadex --query Keiyoushi` | recherche de groupes de traduction (API MangaDex) |
| `scalper fetch-url <url>` | lien direct → `raw.html` + `meta.json` + `chapter.json` |
| `scalper ocr <chapter_dir> [--mock-ocr]` | OCR des images (tesseract) → `ocr.json` |
| `scalper typeset <chapter_dir> --lang fr [--inpaint-engine lama]` | remplace le texte des images par la traduction → `export/images-fr/` |
| `scalper run ... --no-ocr` | désactive l'OCR/typeset (actif par défaut si images présentes) |
| `scalper fetch <source> <series> <chapitre>` | téléchargement brut + `state.json` (reprise) |
| `scalper clean <raw.html> --out chapter.json` | nettoyage/structuration en blocs typés |
| `scalper translate <chapter.json> --lang fr [--mock-llm]` | traduction LLM + glossaire |
| `scalper qa <source.json> <traduit.json> --lang fr` | score 0-100, pass si ≥ 80 |
| `scalper export <traduit.json> --format md\|json\|html` | export Markdown / JSON / HTML |
| `scalper run --source ... --series ... --lang fr [--force] [--src-lang ja] [--zip-export]` | pipeline complet end-to-end (`--force` = exporte même si la QA échoue ; `--src-lang` = langue d'entrée du chapitre : enregistrée dans `meta.json`, affichée dans l'UI et utilisée comme langue source de l'OCR ; `--zip-export` = zip final dans `out/`, voir ci-dessous) |
| `scalper pack --out out --source mangadex --series <id> --lang fr [--no-clean]` | zippe un `out/` existant (même contenu qu'avec `--zip-export`) |

## OCR & remplacement du texte dans les images (automatique)

Dans `scalper run` (et `POST /run`), l'OCR + typesetting s'exécute **automatiquement** dès qu'un
chapitre contient des images (désactivable avec `--no-ocr` / `"with_ocr": false`) :
l'OCR extrait les régions texte des images (`ocr.json`, avec le quadrilatère `poly` quand le moteur
le fournit — PaddleOCR), chaque texte détecté est traduit par le LLM, puis `typeset` efface le texte
original (**inpainting**, voir moteurs ci-dessous) et redessine la traduction au même endroit,
centrée, avec une couleur contrastée avec le fond (Pillow). Résultat : `export/images-<lang>/`.

La langue source de l'OCR est déduite de la langue détectée du chapitre (`en→eng`, `fr→fra`,
`ja→jpn`…). L'image Docker n'embarque que `eng` (`tesseract-ocr` inclut `tesseract-ocr-eng`) :
ajoute ex. `tesseract-ocr-fra` / `tesseract-ocr-jpn` dans le `Dockerfile` pour d'autres sources.

**Moteurs OCR** (protocol `OCRClient`, sélection par `SCALPER_OCR_ENGINE` ou `--ocr-engine`) :

| Moteur | Gratuit | Local | Qualité bulles/stylisé | Install |
|---|---|---|---|---|
| `tesseract` (défaut) | oui | oui | moyenne | déjà dans l'image Docker |
| `paddle` (PaddleOCR) | oui | oui | **meilleure** (PP-OCRv6) | déjà dans l'image Docker (`[ocr,ocr-paddle]`), puis `SCALPER_OCR_ENGINE=paddle` |

Mistral OCR (API `mistral-ocr-*`) n'est **pas gratuit** (facturé à la page) : non retenu par défaut.
PaddleOCR : l'image Docker embarque déjà les deux moteurs (`pip install ".[ocr,ocr-paddle]"` dans le
`Dockerfile`, sélection par `SCALPER_OCR_ENGINE`). Hors Docker : `pip install -e ".[ocr-paddle]"`
(les wheels Paddle sont prévues pour Linux ; sur Windows en local, préférez le conteneur Docker).
`paddlepaddle` est épinglé `<3.3` : la 3.3.x plante en inférence CPU oneDNN
(`ConvertPirAttribute2RuntimeAttribute ... ArrayAttribute<DoubleAttribute>`, Paddle#79749) —
vérifié localement : 3.3.1 crashe, 3.2.2 fonctionne.

Commandes manuelles : `scalper ocr <chapter_dir> [--mock-ocr]` et `scalper typeset <chapter_dir> --lang fr [--inpaint-engine lama]`.

## Export zip (images + PDF)

Avec `scalper run ... --zip-export` (ou `"zip_export": true` dans `POST /run`, case à cocher dans
l'UI), ou à tout moment avec `scalper pack --out out --source <src> --series <id> --lang fr`,
Scalper crée à la fin dans le dossier `out/` un **seul zip** nommé
`export-<AAAA-MM-JJ-HH-MM>.zip` (date/heure/minutes locales) contenant :

- `images/<chapitre>/<page>` : les pages du résultat — re-traduites (`export/images-<lang>/`)
  quand l'OCR/typeset a tourné, sinon les images d'origine — dans l'ordre des pages (tri naturel) ;
- `pdf/<chapitre>.pdf` : un PDF par chapitre (Pillow, sans dépendance externe ; pages en RGB
  redimensionnées à 1600 px max).

Puis **tout le reste du dossier `out/` est supprimé** (zip excepté — y compris `state.json` et
`glossary.json` : pas de reprise après un export zippé). `scalper pack --no-clean` garde le reste.
Le chemin du zip est renvoyé dans le résumé du run (`export_zip`).

**Moteurs d'inpainting** (protocol `Inpainter` dans `src/scalper/inpaint.py`, sélection par
`SCALPER_INPAINT_ENGINE` ou `--inpaint-engine`, et `"inpaint_engine"` dans `POST /run`) :

| Moteur | Gratuit | Local | Qualité suppression texte | Install |
|---|---|---|---|---|
| `simple` | oui | oui | basique (remplissage par la couleur de fond estimée sur l'anneau autour de la boîte) | inclus (`[ocr]`) |
| `lama` | oui | oui | **bien meilleure** : big-lama [advimman/lama](https://github.com/advimman/lama) (Apache-2.0), inférence ONNX CPU | `.[inpaint-lama]` (dans l'image Docker) |
| `auto` (défaut) | — | — | `lama` si onnxruntime est installé, sinon `simple` | — |

LaMa : le graphe ONNX (`lama_fp32.onnx`, 208 Mo, port [Carve/LaMa-ONNX](https://huggingface.co/Carve/LaMa-ONNX)
du modèle big-lama, entrée fixe 512×512) est téléchargé **une seule fois** au premier run, sha256
vérifié, dans `SCALPER_LAMA_CACHE` (défaut `~/.cache/scalper/lama` ; `/app/data/.lama` dans le
conteneur via `entrypoint.sh`, donc dans le volume monté). `SCALPER_LAMA_MODEL` permet de pointer
directement vers un fichier `.onnx` local. Pour chaque région de texte, Scalper découpe un crop
carré avec contexte autour de la boîte, le redimensionne en 512×512, construit le masque (polygone
OCR `poly` si présent, sinon rectangle dilaté de 10 %), infère, puis ne recolle que les pixels
masqués à la résolution d'origine : le reste de la page est **inchangé**. Comptez ~2 s par région
en CPU (une page de 20 bulles ≈ 45 s). La réinsertion du texte traduit reste faite par Pillow
(retypographie centrée dans la boîte d'origine).

## Serveur & authentification (`scalper serve`)

Au **premier démarrage** (aucun compte), un lien de création de compte à **usage unique** est affiché
dans les logs du conteneur : `http://<host>:<port>/setup?token=...`. Une fois le compte créé, le lien
est invalidé. Ensuite, `POST /login` `{"username": ..., "password": ...}` renvoie un token de session
(8 h), à passer en header `Authorization: Bearer <token>` sur tous les endpoints (sauf `/`, `/healthz`,
`/login`, `/setup`). Le compte est stocké dans `SCALPER_AUTH_FILE` (défaut `data/auth.json`, monté par
docker-compose), mot de passe haché (PBKDF2). En conteneur (`--host 0.0.0.0`), le lien est
affiché avec `<hote-du-serveur>` à remplacer par l'IP/hôte réel. Le cache PaddleX (modèles OCR,
temp) est redirigé vers `/app/data/.paddlex` via `PADDLE_PDX_CACHE_HOME` dans `entrypoint.sh`,
et celui du modèle LaMa vers `/app/data/.lama` via `SCALPER_LAMA_CACHE` :
les modèles ne sont téléchargés qu'une fois, dans le volume monté.

`POST /run` accepte en plus `"src_lang": "ja"` (langue d'entrée du chapitre),
`"inpaint_engine": "lama"` (moteur d'inpainting : `simple` | `lama` | `auto`) et
`"zip_export": true` (zip `export-<date>-<heure>-<minutes>.zip` à la fin, puis supprime le reste de `out/`).

## Architecture

- **Skills Mistral** (workspace, v1) : `manga-scraper`, `chapter-cleaner`,
  `llm-translator`, `translation-qa`, `manga-exporter` (miroirs dans `skills/`).
- **Sous-agents** (`src/scalper/agents/`, Mistral Agents SDK 1.1) : `scalper-orchestrator`
  délègue à `scraper-agent`, `translator-agent`, `qa-agent` (chacun sa propre session).
  Lancer avec `agents chat` après `pip install -e ".[agents]"`.
- **Gestion de projet** : Jira **SCAL** (méthodologie Scrum) — Epic `SCAL-1` (cahier des
  charges), stories US1–US10 (`SCAL-2`…`SCAL-12`), sprints de 2 semaines.

## Ajouter une source

Voir [docs/add-a-source.md](docs/add-a-source.md) : lien direct `fetch-url` ou adaptateur dédié.

## Tests & CI

```bash
python -m pytest -q        # ou python -m unittest discover -s tests
```

CI GitHub Actions (lint + tests) sur chaque PR — voir `.github/workflows/ci.yml`.

## Déploiement Docker / Coolify

```bash
cp .env.example .env       # MISTRAL_API_KEY (jamais committer)
docker compose up -d       # API sur http://localhost:8000 + interface web sur http://localhost:8000/
```

L'interface web (`GET /`) affiche une **barre de progression** pendant le run (via `POST /run`
avec `"background": true` + polling de `GET /jobs/<id>`), puis une **preview par chapitre** :
blocs du chapitre récupéré (source) et du chapitre traduit complet, avec les images servies
via `GET /file` (et les images re-traduites `export/images-<lang>/` quand l'OCR/typeset a tourné).

Déploiement pas à pas sur Coolify : [docs/deploy-coolify.md](docs/deploy-coolify.md).

## Licence

MIT — voir [LICENSE](LICENSE).
