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
| `scalper typeset <chapter_dir> --lang fr` | remplace le texte des images par la traduction → `export/images-fr/` |
| `scalper run ... --no-ocr` | désactive l'OCR/typeset (actif par défaut si images présentes) |
| `scalper fetch <source> <series> <chapitre>` | téléchargement brut + `state.json` (reprise) |
| `scalper clean <raw.html> --out chapter.json` | nettoyage/structuration en blocs typés |
| `scalper translate <chapter.json> --lang fr [--mock-llm]` | traduction LLM + glossaire |
| `scalper qa <source.json> <traduit.json> --lang fr` | score 0-100, pass si ≥ 80 |
| `scalper export <traduit.json> --format md\|json\|html` | export Markdown / JSON / HTML |
| `scalper run --source ... --series ... --lang fr [--force]` | pipeline complet end-to-end (`--force` = exporte même si la QA échoue) |

## OCR & remplacement du texte dans les images (automatique)

Dans `scalper run` (et `POST /run`), l'OCR + typesetting s'exécute **automatiquement** dès qu'un
chapitre contient des images (désactivable avec `--no-ocr` / `"with_ocr": false`) :
Tesseract (`pytesseract`, binaire installé dans l'image Docker) extrait les régions texte des
images (`ocr.json`), chaque texte détecté est traduit par le LLM, puis `typeset` efface le texte
original (inpainting : remplissage par la couleur de fond estimée sur l'anneau de pixels autour de
la bulle) et redessine la traduction au même endroit, centrée, avec une couleur contrastée avec le
fond (Pillow). Résultat : `export/images-<lang>/`.

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

Commandes manuelles : `scalper ocr <chapter_dir> [--mock-ocr]` et `scalper typeset <chapter_dir> --lang fr`.

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
