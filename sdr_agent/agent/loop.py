"""에이전트 루프: 목표를 받은 LLM이 도구를 고르고, 결과를 보고 다시 판단한다.

    LLM 호출 → 도구 호출이 있으면 실행하고 결과를 돌려줌 → 다시 LLM 호출 → …
    끝나는 조건: 도구 호출 없이 답함 / terminal 도구 호출 / 최대 단계 도달 / 오류

도구 실행은 SDK의 자동 호출에 맡기지 않고 여기서 직접 한다. 그래야 모든 단계가
실행 로그에 남고, 최대 단계와 오류 처리를 우리가 통제할 수 있다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from ..llm.base import LLM, Message, ToolResult
from .runlog import RunLog, Step
from .tools import ToolRegistry


@dataclass
class AgentResult:
    status: str  # succeeded | stopped | failed
    stop_reason: str  # finished | terminal_tool | max_steps | error
    final_text: str
    terminal_output: Any = None
    steps: int = 0


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def run_agent(
    llm: LLM,
    system: str,
    goal: str,
    tools: ToolRegistry,
    log: RunLog,
    *,
    workflow: str,
    trigger: str = "manual",
    lead_id: str | None = None,
    max_steps: int = 8,
) -> AgentResult:
    log.start(workflow=workflow, trigger=trigger, model=f"{llm.provider}:{llm.model}", lead_id=lead_id)
    messages: list[Message] = [Message(role="user", text=goal)]
    step_no = 0
    last_text = ""

    try:
        for _ in range(max_steps):
            t0 = time.monotonic()
            resp = llm.chat(system, messages, tools.specs())
            step_no += 1
            last_text = resp.text or last_text
            log.step(Step(
                step_no=step_no,
                kind="llm",
                text=resp.text,
                output={"tool_calls": [{"name": c.name, "args": c.args} for c in resp.tool_calls]},
                duration_ms=int((time.monotonic() - t0) * 1000),
            ))
            messages.append(Message(
                role="assistant", text=resp.text, tool_calls=resp.tool_calls, raw=resp.raw, provider=llm.provider
            ))

            if not resp.tool_calls:
                log.finish("succeeded", "finished", resp.text)
                return AgentResult("succeeded", "finished", resp.text, steps=step_no)

            results: list[ToolResult] = []
            terminal_output: Any = None
            terminal_hit = False
            for call in resp.tool_calls:
                tool = tools.get(call.name)
                t1 = time.monotonic()
                if tool is None:
                    output, is_error = {"error": f"없는 도구입니다: {call.name}. 사용 가능: {tools.names()}"}, True
                else:
                    try:
                        output, is_error = tool.fn(**call.args), False
                    except Exception as exc:  # 도구 오류는 LLM에게 알려 다른 방법을 찾게 한다
                        output, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
                output = _jsonable(output)
                step_no += 1
                log.step(Step(
                    step_no=step_no,
                    kind="tool",
                    tool_name=call.name,
                    input=_jsonable(call.args),
                    output=output,
                    is_error=is_error,
                    duration_ms=int((time.monotonic() - t1) * 1000),
                ))
                results.append(ToolResult(call_id=call.id, name=call.name, content=output, is_error=is_error))
                if tool is not None and tool.terminal and not is_error:
                    terminal_hit, terminal_output = True, output

            messages.append(Message(role="tool", tool_results=results))
            if terminal_hit:
                log.finish("succeeded", "terminal_tool", last_text)
                return AgentResult("succeeded", "terminal_tool", last_text, terminal_output, step_no)

        log.finish("stopped", "max_steps", last_text)
        return AgentResult("stopped", "max_steps", last_text, steps=step_no)
    except Exception as exc:
        log.finish("failed", "error", last_text, error=f"{type(exc).__name__}: {exc}")
        raise
