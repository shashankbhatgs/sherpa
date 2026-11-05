import re

import pytest
from click.testing import CliRunner

import sherpa.workspace as ws
from sherpa.cli import cli
from sherpa.question_bank import Kind
from sherpa.interviewer import (
    InterviewSession,
    State,
    _next_session_path,
    _parse_frontmatter,
    _parse_turns,
)


# --- helpers ---

def _fake_chat(content):
    class _Provider:
        def chat(self, messages, tools=None, **kwargs):
            return {"message": {"content": content, "tool_calls": None}}
        def stream(self, messages, **kwargs):
            yield content
    return _Provider()


# --- _parse_frontmatter ---

def test_parse_frontmatter_basic():
    text = "---\nstate: CLARIFYING\ntopic: Cache\nstarted_at: 2026-06-29T10:00:00\n---\nbody here"
    fm, body = _parse_frontmatter(text)
    assert fm["state"] == "CLARIFYING"
    assert fm["topic"] == "Cache"
    assert body.strip() == "body here"


def test_parse_frontmatter_no_marker():
    text = "no frontmatter"
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert body == "no frontmatter"


def test_parse_frontmatter_empty_body():
    text = "---\nstate: FEEDBACK\ntopic: X\nstarted_at: 2026-06-29T00:00:00\n---\n"
    fm, body = _parse_frontmatter(text)
    assert fm["state"] == "FEEDBACK"
    assert body == ""


# --- _parse_turns ---

def test_parse_turns_basic():
    body = "\n## user\nHello\n\n## assistant\nHi there\n"
    turns = _parse_turns(body)
    assert turns == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]


def test_parse_turns_empty_body():
    assert _parse_turns("") == []


def test_parse_turns_multiple():
    body = "## user\nQ1\n## assistant\nA1\n## user\nQ2\n## assistant\nA2\n"
    turns = _parse_turns(body)
    assert len(turns) == 4
    assert turns[2]["content"] == "Q2"


# --- _next_session_path ---

def test_next_session_path_first(tmp_workspace):
    ws.init_workspace()
    interviews = tmp_workspace / "interviews"
    path = _next_session_path(interviews, "20260629")
    assert path.name == "session_20260629_1.md"


def test_next_session_path_increments(tmp_workspace):
    ws.init_workspace()
    interviews = tmp_workspace / "interviews"
    (interviews / "session_20260629_1.md").touch()
    path = _next_session_path(interviews, "20260629")
    assert path.name == "session_20260629_2.md"


# --- InterviewSession.start ---

