## Deux façons d'ajouter une source

### 1. Registre déclaratif (recommandé) — `sources.toml`

Déclare le domaine avec sa **licence obligatoire** (le pipeline refuse toute source sans licence) :

```toml
[source."exemple-domaine-public.fr"]
license = "Domaine public (œuvres tombées dans le domaine public)"
kind = "html"
```

Utilisation : `scalper --config sources.toml check-source https://exemple-domaine-public.fr/...`

Règles inviolables : robots.txt vérifié, liste de blocage prioritaire (aucune config ne peut
autoriser mangas-origines.fr / keiyoushi.github.io), aucun contournement de paywall.

### 2. Lien direct — `fetch-url`

```bash
scalper --config sources.toml fetch-url "https://exemple-domaine-public.fr/manga/x/chapitre-1" --out data
```

Le lien passe par `legal-source-check` (licence déclarée + robots.txt + liste de blocage) :
s'il est bloqué, rien n'est téléchargé. Produit `raw.html` + `meta.json` + `chapter.json`.

## Adaptateur dédié

Pour une source récurrente, implémenter le contrat `SourceAdapter` (`src/scalper/sources/base.py`) :
   - `list_series() -> list[Series]`
   - `list_chapters(series_id) -> list[ChapterRef]`
   - `fetch_chapter(chapter, dest_dir)` : écrit le brut dans `dest_dir` (raw.html + images + meta.json)
   puis l'enregistrer dans `get_adapter()` (`src/scalper/sources/__init__.py`).
4. **Crawl respectueux** : passer par `RespectfulFetcher` (rate limiting ≥ 1 s, backoff, UA identifiable).
5. **Tests** : ajouter un test hors ligne (fixtures mockées, aucun appel réseau en test).
6. **PR** : ouvrir une PR liée au ticket Jira correspondant (projet SCAL).

> ⚠️ Les index d'extensions de scanlation (keiyoushi.github.io) et les sites de scanlation
> non autorisés (mangas-origines.fr) sont **bloqués** et ne peuvent pas être autorisés via la config.

Exemple d'adaptateur légal existant : `src/scalper/sources/mangadex.py` (API officielle).
