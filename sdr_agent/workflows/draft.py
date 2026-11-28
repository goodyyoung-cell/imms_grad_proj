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
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..feedback import FEEDBACK_RULE, check_ids, goal_block
from ..industry import is_sensitive
from ..llm.base import LLM
from ..toolkit.knowledge import GroundingError, KnowledgeContext

KST = timezone(timedelta(hours=9))

SYSTEM = """당신은 한국 B2B SaaS 회사 노바웍스(제품: 노바데스크)의 SDR을 돕는 메일 작성 에이전트입니다.
리드에게 보낼 첫 메일 초안을 씁니다. 초안은 사람이 검토한 뒤에만 발송됩니다.

규칙:
- 리드 회사에 관한 내용은 주어진 리서치 사실만 씁니다. 사실이 없으면 회사 이야기를 지어내지 말고 문의 경로(데모 요청 등)를 소재로 씁니다.
- 제품·가격·정책 내용은 search_kb로 찾은 자료만 씁니다. 찾지 못한 내용은 쓰지 않습니다.
- 가격·할인은 첫 메일에 꼭 필요할 때만 쓰고, 쓰면 가격 자료를 근거로 답니다.
- 6~10문장, 정중한 존댓말. 과장("업계 최고", "무조건")과 압박 표현을 쓰지 않습니다.
- 마지막 문장은 짧은 화상 미팅(20분)을 제안합니다. 예약 링크·AI 고지·수신 거부 안내는 시스템이 붙이니 쓰지 마세요.
- 다 쓰면 submit_draft를 호출합니다. 쓴 근거를 fact_ids_used와 kb_ids에 모두 넣습니다.

소재(hook)와 오프닝:
- 소재는 이 순서로 고릅니다. ① 회사 소식: 리서치 사실 중 recent_news·talking_point로, 오늘 기준 약 30일 이내이고 고객 문의 응대·디지털 전환과 이어지는 것.
  ② 회사 소식이 마땅치 않으면 업종 화두: 주어진 업종 후보(ind1…) 중 가장 최근이고 고객 응대에 시사점이 있는 것.
  ③ 둘 다 없으면 문의 경로(type=inquiry).
- sensitive=true인 소식(유출·사고·소송·구조조정 등)은 소재로 쓰지 않습니다. 보도되지 않았거나 날짜를 알 수 없는 내용도 쓰지 않습니다.
- 본문 첫 1~2문장은 소재를 사실 그대로(날짜·수치 포함) 짧게 언급하고, 그 소식이 고객 문의 응대와 어떻게 이어지는지 한 문장으로 연결합니다. 소재가 문의 경로면 문의해 주신 데 대한 감사로 엽니다.
- hook에 type(company/industry/inquiry), summary(소재 한 줄), 쓴 근거(company면 fact_ids, industry면 industry_ids)를 담습니다.

제목은 세 가지를 씁니다. 모두 40자 이내, 과장·낚시·물음표 남발 없이 담백하게.
- subject (일반 제목, 기본값): 회사명과 소재 없이 가치 중심. 고객 문의 응대의 어떤 어려움을 어떻게 줄일 수 있는지를 담고, 리드마다 같은 문장을 반복하지 않습니다 (리드의 문의 경로나 업종 특성을 살려 다르게 씁니다).
- subject_company: 회사명을 넣은 제목.
- subject_hook: 소재를 반영한 제목 (회사명을 넣어도 됩니다). 소재가 문의 경로(inquiry)이면 쓰지 않습니다.
""" + FEEDBACK_RULE

REWRITE_RULE = """

지금은 이미 쓴 초안을 사용자의 지시에 맞게 고쳐 쓰는 일입니다. 지시와 무관한 부분은 최대한 그대로 둡니다.
위 규칙(근거, 소재, 제목 세 가지)은 그대로 지킵니다. 다 고치면 submit_draft로 제출합니다."""

FOOTER = (
    "\n\n---\n"
    "이 메일은 AI의 도움을 받아 작성되었고, 노바웍스 담당자가 검토한 뒤 발송했습니다.\n"
    "더 이상 연락을 원하지 않으시면 이 메일에 \"수신 거부\"라고 답장해 주세요. 즉시 발송을 중단합니다."
)


