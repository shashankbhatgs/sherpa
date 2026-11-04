from __future__ import annotations

from importlib.resources import files
from pathlib import Path

SHERPA_DIR: Path = Path.home() / ".sherpa"

_USER_DATA = {"applications.jsonl", "interviews", "outreach", "credentials.json"}


def workspace_paths() -> dict[str, Path]:
    return {
        "root": SHERPA_DIR,
        "config": SHERPA_DIR / "config.json",
        "profile": SHERPA_DIR / "profile.json",
        "resume": SHERPA_DIR / "resume.md",
        "applications": SHERPA_DIR / "applications.jsonl",
        "interviews": SHERPA_DIR / "interviews",
        "outreach": SHERPA_DIR / "outreach",
        "question_bank": SHERPA_DIR / "question_bank",
        "credentials": SHERPA_DIR / "credentials.json",
    }


def init_workspace(force: bool = False) -> dict[str, str]:
    paths = workspace_paths()
    result: dict[str, str] = {}

    paths["root"].mkdir(exist_ok=True)
    paths["interviews"].mkdir(exist_ok=True)
    paths["outreach"].mkdir(exist_ok=True)
    paths["question_bank"].mkdir(exist_ok=True)

    seeds_pkg = files("sherpa.seeds")

    _copy_seed(seeds_pkg / "config.default.json", paths["config"], force, result)
    _copy_seed(seeds_pkg / "profile.template.json", paths["profile"], force, result)
    _copy_seed(seeds_pkg / "resume.template.md", paths["resume"], force, result)

    qb_pkg = files("sherpa.seeds.question_bank")
    for seed_name in ["hld_distributed_cache.md", "lld_parking_lot.md", "dsa_lru_cache.md"]:
        dest = paths["question_bank"] / seed_name
        _copy_seed(qb_pkg / seed_name, dest, force, result)

    return result


def _copy_seed(src, dest: Path, force: bool, result: dict[str, str]) -> None:
    dest_str = str(dest)
    is_user_data = dest.name in _USER_DATA or dest.parent.name in _USER_DATA

    if dest.exists() and (not force or is_user_data):
        result[dest_str] = "exists"
        return

    dest.write_bytes(src.read_bytes())
    result[dest_str] = "created"
