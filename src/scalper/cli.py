"""CLI Scalper : list-series, list-groups, list-chapters, fetch, fetch-url, clean, translate, qa, export, ocr, typeset, pack, serve, run."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import pipeline
from .cleaner import clean_chapter
from .exporter import export_chapter, slugify
from .models import Chapter
from .qa import qa_chapter
from .scraper import RespectfulFetcher, chapter_dir
from .sources import get_adapter
from .translator import FakeLLM, MistralChatClient, load_glossary, save_glossary, translate_chapter


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scalper", description="Pipeline de scraping + traduction de chapitres manga/webtoon")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("list-series", help="liste les séries d'une source")
    c.add_argument("source")
    c.add_argument("--query", default="")

    c = sub.add_parser("list-groups", help="liste les groupes de traduction (source mangadex uniquement)")
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

    c = sub.add_parser("fetch-url", help="récupère un lien direct")
    c.add_argument("url")
    c.add_argument("--out", default="data")

    c = sub.add_parser("ocr", help="OCR des images d'un chapitre")
    c.add_argument("chapter_dir")
    c.add_argument("--lang", default="eng", help="langue OCR")
    c.add_argument("--engine", choices=["tesseract", "paddle"], default=None,
                   help="moteur OCR (défaut: SCALPER_OCR_ENGINE ou tesseract)")
    c.add_argument("--mock-ocr", action="store_true")

    c = sub.add_parser("typeset", help="remplace le texte des images par la traduction")
    c.add_argument("chapter_dir")
    c.add_argument("--lang", required=True)
    c.add_argument("--inpaint-engine", choices=["simple", "lama", "auto"], default=None,
                   help="moteur d'inpainting pour la suppression du texte (défaut: SCALPER_INPAINT_ENGINE ou auto)")

    c = sub.add_parser("pack", help="zippe un out/ existant : export-<date>-<heure>-<minutes>.zip (images + PDF), puis supprime le reste de out/ sauf --no-clean")
    c.add_argument("--out", default="out")
    c.add_argument("--source", required=True)
    c.add_argument("--series", required=True)
    c.add_argument("--lang", required=True)
    c.add_argument("--chapters", default=None, help="ids de chapitres séparés par des virgules (défaut: tous les chapitres de la série)")
    c.add_argument("--no-clean", action="store_true", help="garde le reste du dossier out/ (seul le zip est ajouté)")
    c.add_argument("--with-clean", action="store_true", help="embarque aussi cleaned/<chapitre>/ (images nettoyées avant retypographie, debug inpainting) dans le zip")

    c = sub.add_parser("serve", help="démarre le serveur HTTP (déploiement Docker/Coolify)")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8000)

    c = sub.add_parser("run", help="pipeline complet end-to-end")
    c.add_argument("--source", default=os.environ.get("SCALPER_DEFAULT_SOURCE"),
                   help="source (défaut: SCALPER_DEFAULT_SOURCE)")
    c.add_argument("--series", default=os.environ.get("SCALPER_DEFAULT_SERIES"),
                   help="id série (défaut: SCALPER_DEFAULT_SERIES)")
    c.add_argument("--lang", default=os.environ.get("SCALPER_DEFAULT_LANG"),
                   help="langue cible (défaut: SCALPER_DEFAULT_LANG)")
    c.add_argument("--group", default=None, help="filtrer par groupe (mangadex)")
    c.add_argument("--out", default="out")
    c.add_argument("--max-chapters", type=int, default=None)
    c.add_argument("--mock-llm", action="store_true", help="démo hors ligne (LLM factice)")
    c.add_argument("--with-ocr", action=argparse.BooleanOptionalAction, default=True,
                   help="OCR + remplacement du texte dans les images (auto si images présentes ; --no-ocr pour désactiver)")
    c.add_argument("--ocr-engine", choices=["tesseract", "paddle"], default=None,
                   help="moteur OCR (défaut: SCALPER_OCR_ENGINE ou tesseract)")
    c.add_argument("--inpaint-engine", choices=["simple", "lama", "auto"], default=None,
                   help="moteur d'inpainting pour la suppression du texte (défaut: SCALPER_INPAINT_ENGINE ou auto)")
    c.add_argument("--zip-export", action=argparse.BooleanOptionalAction, default=_env_bool("SCALPER_ZIP_EXPORT", False),
                   help="à la fin du run : zip export-<date>-<heure>-<minutes>.zip dans out/ (images + PDF + metadata/ par chapitre), puis supprime le reste de out/")
    c.add_argument("--zip-include-clean", action=argparse.BooleanOptionalAction, default=_env_bool("SCALPER_ZIP_INCLUDE_CLEAN", False),
                   help="avec --zip-export : embarque aussi cleaned/<chapitre>/ (images nettoyées, debug inpainting) dans le zip")
    c.add_argument("--force", action="store_true", help="exporte même si la QA échoue (bypass vérification)")
    c.add_argument("--src-lang", default=os.environ.get("SCALPER_DEFAULT_SRC_LANG"),
                   help="langue d'entrée du chapitre (défaut: SCALPER_DEFAULT_SRC_LANG ; enregistrée dans meta.json et utilisée pour l'OCR)")

    args = p.parse_args(argv)

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
        fetcher = RespectfulFetcher()
        html = fetcher.get(args.url).decode("utf-8", errors="replace")
        domain = (urlparse(args.url).netloc or args.url).lower()
        dest = Path(args.out) / "raw" / domain / slugify(args.url)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "raw.html").write_text(html, encoding="utf-8")
        meta = {
            "source": domain,
            "url": args.url,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        chapter = clean_chapter(html, meta)
        (dest / "chapter.json").write_text(json.dumps(chapter.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _print({"status": "ok", "dest": str(dest), "blocks": len(chapter.blocks), "language": chapter.meta.get("language")})
        return 0
    if args.cmd == "ocr":
        from .ocr import FakeOCR, default_ocr_client, ocr_chapter

        if getattr(args, "engine", None):
            os.environ["SCALPER_OCR_ENGINE"] = args.engine
        client = FakeOCR() if args.mock_ocr else default_ocr_client(lang=args.lang, engine=args.engine)
        regions = ocr_chapter(Path(args.chapter_dir), client, lang=args.lang)
        _print({"status": "ok", "regions": len(regions), "out": str(Path(args.chapter_dir) / "ocr.json")})
        return 0
    if args.cmd == "typeset":
        from .inpaint import default_inpainter
        from .typeset import typeset_chapter

        if args.inpaint_engine:
            os.environ["SCALPER_INPAINT_ENGINE"] = args.inpaint_engine
        outputs = typeset_chapter(Path(args.chapter_dir), args.lang,
                                  inpainter=default_inpainter(args.inpaint_engine))
        _print({"status": "ok", "images": outputs})
        return 0
    if args.cmd == "pack":
        ids = [c.strip() for c in args.chapters.split(",") if c.strip()] if args.chapters else None
        from .drive import is_dev

        if is_dev():
            # dev (SCALPER_ENV=dev) : dossier horodaté au lieu du zip, reste de out/ conservé
            from .drive import folder_run_export, upload_folder_to_drive

            folder = folder_run_export(args.out, args.source, args.series, args.lang, chapter_ids=ids,
                                       include_clean=args.with_clean)
            result: dict = {"status": "ok", "folder": str(folder), "clean": False, "with_clean": args.with_clean}
            try:
                result["drive"] = upload_folder_to_drive(folder)
            except Exception as e:
                result["drive"] = f"skipped: {e}"
            _print(result)
            return 0
        from .packager import zip_run_export

        zip_path = zip_run_export(args.out, args.source, args.series, args.lang, chapter_ids=ids,
                                  clean=not args.no_clean, include_clean=args.with_clean)
        _print({"status": "ok", "zip": str(zip_path), "clean": not args.no_clean, "with_clean": args.with_clean})
        return 0
    if args.cmd == "serve":
        from .server import serve

        serve(args.host, args.port)
        return 0
    if args.cmd == "run":
        for _name in ("source", "series", "lang"):
            if not getattr(args, _name):
                p.error(f"--{_name} requis (ou variable d'env SCALPER_DEFAULT_{_name.upper()})")
        if getattr(args, "ocr_engine", None):
            os.environ["SCALPER_OCR_ENGINE"] = args.ocr_engine
        if getattr(args, "inpaint_engine", None):
            os.environ["SCALPER_INPAINT_ENGINE"] = args.inpaint_engine
        summary = pipeline.run_pipeline(
            args.source, args.series, args.lang,
            out_root=Path(args.out), mock_llm=args.mock_llm,
            max_chapters=args.max_chapters, group=args.group, with_ocr=args.with_ocr,
            force=args.force, src_lang=args.src_lang, inpaint_engine=args.inpaint_engine,
            ocr_engine=args.ocr_engine, zip_export=args.zip_export,
            zip_include_clean=getattr(args, "zip_include_clean", False),
        )
        _print(summary)
        return 0 if summary.get("status") == "done" else 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
