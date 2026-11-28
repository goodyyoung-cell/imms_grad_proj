"""소식 기반 초안(제목 후보·소재), 업종 브리프, 피드백 메모리, 대화형 에이전트. 가짜 LLM·가짜 DB로 검증한다."""

import itertools
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sdr_agent import chat, feedback, industry, jobs, slack, slack_chat, webform
from sdr_agent.agent import MemoryRunLog
from sdr_agent.config import load_settings
from sdr_agent.llm.base import LLMResponse
from sdr_agent.outbox import SandboxSender
from sdr_agent.workflows.draft import FOOTER, run_draft, strip_generated_tail
from tests.test_email_loop import LEAD, FakeRepo, call
from tests.test_research_agent import ScriptedLLM

NOW = datetime.now(timezone.utc).isoformat()
TODAY = date(2026, 11, 28)
FACTS = [
    {"id": "F1", "field": "summary", "value": "온라인 생활용품 쇼핑몰", "source_url": "https://hanbit.example",
     "source_type": "homepage", "retrieved_at": NOW},
    {"id": "F2", "field": "recent_news", "value": "2026-11-26 한빛몰이 새벽배송 물류센터를 확장한다고 밝혔습니다.",
     "source_url": "https://news.example/a", "source_type": "news", "retrieved_at": NOW},
    {"id": "F3", "field": "recent_news", "value": "2026-11-20 한빛몰 회원 정보 유출 의혹이 제기됐습니다.",
     "source_url": "https://news.example/b", "source_type": "news", "retrieved_at": NOW},
]
ITEMS = [{"id": "ind1", "brief_id": "B1", "title": "이커머스 새벽배송 경쟁 심화", "summary": "문의 증가",
          "url": "https://news.example/ind", "publisher": "x", "published_at": "2026-11-25"}]


class MemRepo(FakeRepo):
    """FakeRepo에 업종 브리프, 피드백, 대화, Slack 위치 조회를 더한 가짜 DB."""

    def __init__(self, leads, facts=None):
        super().__init__(leads)
        self.facts = {"L1": facts if facts is not None else FACTS}
        self.notes, self.briefs, self.sessions, self.turns = {}, {}, {}, []

    def insert_message(self, lead_id, run_id, kind, draft):
        m = super().insert_message(lead_id, run_id, kind, draft)
        extra = {"subject_options": draft.get("subject_options", []), "subject_choice": draft.get("subject_choice", "general"),
                 "hook": draft.get("hook"), "revisions": [], "slack_ref": None}
        self.messages[m["id"]].update(extra)
        return dict(self.messages[m["id"]])

    def list_leads(self, status=None, q=None, limit=50, offset=0):
        rows = [l for l in self.leads.values() if not q or q in l["company_name"]]
        return [dict(r) for r in rows][:limit], len(rows)

    def leads_by_ids(self, ids):
        return {i: dict(self.leads[i]) for i in ids if i in self.leads}

    def status_counts(self):
        return {"researched": 1}

    def count_sent_today(self):
        return 0

    def merged_leads(self, lead_id):
        return []

    def list_handoffs(self, lead_id):
        return []

    def list_runs(self, lead_id=None, workflow=None, limit=50):
        return []

    def find_message_by_slack_ts(self, channel, ts):
        return next((dict(m) for m in self.messages.values()
                     if (m.get("slack_ref") or {}).get("ts") == ts and m["slack_ref"].get("channel") == channel), None)

    # 업종 브리프
    def latest_industry_brief(self, key):
        return self.briefs.get(key)

    def insert_industry_brief(self, key, items):
        self.briefs[key] = {"id": self._id("B"), "industry_key": key, "items": items, "collected_at": NOW}
        return self.briefs[key]

    # 피드백
    def list_feedback(self, scope=None, scope_key=None, status="active", limit=200):
        rows = [dict(n) for n in self.notes.values() if (not scope or n["scope"] == scope)
                and (not scope_key or n["scope_key"] == scope_key) and (status in (None, "all") or n["status"] == status)]
        return sorted(rows, key=lambda n: n["created_at"], reverse=True)[:limit]

    def feedback_by_ids(self, ids):
        return {i: dict(self.notes[i]) for i in ids if i in self.notes}

    def get_feedback(self, fid):
        if fid not in self.notes:
            raise LookupError(fid)
        return dict(self.notes[fid])

    def insert_feedback(self, row):
        n = {"id": self._id("N"), "status": "active", "applied_count": 0, "created_at": NOW, **row}
        self.notes[n["id"]] = n
        return dict(n)

    def update_feedback(self, fid, fields):
        self.notes[fid].update(fields)
        return dict(self.notes[fid])

    def delete_feedback(self, fid):
        self.notes.pop(fid, None)

    def bump_feedback_applied(self, ids):
        for i in ids:
            self.notes[i]["applied_count"] += 1

    def count_feedback(self, status="active"):
        return sum(1 for n in self.notes.values() if n["status"] == status)

    # 대화
    def get_chat_session(self, sid):
        return self.sessions.get(sid)

    def find_chat_session(self, key):
        return next((s for s in self.sessions.values() if s.get("external_key") == key), None)

    def insert_chat_session(self, row):
        s = {"id": self._id("S"), **row}
        self.sessions[s["id"]] = s
        return dict(s)

    def update_chat_session(self, sid, fields):
        self.sessions[sid].update(fields)

    def insert_chat_turn(self, row):
        t = {"id": self._id("T"), "created_at": NOW, **row}
        self.turns.append(t)
        return t

    def list_chat_turns(self, sid, limit=200):
        return [t for t in self.turns if t["session_id"] == sid]

    def chat_event_seen(self, eid):
        return any(t.get("event_id") == eid for t in self.turns)


