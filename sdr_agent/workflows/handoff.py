"""모듈 4: 인간 SDR 인계 요약 (모듈 3은 '스크리닝에서 확인할 질문'으로 축소해 여기에 포함).

사실 부분(리서치, 접촉 이력, 선별 근거)은 코드가 DB 기록에서 그대로 옮긴다. LLM이 바꿀 수 없다.
LLM은 해석 부분(관심사, 권장 후속 조치, 고객 맞춤 스크리닝 질문)만 쓴다.
LLM이 실패해도 기본 질문과 사실 부분만으로 인계 자료를 만든다.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..llm.base import LLM

# 스크리닝 콜에서 확인할 네 가지 (계획서 모듈 3)
SCREENING_TOPICS = {
    "purpose": "도입 목적",
    "environment": "사용 환경",
    "decision": "의사결정 구조",
    "timing": "도입 시점",
}
DEFAULT_QUESTIONS = {
    "purpose": "지금 고객 문의 응대에서 가장 해결하고 싶은 문제는 무엇인가요?",
    "environment": "상담 인원과 월 문의량, 주로 쓰는 채널(메일·채팅·카카오톡)은 어떻게 되나요?",
    "decision": "도입 검토에는 어느 분들이 참여하고, 최종 결정은 누가 하시나요?",
    "timing": "언제까지 도입하거나 결정하실 계획인가요?",
}

SYSTEM = """당신은 노바웍스 SDR 팀의 인계 담당 에이전트입니다.
AI가 처리한 리드를 인간 SDR에게 넘기며, 담당자가 바로 상담을 준비할 수 있도록 해석을 덧붙입니다.

규칙:
- 주어진 기록(리서치 사실, 메일, 답장)에 있는 내용만 근거로 씁니다. 추측은 "추정"이라고 표시합니다.
- interests: 고객이 관심을 보인 주제 (답장·문의 메모 근거). 없으면 빈 목록.
- recommended_actions: 담당자가 다음에 할 일 2~4개, 구체적으로.
- screening_questions: purpose(도입 목적), environment(사용 환경), decision(의사결정 구조), timing(도입 시점) 네 가지 각각에 대해
  이 고객 상황에 맞춘 질문 하나씩. 이미 답장에서 확인된 항목은 그 내용을 확인하는 질문으로 씁니다.
- 마지막에 submit_handoff를 호출합니다."""


def contact_history(messages: list[dict], replies: list[dict]) -> list[dict]:
    events = [{"at": m.get("sent_at") or m.get("created_at"), "type": f"메일({m.get('kind')})",
               "status": m.get("status"), "subject": m.get("subject")} for m in messages]
    events += [{"at": r.get("received_at"), "type": "답장", "classification": r.get("classification"),
                "excerpt": (r.get("body") or "")[:200]} for r in replies]
    return sorted(events, key=lambda e: str(e.get("at") or ""))


def base_summary(lead: dict, facts: list[dict], score: dict | None,
                 messages: list[dict], replies: list[dict], reason: str) -> dict[str, Any]:
    return {
        "lead": {k: lead.get(k) for k in ("id", "name", "company_name", "title", "email", "source", "inbound_at")},
        "handoff_reason": reason,
        "research": [{"field": f["field"], "value": f["value"], "source_url": f["source_url"]} for f in facts],
        "selection": {"score": (score or {}).get("score"), "tags": (score or {}).get("tags", []),
                      "breakdown": (score or {}).get("breakdown", {}),
                      "note": "점수는 규칙 기반이며, 영업기회 등록 여부는 담당자가 결정합니다."},
        "contact_history": contact_history(messages, replies),
        "interests": [],
        "recommended_actions": ["답장 내용을 확인하고 스크리닝 미팅 일정을 잡는다."],
    }


def _questions(given: dict | None) -> list[dict]:
    given = given or {}
    return [{"topic": key, "label": label, "question": (given.get(key) or DEFAULT_QUESTIONS[key]).strip()}
            for key, label in SCREENING_TOPICS.items()]


def run_handoff(
    lead: dict[str, Any],
    facts: list[dict],
    score: dict | None,
    messages: list[dict],
    replies: list[dict],
    llm: LLM | None,
    log: RunLog | None,
    *,
    reason: str = "고객 관심 표명",
    trigger: str = "manual",
    max_steps: int = 3,
) -> dict[str, Any]:
    summary = base_summary(lead, facts, score, messages, replies, reason)
    if llm is None or log is None:
        return {"run_id": None, "summary": summary, "screening_questions": _questions(None), "by": "rule"}

    def submit_handoff(interests: list[str], recommended_actions: list[str], screening_questions: dict) -> dict:
        return {"interests": interests, "recommended_actions": recommended_actions,
                "screening_questions": screening_questions}

    registry = ToolRegistry([Tool(
        "submit_handoff", "인계 요약의 해석 부분을 제출한다.",
        {"type": "object", "properties": {
            "interests": {"type": "array", "items": {"type": "string"}},
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
            "screening_questions": {"type": "object", "properties": {
                k: {"type": "string", "description": v} for k, v in SCREENING_TOPICS.items()},
                "required": list(SCREENING_TOPICS)},
        }, "required": ["interests", "recommended_actions", "screening_questions"]},
        submit_handoff, terminal=True)])
    record = {k: summary[k] for k in ("lead", "handoff_reason", "research", "selection", "contact_history")}
    record["lead_notes"] = lead.get("notes")
    goal = f"다음 리드를 인간 SDR에게 인계합니다. 기록:\n{json.dumps(record, ensure_ascii=False, default=str)}"
    result = run_agent(llm, SYSTEM, goal, registry, log, workflow="handoff", trigger=trigger,
                       lead_id=lead.get("id"), max_steps=max_steps)
    out = result.terminal_output or {}
    if out.get("interests"):
        summary["interests"] = out["interests"]
    if out.get("recommended_actions"):
        summary["recommended_actions"] = out["recommended_actions"]
    return {"run_id": log.run_id, "summary": summary,
            "screening_questions": _questions(out.get("screening_questions")), "by": "agent" if out else "rule"}


def render_text(summary: dict, questions: list[dict]) -> str:
    """메일·메신저로 보낼 한 장짜리 텍스트."""
    lead, sel = summary["lead"], summary["selection"]
    lines = [f"[인계] {lead.get('company_name')} · {lead.get('name') or '이름 미상'} ({lead.get('title') or '직급 미상'})",
             f"인계 사유: {summary['handoff_reason']}",
             f"선별 점수: {sel.get('score')}점 · {', '.join(sel.get('tags', []))}",
             "", "■ 리서치"]
    lines += [f"- {f['field']}: {f['value']} ({f['source_url']})" for f in summary["research"]] or ["- (출처 있는 사실 없음)"]
    lines += ["", "■ 접촉 이력"]
    lines += [f"- {e.get('at')} {e['type']} {e.get('subject') or e.get('classification') or ''}".rstrip()
              for e in summary["contact_history"]] or ["- (없음)"]
    lines += ["", "■ 관심사"] + ([f"- {i}" for i in summary["interests"]] or ["- (확인된 관심사 없음)"])
    lines += ["", "■ 권장 후속 조치"] + [f"- {a}" for a in summary["recommended_actions"]]
    lines += ["", "■ 스크리닝에서 확인할 질문"] + [f"- {q['label']}: {q['question']}" for q in questions]
    lines += ["", sel["note"]]
    return "\n".join(lines)
