"""CLI Scalper : check-source, list-series, list-groups, list-chapters, fetch, clean, translate, qa, export, run."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import legal, pipeline
from .cleaner import clean_chapter
from .exporter import export_chapter, slugify
from .models import Chapter
from .qa import qa_chapter
from .scraper import RespectfulFetcher, chapter_dir
from .sources import get_adapter
from .translator import FakeLLM, MistralChatClient, load_glossary, save_glossary, translate_chapter


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scalper", description="Pipeline légal de scraping + traduction de chapitres manga/webtoon")
    p.add_argument("--config", default=None, help="registre de sources personnalisées (TOML, ex. sources.example.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check-source", help="valide une source (robots.txt, licence, liste de blocage)")
    c.add_argument("source")

    c = sub.add_parser("list-series", help="liste les séries d'une source")
    c.add_argument("source")
    c.add_argument("--query", default="")

    c = sub.add_parser("list-groups", help="liste les groupes de scanlation (source mangadex uniquement)")
    c.add_argument("source")
    c.add_argument("--query", default="")

    c = sub.add_parser("list-chapters", help="liste les chapitres d'une série (--group pour filtrer par groupe, ex. Keiyoushi)")
    c.add_argument("source")
    c.add_argument("series")
    c.add_argument("--group", default=None, help="nom ou UUID du groupe (mangadex)")
    c.add_argument("--lang", default="en", help="langue des chapitres (mangadex), '' pour toutes")

    c = sub.add_parser("fetch", help="télécharge un chapitre brut")
    c.add_argument("source")
    c.add_argument("series")
    c.add_argument("chapter")
    c.add_argument("--out", default="data")

    c = sub.add_parser("clean", help="nettoie un raw.html en chapter.json")
    c.add_argument("raw_html")
    c.add_argument("--meta", default=None)
    c.add_argument("--out", required=True)

    c = sub.add_parser("translate", help="traduit un chapter.json")
    c.add_argument("chapter_json")
    c.add_argument("--lang", required=True)
    c.add_argument("--glossary", default=None)
    c.add_argument("--mock-llm", action="store_true")
    c.add_argument("--out", required=True)

    c = sub.add_parser("qa", help="contrôle qualité d'une traduction")
    c.add_argument("source_json")
    c.add_argument("translated_json")
    c.add_argument("--lang", required=True)
    c.add_argument("--glossary", default=None)

    c = sub.add_parser("export", help="exporte un chapitre traduit")
    c.add_argument("chapter_json")
    c.add_argument("--format", choices=["md", "json", "html"], required=True)
    c.add_argument("--out", required=True)

    c = sub.add_parser("fetch-url", help="récupère un lien direct (soumis à legal-source-check : licence déclarée + robots.txt + liste de blocage)")
    c.add_argument("url")
    c.add_argument("--out", default="data")

    c = sub.add_parser("run", help="pipeline complet end-to-end")
    c.add_argument("--source", required=True)
    c.add_argument("--series", required=True)
    c.add_argument("--lang", required=True)
    c.add_argument("--group", default=None, help="filtrer par groupe (mangadex)")
    c.add_argument("--out", default="out")
    c.add_argument("--max-chapters", type=int, default=None)
    c.add_argument("--mock-llm", action="store_true", help="démo hors ligne (LLM factice)")

    args = p.parse_args(argv)

    if getattr(args, "config", None):
        legal.load_config(args.config)

    if args.cmd == "check-source":
        _print(legal.check_source(args.source).to_dict())
        return 0
    if args.cmd == "list-series":
        adapter = get_adapter(args.source)
        series = adapter.list_series(query=args.query) if args.source == "mangadex" else adapter.list_series()
        _print([s.to_dict() for s in series])
        return 0
    if args.cmd == "list-groups":
        if args.source != "mangadex":
            print("list-groups est disponible uniquement pour la source 'mangadex'", file=sys.stderr)
            return 1
        from .sources.mangadex import MangaDexAdapter

        _print(MangaDexAdapter(fetcher=RespectfulFetcher()).list_groups(args.query))
        return 0
    if args.cmd == "list-chapters":
        adapter = get_adapter(args.source)
        kwargs: dict = {}
        if args.group:
            if args.source != "mangadex":
                print("--group est disponible uniquement pour la source 'mangadex'", file=sys.stderr)
                return 1
            kwargs["group"] = args.group
        if args.source == "mangadex":
            kwargs["lang"] = args.lang or None
        _print([c.to_dict() for c in adapter.list_chapters(args.series, **kwargs)])
        return 0
    if args.cmd == "fetch":
        adapter = get_adapter(args.source, fetcher=RespectfulFetcher())
        refs = adapter.list_chapters(args.series)
        ref = next((r for r in refs if r.id == args.chapter or r.number == args.chapter), None)
        if ref is None:
            print(f"chapitre introuvable: {args.chapter}", file=sys.stderr)
            return 1
        dest = chapter_dir(Path(args.out), args.source, args.series, ref.id)
        adapter.fetch_chapter(ref, dest)
        _print({"fetched": ref.to_dict(), "dest": str(dest)})
        return 0
    if args.cmd == "clean":
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if args.meta else {}
        chapter = clean_chapter(Path(args.raw_html).read_text(encoding="utf-8"), meta)
        Path(args.out).write_text(json.dumps(chapter.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _print({"out": args.out, "blocks": len(chapter.blocks), "language": chapter.meta.get("language")})
        return 0
    if args.cmd == "translate":
        chapter = Chapter.from_dict(json.loads(Path(args.chapter_json).read_text(encoding="utf-8")))
        glossary = load_glossary(Path(args.glossary) if args.glossary else None)
        client = FakeLLM() if args.mock_llm else MistralChatClient()
        translated = translate_chapter(chapter, args.lang, glossary, client)
        Path(args.out).write_text(json.dumps(translated.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        if args.glossary:
            save_glossary(Path(args.glossary), glossary)
        _print({"out": args.out, "lang": args.lang})
        return 0
    if args.cmd == "qa":
        src = Chapter.from_dict(json.loads(Path(args.source_json).read_text(encoding="utf-8")))
        tr = Chapter.from_dict(json.loads(Path(args.translated_json).read_text(encoding="utf-8")))
        glossary = load_glossary(Path(args.glossary) if args.glossary else None)
        report = qa_chapter(src, tr, glossary, args.lang)
        _print(report)
        return 0 if report["pass"] else 2
    if args.cmd == "export":
        chapter = Chapter.from_dict(json.loads(Path(args.chapter_json).read_text(encoding="utf-8")))
        path = export_chapter(chapter, args.format, Path(args.out))
        _print({"exported": str(path)})
        return 0
    if args.cmd == "fetch-url":
        verdict = legal.check_source(args.url)
        if not verdict.allowed:
            _print({"status": "blocked", "verdict": verdict.to_dict()})
            return 3
        fetcher = RespectfulFetcher()
        html = fetcher.get(args.url).decode("utf-8", errors="replace")
        domain = legal.domain_of(args.url)
        dest = Path(args.out) / "raw" / domain / slugify(args.url)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "raw.html").write_text(html, encoding="utf-8")
        meta = {
            "source": domain,
            "license": verdict.license,
            "url": args.url,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        chapter = clean_chapter(html, meta)
        (dest / "chapter.json").write_text(json.dumps(chapter.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _print({"status": "ok", "dest": str(dest), "blocks": len(chapter.blocks), "language": chapter.meta.get("language")})
        return 0
    if args.cmd == "run":
        summary = pipeline.run_pipeline(
            args.source, args.series, args.lang,
            out_root=Path(args.out), mock_llm=args.mock_llm,
            max_chapters=args.max_chapters, group=args.group,
        )
        _print(summary)
        return 0 if summary.get("status") == "done" else 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