def good_submit(**over):
    args = dict(subject="고객 문의 응대, 20분이면 보여드릴 수 있습니다",
                subject_company="한빛몰의 새벽배송 문의, 이렇게 줄일 수 있습니다",
                subject_hook="새벽배송 확장 소식, 문의 응대 준비는 되셨나요",
                body="김가상 팀장님, 새벽배송 물류센터를 확장하신다는 소식을 봤습니다. 문의가 늘 시기라 연락드렸습니다. 20분 통화 가능하실까요?",
                hook={"type": "company", "summary": "새벽배송 물류센터 확장", "fact_ids": ["F2"]},
                fact_ids_used=["F1", "F2"])
    args.update(over)
    return call("submit_draft", **args)


@pytest.fixture
def repo():
    return MemRepo([LEAD])


# --- 업종 ---

def test_classify_industry_by_company_facts_not_news():
    assert industry.classify(LEAD, FACTS) == "retail_commerce"
    assert industry.classify({"company_name": "가나다"}, []) is None
    only_news = [{"field": "recent_news", "value": "반도체 반도체 반도체 배터리", "source_url": "u"}]
    assert industry.classify({"company_name": "가나다"}, only_news) is None


def test_sensitive_words_and_usable_items_filter():
    assert industry.is_sensitive("회원 정보 유출 의혹") and not industry.is_sensitive("물류센터 확장")
    brief = {"id": "B1", "items": [
        {"title": "새벽배송 경쟁", "summary": "x", "url": "u1", "published_at": "2026-11-25"},
        {"title": "오래된 기사", "summary": "x", "url": "u2", "published_at": "2026-09-01"},
        {"title": "대규모 해킹 사고", "summary": "x", "url": "u3", "published_at": "2026-11-26"}]}
    got = industry.usable_items(brief, today=TODAY)
    assert [i["title"] for i in got] == ["새벽배송 경쟁"] and got[0]["id"] == "ind1"


class FakeNews:
    def __init__(self):
        self.calls = 0

    def search(self, query, max_age_days=365):
        self.calls += 1
        return [{"title": "이커머스 동향", "summary": "s", "url": "https://n.example/1", "publisher": "p",
                 "published_at": "2026-11-25"}]


def test_industry_brief_is_collected_once_then_reused_for_a_week(repo):
    news = FakeNews()
    first = industry.ensure_brief(repo, "retail_commerce", news, None)
    again = industry.ensure_brief(repo, "retail_commerce", news, None)
    assert news.calls == 1 and again["id"] == first["id"]
    stale = datetime.now(timezone.utc) + timedelta(days=8)
    industry.ensure_brief(repo, "retail_commerce", news, None, now=stale)
    assert news.calls == 2


# --- 초안: 제목 후보와 소재 ---

def test_draft_returns_three_subjects_with_general_as_default_and_hook(repo):
    out = run_draft(LEAD, FACTS, None, ScriptedLLM([LLMResponse("", [good_submit()])]), MemoryRunLog(),
                    booking_url="https://book.example", today=TODAY)
    d = out["draft"]
    assert [o["id"] for o in d["subject_options"]] == ["general", "company", "hook"]
    assert d["subject"] == d["subject_options"][0]["subject"] and d["subject_choice"] == "general"
    assert "한빛몰" not in d["subject"]  # 기본은 일반 제목
    assert d["hook"] == {"type": "company", "summary": "새벽배송 물류센터 확장",
                         "source_url": "https://news.example/a", "source_title": None}
    assert d["body"].endswith(FOOTER) and "https://book.example" in d["body"]


