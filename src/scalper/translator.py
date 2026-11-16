"""llm-translator : traduction via LLM avec glossaire et chunking."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .models import Block, Chapter

DEFAULT_MODEL = "mistral-small-latest"
MAX_CHUNK_CHARS = 6000

SYSTEM_PROMPT = """Tu es un traducteur littéraire professionnel de mangas/webtoons.
Traduis le texte vers {target_lang}. Règles :
1. Ne traduis jamais les placeholders [IMG:n] ni les URLs.
2. Respecte strictement le glossaire fourni pour les noms propres, lieux et termes récurrents.
3. Conserve le ton et le registre (dialogue vs narration).
4. Ne rajoute aucune explication ni commentaire : uniquement le texte traduit, un bloc par ligne.
5. Conserve la ponctuation et la casse des onomatopées autant que possible."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class MistralChatClient:
    """Client API Mistral (clé via MISTRAL_API_KEY, jamais écrite dans le code)."""

    def __init__(self, model: str | None = None, api_key: str | None = None, api_url: str = "https://api.mistral.ai/v1") -> None:
        self.model = model or os.environ.get("MISTRAL_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not self.api_key:
            raise RuntimeError("MISTRAL_API_KEY manquante (variable d'environnement requise)")
        self.api_url = api_url.rstrip("/")

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            f"{self.api_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()


class FakeLLM:
    """LLM déterministe hors ligne pour tests et démo (--mock-llm)."""

    DICT = {
        "the": "le", "a": "un", "an": "un", "and": "et", "is": "est", "was": "était",
        "of": "de", "to": "à", "in": "dans", "on": "sur", "with": "avec", "his": "son",
        "her": "sa", "he": "il", "she": "elle", "they": "ils", "it": "il", "his friends": "ses amis",
        "hero": "héros", "starts": "commence", "journey": "voyage", "chapter": "chapitre",
        "one": "un", "new": "nouveau", "life": "vie", "decides": "décide", "leave": "quitter",
        "village": "village", "find": "trouver", "treasure": "trésor", "travel": "voyagent",
        "through": "à travers", "mountains": "montagnes", "rivers": "rivières", "end": "fin",
        "day": "jour", "every": "chaque", "morning": "matin", "world": "monde", "full": "plein",
        "of wonders": "de merveilles", "bang": "BAM", "pow": "POW",
    }

    def complete(self, system: str, user: str) -> str:
        out_lines = []
        for line in user.splitlines():
            words = line.split(" ")
            out_lines.append(" ".join(self.DICT.get(w.lower().strip(".,!?"), w) for w in words))
        return "\n".join(out_lines)


def load_glossary(path: Path | None) -> dict[str, str]:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_glossary(path: Path, glossary: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")


def chunk_blocks(blocks: list[Block], max_chars: int = MAX_CHUNK_CHARS) -> list[list[Block]]:
    chunks: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for b in blocks:
        n = len(b.text) + 1
        if current and size + n > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(b)
        size += n
    if current:
        chunks.append(current)
    return chunks


def translate_chapter(
    chapter: Chapter,
    target_lang: str,
    glossary: dict[str, str],
    client: LLMClient,
) -> Chapter:
    text_blocks = chapter.text_blocks()
    image_blocks = [b for b in chapter.blocks if b.type == "image"]
    glossary_str = "\n".join(f"- {k} -> {v}" for k, v in sorted(glossary.items())) or "(vide)"
    system = SYSTEM_PROMPT.format(target_lang=target_lang)

    translated: dict[int, str] = {}
    for chunk in chunk_blocks(text_blocks):
        user = (
            f"Glossaire:\n{glossary_str}\n\nTexte à traduire ({len(chunk)} blocs, un par ligne) :\n"
            + "\n".join(b.text for b in chunk)
        )
        reply = client.complete(system, user)
        lines = [ln for ln in reply.splitlines() if ln.strip()]
        if len(lines) != len(chunk):
            lines = (lines + [""] * len(chunk))[: len(chunk)]
        for b, ln in zip(chunk, lines):
            translated[b.index] = ln.strip()

    new_blocks: list[Block] = []
    for b in chapter.blocks:
        if b.type == "image":
            new_blocks.append(Block(index=b.index, type="image", image=b.image))
        else:
            new_blocks.append(Block(index=b.index, type=b.type, text=translated.get(b.index, b.text)))

    meta = dict(chapter.meta)
    meta["language"] = target_lang
    meta["translated_from"] = chapter.meta.get("language", "unknown")
    return Chapter(meta=meta, blocks=new_blocks)
