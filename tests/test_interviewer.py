import pytest
import sherpa.workspace as ws
from sherpa.interviewer import (
    InterviewSession,
    State,
    _NEXT_STATE,
    _next_session_path,
    _parse_frontmatter,
    _parse_turns,
    _write_frontmatter,
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
