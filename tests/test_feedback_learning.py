"""피드백에서 배우기: 수정·반려·대화형 피드백 → 작성 지침 → 다음 초안 반영 (가짜 LLM·가짜 DB)."""

import itertools

import pytest
from fastapi.testclient import TestClient

from sdr_agent import jobs
from sdr_agent.agent import MemoryRunLog
from sdr_agent.llm.base import LLMResponse, ToolCall
from sdr_agent.outbox import SandboxSender
from sdr_agent.workflows.feedback import run_extract_lessons
from tests.test_email_loop import LEAD, FakeRepo, good_draft_llm
from tests.test_research_agent import ScriptedLLM


def call(name, **args):
    return ToolCall(f"c-{name}", name, args)


def lessons_llm(*texts):
    return ScriptedLLM([LLMResponse("수정 내용을 비교해 지침을 뽑습니다", [call("submit_lessons", lessons=[
        {"text": t, "why": "담당자 수정"} for t in texts])])])


class LearningRepo(FakeRepo):
    def __init__(self, leads):
        super().__init__(leads)
        self.lessons, self.revisions = {}, []
        self._lid = itertools.count(1)

    def list_lessons(self, status="active", scope="draft", limit=50):
        rows = [dict(l) for l in self.lessons.values() if status is None or l["status"] == status]
        return list(reversed(rows))[:limit]

    def insert_lessons(self, rows):
        out = []
        for r in rows:
            l = {"id": f"K{next(self._lid)}", "status": "active", **r}
            self.lessons[l["id"]] = l
            out.append(dict(l))
        return out

    def update_lesson(self, lesson_id, fields):
        if lesson_id not in self.lessons:
            raise LookupError(lesson_id)
        self.lessons[lesson_id].update(fields)
        return dict(self.lessons[lesson_id])

    def insert_revision(self, row):
        r = {"id": f"V{len(self.revisions) + 1}", **row}
        self.revisions.append(r)
        return r

    def list_revisions(self, message_id):
        return [r for r in self.revisions if r["message_id"] == message_id]

    def insert_message(self, lead_id, run_id, kind, draft):
        m = super().insert_message(lead_id, run_id, kind, draft)
        self.messages[m["id"]]["applied_lessons"] = draft.get("applied_lessons", [])
        return dict(self.messages[m["id"]])


@pytest.fixture
def repo():
    return LearningRepo([LEAD])


def drafted(repo):
    [r] = jobs.draft_emails(repo=repo, llm=good_draft_llm())
    return r["message_id"]


# --- 지침 추출 규칙 ---

def test_extract_keeps_general_lessons_and_drops_bad_ones():
    llm = lessons_llm(
        "첫 메일 본문은 5문장 이내로 쓴다",                 # 남김
        "한빛몰에는 성수기 이야기를 먼저 쓴다",              # 회사명 → 버림
        "할인을 약속해서 관심을 끈다",                      # 근거 없는 약속 → 버림
    )
    out = run_extract_lessons("edit", llm, MemoryRunLog(), before="긴 초안", after="짧은 초안",
                              existing=[], banned_terms=["한빛몰", "김가상"])
    assert [l["text"] for l in out["lessons"]] == ["첫 메일 본문은 5문장 이내로 쓴다"]
    assert {d["reason"] for d in out["dropped"]} == {"특정 회사·사람 이름이 들어감", "근거 없는 약속을 지시함"}


def test_extract_skips_duplicates_and_strips_footer_from_comparison():
    llm = lessons_llm("첫 메일 본문은 5문장 이내로 쓴다.")
    body = "본문입니다.\n\n---\n이 메일은 AI의 도움을 받아 작성되었고"
    out = run_extract_lessons("edit", llm, MemoryRunLog(), before=body, after="고친 본문",
                              existing=["첫 메일 본문은 5문장 이내로 쓴다"])
    assert out["lessons"] == [] and out["dropped"][0]["reason"] == "기존 지침과 같음"
    goal = llm.seen[0][0].text
    assert "본문입니다." in goal and "AI의 도움" not in goal  # 코드가 붙인 안내는 사람이 바꾼 게 아니다


# --- 수정 승인·반려에서 배우기 ---

