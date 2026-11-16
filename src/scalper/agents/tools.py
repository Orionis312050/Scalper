"""Outils Python exposés aux agents Scalper (Mistral Agents SDK).

Chaque fonction = un @agents.tool : nom depuis la fonction, schéma depuis les
paramètres typés, description depuis le docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

from mistralai import agents

from .. import pipeline
from ..cleaner import clean_chapter
from ..models import Chapter
from ..qa import qa_chapter
from ..translator import FakeLLM, MistralChatClient, load_glossary, translate_chapter


@agents.tool
def run_chapter_pipeline(source: str, series: str, lang: str, mock_llm: bool = False) -> dict:
    """Exécute le pipeline complet (scrape -> clean -> translate -> QA -> export) pour une série. mock_llm=True pour une démo hors ligne."""
    return pipeline.run_pipeline(source, series, lang, mock_llm=mock_llm)


@agents.tool
def translate_text(text: str, target_lang: str, glossary_json: str = "{}") -> str:
    """Traduit un texte vers target_lang via le LLM Mistral, en respectant le glossaire JSON {terme: traduction}."""
    client = MistralChatClient()
    glossary = json.loads(glossary_json or "{}")
    meta = {"language": "unknown"}
    chapter = Chapter(meta=meta, blocks=[])
    from ..models import Block

    chapter.blocks = [Block(index=0, type="narration", text=text)]
    out = translate_chapter(chapter, target_lang, glossary, client)
    return out.blocks[0].text


@agents.tool
def qa_translation(source_json: str, translated_json: str, target_lang: str, glossary_json: str = "{}") -> dict:
    """Contrôle qualité : compare un chapitre source et sa traduction (JSON), retourne {score, issues, pass}."""
    src = Chapter.from_dict(json.loads(source_json))
    tr = Chapter.from_dict(json.loads(translated_json))
    glossary = json.loads(glossary_json or "{}")
    return qa_chapter(src, tr, glossary, target_lang)
