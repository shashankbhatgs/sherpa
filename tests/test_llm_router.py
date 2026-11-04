import json
import subprocess

import pytest

import sherpa
import sherpa.workspace as ws
from sherpa.agent_loop import run_tool_loop
from sherpa.llm_router import (
    ClaudeCodeProvider,
    FallbackProvider,
    LLMProvider,
    OllamaProvider,
    ProviderError,
    get_provider,
)

FETCH_TOOL = {
    "type": "function",
    "function": {"name": "fetch_full_resume", "description": "Read the resume.", "parameters": {"type": "object", "properties": {}}},
}


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


def _edit_config(workspace, mutate) -> None:
    path = workspace / "config.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))
    sherpa._reset_config()


def _ollama_only(workspace) -> None:
    def mutate(data):
        for tier in data["model_routing"].values():
            tier.clear()
            tier.update({"provider": "ollama", "model": "qwen2.5:7b"})

    _edit_config(workspace, mutate)


def _cli_result(result: str = "", is_error: bool = False, subtype: str = "success") -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": is_error, "result": result})


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ── Routing ───────────────────────────────────────────────────────────────────


def test_default_tiers_use_claude_code_with_ollama_fallback(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.llm_router.ollama.Client")

    for tier in ("default", "extraction", "interviewer"):
        provider = get_provider(tier)
        assert isinstance(provider, FallbackProvider)
        assert isinstance(provider._primary, ClaudeCodeProvider)
        assert isinstance(provider._backup, OllamaProvider)
    assert get_provider("extraction")._primary._model == "haiku"


def test_claude_code_settings_come_from_config(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.llm_router.ollama.Client")
    _edit_config(
        tmp_workspace,
        lambda d: d["claude_code"].update({"command": "/opt/claude", "config_dir": "~/.claude-personal", "timeout_seconds": 30}),
    )

    primary = get_provider("default")._primary

    assert primary._command == "/opt/claude"
    assert primary._config_dir == "~/.claude-personal"
    assert primary._timeout == 30


def test_tier_without_fallback_returns_bare_provider(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.llm_router.ollama.Client")
    _edit_config(tmp_workspace, lambda d: d["model_routing"]["default"].pop("fallback"))

    assert isinstance(get_provider("default"), ClaudeCodeProvider)


def test_get_provider_returns_ollama(tmp_workspace, mocker):
    ws.init_workspace()
    _ollama_only(tmp_workspace)
    mock_client = mocker.patch("sherpa.llm_router.ollama.Client")

    provider = get_provider("default")
    assert isinstance(provider, OllamaProvider)
    mock_client.assert_called_once_with(host="http://localhost:11434")


def test_get_provider_uses_tier_model(tmp_workspace, mocker):
    ws.init_workspace()
    _ollama_only(tmp_workspace)
    mock_instance = mocker.patch("sherpa.llm_router.ollama.Client").return_value
    mock_instance.chat.return_value = _make_fake_response()

    get_provider("extraction").chat([{"role": "user", "content": "test"}])
    assert mock_instance.chat.call_args.kwargs["model"] == "qwen2.5:7b"


def test_provider_satisfies_protocol(tmp_workspace, mocker):
    ws.init_workspace()
    mocker.patch("sherpa.llm_router.ollama.Client")
    assert isinstance(get_provider("default"), LLMProvider)
    assert isinstance(ClaudeCodeProvider(model="sonnet"), LLMProvider)


def test_unknown_tier_raises(tmp_workspace):
    ws.init_workspace()
    with pytest.raises(KeyError, match="nonexistent"):
        get_provider("nonexistent")


def test_unknown_provider_raises(tmp_workspace):
    ws.init_workspace()
    _edit_config(tmp_workspace, lambda d: d["model_routing"]["default"].update({"provider": "openai"}))
    with pytest.raises(NotImplementedError, match="openai"):
        get_provider("default")


# ── Ollama ────────────────────────────────────────────────────────────────────


def test_chat_returns_message_dict(tmp_workspace, mocker):
    ws.init_workspace()
    _ollama_only(tmp_workspace)
    mock_instance = mocker.patch("sherpa.llm_router.ollama.Client").return_value
    mock_instance.chat.return_value = _make_fake_response("hello sherpa")

    result = get_provider("default").chat([{"role": "user", "content": "hi"}])
    assert result["message"]["content"] == "hello sherpa"
    assert "tool_calls" in result["message"]


def test_chat_passes_tools(tmp_workspace, mocker):
    ws.init_workspace()
    _ollama_only(tmp_workspace)
    mock_instance = mocker.patch("sherpa.llm_router.ollama.Client").return_value
    mock_instance.chat.return_value = _make_fake_response()

    get_provider("default").chat([{"role": "user", "content": "go"}], tools=[FETCH_TOOL])
    assert mock_instance.chat.call_args.kwargs["tools"] == [FETCH_TOOL]


# ── Claude Code ───────────────────────────────────────────────────────────────


def test_claude_code_invocation(mocker):
    run = mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result("hi there")))
    provider = ClaudeCodeProvider(model="sonnet", command="claude", config_dir="~/.claude-personal", timeout_seconds=42)

    result = provider.chat([{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hello"}])

    assert result == {"message": {"content": "hi there", "tool_calls": None}}
    args = run.call_args.args[0]
    assert args[:2] == ["claude", "-p"]
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--tools") + 1] == ""  # Claude Code's own tools are disabled
    assert args[args.index("--system-prompt") + 1] == "Be brief."
    assert "--no-session-persistence" in args
    kwargs = run.call_args.kwargs
    assert kwargs["input"] == "hello"
    assert kwargs["timeout"] == 42
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"].endswith("/.claude-personal")
    assert not kwargs["env"]["CLAUDE_CONFIG_DIR"].startswith("~")


def test_claude_code_without_config_dir_inherits_env(mocker, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/from/env")
    run = mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result("ok")))

    ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "hi"}])

    assert run.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == "/from/env"
    assert "--system-prompt" not in run.call_args.args[0]


