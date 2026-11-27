"""Slack 승인 채널: 서명 검증, 메시지 구성, 알림 전송, 버튼·수정 창 처리. 실제 Slack 없이 가짜 클라이언트로 검증한다."""

import hashlib
import hmac
import json
import time
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from sdr_agent import jobs, notify, slack
from sdr_agent.config import load_settings
from sdr_agent.workflows.draft import FOOTER
from tests.test_email_loop import LEAD, FakeRepo

SECRET = "signing-secret"
DATA = {"company": "한빛몰", "name": "김가상", "title": "CS팀장", "subject": "문의 감사합니다", "body": "안녕하세요. 체험 계정을 안내드립니다.",
        "kind": "initial", "score": 62.0, "tags": ["데모 요청", "팀장·부장급"], "customer_reply": None}


class FakeSlack:
    def __init__(self, fail=False, respond_fails=False):
        self.posted, self.responses, self.modals, self.updates, self.fail = [], [], [], [], fail
        self.respond_fails = respond_fails

    def post_message(self, channel, blocks, text):
        if self.fail:
            raise slack.SlackError("channel_not_found")
        self.posted.append({"channel": channel, "blocks": blocks, "text": text})
        return {"ok": True}

    def respond(self, url, body):
        if self.respond_fails:
            raise RuntimeError("response_url expired")
        self.responses.append((url, body))

    def update_message(self, channel, ts, blocks, text):
        self.updates.append((channel, ts, blocks, text))

    def open_modal(self, trigger_id, view):
        self.modals.append((trigger_id, view))


def settings(**kw):
    base = dict(slack_bot_token="xoxb-test", slack_signing_secret=SECRET, slack_channel="C123", slack_approvers=(),
                admin_web_url="https://admin.example.app")
    return replace(load_settings(), **{**base, **kw})


def pending_repo():
    repo = FakeRepo([LEAD])
    msg = repo.insert_message("L1", None, "initial", {"subject": "문의 감사합니다", "body": "안녕하세요. 체험 계정을 안내드립니다." + FOOTER})
    return repo, msg["id"]


def click(action_id, mid, user="U1", name="alice"):
    return {"type": "block_actions", "user": {"id": user, "username": name}, "response_url": "https://hooks.slack.test/r/1",
            "trigger_id": "T1", "actions": [{"action_id": action_id, "value": mid}]}


def sign(body: bytes, ts=None, secret=SECRET):
    ts = str(int(ts if ts is not None else time.time()))
    return ts, "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()


# --- 서명 ---

def test_signature_valid_wrong_stale_and_missing():
    body = b"payload=%7B%7D"
    ts, sig = sign(body)
    assert slack.verify_signature(SECRET, ts, body, sig)
    assert not slack.verify_signature("other", ts, body, sig)
    assert not slack.verify_signature(SECRET, ts, body + b"x", sig)
    old_ts, old_sig = sign(body, ts=time.time() - 600)
    assert not slack.verify_signature(SECRET, old_ts, body, old_sig)
    assert not slack.verify_signature(None, ts, body, sig)
    assert not slack.verify_signature(SECRET, "abc", body, sig)
    assert not slack.verify_signature(SECRET, ts, body, None)


# --- 메시지 구성 ---

def buttons(blocks):
    return {e["action_id"]: e for b in blocks if b["type"] == "actions" for e in b["elements"]}


def test_approval_blocks_have_buttons_and_stay_under_slack_limits():
    blocks = slack.approval_blocks({**DATA, "body": "가" * 8000}, "M1", "https://admin.example.app")
    b = buttons(blocks)
    assert set(b) == {"approve", "edit", "reject", "open_admin"}
    assert b["approve"]["value"] == "M1" and b["approve"]["style"] == "primary"
    assert b["reject"]["style"] == "danger"
    assert b["open_admin"]["url"] == "https://admin.example.app/m/M1"
    for blk in blocks:
        txt = blk.get("text")
        if txt:
            assert len(txt["text"]) <= 3000
    body_block = next(blk for blk in blocks if blk["type"] == "section" and "제목:" in blk["text"]["text"])
    assert body_block["text"]["type"] == "plain_text"  # 본문에 <, & 가 있어도 서식으로 해석되지 않는다


