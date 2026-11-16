# Scalper

Pipeline **légal** de scraping et de traduction de chapitres de mangas/webtoons :
`legal-source-check → scrape → clean → translate (LLM) → QA → export` (Markdown / JSON / HTML).

> ⚠️ **Conformité** : Scalper ne traite que des **sources légales** (API officielles comme
> MangaDex, contenus Creative Commons, domaine public, vos propres contenus). Les sites de
> scanlation non autorisés (ex. **mangas-origines**) sont **bloqués** par `legal-source-check` :
> scraper et traduire des mangas commerciaux sans autorisation est de la contrefaçon.
> Respect de `robots.txt`, rate limiting (≥ 1 s entre requêtes), aucun contournement de
> paywall/captcha. Chaque export embarque une page « Source et licence ».

## Installation

```bash
pip install -e ".[dev]"        # + pytest pour les tests
pip install -e ".[agents]"     # + sous-agents Mistral (mistralai-agents)
export MISTRAL_API_KEY=...     # traduction réelle (jamais committée)
```

## Démarrage rapide (démo hors ligne, source fixture « demo »)

```bash
scalper check-source demo
scalper list-series demo
scalper list-chapters demo demo-1
scalper run --source demo --series demo-1 --lang fr --mock-llm --out out
```

## Commandes

| Commande | Description |
|---|---|
| `scalper check-source <url>` | verdict légal (allowed/blocked + raisons) |
| `scalper list-series <source>` / `list-chapters <source> <series> [--group Keiyoushi]` | catalogage (filtre par groupe via API MangaDex) |
| `scalper list-groups mangadex --query Keiyoushi` | recherche de groupes de scanlation (API officielle) |
| `scalper --config sources.toml fetch-url <url>` | lien direct (gate légal : licence déclarée + robots.txt + blocage) |
| `scalper fetch <source> <series> <chapitre>` | téléchargement brut + `state.json` (reprise) |
| `scalper clean <raw.html> --out chapter.json` | nettoyage/structuration en blocs typés |
| `scalper translate <chapter.json> --lang fr [--mock-llm]` | traduction LLM + glossaire |
| `scalper qa <source.json> <traduit.json> --lang fr` | score 0-100, pass si ≥ 80 |
| `scalper export <traduit.json> --format md\|json\|html` | export avec page licence |
| `scalper run --source ... --series ... --lang fr` | pipeline complet end-to-end |

## Architecture

- **Skills Mistral** (workspace, v1) : `legal-source-check`, `manga-scraper`, `chapter-cleaner`,
  `llm-translator`, `translation-qa`, `manga-exporter` (miroirs dans `skills/`).
- **Sous-agents** (`src/scalper/agents/`, Mistral Agents SDK 1.1) : `scalper-orchestrator`
  délègue à `scraper-agent`, `translator-agent`, `qa-agent` (chacun sa propre session).
  Lancer avec `agents chat` après `pip install -e ".[agents]"`.
- **Gestion de projet** : Jira **SCAL** (méthodologie Scrum) — Epic `SCAL-1` (cahier des
  charges), stories US1–US10 (`SCAL-2`…`SCAL-12`), sprints de 2 semaines.

## Ajouter une source légale

Voir [docs/add-a-source.md](docs/add-a-source.md) : registre déclaratif `sources.toml`
(licence obligatoire par domaine) ou lien direct `fetch-url`, plus adaptateur dédié.
La liste de blocage (mangas-origines, keiyoushi.github.io…) n'est **pas contournable**.

## Politique des sources légales
[docs/legal-sources.md](docs/legal-sources.md).

## Tests & CI

```bash
python -m pytest -q        # ou python -m unittest discover -s tests
```

CI GitHub Actions (lint + tests) sur chaque PR — voir `.github/workflows/ci.yml`.

## Licence

MIT — voir [LICENSE](LICENSE).
