"""Cloud Run에서 도는 웹 서버.

- Cloud Scheduler가 /jobs/* 를 정해진 시간에 호출한다 (평일 아침 리서치 → 초안)
- 외부 이벤트(답장 도착, 외부 폼)는 /webhooks/* 로 들어온다
- 관리 화면(프론트엔드)은 /auth/login 으로 로그인한 뒤 /admin/* 을 부른다 (docs/frontend-handoff.md)

인증 (공개 URL이라 /health, /form 말고는 모두 필요)
- 관리 화면: POST /auth/login {name, password=ADMIN_PASSWORD} → `Authorization: Bearer adm.…` (8시간)
- 예약 실행·CLI·외부 서버: `Authorization: Bearer <JOB_TOKEN>`
JOB_TOKEN을 브라우저 코드에 넣지 않는다.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import auth as admin_auth
from . import jobs, sheets, webform
from .config import load_settings
from .db import Repo, get_client
from .workflows.call_script import render_text as render_call_script

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="SDR Agent", description="도메인 특화 AI SDR 에이전트 백엔드. 화면 개발은 docs/frontend-handoff.md")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_settings().allowed_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --- 인증 ---

def principal(authorization: str | None = Header(default=None)) -> admin_auth.Principal:
    """JOB_TOKEN(시스템) 또는 관리 화면 로그인 토큰(사람)을 받는다."""
    s = load_settings()
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not given:
        raise HTTPException(401, "인증 실패")
    if s.job_token and hmac.compare_digest(given, s.job_token):
        return admin_auth.Principal("system", "system")
    if given.startswith(admin_auth.PREFIX) and (s.admin_token_secret or s.job_token):
        name = admin_auth.verify(given, admin_auth.signing_key(s.admin_token_secret, s.job_token))
        if name:
            return admin_auth.Principal(name, "admin")
    if not s.job_token:
        raise HTTPException(503, "JOB_TOKEN이 설정되지 않았습니다")
    raise HTTPException(401, "인증 실패 (토큰이 틀렸거나 로그인이 만료됨)")


def require_job_token(authorization: str | None = Header(default=None)) -> None:
    """예전 이름 호환. principal과 같다."""
    principal(authorization)


auth = [Depends(principal)]


def get_repo() -> Repo:
    return Repo(get_client())


def who(given: str | None, p: admin_auth.Principal) -> str:
    """승인·통화 기록의 '누가'. 관리 화면 로그인이면 로그인 이름을 쓴다."""
    if given and given.strip():
        return given.strip()
    if p.kind == "admin":
        return p.name
    raise HTTPException(400, "decided_by(또는 called_by)가 필요합니다")


class Login(BaseModel):
    name: str
    password: str


@app.post("/auth/login")
def login(body: Login, request: Request) -> dict:
    s = load_settings()
    if not s.admin_password:
        raise HTTPException(503, "ADMIN_PASSWORD가 설정되지 않았습니다")
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    if not webform.allow(f"login:{ip}"):
        raise HTTPException(429, "잠시 후 다시 시도해 주세요")
    name = body.name.strip()
    if not name or len(name) > 30:
        raise HTTPException(400, "이름을 1~30자로 입력해 주세요")
    if not admin_auth.check_password(body.password, s.admin_password):
        raise HTTPException(401, "비밀번호가 틀렸습니다")
    token, exp = admin_auth.issue(name, admin_auth.signing_key(s.admin_token_secret, s.job_token))
    return {"token": token, "name": name, "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat()}


@app.get("/admin/me")
def me(p: admin_auth.Principal = Depends(principal)) -> dict:
    return {"name": p.name, "kind": p.kind}


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


# --- 작업 실행 (예약 실행이 부르고, 시연 때 화면에서 버튼으로 불러도 된다) ---

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
    """답장 도착. 실제 메일 수신은 연동하지 않으므로 모의 수신함(화면·CLI·평가 스크립트)이 호출한다."""
    try:
        return jobs.handle_reply(reply.body, lead_id=reply.lead_id, from_email=reply.from_email,
                                 message_id=reply.message_id, provider=reply.provider)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# --- 관리 화면 조회 ---

def _with_lead(rows: list[dict], repo: Repo) -> list[dict]:
    leads = repo.leads_by_ids([r.get("lead_id") for r in rows])
    return [{**r, "lead": leads.get(r.get("lead_id"))} for r in rows]


@app.get("/admin/summary", dependencies=auth)
def admin_summary(repo: Repo = Depends(get_repo)) -> dict:
    """첫 화면 요약: 리드 상태별 개수, 승인 대기, 걸어야 할 전화, 최근 실행."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "lead_status_counts": repo.status_counts(),
        "pending_approvals": len(repo.list_messages(status="pending_approval", limit=200)),
        "calls_due": len(repo.list_call_tasks(status="pending", due_before=now)),
        "calls_pending": len(repo.list_call_tasks(status="pending")),
        "recent_runs": _with_lead(repo.list_runs(limit=10), repo),
    }