def test_draft_rules_company_name_hook_required_sensitive_and_length(repo):
    llm = ScriptedLLM([
        LLMResponse("", [good_submit(subject_company="새벽배송 문의 이렇게 줄입니다")]),          # 회사명 빠짐
        LLMResponse("", [good_submit(hook=None)]),                                              # 후보가 있는데 hook 없음
        LLMResponse("", [good_submit(hook={"type": "company", "summary": "유출", "fact_ids": ["F3"]})]),  # 민감
        LLMResponse("", [good_submit(subject="가" * 46)]),                                       # 너무 김
        LLMResponse("", [good_submit(subject_hook=None)]),                                      # 소식 제목 누락
        LLMResponse("", [good_submit()]),
    ])
    log = MemoryRunLog()
    out = run_draft(LEAD, FACTS, None, llm, log, today=TODAY)
    errors = [s.output["error"] for s in log.steps if s.kind == "tool" and s.is_error]
    assert len(errors) == 5
    assert "회사명" in errors[0] and "hook을 제출" in errors[1] and "민감" in errors[2]
    assert "45자" in errors[3] and "subject_hook" in errors[4]
    assert out["draft"]["hook"]["type"] == "company"


def test_draft_industry_hook_cites_brief_article_and_inquiry_has_no_hook_subject(repo):
    facts = [FACTS[0]]
    llm = ScriptedLLM([LLMResponse("", [good_submit(
        hook={"type": "industry", "summary": "새벽배송 경쟁", "industry_ids": ["ind1"]}, fact_ids_used=["F1"])])])
    out = run_draft(LEAD, facts, None, llm, MemoryRunLog(), industry_label="유통·이커머스", industry_items=ITEMS, today=TODAY)
    d = out["draft"]
    assert d["hook"]["type"] == "industry" and d["hook"]["source_url"] == "https://news.example/ind"
    assert {"type": "industry", "id": "B1#ind1", "url": "https://news.example/ind", "title": "이커머스 새벽배송 경쟁 심화"} in d["citations"]

    inquiry = good_submit(hook={"type": "inquiry", "summary": "데모 요청"}, subject_hook=None, fact_ids_used=["F1"])
    out = run_draft(LEAD, facts, None, ScriptedLLM([LLMResponse("", [inquiry])]), MemoryRunLog(), today=TODAY)
    assert [o["id"] for o in out["draft"]["subject_options"]] == ["general", "company"]


def test_strip_generated_tail_removes_cta_and_footer():
    body = "본문입니다.\n\n편하신 시간을 아래 링크에서 골라 주세요.\nhttps://book.example" + FOOTER
    assert strip_generated_tail(body) == "본문입니다."


# --- 답장 응대와 통화 스크립트에도 피드백이 들어간다 ---

def test_reply_answer_and_call_script_carry_feedback(repo):
    from sdr_agent.workflows.call_script import run_call_script
    from sdr_agent.workflows.reply import run_reply_triage

    note = repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "tone", "text": "답변은 세 문장 안으로 쓴다",
                                 "created_by": "a"})
    notes = [repo.notes[note["id"]]]
    llm = ScriptedLLM([
        LLMResponse("", [call("search_kb", query="무료 체험")]),
        LLMResponse("", [call("submit_triage", classification="question", confidence=0.9, reason="체험 문의",
                              answer_body="14일 무료 체험이 있습니다.", kb_ids=["02_pricing#무료 체험"], feedback_ids=[note["id"]])]),
    ])
    out = run_reply_triage(LEAD, "체험이 가능한가요?", llm, MemoryRunLog(), feedback=notes)
    assert {"type": "feedback", "id": note["id"]} in out["answer"]["citations"]
    assert "답변은 세 문장 안으로 쓴다" in llm.seen[0][0].text

    cs = ScriptedLLM([
        LLMResponse("", [call("search_kb", query="가격 반대 의견")]),
        LLMResponse("", [call("submit_call_script", email_summary="체험 안내 메일입니다.", meeting_ask="언제쯤 가능하실까요?",
                              compelling_questions=[{"angle": "계기", "question": "계기가 있으셨을까요?", "listen_for": "사건"}] * 3,
                              objections=[], voicemail="메일 드렸습니다.", feedback_ids=[note["id"]])]),
    ])
    out = run_call_script(LEAD, FACTS, None, {"subject": "s", "body": "b"}, cs, MemoryRunLog(), feedback=notes)
    assert {"type": "feedback", "id": note["id"]} in out["script"]["citations"]


# --- draft_emails: 저장, 피드백 반영, 제목 선택 ---

