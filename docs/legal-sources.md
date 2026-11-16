# Politique des sources légales

Scalper ne doit traiter que des contenus pour lesquels une autorisation existe.

## Autorisé
- **API officielles** : ex. API MangaDex (`api.mangadex.org`), enregistrée dans `TRUSTED_SOURCES`.
- **Creative Commons** : œuvres publiées sous licence CC (vérifier la licence exacte).
- **Domaine public** : œuvres dont les droits patrimoniaux sont éteints.
- **Contenus propres** : vos propres webtoons/mangas.
- **Fixture de test** : `demo` (texte inventé, hors ligne).

## Interdit (bloqué par `legal-source-check`)
- Sites de scanlation non autorisés hébergeant des mangas commerciaux sans licence
  (ex. **mangas-origines**) : contrefaçon + violation des CGU.
- **keiyoushi.github.io / keiyoushi.com** : index d'extensions de lecteurs pointant vers des
  sites de scanlation non autorisés — bloqué. Pour suivre un groupe de scanlation (ex.
  Keiyoushi), utiliser la voie légale : l'API officielle MangaDex avec le filtre par groupe
  (`scalper list-chapters mangadex <series> --group Keiyoushi`, `scalper list-groups mangadex --query Keiyoushi`).
- Toute source dont `robots.txt` interdit le chemin ciblé.
- Tout contournement de paywall, captcha, authentification ou DRM.

## Règles de crawl obligatoires
- `User-Agent` identifiable (nom du projet + contact).
- ≥ 1 seconde entre deux requêtes, 1 seule requête concurrente.
- Backoff exponentiel sur 429/5xx.
- Chaque export embarque une page « Source et licence » (URL, licence, date de récupération).

## Ajouter une source
Voir [add-a-source.md](add-a-source.md).
