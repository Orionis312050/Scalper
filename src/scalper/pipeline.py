"""Orchestration : scrape -> clean -> translate -> QA -> export."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .cleaner import clean_chapter
from .exporter import export_chapter
from .models import Block, Chapter
from .qa import qa_chapter
from .scraper import RespectfulFetcher, RunState, chapter_dir
from .sources import get_adapter
from .translator import FakeLLM, LLMClient, MistralChatClient, load_glossary, save_glossary, translate_chapter


def run_pipeline(
    source_id: str,
    series_id: str,
    lang: str,
    out_root: Path | str = Path("out"),
    mock_llm: bool = False,
    fetcher: RespectfulFetcher | None = None,
    max_chapters: int | None = None,
    group: str | None = None,
    with_ocr: bool = True,
    force: bool = False,
    on_progress: Callable[[int, int, Any], None] | None = None,
) -> dict[str, Any]:
    out_root = Path(out_root)
    fetcher = fetcher or RespectfulFetcher(min_delay=1.0)
    adapter = get_adapter(source_id, fetcher=fetcher)
    chapters = adapter.list_chapters(series_id, **({"group": group} if group else {}))
    if max_chapters is not None:
        chapters = chapters[:max_chapters]

    glossary_path = out_root / "glossary.json"
    glossary = load_glossary(glossary_path)
    client: LLMClient = FakeLLM() if mock_llm else MistralChatClient()
    state = RunState(out_root / "state.json")

    results: list[dict[str, Any]] = []
    for ref in chapters:
        entry: dict[str, Any] = {"chapter": ref.to_dict()}
        if state.is_fetched(ref.id):
            entry["skipped"] = "déjà récupéré (state.json)"
        else:
            dest = chapter_dir(out_root, source_id, series_id, ref.id)
            adapter.fetch_chapter(ref, dest)
            meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
            meta["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            meta["series"] = series_id
            meta["number"] = ref.number
            meta["title"] = ref.title
            meta["url"] = ref.url
            raw = (dest / "raw.html").read_text(encoding="utf-8")
            chapter = clean_chapter(raw, meta)
            (dest / "chapter.json").write_text(json.dumps(chapter.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            state.mark_fetched(ref.id)
            entry["blocks"] = len(chapter.blocks)

            translated = translate_chapter(chapter, lang, glossary, client)
            (dest / f"chapter.{lang}.json").write_text(
                json.dumps(translated.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report = qa_chapter(chapter, translated, glossary, lang)
            (dest / f"qa.{lang}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            entry["qa"] = {"score": report["score"], "pass": report["pass"], "issues": len(report["issues"])}
            if report["pass"] or force:
                for fmt in ("md", "json", "html"):
                    export_chapter(translated, fmt, dest / "export")
                if with_ocr:
                    entry["ocr"] = _run_ocr_typeset(dest, meta, lang, client, glossary)
                state.mark_done(ref.id)
                entry["status"] = "ok"
                if not report["pass"]:
                    entry["forced"] = True
            else:
                entry["status"] = "qa_failed"
        results.append(entry)
        if on_progress is not None:
            on_progress(len(results), len(chapters), ref)

    save_glossary(glossary_path, glossary)
    return {"status": "done", "source": source_id, "series": series_id, "lang": lang, "chapters": results}


# Langue détectée (2 lettres) -> code langue Tesseract
_TESSERACT_LANGS = {
    "en": "eng", "fr": "fra", "es": "spa", "de": "deu", "it": "ita", "pt": "por",
    "ja": "jpn", "ko": "kor", "zh": "chi_sim",
}


def _run_ocr_typeset(dest: Path, meta: dict[str, Any], lang: str, client: LLMClient, glossary: dict[str, str]) -> str:
    """OCR des images du chapitre, traduction des textes détectés via le LLM, puis
    remplacement dans les images (inpainting + retypographie) -> export/images-<lang>/."""
    from .ocr import TesseractOCR, ocr_chapter
    from .typeset import typeset_chapter

    if not (dest / "images").exists():
        return "skipped : aucune image dans ce chapitre"
    try:
        src_lang = _TESSERACT_LANGS.get(str(meta.get("language") or "en")[:2].lower(), "eng")
        regions = ocr_chapter(dest, TesseractOCR(), lang=src_lang)
        if not regions:
            return "skipped : aucun texte détecté dans les images"
        ocr_chapter_src = Chapter(
            meta={"language": src_lang},
            blocks=[Block(index=i, type="narration", text=r.get("text", "")) for i, r in enumerate(regions)],
        )
        translated = translate_chapter(ocr_chapter_src, lang, glossary, client)
        outs = typeset_chapter(dest, lang, translations=[b.text for b in translated.blocks])
        return f"ok ({len(outs)} images reconstituées, {len(regions)} textes remplacés)"
    except Exception as e:
        return f"erreur: {e}"
