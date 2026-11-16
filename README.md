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
| `scalper run ... --with-ocr` | pipeline + OCR/typeset (si images présentes) |
| `scalper fetch <source> <series> <chapitre>` | téléchargement brut + `state.json` (reprise) |
| `scalper clean <raw.html> --out chapter.json` | nettoyage/structuration en blocs typés |
| `scalper translate <chapter.json> --lang fr [--mock-llm]` | traduction LLM + glossaire |
| `scalper qa <source.json> <traduit.json> --lang fr` | score 0-100, pass si ≥ 80 |
| `scalper export <traduit.json> --format md\|json\|html` | export Markdown / JSON / HTML |
| `scalper run --source ... --series ... --lang fr [--force]` | pipeline complet end-to-end (`--force` = exporte même si la QA échoue) |

## OCR & remplacement du texte dans les images (V2)

`scalper ocr` (Tesseract via `pytesseract`, binaire installé dans l'image Docker) extrait les
régions texte des images (`ocr.json`) ; `scalper typeset` efface la bulle (inpainting par couleur
moyenne floutée) et redessine la traduction (Pillow).

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

Déploiement pas à pas sur Coolify : [docs/deploy-coolify.md](docs/deploy-coolify.md).

## Licence

MIT — voir [LICENSE](LICENSE).
