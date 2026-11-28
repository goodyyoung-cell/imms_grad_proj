"""모듈 2·4: 초안 → 승인 → 샌드박스 발송 → 답장 분류 → 인계. 가짜 LLM과 가짜 DB로 검증한다."""

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sdr_agent import jobs
from sdr_agent.agent import MemoryRunLog
from sdr_agent.kb import default_kb
from sdr_agent.llm.base import LLMResponse, ToolCall
from sdr_agent.outbox import SandboxSender
from sdr_agent.workflows.draft import FOOTER, run_draft
from sdr_agent.workflows.handoff import SCREENING_TOPICS, run_handoff
from sdr_agent.workflows.reply import decide, detect_unsubscribe, run_reply_triage
from tests.test_research_agent import ScriptedLLM

NOW = datetime.now(timezone.utc).isoformat()
LEAD = {"id": "L1", "name": "김가상", "email": "kim@hanbit.example", "company_name": "한빛몰", "title": "CS팀장",
        "source": "demo_request", "inbound_at": NOW, "notes": "성수기 문의 급증", "status": "researched",
        "unsubscribed_at": None}
FACTS = [{"id": "F1", "field": "summary", "value": "온라인 생활용품 쇼핑몰", "source_url": "https://hanbit.example"}]
SCORE = {"score": 62, "tags": ["데모 요청", "팀장·부장급"], "breakdown": {"source": 30}}


def call(name, **args):
    return ToolCall(f"c-{name}", name, args)


# --- 가짜 DB: DB 트리거 두 개(수신 거부 시 취소, 미승인 발송 차단)도 흉내 낸다 ---

class FakeRepo:
    def __init__(self, leads):
        self.leads = {l["id"]: dict(l) for l in leads}
        self.messages, self.approvals, self.replies, self.handoffs = {}, [], [], []
        self.calls = {}
        self.facts = {"L1": FACTS}
        self.scores = {"L1": SCORE}
        self._ids = itertools.count(1)

    def _id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    def get_lead(self, lead_id):
        return {**self.leads[lead_id], "channel_count": 1}

    def find_lead_by_email(self, email):
        return next((l for l in self.leads.values() if l["email"].lower() == email.lower()), None)

    def list_lead_ids(self, status, limit=10):
        return [i for i, l in self.leads.items() if l["status"] == status][:limit]

    def set_status(self, lead_id, status):
        self.leads[lead_id]["status"] = status

    def unsubscribe(self, lead_id):
        self.leads[lead_id].update(unsubscribed_at=NOW, status="unsubscribed")
        for m in self.messages.values():
            if m["lead_id"] == lead_id and m["status"] in ("pending_approval", "approved"):
                m["status"] = "cancelled"
        for t in self.calls.values():
            if t["lead_id"] == lead_id and t["status"] == "pending":
                t["status"] = "cancelled"

    def insert_call_task(self, row):
        t = {"id": self._id("C"), "status": "pending", "outcome": None, "called_at": None, **row}
        self.calls[t["id"]] = t
        return dict(t)

    def get_call_task(self, task_id):
        return dict(self.calls[task_id])

    def list_call_tasks(self, lead_id=None, status=None, due_before=None, limit=100):
        return [dict(t) for t in self.calls.values() if (lead_id is None or t["lead_id"] == lead_id)
                and (status is None or t["status"] == status) and (due_before is None or t["due_at"] <= due_before)]

    def update_call_task(self, task_id, fields):
        self.calls[task_id].update(fields)
        return dict(self.calls[task_id])

    def list_facts(self, lead_id):
        return self.facts.get(lead_id, [])

    def latest_score(self, lead_id):
        return self.scores.get(lead_id)

    def insert_message(self, lead_id, run_id, kind, draft):
        m = {"id": self._id("M"), "lead_id": lead_id, "run_id": run_id, "kind": kind, "status": "pending_approval",
             "subject": draft["subject"], "body": draft["body"], "citations": draft.get("citations", []),
             "created_at": NOW, "sent_at": None}
        self.messages[m["id"]] = m
        return dict(m)

    def get_message(self, message_id):
        if message_id not in self.messages:
            raise LookupError(message_id)
        return dict(self.messages[message_id])

    def list_messages(self, lead_id=None, status=None, limit=50):
        return [dict(m) for m in self.messages.values()
                if (lead_id is None or m["lead_id"] == lead_id) and (status is None or m["status"] == status)]

    def update_message(self, message_id, fields):
        m = self.messages[message_id]
        if fields.get("status") == "sent" and m["status"] != "sent":
            if m["status"] != "approved":
                raise RuntimeError("DB: not approved")
            if self.leads[m["lead_id"]]["unsubscribed_at"]:
                raise RuntimeError("DB: unsubscribed")
            fields = {**fields, "sent_at": NOW}
        m.update(fields)
        return dict(m)

    def insert_approval(self, row):
        self.approvals.append(row)

    def count_sent_since(self, since_iso):
        return sum(1 for m in self.messages.values() if m["status"] == "sent")

    def update_lead(self, lead_id, fields):
        self.leads[lead_id].update(fields)
        return dict(self.leads[lead_id])

    def insert_reply(self, row):
        r = {"id": self._id("R"), "received_at": NOW, **row}
        self.replies.append(r)
        return r

    def list_replies(self, lead_id):
        return [r for r in self.replies if r["lead_id"] == lead_id]

    def insert_handoff(self, row):
        h = {"id": self._id("H"), **row}
        self.handoffs.append(h)
        return h


