from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import ChapterRef, Series


class SourceAdapter(Protocol):
    """Contrat d'un adaptateur de source légale."""

    id: str
    license: str

    def list_series(self) -> list[Series]: ...

    def list_chapters(self, series_id: str) -> list[ChapterRef]: ...

    def fetch_chapter(self, chapter: ChapterRef, dest_dir: Path) -> None:
        """Télécharge le chapitre brut (HTML + images) dans dest_dir."""
        ...