def test_approval_blocks_without_base_url_have_no_link_button_and_show_reply():
    blocks = slack.approval_blocks({**DATA, "kind": "followup", "customer_reply": "가격이 궁금합니다"}, "M1", None)
    assert "open_admin" not in buttons(blocks)
    assert any("가격이 궁금합니다" in blk.get("text", {}).get("text", "") for blk in blocks)
    assert blocks[0]["text"]["text"].startswith("답변 승인 요청")


# --- 알림 전송 ---

def test_notify_posts_approval_with_buttons():
    fake = FakeSlack()
    notify.notify("approval", "t", "x", message_id="M1", lead_id="L1", data=DATA, settings=settings(), client=fake)
    assert len(fake.posted) == 1 and fake.posted[0]["channel"] == "C123"
    assert "approve" in buttons(fake.posted[0]["blocks"])


def test_notify_plain_kinds_link_to_lead_page():
    fake = FakeSlack()
    notify.notify("needs_human", "사람 확인 필요: 한빛몰", "애매한 답장", lead_id="L1", settings=settings(), client=fake)
    assert buttons(fake.posted[0]["blocks"])["open_admin"]["url"].endswith("/l/L1")


def test_notify_skips_unlisted_kinds_and_unconfigured_slack():
    fake = FakeSlack()
    notify.notify("intake", "리드 유입", settings=settings(), client=fake)  # 기본 목록에 없다
    notify.notify("approval", "t", settings=replace(settings(), slack_channel=None), client=fake)
    assert fake.posted == []


def test_notify_never_raises_when_slack_fails():
    notify.notify("approval", "t", message_id="M1", data=DATA, settings=settings(), client=FakeSlack(fail=True))


# --- 버튼 처리 ---

def run(payload, repo, fake, **kw):
    return slack.handle_interaction(payload, settings=settings(**kw), repo=repo, client=fake)


def test_approve_button_sends_records_slack_channel_and_replaces_message():
    repo, mid = pending_repo()
    fake = FakeSlack()
    assert run(click("approve", mid), repo, fake) == {}
    assert repo.messages[mid]["status"] == "sent"
    assert repo.approvals[0]["channel"] == "slack" and repo.approvals[0]["decided_by"] == "slack:alice"
    url, body = fake.responses[-1]
    assert url == "https://hooks.slack.test/r/1" and body["replace_original"] is True
    assert "승인" in body["blocks"][0]["text"]["text"] and not any(b["type"] == "actions" for b in body["blocks"])


def test_reject_button_opens_reason_modal_without_deciding():
    repo, mid = pending_repo()
    fake = FakeSlack()
    run(click("reject", mid), repo, fake)
    _, view = fake.modals[0]
    assert view["callback_id"] == "reject_reason" and repo.messages[mid]["status"] == "pending_approval"
    options = view["blocks"][1]["element"]["options"]
    assert {o["value"] for o in options} == {"factual_error", "tone", "wrong_target", "forbidden_phrase", "other"}


def reject_submit(mid, reason, note=None, **meta):
    values = {"reason_block": {"reason": {"selected_option": {"value": reason} if reason else None}},
              "note_block": {"note": {"value": note}}}
    return {"type": "view_submission", "user": {"id": "U1", "username": "alice"},
            "view": {"callback_id": "reject_reason", "private_metadata": json.dumps({"message_id": mid, **meta}),
                     "state": {"values": values}}}


def test_reject_modal_submit_records_reason_and_replaces_message():
    repo, mid = pending_repo()
    fake = FakeSlack()
    out = run(reject_submit(mid, "factual_error", "매출 숫자가 다릅니다", response_url="https://hooks.slack.test/r/1"), repo, fake)
    assert out == {"response_action": "clear"}
    a = repo.approvals[0]
    assert (a["decision"], a["reason_code"], a["channel"], a["note"]) == ("rejected", "factual_error", "slack", "매출 숫자가 다릅니다")
    assert repo.messages[mid]["status"] == "rejected"
    assert "반려" in fake.responses[-1][1]["blocks"][0]["text"]["text"]


def test_reject_modal_requires_a_reason():
    repo, mid = pending_repo()
    out = run(reject_submit(mid, None), repo, FakeSlack())
    assert out["response_action"] == "errors" and repo.approvals == []


