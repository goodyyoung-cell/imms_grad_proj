"""모듈 2-2: 답장 분류와 후속 응대.

순서
1. 규칙으로 수신 거부를 먼저 본다. 걸리면 LLM을 부르지 않고 즉시 중단한다 (가장 확실해야 하는 판단).
2. 아니면 에이전트가 답장을 분류한다. 질문이면 승인된 자료로 답변 초안을 쓴다.
3. 코드가 행동을 정한다. 애매하거나(unclear, 확신도 낮음) 근거가 없으면 사람에게 넘긴다.

에이전트는 분류와 초안만 만든다. 답변 초안도 사람 승인 뒤에 발송된다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..llm.base import LLM
from ..toolkit.knowledge import GroundingError, KnowledgeContext

CLASSES = ["interested", "question", "not_now", "not_decision_maker", "out_of_office", "unsubscribe", "unclear"]
CONFIDENCE_THRESHOLD = 0.7

# 수신 거부 의사 표현. 넓게 잡는다 (놓치는 것보다 잘못 멈추는 편이 낫다)
UNSUBSCRIBE_PATTERNS = [
    r"수신\s*거부", r"수신\s*거절", r"수신\s*중단", r"(메일|연락|발송).{0,6}(그만|중단|멈춰|사절|하지\s*마)",
    r"보내지\s*(마|말아)", r"연락\s*(하지|주지)\s*마", r"구독\s*취소", r"빼\s*주세요",
    r"\bunsubscribe\b", r"\bstop\b", r"\bremove me\b", r"\bopt[\s-]?out\b",
]
_UNSUB = re.compile("|".join(UNSUBSCRIBE_PATTERNS), re.IGNORECASE)

# 분류 → 리드에게 할 행동
ACTIONS = {
    "unsubscribe": "unsubscribe",           # 모든 발송 중단
    "interested": "handoff",                # 인간 SDR에게 인계
    "question": "answer_draft",             # 근거 있는 답변 초안 → 사람 승인
    "not_now": "followup_later",            # 나중에 다시 연락 (사람이 시점 확인)
    "out_of_office": "followup_later",
    "not_decision_maker": "needs_human",    # 소개 요청은 사람이 판단
    "unclear": "needs_human",
}

SYSTEM = """당신은 노바웍스(제품: 노바데스크) SDR을 돕는 답장 분류 에이전트입니다.
리드가 보낸 답장을 읽고 분류합니다.

분류:
- interested: 미팅·데모·상담 의사가 있음
- question: 제품·가격·도입에 관한 질문 (미팅 의사가 함께 있으면 interested)
- not_now: 지금은 아니지만 나중에 가능
- not_decision_maker: 본인이 담당자가 아님, 다른 사람 언급
- out_of_office: 부재 자동응답
- unsubscribe: 연락을 원하지 않음
- unclear: 위 어디에도 확실히 맞지 않음

규칙:
- 확신이 없으면 unclear로 두거나 confidence를 낮게 줍니다. 애매한 답장은 사람이 봅니다.
- question이면 search_kb로 근거를 찾아 짧은 답변 초안(answer_body)을 씁니다. 근거가 없는 질문은 answer_body를 비우고 reason에 "근거 없음"을 적습니다.
- 답변 초안에는 찾은 자료 내용만 씁니다. 약속(할인, 일정, 기능 추가)을 하지 않습니다.
- 마지막에 submit_triage를 호출합니다."""


def detect_unsubscribe(text: str) -> bool:
    return bool(_UNSUB.search(text or ""))


def decide(classification: str, confidence: float, has_answer: bool) -> str:
    if classification == "unsubscribe":
        return "unsubscribe"  # 확신도와 관계없이 멈춘다
    if classification not in ACTIONS or confidence < CONFIDENCE_THRESHOLD:
        return "needs_human"
    action = ACTIONS[classification]
    if action == "answer_draft" and not has_answer:
        return "needs_human"  # 근거 있는 답이 없으면 사람에게
    return action


def run_reply_triage(
    lead: dict[str, Any],
    reply_body: str,
    llm: LLM | None,
    log: RunLog | None,
    *,
    last_message: dict | None = None,
    knowledge: KnowledgeContext | None = None,
    trigger: str = "reply",
    max_steps: int = 5,
) -> dict[str, Any]:
    if detect_unsubscribe(reply_body):
        return {"run_id": None, "classification": "unsubscribe", "confidence": 1.0,
                "reason": "수신 거부 표현 감지 (규칙)", "action": "unsubscribe", "answer": None, "by": "rule"}
    if llm is None or log is None:
        return {"run_id": None, "classification": "unclear", "confidence": 0.0,
                "reason": "LLM 없음", "action": "needs_human", "answer": None, "by": "rule"}

    kctx = knowledge or KnowledgeContext()

    def submit_triage(classification: str, confidence: float, reason: str,
                      answer_subject: str | None = None, answer_body: str | None = None,
                      kb_ids: list[str] | None = None) -> dict:
        if classification not in CLASSES:
            raise GroundingError(f"classification은 {CLASSES} 중 하나여야 합니다.")
        answer = None
        if answer_body and answer_body.strip():
            if not kb_ids:
                raise GroundingError("답변 초안에는 search_kb로 찾은 근거(kb_ids)가 필요합니다. 근거가 없으면 answer_body를 비우세요.")
            ids = kctx.check(answer_body, kb_ids)
            answer = {"subject": (answer_subject or "문의하신 내용 답변드립니다").strip(),
                      "body": answer_body.strip(), "citations": [{"type": "kb", "id": i} for i in ids]}
        return {"classification": classification, "confidence": max(0.0, min(1.0, float(confidence))),
                "reason": reason, "answer": answer}

    registry = ToolRegistry([
        kctx.tool(),
        Tool("submit_triage", "답장 분류 결과를 제출한다. question이면 근거 있는 답변 초안을 함께 낸다.",
             {"type": "object", "properties": {
                 "classification": {"type": "string", "enum": CLASSES},
                 "confidence": {"type": "number", "description": "0~1"},
                 "reason": {"type": "string", "description": "분류 근거 한두 문장"},
                 "answer_subject": {"type": "string"},
                 "answer_body": {"type": "string"},
                 "kb_ids": {"type": "array", "items": {"type": "string"}},
             }, "required": ["classification", "confidence", "reason"]},
             submit_triage, terminal=True),
    ])
    context = {"company_name": lead.get("company_name"), "title": lead.get("title"),
               "our_last_message": (last_message or {}).get("body", "")[:1500]}
    goal = (f"다음 답장을 분류하세요.\n맥락: {json.dumps(context, ensure_ascii=False)}\n"
            f"답장 원문:\n\"\"\"\n{reply_body[:4000]}\n\"\"\"")
    result = run_agent(llm, SYSTEM, goal, registry, log, workflow="reply_triage", trigger=trigger,
                       lead_id=lead.get("id"), max_steps=max_steps)
    out = result.terminal_output
    if not out:
        return {"run_id": log.run_id, "classification": "unclear", "confidence": 0.0,
                "reason": f"분류 실패 ({result.stop_reason})", "action": "needs_human", "answer": None, "by": "agent"}
    action = decide(out["classification"], out["confidence"], out["answer"] is not None)
    return {"run_id": log.run_id, **out, "action": action, "by": "agent"}
