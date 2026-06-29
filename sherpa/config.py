from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sherpa.workspace import workspace_paths

_singleton: Config | None = None


class Config:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: float = 0.0
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            text = self._path.read_text()
            self._data = json.loads(text)
            self._mtime = self._path.stat().st_mtime
        except json.JSONDecodeError as exc:
            print(f"sherpa config: JSON decode error, keeping last known-good value: {exc}", file=sys.stderr)
        except FileNotFoundError:
            pass

    def _maybe_reload(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime != self._mtime:
            self._load()

    def get(self, key: str, default: Any = None) -> Any:
        self._maybe_reload()
        return self._data.get(key, default)

    def reload(self) -> None:
        self._load()

    @property
    def data(self) -> dict:
        self._maybe_reload()
        return dict(self._data)


def get_config() -> Config:
    global _singleton
    if _singleton is None:
        _singleton = Config(workspace_paths()["config"])
    return _singleton


def _reset_config() -> None:
    global _singleton
    _singleton = None
