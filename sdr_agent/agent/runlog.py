"""실행 로그. 어떤 도구를 왜 골랐는지 남겨 평가와 보고서에 쓴다.

MemoryRunLog는 테스트와 로컬 실험용, SupabaseRunLog는 실제 실행용이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass
class Step:
    step_no: int
    kind: str  # "llm" | "tool"
    text: str = ""
    tool_name: str | None = None
    input: Any = None
    output: Any = None
    is_error: bool = False
    duration_ms: int | None = None


class RunLog(Protocol):
    run_id: str | None

    def start(self, workflow: str, trigger: str, model: str, lead_id: str | None = None) -> str: ...

    def step(self, step: Step) -> None: ...

    def finish(self, status: str, stop_reason: str, final_text: str = "", error: str | None = None) -> None: ...


@dataclass
class MemoryRunLog:
    run_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    def start(self, workflow: str, trigger: str, model: str, lead_id: str | None = None) -> str:
        self.run_id = "memory-run"
        self.meta = {"workflow": workflow, "trigger": trigger, "model": model, "lead_id": lead_id}
        return self.run_id

    def step(self, step: Step) -> None:
        self.steps.append(step)

    def finish(self, status: str, stop_reason: str, final_text: str = "", error: str | None = None) -> None:
        self.result = {"status": status, "stop_reason": stop_reason, "final_text": final_text, "error": error}


class SupabaseRunLog:
    def __init__(self, client):
        self.client = client
        self.run_id: str | None = None

    def start(self, workflow: str, trigger: str, model: str, lead_id: str | None = None) -> str:
        row = self.client.table("agent_runs").insert(
            {"workflow": workflow, "trigger": trigger, "model": model, "lead_id": lead_id}
        ).execute().data[0]
        self.run_id = row["id"]
        return self.run_id

    def step(self, step: Step) -> None:
        self.client.table("agent_steps").insert({
            "run_id": self.run_id,
            "step_no": step.step_no,
            "kind": step.kind,
            "tool_name": step.tool_name,
            "input": step.input,
            "output": step.output,
            "text": step.text or None,
            "is_error": step.is_error,
            "duration_ms": step.duration_ms,
        }).execute()

    def finish(self, status: str, stop_reason: str, final_text: str = "", error: str | None = None) -> None:
        self.client.table("agent_runs").update({
            "status": status,
            "stop_reason": stop_reason,
            "final_text": final_text or None,
            "error": error,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", self.run_id).execute()
