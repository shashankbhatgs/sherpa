"""Semantic code evaluation against a fixed SDE2 rubric (spec Module 3)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sherpa.agent_loop import parse_json_object
from sherpa.llm_router import get_provider

SCORE_MIN, SCORE_MAX = 1, 5
_MAX_ISSUES = 5

_CODE_BLOCK = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)

_CODE_REVIEW_PROMPT = """\
You are a senior engineer reviewing a candidate's code in an SDE2 interview.
Evaluate ONLY the code given. Do not invent requirements beyond the problem statement.

Score each dimension from 1 (poor) to 5 (excellent):
- correctness: handles the stated behavior and edge cases (empty input, capacity 0, duplicates, overflow)
- thread_safety: safe under concurrent access, or clearly not needed for this problem (then score on whether that is stated)
- complexity: time and space match what the problem requires

Return ONLY a JSON object with these keys:
- correctness (integer 1-5)
- thread_safety (integer 1-5)
- complexity (integer 1-5)
- time_complexity (string, Big-O of the main operations, e.g. "get O(1), put O(1)")
- space_complexity (string, Big-O)
- issues (array of up to 5 short strings, each a concrete problem with the line or construct it refers to)
- summary (string, one sentence)

No markdown fences, no explanation."""


class CodeReviewError(RuntimeError):
    """The model did not return a usable code review."""


@dataclass
class CodeReview:
    correctness: int
    thread_safety: int
    complexity: int
    time_complexity: str
    space_complexity: str
    summary: str
    issues: list[str] = field(default_factory=list)

    def as_markdown(self) -> str:
        lines = [
            f"**Code review** - correctness {self.correctness}/5, "
            f"thread safety {self.thread_safety}/5, complexity {self.complexity}/5",
            f"Time: {self.time_complexity or 'n/a'} | Space: {self.space_complexity or 'n/a'}",
        ]
        lines += [f"- {issue}" for issue in self.issues]
        if self.summary:
            lines.append(self.summary)
        return "\n".join(lines)


def extract_code(text: str) -> str | None:
    """Return the fenced code blocks in text joined together, or None if there are none."""
    blocks = [b.strip() for b in _CODE_BLOCK.findall(text)]
    code = "\n\n".join(b for b in blocks if b)
    return code or None


def evaluate_code(code: str, problem: str = "") -> CodeReview:
    """Score code for correctness/edge cases, thread safety, and complexity."""
    if not code.strip():
        raise ValueError("No code to review.")

    user = f"Problem:\n{problem.strip() or 'Not stated; infer it from the code.'}\n\nCode:\n```\n{code}\n```"
    response = get_provider("interviewer").chat([
        {"role": "system", "content": _CODE_REVIEW_PROMPT},
        {"role": "user", "content": user},
    ])
    data = parse_json_object(response["message"].get("content") or "")

    scores = {key: _score(data.get(key)) for key in ("correctness", "thread_safety", "complexity")}
    missing = [key for key, value in scores.items() if value is None]
    if missing:
        raise CodeReviewError(f"The model did not score: {', '.join(missing)}. Try again.")

    raw_issues = data.get("issues")
    issues = [i.strip() for i in raw_issues if isinstance(i, str) and i.strip()] if isinstance(raw_issues, list) else []
    return CodeReview(
        **scores,
        time_complexity=_text(data, "time_complexity"),
        space_complexity=_text(data, "space_complexity"),
        summary=_text(data, "summary"),
        issues=issues[:_MAX_ISSUES],
    )


def _score(value: object) -> int | None:
    """Coerce a model score to an int clamped to the rubric range; None if it is not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().split("/")[0]
    try:
        number = round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return max(SCORE_MIN, min(SCORE_MAX, number))


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    return " ".join(value.split()) if isinstance(value, str) else ""
