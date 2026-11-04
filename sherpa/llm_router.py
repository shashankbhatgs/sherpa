"""
LLM routing layer.

Providers: "claude_code" (the Claude Code CLI, primary) and "ollama" (local, backup).
Each tier in config.model_routing may name a "fallback" provider spec.

Tool-loop pattern (implemented in Phase 2 agent_loop.py):
  1. Build tools=[{"type": "function", "function": {...}}, ...] in Ollama format.
  2. Call provider.chat(messages, tools=tools).
  3. If response.message.tool_calls is non-empty:
       dispatch each call to a registry {name: callable}, append tool result message, loop.
  4. Otherwise: return response.message.content.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

import ollama

from sherpa.config import get_config


@runtime_checkable
class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict: ...

    def stream(
        self,
        messages: list[dict],
        **kwargs,
    ) -> Iterator[str]: ...


class OllamaProvider:
    def __init__(self, host: str, model: str) -> None:
        self._model = model
        self._client = ollama.Client(host=host)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        kwargs_merged = dict(kwargs)
        if tools is not None:
            kwargs_merged["tools"] = tools
        response = self._client.chat(model=self._model, messages=messages, **kwargs_merged)
        return {"message": {"content": response.message.content, "tool_calls": response.message.tool_calls}}

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        for chunk in self._client.chat(model=self._model, messages=messages, stream=True, **kwargs):
            content = chunk.message.content
            if content:
                yield content


class ProviderError(RuntimeError):
    """An LLM backend could not produce a response (not installed, logged out, timed out, bad output)."""


_DEFAULT_CLAUDE_TIMEOUT_SECONDS = 180

_TOOL_PROTOCOL = """\

## Tools

You can call these tools. Their results come back as [tool result] blocks in the conversation.
{tool_specs}

To call a tool, reply with ONLY this JSON and nothing else:
{{"tool_call": {{"name": "<tool name>", "arguments": {{}}}}}}
Otherwise, reply normally."""


class ClaudeCodeProvider:
    """Runs a prompt through the Claude Code CLI (`claude -p`), using the CLI's own login.

    Claude Code's built-in tools are disabled. Sherpa's tools are described in the system
    prompt, and a reply of the form {"tool_call": {...}} is returned as a tool call so
    run_tool_loop() works the same as with Ollama.
    """

    def __init__(
        self,
        model: str,
        command: str = "claude",
        config_dir: str | None = None,
        timeout_seconds: float = _DEFAULT_CLAUDE_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._command = command
        self._config_dir = config_dir
        self._timeout = timeout_seconds

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if tools:
            system += _TOOL_PROTOCOL.format(tool_specs=_describe_tools(tools))
        conversation = [m for m in messages if m["role"] != "system"]

        reply = self._run(system, _render_conversation(conversation))
        tool_call = _parse_tool_call(reply, tools) if tools else None
        if tool_call is not None:
            return {"message": {"content": "", "tool_calls": [tool_call]}}
        return {"message": {"content": reply, "tool_calls": None}}

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        yield self.chat(messages)["message"]["content"]

    def _run(self, system: str, prompt: str) -> str:
        args = [
            self._command, "-p",
            "--output-format", "json",
            "--model", self._model,
            "--tools", "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "",
        ]
        if system:
            args += ["--system-prompt", system]
        env = dict(os.environ)
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(Path(self._config_dir).expanduser())

        try:
            proc = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
                cwd=tempfile.gettempdir(),  # keep project CLAUDE.md files out of the prompt
            )
        except FileNotFoundError as exc:
            raise ProviderError(f"Claude Code CLI not found ({self._command!r}). Install it or fix claude_code.command.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"Claude Code timed out after {self._timeout:.0f}s") from exc

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            detail = (proc.stderr or proc.stdout).strip()[:300]
            raise ProviderError(f"Claude Code exited {proc.returncode}: {detail or 'no output'}") from exc

        if result.get("is_error") or result.get("subtype") != "success":
            detail = result.get("result") or result.get("terminal_reason") or "unknown error"
            raise ProviderError(f"Claude Code returned an error: {str(detail)[:300]}")
        return str(result.get("result") or "")


def _describe_tools(tools: list[dict]) -> str:
    lines = []
    for tool in tools:
        fn = tool.get("function", {})
        params = json.dumps(fn.get("parameters", {}).get("properties", {}))
        lines.append(f"- {fn.get('name')}: {fn.get('description', '')} Arguments: {params}")
    return "\n".join(lines)


def _render_conversation(messages: list[dict]) -> str:
    """Flatten chat history into one prompt. A lone user message is passed through unchanged."""
    if len(messages) == 1 and messages[0]["role"] == "user":
        return messages[0]["content"]

    blocks = []
    for m in messages:
        if m["role"] == "tool":
            blocks.append(f"[tool result: {m.get('name', 'tool')}]\n{m['content']}")
        elif m["role"] == "assistant" and m.get("tool_calls"):
            names = ", ".join(_tool_call_name(c) for c in m["tool_calls"])
            blocks.append(f"[assistant called tool: {names}]")
        else:
            blocks.append(f"[{m['role']}]\n{m['content']}")
    return "\n\n".join(blocks) + "\n\nReply as the assistant to the conversation above."


def _tool_call_name(call) -> str:
    fn = call.function if hasattr(call, "function") else call.get("function", {})
    return fn.name if hasattr(fn, "name") else fn.get("name", "tool")


def _parse_tool_call(reply: str, tools: list[dict]) -> dict | None:
    """Return a tool call if the whole reply is a {"tool_call": ...} object naming a known tool."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    call = data.get("tool_call") if isinstance(data, dict) else None
    if not isinstance(call, dict):
        return None
    known = {t.get("function", {}).get("name") for t in tools}
    if call.get("name") not in known:
        return None
    args = call.get("arguments")
    return {"function": {"name": call["name"], "arguments": args if isinstance(args, dict) else {}}}


