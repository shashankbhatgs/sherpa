from __future__ import annotations

import re
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from sherpa.agent_loop import run_tool_loop
from sherpa.code_review import CodeReview, CodeReviewError, evaluate_code, extract_code
from sherpa.llm_router import get_provider
from sherpa.question_bank import Question, QuestionNotFoundError, load_question
from sherpa.scout import FETCH_RESUME_TOOL, SCOUT_REGISTRY
from sherpa.workspace import workspace_paths

# telemetry imported lazily inside start() to avoid circular imports at module load


class State(str, Enum):
    CLARIFYING = "CLARIFYING"
    HIGH_LEVEL = "HIGH_LEVEL"
    DEEP_DIVE = "DEEP_DIVE"
    FEEDBACK = "FEEDBACK"


_NEXT_STATE: dict[State, Optional[State]] = {
    State.CLARIFYING: State.HIGH_LEVEL,
    State.HIGH_LEVEL: State.DEEP_DIVE,
    State.DEEP_DIVE: State.FEEDBACK,
    State.FEEDBACK: None,
}

_EXIT_CRITERIA: dict[State, str] = {
    State.CLARIFYING: "the user specified DAU, QPS, and latency SLA requirements",
    State.HIGH_LEVEL: "the user described API endpoints, system components, and their boundaries",
    State.DEEP_DIVE: "the user addressed distributed synchronization, single points of failure, and failover",
}

_PROMPT_CLARIFYING = """\
You are a senior staff engineer conducting a technical system design interview.
You are in the CLARIFYING phase. Your job is to probe the candidate for concrete scale requirements before any design begins.
Ask about: Daily Active Users (DAU), requests per second (QPS), and latency SLAs (p99 target).
Do not allow the candidate to jump into design until all three baselines are established.
Be direct, professional, and concise. Ask one targeted question at a time."""

_PROMPT_HIGH_LEVEL = """\
You are a senior staff engineer conducting a technical system design interview.
Scale requirements are now established. You are in the HIGH_LEVEL design phase.
Guide the candidate to articulate: API endpoint signatures, major system components, and the boundaries between them.
Encourage the use of text-based component diagrams or Mermaid syntax.
Probe for missing components and unclear boundaries. Ask one targeted question at a time."""

_PROMPT_DEEP_DIVE = """\
You are a senior staff engineer conducting a technical system design interview.
The high-level design is established. You are in the DEEP_DIVE phase.
Drill into hard distributed systems problems: data consistency and synchronization anomalies,
single points of failure, concurrency and race conditions, failover and recovery strategies.
Push the candidate to justify every design choice with engineering reasoning. Be rigorous."""

_PROMPT_FEEDBACK = """\
You are a senior staff engineer who just completed a technical interview. Step out of interviewer character.
Evaluate the candidate's performance across the SDE2 rubric:
1. Requirements clarity (did they nail scale baselines?)
2. Design correctness (is the architecture sound?)
3. Depth of reasoning (did they explain trade-offs?)
4. Handling of failure scenarios (SPOF, failover, consistency?)
5. Communication (clear, structured, concise?)

Give a structured written evaluation with a score (1-5) per dimension and 2-3 concrete improvement suggestions."""

_PROMPT_ADVISORY = """\
You are a supportive interview coach. The candidate has passed their daily study limit, so the burnout guard is on.
Do not ask new technical questions, start new problems, or push the design further.
Briefly acknowledge what they just said, give at most one short takeaway from today's session,
and encourage them to stop and come back after the cooldown. Keep it under 80 words and warm, not preachy."""

ADVISORY_MARKER = "advisory"
_REVIEW_ROLE = "review"

_REVIEW_CONTEXT = """\
A rubric review of the candidate's latest code is below. Use it to probe the weakest points with follow-up
questions. Do not read the review out or list every issue at once.

{review}"""

_STATE_PROMPTS: dict[State, str] = {
    State.CLARIFYING: _PROMPT_CLARIFYING,
    State.HIGH_LEVEL: _PROMPT_HIGH_LEVEL,
    State.DEEP_DIVE: _PROMPT_DEEP_DIVE,
    State.FEEDBACK: _PROMPT_FEEDBACK,
}


def _next_session_path(interviews_dir: Path, date_str: str) -> Path:
    existing = list(interviews_dir.glob(f"session_{date_str}_*.md"))
    n = len(existing) + 1
    return interviews_dir / f"session_{date_str}_{n}.md"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body_text). Body starts after the closing ---."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm, body


def _parse_turns(body: str) -> list[dict]:
    """Parse ## user / ## assistant blocks into message dicts. Headers may have an optional timestamp."""
    messages: list[dict] = []
    pattern = re.compile(rf"^## (user|assistant|{_REVIEW_ROLE})(?:[ \t]+\S+)*[ \t]*$", re.MULTILINE)
    parts = pattern.split(body)
    # parts alternates: [preamble, role, content, role, content, ...]
    i = 1
    while i + 1 < len(parts):
        role = parts[i].strip()
        content = parts[i + 1].strip()
        if content and role != _REVIEW_ROLE:  # reviews are per-turn context, not chat history
            messages.append({"role": role, "content": content})
        i += 2
    return messages


