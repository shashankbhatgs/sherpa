"""Job application tracker: plain-English updates to structured entries (spec Module 2).

The model mirrors the user's Google Sheet: a pipeline `stage` (where the application is) and an
optional `status` (outcome or referral state), plus contact, job ID, and portal link. Stage and
status vocabularies, sheet column names, and the sheet's date format come from `config.tracker`.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

from sherpa.agent_loop import parse_json_object, run_tool_loop
from sherpa.config import get_config
from sherpa.llm_router import get_provider
from sherpa.workspace import workspace_paths

REQUIRED_FIELDS = ("company", "stage")
_TEXT_FIELDS = ("company", "role", "stage", "status", "contact", "job_id", "link", "date", "notes")

DEFAULT_STAGES = ("Not applied", "Applied", "Phone call", "Take Home Assessment", "Interview", "Offer")
DEFAULT_STATUSES = ("Asked for referral", "Got referral", "Waiting", "Didn't hear back", "Rejected")
DEFAULT_COLUMNS = {
    "company": "Company",
    "role": "Role",
    "stage": "Stage",
    "status": "Status",
    "contact": "Interviewer/HA/POC Name or Phone number (optional)",
    "job_id": "Job ID",
    "link": "Link to portal",
    "date": "Date",
}
DEFAULT_DATE_FORMAT = "%m/%d/%Y"

# Models sometimes fill unknown fields with these instead of omitting them.
_PLACEHOLDERS = {"unknown", "n/a", "na", "none", "null", "not specified", "not provided", "not stated", "unspecified", "tbd", "-", "?"}

_EXTRACTION_PROMPT = """\
Extract a job application update from the user's message. Today is {today}.
Return ONLY a JSON object with these keys:
- company (string, required): company name
- role (string): job title, e.g. "SDE 2"
- stage (string, required): where the application is, exactly one of: {stages}.
  Use "Not applied" when the user has only reached out or asked for a referral.
- status (string): outcome or referral state, exactly one of: {statuses}
- contact (string): recruiter, interviewer, or referrer name or phone number
- job_id (string): job ID or requisition code
- link (string): URL of the job posting or application portal
- date (string): ISO date (YYYY-MM-DD) the update happened; resolve "yesterday" etc. against today
- notes (string): any other useful context

If a value is not stated in the message, leave its key out. Never guess, and never write placeholders
such as "Unknown" or "N/A".
Return only the raw JSON object, no markdown, no explanation."""


@dataclass
class ValidationGap:
    """Required fields could not be extracted; nothing was written.

    `partial` holds what was extracted, so the caller can ask for just the
    missing fields and finish with `complete_track()`.
    """

    missing: list[str] = field(default_factory=list)
    partial: dict = field(default_factory=dict)


def application_stages() -> tuple[str, ...]:
    """Allowed pipeline stages, from config.tracker.stages or the defaults."""
    return _configured_labels("stages", DEFAULT_STAGES)


def application_statuses() -> tuple[str, ...]:
    """Allowed outcome/referral statuses, from config.tracker.statuses or the defaults."""
    return _configured_labels("statuses", DEFAULT_STATUSES)


def track(text: str) -> dict | ValidationGap:
    """Turn a plain-English update into an entry and record it, or return what is missing."""
    prompt = _EXTRACTION_PROMPT.format(
        today=date.today().isoformat(),
        stages=", ".join(f'"{s}"' for s in application_stages()),
        statuses=", ".join(f'"{s}"' for s in application_statuses()),
    )
    raw = run_tool_loop(
        provider=get_provider("extraction"),
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        tools=[],
        registry={},
    )
    return _record(parse_json_object(raw))


def complete_track(partial: dict, answers: dict[str, str]) -> dict | ValidationGap:
    """Fill the missing fields of a ValidationGap with the user's answers and record the entry."""
    filled = {key: value for key, value in answers.items() if isinstance(value, str) and value.strip()}
    return _record({**partial, **filled})


def load_applications() -> list[dict]:
    """All entries in the local ledger, oldest first; malformed lines are skipped."""
    path = workspace_paths()["applications"]
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _record(raw_entry: dict) -> dict | ValidationGap:
    """Validate an entry and write it, or return a ValidationGap without writing anything."""
    entry = _normalize(raw_entry)
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        return ValidationGap(missing=missing, partial=entry)
    entry.setdefault("date", date.today().isoformat())
    entry["tracked_at"] = datetime.now().isoformat(timespec="seconds")

    # The local ledger is the source of truth (stats read it); the sheet is a mirror.
    _append_jsonl(entry)
    sheet = _get_sheet()
    if sheet is not None:
        try:
            _append_sheet(sheet, entry)
        except Exception as exc:
            print(f"sherpa tracker: sheet write failed ({exc}); saved locally only", file=sys.stderr)
    return entry


def _normalize(raw: dict) -> dict:
    """Keep known string fields, trimmed; snap stage/status onto the allowed labels or drop them."""
    entry = {}
    for key in _TEXT_FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and " ".join(value.split()).lower().strip(".") not in _PLACEHOLDERS | {""}:
            entry[key] = " ".join(value.split())
    for key, allowed in (("stage", application_stages()), ("status", application_statuses())):
        label = _match_label(entry.get(key, ""), allowed)
        if label:
            entry[key] = label
        else:
            entry.pop(key, None)
    return entry


def _match_label(value: str, allowed: tuple[str, ...]) -> str | None:
    wanted = _label_key(value)
    return next((label for label in allowed if _label_key(label) == wanted), None) if wanted else None


def _label_key(label: str) -> str:
    """Comparison key: case, spacing, hyphens/underscores, and apostrophe style do not matter."""
    cleaned = label.casefold().replace("’", "'").replace("-", " ").replace("_", " ")
    return " ".join(cleaned.split())


def _tracker_config() -> dict:
    cfg = get_config().get("tracker") or {}
    return cfg if isinstance(cfg, dict) else {}


def _configured_labels(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    labels = _tracker_config().get(key)
    if isinstance(labels, list):
        cleaned = tuple(" ".join(l.split()) for l in labels if isinstance(l, str) and l.strip())
        if cleaned:
            return cleaned
    return default


def _append_jsonl(entry: dict) -> None:
    path = workspace_paths()["applications"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _get_sheet():
    spreadsheet_url = _tracker_config().get("spreadsheet_url")
    if not spreadsheet_url:
        return None

    credentials_path = workspace_paths()["credentials"]
    if not credentials_path.exists():
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(credentials_path), scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_url(spreadsheet_url).sheet1
    except Exception as exc:
        print(f"sherpa tracker: could not open sheet ({exc})", file=sys.stderr)
        return None


def _append_sheet(sheet, entry: dict) -> None:
    """Append one row, placing each field under its configured column header (order does not matter)."""
    columns = {**DEFAULT_COLUMNS, **(_tracker_config().get("columns") or {})}
    field_by_header = {_label_key(header): name for name, header in columns.items() if isinstance(header, str)}
    headers = sheet.row_values(1)
    fields = [field_by_header.get(_label_key(h)) for h in headers]
    if not any(fields):
        raise ValueError("none of the sheet's row-1 headers match config.tracker.columns")
    sheet.append_row([_sheet_value(entry, name) for name in fields], value_input_option="USER_ENTERED")


def _sheet_value(entry: dict, name: str | None) -> str:
    if name is None:
        return ""
    value = entry.get(name, "")
    if name == "date" and value:
        fmt = _tracker_config().get("date_format") or DEFAULT_DATE_FORMAT
        try:
            return date.fromisoformat(value).strftime(fmt)
        except ValueError:
            return value
    return value
