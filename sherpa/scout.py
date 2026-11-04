from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from sherpa.agent_loop import parse_json_object, run_tool_loop
from sherpa.config import get_config
from sherpa.llm_router import get_provider
from sherpa.workspace import workspace_paths


def resume_index() -> str:
    path = workspace_paths()["profile"]
    try:
        profile = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        profile = {}

    name = profile.get("name", "")
    years = experience_years(profile.get("experience_since"))
    if years is None:
        years = profile.get("years_experience", 0)
    competencies = ", ".join(profile.get("core_competencies", []))
    roles = ", ".join(profile.get("target_roles", []))
    return f"Name: {name}\nYears experience: {years}\nCore competencies: {competencies}\nTarget roles: {roles}"


def experience_years(since: object, today: date | None = None) -> int | None:
    """Whole years since a "YYYY-MM" start, rounded to the nearest year (2y10m -> 3); None if unset or invalid."""
    if not isinstance(since, str):
        return None
    try:
        start = datetime.strptime(since.strip(), "%Y-%m").date()
    except ValueError:
        return None
    today = today or date.today()
    months = (today.year - start.year) * 12 + (today.month - start.month)
    return max(0, (months + 6) // 12)  # half-up: 2y6m -> 3 (round() would give 2)


def fetch_full_resume() -> str:
    path = workspace_paths()["resume"]
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


FETCH_RESUME_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "fetch_full_resume",
        "description": "Fetch the full resume Markdown from ~/.sherpa/resume.md",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

SCOUT_REGISTRY: dict[str, Callable] = {
    "fetch_full_resume": fetch_full_resume,
}


# ── Outreach engine ───────────────────────────────────────────────────────────

LINKEDIN_NOTE_LIMIT = 300

_OUTREACH_PROMPT = """\
You write short, specific cold outreach messages for a software engineer who is job hunting.

Candidate summary (Resume Index):
{index}

Steps:
1. Read the job post. Decide whether the role aligns with the candidate's competencies and target roles.
2. If it aligns, call the fetch_full_resume tool and ground the message in concrete projects from the resume \
that match the job's requirements.
3. Never invent employers, projects, skills, or metrics that are not in the summary or the resume.
4. Never state the candidate's preferences or circumstances (location, relocation, in-office or remote, \
availability, notice period, salary, visa) unless they appear in the summary or the resume.
5. When mentioning experience, use "Years experience" from the summary as given.

Return ONLY a JSON object with these keys:
- company (string): the hiring company
- role (string): the job title
- aligned (boolean): true if the role fits the candidate
- linkedin_note (string): a LinkedIn connection note, at most {limit} characters, written in first person, \
that answers "why are you a good fit for this role?" by showing it, not claiming it:
  * open with what kind of engineer the candidate is, framed with the tech stack and responsibilities that \
appear in BOTH the job post and the resume (e.g. "Backend engineer building Java/Kotlin services on Postgres and AWS")
  * back it with one or two concrete proofs from the resume that match what the job asks for, preferring \
ownership and measurable impact (e.g. "owned an ingestion pipeline end to end, 300 MB to 3 GB uploads")
  * no dead prose: every phrase must carry a fact the reader did not have. Cut greetings beyond "Hi", \
self-assessments, and enthusiasm, such as "strong fit", "great fit", "good fit", "love to be considered", \
"love to connect", "caught my eye", "excited", "passionate", "I came across"
- email_subject (string): a specific subject line, under 10 words
- email_body (string): a plain-text email under 150 words. End with this ask, in your own words: {closing_ask}

No markdown fences, no explanation."""

_SHORTEN_PROMPT = (
    "The linkedin_note is {length} characters. Rewrite it to at most {limit} characters, "
    "keeping the most specific point. Return the same JSON object with only linkedin_note changed."
)


class OutreachError(RuntimeError):
    """The model did not return a usable outreach draft."""


@dataclass
class OutreachDraft:
    company: str
    role: str
    aligned: bool
    used_full_resume: bool
    linkedin_note: str
    email_subject: str
    email_body: str
    path: Path


def draft_outreach(job_post: str) -> OutreachDraft:
    """Draft a LinkedIn note and cold email for a pasted job post, and save it to ~/.sherpa/outreach/.

    The model only sees the Resume Index up front; it pulls the full resume through the
    fetch_full_resume tool when it judges the role a match.
    """
    job_post = job_post.strip()
    if not job_post:
        raise ValueError("Job post is empty.")

    fetched: list[bool] = []

    def _tracked_fetch() -> str:
        fetched.append(True)
        return fetch_full_resume()

    provider = get_provider("default")
    messages = [
        {"role": "system", "content": _OUTREACH_PROMPT.format(
            index=resume_index(), limit=LINKEDIN_NOTE_LIMIT, closing_ask=_closing_ask(),
        )},
        {"role": "user", "content": f"Job post:\n\n{job_post}"},
    ]
    raw = run_tool_loop(
        provider=provider,
        messages=messages,
        tools=[FETCH_RESUME_TOOL],
        registry={"fetch_full_resume": _tracked_fetch},
    )
    data = parse_json_object(raw)

    note = _text(data, "linkedin_note")
    if len(note) > LINKEDIN_NOTE_LIMIT:
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": _SHORTEN_PROMPT.format(length=len(note), limit=LINKEDIN_NOTE_LIMIT)},
        ]
        retry = parse_json_object(run_tool_loop(provider=provider, messages=retry_messages, tools=[], registry={}))
        shorter = _text(retry, "linkedin_note")
        if shorter and len(shorter) < len(note):
            note = shorter
        note = _trim_to_limit(note, LINKEDIN_NOTE_LIMIT)

    email_body = _text(data, "email_body")
    if not note or not email_body:
        raise OutreachError("The model did not return a LinkedIn note and email. Try again.")

    company = _text(data, "company") or "Unknown company"
    draft = OutreachDraft(
        company=company,
        role=_text(data, "role"),
        aligned=data.get("aligned") is True,
        used_full_resume=bool(fetched),
        linkedin_note=note,
        email_subject=_text(data, "email_subject") or f"Interest in the role at {company}",
        email_body=email_body,
        path=_next_outreach_path(),
    )
    _write_outreach(draft, job_post)
    return draft