@pytest.fixture
def repo():
    return FakeRepo([LEAD])


def good_draft_llm():
    return ScriptedLLM([
        LLMResponse("체험 조건을 찾아봅니다", [call("search_kb", query="무료 체험")]),
        LLMResponse("제출합니다", [call("submit_draft", subject="한빛몰 성수기 문의 응대 관련",
                                         body="김가상 팀장님, 안녕하세요. 14일 무료 체험으로 먼저 확인해 보실 수 있습니다. 20분 통화 가능하실까요?",
                                         fact_ids_used=["F1"], kb_ids=["02_pricing#무료 체험"])]),
    ])


# --- 지식베이스 ---

def test_kb_finds_relevant_section_and_nothing_for_offtopic():
    kb = default_kb()
    assert kb.search("무료 체험 기간")[0]["id"] == "02_pricing#무료 체험"
    assert kb.search("고객 데이터로 AI 학습하나요")[0]["id"].startswith("05_security")
    assert kb.search("점심 메뉴 추천") == []


# --- 초안 ---

def test_draft_rejects_ungrounded_price_then_accepts_fix():
    llm = ScriptedLLM([
        # 가격을 말했는데 가격 자료를 검색하지 않음 → 오류로 돌려받고 다시 씀
        LLMResponse("", [call("submit_draft", subject="안녕하세요", body="1인당 월 1만원입니다.")]),
        LLMResponse("", [call("search_kb", query="무료 체험")]),
        LLMResponse("", [call("submit_draft", subject="안녕하세요", body="14일 무료 체험이 있습니다.",
                              kb_ids=["02_pricing#무료 체험"])]),
    ])
    log = MemoryRunLog()
    out = run_draft(LEAD, FACTS, SCORE, llm, log, booking_url="https://book.example/nova")
    tool_steps = [s for s in log.steps if s.kind == "tool"]
    assert tool_steps[0].is_error and "가격 자료" in tool_steps[0].output["error"]
    assert out["draft"]["citations"] == [{"type": "kb", "id": "02_pricing#무료 체험"}]
    body = out["draft"]["body"]
    assert "https://book.example/nova" in body and body.endswith(FOOTER)  # 고지·수신 거부 안내는 코드가 붙인다


def test_draft_rejects_kb_id_not_retrieved_and_unknown_fact():
    llm = ScriptedLLM([
        LLMResponse("", [call("submit_draft", subject="s", body="b", kb_ids=["01_product#주요 기능"])]),
        LLMResponse("", [call("submit_draft", subject="s", body="b", fact_ids_used=["F999"])]),
        LLMResponse("", [call("submit_draft", subject="s", body="데모 요청 감사합니다.", fact_ids_used=["F1"])]),
    ])
    out = run_draft(LEAD, FACTS, SCORE, llm, MemoryRunLog())
    assert out["draft"]["citations"] == [{"type": "fact", "id": "F1"}]


