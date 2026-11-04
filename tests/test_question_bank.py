import pytest
from click.testing import CliRunner

import sherpa.workspace as ws
from sherpa.cli import cli
from sherpa.interviewer import InterviewSession
from sherpa.question_bank import QuestionNotFoundError, list_questions, load_question

SEEDED = ["dsa_lru_cache", "hld_distributed_cache", "lld_parking_lot"]


class _Recorder:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return {"message": {"content": "Go on.", "tool_calls": None}}

    def stream(self, messages, **kwargs):
        yield ""


def test_list_questions_reads_seeded_bank(tmp_workspace):
    ws.init_workspace()
    questions = list_questions()
    assert [q.name for q in questions] == SEEDED
    assert questions[2].title == "LLD: Parking Lot"


def test_title_falls_back_to_file_name(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "question_bank" / "custom_q.md").write_text("no heading here")
    assert load_question("custom_q").title == "custom_q"


def test_list_questions_empty_without_bank(tmp_workspace):
    assert list_questions() == []


def test_unknown_question_lists_available(tmp_workspace):
    ws.init_workspace()
    with pytest.raises(QuestionNotFoundError, match="lld_parking_lot"):
        load_question("nope")


def test_start_from_question_seeds_prompt_and_front_matter(tmp_workspace):
    ws.init_workspace()

    s = InterviewSession.start(question="lld_parking_lot")

    assert s.topic == "LLD: Parking Lot"
    assert "question: lld_parking_lot" in s.path.read_text()
    assert "Parking Lot" in s.messages[0]["content"]
    assert "CLARIFYING" in s.messages[0]["content"]


def test_custom_topic_overrides_question_title(tmp_workspace):
    ws.init_workspace()
    s = InterviewSession.start("Parking lot for a stadium", question="lld_parking_lot")
    assert s.topic == "Parking lot for a stadium"


def test_start_needs_topic_or_question(tmp_workspace):
    ws.init_workspace()
    with pytest.raises(ValueError):
        InterviewSession.start("   ")


def test_resume_reloads_question(tmp_workspace, mocker):
    ws.init_workspace()
    provider = _Recorder()
    mocker.patch("sherpa.interviewer.get_provider", return_value=provider)
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    s = InterviewSession.start(question="dsa_lru_cache")
    s.turn("What's the capacity range?")

    resumed = InterviewSession.resume(s.path)

    assert resumed.question is not None and resumed.question.name == "dsa_lru_cache"
    assert "LRU" in resumed.messages[0]["content"]
    assert [m["role"] for m in resumed.messages] == ["system", "user", "assistant"]


def test_question_survives_state_advance(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_Recorder())
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=True)
    s = InterviewSession.start(question="hld_distributed_cache")

    s.turn("DAU 10M, QPS 100k, p99 5ms")

    assert s.state.value == "HIGH_LEVEL"
    assert "Distributed Cache" in s.messages[0]["content"]
    assert "question: hld_distributed_cache" in s.path.read_text()


def test_resume_tolerates_deleted_question(tmp_workspace, capsys):
    ws.init_workspace()
    s = InterviewSession.start(question="lld_parking_lot")
    (tmp_workspace / "question_bank" / "lld_parking_lot.md").unlink()

    resumed = InterviewSession.resume(s.path)

    assert resumed.question is None
    assert "no longer in the bank" in capsys.readouterr().err


def test_code_review_uses_question_text_as_problem(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.interviewer.get_provider", return_value=_Recorder())
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    review = mocker.patch("sherpa.interviewer.evaluate_code", return_value=None)
    s = InterviewSession.start(question="dsa_lru_cache")

    s.turn("```\nx = 1\n```")

    assert "O(1)" in review.call_args.kwargs["problem"]


# --- CLI ---

def test_cli_list(tmp_workspace):
    ws.init_workspace()
    result = CliRunner().invoke(cli, ["interview", "--list"])
    assert result.exit_code == 0
    for name in SEEDED:
        assert name in result.output


def test_cli_list_empty_bank_errors(tmp_workspace):
    result = CliRunner().invoke(cli, ["interview", "--list"])
    assert result.exit_code != 0
    assert "sherpa init" in result.output


def test_cli_start_from_question(tmp_workspace):
    ws.init_workspace()
    result = CliRunner().invoke(cli, ["interview", "-q", "lld_parking_lot"], input="quit\n")
    assert result.exit_code == 0, result.output
    assert "Topic: LLD: Parking Lot" in result.output


def test_cli_unknown_question_errors(tmp_workspace):
    ws.init_workspace()
    result = CliRunner().invoke(cli, ["interview", "-q", "nope"])
    assert result.exit_code != 0
    assert "Available:" in result.output