def test_draft_emails_applies_feedback_and_stores_options(repo, monkeypatch):
    posted = []
    monkeypatch.setattr(jobs, "notify", lambda kind, title, text="", **kw: posted.append(kw) or kw["slack_ref_out"].update(
        {"channel": "C1", "ts": "111.1"}))
    note = repo.insert_feedback({"scope": "lead", "scope_key": "L1", "kind": "avoid", "text": "가격 이야기는 첫 메일에 쓰지 않는다",
                                 "source": "manual", "created_by": "담당자"})
    other = repo.insert_feedback({"scope": "lead", "scope_key": "L9", "kind": "tone", "text": "다른 고객 규칙",
                                  "source": "manual", "created_by": "담당자"})
    llm = ScriptedLLM([LLMResponse("", [good_submit(feedback_ids=[note["id"]])])])
    out = jobs.draft_emails(["L1"], settings=load_settings(), repo=repo, llm=llm, news_clients=(None, None))
    assert out[0]["status"] == "drafted"
    goal = llm.seen[0][0].text
    assert "가격 이야기는 첫 메일에 쓰지 않는다" in goal and "다른 고객 규칙" not in goal
    msg = repo.get_message(out[0]["message_id"])
    assert msg["subject_choice"] == "general" and len(msg["subject_options"]) == 3 and msg["hook"]["type"] == "company"
    assert {"type": "feedback", "id": note["id"]} in msg["citations"]
    assert repo.notes[note["id"]]["applied_count"] == 1 and repo.notes[other["id"]]["applied_count"] == 0
    assert msg["slack_ref"] == {"channel": "C1", "ts": "111.1"}
    assert posted[0]["data"]["subject_options"] and posted[0]["data"]["feedback_count"] == 1


def test_draft_rejects_unknown_feedback_id(repo):
    llm = ScriptedLLM([LLMResponse("", [good_submit(feedback_ids=["NOPE"])]), LLMResponse("", [good_submit()])])
    log = MemoryRunLog()
    run_draft(LEAD, FACTS, None, llm, log, today=TODAY)
    assert "승인자 피드백에 없는 id" in [s for s in log.steps if s.is_error][0].output["error"]


def make_pending(repo, monkeypatch):
    monkeypatch.setattr(jobs, "notify", lambda *a, **k: True)
    llm = ScriptedLLM([LLMResponse("", [good_submit()])])
    out = jobs.draft_emails(["L1"], settings=load_settings(), repo=repo, llm=llm, news_clients=(None, None))
    return out[0]["message_id"]


def test_choose_subject_and_approve_with_subject_option(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    sender = SandboxSender()
    jobs.choose_subject(mid, "hook", "담당자", repo=repo)
    assert repo.messages[mid]["subject_choice"] == "hook"
    with pytest.raises(ValueError):
        jobs.choose_subject(mid, "nope", "담당자", repo=repo)
    out = jobs.decide_message(mid, "approved", "담당자", subject_option="company", repo=repo, sender=sender,
                              settings=load_settings())
    assert out["status"] == "sent"
    assert sender.sent[0]["subject"].startswith("한빛몰의 새벽배송")
    assert repo.messages[mid]["subject_choice"] == "company"
    with pytest.raises(ValueError):  # 이미 처리된 메일은 제목을 못 바꾼다
        jobs.choose_subject(mid, "general", "담당자", repo=repo)


def test_edit_message_text_keeps_footer_and_records_revision(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    before = repo.messages[mid]["body"]
    jobs.edit_message_text(mid, "담당자", subject="직접 쓴 제목", body="직접 쓴 본문입니다.", repo=repo)
    m = repo.messages[mid]
    assert m["subject"] == "직접 쓴 제목" and m["subject_choice"] == "custom"
    assert m["body"].startswith("직접 쓴 본문입니다.") and FOOTER.strip().splitlines()[-1] in m["body"]
    assert m["revisions"][0]["body"] == before


# --- 피드백 메모리 ---

@pytest.mark.parametrize("text", ["연락은 kim@hanbit.example 로 한다", "담당자 010-1234-5678 로 전화한다",
                                  "수신 거부 안내는 빼 주세요", "AI 사용 고지를 생략한다", "가" * 301, "  "])
def test_feedback_sanitize_rejects_pii_legal_notice_and_bad_length(text):
    with pytest.raises(feedback.FeedbackError):
        feedback.sanitize(text)


def test_feedback_validate_scope_keys():
    assert feedback.validate_note("global", "x", None) == ("global", None, "other")
    assert feedback.validate_note("industry", "battery", "tone") == ("industry", "battery", "tone")
    for bad in (("lead", None, None), ("industry", "nope", None), ("team", None, None), ("global", None, "zzz")):
        with pytest.raises(feedback.FeedbackError):
            feedback.validate_note(*bad)


def test_select_for_orders_narrow_scope_first_and_caps(repo):
    for i in range(6):
        repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "tone", "text": f"전체{i}", "created_by": "a"})
    repo.insert_feedback({"scope": "industry", "scope_key": "retail_commerce", "kind": "topic", "text": "업종", "created_by": "a"})
    repo.insert_feedback({"scope": "lead", "scope_key": "L1", "kind": "avoid", "text": "고객", "created_by": "a"})
    repo.insert_feedback({"scope": "lead", "scope_key": "L1", "kind": "avoid", "text": "보관됨", "created_by": "a", "status": "archived"})
    picked = feedback.select_for(repo, "L1", "retail_commerce")
    assert [n["scope"] for n in picked][:2] == ["lead", "industry"] and len(picked) == 6  # 고객1+업종1+전체4
    assert "보관됨" not in [n["text"] for n in picked]
    assert feedback.select_for(object(), "L1", None) == []  # 저장소 오류에도 초안은 만든다