# --- 답장 분류 ---

def test_unsubscribe_rules_on_sample_replies():
    cases = json.loads((Path(__file__).parent.parent / "data/sample/replies.json").read_text(encoding="utf-8"))
    for c in cases:
        assert detect_unsubscribe(c["body"]) == (c["expected"] == "unsubscribe"), c["id"]


def test_unsubscribe_skips_llm():
    out = run_reply_triage(LEAD, "수신 거부합니다", llm=None, log=None)
    assert out["action"] == "unsubscribe" and out["by"] == "rule"


@pytest.mark.parametrize("cls,conf,has_answer,expected", [
    ("interested", 0.9, False, "handoff"),
    ("interested", 0.5, False, "needs_human"),     # 확신 낮음
    ("question", 0.9, True, "answer_draft"),
    ("question", 0.9, False, "needs_human"),       # 근거 없는 질문
    ("unclear", 0.95, False, "needs_human"),
    ("unsubscribe", 0.95, False, "unsubscribe"),          # LLM 수신 거부, 확신 높음 → 즉시 중단
    ("unsubscribe", 0.75, False, "confirm_unsubscribe"),  # 확신 낮음 → 사람이 확인 (Gemini R23 사례)
    ("not_now", 0.8, False, "followup_later"),
])
def test_decide(cls, conf, has_answer, expected):
    assert decide(cls, conf, has_answer) == expected


def test_question_without_kb_answer_goes_to_human():
    llm = ScriptedLLM([
        LLMResponse("", [call("search_kb", query="ISMS 인증")]),
        LLMResponse("", [call("submit_triage", classification="question", confidence=0.9, reason="인증 질문, 근거 없음")]),
    ])
    out = run_reply_triage(LEAD, "ISMS 인증 있나요?", llm, MemoryRunLog())
    assert out["action"] == "needs_human"


# --- 잡: 전체 루프 ---

def test_draft_approve_send(repo):
    sender = SandboxSender()
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    assert r["status"] == "drafted" and repo.leads["L1"]["status"] == "awaiting_approval"
    assert sender.sent == []  # 초안만으로는 아무것도 나가지 않는다

    out = jobs.decide_message(r["message_id"], "approved", "담당자A", repo=repo, sender=sender)
    assert out["status"] == "sent" and len(sender.sent) == 1
    assert repo.leads["L1"]["status"] == "contacted"
    assert repo.approvals[0]["decided_by"] == "담당자A"
    # 같은 메시지를 다시 승인할 수 없다
    with pytest.raises(ValueError):
        jobs.decide_message(r["message_id"], "approved", "담당자A", repo=repo, sender=sender)


def test_edited_approval_keeps_mandatory_footer(repo):
    sender = SandboxSender()
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    jobs.decide_message(r["message_id"], "edited", "담당자A", edited_body="짧게 고친 본문", repo=repo, sender=sender)
    body = sender.sent[0]["body"]
    assert body.startswith("짧게 고친 본문") and FOOTER in body and 'reply "unsubscribe"' in body  # 광고 표시 의무로 안내가 늘었다


def test_reject_does_not_send(repo):
    sender = SandboxSender()
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    jobs.decide_message(r["message_id"], "rejected", "담당자A", note="톤 과함", repo=repo, sender=sender)
    assert sender.sent == [] and repo.leads["L1"]["status"] == "needs_human"


def test_unsubscribe_reply_cancels_pending_and_blocks_send(repo):
    sender = SandboxSender()
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    out = jobs.handle_reply("메일 그만 보내 주세요", from_email="KIM@hanbit.example", repo=repo)
    assert out["action"] == "unsubscribe"
    assert repo.leads["L1"]["status"] == "unsubscribed"
    assert repo.messages[r["message_id"]]["status"] == "cancelled"
    with pytest.raises(ValueError):  # 취소된 메시지는 승인 대기가 아니므로 승인 불가
        jobs.decide_message(r["message_id"], "approved", "담당자A", repo=repo, sender=sender)
    assert sender.sent == []
    # 수신 거부 리드에는 새 초안도 만들지 않는다
    assert jobs.draft_emails(["L1"], repo=repo, llm=good_draft_llm())[0]["status"] == "skipped"


