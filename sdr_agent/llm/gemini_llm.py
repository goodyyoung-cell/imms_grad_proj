"""Gemini 어댑터 (google-genai SDK)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from google import genai
from google.genai import types

from .base import LLMResponse, Message, ToolCall, ToolSpec


def _as_dict(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            return {"result": content}
    return {"result": content}


class GeminiLLM:
    provider = "gemini"

    def __init__(self, api_key: str, model: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    @staticmethod
    def to_provider_contents(messages: list[Message]) -> list[types.Content]:
        out: list[types.Content] = []
        for m in messages:
            if m.role == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=m.text)]))
            elif m.role == "assistant":
                # 같은 모델이 만든 원본이 있으면 그대로 돌려준다 (함수 호출 서명 보존)
                if m.provider == "gemini" and isinstance(m.raw, types.Content):
                    out.append(m.raw)
                    continue
                parts: list[types.Part] = []
                if m.text:
                    parts.append(types.Part(text=m.text))
                for c in m.tool_calls:
                    parts.append(types.Part(function_call=types.FunctionCall(name=c.name, args=c.args)))
                out.append(types.Content(role="model", parts=parts))
            elif m.role == "tool":
                out.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=r.name, response=_as_dict(r.content)) for r in m.tool_results],
                ))
        return out

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 2048) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.input_schema)
                for t in tools
            ])] if tools else None,
            # 도구 실행은 우리 루프가 직접 하고 로그를 남긴다
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self.client.models.generate_content(
            model=self.model, contents=self.to_provider_contents(messages), config=config
        )
        content = resp.candidates[0].content if resp.candidates else None
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for part in (content.parts if content and content.parts else []):
            if part.function_call:
                fc = part.function_call
                calls.append(ToolCall(id=fc.id or f"call_{uuid.uuid4().hex[:8]}", name=fc.name, args=dict(fc.args or {})))
            elif part.text and not getattr(part, "thought", False):
                text_parts.append(part.text)
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, raw=content)
