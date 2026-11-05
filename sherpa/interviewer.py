from __future__ import annotations

import re
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path

from sherpa.agent_loop import run_tool_loop
from sherpa.code_review import CodeReview, CodeReviewError, evaluate_code, extract_code
from sherpa.llm_router import get_provider
from sherpa.question_bank import DEFAULT_KIND, Kind, Question, QuestionNotFoundError, load_question
from sherpa.scout import FETCH_RESUME_TOOL, SCOUT_REGISTRY
from sherpa.workspace import workspace_paths

# telemetry imported lazily inside start() to avoid circular imports at module load


class State(str, Enum):
    CLARIFYING = "CLARIFYING"
    HIGH_LEVEL = "HIGH_LEVEL"
    DEEP_DIVE = "DEEP_DIVE"
    APPROACH = "APPROACH"
    CLASS_DESIGN = "CLASS_DESIGN"
    CODE = "CODE"
    FEEDBACK = "FEEDBACK"


_FLOWS: dict[Kind, tuple[State, ...]] = {
    Kind.HLD: (State.CLARIFYING, State.HIGH_LEVEL, State.DEEP_DIVE, State.FEEDBACK),
    Kind.DSA: (State.CLARIFYING, State.APPROACH, State.CODE, State.FEEDBACK),
    Kind.LLD: (State.CLARIFYING, State.CLASS_DESIGN, State.CODE, State.FEEDBACK),
}

_EXIT_CRITERIA: dict[tuple[Kind, State], str] = {
    (Kind.HLD, State.CLARIFYING): "the user specified DAU, QPS, and latency SLA requirements",
    (Kind.HLD, State.HIGH_LEVEL): "the user described API endpoints, system components, and their boundaries",
    (Kind.HLD, State.DEEP_DIVE): "the user addressed distributed synchronization, single points of failure, and failover",
    (Kind.DSA, State.CLARIFYING): "the user pinned down the input constraints and the edge cases to handle",
    (Kind.DSA, State.APPROACH): "the user explained an approach along with its time and space complexity",
    (Kind.DSA, State.CODE): "the user shared a complete code solution and answered follow-up questions about it",
    (Kind.LLD, State.CLARIFYING): "the user pinned down the functional requirements and the main use cases",
    (Kind.LLD, State.CLASS_DESIGN): "the user described the core classes, their responsibilities, and their relationships",
    (Kind.LLD, State.CODE): "the user shared code for the core classes and answered follow-up questions about it",
}

_HLD_ROLE = "You are a senior staff engineer conducting a technical system design interview."
_DSA_ROLE = "You are a senior staff engineer conducting a coding (data structures and algorithms) interview."
_LLD_ROLE = "You are a senior staff engineer conducting a low-level (object-oriented) design interview."

_PROMPT_HLD_CLARIFYING = f"""\
{_HLD_ROLE}
You are in the CLARIFYING phase. Your job is to probe the candidate for concrete scale requirements before any design begins.
Ask about: Daily Active Users (DAU), requests per second (QPS), and latency SLAs (p99 target).
Do not allow the candidate to jump into design until all three baselines are established.
Be direct, professional, and concise. Ask one targeted question at a time."""

_PROMPT_HLD_HIGH_LEVEL = f"""\
{_HLD_ROLE}
Scale requirements are now established. You are in the HIGH_LEVEL design phase.
Guide the candidate to articulate: API endpoint signatures, major system components, and the boundaries between them.
Encourage the use of text-based component diagrams or Mermaid syntax.
Probe for missing components and unclear boundaries. Ask one targeted question at a time."""

_PROMPT_HLD_DEEP_DIVE = f"""\
{_HLD_ROLE}
The high-level design is established. You are in the DEEP_DIVE phase.
Drill into hard distributed systems problems: data consistency and synchronization anomalies,
single points of failure, concurrency and race conditions, failover and recovery strategies.
Push the candidate to justify every design choice with engineering reasoning. Be rigorous."""

