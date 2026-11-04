from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sherpa.agent_loop import parse_json_object, run_tool_loop
from sherpa.config import get_config
from sherpa.llm_router import get_provider
from sherpa.scout import FETCH_RESUME_TOOL, SCOUT_REGISTRY
from sherpa.workspace import workspace_paths

REQUIRED_FIELDS = {"company", "state"}

_EXTRACTION_PROMPT = """\
Extract the job application details from the user's message and return ONLY a JSON object with these keys:
- company (string, required): company name
- role (string): job title or role
- state (string, required): one of applied/phone_screen/oa/interview/offer/rejected
- date (string): ISO date (YYYY-MM-DD), use today if not specified
- notes (string): any extra context

Return only the raw JSON object, no markdown, no explanation."""


@dataclass
class ValidationGap:
    missing: list[str] = field(default_factory=list)


def track(text: str) -> dict | ValidationGap:
    provider = get_provider("extraction")
    messages = [
        {"role": "system", "content": _EXTRACTION_PROMPT},
        {"role": "user", "content": text},
    ]
    raw = run_tool_loop(
        provider=provider,
        messages=messages,
        tools=[FETCH_RESUME_TOOL],
        registry=SCOUT_REGISTRY,
    )

    entry = parse_json_object(raw)

    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        return ValidationGap(missing=sorted(missing))

    sheet = _get_sheet()
    if sheet is not None:
        try:
            _append_sheet(sheet, entry)
            return entry
        except Exception as exc:
            print(f"sherpa tracker: sheet write failed ({exc}), falling back to JSONL", file=sys.stderr)

    _append_jsonl(entry)
    return entry


def _append_jsonl(entry: dict) -> None:
    path = workspace_paths()["applications"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _get_sheet():
    cfg = get_config()
    tracker_cfg = cfg.get("tracker") or {}
    spreadsheet_url = tracker_cfg.get("spreadsheet_url") if isinstance(tracker_cfg, dict) else None
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
    headers = ["company", "role", "state", "date", "notes"]
    row = [str(entry.get(h, "")) for h in headers]
    sheet.append_row(row)
