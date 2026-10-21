"""The principle library: every rule the tool applies comes from here.

Each book lives in ``knowledge/<id>.json`` (see ``knowledge/SCHEMA.md``).
Adding a book is a matter of dropping another JSON file in that folder; the
engine refuses to cite a principle id that is not in the library, so every
decision it prints is traceable to a page of a book you loaded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "knowledge"


@dataclass(frozen=True)
class Principle:
    id: str
    book_id: str
    chapter: str
    page: int | None
    category: str
    principle: str
    quote: str = ""
    applies_to: tuple[str, ...] = ()
    rule: dict = field(default_factory=dict, hash=False, compare=False)
    machine_checkable: bool = False

    def citation(self, library: "Library") -> str:
        book = library.books.get(self.book_id, {})
        title = book.get("title", self.book_id)
        page = f", p. {self.page}" if self.page else ""
        return f"[{self.id}] {title} — \"{self.chapter}\"{page}"


class UnknownPrinciple(KeyError):
    pass


class Library:
    def __init__(self, directory: str | Path = DEFAULT_DIR):
        self.directory = Path(directory)
        self.books: dict[str, dict] = {}
        self.principles: dict[str, Principle] = {}
        for path in sorted(self.directory.glob("*.json")):
            self._load(path)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        book = data["book"]
        book_id = book["id"]
        self.books[book_id] = book
        for p in data["principles"]:
            chapter = p.get("chapter", "")
            pid = p["id"]
            if pid in self.principles:
                raise ValueError(f"duplicate principle id {pid} in {path.name}")
            self.principles[pid] = Principle(
                id=pid,
                book_id=book_id,
                chapter=chapter,
                page=p.get("page"),
                category=p.get("category", ""),
                principle=p.get("principle", ""),
                quote=p.get("quote", ""),
                applies_to=tuple(p.get("applies_to", ())),
                rule=p.get("rule") or {},
                machine_checkable=bool(p.get("machine_checkable")),
            )

    def __contains__(self, pid: str) -> bool:
        return pid in self.principles

    def __len__(self) -> int:
        return len(self.principles)

    def get(self, pid: str) -> Principle:
        try:
            return self.principles[pid]
        except KeyError:
            raise UnknownPrinciple(pid) from None

    def search(self, text: str = "", category: str | None = None,
               applies_to: str | None = None, book: str | None = None) -> list[Principle]:
        words = text.lower().split()
        out = []
        for p in self.principles.values():
            if category and p.category != category:
                continue
            if book and p.book_id.lower() != book.lower():
                continue
            if applies_to and applies_to not in p.applies_to and "any" not in p.applies_to:
                continue
            hay = " ".join([p.id, p.chapter, p.principle, p.quote,
                            json.dumps(p.rule)]).lower()
            if all(w in hay for w in words):
                out.append(p)
        return out
