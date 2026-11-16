---
name: translation-qa
description: Contrôle qualité d'une traduction : segments non traduits, conformité glossaire, cohérence des noms, ratio de longueur, intégrité des placeholders [IMG:n], rapport de score 0-100.
---

# translation-qa

Contrôles : langue cible détectée ; conformité glossaire ; cohérence des noms ; ratio longueur 0.5x-2.5x ; nombre de blocs identique et placeholders intacts ; pas de blocs vides.

Sortie : `{score: 0-100, issues: [...], pass: score >= 80 et 0 issue critique}`.
Implémentation : `src/scalper/qa.py`.