CTA_MARK = "\n\n편하신 시간을 아래 링크에서 골라 주세요."
_FOOTER_CUT = "\n\n---\n"
MAX_SUBJECT_LEN = 45
SUBJECT_LABELS = {"general": "일반 제목", "company": "회사명 포함", "hook": "소식 반영"}


def compose_body(body: str, booking_url: str | None) -> str:
    cta = f"{CTA_MARK}\n{booking_url}" if booking_url else ""
    return body.rstrip() + cta + FOOTER


def strip_generated_tail(body: str) -> str:
    """코드가 붙인 예약 링크 문단과 고지(FOOTER 이후)를 걷어 낸다. 다시 쓰기 전에 본문만 남기려는 것."""
    cut = body.find(_FOOTER_CUT)
    base = body[:cut] if cut >= 0 else body
    cta = base.find(CTA_MARK)
    return (base[:cta] if cta >= 0 else base).rstrip()


def _fact_card(facts: list[dict]) -> list[dict]:
    out = []
    for f in facts:
        card = {"fact_id": f["id"], "field": f["field"], "value": f["value"], "source_url": f["source_url"]}
        if f["field"] in ("recent_news", "talking_point"):
            card["sensitive"] = is_sensitive(f["value"])
            card["retrieved_at"] = str(f.get("retrieved_at") or "")[:10] or None
        out.append(card)
    return out


def _industry_card(items: list[dict]) -> list[dict]:
    return [{"id": i["id"], "title": i.get("title"), "summary": i.get("summary"), "published_at": i.get("published_at"),
             "publisher": i.get("publisher")} for i in items]


def build_goal(lead: dict[str, Any], facts: list[dict], score: dict | None, *, today: date | None = None,
               industry_label: str | None = None, industry_items: list[dict] | None = None,
               feedback: list[dict] | None = None) -> str:
    card = {k: lead.get(k) for k in ("name", "company_name", "title", "source", "notes")}
    today = today or datetime.now(timezone.utc).astimezone(KST).date()
    goal = (
        "다음 리드에게 보낼 첫 메일 초안을 쓰세요.\n"
        f"오늘 날짜: {today.isoformat()}\n"
        f"리드: {json.dumps(card, ensure_ascii=False, default=str)}\n"
        f"리서치 사실: {json.dumps(_fact_card(facts), ensure_ascii=False)}\n"
        f"선별 근거 태그: {json.dumps((score or {}).get('tags', []), ensure_ascii=False)}"
    )
    if industry_items:
        goal += (f"\n업종({industry_label}) 최근 화두 후보: "
                 f"{json.dumps(_industry_card(industry_items), ensure_ascii=False)}")
    elif industry_label:
        goal += f"\n업종: {industry_label} (최근 화두 후보 없음)"
    return goal + goal_block(feedback or [])


def _core_name(name: str | None) -> str:
    return re.sub(r"\(주\)|㈜|주식회사", "", name or "").strip()


