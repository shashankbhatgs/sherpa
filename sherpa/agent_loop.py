from __future__ import annotations

import json
from typing import Callable

from sherpa.llm_router import LLMProvider


def run_tool_loop(
    provider: LLMProvider,
    messages: list[dict],
    tools: list[dict],
    registry: dict[str, Callable],
    max_iterations: int = 10,
) -> str:
    messages = list(messages)
    for _ in range(max_iterations):
        response = provider.chat(messages, tools=tools if tools else None)
        msg = response["message"]
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return msg.get("content") or ""

        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

        for call in tool_calls:
            fn = call.function if hasattr(call, "function") else call.get("function", {})
            name = fn.name if hasattr(fn, "name") else fn.get("name", "")
            args_raw = fn.arguments if hasattr(fn, "arguments") else fn.get("arguments", {})
            args = args_raw if isinstance(args_raw, dict) else json.loads(args_raw or "{}")

            if name in registry:
                result = registry[name](**args)
            else:
                result = f"Unknown tool: {name}"

            messages.append({"role": "tool", "name": name, "content": str(result)})

    return messages[-1].get("content", "") if messages else ""