def test_falls_back_to_chat_update_when_response_url_is_dead():
    repo, mid = pending_repo()
    fake = FakeSlack(respond_fails=True)
    run(reject_submit(mid, "tone", response_url="https://hooks.slack.test/r/expired", channel="C123", ts="1700000000.000100"), repo, fake)
    assert fake.updates and fake.updates[0][:2] == ("C123", "1700000000.000100")
    assert repo.messages[mid]["status"] == "rejected"


def test_unauthorized_user_cannot_decide():
    repo, mid = pending_repo()
    fake = FakeSlack()
    run(click("approve", mid, user="U9"), repo, fake, slack_approvers=("U1",))
    assert repo.messages[mid]["status"] == "pending_approval" and repo.approvals == []
    assert fake.responses[-1][1]["response_type"] == "ephemeral"


def test_double_click_is_reported_not_crashing():
    repo, mid = pending_repo()
    fake = FakeSlack()
    run(click("approve", mid), repo, fake)
    run(click("approve", mid), repo, fake)  # Slack 재시도나 두 번 누름
    assert len(repo.approvals) == 1
    assert "처리하지 못했습니다" in fake.responses[-1][1]["text"]


def test_unsubscribed_lead_is_cancelled_not_sent():
    repo, mid = pending_repo()
    repo.leads["L1"]["unsubscribed_at"] = "2026-11-25T00:00:00+00:00"
    fake = FakeSlack()
    run(click("approve", mid), repo, fake)
    assert repo.messages[mid]["status"] == "cancelled"


def test_edit_button_opens_modal_with_body():
    repo, mid = pending_repo()
    fake = FakeSlack()
    run(click("edit", mid), repo, fake)
    trigger, view = fake.modals[0]
    assert trigger == "T1" and view["callback_id"] == "edit_approve"
    assert view["blocks"][1]["element"]["initial_value"].startswith("안녕하세요")
    assert json.loads(view["private_metadata"])["message_id"] == mid


def test_edit_modal_submit_sends_edited_body_and_keeps_footer():
    repo, mid = pending_repo()
    fake = FakeSlack()
    meta = json.dumps({"message_id": mid, "response_url": "https://hooks.slack.test/r/1"})
    payload = {"type": "view_submission", "user": {"id": "U1", "username": "alice"},
               "view": {"callback_id": "edit_approve", "private_metadata": meta,
                        "state": {"values": {"body_block": {"body": {"value": "안녕하세요. 수정한 본문입니다."}}}}}}
    assert run(payload, repo, fake) == {"response_action": "clear"}
    m = repo.messages[mid]
    assert m["status"] == "sent" and "수정한 본문" in m["body"] and FOOTER.strip() in m["body"]
    assert repo.approvals[0]["decision"] == "edited" and repo.approvals[0]["channel"] == "slack"


def test_edit_modal_empty_body_returns_validation_error():
    repo, mid = pending_repo()
    payload = {"type": "view_submission", "user": {"id": "U1"},
               "view": {"callback_id": "edit_approve", "private_metadata": json.dumps({"message_id": mid}),
                        "state": {"values": {"body_block": {"body": {"value": "  "}}}}}}
    out = run(payload, repo, FakeSlack())
    assert out["response_action"] == "errors" and repo.messages[mid]["status"] == "pending_approval"


def test_url_button_click_is_ignored():
    repo, mid = pending_repo()
    assert run(click("open_admin", mid), repo, FakeSlack()) == {}
    assert repo.messages[mid]["status"] == "pending_approval"


# --- 웹 경로 ---

@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("JOB_TOKEN", "tok")
    from sdr_agent.app import app, get_repo

    repo, mid = pending_repo()
    app.dependency_overrides[get_repo] = lambda: repo
    yield TestClient(app), repo, mid
    app.dependency_overrides.clear()


def post_interaction(client, payload, **headers):
    body = urlencode({"payload": json.dumps(payload)}).encode()
    ts, sig = sign(body)
    h = {"x-slack-request-timestamp": ts, "x-slack-signature": sig, "content-type": "application/x-www-form-urlencoded"}
    h.update(headers)
    return client.post("/webhooks/slack/interactions", content=body, headers=h)


def test_endpoint_rejects_bad_signature(web):
    client, repo, mid = web
    r = post_interaction(client, click("approve", mid), **{"x-slack-signature": "v0=bad"})
    assert r.status_code == 401 and repo.messages[mid]["status"] == "pending_approval"