_DEFAULT_CLOSING_ASK = (
    "ask them to consider the candidate for this role, or to point them to the right person; "
    "do not ask for a call or meeting"
)


def _closing_ask() -> str:
    cfg = get_config().get("outreach") or {}
    ask = cfg.get("closing_ask") if isinstance(cfg, dict) else None
    return ask.strip() if isinstance(ask, str) and ask.strip() else _DEFAULT_CLOSING_ASK


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _trim_to_limit(text: str, limit: int) -> str:
    """Cut text to at most `limit` chars, preferring a sentence end, then a word boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    sentence_end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if sentence_end >= limit // 2:
        return head[: sentence_end + 1]
    if text[limit] in " \n":
        return head.rstrip()
    word_end = head.rfind(" ")
    return head[:word_end].rstrip(" ,;:-") if word_end > 0 else head


def latest_outreach() -> OutreachDraft | None:
    """The most recently saved draft in ~/.sherpa/outreach/, or None if there are none or it is unreadable."""
    outreach_dir = workspace_paths()["outreach"]
    paths = list(outreach_dir.glob("outreach_*_*.md")) if outreach_dir.exists() else []
    if not paths:
        return None
    return _read_outreach(max(paths, key=_outreach_sort_key))


def _outreach_sort_key(path: Path) -> tuple[str, int]:
    _, day, number = path.stem.split("_", 2)
    return day, int(number) if number.isdigit() else 0


def _read_outreach(path: Path) -> OutreachDraft | None:
    text = path.read_text(encoding="utf-8")
    header, _, body = text.removeprefix("---\n").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    sections = _markdown_sections(body)
    subject, _, email_body = sections.get("Email", "").partition("\n\n")
    note = sections.get("LinkedIn note", "")
    if not note or not email_body:
        return None
    return OutreachDraft(
        company=meta.get("company", ""),
        role=meta.get("role", ""),
        aligned=meta.get("aligned") == "true",
        used_full_resume=meta.get("used_full_resume") == "true",
        linkedin_note=note,
        email_subject=subject.removeprefix("Subject: ").strip(),
        email_body=email_body.strip(),
        path=path,
    )


def _markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return {name: content.strip() for name, content in sections.items()}


def _next_outreach_path() -> Path:
    outreach_dir = workspace_paths()["outreach"]
    outreach_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    taken = [
        int(p.stem.rsplit("_", 1)[1])
        for p in outreach_dir.glob(f"outreach_{date_str}_*.md")
        if p.stem.rsplit("_", 1)[1].isdigit()
    ]
    return outreach_dir / f"outreach_{date_str}_{max(taken, default=0) + 1}.md"


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _write_outreach(draft: OutreachDraft, job_post: str) -> None:
    content = (
        "---\n"
        f"company: {_one_line(draft.company)}\n"
        f"role: {_one_line(draft.role)}\n"
        f"aligned: {str(draft.aligned).lower()}\n"
        f"used_full_resume: {str(draft.used_full_resume).lower()}\n"
        f"created_at: {datetime.now().isoformat(timespec='seconds')}\n"
        "---\n\n"
        f"## LinkedIn note\n\n{draft.linkedin_note}\n\n"
        f"## Email\n\nSubject: {_one_line(draft.email_subject)}\n\n{draft.email_body}\n\n"
        f"## Job post\n\n{job_post}\n"
    )
    draft.path.write_text(content, encoding="utf-8")
