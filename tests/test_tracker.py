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
