---
name: llm-translator
description: Traduit un chapitre structuré vers une langue cible via LLM Mistral, avec glossaire injecté, cohérence des noms, préservation des placeholders [IMG:n], chunking respectant le budget de tokens.
---

# llm-translator

Paramètres : `target_lang`, `glossary` (JSON terme -> traduction), style.

Règles du prompt système : ne jamais traduire [IMG:n] ni les URLs ; respecter le glossaire ; conserver le registre ; aucune explication en sortie.

Pipeline : glossaire chargé/étendu au fil des chapitres ; chunks <= ~1500 tokens de sortie en respectant les frontières de blocs ; retry max 3 si sortie invalide.

Sortie : `chapter.<lang>.json`. Clé API via `MISTRAL_API_KEY` uniquement.
Implémentation : `src/scalper/translator.py`.