# --- 직접 지시 판별 ---

@pytest.mark.parametrize("text,kind,expected", [
    ("이 메일 발송해줘", "send", True), ("이 메일 승인하고 보내", "send", True), ("지금 바로 발송해", "send", True),
    ("승인 대기 목록 보여줘", "send", False), ("이 메일 보내도 될까?", "send", False), ("발송하지 마", "send", False),
    ("발송 전에 제목 짧게 바꿔줘", "send", False), ("이 메일 제목 더 짧게", "send", False),
    ("이 메일 반려해줘", "reject", True), ("반려 사유가 뭐야?", "reject", False),
    ("그 규칙 삭제해줘", "delete", True), ("규칙을 삭제하면 어떻게 돼?", "delete", False),
])
def test_explicit_command(text, kind, expected):
    assert chat.explicit_command(text, kind) is expected


# --- 대화형 에이전트 ---

def run(repo, text, responses, **kw):
    llm = ScriptedLLM(responses)
    out = chat.run_chat(text, user="이담당", repo=repo, llm=llm, settings=load_settings(), **kw)
    return out, llm


def test_chat_rewrite_and_save_feedback_in_one_turn(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    out, _ = run(repo, "이 메일 제목을 더 짧게 다시 써줘. 앞으로 이 고객 메일 제목은 30자 안으로 해줘", [
        LLMResponse("제목만 다시 씁니다", [call("rewrite_message", instruction="제목을 더 짧게", part="subject")]),
        LLMResponse("", [good_submit(subject="문의 응대 20분 데모", subject_company="한빛몰 문의 응대 20분 데모",
                                     subject_hook="새벽배송 확장, 문의 응대 데모")]),
        LLMResponse("규칙도 저장합니다", [call("save_feedback", text="제목은 30자 안으로 쓴다", scope="lead", kind="length")]),
        LLMResponse("제목을 짧게 바꾸고, 이 고객의 규칙으로 저장했습니다.", []),
    ], context={"screen": "message", "message_id": mid})
    assert out["reply"].startswith("제목을 짧게")
    assert [a["type"] for a in out["actions"]] == ["message_updated", "feedback_saved"]
    m = repo.messages[mid]
    assert m["subject"] == "문의 응대 20분 데모" and len(m["revisions"]) == 1
    note = next(iter(repo.notes.values()))
    assert note["scope"] == "lead" and note["scope_key"] == "L1" and note["source"] == "chat_web" and note["created_by"] == "이담당"
    assert [t["role"] for t in repo.turns] == ["user", "assistant"]


def test_chat_cannot_send_without_an_explicit_order(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    out, _ = run(repo, "이 메일 어때?", [
        LLMResponse("좋아 보이니 보내겠습니다", [call("send_now")]),
        LLMResponse("발송은 지시해 주시면 처리하겠습니다.", []),
    ], context={"message_id": mid})
    assert repo.messages[mid]["status"] == "pending_approval" and out["actions"] == [] and not repo.approvals


def test_chat_sends_when_user_orders_it_and_records_channel(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    out, _ = run(repo, "이 메일 발송해줘", [
        LLMResponse("", [call("send_now")]),
        LLMResponse("발송했습니다.", []),
    ], context={"message_id": mid})
    assert repo.messages[mid]["status"] == "sent" and out["actions"][0]["type"] == "message_sent"
    ap = repo.approvals[-1]
    assert ap["decision"] == "approved" and ap["channel"] == "chat_web" and ap["decided_by"] == "이담당"
    assert repo.messages[mid]["body"].endswith(FOOTER.strip().splitlines()[-1]) or FOOTER.strip().splitlines()[-1] in repo.messages[mid]["body"]


def test_chat_refuses_unknown_message_ids_and_unknown_lead(repo, monkeypatch):
    make_pending(repo, monkeypatch)
    out, _ = run(repo, "M999 발송해줘", [
        LLMResponse("", [call("send_now", message_id="M999")]),
        LLMResponse("그 메일은 찾을 수 없습니다.", []),
    ])
    assert out["actions"] == [] and not repo.approvals


def test_chat_lists_then_sends_found_message_and_caps_per_turn(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    out, _ = run(repo, "대기 중인 거 전부 발송해줘", [
        LLMResponse("", [call("list_pending_approvals")]),
        LLMResponse("", [call("send_now", message_id=mid)]),
        LLMResponse("1건 발송했습니다.", []),
    ])
    assert repo.messages[mid]["status"] == "sent"


def test_chat_feedback_rejects_legal_notice_and_remove_requires_order(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    out, _ = run(repo, "수신 거부 안내는 앞으로 빼줘", [
        LLMResponse("", [call("save_feedback", text="수신 거부 안내는 뺀다", scope="global")]),
        LLMResponse("법정 고지는 바꿀 수 없습니다.", []),
    ], context={"message_id": mid})
    assert not repo.notes and out["actions"] == []
    saved = repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "tone", "text": "정중하게 쓴다", "created_by": "a"})
    out, _ = run(repo, "정중하게 쓰는 규칙이 뭐였지?", [
        LLMResponse("", [call("remove_feedback", feedback_id=saved["id"])]),
        LLMResponse("삭제는 지시해 주시면 처리합니다.", []),
    ])
    assert repo.notes[saved["id"]]["status"] == "active"


def test_chat_slack_session_continues_in_same_thread(repo, monkeypatch):
    make_pending(repo, monkeypatch)
    first, _ = run(repo, "안녕", [LLMResponse("안녕하세요.", [])], channel="slack", external_key="slack:C1:100.1")
    second, _ = run(repo, "한 번 더", [LLMResponse("네.", [])], channel="slack", external_key="slack:C1:100.1")
    assert first["session_id"] == second["session_id"] and len(repo.sessions) == 1


# --- Slack 이벤트 ---

def event(etype="app_mention", **over):
    ev = {"type": etype, "user": "U1", "text": "<@UBOT> 대기 목록 보여줘", "channel": "C1", "ts": "200.1"}
    ev.update(over)
    return {"type": "event_callback", "event_id": over.pop("eid", None) or f"E{next(_eid)}", "event": ev,
            "authorizations": [{"user_id": "UBOT"}]}


_eid = itertools.count(1)


def test_slack_plan_filters_events(repo, monkeypatch):
    s = load_settings()
    job = slack_chat.plan(event(), settings=s, repo=repo)
    assert job and job.text == "대기 목록 보여줘" and job.denied is False
    assert slack_chat.plan(event(bot_id="B1"), settings=s, repo=repo) is None                    # 봇 자신
    assert slack_chat.plan(event("message", channel_type="channel", ts="201.1"), settings=s, repo=repo) is None  # 일반 대화
    assert slack_chat.plan(event("message", channel_type="im", text="안녕"), settings=s, repo=repo).is_dm
    dup = event()
    assert slack_chat.plan(dup, settings=s, repo=repo) and slack_chat.plan(dup, settings=s, repo=repo) is None  # 재전송
    monkeypatch.setenv("SLACK_APPROVERS", "U9")
    assert slack_chat.plan(event(), settings=load_settings(), repo=repo).denied is True


def test_slack_thread_reply_needs_known_thread_or_card(repo, monkeypatch):
    s = load_settings()
    reply = event("message", channel_type="channel", text="제목 바꿔줘", thread_ts="100.1", ts="101.1")
    assert slack_chat.plan(reply, settings=s, repo=repo) is None
    mid = make_pending(repo, monkeypatch)
    repo.messages[mid]["slack_ref"] = {"channel": "C1", "ts": "100.1"}
    assert slack_chat.plan(event("message", channel_type="channel", text="제목 바꿔줘", thread_ts="100.1", ts="101.2"),
                           settings=s, repo=repo)


class FakeSlackClient:
    def __init__(self):
        self.posted, self.updated = [], []

    def post_message(self, channel, blocks, text, thread_ts=None):
        self.posted.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True, "channel": channel, "ts": "300.1"}

    def update_message(self, channel, ts, blocks, text):
        self.updated.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})

    def user_name(self, uid):
        return "hanian"


