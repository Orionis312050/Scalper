## Lien direct — `fetch-url`

```bash
scalper fetch-url "https://exemple.fr/manga/x/chapitre-1" --out data
```

Produit `raw.html` + `meta.json` + `chapter.json`.

## Adaptateur dédié

Pour une source récurrente, implémenter le contrat `SourceAdapter` (`src/scalper/sources/base.py`) :
   - `list_series() -> list[Series]`
   - `list_chapters(series_id) -> list[ChapterRef]`
   - `fetch_chapter(chapter, dest_dir)` : écrit le brut dans `dest_dir` (raw.html + images + meta.json)
   puis l'enregistrer dans `get_adapter()` (`src/scalper/sources/__init__.py`).
1. **Crawl** : passer par `RespectfulFetcher` (rate limiting ≥ 1 s, backoff, UA identifiable).
2. **Tests** : ajouter un test hors ligne (fixtures mockées, aucun appel réseau en test).
3. **PR** : ouvrir une PR liée au ticket Jira correspondant (projet SCAL).

Exemple d'adaptateur existant : `src/scalper/sources/mangadex.py` (API MangaDex).