class FallbackProvider:
    """Uses the primary provider and falls back to the backup when the primary fails."""

    def __init__(self, primary: LLMProvider, backup: LLMProvider) -> None:
        self._primary = primary
        self._backup = backup

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> dict:
        try:
            return self._primary.chat(messages, tools=tools, **kwargs)
        except ProviderError as primary_exc:
            print(f"sherpa: {primary_exc}; falling back to backup model", file=sys.stderr)
            try:
                return self._backup.chat(messages, tools=tools, **kwargs)
            except Exception as backup_exc:
                raise ProviderError(f"{primary_exc}. Backup model also failed: {backup_exc}") from backup_exc

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        yield self.chat(messages, **kwargs)["message"]["content"]


def get_provider(tier: str = "default") -> LLMProvider:
    cfg = get_config()
    routing: dict = cfg.get("model_routing", {})
    tier_cfg = routing.get(tier)
    if tier_cfg is None:
        raise KeyError(f"Unknown LLM tier: {tier!r}")

    provider = _build_provider(tier_cfg)
    fallback_cfg = tier_cfg.get("fallback")
    if isinstance(fallback_cfg, dict):
        return FallbackProvider(provider, _build_provider(fallback_cfg))
    return provider


def _build_provider(spec: dict) -> LLMProvider:
    cfg = get_config()
    provider_name = spec.get("provider", "ollama")

    if provider_name == "ollama":
        host = cfg.get("ollama_host", "http://localhost:11434")
        return OllamaProvider(host=host, model=spec.get("model", "qwen2.5:7b"))

    if provider_name == "claude_code":
        cc_cfg = cfg.get("claude_code") or {}
        return ClaudeCodeProvider(
            model=spec.get("model", "sonnet"),
            command=cc_cfg.get("command") or "claude",
            config_dir=cc_cfg.get("config_dir"),
            timeout_seconds=cc_cfg.get("timeout_seconds") or _DEFAULT_CLAUDE_TIMEOUT_SECONDS,
        )

    raise NotImplementedError(f"Provider {provider_name!r} is not implemented yet")
