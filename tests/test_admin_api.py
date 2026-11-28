"""관리 화면용 API: 로그인 토큰, CORS, 조회, 로그인 이름으로 결정 기록."""


import pytest
from fastapi.testclient import TestClient

from sdr_agent import auth as admin_auth
from sdr_agent import jobs, webform

NOW = "2026-11-26T12:00:00+00:00"
LEAD = {"id": "L1", "name": "김가상", "company_name": "한빛몰", "title": "팀장", "status": "contacted", "email": "k@h.example",
        "phone": "010-0000-0001", "channel_count": 1}
SCRIPT = {"phone": "010-0000-0001", "compliance": {"permission": "p", "recording": "r", "stop": "s", "no_promises": "n"},
          "opening": "o", "if_not_received": "i", "email_summary": "e", "talking_points": [], "meeting_ask": "m",
          "compelling_questions": [{"angle": "계기", "question": "q", "listen_for": "l"}], "objections": [],
          "voicemail": "v", "record_fields": ["a"], "outcomes": ["interested"], "notices": []}


class AdminRepo:
    def status_counts(self):
        return {"new": 97, "contacted": 2, "awaiting_approval": 1}

    def list_messages(self, lead_id=None, status=None, limit=50):
        return [{"id": "M1", "lead_id": "L1", "status": status or "sent", "subject": "s"}]

    def list_call_tasks(self, lead_id=None, status=None, due_before=None, limit=100):
        return [{"id": "C1", "lead_id": "L1", "status": "pending", "due_at": NOW, "script": SCRIPT}]

    def list_runs(self, lead_id=None, workflow=None, limit=50):
        return [{"id": "R1", "lead_id": "L1", "workflow": workflow or "research", "status": "succeeded"}]

    def leads_by_ids(self, ids):
        return {"L1": LEAD}

    def list_leads(self, status=None, q=None, limit=50, offset=0):
        return [dict(LEAD)], 1

    def latest_scores(self, ids):
        return {"L1": {"score": 68.0, "tags": ["데모 요청"]}}

    def get_lead(self, lead_id):
        if lead_id != "L1":
            raise LookupError(lead_id)
        return dict(LEAD)

    def merged_leads(self, lead_id):
        return []

    def list_facts(self, lead_id):
        return [{"field": "summary", "value": "쇼핑몰", "source_url": "https://h.example"}]

    def latest_score(self, lead_id):
        return {"score": 68.0, "tags": ["데모 요청"]}

    def list_replies(self, lead_id):
        return []

    def list_handoffs(self, lead_id):
        return []

    timings: list = []

    def list_all_approvals(self):
        return [{"message_id": "M1", "decision": "approved", "decided_at": NOW, "channel": "admin"}]

    def list_all_replies(self):
        return []

    def list_all_leads(self):
        return [{"id": "L1", "created_at": NOW}]

    def list_manual_timings(self):
        return list(self.timings)

    def insert_manual_timing(self, row):
        self.timings.append({"id": "T1", "created_at": NOW, **row})
        return self.timings[-1]

    def get_run(self, run_id):
        return {"id": run_id, "steps": [{"step_no": 1, "kind": "llm", "text": "홈페이지부터 봅니다"}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "job-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "team-pass")
    webform._hits.clear()
    from sdr_agent.app import app, get_repo

    app.dependency_overrides[get_repo] = lambda: AdminRepo()
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client, name="담당자A", password="team-pass"):
    return client.post("/auth/login", json={"name": name, "password": password})


def test_token_sign_verify_expiry_and_tamper():
    key = admin_auth.signing_key(None, "job-secret")
    token, exp = admin_auth.issue("담당자A", key, now=1000, ttl=60)
    assert admin_auth.verify(token, key, now=1050) == "담당자A"
    assert admin_auth.verify(token, key, now=1061) is None                       # 만료
    assert admin_auth.verify(token[:-2] + "xx", key, now=1050) is None           # 서명 변조
    assert admin_auth.verify(token, admin_auth.signing_key(None, "other"), now=1050) is None  # 다른 키


def test_login_and_admin_token_works_but_wrong_password_does_not(client):
    assert login(client, password="wrong").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json()["token"].startswith("adm.") and r.json()["name"] == "담당자A"
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/admin/me", headers=h).json() == {"name": "담당자A", "kind": "admin"}
    assert client.get("/admin/summary", headers=h).status_code == 200
    assert client.get("/admin/summary").status_code == 401
    assert client.get("/admin/summary", headers={"Authorization": "Bearer adm.garbage"}).status_code == 401
    # 예약 실행·CLI용 JOB_TOKEN도 그대로 된다
    assert client.get("/admin/me", headers={"Authorization": "Bearer job-secret"}).json()["kind"] == "system"


def test_login_disabled_without_password(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    assert login(client).status_code == 503


def test_login_is_rate_limited(client):
    codes = [login(client, password="wrong").status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_cors_allows_local_dev_origin_only(client):
    pre = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/admin/summary", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/admin/summary", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers


def test_read_endpoints_shapes(client):
    h = {"Authorization": f"Bearer {login(client).json()['token']}"}
    s = client.get("/admin/summary", headers=h).json()
    assert s["lead_status_counts"]["new"] == 97 and s["calls_due"] == 1 and s["recent_runs"][0]["lead"]["company_name"] == "한빛몰"
    leads = client.get("/admin/leads?status=contacted", headers=h).json()
    assert leads["total"] == 1 and leads["leads"][0]["score"] == 68.0 and leads["leads"][0]["tags"] == ["데모 요청"]
    d = client.get("/admin/leads/L1", headers=h).json()
    assert set(d) >= {"lead", "facts", "score", "messages", "replies", "calls", "handoffs", "runs", "merged_leads"}
    assert "[콜 팔로업] 한빛몰" in d["calls"][0]["script_text"]
    assert client.get("/admin/leads/NOPE", headers=h).status_code == 404
    calls = client.get("/admin/calls", headers=h).json()["calls"]
    assert calls[0]["lead"]["phone"] == "010-0000-0001" and "Compelling Reason" in calls[0]["script_text"]
    assert client.get("/admin/messages", headers=h).json()["messages"][0]["lead"]["name"] == "김가상"
    assert client.get("/admin/runs?workflow=draft", headers=h).json()["runs"][0]["workflow"] == "draft"
    assert client.get("/admin/runs/R1", headers=h).json()["steps"][0]["text"] == "홈페이지부터 봅니다"


def test_decisions_use_login_name_when_not_given(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(jobs, "decide_message", lambda mid, decision, by, **kw: seen.update(by=by) or {"status": "sent"})
    monkeypatch.setattr(jobs, "record_call_outcome", lambda tid, outcome, by, **kw: seen.update(called=by) or {})
    h = {"Authorization": f"Bearer {login(client, name='이담당').json()['token']}"}
    assert client.post("/admin/messages/M1/decision", json={"decision": "approved"}, headers=h).status_code == 200
    assert client.post("/admin/calls/C1/outcome", json={"outcome": "voicemail"}, headers=h).status_code == 200
    assert seen == {"by": "이담당", "called": "이담당"}
    monkeypatch.setattr(jobs, "decide_message", lambda mid, decision, by, **kw: seen.update(kw=kw) or {"status": "rejected"})
    r = client.post("/admin/messages/M1/decision", headers=h,
                    json={"decision": "rejected", "reason_code": "tone", "review_seconds": 30})
    assert r.status_code == 200 and seen["kw"]["reason_code"] == "tone" and seen["kw"]["review_seconds"] == 30
    # JOB_TOKEN(시스템)으로는 누가 했는지 직접 적어야 한다
    sys_h = {"Authorization": "Bearer job-secret"}
    assert client.post("/admin/messages/M1/decision", json={"decision": "approved"}, headers=sys_h).status_code == 400


def test_metrics_and_baseline_require_auth_and_have_expected_shape(client):
    AdminRepo.timings = []
    assert client.get("/admin/metrics").status_code == 401
    assert client.get("/admin/baseline").status_code == 401
    assert client.post("/admin/baseline", json={"task": "draft", "minutes": 5}).status_code == 401
    h = {"Authorization": f"Bearer {login(client, name='이담당').json()['token']}"}
    m = client.get("/admin/metrics", headers=h).json()
    assert set(m) >= {"approval", "replies", "runs", "calls", "first_draft", "compare", "per_lead", "leads", "rows"}
    assert m["approval"]["decisions"] == 1 and m["calls"]["total"] == 1 and m["calls"]["pending"] == 1
    assert ["승인", "결정 수", 1] in m["rows"]
    b = client.get("/admin/baseline", headers=h).json()
    assert set(b["tasks"]) == {"research", "prioritize", "draft", "handoff", "reply_triage"} and b["timings"] == []
    assert b["stats"]["draft"] == {"n": 0, "median": None, "mean": None}


def test_baseline_post_validates_and_records_login_name(client):
    AdminRepo.timings = []
    h = {"Authorization": f"Bearer {login(client, name='이담당').json()['token']}"}
    ok = client.post("/admin/baseline", headers=h, json={"task": "draft", "minutes": 7.5, "label": "한빛몰"})
    assert ok.status_code == 200 and ok.json()["seconds"] == 450 and ok.json()["recorded_by"] == "이담당"
    assert client.post("/admin/baseline", headers=h, json={"task": "research", "seconds": 120}).json()["seconds"] == 120
    for bad in ({"task": "nope", "minutes": 5}, {"task": "draft", "minutes": 0}, {"task": "draft", "minutes": 9 * 60},
                {"task": "draft"}):
        assert client.post("/admin/baseline", headers=h, json=bad).status_code == 400
    stats = client.get("/admin/baseline", headers=h).json()["stats"]
    assert stats["draft"]["n"] == 1 and stats["draft"]["median"] == 450
    sys_h = {"Authorization": "Bearer job-secret"}
    assert client.post("/admin/baseline", headers=sys_h, json={"task": "draft", "minutes": 5}).status_code == 400
    AdminRepo.timings = []
