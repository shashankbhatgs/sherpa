import pytest
import sherpa.workspace as ws
import sherpa


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "SHERPA_DIR", tmp_path / ".sherpa")
    sherpa._reset_config()
    yield tmp_path / ".sherpa"
    sherpa._reset_config()


@pytest.fixture(autouse=True)
def _no_real_clipboard(monkeypatch):
    """CLI tests must never overwrite the developer's clipboard."""
    import sherpa.cli

    monkeypatch.setattr(sherpa.cli, "_CLIPBOARD_COMMANDS", ())
