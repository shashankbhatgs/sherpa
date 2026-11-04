"""Pre-seeded interview questions in ~/.sherpa/question_bank/*.md."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sherpa.workspace import workspace_paths


class QuestionNotFoundError(LookupError):
    """No question file with the requested name exists."""


@dataclass
class Question:
    name: str  # file stem, e.g. "lld_parking_lot"
    title: str  # first "# " heading, e.g. "LLD: Parking Lot"
    text: str


def list_questions() -> list[Question]:
    """All questions in the bank, sorted by name."""
    bank = workspace_paths()["question_bank"]
    if not bank.exists():
        return []
    return [_read(path) for path in sorted(bank.glob("*.md"))]


def load_question(name: str) -> Question:
    """Load one question by file stem; raises QuestionNotFoundError with the available names."""
    path = workspace_paths()["question_bank"] / f"{name}.md"
    if not path.is_file():
        available = ", ".join(q.name for q in list_questions()) or "none"
        raise QuestionNotFoundError(f"No question named {name!r}. Available: {available}.")
    return _read(path)


def _read(path: Path) -> Question:
    text = path.read_text(encoding="utf-8")
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
    return Question(name=path.stem, title=title, text=text.strip())