@app.get("/admin/leads", dependencies=auth)
def admin_leads(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                repo: Repo = Depends(get_repo)) -> dict:
    """대표 리드 목록 (최근 변경 순). status로 거르고 q로 회사명 검색. 최신 점수·근거 태그 포함."""
    leads, total = repo.list_leads(status=status, q=q, limit=min(limit, 200), offset=offset)
    scores = repo.latest_scores([l["id"] for l in leads])
    for l in leads:
        sc = scores.get(l["id"])
        l["score"], l["tags"] = (sc["score"], sc["tags"]) if sc else (None, [])
    return {"total": total, "leads": leads}


@app.get("/admin/leads/{lead_id}", dependencies=auth)
def admin_lead_detail(lead_id: str, repo: Repo = Depends(get_repo)) -> dict:
    """리드 한 건의 전부: 리서치 사실, 점수, 메일, 답장, 통화, 인계, 실행 기록, 병합된 중복 리드."""
    try:
        lead = repo.get_lead(lead_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    calls = repo.list_call_tasks(lead_id=lead_id)
    for c in calls:
        c["script_text"] = render_call_script(c["script"], lead)
    return {
        "lead": lead,
        "merged_leads": repo.merged_leads(lead_id),
        "facts": repo.list_facts(lead_id),
        "score": repo.latest_score(lead_id),
        "messages": repo.list_messages(lead_id=lead_id),
        "replies": repo.list_replies(lead_id),
        "calls": calls,
        "handoffs": repo.list_handoffs(lead_id),
        "runs": repo.list_runs(lead_id=lead_id),
    }


@app.get("/admin/messages", dependencies=auth)
def admin_messages(status: str = "pending_approval", limit: int = 50, repo: Repo = Depends(get_repo)) -> dict:
    """메일 목록 (기본: 승인 대기). 각 메일에 리드 요약(lead) 포함."""
    return {"messages": _with_lead(repo.list_messages(status=status, limit=limit), repo)}


@app.get("/admin/calls", dependencies=auth)
def admin_calls(due_only: bool = True, repo: Repo = Depends(get_repo)) -> dict:
    """걸어야 할 전화 (due_only=false면 대기 중 전체). 리드 요약과 바로 읽을 스크립트 텍스트(script_text) 포함."""
    now = datetime.now(timezone.utc).isoformat()
    calls = _with_lead(repo.list_call_tasks(status="pending", due_before=now if due_only else None), repo)
    for c in calls:
        c["script_text"] = render_call_script(c["script"], c["lead"] or {})
    return {"calls": calls}


@app.get("/admin/runs", dependencies=auth)
def admin_runs(lead_id: str | None = None, workflow: str | None = None, limit: int = 50,
               repo: Repo = Depends(get_repo)) -> dict:
    """에이전트 실행 기록 (최근 순). workflow: research | draft | reply_triage | call_script | handoff"""
    return {"runs": _with_lead(repo.list_runs(lead_id=lead_id, workflow=workflow, limit=min(limit, 200)), repo)}


@app.get("/admin/runs/{run_id}", dependencies=auth)
def admin_run_detail(run_id: str, repo: Repo = Depends(get_repo)) -> dict:
    """실행 한 건의 단계: LLM이 쓴 이유(text)와 도구 호출(tool_name, input, output, is_error)."""
    try:
        return repo.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


# --- 관리 화면 동작 (사람의 결정) ---

class Decision(BaseModel):
    decision: str  # approved | rejected | edited
    decided_by: str | None = None  # 비우면 로그인 이름
    edited_body: str | None = None
    note: str | None = None


@app.post("/admin/messages/{message_id}/decision")
def admin_decide(message_id: str, d: Decision, p: admin_auth.Principal = Depends(principal)) -> dict:
    """메일 승인·수정 승인·반려. 승인하면 바로 샌드박스 발송, 첫 메일이면 전화 과제 생성."""
    try:
        return jobs.decide_message(message_id, d.decision, who(d.decided_by, p), channel="admin",
                                   edited_body=d.edited_body, note=d.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class UnsubscribeConfirm(BaseModel):
    decided_by: str | None = None
    note: str | None = None


@app.post("/admin/leads/{lead_id}/unsubscribe")
def admin_unsubscribe(lead_id: str, u: UnsubscribeConfirm, p: admin_auth.Principal = Depends(principal)) -> dict:
    """담당자가 수신 거부를 확정한다 (AI가 확신도 낮게 수신 거부로 본 경우 등)."""
    try:
        return jobs.confirm_unsubscribe(lead_id, who(u.decided_by, p), note=u.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


class CallOutcome(BaseModel):
    outcome: str  # interested | not_now | needs_info | no_answer | voicemail | wrong_contact | unsubscribe
    called_by: str | None = None            # 비우면 로그인 이름
    notes: str | None = None
    email_received: bool | None = None      # 메일을 받았다고 했는지
    meeting_preference: str | None = None   # 희망 미팅 시기
    compelling_reason: str | None = None    # 왜 지금 바꿔야 하는지, 들은 내용


@app.post("/admin/calls/{task_id}/outcome")
def admin_call_outcome(task_id: str, c: CallOutcome, p: admin_auth.Principal = Depends(principal)) -> dict:
    try:
        return jobs.record_call_outcome(task_id, c.outcome, who(c.called_by, p), notes=c.notes,
                                        email_received=c.email_received, meeting_preference=c.meeting_preference,
                                        compelling_reason=c.compelling_reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
