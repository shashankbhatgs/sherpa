from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sherpa.config import get_config
from sherpa.workspace import workspace_paths

_TURN_TS = re.compile(r"^## (?:user|assistant) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", re.MULTILINE)
_IDLE_GAP_MINUTES = 10


def _session_date(path: Path) -> str:
    """Extract YYYYMMDD from session_YYYYMMDD_N.md filename."""
    parts = path.stem.split("_")
    return parts[1] if len(parts) >= 2 else ""


def _session_minutes(path: Path) -> float:
    """Sum active minutes in a single session file (gaps >= _IDLE_GAP_MINUTES excluded)."""
    text = path.read_text()
    timestamps = [
        datetime.fromisoformat(m.group(1))
        for m in _TURN_TS.finditer(text)
    ]
    if len(timestamps) < 2:
        return 0.0
    timestamps.sort()
    total = 0.0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds() / 60.0
        if gap < _IDLE_GAP_MINUTES:
            total += gap
    return total


def daily_active_minutes(date_str: str | None = None) -> float:
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    interviews_dir = workspace_paths()["interviews"]
    if not interviews_dir.exists():
        return 0.0
    total = 0.0
    for path in interviews_dir.glob(f"session_{date_str}_*.md"):
        try:
            total += _session_minutes(path)
        except Exception:
            pass
    return total


def _last_turn_timestamp() -> datetime | None:
    """Return the latest turn timestamp across all session files from today."""
    date_str = datetime.now().strftime("%Y%m%d")
    interviews_dir = workspace_paths()["interviews"]
    if not interviews_dir.exists():
        return None
    latest: datetime | None = None
    for path in interviews_dir.glob(f"session_{date_str}_*.md"):
        try:
            text = path.read_text()
            for m in _TURN_TS.finditer(text):
                ts = datetime.fromisoformat(m.group(1))
                if latest is None or ts > latest:
                    latest = ts
        except Exception:
            pass
    return latest


@dataclass
class DailyActivity:
    interview_minutes: float
    applications: int
    outreach_drafts: int


def daily_activity(day: date | None = None) -> DailyActivity:
    """Today's (or `day`'s) activity. Only interview minutes count toward the burnout threshold."""
    from sherpa.tracker import load_applications  # lazy: tracker pulls in the LLM stack

    day = day or date.today()
    outreach_dir = workspace_paths()["outreach"]
    drafts = len(list(outreach_dir.glob(f"outreach_{day:%Y%m%d}_*.md"))) if outreach_dir.exists() else 0
    applications = sum(1 for e in load_applications() if str(e.get("tracked_at", "")).startswith(day.isoformat()))
    return DailyActivity(
        interview_minutes=daily_active_minutes(f"{day:%Y%m%d}"),
        applications=applications,
        outreach_drafts=drafts,
    )


def burnout_status() -> dict:
    cfg = get_config()
    burnout_cfg = cfg.get("burnout") or {}
    threshold: int = burnout_cfg.get("daily_minutes_threshold", 180) if isinstance(burnout_cfg, dict) else 180
    cooldown_hours: int = burnout_cfg.get("cooldown_hours", 12) if isinstance(burnout_cfg, dict) else 12

    daily_minutes = daily_active_minutes()
    blocked = daily_minutes >= threshold

    unblocks_at: str | None = None
    if blocked:
        last_ts = _last_turn_timestamp()
        if last_ts is not None:
            unblocks_at = (last_ts + timedelta(hours=cooldown_hours)).isoformat(timespec="seconds")

    return {
        "daily_minutes": daily_minutes,
        "threshold": threshold,
        "blocked": blocked,
        "cooldown_hours": cooldown_hours,
        "unblocks_at": unblocks_at,
    }


def is_blocked() -> bool:
    return burnout_status()["blocked"]
