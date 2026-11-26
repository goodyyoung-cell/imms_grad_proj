"""메일 발송 1시간 뒤 전화 팔로업 콜 스크립트.

통화 목적
1. 메일을 잘 받으셨는지 확인한다.
2. 미팅을 언제쯤 원하시는지 묻는다.
3. 고객사의 Compelling Reason(왜 지금 바꿔야 하는가)을 찾는다.

전화는 사람이 직접 건다 (인터넷·유선 전화와 연동하지 않음). 에이전트는 준비 자료만 만든다 (내부 작업이라 자율).

근거 규칙은 메일 초안과 같다.
- 회사 이야기는 저장된 리서치 사실(fact id)만, 제품·가격·정책은 search_kb로 받은 자료 id만.
- 가격·할인·체험 조건을 말하면 가격 자료가 있어야 한다.
- 통화 허락, 녹음 동의, 수신 거부 시 즉시 종료 같은 필수 문구는 코드가 넣는다.
LLM이 없거나 실패하면 기본 질문과 반대 의견 대응 자료로 기본 스크립트를 만든다.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent import RunLog, Tool, ToolRegistry, run_agent
from ..kb import default_kb
from ..llm.base import LLM
from ..toolkit.knowledge import GroundingError, KnowledgeContext

# 코드가 넣는 필수 문구
COMPLIANCE = {
    "permission": "지금 2~3분 통화 괜찮으실까요? (어렵다고 하면 편한 시간을 여쭙고 바로 마무리)",
    "recording": "통화를 녹음하거나 기록할 경우, 시작 전에 반드시 동의를 구합니다.",
    "stop": "연락을 원하지 않는다고 하면 사과하고 즉시 통화를 마친 뒤, 결과를 '연락 거부'로 기록합니다.",
    "no_promises": "자료에 없는 할인·일정·기능은 약속하지 않고 \"확인해서 메일로 드리겠습니다\"라고 답합니다.",
}
OBJECTIONS_DOC = "04_objections"
MIN_CR_QUESTIONS = 3

# Compelling Reason: 고객이 '왜 지금' 바꿔야 하는가. 계기·영향·미해결 비용·기한·반길 사람을 듣는다
DEFAULT_CR_QUESTIONS = [
    {"angle": "계기", "question": "이번에 고객 문의 응대를 살펴보시게 된 계기가 있으셨을까요?",
     "listen_for": "최근 사건(문의 폭주, 불만 증가), 성수기, 조직 변화, 경영진 지시"},
    {"angle": "영향", "question": "지금 응대 방식에서 숫자로 드러나는 어려움이 있을까요? 응답 시간이나 문의량 같은 것이요.",
     "listen_for": "첫 응답 시간, 월 문의량, 상담원 수, 재문의율 같은 구체적인 지표"},
    {"angle": "미해결 비용", "question": "지금 방식을 그대로 두면 어떤 점이 가장 부담되실까요?",
     "listen_for": "인건비 증가, 고객 이탈, 평판 하락, 상담원 이직"},
    {"angle": "기한", "question": "언제까지 해결되면 좋으실까요? 그 시점이 정해진 이유가 있을까요?",
     "listen_for": "성수기·예산 주기·프로젝트 일정 같은 기한과 그 이유"},
    {"angle": "반길 사람", "question": "이 문제가 풀리면 사내에서 누가 가장 반기실까요?",
     "listen_for": "결정권자, 예산 보유자, 함께 검토할 부서"},
]
RECORD_FIELDS = ["메일 수신 여부", "희망 미팅 시기", "Compelling Reason", "통화 결과"]

SYSTEM = """당신은 노바웍스(제품: 노바데스크) SDR이 전화 팔로업을 준비하도록 돕는 에이전트입니다.
한 시간 전쯤 고객에게 메일을 보냈습니다. SDR이 전화로
1) 메일을 잘 받으셨는지 확인하고, 2) 미팅을 언제쯤 원하시는지 묻고,
3) 고객사의 Compelling Reason(왜 지금 바꿔야 하는가)을 찾도록 콜 스크립트를 씁니다.

