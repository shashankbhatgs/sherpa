import json
import sherpa.workspace as ws
from sherpa.config import Config, get_config, _reset_config


def test_reads_config(tmp_workspace):
    ws.init_workspace()
    cfg = Config(tmp_workspace / "config.json")
    assert cfg.get("ollama_host") == "http://localhost:11434"


def test_hot_reload(tmp_workspace):
    ws.init_workspace()
    config_path = tmp_workspace / "config.json"
    cfg = Config(config_path)

    data = json.loads(config_path.read_text())
    data["ollama_host"] = "http://localhost:9999"
    config_path.write_text(json.dumps(data))
    # touch mtime to ensure change is detected
    config_path.touch()

    assert cfg.get("ollama_host") == "http://localhost:9999"


def test_mid_write_json_error_keeps_last_good(tmp_workspace, capsys):
    ws.init_workspace()
    config_path = tmp_workspace / "config.json"
    cfg = Config(config_path)
    original = cfg.get("ollama_host")

    config_path.write_text("{invalid json")
    config_path.touch()

    assert cfg.get("ollama_host") == original
    captured = capsys.readouterr()
    assert "JSON decode error" in captured.err


def test_get_config_singleton(tmp_workspace):
    ws.init_workspace()
    a = get_config()
    b = get_config()
    assert a is b


def test_reset_config(tmp_workspace):
    ws.init_workspace()
    a = get_config()
    _reset_config()
    b = get_config()
    assert a is not b


def test_data_property(tmp_workspace):
    ws.init_workspace()
    cfg = Config(tmp_workspace / "config.json")
    d = cfg.data
    assert isinstance(d, dict)
    assert "model_routing" in d


def test_reload_method(tmp_workspace):
    ws.init_workspace()
    config_path = tmp_workspace / "config.json"
    cfg = Config(config_path)

    data = json.loads(config_path.read_text())
    data["ollama_host"] = "http://forced:1234"
    config_path.write_text(json.dumps(data))

    cfg.reload()
    assert cfg.get("ollama_host") == "http://forced:1234"