def test_slack_process_replies_in_thread_with_card_as_context(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    repo.messages[mid]["slack_ref"] = {"channel": "C1", "ts": "100.1"}
    llm = ScriptedLLM([LLMResponse("", [call("get_message")]), LLMResponse("제목은 일반 제목입니다.", [])])
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: llm)
    job = slack_chat.Job("E1", "C1", "101.1", "100.1", "U1", "이 메일 제목이 뭐야", False)
    client = FakeSlackClient()
    slack_chat.process(job, settings=load_settings(), repo=repo, client=client)
    assert client.posted[0]["thread_ts"] == "100.1" and client.posted[0]["text"] == "작업 중"
    assert client.updated[0]["ts"] == "300.1" and "일반 제목" in client.updated[0]["text"]
    assert next(iter(repo.sessions.values()))["external_key"] == "slack:C1:100.1"


def test_slack_process_reports_error_and_denial(repo, monkeypatch):
    client = FakeSlackClient()
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("키 없음")))
    slack_chat.process(slack_chat.Job("E2", "C1", "5.1", None, "U1", "안녕", False), settings=load_settings(), repo=repo, client=client)
    assert "LLM 키" in client.updated[0]["text"]
    client = FakeSlackClient()
    slack_chat.process(slack_chat.Job("E3", "C1", "5.2", None, "U1", "안녕", False, denied=True), settings=load_settings(),
                       repo=repo, client=client)
    assert "권한이 없는" in client.posted[0]["text"] or "권한" in client.posted[0]["text"]