def test_edited_approval_saves_lessons_from_human_edit(repo):
    mid = drafted(repo)
    original = repo.messages[mid]["body"]
    llm = lessons_llm("첫 메일에서는 가격·체험 조건을 언급하지 않는다")
    out = jobs.decide_message(mid, "edited", "담당자A", edited_body="김가상 팀장님, 20분 통화 가능하실까요?",
                              note="가격 이야기는 빼 주세요", repo=repo, sender=SandboxSender(), llm=llm)
    assert out["status"] == "sent" and out["lessons"]["status"] == "ok"
    [lesson] = repo.lessons.values()
    assert lesson["source"] == "edit" and lesson["created_by"] == "담당자A" and lesson["message_id"] == mid
    assert lesson["evidence"] == "가격 이야기는 빼 주세요"
    goal = llm.seen[0][0].text
    assert original.split("\n")[0] in goal and "20분 통화 가능하실까요" in goal  # 수정 전후를 함께 봤다


def test_rejection_with_reason_teaches_but_wrong_target_alone_does_not(repo):
    mid = drafted(repo)
    out = jobs.decide_message(mid, "rejected", "담당자A", reason_code="tone", note="너무 영업적임",
                              repo=repo, llm=lessons_llm("과장 없이 담백한 어조로 쓴다"))
    assert out["lessons"]["saved"][0]["text"] == "과장 없이 담백한 어조로 쓴다"
    assert list(repo.lessons.values())[0]["evidence"] == "어조가 맞지 않음: 너무 영업적임"

    repo2 = LearningRepo([LEAD])
    mid2 = drafted(repo2)
    out2 = jobs.decide_message(mid2, "rejected", "담당자A", reason_code="wrong_target", repo=repo2, llm=lessons_llm("x"))
    assert out2["lessons"]["status"] == "skipped" and repo2.lessons == {}


def test_plain_approval_and_reason_less_rejection_learn_nothing(repo):
    mid = drafted(repo)
    out = jobs.decide_message(mid, "approved", "담당자A", repo=repo, sender=SandboxSender())
    assert "lessons" not in out


def test_learning_failure_never_blocks_approval(repo):
    class Boom:
        provider, model = "fake", "boom"

        def chat(self, *a, **k):
            raise RuntimeError("LLM 장애")

    mid = drafted(repo)
    out = jobs.decide_message(mid, "edited", "담당자A", edited_body="고친 본문", repo=repo, sender=SandboxSender(), llm=Boom())
    assert out["status"] == "sent" and out["lessons"]["status"] == "failed"


def test_slack_decisions_learn_in_background(repo, monkeypatch):
    started = []
    monkeypatch.setattr("threading.Thread.start", lambda self: started.append(self))
    mid = drafted(repo)
    out = jobs.decide_message(mid, "edited", "slack:kim", channel="slack", edited_body="고친 본문", note="Slack에서 결정",
                              repo=repo, sender=SandboxSender(), llm=lessons_llm("x"))
    assert out["lessons"] == {"status": "queued"} and len(started) == 1
    assert started[0]._kwargs["feedback"] is None  # 기본 메모는 피드백이 아니다


# --- 반영 ---

def test_next_draft_uses_active_lessons_and_records_them(repo):
    repo.insert_lessons([{"text": "본문은 5문장 이내로 쓴다", "scope": "draft", "source": "edit", "created_by": "A"},
                         {"text": "꺼진 지침", "scope": "draft", "source": "edit", "created_by": "A"}])
    repo.update_lesson("K2", {"status": "disabled"})
    llm = good_draft_llm()
    [r] = jobs.draft_emails(repo=repo, llm=llm)
    goal = llm.seen[0][0].text
    assert "담당자 작성 지침" in goal and "본문은 5문장 이내로 쓴다" in goal and "꺼진 지침" not in goal
    assert repo.messages[r["message_id"]]["applied_lessons"] == [{"id": "K1", "text": "본문은 5문장 이내로 쓴다"}]


def test_no_lessons_means_no_lesson_block(repo):
    llm = good_draft_llm()
    jobs.draft_emails(repo=repo, llm=llm)
    assert "담당자 작성 지침" not in llm.seen[0][0].text


# --- 대화형 피드백 ---

def revise_llm():
    return ScriptedLLM([
        LLMResponse("피드백대로 짧게 다시 씁니다", [call("submit_draft", subject="한빛몰 문의 응대 관련 짧은 제안",
                                                   body="김가상 팀장님, 20분 통화 가능하실까요?", fact_ids_used=["F1"])]),
    ])