def test_start_creates_file(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start("Distributed Cache")
    assert s.path.exists()
    assert s.state == State.CLARIFYING
    assert s.topic == "Distributed Cache"


def test_start_writes_frontmatter(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start("Parking Lot")
    text = s.path.read_text()
    assert "state: CLARIFYING" in text
    assert "topic: Parking Lot" in text
    assert "started_at:" in text


def test_start_seeds_system_message(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start("LRU Cache")
    assert s.messages[0]["role"] == "system"
    assert "CLARIFYING" in s.messages[0]["content"] or "clarif" in s.messages[0]["content"].lower()


def test_start_two_sessions_same_day(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.datetime").now.return_value.strftime.return_value = "20260629"
    mocker.patch("sherpa.interviewer.datetime").now.return_value.isoformat.return_value = "2026-06-29T10:00:00"
    s1 = InterviewSession.start("Topic A")
    s2 = InterviewSession.start("Topic B")
    assert s1.path.name != s2.path.name
    assert "_1.md" in s1.path.name or "_2.md" in s2.path.name


# --- InterviewSession.turn ---

def test_turn_appends_blocks(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("What is your DAU?"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)

    s = InterviewSession.start("Cache")
    s.turn("I want to design Redis.")

    text = s.path.read_text()
    assert "## user" in text
    assert "I want to design Redis." in text
    assert "## assistant" in text
    assert "What is your DAU?" in text


def test_turn_returns_assistant_response(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Tell me the QPS."))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)

    s = InterviewSession.start("Cache")
    response = s.turn("Hello.")
    assert response == "Tell me the QPS."


def test_turn_appends_to_messages(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Answer"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)

    s = InterviewSession.start("Cache")
    s.turn("User turn")
    assert any(m["role"] == "user" and m["content"] == "User turn" for m in s.messages)
    assert any(m["role"] == "assistant" and m["content"] == "Answer" for m in s.messages)


# --- InterviewSession.resume ---

def test_resume_restores_state_and_messages(tmp_workspace):
    ws.init_workspace()
    interviews = tmp_workspace / "interviews"
    path = interviews / "session_20260629_1.md"
    path.write_text(
        "---\nstate: HIGH_LEVEL\ntopic: Cache\nstarted_at: 2026-06-29T10:00:00\n---\n"
        "\n## user\nHere is my design.\n\n## assistant\nTell me about the API.\n"
    )
    s = InterviewSession.resume(path)
    assert s.state == State.HIGH_LEVEL
    assert s.topic == "Cache"
    assert any(m["role"] == "user" for m in s.messages)
    assert any(m["role"] == "assistant" for m in s.messages)


def test_resume_injects_system_prompt(tmp_workspace):
    ws.init_workspace()
    path = tmp_workspace / "interviews" / "session_20260629_1.md"
    path.write_text("---\nstate: DEEP_DIVE\ntopic: T\nstarted_at: 2026-06-29T00:00:00\n---\n")
    s = InterviewSession.resume(path)
    assert s.messages[0]["role"] == "system"


def test_resume_empty_body_does_not_raise(tmp_workspace):
    ws.init_workspace()
    path = tmp_workspace / "interviews" / "session_20260629_1.md"
    path.write_text("---\nstate: CLARIFYING\ntopic: T\nstarted_at: 2026-06-29T00:00:00\n---\n")
    s = InterviewSession.resume(path)
    assert s.state == State.CLARIFYING
    assert s.messages[0]["role"] == "system"


# --- state advancement ---

def test_maybe_advance_yes_advances_state(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Answer"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start("Cache")
    s.turn("DAU is 10M, QPS is 50K, latency is 50ms.")
    assert s.state == State.HIGH_LEVEL


def test_maybe_advance_no_keeps_state(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Answer"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)

    s = InterviewSession.start("Cache")
    s.turn("I want to design a cache.")
    assert s.state == State.CLARIFYING


def test_feedback_state_is_terminal(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Feedback text"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    path = tmp_workspace / "interviews" / "session_20260629_1.md"
    path.write_text("---\nstate: FEEDBACK\ntopic: T\nstarted_at: 2026-06-29T00:00:00\n---\n")
    s = InterviewSession.resume(path)
    s.turn("Any feedback?")
    assert s.state == State.FEEDBACK


def test_rewrite_frontmatter_only_changes_yaml(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Next phase prompt"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start("Cache")
    body_marker = "BODY_CONTENT_PRESERVED"
    with s.path.open("a") as f:
        f.write(f"\n## user\n{body_marker}\n")
    s._rewrite_frontmatter()
    text = s.path.read_text()
    assert body_marker in text


def test_full_state_walk(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Response"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start("Cache")
    assert s.state == State.CLARIFYING
    s.turn("DAU 10M, QPS 50K, latency 50ms")
    assert s.state == State.HIGH_LEVEL
    s.turn("Here are my API endpoints and components")
    assert s.state == State.DEEP_DIVE
    s.turn("Let me discuss SPOF and failover")
    assert s.state == State.FEEDBACK


def test_state_written_to_file_on_advance(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Response"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start("Cache")
    s.turn("DAU 10M, QPS 50K, latency 50ms")

    text = s.path.read_text()
    assert "state: HIGH_LEVEL" in text


# --- Advisory mode (burnout guard) ---

class _RecordingProvider:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return {"message": {"content": self.content, "tool_calls": None}}

    def stream(self, messages, **kwargs):
        yield self.content


def test_blocked_turn_uses_advisory_prompt_without_tools(tmp_workspace, mocker):
    ws.init_workspace()
    provider = _RecordingProvider("Great work today, time to rest.")
    mocker.patch("sherpa.interviewer.get_provider", return_value=provider)
    s = InterviewSession.start("Cache")
    mocker.patch("sherpa.telemetry.is_blocked", return_value=True)

    reply = s.turn("Let's do the deep dive now.")

    assert reply == "Great work today, time to rest."
    assert "burnout guard is on" in provider.calls[0]["messages"][0]["content"]
    assert provider.calls[0]["tools"] is None
    assert s.last_reply_advisory is True
    # the stored state prompt is untouched, so the session resumes normally later
    assert "CLARIFYING" in s.messages[0]["content"]


def test_blocked_turn_does_not_advance_state(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Rest now."))
    check = mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)
    s = InterviewSession.start("Cache")
    mocker.patch("sherpa.telemetry.is_blocked", return_value=True)

    s.turn("DAU 10M, QPS 50k, p99 20ms")

    assert s.state == State.CLARIFYING
    check.assert_not_called()


def test_advisory_reply_is_marked_in_ledger_and_survives_resume(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Rest now."))
    s = InterviewSession.start("Cache")
    mocker.patch("sherpa.telemetry.is_blocked", return_value=True)

    s.turn("one more question?")

    text = s.path.read_text()
    assert re.search(r"^## assistant \S+ advisory$", text, re.MULTILINE)
    resumed = InterviewSession.resume(s.path)
    assert [m["content"] for m in resumed.messages[1:]] == ["one more question?", "Rest now."]


def test_unblocked_turn_is_not_advisory(tmp_workspace, mocker):
    ws.init_workspace()
    provider = _RecordingProvider("What is your DAU?")
    mocker.patch("sherpa.interviewer.get_provider", return_value=provider)
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    mocker.patch("sherpa.telemetry.is_blocked", return_value=False)
    s = InterviewSession.start("Cache")

    s.turn("Hello")

    assert s.last_reply_advisory is False
    assert provider.calls[0]["tools"][0]["function"]["name"] == "fetch_full_resume"
    assert " advisory" not in s.path.read_text()


def test_cli_resume_while_blocked_shows_advisory_banner(tmp_workspace, mocker):
    from click.testing import CliRunner
    from sherpa.cli import cli

    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Rest now."))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    InterviewSession.start("Cache")
    mocker.patch("sherpa.telemetry.is_blocked", return_value=True)

    result = CliRunner().invoke(cli, ["interview"], input="one more?\nquit\n")

    assert result.exit_code == 0, result.output
    assert "Rest now." in result.output
    assert "Advisory mode" in result.output


def test_cli_new_session_refused_while_blocked(tmp_workspace, mocker):
    from click.testing import CliRunner
    from sherpa.cli import cli

    ws.init_workspace()
    mocker.patch("sherpa.telemetry.is_blocked", return_value=True)
    mocker.patch("sherpa.telemetry.burnout_status", return_value={"daily_minutes": 200, "threshold": 180, "unblocks_at": None})

    result = CliRunner().invoke(cli, ["interview", "New topic"])

    assert result.exit_code != 0
    assert "Burnout guard" in result.output


# --- latest_open ---

def _session_file(workspace, n, state):
    from datetime import datetime as _dt
    path = workspace / "interviews" / f"session_{_dt.now():%Y%m%d}_{n}.md"
    path.write_text(f"---\nstate: {state}\ntopic: T{n}\nstarted_at: x\n---\n")
    return path


def test_latest_open_sorts_numerically_and_skips_finished(tmp_workspace):
    ws.init_workspace()
    _session_file(tmp_workspace, 9, "CLARIFYING")
    _session_file(tmp_workspace, 10, "HIGH_LEVEL")
    _session_file(tmp_workspace, 11, "FEEDBACK")

    assert InterviewSession.latest_open().topic == "T10"


def test_latest_open_skips_unreadable_session(tmp_workspace, capsys):
    ws.init_workspace()
    _session_file(tmp_workspace, 1, "CLARIFYING")
    _session_file(tmp_workspace, 2, "NOT_A_STATE")

    assert InterviewSession.latest_open().topic == "T1"
    assert "skipping unreadable session" in capsys.readouterr().err


def test_latest_open_none_when_no_sessions(tmp_workspace):
    assert InterviewSession.latest_open() is None


# --- interview kinds ---

def _walk(session, turns):
    states = [session.state]
    for _ in range(turns):
        session.turn("next")
        states.append(session.state)
    return states


@pytest.mark.parametrize(
    "kind, flow",
    [
        (Kind.HLD, [State.CLARIFYING, State.HIGH_LEVEL, State.DEEP_DIVE, State.FEEDBACK]),
        (Kind.DSA, [State.CLARIFYING, State.APPROACH, State.CODE, State.FEEDBACK]),
        (Kind.LLD, [State.CLARIFYING, State.CLASS_DESIGN, State.CODE, State.FEEDBACK]),
    ],
)
def test_each_kind_walks_its_own_flow(tmp_workspace, mocker, kind, flow):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Response"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start("Topic", kind=kind)
    assert _walk(s, 3) == flow


def test_free_text_topic_defaults_to_hld(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start("Design a distributed cache")
    assert s.kind == Kind.HLD
    assert "DAU" in s.messages[0]["content"]


def test_dsa_question_never_asks_about_scale(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Response"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)

    s = InterviewSession.start(question="dsa_lru_cache")
    assert s.kind == Kind.DSA
    prompts = [s.messages[0]["content"]]
    for _ in range(3):
        s.turn("next")
        prompts.append(s.messages[0]["content"])
    assert all("DAU" not in p for p in prompts)
    assert "input constraints" in prompts[0]


def test_explicit_kind_overrides_question_kind(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start(question="dsa_lru_cache", kind=Kind.LLD)
    assert s.kind == Kind.LLD


def test_kind_written_to_frontmatter_and_restored(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_fake_chat("Response"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)
    s = InterviewSession.start("Two sum", kind=Kind.DSA)
    s.turn("next")

    assert "kind: dsa" in s.path.read_text()
    resumed = InterviewSession.resume(s.path)
    assert (resumed.kind, resumed.state) == (Kind.DSA, State.APPROACH)


def test_resume_without_kind_is_hld(tmp_workspace):
    ws.init_workspace()
    path = tmp_workspace / "interviews" / "session_20260629_1.md"
    path.write_text("---\nstate: DEEP_DIVE\ntopic: T\nstarted_at: 2026-06-29T00:00:00\n---\n")
    assert InterviewSession.resume(path).kind == Kind.HLD


def test_resume_rejects_state_outside_kind_flow(tmp_workspace):
    ws.init_workspace()
    path = tmp_workspace / "interviews" / "session_20260629_1.md"
    path.write_text("---\nstate: DEEP_DIVE\nkind: dsa\ntopic: T\nstarted_at: 2026-06-29T00:00:00\n---\n")
    with pytest.raises(ValueError, match="not part of the dsa"):
        InterviewSession.resume(path)


def test_cli_kind_option_starts_that_flow(tmp_workspace):
    ws.init_workspace()
    result = CliRunner().invoke(cli, ["interview", "Two sum", "--kind", "dsa"], input="quit\n")
    assert result.exit_code == 0, result.output
    assert "Topic: Two sum (dsa)" in result.output