def test_approval_card_has_subject_select_and_hook_line():
    data = {"company": "한빛몰", "subject": "일반", "body": "b", "kind": "initial", "tags": [],
            "subject_options": [{"id": "general", "label": "일반 제목", "subject": "일반"},
                                {"id": "company", "label": "회사명 포함", "subject": "한빛몰 일반"}],
            "subject_choice": "company", "hook": {"type": "company", "summary": "새벽배송 확장"}, "feedback_count": 2}
    blocks = slack.approval_blocks(data, "M1", None)
    select = next(b["accessory"] for b in blocks if b.get("accessory"))
    assert select["action_id"] == "select_subject" and select["initial_option"]["value"] == "M1|company"
    ctx = [b for b in blocks if b["type"] == "context"][0]["elements"][0]["text"]
    assert "회사 소식" in ctx and "피드백 2건" in ctx


def test_slack_subject_select_updates_message_and_redraws_card(repo, monkeypatch):
    mid = make_pending(repo, monkeypatch)
    client = FakeSlackClient()
    client.respond = lambda url, body: client.updated.append({"respond": body})
    payload = {"type": "block_actions", "user": {"id": "U1", "username": "hanian"}, "response_url": "https://hooks.slack.com/x",
               "container": {"channel_id": "C1", "message_ts": "100.1"},
               "actions": [{"action_id": "select_subject", "selected_option": {"value": f"{mid}|hook"}}]}
    slack.handle_interaction(payload, settings=load_settings(), repo=repo, client=client)
    assert repo.messages[mid]["subject_choice"] == "hook"
    assert client.updated and client.updated[0]["respond"]["replace_original"] is True


# --- 관리 API ---

@pytest.fixture
def api(monkeypatch, repo):
    monkeypatch.setenv("JOB_TOKEN", "job-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "team-pass")
    webform._hits.clear()
    from sdr_agent.app import app, get_repo

    app.dependency_overrides[get_repo] = lambda: repo
    c = TestClient(app)
    token = c.post("/auth/login", json={"name": "이담당", "password": "team-pass"}).json()["token"]
    yield c, {"Authorization": f"Bearer {token}"}, repo
    app.dependency_overrides.clear()


def test_api_feedback_crud_and_validation(api):
    c, h, repo = api
    assert c.get("/admin/industries", headers=h).json()["industries"][0] == {"key": "semiconductor", "label": "반도체·전자부품"}
    r = c.post("/admin/feedback", json={"text": "제목은 30자 안으로", "scope": "lead", "scope_key": "L1", "kind": "length"}, headers=h)
    note = r.json()
    assert r.status_code == 200 and note["scope_label"] == "한빛몰" and note["created_by"] == "이담당" and note["source"] == "manual"
    assert c.post("/admin/feedback", json={"text": "수신 거부 안내 빼기", "scope": "global"}, headers=h).status_code == 400
    assert c.post("/admin/feedback", json={"text": "규칙", "scope": "lead", "scope_key": "NOPE"}, headers=h).status_code == 404
    assert c.post("/admin/feedback", json={"text": "규칙", "scope": "industry"}, headers=h).status_code == 400
    p = c.patch(f"/admin/feedback/{note['id']}", json={"text": "제목은 25자 안으로", "scope": "industry", "scope_key": "retail_commerce"}, headers=h)
    assert p.json()["scope_label"] == "유통·이커머스" and p.json()["text"] == "제목은 25자 안으로"
    assert c.patch(f"/admin/feedback/{note['id']}", json={"status": "archived"}, headers=h).json()["status"] == "archived"
    assert c.get("/admin/feedback", headers=h).json()["notes"] == []
    assert len(c.get("/admin/feedback?status=all", headers=h).json()["notes"]) == 1
    assert c.delete(f"/admin/feedback/{note['id']}", headers=h).json() == {"ok": True}
    assert c.delete(f"/admin/feedback/{note['id']}", headers=h).status_code == 404


def test_api_lead_feedback_lists_applicable_notes(api):
    c, h, repo = api
    repo.insert_feedback({"scope": "lead", "scope_key": "L1", "kind": "avoid", "text": "고객 규칙", "created_by": "a"})
    repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "tone", "text": "전체 규칙", "created_by": "a"})
    repo.insert_feedback({"scope": "lead", "scope_key": "L9", "kind": "avoid", "text": "남의 규칙", "created_by": "a"})
    texts = [n["text"] for n in c.get("/admin/feedback?lead_id=L1", headers=h).json()["notes"]]
    assert texts == ["고객 규칙", "전체 규칙"]