_PROMPT_DSA_CLARIFYING = f"""\
{_DSA_ROLE}
You are in the CLARIFYING phase. Make sure the candidate understands the problem before solving it.
Probe for input constraints (sizes, value ranges, duplicates, ordering) and edge cases (empty input, a single element, capacity limits).
This is not a system design interview: never ask about users, traffic, QPS, or latency SLAs.
Be direct and concise. Ask one targeted question at a time."""

_PROMPT_DSA_APPROACH = f"""\
{_DSA_ROLE}
The problem is clear. You are in the APPROACH phase.
Have the candidate explain an algorithm and the data structures it uses before writing code, with its time and space complexity.
Challenge a brute-force idea by asking how to do better. Ask one targeted question at a time; do not give the answer."""

_PROMPT_DSA_CODE = f"""\
{_DSA_ROLE}
The approach is agreed. You are in the CODE phase.
Ask the candidate to write the solution in a ``` code block.
Once they share code, probe bugs, unhandled edge cases, and complexity, and ask how they would test it. Ask one question at a time."""

_PROMPT_LLD_CLARIFYING = f"""\
{_LLD_ROLE}
You are in the CLARIFYING phase. Pin down the functional requirements and main use cases before any design.
Ask what the system must support, which actors use it, and which rules apply.
This is not a distributed system design interview: do not ask about users, traffic, QPS, or latency SLAs.
Be direct and concise. Ask one targeted question at a time."""

_PROMPT_LLD_CLASS_DESIGN = f"""\
{_LLD_ROLE}
The requirements are clear. You are in the CLASS_DESIGN phase.
Guide the candidate to name the core classes and interfaces, their responsibilities, and their relationships.
Probe for single responsibility, extensibility for new types, and fitting design patterns. Ask one targeted question at a time."""

_PROMPT_LLD_CODE = f"""\
{_LLD_ROLE}
The class design is agreed. You are in the CODE phase.
Ask the candidate to write the core classes in a ``` code block.
Once they share code, probe encapsulation, thread safety of shared state, and how a new requirement would change it. Ask one question at a time."""

_FEEDBACK_HEADER = """\
You are a senior staff engineer who just completed a technical interview. Step out of interviewer character.
Evaluate the candidate's performance across the SDE2 rubric:
"""

_FEEDBACK_FOOTER = """

Give a structured written evaluation with a score (1-5) per dimension and 2-3 concrete improvement suggestions."""

_PROMPT_HLD_FEEDBACK = _FEEDBACK_HEADER + """\
1. Requirements clarity (did they nail scale baselines?)
2. Design correctness (is the architecture sound?)
3. Depth of reasoning (did they explain trade-offs?)
4. Handling of failure scenarios (SPOF, failover, consistency?)
5. Communication (clear, structured, concise?)""" + _FEEDBACK_FOOTER

_PROMPT_DSA_FEEDBACK = _FEEDBACK_HEADER + """\
1. Problem understanding (did they pin down constraints and edge cases?)
2. Approach (did they reach an efficient algorithm and state its complexity?)
3. Code correctness (does the code work, including edge cases?)
4. Code quality (readable names, clean structure?)
5. Communication (did they think out loud and test their code?)""" + _FEEDBACK_FOOTER

_PROMPT_LLD_FEEDBACK = _FEEDBACK_HEADER + """\
1. Requirements clarity (did they pin down use cases and rules?)
2. Class design (clear responsibilities and relationships?)
3. Extensibility (can new types and rules be added cleanly?)
4. Code quality (encapsulation, thread safety where it matters?)
5. Communication (clear, structured, concise?)""" + _FEEDBACK_FOOTER

