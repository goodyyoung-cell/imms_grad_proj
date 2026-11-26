"""Cloud Run에서 도는 웹 서버.

- Cloud Scheduler가 /jobs/* 를 정해진 시간에 호출한다 (예: 평일 아침 리서치 → 초안)
- 답장 도착 같은 외부 이벤트는 /webhooks/* 로 들어온다
- 승인 채널(Slack 또는 이메일)이 정해지기 전까지 승인은 /admin/* 으로 한다 (scope.md의 대체안)

모든 경로는 공개 URL이라 JOB_TOKEN으로 보호한다. 호출할 때
`Authorization: Bearer <JOB_TOKEN>` 헤더를 넣는다.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import jobs, sheets, webform
from .config import load_settings
from .db import Repo, get_client

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="SDR Agent")


def require_job_token(authorization: str | None = Header(default=None)) -> None:
    expected = load_settings().job_token
    if not expected:
        raise HTTPException(503, "JOB_TOKEN이 설정되지 않았습니다")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "인증 실패")


auth = [Depends(require_job_token)]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# --- 리드 유입 1: 웹사이트 폼 ---

@app.get("/form", response_class=HTMLResponse)
def form_page() -> str:
    return webform.form_page()


@app.post("/form/submit", response_class=HTMLResponse)
def form_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    company_name: str = Form(""),
    company_domain: str = Form(""),
    title: str = Form(""),
    source: str = Form("demo_request"),
    notes: str = Form(""),
    consent: str = Form(""),
    website: str = Form(""),  # 사람에게 안 보이는 칸. 채워져 있으면 봇
):
    if website:
        return HTMLResponse(webform.done_page())  # 봇에게는 성공처럼 보이고 저장하지 않는다
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    if not webform.allow(ip):
        return HTMLResponse(webform.form_page("잠시 후 다시 시도해 주세요."), status_code=429)
    if consent != "yes":
        return HTMLResponse(webform.form_page("개인정보 수집·이용에 동의해 주셔야 신청할 수 있습니다."), status_code=400)
    if not (name.strip() and email.strip() and company_name.strip()):
        return HTMLResponse(webform.form_page("이름, 회사 이메일, 회사명을 입력해 주세요."), status_code=400)
    row = {"name": name, "email": email, "company_name": company_name, "company_domain": company_domain,
           "title": title, "source": source if source in ("demo_request", "web_inquiry") else "web_inquiry",
           "notes": notes}
    result = jobs.intake_leads([row], channel="web_form", consent_at=datetime.now(timezone.utc).isoformat())
    if result["errors"]:
        return HTMLResponse(webform.form_page(result["details"]["errors"][0]["error"]), status_code=400)
    return HTMLResponse(webform.done_page())


class InboundLead(BaseModel):
    company_name: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_domain: str | None = None
    title: str | None = None
    source: str = "web_inquiry"
    notes: str | None = None
    consent: bool = False


@app.post("/webhooks/lead", dependencies=auth)
def webhook_lead(lead: InboundLead) -> dict:
    """외부 폼 서비스(구글 폼 Apps Script, Tally 등)가 서버에서 보내는 리드."""
    data = lead.model_dump()
    consent = data.pop("consent")
    return jobs.intake_leads([data], channel="web_form",
                             consent_at=datetime.now(timezone.utc).isoformat() if consent else None)


# --- 리드 유입 2: 직원 시트 업로드 ---

@app.post("/admin/leads/import", dependencies=auth)
async def admin_import_leads(
    file: UploadFile | None = File(None),
    google_sheet: str | None = Form(None),
    sheet: str | None = Form(None),
    dry_run: bool = Form(True),
) -> dict:
    """CSV·엑셀 파일 또는 구글 시트 주소로 리드를 올린다. 기본은 미리보기(dry_run). 확인 후 dry_run=false로 다시 보낸다."""
    try:
        if file is not None:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "파일이 너무 큽니다 (최대 5MB)")
            rows, info = sheets.read_upload(file.filename or "", data, sheet)
        elif google_sheet:
            rows, info = sheets.read_google_sheet(google_sheet, sheet)
        else:
            raise HTTPException(400, "file 또는 google_sheet가 필요합니다")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**info, **jobs.intake_leads(rows, channel="sheet_upload", dry_run=dry_run)}


class BatchJob(BaseModel):
    lead_ids: list[str] | None = None
    limit: int = 10
    provider: str | None = None  # "claude" | "gemini". 비우면 DEFAULT_LLM
    trigger: str = "schedule"


@app.post("/jobs/research", dependencies=auth)
def job_research(job: BatchJob) -> dict:
    results = jobs.research_leads(job.lead_ids, limit=job.limit, provider=job.provider, trigger=job.trigger)
    return {"processed": len(results), "results": results}


@app.post("/jobs/draft", dependencies=auth)
def job_draft(job: BatchJob) -> dict:
    results = jobs.draft_emails(job.lead_ids, limit=job.limit, provider=job.provider, trigger=job.trigger)
    return {"processed": len(results), "results": results}


class HandoffJob(BaseModel):
    lead_id: str
    reason: str = "담당자 요청"
    provider: str | None = None


@app.post("/jobs/handoff", dependencies=auth)
def job_handoff(job: HandoffJob) -> dict:
    return jobs.handoff_lead(job.lead_id, reason=job.reason, provider=job.provider, trigger="manual")


class InboundReply(BaseModel):
    body: str
    from_email: str | None = None
    lead_id: str | None = None
    message_id: str | None = None
    provider: str | None = None


@app.post("/webhooks/reply", dependencies=auth)
def webhook_reply(reply: InboundReply) -> dict:
    """답장 도착. 지금은 모의 수신함(CLI·평가 스크립트)이 호출한다."""
    try:
        return jobs.handle_reply(reply.body, lead_id=reply.lead_id, from_email=reply.from_email,
                                 message_id=reply.message_id, provider=reply.provider)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/admin/messages", dependencies=auth)
def admin_messages(status: str = "pending_approval", limit: int = 50) -> dict:
    return {"messages": Repo(get_client()).list_messages(status=status, limit=limit)}


class Decision(BaseModel):
    decision: str  # approved | rejected | edited
    decided_by: str
    edited_body: str | None = None
    note: str | None = None


@app.get("/admin/calls", dependencies=auth)
def admin_calls(due_only: bool = True) -> dict:
    """걸어야 할 전화 목록 (due_only=false면 대기 중인 전화 전체)."""
    if due_only:
        return {"calls": jobs.due_calls()}
    return {"calls": Repo(get_client()).list_call_tasks(status="pending")}


class CallOutcome(BaseModel):
    outcome: str  # interested | not_now | needs_info | no_answer | voicemail | wrong_contact | unsubscribe
    called_by: str
    notes: str | None = None
    email_received: bool | None = None      # 메일을 받았다고 했는지
    meeting_preference: str | None = None   # 희망 미팅 시기
    compelling_reason: str | None = None    # 왜 지금 바꿔야 하는지, 들은 내용


@app.post("/admin/calls/{task_id}/outcome", dependencies=auth)
def admin_call_outcome(task_id: str, c: CallOutcome) -> dict:
    try:
        return jobs.record_call_outcome(task_id, c.outcome, c.called_by, notes=c.notes, email_received=c.email_received,
                                        meeting_preference=c.meeting_preference, compelling_reason=c.compelling_reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/admin/messages/{message_id}/decision", dependencies=auth)
def admin_decide(message_id: str, d: Decision) -> dict:
    try:
        return jobs.decide_message(message_id, d.decision, d.decided_by, channel="admin",
                                   edited_body=d.edited_body, note=d.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
