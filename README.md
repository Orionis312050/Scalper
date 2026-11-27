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
| `scalper pack --out out --source mangadex --series <id> --lang fr [--no-clean] [--with-clean]` | zippe un `out/` existant (même contenu qu'avec `--zip-export` ; `--with-clean` embarque en plus `cleaned/`, debug inpainting) |

## OCR & remplacement du texte dans les images (automatique)

Dans `scalper run` (et `POST /run`), l'OCR + typesetting s'exécute **automatiquement** dès qu'un
chapitre contient des images (désactivable avec `--no-ocr` / `"with_ocr": false`), en **4 étapes**
(loguées `1/4`…`4/4`) :

1. **OCR** : extraction du texte brut des bulles (`ocr.json`, avec le quadrilatère `poly` quand le
   moteur le fournit — PaddleOCR). Les coordonnées renvoyées par PaddleOCR sont **normalisées dans le
   référentiel de l'image réelle** (sur les très grandes pages, le modèle de détection redimensionne
   en interne et peut renvoyer des coordonnées hors image → rescale uniforme + clamp) ;
2. **traduction** de chaque texte détecté par le LLM ;
3. **inpainting** du texte original sur les images **brutes** (voir moteurs ci-dessous) → `export/images-clean/`.
   Les boîtes OCR (lignes de texte) sont d'abord **fusionnées en régions-bulles**
   (`merge_regions_for_erase`, gap 50 px) : LaMa efface nettement mieux un grand masque continu
   qu'une fine ligne seule. Masque = union rectangle paddé + polygone OCR, dilaté (style
   « pinceau magique ») ; seuls les pixels masqués sont remplacés. Si le modèle ne change rien
   (delta ~0, ex. mauvais fichier ONNX), repli : remplissage par la couleur de l'anneau ;
4. **retypographie** : la traduction est redessinée au même endroit, centrée dans la boîte
   d'origine, **à la taille du texte d'origine** (plus grande police tenant dans la boîte OCR,
   hauteur = hauteur de boîte), avec une couleur contrastée avec le fond (Pillow)
   → `export/images-<lang>/` (les images nettoyées avant retypographie sont aussi gardées dans
   `export/images-clean/`).

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
Le tier PP-OCRv6 se règle via `SCALPER_PADDLE_OCR_TIER` (`tiny|small|medium`, défaut `medium` = défaut
PaddleOCR 3.7 ; `tiny` ne reconnaît pas le japonais, pas de kana → bascule auto en `small`).
⚠️ Par défaut, le **doc unwarping (UVDoc) et la classification d'orientation de page sont désactivés**
(`use_doc_unwarping=False`, `use_doc_orientation_classify=False`) : actifs par défaut dans PaddleOCR 3.7,
ils faussent le mapping des coordonnées des boîtes de détection (offsets de dizaines de px, textes de
bulles manqués — vérifié sur page synthétique). Réactiver via `SCALPER_PADDLE_DOC_UNWARP=1` /
`SCALPER_PADDLE_DOC_ORIENT=1` pour des scans photo déformés. Détection : `SCALPER_PADDLE_DET_BOX_THRESH`
(défaut 0.5) et `SCALPER_PADDLE_DET_UNCLIP` (défaut 2.0) — boîtes un peu plus larges = meilleur
recouvrement pour l'effacement LaMa. `use_textline_orientation=True` conservé (texte vertical JP).

Commandes manuelles : `scalper ocr <chapter_dir> [--mock-ocr]` et `scalper typeset <chapter_dir> --lang fr [--inpaint-engine lama]`.

## Export zip (images + PDF)

