"""피드백에서 배우기: 담당자의 수정·반려·대화형 피드백에서 '작성 지침'을 뽑는다.

모델을 다시 훈련하지 않는다 (파인튜닝은 이번 학기 범위 밖). 지침 문장을 저장해 두고
다음 초안을 쓸 때 에이전트에게 함께 준다 (workflows/draft.py의 lessons).

지침이 되는 것: 다음 메일에도 통하는 일반적인 작성 방식 (길이, 어조, 무엇을 먼저 말할지, 무엇을 빼야 할지)
지침이 되지 않는 것: 특정 회사·사람에 대한 사실, 이번 메일에만 맞는 수정, 근거 없는 약속(할인·일정)
안전장치(근거 검사, 수신 거부, 승인)는 코드가 하므로 지침이 덮어쓸 수 없다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..llm.base import LLM
from ..toolkit.knowledge import GroundingError

MAX_LESSONS_PER_FEEDBACK = 3
LESSON_MIN, LESSON_MAX = 5, 120
# 지침으로 남기면 안 되는 약속 표현 (근거 규칙을 우회하려는 지침을 막는다)
_PROMISE = re.compile(r"할인(을|해)\s*(약속|제공|드리)|무료로\s*(드리|제공)|반드시\s*(계약|도입)|가격을\s*(낮춰|깎)")

SYSTEM = """당신은 노바웍스 SDR 팀의 메일 작성 코치입니다.
담당자가 AI가 쓴 메일 초안을 고치거나 반려하거나 피드백을 남겼습니다. 다음 초안부터 같은 지적을 받지 않도록,
여기서 배울 수 있는 '작성 지침'을 0~3개 뽑습니다.

좋은 지침:
- 다른 고객에게 보내는 다음 메일에도 그대로 통하는 일반적인 작성 방식입니다 (길이, 어조, 순서, 뺄 것, 강조할 것).
- 한 문장, 명령형, 구체적으로. 예: "첫 메일에서는 가격을 언급하지 않는다", "본문은 5문장 이내로 쓴다"
- 담당자가 실제로 바꾼 것·말한 것에서만 뽑습니다. 추측하지 않습니다.

뽑으면 안 되는 것:
- 특정 회사명·사람·이번 고객에게만 맞는 사실 (예: "삼성전자에는 반도체 이야기를 쓴다")
- 오탈자 수정처럼 일반화할 수 없는 사소한 수정
- 근거 없는 약속을 하라는 내용 (할인, 일정, 기능 추가)
- 이미 있는 지침과 같은 내용 (목표에 기존 지침 목록이 있습니다)

배울 것이 없으면 빈 목록으로 제출합니다. 마지막에 submit_lessons를 호출합니다."""


def _norm(text: str) -> str:
    return re.sub(r"[\s.,!?·]+", "", text).lower()


def _strip_footer(body: str | None) -> str:
    """비교할 때 코드가 붙인 고지·수신 거부 안내는 뺀다 (사람이 바꾼 게 아니므로)."""
    if not body:
        return ""
    try:
        from ..compliance import FOOTER_START
        cut = body.find(FOOTER_START)
    except ImportError:  # pragma: no cover
        cut = -1
    if cut < 0:
        cut = body.find("\n---\n")
    return (body[:cut] if cut >= 0 else body).strip()


def build_goal(source: str, *, before: str | None, after: str | None, feedback: str | None,
               reason: str | None, existing: list[str]) -> str:
    parts = {"edit": "담당자가 초안을 직접 고쳐서 승인했습니다.",
             "reject": "담당자가 초안을 반려했습니다.",
             "chat": "담당자가 초안에 대해 이렇게 고쳐 달라고 말했습니다."}
    lines = [parts[source]]
    if reason:
        lines.append(f"반려 사유: {reason}")
    if feedback:
        lines.append(f"담당자 말: {feedback}")
    if before:
        lines.append(f"AI 초안:\n\"\"\"\n{before[:3000]}\n\"\"\"")
    if after:
        lines.append(f"담당자가 고친 최종본:\n\"\"\"\n{after[:3000]}\n\"\"\"")
    lines.append("기존 지침: " + (json.dumps(existing, ensure_ascii=False) if existing else "없음"))
    return "\n\n".join(lines)


def run_extract_lessons(
    source: str,
    llm: LLM,
    log: RunLog,
    *,
    before: str | None = None,
    after: str | None = None,
    feedback: str | None = None,
    reason: str | None = None,
    existing: list[str] | None = None,
    banned_terms: list[str] | None = None,
    lead_id: str | None = None,
    trigger: str = "feedback",
    max_steps: int = 3,
) -> dict[str, Any]:
    """source: edit | reject | chat. 돌려주는 lessons는 저장 전 후보다 (검증·중복 제거 완료)."""
    if source not in ("edit", "reject", "chat"):
        raise ValueError("source는 edit, reject, chat 중 하나입니다")
    existing = existing or []
    seen = {_norm(t) for t in existing}
    banned = [t for t in (banned_terms or []) if t and len(t) >= 2]

    def submit_lessons(lessons: list[dict]) -> dict:
        if len(lessons) > MAX_LESSONS_PER_FEEDBACK:
            raise GroundingError(f"지침은 {MAX_LESSONS_PER_FEEDBACK}개 이하로 뽑으세요.")
        kept, dropped = [], []
        for item in lessons:
            text = " ".join(str(item.get("text", "")).split())
            why = str(item.get("why", "")).strip()
            reason_drop = None
            if not (LESSON_MIN <= len(text) <= LESSON_MAX):
                reason_drop = f"길이는 {LESSON_MIN}~{LESSON_MAX}자"
            elif any(t in text for t in banned):
                reason_drop = "특정 회사·사람 이름이 들어감"
            elif _PROMISE.search(text):
                reason_drop = "근거 없는 약속을 지시함"
            elif _norm(text) in seen:
                reason_drop = "기존 지침과 같음"
            if reason_drop:
                dropped.append({"text": text, "reason": reason_drop})
                continue
            seen.add(_norm(text))
            kept.append({"text": text, "why": why})
        return {"lessons": kept, "dropped": dropped}

    registry = ToolRegistry([Tool(
        "submit_lessons", "작성 지침을 0~3개 제출한다. 배울 것이 없으면 빈 목록.",
        {"type": "object", "properties": {"lessons": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string", "description": "지침 한 문장 (120자 이내, 명령형)"},
            "why": {"type": "string", "description": "담당자의 어떤 수정·말에서 나왔는지"},
        }, "required": ["text"]}}}, "required": ["lessons"]},
        submit_lessons, terminal=True)])
    goal = build_goal(source, before=_strip_footer(before), after=_strip_footer(after), feedback=feedback,
                      reason=reason, existing=existing)
    result = run_agent(llm, SYSTEM, goal, registry, log, workflow="learn_feedback", trigger=trigger,
                       lead_id=lead_id, max_steps=max_steps, max_tokens=2000)
    out = result.terminal_output or {"lessons": [], "dropped": []}
    return {"run_id": log.run_id, "status": result.status, **out}