규칙:
- 말로 하는 문장입니다. 짧고 자연스러운 존댓말, 한 문장에 하나의 내용. 캐묻는 느낌이 들지 않게.
- 리드 회사에 관한 내용은 주어진 리서치 사실만 씁니다. 없으면 문의 경로·문의 메모를 소재로 씁니다.
- 제품·가격·정책 내용은 search_kb로 찾은 자료만 씁니다. 반대 의견 대응은 반대 의견 대응 자료를 찾아 근거로 씁니다.
- email_summary: 보낸 메일의 핵심을 30초 안에 말할 수 있게 2~3문장.
- meeting_ask: 미팅 희망 시기를 묻는 한두 문장 (20분 화상 데모 제안).
- compelling_questions: 이 고객에게 맞춘 Compelling Reason 질문 3~5개. 각각 angle(계기·영향·미해결 비용·기한·반길 사람 중),
  question, listen_for(답에서 들어야 할 신호). 리서치 사실을 활용했으면 fact_ids를 답니다.
- objections: 이 고객에게 나올 법한 반대 의견 2~3개와 대응. 대응마다 근거 kb_ids.
- voicemail: 부재 시 남길 20초 이내 음성 메시지. 메일을 보냈다는 사실과 회신 방법만.
- 통화 허락·녹음 동의·수신 거부 처리 문구는 시스템이 붙이니 쓰지 마세요.
- 다 쓰면 submit_call_script를 호출합니다."""


def build_goal(lead: dict, facts: list[dict], score: dict | None, sent: dict) -> str:
    card = {k: lead.get(k) for k in ("name", "company_name", "title", "source", "notes")}
    fact_card = [{"fact_id": f["id"], "field": f["field"], "value": f["value"]} for f in facts]
    return (
        "다음 리드에게 보낸 메일을 전화로 팔로업할 콜 스크립트를 쓰세요.\n"
        f"리드: {json.dumps(card, ensure_ascii=False, default=str)}\n"
        f"리서치 사실: {json.dumps(fact_card, ensure_ascii=False)}\n"
        f"선별 근거 태그: {json.dumps((score or {}).get('tags', []), ensure_ascii=False)}\n"
        f"보낸 메일 제목: {sent.get('subject')}\n보낸 메일 본문:\n\"\"\"\n{(sent.get('body') or '')[:3000]}\n\"\"\""
    )


def default_objections(limit: int = 3) -> list[dict]:
    chunks = [c for c in default_kb().chunks if c.id.startswith(OBJECTIONS_DOC)][:limit]
    return [{"objection": c.heading, "response": c.text, "kb_ids": [c.id]} for c in chunks]


def assemble(lead: dict, sent: dict, parts: dict, booking_url: str | None) -> dict[str, Any]:
    """LLM 결과(또는 기본값)에 필수 문구와 고정 항목을 붙여 최종 스크립트로."""
    who = lead.get("name") or "담당자"
    title = f" {lead['title']}" if lead.get("title") else ""
    opening = (f"안녕하세요, {who}{title}님. 노바웍스 노바데스크 담당자 ○○○입니다. "
               f"조금 전에 '{sent.get('subject')}' 제목으로 메일을 드렸는데, 잘 받아 보셨을까요?")
    if parts.get("opening_hook"):
        opening += " " + parts["opening_hook"].strip()
    meeting_ask = parts.get("meeting_ask") or ("20분 정도 화면으로 직접 보여 드리고 싶은데, 미팅은 언제쯤이 편하실까요? "
                                               "이번 주나 다음 주 중 원하시는 요일과 시간대를 말씀해 주시면 맞추겠습니다.")
    if booking_url:
        meeting_ask += f" (통화 뒤 예약 링크 다시 보내기: {booking_url})"
    return {
        "phone": lead.get("phone"),
        "compliance": COMPLIANCE,
        "opening": opening,
        "if_not_received": "메일함(스팸함 포함)을 한번 봐 주실 수 있을지 여쭙고, 핵심만 짧게 말씀드린 뒤 통화 끝나고 다시 보내 드립니다.",
        "email_summary": parts.get("email_summary") or f"메일에서는 '{sent.get('subject')}'에 대해 안내드렸습니다.",
        "talking_points": parts.get("talking_points") or [],
        "meeting_ask": meeting_ask,
        "compelling_questions": parts.get("compelling_questions") or DEFAULT_CR_QUESTIONS,
        "objections": parts.get("objections") or default_objections(),
        "voicemail": parts.get("voicemail") or (f"안녕하세요, 노바웍스 ○○○입니다. {who}님께 조금 전 노바데스크 관련 메일을 드려서 "
                                                  "확인차 연락드렸습니다. 메일로 편한 시간을 알려 주시면 다시 연락드리겠습니다. 감사합니다."),
        "record_fields": RECORD_FIELDS,
        "citations": parts.get("citations", []),
        "outcomes": ["interested", "not_now", "needs_info", "no_answer", "voicemail", "wrong_contact", "unsubscribe"],
        "notices": [],  # 통화 전에 SDR이 알아야 할 일 (예: 고객이 그새 답장함). 시스템이 덧붙인다
    }


def run_call_script(
    lead: dict[str, Any],
    facts: list[dict],
    score: dict | None,
    sent: dict,
    llm: LLM | None,
    log: RunLog | None,
    *,
    knowledge: KnowledgeContext | None = None,
    booking_url: str | None = None,
    trigger: str = "email_sent",
    max_steps: int = 6,
) -> dict[str, Any]:
    if llm is None or log is None:
        return {"run_id": None, "by": "rule", "script": assemble(lead, sent, {}, booking_url)}

    kctx = knowledge or KnowledgeContext()
    fact_ids = {f["id"] for f in facts}

    def check_facts(items: list[dict]) -> list[str]:
        used: list[str] = []
        for it in items:
            bad = [i for i in it.get("fact_ids", []) if i not in fact_ids]
            if bad:
                raise GroundingError(f"주어진 리서치 사실에 없는 fact_id입니다: {bad}")
            used += it.get("fact_ids", [])
        return used

    def submit_call_script(email_summary: str, meeting_ask: str, compelling_questions: list[dict],
                           objections: list[dict], voicemail: str, opening_hook: str = "",
                           talking_points: list[dict] | None = None) -> dict:
        talking_points = talking_points or []
        if len(compelling_questions) < MIN_CR_QUESTIONS:
            raise GroundingError(f"compelling_questions를 {MIN_CR_QUESTIONS}개 이상 쓰세요.")
        if any(not (q.get("question") and q.get("listen_for")) for q in compelling_questions):
            raise GroundingError("compelling_questions마다 question과 listen_for가 필요합니다.")
        used_facts = check_facts(talking_points) + check_facts(compelling_questions)
        kb_ids: list[str] = []
        for ob in objections:
            if not ob.get("kb_ids"):
                raise GroundingError(f"반대 의견 '{ob.get('objection')}'의 대응에 근거(kb_ids)가 없습니다. search_kb로 찾아 넣으세요.")
            kb_ids += ob["kb_ids"]
        kb_ids += [i for tp in talking_points for i in tp.get("kb_ids", [])]
        spoken = "\n".join([email_summary, opening_hook, meeting_ask, voicemail,
                            *(tp.get("text", "") for tp in talking_points),
                            *(q.get("question", "") for q in compelling_questions),
                            *(f"{o.get('objection', '')} {o.get('response', '')}" for o in objections)])
        kb_ids = kctx.check(spoken, list(dict.fromkeys(kb_ids)))
        return {"opening_hook": opening_hook, "email_summary": email_summary, "meeting_ask": meeting_ask,
                "talking_points": talking_points, "compelling_questions": compelling_questions,
                "objections": objections, "voicemail": voicemail,
                "citations": [{"type": "fact", "id": i} for i in dict.fromkeys(used_facts)]
                + [{"type": "kb", "id": i} for i in kb_ids]}

    ids = {"type": "array", "items": {"type": "string"}}
    item_tp = {"type": "object", "properties": {"text": {"type": "string"}, "fact_ids": ids, "kb_ids": ids},
               "required": ["text"]}
    item_cr = {"type": "object", "properties": {
        "angle": {"type": "string", "enum": ["계기", "영향", "미해결 비용", "기한", "반길 사람"]},
        "question": {"type": "string"}, "listen_for": {"type": "string"}, "fact_ids": ids},
        "required": ["angle", "question", "listen_for"]}
    item_ob = {"type": "object", "properties": {"objection": {"type": "string"}, "response": {"type": "string"},
                                                "kb_ids": ids}, "required": ["objection", "response", "kb_ids"]}
    registry = ToolRegistry([
        kctx.tool(),
        Tool("submit_call_script", "콜 스크립트를 제출한다. 근거 규칙을 어기면 오류가 돌아오니 고쳐서 다시 제출한다.",
             {"type": "object", "properties": {
                 "opening_hook": {"type": "string", "description": "수신 확인 뒤 관심을 끌 한 문장 (리서치 사실이나 문의 메모 기반)"},
                 "email_summary": {"type": "string"},
                 "meeting_ask": {"type": "string"},
                 "talking_points": {"type": "array", "items": item_tp},
                 "compelling_questions": {"type": "array", "items": item_cr},
                 "objections": {"type": "array", "items": item_ob},
                 "voicemail": {"type": "string"},
             }, "required": ["email_summary", "meeting_ask", "compelling_questions", "objections", "voicemail"]},
             submit_call_script, terminal=True),
    ])
    result = run_agent(llm, SYSTEM, build_goal(lead, facts, score, sent), registry, log,
                       workflow="call_script", trigger=trigger, lead_id=lead.get("id"), max_steps=max_steps)
    parts = result.terminal_output or {}
    return {"run_id": log.run_id, "by": "agent" if parts else "rule",
            "stop_reason": result.stop_reason, "script": assemble(lead, sent, parts, booking_url)}


def render_text(script: dict, lead: dict, due_at: str | None = None) -> str:
    """SDR이 통화하면서 보는 한 장짜리 텍스트."""
    c = script["compliance"]
    lines = [f"[콜 팔로업] {lead.get('company_name')} · {lead.get('name') or '담당자'} ({lead.get('title') or '직급 미상'})",
             f"전화: {script.get('phone') or '번호 없음 (대표번호 경유 또는 메일 회신 유도)'}" + (f" · 예정: {due_at}" if due_at else "")]
    if script.get("notices"):
        lines += ["", "■ 통화 전 확인"] + [f"- {n['text']}" for n in script["notices"]]
    lines += ["", "■ 시작 전", f"- {c['permission']}", f"- {c['recording']}",
              "", "■ 1. 메일 수신 확인", script["opening"], f"- 못 받았다면: {script['if_not_received']}",
              "", "■ 메일 핵심 (30초)", script["email_summary"]]
    if script["talking_points"]:
        lines += ["", "■ 대화 소재"] + [f"- {t['text']}" for t in script["talking_points"]]
    lines += ["", "■ 2. 미팅 희망 시기", script["meeting_ask"],
              "", "■ 3. Compelling Reason 찾기"]
    lines += [f"- [{q.get('angle', '')}] {q['question']}\n    들을 신호: {q['listen_for']}"
              for q in script["compelling_questions"]]
    lines += ["", "■ 예상 반대 의견"] + [f"- {o['objection']} → {o['response']}" for o in script["objections"]]
    lines += ["", "■ 부재 시 음성 메시지", script["voicemail"],
              "", "■ 지킬 것", f"- {c['stop']}", f"- {c['no_promises']}",
              "", "■ 기록할 것: " + " / ".join(script["record_fields"]),
              "결과: " + " / ".join(script["outcomes"])]
    return "\n".join(lines)