Avec `scalper run ... --zip-export` (ou `"zip_export": true` dans `POST /run`, case à cocher dans
l'UI), ou à tout moment avec `scalper pack --out out --source <src> --series <id> --lang fr`,
Scalper crée à la fin dans le dossier `out/` un **seul zip** nommé
`export-<AAAA-MM-JJ-HH-MM>.zip` (date/heure/minutes locales) contenant :

- `images/<chapitre>/<page>` : les pages du résultat — re-traduites (`export/images-<lang>/`)
  quand l'OCR/typeset a tourné, sinon les images d'origine — dans l'ordre des pages (tri naturel) ;
- `pdf/<chapitre>.pdf` : un PDF par chapitre (Pillow, sans dépendance externe ; pages en RGB
  redimensionnées à 1600 px max) ;
- `metadata/<chapitre>/*.json` : métadonnées du chapitre, dont **`ocr.json`** (texte détecté +
  boîtes/polygones normalisés dans l'image) — utile pour déboguer placement/effacement quand on
  n'a plus que le zip ;
- `cleaned/<chapitre>/<page>` (**option**) : images nettoyées (texte original effacé par LaMa,
  traduction pas encore redessinée) pour vérifier l'inpainting — `scalper pack --with-clean`,
  `run --zip-include-clean`, ou `"zip_include_clean": true` dans `POST /run` (case à cocher UI).

Puis **tout le reste du dossier `out/` est supprimé** (zip excepté — y compris `state.json` et
`glossary.json` : pas de reprise après un export zippé). `scalper pack --no-clean` garde le reste.
Le chemin du zip est renvoyé dans le résumé du run (`export_zip`).

### Export dossier + Google Drive (dev uniquement)

En environnement de **dev** (`SCALPER_ENV=dev`), quand un export zip est demandé
(`--zip-export` / `SCALPER_ZIP_EXPORT=true` / `POST /run` / `scalper pack`), Scalper ne zippe
**pas** : il crée à la place un **dossier horodaté** `out/export-<AAAA-MM-JJ-HH-MM>/`
(date + heure/minutes locales) contenant le contenu de l'export — `export/<chapitre>/`
(md/json/html/images re-traduites), `images/<chapitre>/` + `pdf/<chapitre>.pdf`,
`metadata/<chapitre>/*.json`, `cleaned/<chapitre>/` si demandé — et **conserve le reste de
`out/`** (pas de clean en dev, pour débugger). Le chemin est renvoyé dans le résumé du run
(`export_folder`).

Si `GOOGLE_DRIVE_ACCESS_TOKEN` est renseigné (token OAuth, scope `drive.file`), le dossier est
de plus créé dans **Google Drive** sous le même nom `export-<AAAA-MM-JJ-HH-MM>` (parent
`GOOGLE_DRIVE_PARENT_ID`, racine sinon) avec tout le contenu uploadé ; le résumé du run
contient alors `export_drive` (`folder_id`, `folder_name`, `files`). Sans token, l'upload est
ignoré (`export_drive: "skipped: ..."`) : le dossier local suffit à regarder le résultat.

Hors dev (`SCALPER_ENV` absent ou `prod`), le comportement est inchangé : zip uniquement.

Pendant l'inpainting LaMa, chaque page loggue un diagnostic :
`[scalper][lama] image 690x10000 : 28/28 régions inpaintées, couverture masque 20.9% du crop (entrée modèle 21.5%), delta |pred-input| moyen 25.5, repli fill 0, 68.2s`
- « masque vide » / couverture 0 % = coordonnées OCR hors image (voir `_normalize_polys`) ;
- `delta ~0` = le modèle ne produit aucun changement (fichier ONNX en cache invalide) : le cache
  est re-vérifié (taille + sha256) à chaque chargement et re-téléchargé si besoin, et à défaut
  chaque région est remplie par la couleur de l'anneau (**repli fill**) pour effacer le texte.
 Empreinte attendue du modèle : sha256 `1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6`,
 208 044 816 octets (`sha256sum /app/data/.lama/lama_fp32.onnx` dans le conteneur).

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
`"ocr_engine": "paddle"` (moteur OCR : `tesseract` | `paddle`, défaut `SCALPER_OCR_ENGINE` ou `tesseract` —
`paddle` dans le docker-compose),
`"inpaint_engine": "lama"` (moteur d'inpainting : `simple` | `lama` | `auto`),
`"zip_export": true` (zip `export-<date>-<heure>-<minutes>.zip` à la fin, puis supprime le reste de `out/`)
et `"zip_include_clean": true` (embarque aussi `cleaned/` dans le zip, debug inpainting).

## GPU NVIDIA (ex. RTX 2060, 6 Go VRAM)

Le modèle recommandé reste **PP-OCRv6_medium** (`SCALPER_PADDLE_OCR_TIER=medium`) : 34,5 M params,
moins de 1 Go de VRAM en inférence — la RTX 2060 suffit largement, et le gain de vitesse est
conséquent (le doc annonce 0,13 s/page sur A100 ; sur RTX 2060 comptez une fraction de seconde).
`PaddleOCR-VL` (VLM 0,9 B) n'est PAS conseillé ici : trop lourd/limite en 6 Go et mauvais outil
(parsing de documents, pas de bulles).

Déploiement GPU (opt-in, l'image par défaut reste CPU) :

```bash
# prérequis hôte : driver NVIDIA >= 525 (CUDA 12.6) + NVIDIA Container Toolkit (nvidia-ctk)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
# vérif : docker compose exec scalper nvidia-smi
```

- `Dockerfile.gpu` : runtime CUDA 12.6 + cuDNN en wheels pip (`nvidia-cuda-runtime-cu12`,
  `nvidia-cudnn-cu12`), `paddlepaddle-gpu==3.2.2` depuis l'index Baidu cu126 (les wheels GPU 3.x
  ne sont pas sur PyPI), `onnxruntime-gpu` pour LaMa.
- `docker-compose.gpu.yml` : réserve 1 GPU (`deploy.resources.reservations.devices`, driver
  `nvidia`) et pose `SCALPER_PADDLE_DEVICE=gpu:0` (PaddleOCR sur le GPU) + `SCALPER_LAMA_DEVICE=gpu`
  (big-lama en CUDA). Sans ces variables, tout retombe sur le CPU automatiquement.

## Valeurs par défaut du run (env)

`POST /run`, `scalper run` et le formulaire web prennent leurs valeurs par défaut dans l'env :
`SCALPER_DEFAULT_SOURCE`, `SCALPER_DEFAULT_SERIES`, `SCALPER_DEFAULT_LANG` (cible),
`SCALPER_DEFAULT_SRC_LANG` (entrée), `SCALPER_DEFAULT_OCR_ENGINE` (paddle = PP-OCRv6),
`SCALPER_DEFAULT_INPAINT_ENGINE` (lama = big-lama), `SCALPER_ZIP_EXPORT`,
`SCALPER_ZIP_INCLUDE_CLEAN` (embarque `cleaned/` — images nettoyées avant retypographie — dans le
zip d'export). Exemple dans `docker-compose.yml` / `.env.example`.

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

L'interface web (`GET /`) — refondue dans un style **Aceternity UI** (thème sombre, aurora/grid
animée, cartes à bord dégradé, toggles, barre de progression dégradée ; 100 % vanilla CSS/JS,
sans build ni CDN, servie en un seul fichier par le serveur stdlib) — affiche une **barre de
progression** pendant le run (via `POST /run` avec `"background": true` + polling de
`GET /jobs/<id>`) — affichée **en étapes** : téléchargement des images → récupération des textes
(OCR) → traduction des textes → effacement des bulles sur les images (LaMa) → mise en place du
texte traduit sur les images cleaned → compression du rendu (zip) → suppression du reste de `out/`.
L'UI permet de choisir les moteurs (`ocr_engine`, `inpaint_engine`) et coche par
défaut le **zip export** ; puis une **preview par chapitre** : blocs du chapitre récupéré
(source) et du chapitre traduit complet, avec les images servies via `GET /file` (et les images
re-traduites `export/images-<lang>/` quand l'OCR/typeset a tourné).

Déploiement pas à pas sur Coolify : [docs/deploy-coolify.md](docs/deploy-coolify.md).

## Licence

MIT — voir [LICENSE](LICENSE).