def test_question_reply_creates_followup_awaiting_approval(repo):
    llm = ScriptedLLM([
        LLMResponse("", [call("search_kb", query="카카오톡 채널 연동")]),
        LLMResponse("", [call("submit_triage", classification="question", confidence=0.85, reason="연동 질문",
                              answer_body="네, 카카오톡 채널 문의도 통합 문의함에서 관리됩니다.",
                              kb_ids=["01_product#연동"])]),
    ])
    out = jobs.handle_reply("카카오톡 채널도 되나요?", lead_id="L1", repo=repo, llm=llm)
    assert out["action"] == "answer_draft"
    msg = repo.messages[out["message_id"]]
    assert msg["kind"] == "followup" and msg["status"] == "pending_approval" and FOOTER in msg["body"]
    assert repo.replies[0]["classification"] == "question" and repo.replies[0]["handled_by"] == "agent"


def test_interested_reply_hands_off_with_screening_questions(repo):
    llm = ScriptedLLM([
        LLMResponse("", [call("submit_triage", classification="interested", confidence=0.92, reason="미팅 의사")]),
        LLMResponse("", [call("submit_handoff", interests=["성수기 문의 응대"],
                              recommended_actions=["화요일 오후 미팅 확정"],
                              screening_questions={"purpose": "성수기 지연을 얼마나 줄이고 싶으신가요?",
                                                   "environment": "카카오톡 문의 비중은?",
                                                   "decision": "CS팀장님 외 결재 라인은?",
                                                   "timing": "다음 성수기 전 도입이 목표인가요?"})]),
    ])
    out = jobs.handle_reply("다음 주 화요일 오후 미팅 가능합니다", lead_id="L1", repo=repo, llm=llm)
    assert out["action"] == "handoff" and repo.leads["L1"]["status"] == "handed_off"
    h = repo.handoffs[0]
    assert [q["topic"] for q in h["screening_questions"]] == list(SCREENING_TOPICS)
    assert h["summary"]["research"][0]["source_url"] == "https://hanbit.example"
    assert "화요일" in out["handoff"]["text"]


def test_handoff_without_llm_uses_records_and_default_questions():
    out = run_handoff(LEAD, FACTS, SCORE, [], [], llm=None, log=None)
    assert out["by"] == "rule"
    assert len(out["screening_questions"]) == 4
    assert out["summary"]["selection"]["score"] == 62


# --- 전화 팔로업 ---

from datetime import datetime as _dt  # noqa: E402

from sdr_agent.workflows.call_script import (COMPLIANCE, DEFAULT_CR_QUESTIONS,  # noqa: E402
                                             render_text as render_call, run_call_script)
from sdr_agent.jobs import KST, call_due_at  # noqa: E402


def _approve_first_mail(repo):
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    out = jobs.decide_message(r["message_id"], "approved", "담당자A", repo=repo, sender=SandboxSender())
    return r["message_id"], out


def test_first_mail_sent_creates_call_task_with_default_script(repo):
    message_id, out = _approve_first_mail(repo)
    ct = out["call_task"]
    assert ct["status"] == "pending" and ct["by"] == "rule"  # 키가 없으면 기본 스크립트
    [task] = repo.calls.values()
    s = task["script"]
    assert task["message_id"] == message_id
    assert s["compliance"] == COMPLIANCE
    assert "잘 받아 보셨을까요" in s["opening"] and "한빛몰 성수기 문의 응대 관련" in s["opening"]  # 1. 수신 확인
    assert "미팅은 언제쯤" in s["meeting_ask"]                                                    # 2. 미팅 희망 시기
    assert s["compelling_questions"] == DEFAULT_CR_QUESTIONS                                      # 3. Compelling Reason
    assert all(o["kb_ids"][0].startswith("04_objections") for o in s["objections"])
    text = render_call(s, LEAD)
    for heading in ("1. 메일 수신 확인", "2. 미팅 희망 시기", "3. Compelling Reason 찾기", "들을 신호", "부재 시 음성 메시지"):
        assert heading in text


