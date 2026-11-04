import json
import pytest
import sherpa.workspace as ws
from sherpa.tracker import track, ValidationGap, _append_jsonl


def _mock_run_tool_loop(mocker, return_value: str):
    return mocker.patch("sherpa.tracker.run_tool_loop", return_value=return_value)


def test_valid_extraction_writes_jsonl(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Stripe", "state": "applied", "role": "SDE2", "date": "2026-06-29", "notes": ""}')
    mocker.patch("sherpa.tracker.get_provider")

    result = track("Applied to Stripe for SDE2 yesterday")
    assert isinstance(result, dict)
    assert result["company"] == "Stripe"
    assert result["state"] == "applied"

    apps = tmp_workspace / "applications.jsonl"
    assert apps.exists()
    entry = json.loads(apps.read_text().strip())
    assert entry["company"] == "Stripe"


def test_missing_company_returns_validation_gap(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"state": "applied", "role": "SDE2"}')
    mocker.patch("sherpa.tracker.get_provider")

    result = track("Applied somewhere")
    assert isinstance(result, ValidationGap)
    assert "company" in result.missing


def test_missing_state_returns_validation_gap(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "role": "SDE2"}')
    mocker.patch("sherpa.tracker.get_provider")

    result = track("Something at Acme")
    assert isinstance(result, ValidationGap)
    assert "state" in result.missing


def test_both_required_missing(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"role": "SDE2"}')
    mocker.patch("sherpa.tracker.get_provider")

    result = track("applied somewhere")
    assert isinstance(result, ValidationGap)
    assert set(result.missing) == {"company", "state"}


def test_sheet_failure_falls_back_to_jsonl(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Google", "state": "interview"}')
    mocker.patch("sherpa.tracker.get_provider")
    mock_sheet = mocker.MagicMock()
    mock_sheet.append_row.side_effect = Exception("network error")
    mocker.patch("sherpa.tracker._get_sheet", return_value=mock_sheet)

    result = track("Interview with Google")
    assert isinstance(result, dict)
    apps = tmp_workspace / "applications.jsonl"
    assert apps.exists()


def test_jsonl_entries_are_valid_json(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Meta", "state": "applied"}')
    mocker.patch("sherpa.tracker.get_provider")

    track("Applied to Meta")
    track("Applied to Meta again")

    apps = tmp_workspace / "applications.jsonl"
    lines = apps.read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "company" in parsed


def test_append_jsonl_direct(tmp_workspace):
    ws.init_workspace()
    _append_jsonl({"company": "TestCo", "state": "applied"})
    apps = tmp_workspace / "applications.jsonl"
    entry = json.loads(apps.read_text().strip())
    assert entry["company"] == "TestCo"


def test_llm_json_embedded_in_text(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, 'Sure! Here is the info: {"company": "Netflix", "state": "offer"} Hope that helps.')
    mocker.patch("sherpa.tracker.get_provider")

    result = track("Got offer from Netflix")
    assert isinstance(result, dict)
    assert result["company"] == "Netflix"


# --- missing-field follow-up ---

from click.testing import CliRunner

from sherpa.cli import cli
from sherpa.tracker import complete_track


def _lines(workspace):
    path = workspace / "applications.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_gap_keeps_partial_and_writes_nothing(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"role": "SDE2", "state": "applied", "notes": "via referral"}')
    mocker.patch("sherpa.tracker.get_provider")

    gap = track("applied for SDE2 via referral")

    assert gap.missing == ["company"]
    assert gap.partial == {"role": "SDE2", "state": "applied", "notes": "via referral"}
    assert _lines(tmp_workspace) == []


def test_complete_track_merges_answers_and_writes_one_row(tmp_workspace):
    ws.init_workspace()

    entry = complete_track({"role": "SDE2", "state": "applied"}, {"company": "  Stripe "})

    assert entry["company"] == "Stripe"
    assert entry["role"] == "SDE2"
    assert _lines(tmp_workspace) == [entry]


def test_complete_track_still_missing_returns_gap(tmp_workspace):
    ws.init_workspace()

    gap = complete_track({"role": "SDE2"}, {"company": "", "state": "applied"})

    assert isinstance(gap, ValidationGap)
    assert gap.missing == ["company"]
    assert gap.partial["state"] == "applied"
    assert _lines(tmp_workspace) == []


@pytest.mark.parametrize("raw, expected", [("Applied", "applied"), ("phone screen", "phone_screen"), ("Phone-Screen", "phone_screen")])
def test_state_is_normalized(tmp_workspace, mocker, raw, expected):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, json.dumps({"company": "Acme", "state": raw}))
    mocker.patch("sherpa.tracker.get_provider")
    assert track("x")["state"] == expected


def test_unknown_state_counts_as_missing(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "state": "ghosted"}')
    mocker.patch("sherpa.tracker.get_provider")

    gap = track("Acme ghosted me")

    assert isinstance(gap, ValidationGap)
    assert gap.missing == ["state"]


def test_non_string_fields_are_dropped(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "state": "offer", "role": 42, "notes": ["a"]}')
    mocker.patch("sherpa.tracker.get_provider")

    entry = track("offer from Acme")

    assert "role" not in entry and "notes" not in entry


def test_missing_date_defaults_to_today(tmp_workspace, mocker):
    from datetime import date

    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "state": "applied"}')
    mocker.patch("sherpa.tracker.get_provider")

    assert track("applied to Acme")["date"] == date.today().isoformat()


def test_cli_asks_only_for_missing_fields(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"role": "SDE2"}')
    mocker.patch("sherpa.tracker.get_provider")

    result = CliRunner().invoke(cli, ["track", "applied somewhere for SDE2"], input="Stripe\napplied\n")

    assert result.exit_code == 0, result.output
    assert "Need a bit more: Company, Status" in result.output
    assert _lines(tmp_workspace)[0]["company"] == "Stripe"
    assert _lines(tmp_workspace)[0]["role"] == "SDE2"


def test_cli_rejects_invalid_status_choice(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme"}')
    mocker.patch("sherpa.tracker.get_provider")

    result = CliRunner().invoke(cli, ["track", "Acme"], input="ghosted\noffer\n")

    assert result.exit_code == 0, result.output
    assert "is not one of" in result.output
    assert _lines(tmp_workspace)[0]["state"] == "offer"


@pytest.mark.parametrize("placeholder", ["Unknown", "unknown", "N/A", "Not specified.", "  none  ", "?"])
def test_placeholder_company_counts_as_missing(tmp_workspace, mocker, placeholder):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, json.dumps({"company": placeholder, "state": "applied", "role": "SDE2"}))
    mocker.patch("sherpa.tracker.get_provider")

    gap = track("applied for SDE2")

    assert isinstance(gap, ValidationGap)
    assert gap.missing == ["company"]
    assert "company" not in gap.partial


# --- local ledger ---

from sherpa.tracker import load_applications


def test_entries_get_tracked_at(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "state": "applied"}')
    mocker.patch("sherpa.tracker.get_provider")
    assert track("Acme")["tracked_at"][:4].isdigit()


def test_sheet_success_still_writes_local_ledger(tmp_workspace, mocker):
    ws.init_workspace()
    _mock_run_tool_loop(mocker, '{"company": "Acme", "state": "applied"}')
    mocker.patch("sherpa.tracker.get_provider")
    sheet = mocker.MagicMock()
    mocker.patch("sherpa.tracker._get_sheet", return_value=sheet)

    track("Acme")

    sheet.append_row.assert_called_once()
    assert _lines(tmp_workspace)[0]["company"] == "Acme"


def test_load_applications_skips_bad_lines(tmp_workspace):
    ws.init_workspace()
    (tmp_workspace / "applications.jsonl").write_text('{"company": "A"}\nnot json\n[1, 2]\n\n{"company": "B"}\n')
    assert [e["company"] for e in load_applications()] == ["A", "B"]


def test_load_applications_without_file(tmp_workspace):
    assert load_applications() == []