def _draft_tool(lead: dict[str, Any], facts: list[dict], kctx: KnowledgeContext, industry_items: list[dict],
                feedback: list[dict]) -> Tool:
    """submit_draft 도구. 근거·소재·제목 규칙은 여기서 코드로 검사한다 (첫 초안과 다시 쓰기가 함께 쓴다)."""
    fact_by_id = {f["id"]: f for f in facts}
    ind_by_id = {i["id"]: i for i in industry_items}
    has_candidates = bool(industry_items) or any(
        f["field"] in ("recent_news", "talking_point") and not is_sensitive(f["value"]) for f in facts)

    def submit_draft(subject: str, body: str, fact_ids_used: list[str] | None = None,
                     kb_ids: list[str] | None = None, subject_company: str | None = None,
                     subject_hook: str | None = None, hook: dict | None = None,
                     feedback_ids: list[str] | None = None) -> dict:
        used = list(dict.fromkeys(fact_ids_used or []))
        unknown = [i for i in used if i not in fact_by_id]
        if unknown:
            raise GroundingError(f"주어진 리서치 사실에 없는 fact_id입니다: {unknown}")
        if not subject.strip() or not body.strip():
            raise GroundingError("제목과 본문이 모두 필요합니다.")
        subjects = {"general": subject.strip()}
        core = _core_name(lead.get("company_name"))
        if subject_company and subject_company.strip():
            if core and core not in subject_company:
                raise GroundingError(f"subject_company에는 회사명({core})이 들어가야 합니다.")
            subjects["company"] = subject_company.strip()
        for sid, s in subjects.items():
            if len(s) > MAX_SUBJECT_LEN:
                raise GroundingError(f"제목({SUBJECT_LABELS[sid]})이 {MAX_SUBJECT_LEN}자를 넘습니다. 줄여서 다시 제출하세요.")

        hook_out, hook_cites = None, []
        if hook is None:
            if has_candidates:
                raise GroundingError("hook을 제출하세요. 회사 소식이나 업종 화두 후보가 있으면 그중에서 고르고, "
                                     "쓸 만한 것이 없으면 type=inquiry로 제출합니다.")
        else:
            htype = hook.get("type")
            if htype not in ("company", "industry", "inquiry"):
                raise GroundingError("hook.type은 company, industry, inquiry 중 하나여야 합니다.")
            summary = (hook.get("summary") or "").strip()
            if not summary:
                raise GroundingError("hook.summary(소재 한 줄)가 필요합니다.")
            url, title = None, None
            if htype == "company":
                ids = list(dict.fromkeys(hook.get("fact_ids") or []))
                if not ids:
                    raise GroundingError("company 소재에는 hook.fact_ids가 필요합니다.")
                bad = [i for i in ids if i not in fact_by_id]
                if bad:
                    raise GroundingError(f"주어진 리서치 사실에 없는 fact_id입니다: {bad}")
                if any(is_sensitive(fact_by_id[i]["value"]) for i in ids):
                    raise GroundingError("유출·사고·소송 같은 민감한 소식은 소재로 쓸 수 없습니다. 다른 소재를 고르세요.")
                used = list(dict.fromkeys(used + ids))
                url = fact_by_id[ids[0]]["source_url"]
            elif htype == "industry":
                ids = list(dict.fromkeys(hook.get("industry_ids") or []))
                if not ids:
                    raise GroundingError("industry 소재에는 hook.industry_ids가 필요합니다.")
                bad = [i for i in ids if i not in ind_by_id]
                if bad:
                    raise GroundingError(f"주어진 업종 화두 후보에 없는 id입니다: {bad}")
                first = ind_by_id[ids[0]]
                url, title = first.get("url"), first.get("title")
                hook_cites = [{"type": "industry", "id": f"{ind_by_id[i].get('brief_id')}#{i}",
                               "url": ind_by_id[i].get("url"), "title": ind_by_id[i].get("title")} for i in ids]
            hook_out = {"type": htype, "summary": summary, "source_url": url, "source_title": title}
            if htype != "inquiry":
                if not (subject_hook and subject_hook.strip()):
                    raise GroundingError("소재를 반영한 제목(subject_hook)도 쓰세요.")
                if len(subject_hook.strip()) > MAX_SUBJECT_LEN:
                    raise GroundingError(f"제목(소식 반영)이 {MAX_SUBJECT_LEN}자를 넘습니다. 줄여서 다시 제출하세요.")
                subjects["hook"] = subject_hook.strip()
        fb = check_ids(feedback_ids, feedback)
        kb = kctx.check("\n".join([*subjects.values(), body]), kb_ids or [])
        options = [{"id": sid, "label": SUBJECT_LABELS[sid], "subject": s} for sid, s in subjects.items()]
        return {"subject": subjects["general"], "body": body.strip(), "subject_options": options,
                "subject_choice": "general", "hook": hook_out,
                "citations": [{"type": "fact", "id": i} for i in used] + [{"type": "kb", "id": i} for i in kb]
                + hook_cites + [{"type": "feedback", "id": i} for i in fb]}

    ids_t = {"type": "array", "items": {"type": "string"}}
    return Tool(
        "submit_draft", "메일 초안을 제출한다. 근거·소재·제목 규칙을 어기면 오류가 돌아오니 고쳐서 다시 제출한다.",
        {"type": "object", "properties": {
            "subject": {"type": "string", "description": "일반 제목 (기본값). 회사명·소재 없이 가치 중심"},
            "subject_company": {"type": "string", "description": "회사명을 넣은 제목"},
            "subject_hook": {"type": "string", "description": "소재를 반영한 제목. 소재가 inquiry이면 생략"},
            "body": {"type": "string", "description": "인사부터 미팅 제안까지. 서명·링크·고지는 빼고"},
            "hook": {"type": "object", "properties": {
                "type": {"type": "string", "enum": ["company", "industry", "inquiry"]},
                "summary": {"type": "string"}, "fact_ids": ids_t, "industry_ids": ids_t},
                "required": ["type", "summary"]},
            "fact_ids_used": ids_t, "kb_ids": ids_t, "feedback_ids": ids_t,
        }, "required": ["subject", "body"]},
        submit_draft, terminal=True)


