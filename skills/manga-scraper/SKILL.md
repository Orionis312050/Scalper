---
name: manga-scraper
description: Scraping générique et respectueux via un SourceAdapter, avec rate limiting, stockage brut + JSON, et state.json pour reprendre après interruption.
---

# manga-scraper

Contrat `SourceAdapter` : `list_series()`, `list_chapters(series_id)`, `fetch_chapter(chapter, dest_dir)`.

Règles : passer par legal-source-check d'abord ; >= 1s entre requêtes, 1 thread, UA identifiable, backoff ; stockage `out/raw/<source>/<series>/<chapter>/` + `state.json` resumable ; jamais de contournement d'auth/paywall.

Implémentations : `src/scalper/sources/demo.py` (fixture), `src/scalper/sources/mangadex.py` (API officielle).
