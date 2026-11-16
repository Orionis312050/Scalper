---
name: legal-source-check
description: Valide qu'une source manga/webtoon est légale (robots.txt, CGU, licence, liste de blocage) avant tout scraping. Bloqué si un critère échoue.
---

# legal-source-check

Checklist (toutes doivent passer) :
1. robots.txt : chemins ciblés non interdits.
2. CGU : pas d'interdiction de scraping/réutilisation.
3. Licence : API officielle, Creative Commons, domaine public ou contenu propre.
4. Aucun contournement de paywall/captcha/authentification.
5. Liste de blocage : sites de scanlation non autorisés refusés (ex. mangas-origines).

Politique de crawl : UA identifiable, >= 1s entre requêtes, 1 concurrence, backoff sur 429/5xx.

Sortie : verdict JSON `{source, verdict: allowed|blocked, reasons, license}`.
Implémentation : `src/scalper/legal.py`.
