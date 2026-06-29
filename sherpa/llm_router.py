"""
LLM routing layer.

Tool-loop pattern (implemented in Phase 2 agent_loop.py):
  1. Build tools=[{"type": "function", "function": {...}}, ...] in Ollama format.
  2. Call provider.chat(messages, tools=tools).
  3. If response.message.tool_calls is non-empty:
       dispatch each call to a registry {name: callable}, append tool result message, loop.
  4. Otherwise: return response.message.content.
"""
from __future__ import annotations

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


def get_provider(tier: str = "default") -> LLMProvider:
    cfg = get_config()
    routing: dict = cfg.get("model_routing", {})
    tier_cfg = routing.get(tier)
    if tier_cfg is None:
        raise KeyError(f"Unknown LLM tier: {tier!r}")

    provider_name = tier_cfg.get("provider", "ollama")
    model = tier_cfg.get("model", "qwen2.5:7b")
    host = cfg.get("ollama_host", "http://localhost:11434")

    if provider_name == "ollama":
        return OllamaProvider(host=host, model=model)

    raise NotImplementedError(f"Provider {provider_name!r} is not implemented yet")
