import json

import pytest
from click.testing import CliRunner

import sherpa.workspace as ws
from sherpa.cli import cli
from sherpa.scout import (
    LINKEDIN_NOTE_LIMIT,
    OutreachError,
    _trim_to_limit,
    draft_outreach,
)

JOB_POST = "Stripe is hiring a Software Engineer II on the Payments team. Go, Kafka, distributed systems."


def _draft_json(**overrides) -> str:
    data = {
        "company": "Stripe",
        "role": "Software Engineer II",
        "aligned": True,
        "linkedin_note": "Hi! I built Kafka-backed payment pipelines at Acme and would love to chat about SDE2 on Payments.",
        "email_subject": "SDE2 Payments - Kafka pipelines at Acme",
        "email_body": "Hi,\n\nI built Kafka payment pipelines at Acme.\n\nWould you be open to a 15-minute chat?",
    }
    data.update(overrides)
    return json.dumps(data)


class FakeProvider:
    """Replays scripted chat responses and records every request."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return {"message": self.responses.pop(0)}


def _tool_call() -> dict:
    return {"content": "", "tool_calls": [{"function": {"name": "fetch_full_resume", "arguments": {}}}]}


def _answer(content: str) -> dict:
    return {"content": content}


@pytest.fixture
def workspace(tmp_workspace):
    ws.init_workspace()
    profile = {
        "name": "Alice",
        "years_experience": 2,
        "core_competencies": ["Go", "Kafka", "distributed systems"],
        "target_roles": ["SDE2"],
    }
    (tmp_workspace / "profile.json").write_text(json.dumps(profile))
    (tmp_workspace / "resume.md").write_text("# Alice\nBuilt Kafka payment pipelines at Acme handling 5k TPS.")
    return tmp_workspace


def _use(mocker, provider: FakeProvider) -> FakeProvider:
    mocker.patch("sherpa.scout.get_provider", return_value=provider)
    return provider


def test_aligned_role_fetches_full_resume_and_saves(workspace, mocker):
    provider = _use(mocker, FakeProvider([_tool_call(), _answer(_draft_json())]))

    draft = draft_outreach(JOB_POST)

    assert draft.company == "Stripe"
    assert draft.aligned is True
    assert draft.used_full_resume is True
    # The full resume reaches the model only through the tool result.
    tool_msgs = [m for m in provider.calls[1]["messages"] if m["role"] == "tool"]
    assert "5k TPS" in tool_msgs[0]["content"]

    saved = draft.path.read_text()
    assert draft.path.parent == workspace / "outreach"
    assert "company: Stripe" in saved
    assert "used_full_resume: true" in saved
    assert "## LinkedIn note" in saved
    assert "Subject: SDE2 Payments - Kafka pipelines at Acme" in saved
    assert JOB_POST in saved


def test_prompt_carries_index_not_full_resume(workspace, mocker):
    provider = _use(mocker, FakeProvider([_answer(_draft_json(aligned=False))]))

    draft = draft_outreach(JOB_POST)

    system = provider.calls[0]["messages"][0]["content"]
    assert "Kafka, distributed systems" in system  # competencies from the index
    assert "5k TPS" not in system  # full resume is not preloaded
    assert provider.calls[0]["tools"][0]["function"]["name"] == "fetch_full_resume"
    assert draft.used_full_resume is False
    assert draft.aligned is False


def test_long_note_is_rewritten_by_model(workspace, mocker):
    long_note = "A" * 400
    short_note = "Short and specific note."
    provider = _use(
        mocker,
        FakeProvider([_answer(_draft_json(linkedin_note=long_note)), _answer(_draft_json(linkedin_note=short_note))]),
    )

    draft = draft_outreach(JOB_POST)

    assert draft.linkedin_note == short_note
    assert "400 characters" in provider.calls[1]["messages"][-1]["content"]
    assert provider.calls[1]["tools"] is None


def test_long_note_is_trimmed_when_rewrite_still_too_long(workspace, mocker):
    long_note = "I build payment systems. " * 20
    _use(
        mocker,
        FakeProvider([_answer(_draft_json(linkedin_note=long_note)), _answer(_draft_json(linkedin_note=long_note))]),
    )

    draft = draft_outreach(JOB_POST)

    assert len(draft.linkedin_note) <= LINKEDIN_NOTE_LIMIT
    assert draft.linkedin_note.endswith("systems.")


def test_unusable_output_raises_and_saves_nothing(workspace, mocker):
    _use(mocker, FakeProvider([_answer("Sorry, I can't help with that.")]))

    with pytest.raises(OutreachError):
        draft_outreach(JOB_POST)
    assert list((workspace / "outreach").iterdir()) == []


def test_empty_job_post_rejected(workspace):
    with pytest.raises(ValueError):
        draft_outreach("   \n ")


def test_missing_optional_fields_get_defaults(workspace, mocker):
    _use(mocker, FakeProvider([_answer(_draft_json(company="", role=None, email_subject="", aligned="yes"))]))

    draft = draft_outreach(JOB_POST)

    assert draft.company == "Unknown company"
    assert draft.role == ""
    assert draft.email_subject == "Interest in the role at Unknown company"
    assert draft.aligned is False  # only a real boolean true counts


def test_files_are_numbered_without_overwriting(workspace, mocker):
    _use(mocker, FakeProvider([_answer(_draft_json()) for _ in range(3)]))

    first = draft_outreach(JOB_POST).path
    second = draft_outreach(JOB_POST).path
    first.unlink()
    third = draft_outreach(JOB_POST).path

    assert first.name.endswith("_1.md")
    assert second.name.endswith("_2.md")
    assert third.name.endswith("_3.md")
    assert second.exists()


def test_multiline_company_does_not_break_front_matter(workspace, mocker):
    _use(mocker, FakeProvider([_answer(_draft_json(company="Stripe\nInc"))]))

    saved = draft_outreach(JOB_POST).path.read_text()

    assert "company: Stripe Inc\n" in saved


@pytest.mark.parametrize(
    "text, expected",
    [
        ("short", "short"),
        ("First sentence here. Second one runs past the limit", "First sentence here."),
        ("alpha beta gamma delta", "alpha beta"),
        ("alpha beta, gamma", "alpha beta"),
    ],
)
def test_trim_to_limit(text, expected):
    limit = 25 if "sentence" in text else 12
    assert _trim_to_limit(text, limit) == expected


def test_cli_reads_stdin_and_prints_draft(workspace, mocker):
    _use(mocker, FakeProvider([_tool_call(), _answer(_draft_json())]))

    result = CliRunner().invoke(cli, ["outreach"], input=JOB_POST)

    assert result.exit_code == 0, result.output
    assert "Stripe - Software Engineer II" in result.output
    assert "Strong match, full resume used" in result.output
    assert f"/{LINKEDIN_NOTE_LIMIT} chars" in result.output
    assert "Subject: SDE2 Payments" in result.output
    assert "Saved to" in result.output


def test_cli_reads_file(workspace, mocker, tmp_path):
    _use(mocker, FakeProvider([_answer(_draft_json())]))
    job_file = tmp_path / "job.txt"
    job_file.write_text(JOB_POST)

    result = CliRunner().invoke(cli, ["outreach", "--file", str(job_file)])

    assert result.exit_code == 0, result.output
    assert "resume index only" in result.output


def test_cli_empty_input_errors(workspace):
    result = CliRunner().invoke(cli, ["outreach"], input="  ")

    assert result.exit_code != 0
    assert "No job post given" in result.output


def test_cli_model_failure_is_reported(workspace, mocker):
    _use(mocker, FakeProvider([_answer("not json")]))

    result = CliRunner().invoke(cli, ["outreach"], input=JOB_POST)

    assert result.exit_code != 0
    assert "did not return" in result.output
