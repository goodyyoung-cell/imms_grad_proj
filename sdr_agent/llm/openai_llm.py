"""OpenAI 어댑터 (Chat Completions API, HTTP로 직접 호출).

OpenAI와 같은 형식을 쓰는 서비스에도 base_url만 바꿔 쓸 수 있다 (예: Azure OpenAI의 /openai/v1 주소).
모델 이름은 설정(OPENAI_MODEL)에서 받는다. 추론 모델은 출력 한도(max_completion_tokens)에 추론 토큰이 포함되므로,
OPENAI_REASONING_EFFORT(low, medium, high)를 주면 추론량을 줄여 응답이 잘리거나 느려지는 것을 막을 수 있다.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import LLMResponse, Message, ToolCall, ToolSpec

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRY_STATUS = {429, 500, 502, 503, 504}


def _content_str(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


class OpenAILLM:
    provider = "openai"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, reasoning_effort: str | None = None,
                 http: Any = None, timeout: float = 120.0):
        self.api_key, self.model = api_key, model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.http = http or httpx.Client(timeout=timeout)

    @staticmethod
    def to_provider_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function",
                                          "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                                         for c in m.tool_calls]
                out.append(msg)
            elif m.role == "tool":
                for r in m.tool_results:
                    out.append({"role": "tool", "tool_call_id": r.call_id, "content": _content_str(r.content)})
        return out

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in (1, 2, 3):
            resp = self.http.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            if resp.status_code in RETRY_STATUS and attempt < 3:
                time.sleep(min(2 ** attempt, 8))
                continue
            break
        if resp.status_code >= 400:
            try:
                detail = (resp.json().get("error") or {}).get("message") or resp.text[:200]
            except ValueError:
                detail = resp.text[:200]
            raise RuntimeError(f"OpenAI 호출 실패 (HTTP {resp.status_code}): {detail}")
        return resp.json()

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 2048) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.to_provider_messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.input_schema}} for t in tools]
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        data = self._post(payload)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=fn.get("name", ""),
                                  args=args if isinstance(args, dict) else {}))
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls, raw=data,
                           truncated=choice.get("finish_reason") == "length")
