import json
from datetime import date

import pytest
from click.testing import CliRunner

import sherpa
import sherpa.workspace as ws
from sherpa.cli import cli
from sherpa.tracker import (
    DEFAULT_STAGES,
    ValidationGap,
    _append_jsonl,
    application_stages,
    application_statuses,
    complete_track,
    load_applications,
    track,
)


def _extract(mocker, payload) -> object:
    """Make the model return `payload` (dict or raw string) and capture the prompt it was sent."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    mocker.patch("sherpa.tracker.get_provider")
    return mocker.patch("sherpa.tracker.run_tool_loop", return_value=content)


def _lines(workspace):
    path = workspace / "applications.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def _edit_config(workspace, **tracker):
    path = workspace / "config.json"
    data = json.loads(path.read_text())
    data["tracker"].update(tracker)
    path.write_text(json.dumps(data))
    sherpa._reset_config()


@pytest.fixture
def workspace(tmp_workspace):
    ws.init_workspace()
    return tmp_workspace


# --- extraction and recording ---

def test_full_update_is_recorded(workspace, mocker):
    _extract(mocker, {
        "company": "Navi", "role": "SDE 2", "stage": "Not applied", "status": "Asked for referral",
        "contact": "Ankur Raj", "job_id": "NTL-7133", "link": "https://navi.example/job", "date": "2026-11-03",
    })

    entry = track("Asked Ankur Raj for a referral at Navi for SDE 2, job NTL-7133")

    assert entry["stage"] == "Not applied"
    assert entry["status"] == "Asked for referral"
    assert entry["contact"] == "Ankur Raj"
    assert entry["date"] == "2026-11-03"
    assert _lines(workspace) == [entry]


def test_prompt_lists_configured_labels_and_today(workspace, mocker):
    loop = _extract(mocker, {"company": "Acme", "stage": "Applied"})

    track("applied to Acme")

    prompt = loop.call_args.kwargs["messages"][0]["content"]
    assert f"Today is {date.today().isoformat()}" in prompt
    assert '"Take Home Assessment"' in prompt
    assert "\"Didn't hear back\"" in prompt
    assert loop.call_args.kwargs["tools"] == []


@pytest.mark.parametrize(
    "raw, expected",
    [("applied", "Applied"), ("PHONE CALL", "Phone call"), ("take-home assessment", "Take Home Assessment"), ("not_applied", "Not applied")],
)
def test_stage_snaps_to_configured_label(workspace, mocker, raw, expected):
    _extract(mocker, {"company": "Acme", "stage": raw})
    assert track("x")["stage"] == expected


def test_status_snaps_including_curly_apostrophe(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "Applied", "status": "didn’t hear back"})
    assert track("x")["status"] == "Didn't hear back"


def test_unknown_status_is_dropped_not_blocking(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "Applied", "status": "ghosted"})
    entry = track("x")
    assert "status" not in entry
    assert entry["stage"] == "Applied"


def test_unknown_stage_counts_as_missing(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "vibing"})
    gap = track("x")
    assert isinstance(gap, ValidationGap)
    assert gap.missing == ["stage"]


@pytest.mark.parametrize("placeholder", ["Unknown", "N/A", "Not specified.", "  none  ", "?"])
def test_placeholder_company_counts_as_missing(workspace, mocker, placeholder):
    _extract(mocker, {"company": placeholder, "stage": "Applied", "role": "SDE2"})
    gap = track("applied for SDE2")
    assert gap.missing == ["company"]
    assert "company" not in gap.partial


def test_non_string_fields_are_dropped(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "Offer", "role": 42, "notes": ["a"]})
    entry = track("offer from Acme")
    assert "role" not in entry and "notes" not in entry


def test_missing_date_defaults_to_today(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "Applied"})
    assert track("applied to Acme")["date"] == date.today().isoformat()


def test_json_embedded_in_prose_is_parsed(workspace, mocker):
    _extract(mocker, 'Sure! {"company": "Netflix", "stage": "Offer"} Hope that helps.')
    assert track("offer from Netflix")["company"] == "Netflix"


def test_entries_get_tracked_at(workspace, mocker):
    _extract(mocker, {"company": "Acme", "stage": "Applied"})
    assert track("Acme")["tracked_at"][:4].isdigit()


# --- configured vocabulary ---

def test_labels_default_when_not_configured(workspace):
    _edit_config(workspace, stages=None, statuses=[])
    assert application_stages() == DEFAULT_STAGES
    assert "Rejected" in application_statuses()


def test_labels_come_from_config(workspace, mocker):
    _edit_config(workspace, stages=["Sourced", "Onsite"])
    assert application_stages() == ("Sourced", "Onsite")
    _extract(mocker, {"company": "Acme", "stage": "onsite"})
    assert track("x")["stage"] == "Onsite"


# --- follow-up for missing fields ---

def test_gap_keeps_partial_and_writes_nothing(workspace, mocker):
    _extract(mocker, {"role": "SDE2", "stage": "Applied", "notes": "via referral"})

    gap = track("applied for SDE2 via referral")

    assert gap.missing == ["company"]
    assert gap.partial == {"role": "SDE2", "stage": "Applied", "notes": "via referral"}
    assert _lines(workspace) == []


def test_complete_track_merges_answers_and_writes_one_row(workspace):
    entry = complete_track({"role": "SDE2", "stage": "Applied"}, {"company": "  Stripe "})
    assert entry["company"] == "Stripe"
    assert _lines(workspace) == [entry]


def test_complete_track_still_missing_returns_gap(workspace):
    gap = complete_track({"role": "SDE2"}, {"company": "", "stage": "applied"})
    assert gap.missing == ["company"]
    assert gap.partial["stage"] == "Applied"
    assert _lines(workspace) == []


def test_cli_asks_only_for_missing_fields(workspace, mocker):
    _extract(mocker, {"role": "SDE2"})

    result = CliRunner().invoke(cli, ["track", "applied somewhere for SDE2"], input="Stripe\napplied\n")

    assert result.exit_code == 0, result.output
    assert "Need a bit more: Company, Stage" in result.output
    assert _lines(workspace)[0] | {"tracked_at": ""} == {
        "company": "Stripe", "role": "SDE2", "stage": "Applied", "date": date.today().isoformat(), "tracked_at": "",
    }


def test_cli_rejects_invalid_stage_choice(workspace, mocker):
    _extract(mocker, {"company": "Acme"})

    result = CliRunner().invoke(cli, ["track", "Acme"], input="ghosted\nOffer\n")

    assert result.exit_code == 0, result.output
    assert "is not one of" in result.output
    assert _lines(workspace)[0]["stage"] == "Offer"


# --- Google Sheet mirror ---

SHEET_HEADERS = ["Company", "Role", "Stage", "Status", "Interviewer/HA/POC Name or Phone number (optional)", "Job ID", "Link to portal", "Date"]


def _sheet(mocker, headers=SHEET_HEADERS):
    sheet = mocker.MagicMock()
    sheet.row_values.return_value = headers
    mocker.patch("sherpa.tracker._get_sheet", return_value=sheet)
    return sheet


def test_sheet_row_follows_header_names(workspace, mocker):
    sheet = _sheet(mocker)
    _extract(mocker, {
        "company": "Navi", "role": "SDE 2", "stage": "Not applied", "status": "Asked for referral",
        "contact": "Ankur Raj", "job_id": "NTL-7133", "link": "https://navi.example/job", "date": "2026-10-11",
        "notes": "local only",
    })

    track("x")

    row = sheet.append_row.call_args.args[0]
    assert row == ["Navi", "SDE 2", "Not applied", "Asked for referral", "Ankur Raj", "NTL-7133", "https://navi.example/job", "10/11/2026"]
    assert sheet.append_row.call_args.kwargs["value_input_option"] == "USER_ENTERED"


def test_sheet_columns_can_be_reordered_and_unknown_headers_left_blank(workspace, mocker):
    sheet = _sheet(mocker, ["date", "Notes column", " company ", "STAGE"])
    _extract(mocker, {"company": "Acme", "stage": "Applied", "date": "2026-01-02"})

    track("x")

    assert sheet.append_row.call_args.args[0] == ["01/02/2026", "", "Acme", "Applied"]


def test_sheet_date_format_from_config(workspace, mocker):
    _edit_config(workspace, date_format="%d/%m/%Y")
    sheet = _sheet(mocker, ["Company", "Date"])
    _extract(mocker, {"company": "Acme", "stage": "Applied", "date": "2026-01-02"})

    track("x")

    assert sheet.append_row.call_args.args[0] == ["Acme", "02/01/2026"]


def test_sheet_with_no_matching_headers_is_not_written(workspace, mocker, capsys):
    sheet = _sheet(mocker, ["Foo", "Bar"])
    _extract(mocker, {"company": "Acme", "stage": "Applied"})

    entry = track("x")

    sheet.append_row.assert_not_called()
    assert _lines(workspace) == [entry]
    assert "saved locally only" in capsys.readouterr().err


def test_sheet_failure_keeps_local_entry(workspace, mocker):
    sheet = _sheet(mocker)
    sheet.append_row.side_effect = Exception("network error")
    _extract(mocker, {"company": "Google", "stage": "Interview"})

    assert isinstance(track("Interview with Google"), dict)
    assert _lines(workspace)[0]["company"] == "Google"


def test_sheet_success_still_writes_local_ledger(workspace, mocker):
    sheet = _sheet(mocker)
    _extract(mocker, {"company": "Acme", "stage": "Applied"})

    track("Acme")

    sheet.append_row.assert_called_once()
    assert _lines(workspace)[0]["company"] == "Acme"


# --- local ledger ---

def test_append_jsonl_direct(workspace):
    _append_jsonl({"company": "TestCo", "stage": "Applied"})
    assert _lines(workspace)[0]["company"] == "TestCo"


def test_load_applications_skips_bad_lines(workspace):
    (workspace / "applications.jsonl").write_text('{"company": "A"}\nnot json\n[1, 2]\n\n{"company": "B"}\n')
    assert [e["company"] for e in load_applications()] == ["A", "B"]


def test_load_applications_without_file(tmp_workspace):
    assert load_applications() == []