def test_claude_code_tool_call_is_translated(mocker):
    reply = '{"tool_call": {"name": "fetch_full_resume", "arguments": {}}}'
    run = mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result(reply)))

    result = ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "go"}], tools=[FETCH_TOOL])

    assert result["message"]["tool_calls"] == [{"function": {"name": "fetch_full_resume", "arguments": {}}}]
    system = run.call_args.args[0][run.call_args.args[0].index("--system-prompt") + 1]
    assert "fetch_full_resume: Read the resume." in system


def test_claude_code_fenced_tool_call_is_translated(mocker):
    reply = '```json\n{"tool_call": {"name": "fetch_full_resume"}}\n```'
    mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result(reply)))

    result = ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "go"}], tools=[FETCH_TOOL])

    assert result["message"]["tool_calls"][0]["function"]["name"] == "fetch_full_resume"


@pytest.mark.parametrize(
    "reply",
    [
        '{"tool_call": {"name": "delete_everything", "arguments": {}}}',  # unknown tool
        'Here you go: {"tool_call": {"name": "fetch_full_resume"}}',  # not the whole reply
        '{"company": "Stripe"}',  # ordinary JSON answer
    ],
)
def test_claude_code_non_tool_replies_are_content(mocker, reply):
    mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result(reply)))

    result = ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "go"}], tools=[FETCH_TOOL])

    assert result["message"] == {"content": reply, "tool_calls": None}


def test_claude_code_without_tools_never_returns_tool_calls(mocker):
    reply = '{"tool_call": {"name": "fetch_full_resume"}}'
    mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result(reply)))

    result = ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "go"}])

    assert result["message"]["tool_calls"] is None


def test_claude_code_renders_history_with_tool_results(mocker):
    run = mocker.patch("sherpa.llm_router.subprocess.run", return_value=_completed(_cli_result("done")))
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Job post"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "fetch_full_resume", "arguments": {}}}]},
        {"role": "tool", "name": "fetch_full_resume", "content": "# Alice resume"},
    ]

    ClaudeCodeProvider(model="sonnet").chat(messages, tools=[FETCH_TOOL])

    prompt = run.call_args.kwargs["input"]
    assert prompt.index("[user]\nJob post") < prompt.index("[assistant called tool: fetch_full_resume]")
    assert "[tool result: fetch_full_resume]\n# Alice resume" in prompt
    assert "sys" not in prompt


def test_claude_code_drives_the_agent_loop(mocker):
    replies = iter([
        _completed(_cli_result('{"tool_call": {"name": "fetch_full_resume", "arguments": {}}}')),
        _completed(_cli_result('{"company": "Stripe"}')),
    ])
    run = mocker.patch("sherpa.llm_router.subprocess.run", side_effect=lambda *a, **k: next(replies))

    out = run_tool_loop(
        ClaudeCodeProvider(model="sonnet"),
        [{"role": "user", "content": "go"}],
        tools=[FETCH_TOOL],
        registry={"fetch_full_resume": lambda: "RESUME TEXT"},
    )

    assert out == '{"company": "Stripe"}'
    assert "RESUME TEXT" in run.call_args_list[1].kwargs["input"]


@pytest.mark.parametrize(
    "side_effect, match",
    [
        (FileNotFoundError(), "not found"),
        (subprocess.TimeoutExpired(cmd="claude", timeout=5), "timed out"),
        (_completed("", returncode=1, stderr="boom"), "exited 1: boom"),
        (_completed(_cli_result("Not logged in", is_error=True, subtype="error_during_execution")), "Not logged in"),
        (_completed(_cli_result("", is_error=False, subtype="error_max_turns")), "returned an error"),
    ],
)
def test_claude_code_failures_raise_provider_error(mocker, side_effect, match):
    mock_run = mocker.patch("sherpa.llm_router.subprocess.run")
    if isinstance(side_effect, BaseException):
        mock_run.side_effect = side_effect
    else:
        mock_run.return_value = side_effect

    with pytest.raises(ProviderError, match=match):
        ClaudeCodeProvider(model="sonnet").chat([{"role": "user", "content": "hi"}])


# ── Fallback ──────────────────────────────────────────────────────────────────


class _Stub:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    def stream(self, messages, **kwargs):
        yield ""


def test_fallback_uses_primary_when_it_works():
    primary, backup = _Stub(result={"message": {"content": "claude"}}), _Stub(result={"message": {"content": "ollama"}})

    result = FallbackProvider(primary, backup).chat([], tools=[FETCH_TOOL])

    assert result["message"]["content"] == "claude"
    assert backup.calls == 0


def test_fallback_switches_to_backup_on_provider_error(capsys):
    primary = _Stub(error=ProviderError("Claude Code timed out"))
    backup = _Stub(result={"message": {"content": "ollama"}})

    result = FallbackProvider(primary, backup).chat([])

    assert result["message"]["content"] == "ollama"
    assert "falling back" in capsys.readouterr().err


def test_fallback_reports_both_failures():
    primary = _Stub(error=ProviderError("Claude Code CLI not found"))
    backup = _Stub(error=ConnectionError("connection refused"))

    with pytest.raises(ProviderError, match="not found.*Backup model also failed: connection refused"):
        FallbackProvider(primary, backup).chat([])


def test_fallback_does_not_hide_programming_errors():
    primary = _Stub(error=TypeError("bug"))
    backup = _Stub(result={"message": {"content": "ollama"}})

    with pytest.raises(TypeError):
        FallbackProvider(primary, backup).chat([])
    assert backup.calls == 0