def test_followup_answer_does_not_create_call_task(repo):
    repo.messages["MX"] = {"id": "MX", "lead_id": "L1", "kind": "followup", "status": "pending_approval",
                           "subject": "답변", "body": "본문", "citations": [], "created_at": NOW, "sent_at": None}
    out = jobs.decide_message("MX", "approved", "담당자A", repo=repo, sender=SandboxSender())
    assert "call_task" not in out and repo.calls == {}


@pytest.mark.parametrize("sent,expected", [
    (_dt(2026, 11, 26, 14, 20, tzinfo=KST), _dt(2026, 11, 26, 15, 20, tzinfo=KST)),  # 목 오후 → 1시간 뒤
    (_dt(2026, 11, 26, 17, 30, tzinfo=KST), _dt(2026, 11, 27, 9, 0, tzinfo=KST)),    # 18시 넘어감 → 다음 날 9시
    (_dt(2026, 11, 26, 7, 0, tzinfo=KST), _dt(2026, 11, 26, 9, 0, tzinfo=KST)),      # 이른 아침 → 같은 날 9시
    (_dt(2026, 11, 27, 17, 10, tzinfo=KST), _dt(2026, 11, 30, 9, 0, tzinfo=KST)),    # 금요일 저녁 → 월요일 9시
    (_dt(2026, 11, 28, 11, 0, tzinfo=KST), _dt(2026, 11, 30, 9, 0, tzinfo=KST)),     # 토요일 → 월요일 9시
    (_dt(2026, 11, 26, 5, 20, tzinfo=timezone.utc), _dt(2026, 11, 26, 15, 20, tzinfo=KST)),  # UTC 입력도 한국 시간 기준
])
def test_call_due_at_one_hour_within_business_hours(sent, expected):
    assert call_due_at(sent, 60, (9, 18)) == expected


def test_reply_keeps_call_and_adds_notice(repo):
    _approve_first_mail(repo)
    llm = ScriptedLLM([LLMResponse("", [call("submit_triage", classification="not_now", confidence=0.9, reason="내년")])])
    out = jobs.handle_reply("내년에 다시 연락 주세요", lead_id="L1", repo=repo, llm=llm)
    [task] = repo.calls.values()
    assert out["calls_noticed"] == 1 and task["status"] == "pending"  # 전화는 사람이 거니 취소하지 않는다
    assert "답장" in task["script"]["notices"][0]["text"] and "not_now" in task["script"]["notices"][0]["text"]
    assert "통화 전 확인" in render_call(task["script"], LEAD)


def test_unsubscribe_reply_still_cancels_call(repo):
    _approve_first_mail(repo)
    jobs.handle_reply("수신 거부합니다", lead_id="L1", repo=repo)
    assert [t["status"] for t in repo.calls.values()] == ["cancelled"]  # 연락 중단은 예외 (DB 트리거)


def test_handoff_keeps_call_and_adds_notice(repo):
    _approve_first_mail(repo)
    jobs.handoff_lead("L1", reason="담당자 요청", repo=repo, use_llm=False)
    [task] = repo.calls.values()
    assert task["status"] == "pending" and task["script"]["notices"][0]["kind"] == "handoff"


@pytest.mark.parametrize("outcome,status", [("voicemail", "contacted"), ("not_now", "replied"),
                                            ("needs_info", "needs_human"), ("unsubscribe", "unsubscribed")])
def test_call_outcome_moves_lead(repo, outcome, status):
    _approve_first_mail(repo)
    [task_id] = repo.calls
    jobs.record_call_outcome(task_id, outcome, "담당자A", notes="메모", repo=repo)
    assert repo.calls[task_id]["status"] == "done" and repo.leads["L1"]["status"] == status
    with pytest.raises(ValueError):  # 같은 과제를 두 번 기록할 수 없다
        jobs.record_call_outcome(task_id, outcome, "담당자A", repo=repo)