_STATE_PROMPTS: dict[tuple[Kind, State], str] = {
    (Kind.HLD, State.CLARIFYING): _PROMPT_HLD_CLARIFYING,
    (Kind.HLD, State.HIGH_LEVEL): _PROMPT_HLD_HIGH_LEVEL,
    (Kind.HLD, State.DEEP_DIVE): _PROMPT_HLD_DEEP_DIVE,
    (Kind.HLD, State.FEEDBACK): _PROMPT_HLD_FEEDBACK,
    (Kind.DSA, State.CLARIFYING): _PROMPT_DSA_CLARIFYING,
    (Kind.DSA, State.APPROACH): _PROMPT_DSA_APPROACH,
    (Kind.DSA, State.CODE): _PROMPT_DSA_CODE,
    (Kind.DSA, State.FEEDBACK): _PROMPT_DSA_FEEDBACK,
    (Kind.LLD, State.CLARIFYING): _PROMPT_LLD_CLARIFYING,
    (Kind.LLD, State.CLASS_DESIGN): _PROMPT_LLD_CLASS_DESIGN,
    (Kind.LLD, State.CODE): _PROMPT_LLD_CODE,
    (Kind.LLD, State.FEEDBACK): _PROMPT_LLD_FEEDBACK,
}


def next_state(kind: Kind, state: State) -> State | None:
    """The state after `state` in the `kind` flow, or None at FEEDBACK."""
    flow = _FLOWS[kind]
    position = flow.index(state)
    return flow[position + 1] if position + 1 < len(flow) else None


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


def _check_exit_criteria(kind: Kind, state: State, messages: list[dict]) -> bool:
    criteria = _EXIT_CRITERIA.get((kind, state))
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


def _session_number(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


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
        kind: Kind = DEFAULT_KIND,
    ) -> None:
        if state not in _FLOWS[kind]:
            raise ValueError(f"State {state.value} is not part of the {kind.value} interview flow.")
        self.path = path
        self.kind = kind
        self.topic = topic
        self.state = state
        self.started_at = started_at
        self.messages = messages
        self.question = question
        self.last_reply_advisory = False
        self.last_review: CodeReview | None = None

    @classmethod
    def start(
        cls, topic: str | None = None, question: str | None = None, kind: Kind | None = None
    ) -> "InterviewSession":
        """Start a session on a free-text topic, or on a question-bank question by name.

        `kind` picks the interview flow; it defaults to the question's declared kind, else HLD.
        """
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
        kind = kind or (loaded.kind if loaded else DEFAULT_KIND)

        interviews_dir = workspace_paths()["interviews"]
        interviews_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        started_at = datetime.now().isoformat(timespec="seconds")
        path = _next_session_path(interviews_dir, date_str)

        session = cls(
            path=path, topic=topic, state=State.CLARIFYING, started_at=started_at, messages=[], question=loaded, kind=kind
        )
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
        kind = Kind(fm.get("kind", DEFAULT_KIND.value))  # sessions from before R6 have no kind: they were HLD

        session = cls(
            path=path, topic=topic, state=state, started_at=started_at, messages=[], question=question, kind=kind
        )
        session.messages = [session._system_message()] + _parse_turns(body)
        return session

    @classmethod
    def latest_open(cls) -> "InterviewSession | None":
        """The most recent session from today that has not reached FEEDBACK, if any."""
        interviews_dir = workspace_paths()["interviews"]
        date_str = datetime.now().strftime("%Y%m%d")
        paths = interviews_dir.glob(f"session_{date_str}_*.md") if interviews_dir.exists() else []
        for path in sorted(paths, key=_session_number, reverse=True):
            try:
                session = cls.resume(path)
            except (OSError, ValueError) as exc:
                print(f"sherpa interviewer: skipping unreadable session {path.name} ({exc})", file=sys.stderr)
                continue
            if session.state != State.FEEDBACK:
                return session
        return None

    def _system_message(self) -> dict:
        content = _STATE_PROMPTS[(self.kind, self.state)]
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
        following = next_state(self.kind, self.state)
        if following is None:
            return False
        if _check_exit_criteria(self.kind, self.state, self.messages):
            self.state = following
            self._rewrite_frontmatter()
            self.messages[0] = self._system_message()
            return True
        return False

    def _rewrite_frontmatter(self) -> None:
        body = _parse_frontmatter(self.path.read_text())[1] if self.path.exists() else ""
        fm = {"state": self.state.value, "kind": self.kind.value, "topic": self.topic, "started_at": self.started_at}
        if self.question is not None:
            fm["question"] = self.question.name
        self.path.write_text(_write_frontmatter(fm) + body)
