"""Adaptateur 'demo' : fixture locale (texte inventé, domaine public).

Permet de faire tourner le pipeline de bout en bout hors ligne.
"""

from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path

from ..models import ChapterRef, Series


class DemoAdapter:
    id = "demo"
    license = "fixture de test interne (texte inventé, domaine public)"

    def _data_dir(self) -> Path:
        return Path(str(resources.files("scalper") / "data" / "demo"))

    def _index(self) -> dict:
        return json.loads((self._data_dir() / "series.json").read_text(encoding="utf-8"))

    def list_series(self) -> list[Series]:
        idx = self._index()
        return [Series(id=s["id"], title=s["title"], url=s["url"], license=self.license) for s in idx["series"]]

    def list_chapters(self, series_id: str) -> list[ChapterRef]:
        idx = self._index()
        chapters = idx["chapters"].get(series_id, [])
        return [
            ChapterRef(id=c["id"], number=c["number"], title=c["title"], url=f"demo://{series_id}/{c['id']}")
            for c in chapters
        ]

    def fetch_chapter(self, chapter: ChapterRef, dest_dir: Path) -> None:
        idx = self._index()
        src_file = None
        for c in idx["chapters"].get(chapter.id.rsplit("-ch", 1)[0] if "-ch" in chapter.id else "", []):
            if c["id"] == chapter.id:
                src_file = self._data_dir() / c["file"]
        if src_file is None:
            for chapters in idx["chapters"].values():
                for c in chapters:
                    if c["id"] == chapter.id:
                        src_file = self._data_dir() / c["file"]
        if src_file is None or not src_file.exists():
            raise FileNotFoundError(f"chapitre de démo introuvable: {chapter.id}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, dest_dir / "raw.html")
        (dest_dir / "meta.json").write_text(
            json.dumps(
                {"source": self.id, "license": self.license, "chapter": chapter.to_dict()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
