import pytest
import sherpa.workspace as ws
from sherpa.llm_router import get_provider, OllamaProvider, LLMProvider


def _make_fake_response(content="hello", tool_calls=None):
    class Message:
        pass
    class Response:
        pass
    msg = Message()
    msg.content = content
    msg.tool_calls = tool_calls
    resp = Response()
    resp.message = msg
    return resp


def test_get_provider_returns_ollama(tmp_workspace, mocker):
    ws.init_workspace()
    mock_client = mocker.patch("sherpa.llm_router.ollama.Client")
    mock_instance = mock_client.return_value
    mock_instance.chat.return_value = _make_fake_response("hi")

    provider = get_provider("default")
    assert isinstance(provider, OllamaProvider)
    mock_client.assert_called_once_with(host="http://localhost:11434")


def test_get_provider_uses_tier_model(tmp_workspace, mocker):
    ws.init_workspace()
    mock_client = mocker.patch("sherpa.llm_router.ollama.Client")
    mock_instance = mock_client.return_value
    mock_instance.chat.return_value = _make_fake_response()

    provider = get_provider("extraction")
    result = provider.chat([{"role": "user", "content": "test"}])
    mock_instance.chat.assert_called_once()
    call_kwargs = mock_instance.chat.call_args
    assert call_kwargs.kwargs["model"] == "qwen2.5:7b"


def test_provider_satisfies_protocol(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.llm_router.ollama.Client")
    provider = get_provider("default")
    assert isinstance(provider, LLMProvider)


def test_unknown_tier_raises(tmp_workspace):
    ws.init_workspace()
    with pytest.raises(KeyError, match="nonexistent"):
        get_provider("nonexistent")


def test_unknown_provider_raises(tmp_workspace, mocker):
    ws.init_workspace()
    import json
    from sherpa import get_config, _reset_config
    _reset_config()

    config_path = tmp_workspace / "config.json"
    data = json.loads(config_path.read_text())
    data["model_routing"]["default"]["provider"] = "openai"
    config_path.write_text(json.dumps(data))
    config_path.touch()

    _reset_config()
    with pytest.raises(NotImplementedError, match="openai"):
        get_provider("default")


def test_chat_returns_message_dict(tmp_workspace, mocker):
    ws.init_workspace()
    mock_client = mocker.patch("sherpa.llm_router.ollama.Client")
    mock_instance = mock_client.return_value
    mock_instance.chat.return_value = _make_fake_response("hello sherpa")

    provider = get_provider("default")
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result["message"]["content"] == "hello sherpa"
    assert "tool_calls" in result["message"]


def test_chat_passes_tools(tmp_workspace, mocker):
    ws.init_workspace()
    mock_client = mocker.patch("sherpa.llm_router.ollama.Client")
    mock_instance = mock_client.return_value
    mock_instance.chat.return_value = _make_fake_response()

    tools = [{"type": "function", "function": {"name": "fetch_resume", "parameters": {}}}]
    provider = get_provider("default")
    provider.chat([{"role": "user", "content": "go"}], tools=tools)

    call_kwargs = mock_instance.chat.call_args.kwargs
    assert call_kwargs["tools"] == tools
