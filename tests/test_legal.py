import unittest

from scalper import legal


class TestLegal(unittest.TestCase):
    def test_demo_allowed(self):
        v = legal.check_source("demo")
        self.assertTrue(v.allowed)

    def test_mangas_origines_blocked(self):
        for url in ("https://mangas-origines.fr/", "https://www.mangas-origines.fr/manga/x", "mangas-origines.fr"):
            v = legal.check_source(url)
            self.assertFalse(v.allowed, url)
            self.assertIn("blocage", v.reasons[0])

    def test_unknown_domain_blocked(self):
        v = legal.check_source("https://site-inconnu.example/manga")
        self.assertFalse(v.allowed)

    def test_mangadex_trusted(self):
        v = legal.check_source("api.mangadex.org")
        self.assertTrue(v.allowed)
        self.assertIn("MangaDex", v.license)

    def test_domain_of(self):
        self.assertEqual(legal.domain_of("https://WWW.Example.COM/x"), "www.example.com")
        self.assertEqual(legal.domain_of("demo"), "demo")


if __name__ == "__main__":
    unittest.main()
