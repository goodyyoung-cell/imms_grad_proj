"""Claude 어댑터."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from .base import LLMResponse, Message, ToolCall, ToolSpec


def _content_str(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


class ClaudeLLM:
    provider = "claude"

    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    @staticmethod
    def to_provider_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.text:
                    blocks.append({"type": "text", "text": m.text})
                for c in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.args})
                out.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                out.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.call_id,
                            "content": _content_str(r.content),
                            "is_error": r.is_error,
                        }
                        for r in m.tool_results
                    ],
                })
        return out

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 2048) -> LLMResponse:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=self.to_provider_messages(messages),
            tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, raw=resp)
