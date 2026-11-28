"""모델별 메시지 변환 검증 (네트워크 호출 없음)."""

import json

import pytest

from sdr_agent.llm import get_llm
from sdr_agent.llm.anthropic_llm import ClaudeLLM
from sdr_agent.llm.base import Message, ToolCall, ToolResult, ToolSpec
from sdr_agent.llm.gemini_llm import GeminiLLM
from sdr_agent.llm.openai_llm import OpenAILLM

HISTORY = [
    Message(role="user", text="조사해 줘"),
    Message(role="assistant", text="홈페이지를 봅니다", tool_calls=[ToolCall("t1", "fetch_webpage", {"url": "https://a.kr"})]),
    Message(role="tool", tool_results=[ToolResult("t1", "fetch_webpage", {"title": "A"})]),
]


def test_claude_conversion():
    out = ClaudeLLM.to_provider_messages(HISTORY)
    assert out[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "fetch_webpage", "input": {"url": "https://a.kr"}}
    tr = out[2]["content"][0]
    assert out[2]["role"] == "user" and tr["type"] == "tool_result" and tr["tool_use_id"] == "t1"
    assert '"title": "A"' in tr["content"]


def test_gemini_conversion():
    out = GeminiLLM.to_provider_contents(HISTORY)
    assert [c.role for c in out] == ["user", "model", "user"]
    assert out[1].parts[1].function_call.name == "fetch_webpage"
    assert out[2].parts[0].function_response.response == {"title": "A"}


def test_openai_conversion():
    out = OpenAILLM.to_provider_messages("시스템 지침", HISTORY)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    call = out[2]["tool_calls"][0]
    assert call["id"] == "t1" and call["function"]["name"] == "fetch_webpage"
    assert json.loads(call["function"]["arguments"]) == {"url": "https://a.kr"}
    assert out[3]["tool_call_id"] == "t1" and '"title": "A"' in out[3]["content"]


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def test_openai_chat_parses_tool_calls_and_truncation(monkeypatch):
    monkeypatch.setattr("sdr_agent.llm.openai_llm.time.sleep", lambda s: None)
    ok = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "찾아봅니다", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_kb", "arguments": "{\"query\": \"체험\"}"}},
        {"id": "c2", "type": "function", "function": {"name": "x", "arguments": "not json"}}]}}]}
    http = FakeHttp(FakeResp(429, {"error": {"message": "rate"}}), FakeResp(200, ok))  # 한도에 걸리면 다시 시도
    llm = OpenAILLM("sk-test", "model-x", base_url="https://example.test/v1/", reasoning_effort="low", http=http)
    r = llm.chat("sys", HISTORY[:1], [ToolSpec("search_kb", "d", {"type": "object", "properties": {}})], max_tokens=500)
    assert r.text == "찾아봅니다" and not r.truncated
    assert [(c.id, c.name, c.args) for c in r.tool_calls] == [("c1", "search_kb", {"query": "체험"}), ("c2", "x", {})]
    sent = http.calls[-1]
    assert sent["url"] == "https://example.test/v1/chat/completions" and sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["json"]["max_completion_tokens"] == 500 and sent["json"]["reasoning_effort"] == "low"
    assert sent["json"]["tools"][0]["function"]["name"] == "search_kb"
    cut = {"choices": [{"finish_reason": "length", "message": {"content": "잘린"}}]}
    assert OpenAILLM("k", "m", http=FakeHttp(FakeResp(200, cut))).chat("s", HISTORY[:1], []).truncated is True


def test_openai_error_is_readable_and_get_llm_requires_key_and_model(monkeypatch):
    llm = OpenAILLM("k", "m", http=FakeHttp(FakeResp(401, {"error": {"message": "Incorrect API key"}})))
    with pytest.raises(RuntimeError, match="401.*Incorrect API key"):
        llm.chat("s", HISTORY[:1], [])
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_llm("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_MODEL", "m1")
    got = get_llm("openai")
    assert got.provider == "openai" and got.model == "m1" and got.base_url == "https://api.openai.com/v1"