def test_interested_call_hands_off_with_call_insights(repo):
    _approve_first_mail(repo)
    [task_id] = repo.calls
    out = jobs.record_call_outcome(task_id, "interested", "담당자A", email_received=True,
                                   meeting_preference="다음 주 화요일 오후", compelling_reason="12월 성수기 전 응답 지연 해소",
                                   repo=repo)
    assert out["action"] == "handoff" and repo.leads["L1"]["status"] == "handed_off"
    summary = repo.handoffs[0]["summary"]
    assert summary["contact_history"][-1]["type"] == "전화"
    [ci] = summary["call_insights"]
    assert ci["email_received"] is True and ci["meeting_preference"] == "다음 주 화요일 오후"
    assert ci["compelling_reason"] == "12월 성수기 전 응답 지연 해소" and ci["outcome"] == "관심"
    assert "Compelling Reason: 12월 성수기" in out["handoff"]["text"]


def test_call_after_handoff_does_not_change_status(repo):
    _approve_first_mail(repo)
    [task_id] = repo.calls
    jobs.handoff_lead("L1", repo=repo, use_llm=False)
    out = jobs.record_call_outcome(task_id, "not_now", "담당자A", repo=repo)
    assert out["action"] == "keep" and repo.leads["L1"]["status"] == "handed_off"


def test_call_script_agent_must_ground_and_ask_compelling_questions():
    sent = {"subject": "한빛몰 성수기 문의 응대 관련", "body": "본문", "sent_at": NOW}
    crq = [{"angle": "계기", "question": "이번 성수기에 어떤 일이 있었나요?", "listen_for": "문의 폭주", "fact_ids": ["F1"]},
           {"angle": "기한", "question": "언제까지 해결되면 좋을까요?", "listen_for": "성수기 일정"},
           {"angle": "미해결 비용", "question": "그대로 두면 무엇이 부담되세요?", "listen_for": "이탈"}]
    ob_price = {"objection": "비싸다", "response": "1인당 월 29,000원부터입니다"}
    base = dict(email_summary="요약", meeting_ask="미팅은 언제쯤 편하실까요?", voicemail="음성")
    llm = ScriptedLLM([
        LLMResponse("", [call("submit_call_script", **base, compelling_questions=crq[:2],          # 질문 부족
                              objections=[{**ob_price, "kb_ids": ["04_objections#비싸다 / 예산이 없다"]}])]),
        LLMResponse("", [call("submit_call_script", **base, compelling_questions=crq,
                              objections=[{**ob_price, "kb_ids": []}])]),                          # 대응 근거 없음
        LLMResponse("", [call("search_kb", query="비싸다 예산")]),
        LLMResponse("", [call("submit_call_script", **base, compelling_questions=crq,               # 가격 자료 없음
                              objections=[{**ob_price, "kb_ids": ["04_objections#비싸다 / 예산이 없다"]}])]),
        LLMResponse("", [call("search_kb", query="가격 플랜 스타터")]),
        LLMResponse("", [call("submit_call_script", **base, compelling_questions=crq,
                              objections=[{**ob_price, "kb_ids": ["04_objections#비싸다 / 예산이 없다", "02_pricing#플랜"]}])]),
    ])
    log = MemoryRunLog()
    out = run_call_script(LEAD, FACTS, SCORE, sent, llm, log)
    errors = [s.output["error"] for s in log.steps if s.kind == "tool" and s.is_error]
    assert len(errors) == 3 and "3개 이상" in errors[0] and "근거" in errors[1] and "가격 자료" in errors[2]
    assert out["by"] == "agent" and out["script"]["compelling_questions"] == crq
    assert {"type": "fact", "id": "F1"} in out["script"]["citations"]