def _write_frontmatter(fm: dict[str, str]) -> str:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def _check_exit_criteria(state: State, messages: list[dict]) -> bool:
    criteria = _EXIT_CRITERIA.get(state)
    if not criteria:
        return False
    conversation = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages if m["role"] in ("user", "assistant")
    )
    check_prompt = (
        f"Based only on the conversation below, answer with a single word: 'yes' if {criteria}, "
        f"otherwise 'no'.\n\n{conversation}"
    )
    provider = get_provider("interviewer")
    response = provider.chat([{"role": "user", "content": check_prompt}])
    answer = (response["message"]["content"] or "").strip().lower()
    return answer.startswith("yes")


def _load_question_if_present(name: str | None) -> Question | None:
    if not name:
        return None
    try:
        return load_question(name)
    except QuestionNotFoundError:
        print(f"sherpa interviewer: question {name!r} no longer in the bank; resuming without it", file=sys.stderr)
        return None


class InterviewSession:
    def __init__(
        self,
        path: Path,
        topic: str,
        state: State,
        started_at: str,
        messages: list[dict],
        question: Question | None = None,
    ) -> None:
        self.path = path
        self.topic = topic
        self.state = state
        self.started_at = started_at
        self.messages = messages
        self.question = question
        self.last_reply_advisory = False
        self.last_review: CodeReview | None = None

    @classmethod
    def start(cls, topic: str | None = None, question: str | None = None) -> "InterviewSession":
        """Start a session on a free-text topic, or on a question-bank question by name."""
        if not (topic and topic.strip()) and not question:
            raise ValueError("Give a topic or a question name.")
        from sherpa.telemetry import is_blocked, burnout_status
        if is_blocked():
            status = burnout_status()
            when = f"after {status['unblocks_at']}" if status["unblocks_at"] else "after the cooldown"
            raise RuntimeError(
                f"Burnout guard: {status['daily_minutes']:.0f} active minutes today "
                f"(threshold: {status['threshold']} min). Start a new session {when}."
            )
        loaded = load_question(question) if question else None
        topic = " ".join((topic or loaded.title).split())  # type: ignore[union-attr]

        interviews_dir = workspace_paths()["interviews"]
        interviews_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        started_at = datetime.now().isoformat(timespec="seconds")
        path = _next_session_path(interviews_dir, date_str)

        session = cls(path=path, topic=topic, state=State.CLARIFYING, started_at=started_at, messages=[], question=loaded)
        session.messages = [session._system_message()]
        session._rewrite_frontmatter()
        return session

    @classmethod
    def resume(cls, path: Path) -> "InterviewSession":
        text = path.read_text()
        fm, body = _parse_frontmatter(text)

        state = State(fm.get("state", State.CLARIFYING.value))
        topic = fm.get("topic", "")
        started_at = fm.get("started_at", "")
        question = _load_question_if_present(fm.get("question"))

        session = cls(path=path, topic=topic, state=state, started_at=started_at, messages=[], question=question)
        session.messages = [session._system_message()] + _parse_turns(body)
        return session

    def _system_message(self) -> dict:
        content = _STATE_PROMPTS[self.state]
        if self.question is not None:
            content += f"\n\nThe interview problem (share details with the candidate only as they ask):\n\n{self.question.text}"
        return {"role": "system", "content": content}

    def turn(self, user_message: str) -> str:
        """Send one candidate message and return the interviewer's reply.

        While the burnout guard is active, the reply comes from an advisory coach persona,
        the state machine does not advance, and `last_reply_advisory` is set.
        """
        from sherpa.telemetry import is_blocked

        advisory = is_blocked()
        self._append_block("user", user_message)
        self.messages.append({"role": "user", "content": user_message})

        call_messages = list(self.messages)
        if advisory:
            call_messages[0] = {"role": "system", "content": _PROMPT_ADVISORY}

        self.last_review = None if advisory else self._review_code_in(user_message)
        if self.last_review is not None:
            review_md = self.last_review.as_markdown()
            self._append_block(_REVIEW_ROLE, review_md)
            call_messages.append({"role": "system", "content": _REVIEW_CONTEXT.format(review=review_md)})

        response = run_tool_loop(
            provider=get_provider("interviewer"),
            messages=call_messages,
            tools=[] if advisory else [FETCH_RESUME_TOOL],
            registry={} if advisory else SCOUT_REGISTRY,
        )

        self._append_block("assistant", response, marker=ADVISORY_MARKER if advisory else None)
        self.messages.append({"role": "assistant", "content": response})
        self.last_reply_advisory = advisory

        if not advisory:
            self._maybe_advance()
        return response

    def _review_code_in(self, user_message: str) -> CodeReview | None:
        code = extract_code(user_message)
        if code is None:
            return None
        try:
            return evaluate_code(code, problem=self.question.text if self.question else self.topic)
        except CodeReviewError as exc:
            print(f"sherpa interviewer: code review skipped ({exc})", file=sys.stderr)
            return None

    def _append_block(self, role: str, content: str, marker: str | None = None) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        header = f"## {role} {ts}" + (f" {marker}" if marker else "")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{header}\n{content}\n")

    def _maybe_advance(self) -> bool:
        next_state = _NEXT_STATE.get(self.state)
        if next_state is None:
            return False
        if _check_exit_criteria(self.state, self.messages):
            self.state = next_state
            self._rewrite_frontmatter()
            self.messages[0] = self._system_message()
            return True
        return False

    def _rewrite_frontmatter(self) -> None:
        body = _parse_frontmatter(self.path.read_text())[1] if self.path.exists() else ""
        fm = {"state": self.state.value, "topic": self.topic, "started_at": self.started_at}
        if self.question is not None:
            fm["question"] = self.question.name
        self.path.write_text(_write_frontmatter(fm) + body)
