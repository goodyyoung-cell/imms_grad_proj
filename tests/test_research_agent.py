"""가짜 LLM으로 에이전트 루프와 리서치 워크플로우를 검증한다 (API 키 불필요)."""

from datetime import datetime, timezone

from sdr_agent.agent import MemoryRunLog, Tool, ToolRegistry, run_agent
from sdr_agent.llm.base import LLMResponse, ToolCall
from sdr_agent.workflows.research import run_research

HOME = "https://example-saas.kr"


class ScriptedLLM:
    """정해진 순서로 응답하는 가짜 LLM. 받은 메시지를 기록한다."""

    provider, model = "fake", "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def chat(self, system, messages, tools, max_tokens=2048):
        self.seen.append(list(messages))
        return self.responses.pop(0)


def fake_fetch(url):
    return {"url": HOME, "title": "예시 SaaS", "text": "중소기업용 회계 SaaS", "links": [{"url": HOME + "/about", "text": "회사소개"}]}


LEAD = {"id": "lead-1", "company_name": "예시SaaS", "company_domain": "example-saas.kr", "title": "팀장",
        "source": "demo_request", "inbound_at": datetime.now(timezone.utc).isoformat()}


def test_research_keeps_only_facts_with_seen_sources():
    llm = ScriptedLLM([
        LLMResponse("DART에 먼저 찾아봅니다", [ToolCall("c1", "dart_search", {"company_name": "예시SaaS"})]),
        LLMResponse("DART를 못 쓰니 홈페이지로 갑니다", [ToolCall("c2", "fetch_webpage", {"url": HOME})]),
        LLMResponse("", [ToolCall("c3", "compute_score", {})]),
        LLMResponse("제출합니다", [ToolCall("c4", "submit_findings", {"facts": [
            {"field": "summary", "value": "중소기업용 회계 SaaS", "source_type": "homepage", "source_url": HOME},
            {"field": "revenue", "value": "매출 300억", "source_type": "news", "source_url": "https://made-up.example/news"},
        ], "missing": ["revenue"]})]),
    ])
    log = MemoryRunLog()
    out = run_research(LEAD, llm, log, dart=None, fetch=fake_fetch)

    assert out["stop_reason"] == "terminal_tool"
    assert [f["field"] for f in out["facts"]] == ["summary"]
    assert len(out["rejected"]) == 1  # 확인하지 않은 URL을 출처로 단 사실은 버린다
    assert "데모 요청" in out["score"]["tags"]
    tools_called = [s.tool_name for s in log.steps if s.kind == "tool"]
    assert tools_called == ["dart_search", "fetch_webpage", "compute_score", "submit_findings"]
    # DART 사용 불가 메시지가 LLM에게 전달됐다 (다른 출처로 넘어갈 근거)
    assert "DART" in str(llm.seen[1][-1].tool_results[0].content)


def test_score_computed_even_if_agent_skips_it():
    llm = ScriptedLLM([LLMResponse("", [ToolCall("c1", "submit_findings", {"facts": []})])])
    out = run_research(LEAD, llm, MemoryRunLog(), fetch=fake_fetch)
    assert out["score"]["score"] > 0


def test_loop_stops_at_max_steps_and_reports_tool_errors():
    def boom():
        raise ValueError("망가짐")

    reg = ToolRegistry([Tool("boom", "항상 실패", {"type": "object", "properties": {}}, boom)])
    llm = ScriptedLLM([LLMResponse("", [ToolCall(f"c{i}", "boom", {})]) for i in range(3)])
    log = MemoryRunLog()
    res = run_agent(llm, "sys", "goal", reg, log, workflow="test", max_steps=3)
    assert res.stop_reason == "max_steps"
    assert log.result["status"] == "stopped"
    assert all(s.is_error for s in log.steps if s.kind == "tool")


def test_unknown_tool_is_reported_not_raised():
    llm = ScriptedLLM([
        LLMResponse("", [ToolCall("c1", "nope", {})]),
        LLMResponse("끝", []),
    ])
    log = MemoryRunLog()
    res = run_agent(llm, "sys", "goal", ToolRegistry(), log, workflow="test")
    assert res.stop_reason == "finished" and res.final_text == "끝"
    assert log.steps[1].is_error


def test_truncated_response_is_not_executed_and_agent_is_told_why():
    calls = []
    reg = ToolRegistry([Tool("submit", "제출", {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
                             lambda x: calls.append(x) or {"ok": x}, terminal=True)])
    llm = ScriptedLLM([
        LLMResponse("", [ToolCall("c1", "submit", {})], truncated=True),   # 길이 제한으로 잘린 호출
        LLMResponse("", [ToolCall("c2", "submit", {"x": "짧게"})]),
    ])
    log = MemoryRunLog()
    res = run_agent(llm, "sys", "goal", reg, log, workflow="test")
    assert calls == ["짧게"] and res.stop_reason == "terminal_tool"
    assert "잘려" in log.steps[1].output["error"]
