# Déployer Scalper sur Coolify (Docker Compose)

## 1. Préparer le dépôt
```bash
cp .env.example .env        # renseigne MISTRAL_API_KEY (jamais committer .env)
```

## 2. Créer la ressource Coolify
1. Coolify → **New Resource** → **Docker Compose** → sélectionne le repo `Orionis312050/Scalper`.
2. Coolify détecte `docker-compose.yml` (service `scalper`, port **8000**).
3. Dans l'onglet **Environment**, ajoute `MISTRAL_API_KEY` (secret).
4. Déploie, puis attache un domaine. Coolify gère le reverse proxy HTTPS.

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
| GET | `/` | **interface web** (formulaire run) |
| GET | `/healthz` | sonde de santé |
| POST | `/run` | pipeline complet (scrape → clean → translate → QA → export) |

## Sécurité
- Rate limiting ≥ 1 s entre requêtes vers les sources.
- **Conseil** : restreins l'accès à `/run` (authentification Coolify / IP allowlist), car il déclenche
  du scraping et des appels LLM (coût API) sur ton instance.
- Ne mets jamais `MISTRAL_API_KEY` dans le dépôt : `.env` est ignoré par git et exclu de l'image
  (`.dockerignore`).
