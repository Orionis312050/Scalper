"""translation-qa : contrôles qualité et score d'une traduction."""

from __future__ import annotations

from typing import Any

from .cleaner import detect_language
from .models import Chapter

MIN_RATIO = 0.5
MAX_RATIO = 2.5


def qa_chapter(source: Chapter, translated: Chapter, glossary: dict[str, str], target_lang: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    critical = False

    if len(source.blocks) != len(translated.blocks):
        issues.append({"type": "block_count", "critical": True, "detail": f"{len(source.blocks)} blocs source vs {len(translated.blocks)} traduits"})
        critical = True

    src_ph = source.placeholders()
    tr_ph = translated.placeholders()
    if src_ph != tr_ph:
        issues.append({"type": "placeholders", "critical": True, "detail": f"placeholders source {src_ph} != traduits {tr_ph}"})
        critical = True

    for b in translated.text_blocks():
        if not b.text.strip():
            issues.append({"type": "empty_block", "critical": True, "block": b.index, "detail": "bloc traduit vide"})
            critical = True

    src_text = source.full_text()
    tr_text = translated.full_text()
    for term, trad in glossary.items():
        if term in src_text and trad not in tr_text:
            issues.append({"type": "glossary", "critical": True, "block": None, "detail": f"'{term}' devrait être traduit par '{trad}'"})

    ratio = (len(tr_text) / len(src_text)) if src_text else 1.0
    if not (MIN_RATIO <= ratio <= MAX_RATIO):
        issues.append({"type": "length_ratio", "critical": False, "detail": f"ratio {ratio:.2f} hors [{MIN_RATIO}, {MAX_RATIO}]"})

    detected = detect_language(tr_text)
    if detected not in (target_lang, "unknown"):
        issues.append({"type": "wrong_language", "critical": True, "detail": f"langue détectée '{detected}' != cible '{target_lang}'"})
        critical = True

    score = 100
    for i in issues:
        score -= 25 if i.get("critical") else 8
    score = max(0, score)
    return {"score": score, "issues": issues, "pass": score >= 80 and not critical, "target_lang": target_lang}
