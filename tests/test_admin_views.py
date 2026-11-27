"""관리 화면 조회 API 5종: 답장, 승인 기록, 지식베이스, 상태, Slack 연결 시험."""

import pytest
from fastapi.testclient import TestClient

from sdr_agent import notify, slack, webform
from tests.test_admin_api import LEAD, NOW, AdminRepo

REPLIES = [
    {"id": "P2", "lead_id": "L1", "message_id": "M1", "body": "관심 있습니다", "received_at": NOW,
     "classification": "interested", "confidence": 0.9, "handled_by": "agent", "run_id": "R1"},
    {"id": "P1", "lead_id": "L1", "message_id": None, "body": "수신 거부", "received_at": "2026-11-25T12:00:00+00:00",
     "classification": "unsubscribe", "confidence": 0.7, "handled_by": "human", "run_id": None},
]
APPROVALS = [
    {"id": "A1", "message_id": "M1", "decision": "edited", "decided_by": "담당자A", "decided_at": NOW, "note": None,
     "reason_code": None, "original_body": "원본", "review_seconds": 20, "channel": "admin", "edited_body": "수정"},
    {"id": "A2", "message_id": "GONE", "decision": "rejected", "decided_by": "담당자B", "decided_at": NOW,
     "note": "톤", "reason_code": "tone", "original_body": None, "review_seconds": None, "channel": "slack"},
]


class ViewRepo(AdminRepo):
    calls: dict = {}

    def list_recent_replies(self, classification=None, handled_by=None, limit=100):
        self.calls["replies"] = (classification, handled_by, limit)
        return [r for r in REPLIES if (not classification or r["classification"] == classification)
                and (not handled_by or r["handled_by"] == handled_by)]

    def list_recent_approvals(self, limit=200):
        self.calls["approvals"] = limit
        return list(APPROVALS)

    def messages_by_ids(self, ids):
        self.calls["message_queries"] = self.calls.get("message_queries", 0) + 1
        return {"M1": {"id": "M1", "lead_id": "L1", "kind": "initial", "subject": "s", "body": "b", "status": "sent"}}

    def count_sent_today(self):
        return 3


class FakeSlack:
    def __init__(self, fail=False):
        self.posted, self.fail = [], fail

    def post_message(self, channel, blocks, text):
        if self.fail:
            raise slack.SlackError("channel_not_found")
        self.posted.append({"channel": channel, "blocks": blocks, "text": text})


@pytest.fixture
def client(monkeypatch):
    for k in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL", "SLACK_SIGNING_SECRET", "SLACK_APPROVERS", "ANTHROPIC_API_KEY",
              "GEMINI_API_KEY", "ADMIN_WEB_URL", "SLACK_NOTIFY_KINDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JOB_TOKEN", "job-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "team-pass")
    webform._hits.clear()
    ViewRepo.calls = {}
    from sdr_agent.app import app, get_repo

    app.dependency_overrides[get_repo] = lambda: ViewRepo()
    yield TestClient(app)
    app.dependency_overrides.clear()


def hdr(client, name="이담당"):
    token = client.post("/auth/login", json={"name": name, "password": "team-pass"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method,path", [("get", "/admin/replies"), ("get", "/admin/approvals"), ("get", "/admin/kb"),
                                         ("get", "/admin/status"), ("post", "/admin/notify-test")])
