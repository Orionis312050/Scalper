---
name: chapter-cleaner
description: Nettoie un chapitre brut (HTML/JSON) : retire boilerplate, watermarks, liens prev/next ; segmente en blocs typés (dialogue/narration/caption/onomatopée/image [IMG:n]) ; produit chapter.json.
---

# chapter-cleaner

Étapes : supprimer nav/pub/watermarks/liens prev-next ; extraire le contenu principal ; segmenter en blocs ordonnés typés ; normaliser UTF-8 ; détecter la langue source.

Sortie : `chapter.json` = `{meta, blocks: [{index, type, text, image}]}`.
Implémentation : `src/scalper/cleaner.py`.
