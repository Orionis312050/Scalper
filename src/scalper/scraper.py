"""manga-scraper : récupération respectueuse (rate limiting, backoff, reprise)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "Scalper/1.0 (+https://github.com/Orionis312050/Scalper)"


class FetchError(Exception):
    pass


class RespectfulFetcher:
    """HTTP GET avec >= min_delay entre requêtes, 1 concurrence, backoff sur 429/5xx."""

    def __init__(self, min_delay: float = 1.0, max_retries: int = 3, timeout: float = 30.0) -> None:
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str) -> bytes:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise FetchError(f"HTTP {e.code} pour {url}") from e
            except urllib.error.URLError as e:
                raise FetchError(f"erreur réseau pour {url}: {e}") from e
        raise FetchError(f"échec après {self.max_retries} tentatives pour {url}: {last_err}")


class RunState:
    """État resumable d'un run (state.json)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"fetched": [], "done": []}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def is_fetched(self, chapter_id: str) -> bool:
        return chapter_id in self.data["fetched"]

    def mark_fetched(self, chapter_id: str) -> None:
        if chapter_id not in self.data["fetched"]:
            self.data["fetched"].append(chapter_id)
        self.save()

    def mark_done(self, chapter_id: str) -> None:
        if chapter_id not in self.data["done"]:
            self.data["done"].append(chapter_id)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


def chapter_dir(out_root: Path, source: str, series: str, chapter_id: str) -> Path:
    return out_root / "raw" / source / series / chapter_id