def test_new_endpoints_require_auth(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_replies_shape_filters_and_limit_cap(client):
    h = hdr(client)
    rows = client.get("/admin/replies", headers=h).json()["replies"]
    assert [r["id"] for r in rows] == ["P2", "P1"]
    assert set(rows[0]) >= {"id", "lead_id", "message_id", "body", "received_at", "classification", "confidence",
                            "handled_by", "run_id", "lead"}
    assert rows[0]["lead"] == LEAD
    only = client.get("/admin/replies?classification=unsubscribe&handled_by=human", headers=h).json()["replies"]
    assert [r["id"] for r in only] == ["P1"]
    assert ViewRepo.calls["replies"] == ("unsubscribe", "human", 100)
    client.get("/admin/replies?limit=9999", headers=h)
    assert ViewRepo.calls["replies"][2] == 200
    assert client.get("/admin/replies?classification=nope", headers=h).status_code == 400
    assert client.get("/admin/replies?handled_by=robot", headers=h).status_code == 400


def test_approvals_join_messages_without_n_plus_one(client):
    h = hdr(client)
    rows = client.get("/admin/approvals", headers=h).json()["approvals"]
    assert set(rows[0]) == {"id", "message_id", "decision", "decided_by", "decided_at", "note", "reason_code",
                            "original_body", "review_seconds", "channel", "message", "lead"}
    assert "edited_body" not in rows[0]
    assert rows[0]["message"]["subject"] == "s" and rows[0]["lead"]["name"] == "김가상"
    assert rows[0]["original_body"] == "원본" and rows[0]["review_seconds"] == 20
    assert rows[1]["message"] is None and rows[1]["lead"] is None and rows[1]["reason_code"] == "tone"
    assert ViewRepo.calls["message_queries"] == 1
    client.get("/admin/approvals?limit=99999", headers=h)
    assert ViewRepo.calls["approvals"] == 500


def test_kb_lists_docs_in_file_order_with_citation_ids(client):
    docs = client.get("/admin/kb", headers=hdr(client)).json()["docs"]
    ids = [d["id"] for d in docs]
    assert ids == sorted(ids) and "02_pricing" in ids
    pricing = next(d for d in docs if d["id"] == "02_pricing")
    assert pricing["title"] and pricing["sections"]
    sec = pricing["sections"][0]
    assert set(sec) == {"id", "heading", "text"} and sec["id"] == f"02_pricing#{sec['heading']}" and sec["text"]


def test_status_has_no_secrets(client, monkeypatch):
    secrets = {"ANTHROPIC_API_KEY": "sk-ant-SECRETVALUE", "GEMINI_API_KEY": "gem-SECRETVALUE",
               "SLACK_BOT_TOKEN": "xoxb-SECRETVALUE", "SLACK_SIGNING_SECRET": "sign-SECRETVALUE",
               "SUPABASE_SECRET_KEY": "sb_secret_SECRETVALUE"}
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SLACK_CHANNEL", "C123")
    monkeypatch.setenv("SLACK_APPROVERS", "U1,U2")
    monkeypatch.setenv("ADMIN_WEB_URL", "https://admin.example/")
    r = client.get("/admin/status", headers=hdr(client))
    assert "SECRETVALUE" not in r.text and "job-secret" not in r.text and "team-pass" not in r.text
    d = r.json()
    assert d["mode"] == {"send": "sandbox", "llm_default": "claude", "providers": {"claude": True, "gemini": True}}
    assert d["slack"]["configured"] is True and d["slack"]["signing_configured"] is True
    assert d["slack"]["channel"] == "C123" and d["slack"]["approvers"] == 2
    assert d["slack"]["web_url"] == "https://admin.example" and "approval" in d["slack"]["notify_kinds"]
    assert d["safeguards"]["sent_today"] == 3 and d["safeguards"]["max_sends_per_day"] == 50
    assert d["safeguards"]["require_consent"] is False and d["safeguards"]["sender_name"]
    assert d["knowledge_docs"] >= 1


def test_status_unconfigured(client):
    d = client.get("/admin/status", headers=hdr(client)).json()
    assert d["slack"] == {"configured": False, "signing_configured": False, "channel": None, "approvers": 0,
                          "notify_kinds": d["slack"]["notify_kinds"], "web_url": None}
    assert d["mode"]["providers"] == {"claude": False, "gemini": False}


def test_notify_test_without_slack_config(client):
    r = client.post("/admin/notify-test", headers=hdr(client))
    assert r.status_code == 200 and r.json()["posted"] is False and "Slack 설정이 없습니다" in r.json()["reason"]


def test_notify_test_posts_even_if_kind_not_enabled(client, monkeypatch):
    fake = FakeSlack()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")
    monkeypatch.setenv("SLACK_NOTIFY_KINDS", "approval")
    monkeypatch.setattr(slack, "get_client", lambda s: fake)
    r = client.post("/admin/notify-test", json={"text": "안녕"}, headers=hdr(client, "김시험"))
    assert r.json() == {"posted": True, "reason": None}
    post = fake.posted[0]
    assert post["channel"] == "C123"
    assert post["blocks"][0]["text"]["text"] == "연결 시험"
    assert "김시험" in post["blocks"][1]["text"]["text"] and "안녕" in post["blocks"][1]["text"]["text"]


def test_notify_test_reports_failure_and_rate_limits(client, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")
    monkeypatch.setattr(slack, "get_client", lambda s: FakeSlack(fail=True))
    h = hdr(client)
    first = client.post("/admin/notify-test", headers=h).json()
    assert first["posted"] is False and "실패" in first["reason"]
    codes = [client.post("/admin/notify-test", headers=h).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 429]
    assert client.post("/admin/notify-test", headers={"Authorization": "Bearer job-secret"}).status_code == 400


def test_notify_function_sends_test_kind_directly():
    from sdr_agent.config import load_settings
    from dataclasses import replace

    s = replace(load_settings(), slack_channel="C1", slack_bot_token="x", slack_notify_kinds=())
    fake = FakeSlack()
    assert notify.notify("test", "연결 시험", "본문", settings=s, client=fake) is True
    assert notify.notify("handoff", "t", settings=s, client=fake) is False