def test_endpoint_accepts_signed_click(web):
    client, repo, mid = web
    r = post_interaction(client, click("approve", mid))
    assert r.status_code == 200 and repo.messages[mid]["status"] == "sent"


def test_endpoint_needs_signing_secret(web, monkeypatch):
    client, _, mid = web
    monkeypatch.delenv("SLACK_SIGNING_SECRET")
    assert post_interaction(client, click("approve", mid)).status_code == 503


# --- 클라이언트: 한도(429)와 만료된 response_url ---

class FakeResp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data or {"ok": True}, headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0)


def test_client_retries_once_after_rate_limit(monkeypatch):
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    http = FakeHttp(FakeResp(429, headers={"Retry-After": "2"}), FakeResp(200, {"ok": True, "ts": "1.2"}))
    out = slack.SlackClient("xoxb-x", http=http).post_message("C1", [], "t")
    assert out["ts"] == "1.2" and len(http.calls) == 2
    assert http.calls[0][1]["headers"]["Authorization"] == "Bearer xoxb-x"


def test_client_raises_slack_error_on_api_failure():
    with pytest.raises(slack.SlackError, match="not_in_channel"):
        slack.SlackClient("t", http=FakeHttp(FakeResp(200, {"ok": False, "error": "not_in_channel"}))).post_message("C1", [], "t")


def test_respond_raises_when_response_url_is_rejected():
    with pytest.raises(RuntimeError):
        slack.SlackClient("t", http=FakeHttp(FakeResp(404))).respond("https://hooks.slack.test/x", {})


def test_approval_blocks_warn_when_lead_has_no_consent():
    blocks = slack.approval_blocks({**DATA, "no_consent": True}, "M1", None)
    assert any("수신 동의 기록이 없습니다" in e["text"] for b in blocks if b["type"] == "context" for e in b["elements"])
    assert not any("수신 동의" in e["text"] for b in slack.approval_blocks(DATA, "M1", None) if b["type"] == "context" for e in b["elements"])


# --- 딥링크·전화 시각 알림·승인 대기 재알림 ---

def test_notify_deep_links_for_call_and_home():
    fake = FakeSlack()
    notify.notify("call_due", "전화할 시간입니다", "x", lead_id="L1", call_id="C9", settings=settings(), client=fake)
    notify.notify("reminder", "승인 대기 2건", "x", settings=settings(), client=fake)
    assert buttons(fake.posted[0]["blocks"])["open_admin"]["url"] == "https://admin.example.app/c/C9"
    assert buttons(fake.posted[1]["blocks"])["open_admin"]["url"] == "https://admin.example.app/"


def test_notify_without_admin_web_url_has_no_link_button():
    fake = FakeSlack()
    notify.notify("needs_human", "t", "x", lead_id="L1", settings=settings(admin_web_url=None), client=fake)
    assert "open_admin" not in buttons(fake.posted[0]["blocks"])


def test_notify_keeps_old_positional_signature():
    assert notify.notify("intake", "제목", "내용") is False  # 설정이 없어도 예외 없이 로그만 남긴다


def due_repo(due_at="2026-11-27T00:00:00+00:00"):
    repo, mid = pending_repo()
    task = repo.insert_call_task({"lead_id": "L1", "message_id": mid, "script": {"email_summary": "체험 안내 메일", "notices": []},
                                  "due_at": due_at})
    return repo, task["id"]


def test_notify_due_calls_announces_once_per_call():
    repo, cid = due_repo()
    fake = FakeSlack()
    s = settings()
    assert jobs.notify_due_calls(settings=s, repo=repo, client=fake) == {"due": 1, "announced": 1}
    assert jobs.notify_due_calls(settings=s, repo=repo, client=fake) == {"due": 1, "announced": 0}  # 두 번째는 조용
    assert len(fake.posted) == 1
    blocks = fake.posted[0]["blocks"]
    assert "한빛몰" in blocks[0]["text"]["text"] and "체험 안내 메일" in blocks[1]["text"]["text"]
    assert buttons(blocks)["open_admin"]["url"] == f"https://admin.example.app/c/{cid}"
    assert repo.calls[cid]["script"]["due_notified_at"]


