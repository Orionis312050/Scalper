import unittest
from unittest import mock

from scalper import legal

ALLOW_ALL = "User-agent: *\nAllow: /\n"


class TestCustomSourceRegistry(unittest.TestCase):
    def setUp(self):
        legal.CUSTOM_SOURCES.clear()

    def tearDown(self):
        legal.CUSTOM_SOURCES.clear()

    def test_custom_source_with_license_allowed(self):
        with mock.patch.object(legal, "_fetch_robots_txt", return_value=ALLOW_ALL):
            legal.register_custom_sources({"exemple-cc.fr": {"license": "CC BY 4.0", "kind": "html"}})
            v = legal.check_source("https://exemple-cc.fr/manga/x")
            self.assertTrue(v.allowed)
            self.assertEqual(v.license, "CC BY 4.0")

    def test_custom_source_without_license_rejected(self):
        with self.assertRaises(ValueError):
            legal.register_custom_sources({"pirate.example": {"kind": "html"}})
        self.assertNotIn("pirate.example", legal.CUSTOM_SOURCES)

    def test_blocklist_wins_over_config(self):
        with mock.patch.object(legal, "_fetch_robots_txt", return_value=ALLOW_ALL):
            legal.register_custom_sources(
                {"mangas-origines.fr": {"license": "CC BY 4.0"}, "keiyoushi.github.io": {"license": "CC BY 4.0"}}
            )
            for url in ("https://mangas-origines.fr/", "https://keiyoushi.github.io/"):
                v = legal.check_source(url)
                self.assertFalse(v.allowed, url)

    def test_unknown_source_still_blocked(self):
        v = legal.check_source("https://inconnu.example/")
        self.assertFalse(v.allowed)

    def test_robots_disallow_blocks_custom_source(self):
        robots = "User-agent: *\nDisallow: /manga/\n"
        with mock.patch.object(legal, "_fetch_robots_txt", return_value=robots):
            legal.register_custom_sources({"exemple.fr": {"license": "Domaine public"}})
            v = legal.check_source("https://exemple.fr/manga/x", path="/manga/x")
            self.assertFalse(v.allowed)
            self.assertIn("robots.txt interdit", v.reasons[-1])

    def test_demo_still_allowed(self):
        self.assertTrue(legal.check_source("demo").allowed)


if __name__ == "__main__":
    unittest.main()
