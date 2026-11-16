"""Adaptateur API officielle MangaDex (https://api.mangadex.org) — source légale.

L'API publique ne nécessite pas de clé pour la lecture. Rate limiting appliqué.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import ChapterRef, Series
from ..scraper import RespectfulFetcher

API = "https://api.mangadex.org"


class MangaDexAdapter:
    id = "mangadex"
    license = "API officielle MangaDex (données publiques ; images selon licence de l'œuvre)"

    def __init__(self, fetcher: RespectfulFetcher | None = None) -> None:
        self.fetcher = fetcher or RespectfulFetcher(min_delay=1.0)

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict:
        url = API + path
        if params:
            from urllib.parse import urlencode

            url += "?" + urlencode(params)
        return json.loads(self.fetcher.get(url).decode("utf-8"))

    def list_series(self, query: str = "", limit: int = 20) -> list[Series]:
        params = {"limit": str(limit), "order[relevance]": "desc", "includes[]": "cover_art"}
        if query:
            params["title"] = query
        data = self._get_json("/manga", params)
        return [
            Series(
                id=m["id"],
                title=m["attributes"]["title"].get("en") or next(iter(m["attributes"]["title"].values())),
                url=f"https://mangadex.org/title/{m['id']}",
                license=self.license,
            )
            for m in data.get("data", [])
        ]

    def list_chapters(self, series_id: str, limit: int = 100) -> list[ChapterRef]:
        data = self._get_json(
            f"/manga/{series_id}/feed",
            {
                "limit": str(limit),
                "translatedLanguage[]": "en",
                "order[chapter]": "asc",
                "contentRating[]": "safe",
                "contentRating[]": "suggestive",
            },
        )
        refs = []
        for c in data.get("data", []):
            attrs = c["attributes"]
            refs.append(
                ChapterRef(
                    id=c["id"],
                    number=attrs.get("chapter") or "?",
                    title=attrs.get("title") or f"Chapter {attrs.get('chapter', '?')}",
                    url=f"https://mangadex.org/chapter/{c['id']}",
                )
            )
        return refs

    def fetch_chapter(self, chapter: ChapterRef, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        at_home = self._get_json(f"/at-home/server/{chapter.id}")
        base = at_home["baseUrl"]
        data = self._get_json(f"/chapter/{chapter.id}")
        attrs = data["data"]["attributes"]
        chapter_hash = attrs["hash"]
        pages = attrs.get("pages", 0)
        files = attrs.get("data", []) + attrs.get("dataSaver", [])
        lines = [f"# raw chapter {chapter.id} ({chapter.title})", ""]
        img_dir = dest_dir / "images"
        img_dir.mkdir(exist_ok=True)
        quality = "data-saver" if attrs.get("dataSaver") else "data"
        for i, fname in enumerate(files, start=1):
            url = f"{base}/{quality}/{chapter_hash}/{fname}"
            (img_dir / fname).write_bytes(self.fetcher.get(url))
            lines.append(f"![page {i}](images/{fname})")
        (dest_dir / "raw.html").write_text("\n".join(lines), encoding="utf-8")
        (dest_dir / "meta.json").write_text(
            json.dumps(
                {"source": self.id, "license": self.license, "chapter": chapter.to_dict(), "pages": pages},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