def test_notify_due_calls_skips_future_and_retries_when_slack_is_off():
    repo, cid = due_repo(due_at="2999-01-01T00:00:00+00:00")
    fake = FakeSlack()
    assert jobs.notify_due_calls(settings=settings(), repo=repo, client=fake)["due"] == 0
    repo2, cid2 = due_repo()
    off = settings(slack_channel=None)
    assert jobs.notify_due_calls(settings=off, repo=repo2, client=fake)["announced"] == 0
    assert "due_notified_at" not in repo2.calls[cid2]["script"]  # 다음 실행에서 다시 시도
    assert jobs.notify_due_calls(settings=settings(), repo=repo2, client=fake)["announced"] == 1


def test_notify_due_calls_ignores_done_calls():
    repo, cid = due_repo()
    repo.calls[cid]["status"] = "done"
    assert jobs.notify_due_calls(settings=settings(), repo=repo, client=FakeSlack())["due"] == 0


def test_remind_pending_only_reports_stale_approvals():
    repo, mid = pending_repo()
    fake = FakeSlack()
    repo.messages[mid]["created_at"] = "2026-01-01T00:00:00+00:00"
    assert jobs.remind_pending(hours=4, settings=settings(), repo=repo, client=fake) == {"pending_over_limit": 1}
    assert "승인 대기 알림" in fake.posted[0]["blocks"][0]["text"]["text"] and "한빛몰" in fake.posted[0]["blocks"][1]["text"]["text"]
    fresh, _ = pending_repo()
    fake2 = FakeSlack()
    assert jobs.remind_pending(hours=4, settings=settings(), repo=fresh, client=fake2) == {"pending_over_limit": 0}
    assert fake2.posted == []


def test_job_endpoints_require_bearer(web):
    client, repo, mid = web
    assert client.post("/jobs/notify-due-calls").status_code == 401
    assert client.post("/jobs/remind").status_code == 401
    h = {"Authorization": "Bearer tok"}
    assert client.post("/jobs/notify-due-calls", headers=h).json() == {"due": 0, "announced": 0}
    assert client.post("/jobs/remind", headers=h, json={"hours": 4}).json() == {"pending_over_limit": 0}
    assert client.post("/jobs/remind", headers=h).status_code == 200


def test_slack_route_works_without_bearer_and_cors_is_unchanged(web):
    client, repo, mid = web
    assert post_interaction(client, click("open_admin", mid)).status_code == 200  # Bearer 없이 서명만으로
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/auth/login", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_draft_and_reply_answer_requests_carry_structured_data(monkeypatch):
    seen = []
    monkeypatch.setattr(jobs, "notify", lambda kind, title, text="", **kw: seen.append((kind, kw)))
    repo, mid = pending_repo()
    msg = repo.get_message(mid)
    data = jobs._approval_data(repo.get_lead("L1"), msg, {"score": 62, "tags": ["a"]}, customer_reply="가격이 궁금합니다")
    assert data["company"] == "한빛몰" and data["no_consent"] is True and data["customer_reply"] == "가격이 궁금합니다"
    assert set(data) == {"company", "name", "title", "subject", "body", "kind", "score", "tags", "customer_reply", "no_consent"}


def test_reject_reason_code_is_passed_through_and_validated():
    repo, mid = pending_repo()
    with pytest.raises(ValueError):
        jobs.decide_message(mid, "rejected", "a", reason_code="nope", repo=repo, settings=settings())
    jobs.decide_message(mid, "rejected", "a", reason_code="tone", review_seconds=45, repo=repo, settings=settings())
    a = repo.approvals[0]
    assert a["reason_code"] == "tone" and a["review_seconds"] == 45 and "original_body" not in a


def test_edited_approval_stores_original_and_plain_approval_has_no_extra_keys():
    repo, mid = pending_repo()
    original = repo.get_message(mid)["body"]
    jobs.decide_message(mid, "edited", "a", edited_body="고친 본문", repo=repo, settings=settings())
    assert repo.approvals[0]["original_body"] == original and "reason_code" not in repo.approvals[0]
    repo2, mid2 = pending_repo()
    jobs.decide_message(mid2, "approved", "a", repo=repo2, settings=settings())
    assert not ({"reason_code", "original_body", "review_seconds"} & set(repo2.approvals[0]))
