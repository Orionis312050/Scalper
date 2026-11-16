# Ajouter une source légale

1. **Vérification manuelle** : licence (API officielle / CC / domaine public / contenu propre),
   CGU (pas d'interdiction de scraping), `robots.txt` autorisant les chemins ciblés.
2. **Enregistrement** : ajouter le domaine dans `TRUSTED_SOURCES` (`src/scalper/legal.py`)
   avec sa licence. Sans cet enregistrement, la source est **bloquée** par défaut.
3. **Adaptateur** : implémenter le contrat `SourceAdapter` (`src/scalper/sources/base.py`) :
   - `list_series() -> list[Series]`
   - `list_chapters(series_id) -> list[ChapterRef]`
   - `fetch_chapter(chapter, dest_dir)` : écrit le brut dans `dest_dir` (raw.html + images + meta.json)
   puis l'enregistrer dans `get_adapter()` (`src/scalper/sources/__init__.py`).
4. **Crawl respectueux** : passer par `RespectfulFetcher` (rate limiting ≥ 1 s, backoff, UA identifiable).
5. **Tests** : ajouter un test hors ligne (fixtures mockées, aucun appel réseau en test).
6. **PR** : ouvrir une PR liée au ticket Jira correspondant (projet SCAL).

Exemple d'adaptateur légal existant : `src/scalper/sources/mangadex.py` (API officielle).
