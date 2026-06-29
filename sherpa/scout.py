from __future__ import annotations

import json
from typing import Callable

from sherpa.workspace import workspace_paths


def resume_index() -> str:
    path = workspace_paths()["profile"]
    try:
        profile = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        profile = {}

    name = profile.get("name", "")
    years = profile.get("years_experience", 0)
    competencies = ", ".join(profile.get("core_competencies", []))
    roles = ", ".join(profile.get("target_roles", []))
    return f"Name: {name}\nYears experience: {years}\nCore competencies: {competencies}\nTarget roles: {roles}"


def fetch_full_resume() -> str:
    path = workspace_paths()["resume"]
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


FETCH_RESUME_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "fetch_full_resume",
        "description": "Fetch the full resume Markdown from ~/.sherpa/resume.md",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

SCOUT_REGISTRY: dict[str, Callable] = {
    "fetch_full_resume": fetch_full_resume,
}