def test_call_outcome_api_passes_capture_fields(monkeypatch):
    from fastapi.testclient import TestClient

    from sdr_agent import app as app_module

    monkeypatch.setenv("JOB_TOKEN", "secret")
    seen = {}

    def fake(task_id, outcome, called_by, **kw):
        if outcome not in jobs.CALL_OUTCOMES:
            raise ValueError("bad outcome")
        seen.update(task_id=task_id, outcome=outcome, called_by=called_by, **kw)
        return {"task_id": task_id, "outcome": outcome}

    monkeypatch.setattr(app_module.jobs, "record_call_outcome", fake)
    c = TestClient(app_module.app)
    h = {"Authorization": "Bearer secret"}
    body = {"outcome": "interested", "called_by": "담당자A", "email_received": True,
            "meeting_preference": "다음 주 화요일", "compelling_reason": "성수기 전 지연 해소"}
    assert c.post("/admin/calls/T1/outcome", json=body).status_code == 401
    assert c.post("/admin/calls/T1/outcome", json=body, headers=h).status_code == 200
    assert seen["email_received"] is True and seen["compelling_reason"] == "성수기 전 지연 해소"
    assert c.post("/admin/calls/T1/outcome", json={**body, "outcome": "maybe"}, headers=h).status_code == 400


def test_real_repo_without_log_factory_writes_run_logs_to_db():
    from sdr_agent.agent import SupabaseRunLog
    from sdr_agent.config import load_settings
    from sdr_agent.db import Repo

    repo, new_log = jobs._wire(load_settings(), Repo(client=object()), None)
    assert isinstance(new_log(), SupabaseRunLog)  # 메모리 로그의 가짜 run_id가 DB에 들어가지 않게


@pytest.mark.parametrize("sent,call_at,phrase", [
    (_dt(2026, 11, 26, 14, 0, tzinfo=KST), _dt(2026, 11, 26, 15, 0, tzinfo=KST), "조금 전에"),
    (_dt(2026, 11, 26, 21, 0, tzinfo=KST), _dt(2026, 11, 27, 9, 0, tzinfo=KST), "어제"),
    (_dt(2026, 11, 27, 20, 0, tzinfo=KST), _dt(2026, 11, 30, 9, 0, tzinfo=KST), "11월 27일에"),
])
def test_sent_phrase_matches_call_time(sent, call_at, phrase):
    from sdr_agent.workflows.call_script import assemble, sent_phrase

    assert sent_phrase(sent, call_at) == phrase
    s = assemble(LEAD, {"subject": "제목"}, {}, None, phrase)
    assert s["opening"].count(f"{phrase} '제목'") == 1 and f"{phrase} 노바데스크" in s["voicemail"]


def test_low_confidence_llm_unsubscribe_goes_to_human_and_flags_calls(repo):
    _approve_first_mail(repo)
    llm = ScriptedLLM([LLMResponse("", [call("submit_triage", classification="unsubscribe", confidence=0.75,
                                             reason="바꿀 필요를 못 느낀다고 함")])])
    out = jobs.handle_reply("지금 쓰는 헬프데스크가 있어서요. 굳이 바꿀 필요는 못 느낍니다.", lead_id="L1", repo=repo, llm=llm)
    assert out["action"] == "confirm_unsubscribe"
    lead = repo.leads["L1"]
    assert lead["status"] == "needs_human" and lead["unsubscribed_at"] is None  # 아직 멈추지 않음
    [task] = repo.calls.values()
    assert task["status"] == "pending" and task["script"]["notices"][0]["kind"] == "unsubscribe_suspected"
    assert repo.replies[-1]["handled_by"] == "human"  # 사람이 확인할 답장

    # 담당자가 확인하고 수신 거부로 확정하면 남은 메일·전화가 모두 멈춘다
    res = jobs.confirm_unsubscribe("L1", "담당자A", note="통화로 확인", repo=repo)
    assert res["status"] == "unsubscribed" and repo.leads["L1"]["status"] == "unsubscribed"
    assert task["id"] and repo.calls[task["id"]]["status"] == "cancelled"
    assert jobs.confirm_unsubscribe("L1", "담당자A", repo=repo)["status"] == "already_unsubscribed"


def test_high_confidence_llm_unsubscribe_stops_immediately(repo):
    llm = ScriptedLLM([LLMResponse("", [call("submit_triage", classification="unsubscribe", confidence=0.95,
                                             reason="연락을 원하지 않음")])])
    out = jobs.handle_reply("앞으로 이런 제안은 받지 않겠습니다.", lead_id="L1", repo=repo, llm=llm)
    assert out["action"] == "unsubscribe" and repo.leads["L1"]["status"] == "unsubscribed"
