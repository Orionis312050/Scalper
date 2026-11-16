"""chapter-cleaner : HTML brut -> chapter.json structuré (blocs typés)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from .models import Block, Chapter

BOILERPLATE_HINTS = (
    "nav", "menu", "advert", "ads", "pub", "footer", "header", "sidebar",
    "comment", "watermark", "scan", "credit", "prev", "next", "previous",
    "suivant", "précédent", "chapitre", "télécharger", "download",
)
SKIP_TAGS = {"script", "style", "noscript", "iframe", "form", "button"}
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "figcaption", "td", "section", "article"}

_DIALOGUE_RE = re.compile(r"[«»“”\"']|^-\s")
_JP_RE = re.compile(r"[぀-ヿ一-鿿]")
_ONO_RE = re.compile(r"^[A-Za-zÀ-ÿ*!?·.]+$")


def _is_boilerplate(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    if len(t) < 2:
        return True
    return any(h in t for h in BOILERPLATE_HINTS) and len(t) < 120


def classify(text: str) -> str:
    t = text.strip()
    if _DIALOGUE_RE.search(t):
        return "dialogue"
    if _ONO_RE.match(t) and len(t) <= 16 and not t.endswith("."):
        return "onomatopoeia"
    return "narration"


def detect_language(text: str) -> str:
    if _JP_RE.search(text):
        return "ja"
    words = re.findall(r"[a-zàâäéèêëîïôöùûüç']+", text.lower())
    if not words:
        return "unknown"
    fr_words = {"le", "la", "les", "un", "une", "des", "est", "dans", "pour", "avec", "sur", "et", "de", "du", "en", "que", "qui", "pas", "au", "aux", "ce", "cette", "son", "sa", "ses", "il", "elle", "nous", "vous", "à", "était", "décide"}
    en_words = {"the", "and", "is", "are", "of", "to", "in", "that", "it", "for", "on", "with", "as", "was", "this", "his", "her"}
    fr = sum(1 for w in words if w in fr_words)
    en = sum(1 for w in words if w in en_words)
    if fr > en:
        return "fr"
    if en:
        return "en"
    return "unknown"


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[tuple[str, str | None]] = []  # (text, img_src)
        self._skip_depth = 0
        self._current: list[str] = []
        self._in_block = False
        self._attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "img":
            self.chunks.append(("", a.get("src")))
            return
        if tag in BLOCK_TAGS:
            self._flush()
            self._in_block = True
            self._current = []
            self._attrs = a

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS and self._in_block:
            self._flush()
            self._in_block = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._current.append(data.strip())

    def _flush(self) -> None:
        text = " ".join(self._current).strip()
        self._current = []
        if not text:
            return
        cls_id = (self._attrs.get("class", "") + " " + self._attrs.get("id", "")).lower()
        if any(h in cls_id for h in BOILERPLATE_HINTS):
            return
        if _is_boilerplate(text):
            return
        self.chunks.append((text, None))

    def close(self) -> None:
        super().close()
        self._flush()


def clean_chapter(raw_html: str, meta: dict[str, Any]) -> Chapter:
    parser = _Extractor()
    parser.feed(raw_html)
    parser.close()
    blocks: list[Block] = []
    for text, img in parser.chunks:
        if img is not None and not text:
            blocks.append(Block(index=len(blocks), type="image", image=img))
        elif text:
            blocks.append(Block(index=len(blocks), type=classify(text), text=text))
    meta = dict(meta)
    meta["language"] = detect_language(" ".join(b.text for b in blocks if b.type != "image"))
    return Chapter(meta=meta, blocks=blocks)
