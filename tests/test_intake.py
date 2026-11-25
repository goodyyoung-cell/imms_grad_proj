"""리드 유입: 웹 폼, 시트 업로드, 정규화와 중복 병합 (DB·네트워크 없이)."""

import io
import itertools

import httpx
import pytest
from fastapi.testclient import TestClient

from sdr_agent import intake, jobs, sheets, webform
from sdr_agent.intake import clean_lead, find_match, ingest, norm_company


class IntakeRepo:
    def __init__(self, leads=()):
        self.leads = {l["id"]: dict(l) for l in leads}
        self._ids = itertools.count(1)

    def find_candidates(self, email, domain, company_name):
        return [dict(l) for l in self.leads.values()]  # 판정은 find_match가 한다

    def insert_lead(self, row):
        lead = {"id": f"N{next(self._ids)}", "status": "new", "unsubscribed_at": None, **row}
        self.leads[lead["id"]] = lead
        return dict(lead)


EXISTING = {"id": "A", "email": "lee@nuri.example", "company_name": "누리로지스(주)", "company_domain": "nuri.example",
            "source": "web_inquiry", "merged_into": None, "unsubscribed_at": None}


# --- 정규화 ---

def test_clean_lead_normalizes_fields():
    lead = clean_lead({"company_name": " 누리로지스(주) ", "email": " LEE@Nuri.Example ", "phone": "010-1234-5678",
                       "company_domain": "https://www.nuri.example/about", "source": "데모 요청",
                       "inbound_at": "2026.11.20"})
    assert lead["email"] == "lee@nuri.example" and lead["phone"] == "01012345678"
    assert lead["company_domain"] == "nuri.example" and lead["source"] == "demo_request"
    assert lead["inbound_at"].startswith("2026-11-20")


def test_domain_from_company_email_but_not_personal_mail():
    assert clean_lead({"company_name": "가", "email": "a@corp.example"})["company_domain"] == "corp.example"
    assert clean_lead({"company_name": "가", "email": "a@gmail.com"})["company_domain"] is None


@pytest.mark.parametrize("raw,msg", [({"email": "a@b.example"}, "회사명"), ({"company_name": "가", "email": "no-at"}, "이메일"),
                                     ({"company_name": "가", "inbound_at": "어제"}, "날짜")])
def test_clean_lead_errors(raw, msg):
    with pytest.raises(ValueError, match=msg):
        clean_lead(raw)


def test_norm_company_ignores_corp_marks_and_spacing():
    assert norm_company("누리 로지스(주)") == norm_company("주식회사 누리로지스") == "누리로지스"
    assert norm_company("Acme Co., Ltd.") == norm_company("ACME Inc.")


# --- 중복 판정 ---

@pytest.mark.parametrize("new,kind", [
    ({"company_name": "누리로지스", "email": "LEE@nuri.example", "source": "웹문의"}, "existing"),       # 재업로드
    ({"company_name": "누리로지스", "email": "lee@nuri.example", "source": "세미나"}, "same_person"),
    ({"company_name": "누리로지스", "email": "kang@nuri.example", "source": "세미나"}, "same_company"),  # 도메인
    ({"company_name": "주식회사 누리로지스", "email": "kang@gmail.com"}, "same_company"),               # 회사명
    ({"company_name": "다른회사", "email": "x@other.example"}, "none"),
])
def test_find_match(new, kind):
    assert find_match(clean_lead(new), [EXISTING]).kind == kind


def test_merge_points_to_root_lead():
    merged = {**EXISTING, "id": "B", "email": "kang@nuri.example", "merged_into": "A"}
    m = find_match(clean_lead({"company_name": "누리", "email": "kang@nuri.example", "source": "명함"}), [EXISTING, merged])
    assert m.kind == "same_person" and m.lead["id"] == "A"


def test_ingest_reports_and_handles_duplicates_within_file():
    repo = IntakeRepo([EXISTING])
    rows = [
        {"company_name": "누리로지스", "email": "lee@nuri.example", "source": "웹 문의", "_row": 2},   # 이미 있음
        {"company_name": "한빛몰", "email": "kim@hanbit.example", "source": "데모", "_row": 3},        # 신규
        {"company_name": "한빛몰(주)", "email": "park@hanbit.example", "source": "세미나", "_row": 4},  # 같은 파일 안 같은 회사
        {"company_name": "", "email": "x@y.example", "_row": 5},                                     # 오류
    ]
    r = ingest(rows, repo, channel="sheet_upload").as_dict()
    assert (r["created"], r["merged"], r["skipped"], r["errors"]) == (1, 1, 1, 1)
    new_id = r["details"]["created"][0]["lead_id"]
    assert r["details"]["merged"][0]["merged_into"] == new_id
    assert r["details"]["errors"][0]["row"] == 5
    assert repo.leads[new_id]["intake_channel"] == "sheet_upload"


