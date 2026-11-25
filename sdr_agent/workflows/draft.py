"""모듈 2-1: 첫 메일 초안.

목표: "리서치 결과와 승인된 자료를 근거로 이 리드에게 보낼 첫 메일을 써라."
초안은 발송되지 않는다. messages 테이블에 pending_approval로 들어가고 사람이 승인해야 나간다.

근거 규칙
- 회사에 관한 문장은 이번 리서치에서 저장된 사실(fact id)만 근거로 쓴다.
- 제품·가격·정책 문장은 search_kb로 받은 자료 id만 근거로 쓴다. 가격을 말하면 가격 자료가 있어야 한다.
- AI 사용 고지, 미팅 예약 링크, 수신 거부 안내는 LLM이 아니라 코드가 붙인다 (빠뜨릴 수 없게).
"""

from __future__ import annotations

import json
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..llm.base import LLM
from ..toolkit.knowledge import GroundingError, KnowledgeContext

SYSTEM = """당신은 한국 B2B SaaS 회사 노바웍스(제품: 노바데스크)의 SDR을 돕는 메일 작성 에이전트입니다.
리드에게 보낼 첫 메일 초안을 씁니다. 초안은 사람이 검토한 뒤에만 발송됩니다.

규칙:
- 리드 회사에 관한 내용은 주어진 리서치 사실만 씁니다. 사실이 없으면 회사 이야기를 지어내지 말고 문의 경로(데모 요청 등)를 소재로 씁니다.
- 제품·가격·정책 내용은 search_kb로 찾은 자료만 씁니다. 찾지 못한 내용은 쓰지 않습니다.
- 가격·할인은 첫 메일에 꼭 필요할 때만 쓰고, 쓰면 가격 자료를 근거로 답니다.
- 6~10문장, 정중한 존댓말. 과장("업계 최고", "무조건")과 압박 표현을 쓰지 않습니다.
- 마지막 문장은 짧은 화상 미팅(20분)을 제안합니다. 예약 링크·AI 고지·수신 거부 안내는 시스템이 붙이니 쓰지 마세요.
- 다 쓰면 submit_draft를 호출합니다. 쓴 근거를 fact_ids와 kb_ids에 모두 넣습니다."""

FOOTER = (
    "\n\n---\n"
    "이 메일은 AI의 도움을 받아 작성되었고, 노바웍스 담당자가 검토한 뒤 발송했습니다.\n"
    "더 이상 연락을 원하지 않으시면 이 메일에 \"수신 거부\"라고 답장해 주세요. 즉시 발송을 중단합니다."
)


def compose_body(body: str, booking_url: str | None) -> str:
    cta = f"\n\n편하신 시간을 아래 링크에서 골라 주세요.\n{booking_url}" if booking_url else ""
    return body.rstrip() + cta + FOOTER


def _fact_card(facts: list[dict]) -> list[dict]:
    return [{"fact_id": f["id"], "field": f["field"], "value": f["value"], "source_url": f["source_url"]} for f in facts]


def build_goal(lead: dict[str, Any], facts: list[dict], score: dict | None) -> str:
    card = {k: lead.get(k) for k in ("name", "company_name", "title", "source", "notes")}
    return (
        "다음 리드에게 보낼 첫 메일 초안을 쓰세요.\n"
        f"리드: {json.dumps(card, ensure_ascii=False, default=str)}\n"
        f"리서치 사실: {json.dumps(_fact_card(facts), ensure_ascii=False)}\n"
        f"선별 근거 태그: {json.dumps((score or {}).get('tags', []), ensure_ascii=False)}"
    )


def run_draft(
    lead: dict[str, Any],
    facts: list[dict],
    score: dict | None,
    llm: LLM,
    log: RunLog,
    *,
    knowledge: KnowledgeContext | None = None,
    booking_url: str | None = None,
    trigger: str = "manual",
    max_steps: int = 6,
) -> dict[str, Any]:
    kctx = knowledge or KnowledgeContext()
    fact_ids = {f["id"] for f in facts}

    def submit_draft(subject: str, body: str, fact_ids_used: list[str] | None = None,
                     kb_ids: list[str] | None = None) -> dict:
        used = list(dict.fromkeys(fact_ids_used or []))
        unknown = [i for i in used if i not in fact_ids]
        if unknown:
            raise GroundingError(f"주어진 리서치 사실에 없는 fact_id입니다: {unknown}")
        if not subject.strip() or not body.strip():
            raise GroundingError("제목과 본문이 모두 필요합니다.")
        kb = kctx.check(f"{subject}\n{body}", kb_ids or [])
        return {"subject": subject.strip(), "body": body.strip(),
                "citations": [{"type": "fact", "id": i} for i in used] + [{"type": "kb", "id": i} for i in kb]}

    registry = ToolRegistry([
        kctx.tool(),
        Tool("submit_draft", "메일 초안을 제출한다. 근거 규칙을 어기면 오류가 돌아오니 고쳐서 다시 제출한다.",
             {"type": "object", "properties": {
                 "subject": {"type": "string"},
                 "body": {"type": "string", "description": "인사부터 미팅 제안까지. 서명·링크·고지는 빼고"},
                 "fact_ids_used": {"type": "array", "items": {"type": "string"}},
                 "kb_ids": {"type": "array", "items": {"type": "string"}},
             }, "required": ["subject", "body"]},
             submit_draft, terminal=True),
    ])
    result = run_agent(llm, SYSTEM, build_goal(lead, facts, score), registry, log,
                       workflow="draft", trigger=trigger, lead_id=lead.get("id"), max_steps=max_steps)
    draft = result.terminal_output
    if draft:
        draft = {**draft, "body": compose_body(draft["body"], booking_url)}
    return {"run_id": log.run_id, "status": result.status, "stop_reason": result.stop_reason, "draft": draft}
