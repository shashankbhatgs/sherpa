import json
from datetime import datetime, timedelta
import sherpa.workspace as ws
from sherpa.telemetry import daily_active_minutes, burnout_status, is_blocked, _session_minutes


def _make_session(tmp_workspace, date_str: str, n: int, turns: list[str]) -> None:
    """Write a fake session file with timestamped turn headers."""
    interviews = tmp_workspace / "interviews"
    interviews.mkdir(exist_ok=True)
    path = interviews / f"session_{date_str}_{n}.md"
    lines = [f"---\nstate: CLARIFYING\ntopic: Cache\nstarted_at: {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T09:00:00\n---\n"]
    for ts, role, content in turns:
        lines.append(f"\n## {role} {ts}\n{content}\n")
    path.write_text("".join(lines))


def test_empty_interviews_dir(tmp_workspace):
    ws.init_workspace()
    assert daily_active_minutes() == 0.0


def test_no_turns_returns_zero(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260629", 1, [])
    assert daily_active_minutes("20260629") == 0.0


def test_one_turn_returns_zero(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260629", 1, [
        ("2026-06-29T10:00:00", "user", "Hello"),
    ])
    assert daily_active_minutes("20260629") == 0.0


def test_two_turns_five_minutes(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260629", 1, [
        ("2026-06-29T10:00:00", "user", "Hello"),
        ("2026-06-29T10:05:00", "assistant", "Hi"),
    ])
    minutes = daily_active_minutes("20260629")
    assert abs(minutes - 5.0) < 0.1


def test_idle_gap_excluded(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260629", 1, [
        ("2026-06-29T10:00:00", "user", "A"),
        ("2026-06-29T10:05:00", "assistant", "B"),
        ("2026-06-29T10:30:00", "user", "C"),   # 25-min gap → excluded
        ("2026-06-29T10:33:00", "assistant", "D"),
    ])
    minutes = daily_active_minutes("20260629")
    assert abs(minutes - 8.0) < 0.1  # 5 + 3 = 8 min


def test_multiple_sessions_summed(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260629", 1, [
        ("2026-06-29T10:00:00", "user", "A"),
        ("2026-06-29T10:02:00", "assistant", "B"),
    ])
    _make_session(tmp_workspace, "20260629", 2, [
        ("2026-06-29T11:00:00", "user", "C"),
        ("2026-06-29T11:03:00", "assistant", "D"),
    ])
    minutes = daily_active_minutes("20260629")
    assert abs(minutes - 5.0) < 0.1  # 2 + 3 = 5 min


def test_yesterday_not_counted(tmp_workspace):
    ws.init_workspace()
    _make_session(tmp_workspace, "20260628", 1, [
        ("2026-06-28T10:00:00", "user", "A"),
        ("2026-06-28T10:05:00", "assistant", "B"),
    ])
    minutes = daily_active_minutes("20260629")
    assert minutes == 0.0


def test_is_blocked_false_under_threshold(tmp_workspace, monkeypatch):
    ws.init_workspace()
    import sherpa.telemetry as tel
    monkeypatch.setattr(tel, "daily_active_minutes", lambda date_str=None: 10.0)
    assert is_blocked() is False


def test_is_blocked_true_at_threshold(tmp_workspace, monkeypatch):
    ws.init_workspace()
    import sherpa.telemetry as tel
    monkeypatch.setattr(tel, "daily_active_minutes", lambda date_str=None: 180.0)
    assert is_blocked() is True


def test_burnout_status_shape(tmp_workspace):
    ws.init_workspace()
    status = burnout_status()
    assert "daily_minutes" in status
    assert "threshold" in status
    assert "blocked" in status
    assert "cooldown_hours" in status
    assert "unblocks_at" in status


def test_burnout_status_unblocks_at_set_when_blocked(tmp_workspace, monkeypatch):
    ws.init_workspace()
    import sherpa.telemetry as tel
    monkeypatch.setattr(tel, "daily_active_minutes", lambda date_str=None: 999.0)

    ts_str = "2026-06-29T10:00:00"
    monkeypatch.setattr(tel, "_last_turn_timestamp", lambda: datetime.fromisoformat(ts_str))

    status = burnout_status()
    assert status["blocked"] is True
    assert status["unblocks_at"] is not None
    assert "2026-06-29T22:00:00" in status["unblocks_at"]


def test_burnout_status_unblocks_at_none_when_not_blocked(tmp_workspace):
    ws.init_workspace()
    status = burnout_status()
    if not status["blocked"]:
        assert status["unblocks_at"] is None


def test_burnout_respects_config_threshold(tmp_workspace, monkeypatch):
    ws.init_workspace()
    config_path = tmp_workspace / "config.json"
    import json as _json
    data = _json.loads(config_path.read_text())
    data["burnout"]["daily_minutes_threshold"] = 5
    config_path.write_text(_json.dumps(data))
    config_path.touch()

    import sherpa
    sherpa._reset_config()

    import sherpa.telemetry as tel
    monkeypatch.setattr(tel, "daily_active_minutes", lambda date_str=None: 6.0)

    status = burnout_status()
    assert status["threshold"] == 5
    assert status["blocked"] is True