def run_draft(
    lead: dict[str, Any],
    facts: list[dict],
    score: dict | None,
    llm: LLM,
    log: RunLog,
    *,
    knowledge: KnowledgeContext | None = None,
    booking_url: str | None = None,
    industry_label: str | None = None,
    industry_items: list[dict] | None = None,
    feedback: list[dict] | None = None,
    trigger: str = "manual",
    max_steps: int = 6,
    today: date | None = None,
) -> dict[str, Any]:
    kctx = knowledge or KnowledgeContext()
    industry_items, feedback = industry_items or [], feedback or []
    registry = ToolRegistry([kctx.tool(), _draft_tool(lead, facts, kctx, industry_items, feedback)])
    goal = build_goal(lead, facts, score, today=today, industry_label=industry_label,
                      industry_items=industry_items, feedback=feedback)
    result = run_agent(llm, SYSTEM, goal, registry, log,
                       workflow="draft", trigger=trigger, lead_id=lead.get("id"), max_steps=max_steps)
    draft = result.terminal_output
    if draft:
        draft = {**draft, "body": compose_body(draft["body"], booking_url)}
    return {"run_id": log.run_id, "status": result.status, "stop_reason": result.stop_reason, "draft": draft}


def run_rewrite(
    lead: dict[str, Any],
    facts: list[dict],
    message: dict[str, Any],
    instruction: str,
    llm: LLM,
    log: RunLog,
    *,
    knowledge: KnowledgeContext | None = None,
    booking_url: str | None = None,
    industry_label: str | None = None,
    industry_items: list[dict] | None = None,
    feedback: list[dict] | None = None,
    trigger: str = "chat",
    max_steps: int = 6,
    today: date | None = None,
) -> dict[str, Any]:
    """사용자의 지시(예: "제목을 더 짧게")에 맞게 초안을 고쳐 쓴다. 근거 규칙은 첫 초안과 같다."""
    kctx = knowledge or KnowledgeContext()
    industry_items, feedback = industry_items or [], feedback or []
    registry = ToolRegistry([kctx.tool(), _draft_tool(lead, facts, kctx, industry_items, feedback)])
    goal = (build_goal(lead, facts, None, today=today, industry_label=industry_label,
                       industry_items=industry_items, feedback=feedback)
            + f"\n\n현재 초안 제목(일반): {message.get('subject')}\n현재 초안 본문:\n\"\"\"\n"
              f"{strip_generated_tail(message.get('body') or '')}\n\"\"\"\n"
              f"사용자의 수정 지시: {instruction}")
    result = run_agent(llm, SYSTEM + REWRITE_RULE, goal, registry, log,
                       workflow="draft", trigger=trigger, lead_id=lead.get("id"), max_steps=max_steps)
    draft = result.terminal_output
    if draft:
        draft = {**draft, "body": compose_body(draft["body"], booking_url)}
    return {"run_id": log.run_id, "status": result.status, "stop_reason": result.stop_reason, "draft": draft}