def test_dry_run_writes_nothing():
    repo = IntakeRepo()
    r = ingest([{"company_name": "한빛몰", "email": "kim@hanbit.example"},
                {"company_name": "한빛몰", "email": "park@hanbit.example"}], repo, channel="sheet_upload", dry_run=True)
    assert repo.leads == {} and len(r.created) == 1 and len(r.merged) == 1


def test_unsubscribed_person_returning_is_flagged_not_revived(monkeypatch):
    sent = []
    monkeypatch.setattr(intake, "notify", lambda kind, title, text="": sent.append(kind))
    repo = IntakeRepo([{**EXISTING, "unsubscribed_at": "2026-11-01T00:00:00+00:00", "status": "unsubscribed"}])
    r = ingest([{"company_name": "누리로지스", "email": "lee@nuri.example", "source": "demo_request"}], repo, channel="web_form")
    assert len(r.merged) == 1 and sent == ["needs_human"]
    assert repo.leads["A"]["status"] == "unsubscribed"


# --- 시트 읽기 ---

def test_csv_with_korean_headers_and_cp949():
    data = "회사명,담당자,E-mail,직책,비고,모르는 열\n한빛몰,김가상,kim@hanbit.example,팀장,메모,x\n,,,,,\n".encode("cp949")
    rows, info = sheets.read_csv_bytes(data)
    assert rows == [{"company_name": "한빛몰", "name": "김가상", "email": "kim@hanbit.example", "title": "팀장",
                     "notes": "메모", "_row": 2}]
    assert info["ignored_columns"] == ["모르는 열"]


def test_xlsx_upload_reads_first_sheet():
    from datetime import datetime

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Company", "Name", "Email", "유입일"])
    ws.append(["Acme", "Kim", "kim@acme.example", datetime(2026, 11, 20)])
    buf = io.BytesIO()
    wb.save(buf)
    rows, _ = sheets.read_upload("leads.xlsx", buf.getvalue())
    assert rows[0]["company_name"] == "Acme" and clean_lead(dict(rows[0]))["inbound_at"].startswith("2026-11-20")


def test_missing_company_column_is_explained():
    with pytest.raises(ValueError, match="회사명 열"):
        sheets.read_csv_bytes("이름,이메일\n김,k@a.example\n".encode())


def test_google_sheet_reads_values_with_token():
    def handler(req):
        assert "/spreadsheets/abc123/values/" in str(req.url) and req.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={"values": [["회사명", "이메일", "메모"], ["한빛몰", "kim@hanbit.example"]]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    rows, _ = sheets.read_google_sheet("https://docs.google.com/spreadsheets/d/abc123/edit#gid=0", http=http, token="t")
    assert rows == [{"company_name": "한빛몰", "email": "kim@hanbit.example", "notes": "", "_row": 2}]


# --- 웹 폼과 업로드 API ---

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "secret")
    repo = IntakeRepo()
    real = jobs.intake_leads
    monkeypatch.setattr(jobs, "intake_leads", lambda rows, **kw: real(rows, repo=repo, **kw))
    webform._hits.clear()
    from sdr_agent.app import app

    c = TestClient(app)
    c.repo = repo
    return c


FORM = {"name": "김가상", "email": "kim@hanbit.example", "company_name": "한빛몰", "source": "demo_request",
        "notes": "상담원 8명", "consent": "yes"}


def test_form_page_and_successful_submit_records_consent(client):
    assert "개인정보" in client.get("/form").text
    r = client.post("/form/submit", data=FORM)
    assert r.status_code == 200 and "감사합니다" in r.text
    [lead] = client.repo.leads.values()
    assert lead["intake_channel"] == "web_form" and lead["consent_at"] and lead["company_domain"] == "hanbit.example"


def test_form_requires_consent_and_drops_bots(client):
    assert client.post("/form/submit", data={**FORM, "consent": ""}).status_code == 400
    assert client.post("/form/submit", data={**FORM, "website": "http://spam.example"}).status_code == 200
    assert client.repo.leads == {}


def test_form_rate_limit(client):
    codes = [client.post("/form/submit", data={**FORM, "email": f"u{i}@hanbit.example"}).status_code for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_admin_import_defaults_to_preview(client):
    csv_bytes = "회사명,이메일\n한빛몰,kim@hanbit.example\n".encode()
    h = {"Authorization": "Bearer secret"}
    assert client.post("/admin/leads/import", files={"file": ("a.csv", csv_bytes)}).status_code == 401
    r = client.post("/admin/leads/import", files={"file": ("a.csv", csv_bytes)}, headers=h).json()
    assert r["dry_run"] is True and r["created"] == 1 and client.repo.leads == {}
    r = client.post("/admin/leads/import", files={"file": ("a.csv", csv_bytes)}, data={"dry_run": "false"}, headers=h).json()
    assert r["created"] == 1 and len(client.repo.leads) == 1


def test_webhook_lead_needs_token(client):
    body = {"company_name": "한빛몰", "email": "kim@hanbit.example", "consent": True}
    assert client.post("/webhooks/lead", json=body).status_code == 401
    r = client.post("/webhooks/lead", json=body, headers={"Authorization": "Bearer secret"}).json()
    assert r["created"] == 1
