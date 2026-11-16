from __future__ import annotations

from ..scraper import RespectfulFetcher
from .base import SourceAdapter
from .demo import DemoAdapter
from .mangadex import MangaDexAdapter


def get_adapter(source_id: str, fetcher: RespectfulFetcher | None = None) -> SourceAdapter:
    if source_id == "demo":
        return DemoAdapter()
    if source_id == "mangadex":
        return MangaDexAdapter(fetcher=fetcher)
    raise KeyError(
        f"adaptateur inconnu: {source_id}. Sources disponibles : demo, mangadex. "
        "Pour ajouter une source légale, voir docs/add-a-source.md."
    )


__all__ = ["SourceAdapter", "get_adapter", "DemoAdapter", "MangaDexAdapter"]
