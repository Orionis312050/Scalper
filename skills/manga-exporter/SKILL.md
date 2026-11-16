---
name: manga-exporter
description: Exporte un chapitre traduit en Markdown, JSON ou HTML, avec images relatives, page « Source et licence » obligatoire et noms de fichiers assainis.
---

# manga-exporter

Formats : `md`, `json`, `html` (EPUB en V2). Noms : `<series>-<chapitre>-<lang>.<ext>`. Page « Source et licence » (URL, licence, date) incluse dans chaque export. Jamais d'export depuis une source bloquée.

Implémentation : `src/scalper/exporter.py`.
