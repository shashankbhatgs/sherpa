import json

import pytest
from click.testing import CliRunner

import sherpa.workspace as ws
from sherpa.cli import cli
from sherpa.code_review import CodeReviewError, evaluate_code, extract_code
from sherpa.interviewer import InterviewSession

LRU_CODE = "class LRU:\n    def get(self, k):\n        return self.d.get(k, -1)"


class FakeProvider:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return {"message": {"content": self.contents.pop(0), "tool_calls": None}}

    def stream(self, messages, **kwargs):
        yield ""


def _review_json(**overrides) -> str:
    data = {
        "correctness": 3,
        "thread_safety": 2,
        "complexity": 4,
        "time_complexity": "get O(1)",
        "space_complexity": "O(n)",
        "issues": ["No lock around dict mutation", "Eviction not implemented"],
        "summary": "Works for reads but is not a full LRU.",
    }
    data.update(overrides)
    return json.dumps(data)


def _use(mocker, provider):
    mocker.patch("sherpa.code_review.get_provider", return_value=provider)
    return provider


# --- extract_code ---

def test_extract_code_joins_fenced_blocks():
    text = "Here:\n```python\na = 1\n```\nand\n```\nb = 2\n```"
    assert extract_code(text) == "a = 1\n\nb = 2"


@pytest.mark.parametrize("text", ["plain prose about caching", "inline `code` only", "```\n\n```"])
def test_extract_code_none_without_real_blocks(text):
    assert extract_code(text) is None


# --- evaluate_code ---

def test_evaluate_code_parses_rubric(mocker):
    provider = _use(mocker, FakeProvider(_review_json()))

    review = evaluate_code(LRU_CODE, problem="LRU cache")

    assert (review.correctness, review.thread_safety, review.complexity) == (3, 2, 4)
    assert review.issues == ["No lock around dict mutation", "Eviction not implemented"]
    assert "LRU cache" in provider.calls[0][1]["content"]
    md = review.as_markdown()
    assert "thread safety 2/5" in md
    assert "- Eviction not implemented" in md


@pytest.mark.parametrize("raw, expected", [(9, 5), (0, 1), ("4/5", 4), ("3.6", 4), (2.2, 2)])
def test_scores_are_coerced_and_clamped(mocker, raw, expected):
    _use(mocker, FakeProvider(_review_json(correctness=raw)))
    assert evaluate_code(LRU_CODE).correctness == expected


@pytest.mark.parametrize("bad", [None, "great", True, float("inf")])
def test_unusable_score_raises(mocker, bad):
    content = _review_json(thread_safety=None).replace("null", "1e999") if bad == float("inf") else _review_json(thread_safety=bad)
    _use(mocker, FakeProvider(content))
    with pytest.raises(CodeReviewError, match="thread_safety"):
        evaluate_code(LRU_CODE)


def test_non_json_output_raises(mocker):
    _use(mocker, FakeProvider("Looks fine to me!"))
    with pytest.raises(CodeReviewError):
        evaluate_code(LRU_CODE)


def test_issues_are_cleaned_and_capped(mocker):
    _use(mocker, FakeProvider(_review_json(issues=["a", "", 3, "  b  ", "c", "d", "e", "f"])))
    assert evaluate_code(LRU_CODE).issues == ["a", "b", "c", "d", "e"]


def test_empty_code_rejected():
    with pytest.raises(ValueError):
        evaluate_code("   ")


# --- interview integration ---

def test_turn_with_code_reviews_it_and_feeds_interviewer(tmp_workspace, mocker):
    ws.init_workspace()
    _use(mocker, FakeProvider(_review_json()))
    interviewer = FakeProvider("Why no lock on the dict?")
    mocker.patch("sherpa.interviewer.get_provider", return_value=interviewer)
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    s = InterviewSession.start("LRU cache")

    s.turn(f"My solution:\n```python\n{LRU_CODE}\n```")

    assert s.last_review is not None and s.last_review.thread_safety == 2
    assert "No lock around dict mutation" in interviewer.calls[0][-1]["content"]
    assert "## review" in s.path.read_text()
    # the review is not replayed as chat history on resume
    resumed = InterviewSession.resume(s.path)
    assert [m["role"] for m in resumed.messages] == ["system", "user", "assistant"]
    assert "correctness" not in resumed.messages[1]["content"]


def test_turn_without_code_skips_review(tmp_workspace, mocker):
    ws.init_workspace()
    review_provider = _use(mocker, FakeProvider())
    mocker.patch("sherpa.interviewer.get_provider", return_value=FakeProvider("ok"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    s = InterviewSession.start("Cache")

    s.turn("I would use consistent hashing.")

    assert s.last_review is None
    assert review_provider.calls == []


def test_failed_review_does_not_break_the_turn(tmp_workspace, mocker, capsys):
    ws.init_workspace()
    _use(mocker, FakeProvider("garbage"))
    mocker.patch("sherpa.interviewer.get_provider", return_value=FakeProvider("Walk me through it."))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)
    s = InterviewSession.start("Cache")

    assert s.turn(f"```\n{LRU_CODE}\n```") == "Walk me through it."
    assert s.last_review is None
    assert "code review skipped" in capsys.readouterr().err


# --- CLI ---

def test_cli_review_from_stdin(mocker):
    _use(mocker, FakeProvider(_review_json()))
    result = CliRunner().invoke(cli, ["review", "--problem", "LRU"], input=LRU_CODE)
    assert result.exit_code == 0, result.output
    assert "correctness 3/5" in result.output


def test_cli_review_empty_input_errors():
    result = CliRunner().invoke(cli, ["review"], input="  ")
    assert result.exit_code != 0
    assert "No code given" in result.output


def test_cli_review_bad_model_output_errors(mocker):
    _use(mocker, FakeProvider("nope"))
    result = CliRunner().invoke(cli, ["review"], input=LRU_CODE)
    assert result.exit_code != 0
    assert "did not score" in result.output


def test_cli_interview_prints_review(tmp_workspace, mocker):
    ws.init_workspace()
    _use(mocker, FakeProvider(_review_json()))
    mocker.patch("sherpa.interviewer.get_provider", return_value=FakeProvider("Why no lock?"))
    mocker.patch("sherpa.interviewer._check_exit_criteria", return_value=False)

    result = CliRunner().invoke(cli, ["interview", "LRU"], input="```\nx = 1\n```\nquit\n")

    assert result.exit_code == 0, result.output
    assert "Code review" in result.output
    assert result.output.index("Code review") < result.output.index("Why no lock?")
