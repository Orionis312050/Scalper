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
    "keiyoushi.github.io": "index d'extensions Keiyoushi pointant vers des sites de scanlation non autorisés",
    "keiyoushi.com": "dépôt d'extensions Keiyoushi pointant vers des sites de scanlation non autorisés",
}
BLOCKED_SUBSTRINGS = ("mangas-origines", "keiyoushi.github.io", "keiyoushi.com")

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

# Sources personnalisées déclarées par l'utilisateur (sources.toml).
# La liste de blocage a TOUJOURS priorité sur ce registre : aucune config ne
# peut autoriser un domaine bloqué.
CUSTOM_SOURCES: dict[str, dict[str, str]] = {}


def register_custom_sources(entries: dict[str, dict]) -> None:
    """Enregistre des sources personnalisées. Champ 'license' obligatoire."""
    for domain, cfg in entries.items():
        lic = str(cfg.get("license", "")).strip()
        if not lic:
            raise ValueError(
                f"source '{domain}': le champ 'license' est obligatoire "
                "(API officielle, CC, domaine public ou contenu propre)"
            )
        CUSTOM_SOURCES[domain.lower()] = {"license": lic, "kind": str(cfg.get("kind", "html"))}


def load_config(path: str) -> None:
    """Charge un registre de sources TOML : [source."domaine"] license = "..." kind = "..."."""
    import tomllib
    from pathlib import Path

    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    register_custom_sources(data.get("source", {}))


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


DERIVATIVE_LICENSE_MARKERS = (
    "domaine public", "public domain", "creative commons", "cc by", "cc0",
    "contenu propre", "own content",
)


def allows_derivatives(license_str: str) -> bool:
    """True si la licence autorise les œuvres dérivées (ex. modifier les images :
    OCR + remplacement du texte = image modifiée). Réservé domaine public / CC / contenu propre."""
    l = (license_str or "").lower()
    return any(m in l for m in DERIVATIVE_LICENSE_MARKERS)


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

    trusted = TRUSTED_SOURCES.get(domain) or CUSTOM_SOURCES.get(domain)
    if trusted is None:
        return Verdict(
            source=source,
            verdict="blocked",
            reasons=[
                "source non enregistrée : déclarez-la dans sources.toml (champ 'license' obligatoire) "
                "après vérification manuelle (robots.txt, CGU, licence CC/domaine public/API officielle)"
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
