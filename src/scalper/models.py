from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

BLOCK_TYPES = ("dialogue", "narration", "caption", "onomatopoeia", "image")


@dataclass
class Series:
    id: str
    title: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChapterRef:
    id: str
    number: str
    title: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Block:
    index: int
    type: str
    text: str = ""
    image: str | None = None

    @property
    def placeholder(self) -> str | None:
        if self.type == "image":
            return f"[IMG:{self.index}]"
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Block":
        return cls(
            index=int(d["index"]),
            type=d["type"],
            text=d.get("text", ""),
            image=d.get("image"),
        )


@dataclass
class Chapter:
    meta: dict[str, Any] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

    def text_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.type != "image"]

    def placeholders(self) -> list[str]:
        return [b.placeholder for b in self.blocks if b.placeholder]

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.text_blocks())

    def to_dict(self) -> dict[str, Any]:
        return {"meta": self.meta, "blocks": [b.to_dict() for b in self.blocks]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chapter":
        return cls(
            meta=dict(d.get("meta", {})),
            blocks=[Block.from_dict(b) for b in d.get("blocks", [])],
        )
