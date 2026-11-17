"""Adaptateur API MangaDex (https://api.mangadex.org).

L'API publique ne nécessite pas de clé pour la lecture. Rate limiting appliqué.
Permet de filtrer les chapitres par groupe (ex. Keiyoushi) via le paramètre groups[].
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlencode

from ..models import ChapterRef, Series
from ..scraper import RespectfulFetcher

API = "https://api.mangadex.org"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class MangaDexAdapter:
    id = "mangadex"

    def __init__(self, fetcher: RespectfulFetcher | None = None) -> None:
        self.fetcher = fetcher or RespectfulFetcher(min_delay=1.0)

    def _get_json(self, path: str, params: dict[str, object] | None = None) -> dict:
        url = API + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        return json.loads(self.fetcher.get(url).decode("utf-8"))

    def list_series(self, query: str = "", limit: int = 20) -> list[Series]:
        params: dict[str, object] = {"limit": limit, "order[relevance]": "desc", "includes[]": "cover_art"}
        if query:
            params["title"] = query
        data = self._get_json("/manga", params)
        return [
            Series(
                id=m["id"],
                title=m["attributes"]["title"].get("en") or next(iter(m["attributes"]["title"].values())),
                url=f"https://mangadex.org/title/{m['id']}",
            )
            for m in data.get("data", [])
        ]

    def list_groups(self, query: str = "", limit: int = 20) -> list[dict]:
        """Recherche des groupes de traduction par nom (ex. 'Keiyoushi')."""
        params: dict[str, object] = {"limit": limit, "order[name]": "asc"}
        if query:
            params["name"] = query
        data = self._get_json("/group", params)
        return [{"id": g["id"], "name": g["attributes"]["name"]} for g in data.get("data", [])]

    def resolve_group(self, name_or_id: str) -> str:
        if _UUID_RE.match(name_or_id):
            return name_or_id
        groups = self.list_groups(name_or_id)
        if not groups:
            raise KeyError(f"groupe introuvable sur MangaDex: {name_or_id}")
        exact = next((g for g in groups if g["name"].lower() == name_or_id.lower()), None)
        return (exact or groups[0])["id"]

    def list_chapters(self, series_id: str, limit: int = 100, group: str | None = None, lang: str | None = "en") -> list[ChapterRef]:
        params: dict[str, object] = {
            "limit": limit,
            "order[chapter]": "asc",
            "contentRating[]": ["safe", "suggestive"],
            "includeFutureUpdates": "0",
        }
        if lang:
            params["translatedLanguage[]"] = [lang]
        if group:
            params["groups[]"] = [self.resolve_group(group)]
        data = self._get_json(f"/manga/{series_id}/feed", params)
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
        # Le payload at-home embarque hash/data/dataSaver ; repli sur /chapter/{id}
        # (qui peut ne pas encore exposer hash/data juste après une publication).
        payload = at_home.get("chapter") or {}
        attrs = self._get_json(f"/chapter/{chapter.id}")["data"]["attributes"]
        chapter_hash = payload.get("hash") or attrs.get("hash")
        pages = attrs.get("pages") or payload.get("pages") or 0
        files = payload.get("data") or attrs.get("data") or []
        lines = [f"# raw chapter {chapter.id} ({chapter.title})", ""]
        if chapter_hash and files:
            img_dir = dest_dir / "images"
            img_dir.mkdir(exist_ok=True)
            for i, fname in enumerate(files, start=1):
                url = f"{base}/data/{chapter_hash}/{fname}"
                (img_dir / fname).write_bytes(self.fetcher.get(url))
                lines.append(f"![page {i}](images/{fname})")
        else:
            lines.append("(images non disponibles pour ce chapitre pour le moment)")
        (dest_dir / "raw.html").write_text("\n".join(lines), encoding="utf-8")
        (dest_dir / "meta.json").write_text(
            json.dumps(
                {"source": self.id, "chapter": chapter.to_dict(), "pages": pages},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
