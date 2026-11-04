from pathlib import Path
import sherpa.workspace as ws


def test_init_creates_tree(tmp_workspace):
    ws.init_workspace()
    assert (tmp_workspace).is_dir()
    assert (tmp_workspace / "interviews").is_dir()
    assert (tmp_workspace / "outreach").is_dir()
    assert (tmp_workspace / "question_bank").is_dir()
    assert (tmp_workspace / "config.json").is_file()
    assert (tmp_workspace / "profile.json").is_file()
    assert (tmp_workspace / "resume.md").is_file()
    for name in ["hld_distributed_cache.md", "lld_parking_lot.md", "dsa_lru_cache.md"]:
        assert (tmp_workspace / "question_bank" / name).is_file()


def test_init_reports_created(tmp_workspace):
    result = ws.init_workspace()
    assert all(v == "created" for v in result.values())


def test_init_idempotent(tmp_workspace):
    ws.init_workspace()
    result = ws.init_workspace()
    assert all(v == "exists" for v in result.values())


def test_force_overwrites_seeds(tmp_workspace):
    ws.init_workspace()
    config_path = tmp_workspace / "config.json"
    config_path.write_text('{"overwritten": true}')

    ws.init_workspace(force=True)
    content = config_path.read_text()
    assert "overwritten" not in content


def test_force_never_touches_user_data(tmp_workspace):
    ws.init_workspace()
    apps = tmp_workspace / "applications.jsonl"
    apps.write_text('{"company": "Acme"}')

    ws.init_workspace(force=True)
    assert apps.read_text() == '{"company": "Acme"}'


def test_workspace_paths_keys(tmp_workspace):
    paths = ws.workspace_paths()
    for key in ["root", "config", "profile", "resume", "applications", "interviews", "outreach", "question_bank", "credentials"]:
        assert key in paths
        assert isinstance(paths[key], Path)
