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
    ("unsubscribe", 0.3, False, "unsubscribe"),    # 확신 낮아도 멈춘다
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
    assert msg["kind"] == "followup" and msg["status"] == "pending_approval" and msg["body"].endswith(FOOTER)
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
