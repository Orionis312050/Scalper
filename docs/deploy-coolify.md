# Déployer Scalper sur Coolify (Docker Compose)

## 1. Préparer le dépôt
```bash
cp .env.example .env        # renseigne MISTRAL_API_KEY (jamais committer .env)
```

## 2. Créer la ressource Coolify
1. Coolify → **New Resource** → **Docker Compose** → sélectionne le repo `Orionis312050/Scalper`.
2. Coolify détecte `docker-compose.yml` (service `scalper`, port conteneur **8000**, publié sur le port hôte `SCALPER_HOST_PORT`, défaut **8000**).
3. Dans l'onglet **Environment**, ajoute `MISTRAL_API_KEY` (secret).
4. Déploie, puis attache un domaine. Coolify gère le reverse proxy HTTPS.

> **Port déjà occupé** (`Bind for 0.0.0.0:8000 failed: port is already allocated`) :
> un autre service du serveur utilise déjà le port hôte 8000. Soit tu libères le port
> (`sudo ss -ltnp | grep :8000` puis stoppe le conteneur/service concerné), soit tu ajoutes
> `SCALPER_HOST_PORT=<port libre>` (ex. `8001`) dans l'onglet **Environment** de la ressource
> et tu redéploies — le port conteneur reste 8000 en interne.

## 3. Vérifier
```bash
curl https://<ton-domaine>/healthz
```

## 4. Lancer un pipeline
```bash
curl -X POST https://<ton-domaine>/run \
  -H 'Content-Type: application/json' \
  -d '{"source":"mangadex","series":"<id>","lang":"fr","group":"Keiyoushi","max_chapters":3}'
```
Les résultats sont écrits dans le volume `./out` (monté dans le compose).

## Endpoints
| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/` | **interface web** : formulaire run + barre de progression + previews (chapitre récupéré / traduit complet) |
| GET | `/healthz` | sonde de santé |
| POST | `/run` | pipeline complet (scrape → clean → translate → QA → export, + OCR/typeset auto des images). Options : `"force": true` (exporte même si la QA échoue, chapitres marqués `"forced": true`), `"with_ocr": false` (désactive l'OCR/typeset, actif par défaut), `"background": true` → répond `202 {"job": id}` et le run tourne en thread |
| GET | `/jobs/<id>` | progression du job background `{status, progress: {done, total, current}, result, error}` |
| GET | `/api/chapter?out=&source=&series=&chapter=&lang=` | données de preview : meta, blocs source (`chapter.json`) et traduits (`chapter.<lang>.json`), images, images traduites |
| GET | `/file?out=&path=` | sert un fichier sous `out` (images, exports), anti path-traversal |

## Sécurité
- Rate limiting ≥ 1 s entre requêtes vers les sources.
- **Conseil** : restreins l'accès à `/run` (authentification Coolify / IP allowlist), car il déclenche
  du scraping et des appels LLM (coût API) sur ton instance.
- Ne mets jamais `MISTRAL_API_KEY` dans le dépôt : `.env` est ignoré par git et exclu de l'image
  (`.dockerignore`).