def test_revise_rewrites_draft_keeps_it_pending_and_records_revision(repo):
    mid = drafted(repo)
    before = repo.messages[mid]["body"]
    llm = revise_llm()
    out = jobs.revise_message(mid, "더 짧게, 가격 얘기는 빼 줘", "담당자A", repo=repo, llm=llm)
    m = repo.messages[mid]
    assert out["status"] == "revised" and m["status"] == "pending_approval"  # 발송하지 않는다
    assert m["subject"] == "한빛몰 문의 응대 관련 짧은 제안" and m["body"].startswith("김가상 팀장님, 20분")
    assert "수신 거부" in m["body"]  # 다시 써도 안내는 코드가 붙인다
    [rev] = repo.revisions
    assert rev["feedback"] == "더 짧게, 가격 얘기는 빼 줘" and rev["before_body"] == before and rev["requested_by"] == "담당자A"
    goal = llm.seen[0][0].text
    assert "담당자 피드백: 더 짧게, 가격 얘기는 빼 줘" in goal and "이전 초안 본문" in goal
    assert "AI의 도움" not in goal.split("이전 초안 본문")[1]  # 이전 초안에서 안내문은 빼고 준다
    assert out.get("lessons") is None and repo.lessons == {}


def test_revise_can_save_feedback_as_lesson(repo):
    mid = drafted(repo)
    llm = ScriptedLLM(revise_llm().responses + lessons_llm("첫 메일에서는 가격을 언급하지 않는다").responses)
    out = jobs.revise_message(mid, "가격 얘기는 앞으로도 빼 줘", "담당자A", save_as_lesson=True, repo=repo, llm=llm)
    assert out["lessons"]["saved"][0]["text"] == "첫 메일에서는 가격을 언급하지 않는다"
    assert list(repo.lessons.values())[0]["source"] == "chat"


@pytest.mark.parametrize("feedback,error", [("", "입력"), ("x" * 2001, "2,000자")])
def test_revise_validates_feedback(repo, feedback, error):
    mid = drafted(repo)
    with pytest.raises(ValueError, match=error):
        jobs.revise_message(mid, feedback, "A", repo=repo, llm=revise_llm())


def test_revise_only_pending_initial_drafts(repo):
    mid = drafted(repo)
    jobs.decide_message(mid, "approved", "A", repo=repo, sender=SandboxSender())
    with pytest.raises(ValueError, match="승인 대기"):
        jobs.revise_message(mid, "짧게", "A", repo=repo, llm=revise_llm())
    repo.messages["MF"] = {"id": "MF", "lead_id": "L1", "kind": "followup", "status": "pending_approval",
                           "subject": "답변", "body": "본문", "citations": []}
    with pytest.raises(ValueError, match="첫 메일"):
        jobs.revise_message("MF", "짧게", "A", repo=repo, llm=revise_llm())


def test_lesson_status_toggle(repo):
    repo.insert_lessons([{"text": "본문은 5문장 이내로 쓴다", "scope": "draft", "source": "edit", "created_by": "A"}])
    assert jobs.set_lesson_status("K1", "disabled", repo=repo)["status"] == "disabled"
    with pytest.raises(ValueError):
        jobs.set_lesson_status("K1", "deleted", repo=repo)


# --- API ---

def test_feedback_api(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "job-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "team-pass")
    from sdr_agent import webform
    from sdr_agent.app import app, get_repo

    webform._hits.clear()
    seen = {}
    monkeypatch.setattr(jobs, "revise_message", lambda mid, fb, by, **kw: seen.update(mid=mid, fb=fb, by=by, **kw) or {"status": "revised"})
    monkeypatch.setattr(jobs, "set_lesson_status", lambda lid, st: {"id": lid, "status": st})
    repo = LearningRepo([LEAD])
    repo.insert_lessons([{"text": "본문은 5문장 이내로 쓴다", "scope": "draft", "source": "chat", "created_by": "A"}])
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        c = TestClient(app)
        tok = c.post("/auth/login", json={"name": "이담당", "password": "team-pass"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.post("/admin/messages/M1/revise", json={"feedback": "짧게"}).status_code == 401
        r = c.post("/admin/messages/M1/revise", json={"feedback": "짧게", "save_as_lesson": True}, headers=h)
        assert r.status_code == 200 and seen == {"mid": "M1", "fb": "짧게", "by": "이담당", "save_as_lesson": True}
        assert c.get("/admin/lessons", headers=h).json()["lessons"][0]["text"] == "본문은 5문장 이내로 쓴다"
        assert c.post("/admin/lessons/K1/status", json={"status": "disabled"}, headers=h).json()["status"] == "disabled"
        assert c.get("/admin/messages/M1/revisions", headers=h).json() == {"revisions": []}
    finally:
        app.dependency_overrides.clear()
