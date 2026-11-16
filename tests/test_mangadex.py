import json
import unittest
from urllib.parse import urlparse, parse_qs

from scalper.sources.mangadex import MangaDexAdapter

GROUP_UUID = "123e4567-e89b-42d3-a456-426614174000"


class StubFetcher:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        for key, payload in self.responses.items():
            if key in url:
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"URL inattendue: {url}")


def make_adapter() -> tuple[MangaDexAdapter, StubFetcher]:
    stub = StubFetcher(
        {
            "/group": {"data": [{"id": GROUP_UUID, "attributes": {"name": "Keiyoushi"}}]},
            "/manga/m1/feed": {
                "data": [
                    {"id": "ch-1", "attributes": {"chapter": "1", "title": "Start"}},
                    {"id": "ch-2", "attributes": {"chapter": "2", "title": None}},
                ]
            },
        }
    )
    return MangaDexAdapter(fetcher=stub), stub


class TestMangaDexGroupFilter(unittest.TestCase):
    def test_resolve_group_by_name(self):
        adapter, stub = make_adapter()
        self.assertEqual(adapter.resolve_group("Keiyoushi"), GROUP_UUID)
        self.assertTrue(any("/group?" in u and "name=Keiyoushi" in u for u in stub.urls))

    def test_resolve_group_uuid_passthrough(self):
        adapter, _ = make_adapter()
        self.assertEqual(adapter.resolve_group(GROUP_UUID), GROUP_UUID)

    def test_list_chapters_with_group_filter(self):
        adapter, stub = make_adapter()
        chapters = adapter.list_chapters("m1", group="Keiyoushi")
        self.assertEqual([c.number for c in chapters], ["1", "2"])
        feed_url = next(u for u in stub.urls if "/manga/m1/feed" in u)
        qs = parse_qs(urlparse(feed_url).query)
        self.assertEqual(qs.get("groups[]"), [GROUP_UUID])
        self.assertEqual(qs.get("translatedLanguage[]"), ["en"])

    def test_list_chapters_without_group(self):
        adapter, stub = make_adapter()
        adapter.list_chapters("m1", lang=None)
        feed_url = next(u for u in stub.urls if "/manga/m1/feed" in u)
        qs = parse_qs(urlparse(feed_url).query)
        self.assertNotIn("groups[]", qs)
        self.assertNotIn("translatedLanguage[]", qs)


if __name__ == "__main__":
    unittest.main()
