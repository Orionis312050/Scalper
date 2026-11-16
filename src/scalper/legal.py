"""legal-source-check : valide qu'une source est légale avant tout scraping.

Règles bloquantes :
- domaine dans la liste de blocage (sites de scanlation non autorisés) -> blocked
- domaine non enregistré comme source de confiance -> blocked (ajout manuel requis)
- robots.txt interdit les chemins ciblés -> blocked
- aucun contournement de paywall/captcha/authentification n'est jamais tenté
"""

from __future__ import annotations

import urllib.robotparser
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

USER_AGENT = "Scalper/1.0 (+https://github.com/Orionis312050/Scalper)"

BLOCKED_DOMAINS: dict[str, str] = {
    "mangas-origines.fr": "site de scanlation non autorisé (contrefaçon de mangas commerciaux)",
}
BLOCKED_SUBSTRINGS = ("mangas-origines",)

TRUSTED_SOURCES: dict[str, dict[str, str]] = {
    "api.mangadex.org": {
        "license": "API officielle MangaDex (données publiques ; images selon licence de l'œuvre)",
        "kind": "official-api",
    },
    "demo": {
        "license": "fixture de test interne (texte inventé, domaine public)",
        "kind": "local-fixture",
    },
}


@dataclass
class Verdict:
    source: str
    verdict: str  # "allowed" | "blocked"
    reasons: list[str]
    license: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def domain_of(url_or_id: str) -> str:
    if "://" not in url_or_id:
        return url_or_id.lower()
    return (urlparse(url_or_id).netloc or url_or_id).lower()


def _fetch_robots_txt(domain: str) -> str | None:
    url = f"https://{domain}/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def robots_allow(domain: str, path: str = "/") -> tuple[bool, str]:
    """Retourne (autorisé, note). Si robots.txt est inaccessible, le défaut du
    standard robots.txt s'applique : autorisé, avec une note."""
    content = _fetch_robots_txt(domain)
    if content is None:
        return True, "robots.txt inaccessible : défaut autorisé (standard robots.txt)"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(content.splitlines())
    ok = rp.can_fetch(USER_AGENT, f"https://{domain}{path}")
    return ok, "robots.txt vérifié" if ok else f"robots.txt interdit le chemin {path}"


def check_source(source: str, path: str = "/") -> Verdict:
    """Vérifie une source (id local 'demo' ou URL/domaine)."""
    domain = domain_of(source)

    for sub in BLOCKED_SUBSTRINGS:
        if sub in domain:
            return Verdict(
                source=source,
                verdict="blocked",
                reasons=[f"liste de blocage : {BLOCKED_DOMAINS.get(domain, sub)}"],
                license="",
            )

    trusted = TRUSTED_SOURCES.get(domain)
    if trusted is None:
        return Verdict(
            source=source,
            verdict="blocked",
            reasons=[
                "source non enregistrée : ajoutez-la manuellement dans TRUSTED_SOURCES "
                "avec sa licence après vérification (robots.txt, CGU, licence CC/domaine public/API officielle)"
            ],
            license="",
        )

    reasons: list[str] = []
    if trusted["kind"] != "local-fixture":
        ok, note = robots_allow(domain, path)
        reasons.append(note)
        if not ok:
            return Verdict(source=source, verdict="blocked", reasons=reasons, license=trusted["license"])

    return Verdict(
        source=source,
        verdict="allowed",
        reasons=reasons or ["source de confiance enregistrée"],
        license=trusted["license"],
    )
