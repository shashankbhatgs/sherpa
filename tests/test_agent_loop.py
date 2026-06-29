import pytest
from sherpa.agent_loop import run_tool_loop


class _FakeFunction:
    def __init__(self, name, arguments=None):
        self.name = name
        self.arguments = arguments or {}


class _FakeToolCall:
    def __init__(self, name, arguments=None):
        self.function = _FakeFunction(name, arguments or {})


def _make_provider(responses):
    """responses: list of dicts with keys 'content' and optional 'tool_calls'."""
    calls = iter(responses)

    class FakeProvider:
        def chat(self, messages, tools=None, **kwargs):
            resp = next(calls)
            return {"message": {"content": resp.get("content", ""), "tool_calls": resp.get("tool_calls")}}

        def stream(self, messages, **kwargs):
            yield ""

    return FakeProvider()


def test_plain_response_returns_immediately():
    provider = _make_provider([{"content": "hello"}])
    result = run_tool_loop(provider, [{"role": "user", "content": "hi"}], [], {})
    assert result == "hello"


def test_tool_call_dispatched_and_result_appended():
    dispatched = []

    def my_tool():
        dispatched.append(True)
        return "tool result"

    provider = _make_provider([
        {"content": "", "tool_calls": [_FakeToolCall("my_tool")]},
        {"content": "done"},
    ])
    result = run_tool_loop(
        provider,
        [{"role": "user", "content": "go"}],
        [{"type": "function", "function": {"name": "my_tool", "parameters": {}}}],
        {"my_tool": my_tool},
    )
    assert result == "done"
    assert dispatched == [True]


def test_tool_result_appended_to_messages():
    messages_seen = []

    def capture_tool():
        return "captured"

    class RecordingProvider:
        call_count = 0

        def chat(self, messages, tools=None, **kwargs):
            self.call_count += 1
            messages_seen.extend(messages)
            if self.call_count == 1:
                return {"message": {"content": "", "tool_calls": [_FakeToolCall("capture_tool")]}}
            return {"message": {"content": "final", "tool_calls": None}}

        def stream(self, messages, **kwargs):
            yield ""

    provider = RecordingProvider()
    run_tool_loop(provider, [{"role": "user", "content": "x"}], [], {"capture_tool": capture_tool})
    roles = [m["role"] for m in messages_seen]
    assert "tool" in roles


def test_unknown_tool_does_not_crash():
    provider = _make_provider([
        {"content": "", "tool_calls": [_FakeToolCall("nonexistent")]},
        {"content": "ok"},
    ])
    result = run_tool_loop(provider, [{"role": "user", "content": "x"}], [], {})
    assert result == "ok"


def test_max_iterations_stops_loop():
    class InfiniteProvider:
        def chat(self, messages, tools=None, **kwargs):
            return {"message": {"content": "", "tool_calls": [_FakeToolCall("t")]}}

        def stream(self, messages, **kwargs):
            yield ""

    call_count = 0

    def t():
        nonlocal call_count
        call_count += 1
        return "x"

    run_tool_loop(InfiniteProvider(), [{"role": "user", "content": "x"}], [], {"t": t}, max_iterations=3)
    assert call_count == 3