def test_api_messages_include_options_hook_and_applied_feedback_and_subject_endpoint(api, monkeypatch):
    c, h, repo = api
    note = repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "tone", "text": "정중하게", "created_by": "a"})
    monkeypatch.setattr(jobs, "notify", lambda *a, **k: True)
    llm = ScriptedLLM([LLMResponse("", [good_submit(feedback_ids=[note["id"]])])])
    mid = jobs.draft_emails(["L1"], settings=load_settings(), repo=repo, llm=llm, news_clients=(None, None))[0]["message_id"]
    m = c.get("/admin/messages", headers=h).json()["messages"][0]
    assert [o["id"] for o in m["subject_options"]] == ["general", "company", "hook"] and m["hook"]["type"] == "company"
    assert m["feedback_applied"] == [{"id": note["id"], "scope": "global", "scope_key": None, "scope_label": "전체",
                                      "kind": "tone", "text": "정중하게"}]
    r = c.post(f"/admin/messages/{mid}/subject", json={"option_id": "company"}, headers=h)
    assert r.status_code == 200 and r.json()["subject_choice"] == "company"
    assert c.post(f"/admin/messages/{mid}/subject", json={"option_id": "zzz"}, headers=h).status_code == 400
    assert c.post("/admin/messages/NOPE/subject", json={"option_id": "general"}, headers=h).status_code == 404
    assert c.get(f"/admin/leads/L1", headers=h).json()["messages"][0]["feedback_applied"][0]["text"] == "정중하게"


def test_api_chat_roundtrip_and_errors(api, monkeypatch):
    c, h, repo = api
    llm = ScriptedLLM([LLMResponse("안녕하세요. 무엇을 도와드릴까요?", [])])
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: llm)
    r = c.post("/admin/chat", json={"message": "안녕", "context": {"screen": "inbox"}}, headers=h)
    assert r.status_code == 200 and r.json()["reply"].startswith("안녕하세요") and r.json()["actions"] == []
    sid = r.json()["session_id"]
    turns = c.get(f"/admin/chat/sessions/{sid}", headers=h).json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert c.get("/admin/chat/sessions/NOPE", headers=h).status_code == 404
    assert c.post("/admin/chat", json={"message": "  "}, headers=h).status_code == 400
    assert c.post("/admin/chat", json={"message": "안녕", "session_id": "NOPE"}, headers=h).status_code == 404
    assert c.post("/admin/chat", json={"message": "안녕"}).status_code == 401
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다")))
    assert c.post("/admin/chat", json={"message": "안녕"}, headers=h).status_code == 503


def test_api_slack_events_verification_and_dispatch(api, monkeypatch):
    c, h, repo = api
    import hashlib
    import hmac
    import json
    import time

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "sig-secret")

    def post(payload, headers=None, sign=True):
        raw = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"sig-secret", f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
        hd = {"x-slack-request-timestamp": ts, "x-slack-signature": sig if sign else "v0=bad", "content-type": "application/json"}
        return c.post("/webhooks/slack/events", content=raw, headers={**hd, **(headers or {})})

    assert post({"type": "url_verification", "challenge": "abc"}).json() == {"challenge": "abc"}
    assert post({"type": "url_verification", "challenge": "abc"}, sign=False).status_code == 401
    handled = []
    monkeypatch.setattr(slack_chat, "process", lambda job, **kw: handled.append(job))
    assert post(event()).status_code == 200 and len(handled) == 1
    assert post(event(), headers={"x-slack-retry-num": "1"}).json() == {} and len(handled) == 1  # 재전송은 무시
