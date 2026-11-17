"""manga-exporter : exports Markdown / JSON / HTML."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .models import Chapter


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "chapter"


def export_markdown(chapter: Chapter, out_path: Path) -> Path:
    meta = chapter.meta
    lines = [
        f"# {meta.get('title', 'Chapitre')}",
        "",
    ]
    for b in chapter.blocks:
        if b.type == "image":
            lines.append(f"![{b.placeholder}]({b.image})")
        elif b.type == "dialogue":
            lines.append(f"> {b.text}")
        else:
            lines.append(b.text)
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def export_json(chapter: Chapter, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(chapter.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def export_html(chapter: Chapter, out_path: Path) -> Path:
    meta = chapter.meta
    parts = [
        "<!doctype html><html lang=\"{lang}\"><head><meta charset=\"utf-8\">",
        f"<title>{html.escape(str(meta.get('title', 'Chapitre')))}</title>",
        "<style>body{font-family:serif;max-width:42rem;margin:2rem auto;line-height:1.6}blockquote{border-left:3px solid #888;padding-left:1rem;color:#333}img{max-width:100%}</style>",
        "</head><body>",
        f"<h1>{html.escape(str(meta.get('title', 'Chapitre')))}</h1>",
    ]
    for b in chapter.blocks:
        if b.type == "image":
            parts.append(f"<p><img src=\"{html.escape(b.image or '')}\" alt=\"{b.placeholder}\"></p>")
        elif b.type == "dialogue":
            parts.append(f"<blockquote>{html.escape(b.text)}</blockquote>")
        else:
            parts.append(f"<p>{html.escape(b.text)}</p>")
    parts.append("</body></html>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def export_chapter(chapter: Chapter, fmt: str, out_dir: Path) -> Path:
    meta = chapter.meta
    base = slugify(f"{meta.get('series', '')}-{meta.get('number', '')}-{meta.get('language', '')}")
    if fmt == "md":
        return export_markdown(chapter, out_dir / f"{base}.md")
    if fmt == "json":
        return export_json(chapter, out_dir / f"{base}.json")
    if fmt == "html":
        return export_html(chapter, out_dir / f"{base}.html")
    raise ValueError(f"format inconnu: {fmt} (md|json|html)")
