"""모델별 메시지 변환 검증 (네트워크 호출 없음)."""

from sdr_agent.llm.anthropic_llm import ClaudeLLM
from sdr_agent.llm.base import Message, ToolCall, ToolResult
from sdr_agent.llm.gemini_llm import GeminiLLM

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
