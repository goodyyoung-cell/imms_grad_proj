"""광고성 메일 표시(제목 (광고), 영문 수신 거부, 전송자 정보), 수신 거부 링크, 동의 게이트, 발송 상한, 승인 대기 알림."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sdr_agent import compliance, jobs
from sdr_agent.config import load_settings
from sdr_agent.workflows.draft import FOOTER
from tests.test_email_loop import LEAD, FakeRepo, good_draft_llm


def settings(**kw):
    base = dict(job_token="tok", admin_token_secret=None, public_base_url="https://sdr.example.run.app", sender_name="노바웍스",
                sender_contact="02-000-0000", slack_channel=None, slack_bot_token=None, require_consent_to_send=False,
                max_sends_per_day=50)
    return replace(load_settings(), **{**base, **kw})


def test_ad_prefix_is_added_once():
    assert compliance.ensure_ad_subject("체험 안내") == "(광고) 체험 안내"
    assert compliance.ensure_ad_subject("(광고) 체험 안내") == "(광고) 체험 안내"
    assert compliance.ensure_ad_subject("  체험 안내 ") == "(광고) 체험 안내"


def test_footer_has_english_optout_unsubscribe_link_and_sender():
    s = settings()
    footer = compliance.render_footer(s, "L1")
    assert FOOTER in footer and 'reply "unsubscribe"' in footer
    assert f"/unsubscribe/L1/{compliance.unsubscribe_token(s, 'L1')}" in footer
    assert "발신: 노바웍스 | 연락처: 02-000-0000" in footer


def test_footer_without_public_url_has_no_link():
    assert "/unsubscribe/" not in compliance.render_footer(settings(public_base_url=None), "L1")


def test_apply_footer_is_idempotent_and_survives_edits():
    s = settings()
    once = compliance.apply_footer("본문입니다.", s, "L1")
    assert compliance.apply_footer(once, s, "L1") == once
    assert once.count("이 메일은 AI의 도움을") == 1
    assert compliance.apply_footer("본문만 있습니다", s, "L1").endswith("연락처: 02-000-0000")


def test_unsubscribe_token_is_per_lead_and_secret():
    s = settings()
    t = compliance.unsubscribe_token(s, "L1")
    assert compliance.unsubscribe_token_ok(s, "L1", t)
    assert not compliance.unsubscribe_token_ok(s, "L2", t) and not compliance.unsubscribe_token_ok(s, "L1", "x")
    assert not compliance.unsubscribe_token_ok(settings(job_token="other"), "L1", t)
    assert not compliance.unsubscribe_token_ok(settings(job_token=None), "L1", t)


def test_draft_job_adds_ad_prefix_and_compliance_footer():
    repo = FakeRepo([{**LEAD, "status": "researched"}])
    out = jobs.draft_emails(["L1"], repo=repo, llm=good_draft_llm(), settings=settings())
    m = repo.messages[out[0]["message_id"]]
    assert m["subject"].startswith("(광고) ") and "unsubscribe" in m["body"] and "/unsubscribe/L1/" in m["body"]


# --- 수신 거부 링크 ---

@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "job-token")
    from sdr_agent.app import app, get_repo

    repo = FakeRepo([{**LEAD, "status": "awaiting_approval"}])
    repo.insert_message("L1", None, "initial", {"subject": "(광고) 안내", "body": "본문"})
    app.dependency_overrides[get_repo] = lambda: repo
    token = compliance.unsubscribe_token(load_settings(), "L1")
    yield TestClient(app), repo, token
    app.dependency_overrides.clear()


def test_opening_link_alone_does_not_unsubscribe(web):
    client, repo, token = web
    r = client.get(f"/unsubscribe/L1/{token}")
    assert r.status_code == 200 and "Unsubscribe" in r.text and repo.leads["L1"]["unsubscribed_at"] is None


def test_button_press_unsubscribes_and_cancels_pending_mail(web):
    client, repo, token = web
    r = client.post(f"/unsubscribe/L1/{token}")
    assert r.status_code == 200 and "Done" in r.text
    assert repo.leads["L1"]["unsubscribed_at"] and all(m["status"] == "cancelled" for m in repo.messages.values())
    assert client.post(f"/unsubscribe/L1/{token}").status_code == 200  # 두 번 눌러도 안전


def test_bad_token_is_rejected(web):
    client, repo, _ = web
    assert client.get("/unsubscribe/L1/forged").status_code == 404
    assert client.post("/unsubscribe/L1/forged").status_code == 404
    assert repo.leads["L1"]["unsubscribed_at"] is None


def test_unknown_lead_looks_the_same(web):
    client, _, _ = web
    token = compliance.unsubscribe_token(load_settings(), "nope")
    assert "Done" in client.post(f"/unsubscribe/nope/{token}").text


# --- 동의 게이트, 발송 상한 ---

def approved_repo():
    repo = FakeRepo([{**LEAD, "status": "awaiting_approval"}])
    m = repo.insert_message("L1", None, "initial", {"subject": "(광고) 안내", "body": "본문"})
    repo.messages[m["id"]]["status"] = "approved"
    return repo, m["id"]


def test_consent_gate_blocks_send_when_required():
    repo, mid = approved_repo()
    out = jobs.send_message(mid, repo=repo, settings=settings(require_consent_to_send=True))
    assert out["status"] == "cancelled" and "동의" in out["reason"] and repo.messages[mid]["status"] == "cancelled"


def test_consent_gate_allows_consented_lead_and_is_off_by_default():
    repo, mid = approved_repo()
    assert jobs.send_message(mid, repo=repo, settings=settings())["status"] == "sent"  # 기본은 샌드박스라 막지 않는다
    repo2, mid2 = approved_repo()
    repo2.leads["L1"]["consent_at"] = "2026-11-25T00:00:00+00:00"
    assert jobs.send_message(mid2, repo=repo2, settings=settings(require_consent_to_send=True))["status"] == "sent"


def test_daily_send_cap_keeps_message_approved_so_it_can_be_retried():
    repo, mid = approved_repo()
    repo.insert_message("L1", None, "followup", {"subject": "s", "body": "b"})
    next(m for m in repo.messages.values() if m["kind"] == "followup")["status"] = "sent"
    with pytest.raises(ValueError, match="한도"):
        jobs.send_message(mid, repo=repo, settings=settings(max_sends_per_day=1))
    assert repo.messages[mid]["status"] == "approved"
    assert jobs.send_message(mid, repo=repo, settings=settings(max_sends_per_day=2))["status"] == "sent"


def test_record_consent_requires_a_note_and_enables_sending():
    repo, mid = approved_repo()
    with pytest.raises(ValueError):
        jobs.record_consent("L1", "  ", "김담당", repo=repo, settings=settings())
    out = jobs.record_consent("L1", "세미나 현장 서면 동의", "김담당", repo=repo, settings=settings())
    assert "김담당" in out["consent_note"] and repo.leads["L1"]["consent_at"]
    assert jobs.send_message(mid, repo=repo, settings=settings(require_consent_to_send=True))["status"] == "sent"


def test_reply_answer_draft_gets_ad_prefix_and_footer(monkeypatch):
    repo = FakeRepo([{**LEAD, "status": "contacted"}])
    triage = {"classification": "question", "confidence": 0.9, "reason": "r", "action": "answer_draft", "by": "llm",
              "run_id": None, "answer": {"subject": "Re: 문의", "body": "답변입니다", "citations": []}}
    monkeypatch.setattr(jobs, "run_reply_triage", lambda *a, **kw: triage)
    monkeypatch.setattr(jobs, "get_llm", lambda *a, **kw: None)
    out = jobs.handle_reply("가격이 궁금합니다", lead_id="L1", repo=repo, settings=settings())
    m = repo.messages[out["message_id"]]
    assert m["subject"].startswith("(광고) ") and "/unsubscribe/L1/" in m["body"] and "발신: 노바웍스" in m["body"]


def test_edited_approval_gets_footer_with_sender_line():
    repo = FakeRepo([{**LEAD, "status": "awaiting_approval"}])
    m = repo.insert_message("L1", None, "initial", {"subject": "(광고) 안내", "body": "본문"})
    jobs.decide_message(m["id"], "edited", "담당자", edited_body="고친 본문", repo=repo, settings=settings())
    assert "발신: 노바웍스 | 연락처: 02-000-0000" in repo.messages[m["id"]]["body"]


def test_consent_endpoint_requires_login_name_and_note(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "tok")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    from sdr_agent.app import app, get_repo

    repo = FakeRepo([{**LEAD, "status": "contacted"}])
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        c = TestClient(app)
        assert c.post("/admin/leads/L1/consent", json={"note": "x"}).status_code == 401
        tok = c.post("/auth/login", json={"name": "김담당", "password": "pw"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.post("/admin/leads/L1/consent", json={"note": " "}, headers=h).status_code == 400
        assert c.post("/admin/leads/NOPE/consent", json={"note": "서면"}, headers=h).status_code == 404
        r = c.post("/admin/leads/L1/consent", json={"note": "세미나 서면 동의"}, headers=h)
        assert r.status_code == 200 and "김담당" in repo.leads["L1"]["consent_note"]
    finally:
        app.dependency_overrides.clear()


def test_unsubscribe_endpoint_is_rate_limited(web):
    from sdr_agent import webform

    client, repo, token = web
    webform._hits.clear()
    codes = [client.post(f"/unsubscribe/L1/{token}").status_code for _ in range(8)]
    assert 429 in codes
    webform._hits.clear()
