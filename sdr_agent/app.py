"""Cloud Run에서 도는 웹 서버.

- Cloud Scheduler가 /jobs/* 를 정해진 시간에 호출한다 (평일 아침 리서치 → 초안)
- 외부 이벤트(답장 도착, 외부 폼)는 /webhooks/* 로 들어온다
- 관리 화면(프론트엔드)은 /auth/login 으로 로그인한 뒤 /admin/* 을 부른다 (docs/frontend-handoff.md)

인증 (공개 URL이라 /health, /form, /unsubscribe/*, /webhooks/slack/interactions 말고는 모두 필요)
- 관리 화면: POST /auth/login {name, password=ADMIN_PASSWORD} → `Authorization: Bearer adm.…` (8시간)
- 예약 실행·CLI·외부 서버: `Authorization: Bearer <JOB_TOKEN>`
- Slack 버튼: Bearer 대신 Slack 서명(SLACK_SIGNING_SECRET)으로 검증한다
JOB_TOKEN을 브라우저 코드에 넣지 않는다.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import auth as admin_auth
from . import chat, compliance, jobs, metrics, sheets, slack, slack_chat, webform
from . import feedback as feedback_mod
from . import industry as industry_mod
from .config import load_settings
from .kb import default_kb
from .notify import notify
from .db import Repo, get_client
from .workflows.call_script import render_text as render_call_script

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="SDR Agent", description="도메인 특화 AI SDR 에이전트 백엔드. 화면 개발은 docs/frontend-handoff.md")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_settings().allowed_origins),
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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


# --- 수신 거부: 메일 하단 링크. 로그인 없이 한 번에 처리한다 (정보통신망법 제50조 제4항) ---

def _unsub_action(lead_id: str, token: str) -> str:
    return f"/unsubscribe/{lead_id}/{token}"


@app.get("/unsubscribe/{lead_id}/{token}", response_class=HTMLResponse)
def unsubscribe_page(lead_id: str, token: str) -> HTMLResponse:
    # 메일 보안 검사기가 링크를 미리 열어도 거부되지 않도록, 열기만 해서는 처리하지 않고 버튼을 누르게 한다
    if not compliance.unsubscribe_token_ok(load_settings(), lead_id, token):
        raise HTTPException(404)
    return HTMLResponse(compliance.unsubscribe_confirm_page(_unsub_action(lead_id, token)))


@app.post("/unsubscribe/{lead_id}/{token}", response_class=HTMLResponse)
def unsubscribe_do(lead_id: str, token: str, request: Request, repo: Repo = Depends(get_repo)) -> HTMLResponse:
    s = load_settings()
    if not compliance.unsubscribe_token_ok(s, lead_id, token):
        raise HTTPException(404)
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    if not webform.allow(f"unsub:{ip}"):
        return HTMLResponse(compliance.unsubscribe_confirm_page(_unsub_action(lead_id, token)), status_code=429)
    try:
        lead = repo.get_lead(lead_id)
    except LookupError:
        return HTMLResponse(compliance.unsubscribed_page())  # 없는 리드여도 같은 화면 (존재 여부를 알려 주지 않는다)
    if not lead.get("unsubscribed_at"):
        repo.unsubscribe(lead_id)  # DB 트리거가 보내지 않은 메시지와 걸지 않은 전화를 모두 취소한다
        notify("unsubscribe", f"수신 거부: {lead.get('company_name')}",
               "메일의 수신 거부 링크로 접수했습니다. 모든 발송을 중단했습니다.", lead_id=lead_id, settings=s)
    return HTMLResponse(compliance.unsubscribed_page())


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


@app.post("/jobs/notify-due-calls", dependencies=auth)
def job_notify_due_calls(repo: Repo = Depends(get_repo)) -> dict:
    """전화 시각이 된 콜 과제를 Slack에 알린다 (과제마다 한 번)."""
    return jobs.notify_due_calls(repo=repo)


class RemindJob(BaseModel):
    hours: int | None = None  # 비우면 REMINDER_HOURS


@app.post("/jobs/remind", dependencies=auth)
def job_remind(job: RemindJob | None = None, repo: Repo = Depends(get_repo)) -> dict:
    """오래 기다린 승인 대기 메일을 Slack에 알린다."""
    return jobs.remind_pending(hours=job.hours if job else None, repo=repo)


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


@app.post("/webhooks/slack/interactions")
async def webhook_slack_interactions(request: Request, repo: Repo = Depends(get_repo)) -> JSONResponse:
    """Slack 버튼(승인·수정·반려)과 모달 제출이 들어오는 곳. Slack은 Bearer를 보낼 수 없어 서명으로만 보호한다."""
    s = load_settings()
    if not s.slack_signing_secret:
        raise HTTPException(503, "SLACK_SIGNING_SECRET이 설정되지 않았습니다")
    raw = await request.body()
    if not slack.verify_signature(s.slack_signing_secret, request.headers.get("x-slack-request-timestamp"), raw,
                                  request.headers.get("x-slack-signature")):
        raise HTTPException(401, "서명 검증 실패")
    try:
        payload = json.loads(parse_qs(raw.decode()).get("payload", [""])[0])
    except (ValueError, IndexError):
        raise HTTPException(400, "payload를 읽을 수 없습니다")
    body = await run_in_threadpool(slack.handle_interaction, payload, settings=s, repo=repo)
    return JSONResponse(body or {})


@app.post("/webhooks/slack/events")
async def webhook_slack_events(request: Request, background: BackgroundTasks, repo: Repo = Depends(get_repo)) -> JSONResponse:
    """Slack 이벤트(DM, 멘션, 스레드 답글)가 들어오는 곳. 서명으로만 보호한다.
    3초 안에 응답해야 하므로 걸러서 작업만 만들고, 처리는 응답 뒤 백그라운드에서 한다 (slack_chat.py)."""
    s = load_settings()
    if not s.slack_signing_secret:
        raise HTTPException(503, "SLACK_SIGNING_SECRET이 설정되지 않았습니다")
    raw = await request.body()
    if not slack.verify_signature(s.slack_signing_secret, request.headers.get("x-slack-request-timestamp"), raw,
                                  request.headers.get("x-slack-signature")):
        raise HTTPException(401, "서명 검증 실패")
    try:
        payload = json.loads(raw.decode())
    except ValueError:
        raise HTTPException(400, "본문을 읽을 수 없습니다")
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})
    if request.headers.get("x-slack-retry-num"):
        return JSONResponse({})  # 재전송. 첫 요청을 이미 처리 중이다
    job = await run_in_threadpool(slack_chat.plan, payload, settings=s, repo=repo)
    if job:
        background.add_task(slack_chat.process, job, settings=s, repo=repo)
    return JSONResponse({})


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
        "messages": _enrich_messages(repo.list_messages(lead_id=lead_id), repo),
        "replies": repo.list_replies(lead_id),
        "calls": calls,
        "handoffs": repo.list_handoffs(lead_id),
        "runs": repo.list_runs(lead_id=lead_id),
    }


@app.get("/admin/messages", dependencies=auth)
def admin_messages(status: str = "pending_approval", limit: int = 50, repo: Repo = Depends(get_repo)) -> dict:
    """메일 목록 (기본: 승인 대기). 각 메일에 리드 요약(lead), 제목 후보·소재·이전 버전, 반영된 피드백(feedback_applied) 포함."""
    return {"messages": _enrich_messages(_with_lead(repo.list_messages(status=status, limit=limit), repo), repo)}


def _enrich_messages(rows: list[dict], repo: Repo) -> list[dict]:
    """메일에 반영된 피드백 내용을 붙인다 (citations의 feedback id → 규칙 문장). 피드백 표를 못 읽어도 목록은 준다."""
    ids = [c["id"] for r in rows for c in (r.get("citations") or []) if c.get("type") == "feedback"]
    try:
        notes = feedback_mod.with_labels(list(repo.feedback_by_ids(ids).values()), repo) if ids else []
    except Exception:
        logger.exception("반영된 피드백 조회 실패 (건너뜀)")
        notes = []
    by_id = {n["id"]: n for n in notes}
    out = []
    for r in rows:
        applied = [{k: by_id[c["id"]][k] for k in ("id", "scope", "scope_key", "scope_label", "kind", "text")}
                   for c in (r.get("citations") or []) if c.get("type") == "feedback" and c["id"] in by_id]
        out.append({**r, "feedback_applied": applied})
    return out


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


REPLY_CLASSES = ("interested", "question", "not_now", "not_decision_maker", "out_of_office", "unsubscribe", "unclear")


@app.get("/admin/replies", dependencies=auth)
def admin_replies(classification: str | None = None, handled_by: str | None = None, limit: int = 100,
                  repo: Repo = Depends(get_repo)) -> dict:
    """받은 답장 목록 (최근 순). classification, handled_by(agent | human)로 거른다. 리드 요약(lead) 포함."""
    if classification and classification not in REPLY_CLASSES:
        raise HTTPException(400, f"classification은 {list(REPLY_CLASSES)} 중 하나여야 합니다")
    if handled_by and handled_by not in ("agent", "human"):
        raise HTTPException(400, "handled_by는 agent 또는 human이어야 합니다")
    rows = repo.list_recent_replies(classification=classification or None, handled_by=handled_by or None,
                                    limit=max(1, min(limit, 200)))
    return {"replies": _with_lead(rows, repo)}


@app.get("/admin/approvals", dependencies=auth)
def admin_approvals(limit: int = 200, repo: Repo = Depends(get_repo)) -> dict:
    """승인·반려 기록 (최근 순). 각 기록에 대상 메일(message)과 리드 요약(lead) 포함."""
    rows = repo.list_recent_approvals(limit=max(1, min(limit, 500)))
    messages = repo.messages_by_ids([r.get("message_id") for r in rows])
    leads = repo.leads_by_ids([m.get("lead_id") for m in messages.values()])
    out = []
    for r in rows:
        m = messages.get(r.get("message_id"))
        out.append({
            "id": r.get("id"), "message_id": r.get("message_id"), "decision": r.get("decision"),
            "decided_by": r.get("decided_by"), "decided_at": r.get("decided_at"), "note": r.get("note"),
            "reason_code": r.get("reason_code"), "original_body": r.get("original_body"),
            "review_seconds": r.get("review_seconds"), "channel": r.get("channel"),
            "message": m, "lead": leads.get(m.get("lead_id")) if m else None,
        })
    return {"approvals": out}


@app.get("/admin/kb", dependencies=auth)
def admin_kb() -> dict:
    """에이전트가 인용할 수 있는 승인된 회사 자료. 메일 근거 표시의 id(예: 02_pricing#플랜)가 여기 section id와 같다."""
    docs: dict[str, dict] = {}
    for c in default_kb().chunks:
        doc_id = c.id.split("#", 1)[0]
        doc = docs.setdefault(doc_id, {"id": doc_id, "title": c.doc, "sections": []})
        doc["sections"].append({"id": c.id, "heading": c.heading, "text": c.text})
    return {"docs": list(docs.values())}


@app.get("/admin/status", dependencies=auth)
def admin_status(repo: Repo = Depends(get_repo)) -> dict:
    """연결·안전장치 상태. 비밀값은 싣지 않고 설정 여부(true/false)와 개수만 보낸다."""
    s = load_settings()
    return {
        "mode": {"send": "sandbox", "llm_default": s.default_llm,
                 "providers": {"claude": bool(s.anthropic_api_key), "gemini": bool(s.gemini_api_key)}},
        "slack": {"configured": bool(s.slack_bot_token and s.slack_channel),
                  "signing_configured": bool(s.slack_signing_secret), "channel": s.slack_channel,
                  "approvers": len(s.slack_approvers), "notify_kinds": list(s.slack_notify_kinds),
                  "web_url": s.admin_web_url},
        "safeguards": {"require_consent": s.require_consent_to_send, "max_sends_per_day": s.max_sends_per_day,
                       "sent_today": repo.count_sent_today(), "sender_name": s.sender_name,
                       "sender_contact": s.sender_contact},
        "knowledge_docs": len({c.id.split("#", 1)[0] for c in default_kb().chunks}),
        "chat": {"enabled": bool(s.anthropic_api_key or (s.gemini_api_key and s.gemini_model)),
                 "slack_events": bool(s.slack_bot_token and s.slack_signing_secret)},
        "memory": {"active_notes": _count_feedback(repo)},
    }


def _count_feedback(repo: Repo) -> int | None:
    try:
        return repo.count_feedback()
    except Exception:  # 마이그레이션 전에는 표가 없다
        return None


class NotifyTest(BaseModel):
    text: str | None = None


@app.post("/admin/notify-test")
def admin_notify_test(body: NotifyTest | None = None, p: admin_auth.Principal = Depends(principal)) -> dict:
    """Slack 연결 시험 메시지를 한 건 보낸다 (사용자당 1분에 5회)."""
    s = load_settings()
    by = who(None, p)
    if not webform.allow(f"notify-test:{by}"):
        raise HTTPException(429, "잠시 후 다시 시도해 주세요")
    if not (s.slack_bot_token and s.slack_channel):
        return {"posted": False, "reason": "Slack 설정이 없습니다 (SLACK_BOT_TOKEN, SLACK_CHANNEL)"}
    extra = ((body.text if body else None) or "").strip()[:500]
    text = f"{by}님이 연결 시험을 실행했습니다." + (f"\n{extra}" if extra else "")
    ok = notify("test", "연결 시험", text, settings=s)
    return {"posted": ok, "reason": None if ok else "Slack 전송에 실패했습니다. 토큰과 채널을 확인해 주세요"}


# --- 평가 지표와 수작업 기준 시간 ---

@app.get("/admin/metrics", dependencies=auth)
def admin_metrics(repo: Repo = Depends(get_repo)) -> dict:
    """평가 지표. rows는 보고서·CSV용으로 펼친 [구분, 지표, 값] 목록."""
    m = metrics.compute(messages=repo.list_messages(limit=5000), approvals=repo.list_all_approvals(),
                        replies=repo.list_all_replies(), runs=repo.list_runs(limit=5000),
                        leads=repo.list_all_leads(), timings=repo.list_manual_timings(),
                        calls=repo.list_call_tasks(limit=5000))
    return {**m, "rows": [list(r) for r in metrics.to_rows(m)]}


@app.get("/admin/baseline", dependencies=auth)
def admin_baseline(repo: Repo = Depends(get_repo)) -> dict:
    """사람이 직접 했을 때 걸린 시간 기록 목록과 과제별 통계."""
    timings = repo.list_manual_timings()
    return {"tasks": metrics.TASKS, "timings": timings, "stats": metrics.timing_stats(timings)}


class BaselineEntry(BaseModel):
    task: str                       # research | prioritize | draft | handoff | reply_triage
    minutes: float | None = None
    seconds: float | None = None    # minutes 대신 초로 줄 수 있다
    label: str | None = None        # 어떤 리드·시나리오로 쟀는지


@app.post("/admin/baseline")
def admin_baseline_add(b: BaselineEntry, p: admin_auth.Principal = Depends(principal),
                       repo: Repo = Depends(get_repo)) -> dict:
    """수작업 기준 시간 한 건을 기록한다. 기록한 사람은 로그인 이름."""
    if b.seconds is None and b.minutes is None:
        raise HTTPException(400, "minutes 또는 seconds가 필요합니다")
    seconds = round(b.seconds if b.seconds is not None else b.minutes * 60)
    if b.task not in metrics.TASKS or not 0 < seconds <= 8 * 3600:
        raise HTTPException(400, f"task는 {list(metrics.TASKS)} 중 하나이고, 시간은 0보다 크고 8시간 이하여야 합니다")
    return repo.insert_manual_timing({"task": b.task, "seconds": seconds, "label": (b.label or "").strip()[:100] or None,
                                      "recorded_by": who(None, p)})


# --- 관리 화면 동작 (사람의 결정) ---

class Decision(BaseModel):
    decision: str  # approved | rejected | edited
    decided_by: str | None = None  # 비우면 로그인 이름
    edited_body: str | None = None
    note: str | None = None
    reason_code: str | None = None     # 반려 사유: factual_error | tone | wrong_target | forbidden_phrase | other
    review_seconds: int | None = None  # 화면에서 검토에 쓴 시간(초)
    subject_option: str | None = None  # 승인 직전에 바꿀 제목 후보: general | company | hook


@app.post("/admin/messages/{message_id}/decision")
def admin_decide(message_id: str, d: Decision, p: admin_auth.Principal = Depends(principal)) -> dict:
    """메일 승인·수정 승인·반려. 승인하면 바로 샌드박스 발송, 첫 메일이면 전화 과제 생성."""
    try:
        return jobs.decide_message(message_id, d.decision, who(d.decided_by, p), channel="admin",
                                   edited_body=d.edited_body, note=d.note, reason_code=d.reason_code,
                                   review_seconds=d.review_seconds, subject_option=d.subject_option)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class SubjectChoice(BaseModel):
    option_id: str  # general | company | hook


@app.post("/admin/messages/{message_id}/subject")
def admin_choose_subject(message_id: str, c: SubjectChoice, p: admin_auth.Principal = Depends(principal),
                         repo: Repo = Depends(get_repo)) -> dict:
    """승인 대기 메일의 제목을 후보 중 하나로 바꾼다. 올려 둔 Slack 승인 카드도 같은 제목으로 갱신된다."""
    try:
        return jobs.choose_subject(message_id, c.option_id, who(None, p), repo=repo)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# --- 피드백 메모리 ---

@app.get("/admin/industries", dependencies=auth)
def admin_industries() -> dict:
    """피드백·소재 분류에 쓰는 업종 목록."""
    return {"industries": industry_mod.industry_list()}


@app.get("/admin/feedback", dependencies=auth)
def admin_feedback_list(scope: str | None = None, status: str = "active", lead_id: str | None = None, limit: int = 200,
                        repo: Repo = Depends(get_repo)) -> dict:
    """피드백 메모. lead_id를 주면 그 고객에게 적용되는 것(고객+업종+전체)을 돌려준다."""
    if status not in ("active", "archived", "all"):
        raise HTTPException(400, "status는 active, archived, all 중 하나입니다")
    if lead_id:
        try:
            lead = repo.get_lead(lead_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        key = industry_mod.classify(lead, repo.list_facts(lead_id))
        notes = feedback_mod.select_for(repo, lead_id, key, limit=50) if status == "active" else \
            repo.list_feedback(scope="lead", scope_key=lead_id, status=status, limit=limit)
    else:
        notes = repo.list_feedback(scope=scope, status=status, limit=min(limit, 500))
    return {"notes": feedback_mod.with_labels(notes, repo)}


class FeedbackIn(BaseModel):
    text: str
    scope: str                      # lead | industry | global
    scope_key: str | None = None    # lead: 리드 id, industry: 업종 키
    kind: str | None = None         # tone | length | topic | avoid | cta | other


class FeedbackPatch(BaseModel):
    text: str | None = None
    scope: str | None = None
    scope_key: str | None = None
    kind: str | None = None
    status: str | None = None       # active | archived


@app.post("/admin/feedback")
def admin_feedback_add(f: FeedbackIn, p: admin_auth.Principal = Depends(principal), repo: Repo = Depends(get_repo)) -> dict:
    try:
        text = feedback_mod.sanitize(f.text)
        scope, key, kind = feedback_mod.validate_note(f.scope, f.scope_key, f.kind)
        if scope == "lead":
            repo.get_lead(key)
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    row = repo.insert_feedback({"scope": scope, "scope_key": key, "kind": kind, "text": text, "source": "manual",
                                "created_by": who(None, p)})
    return feedback_mod.with_labels([row], repo)[0]


@app.patch("/admin/feedback/{feedback_id}", dependencies=auth)
def admin_feedback_patch(feedback_id: str, f: FeedbackPatch, repo: Repo = Depends(get_repo)) -> dict:
    try:
        cur = repo.get_feedback(feedback_id)
        fields: dict = {}
        if f.text is not None:
            fields["text"] = feedback_mod.sanitize(f.text)
        if f.scope is not None or f.scope_key is not None or f.kind is not None:
            scope, key, kind = feedback_mod.validate_note(f.scope or cur["scope"], f.scope_key if f.scope_key is not None
                                                           else (None if (f.scope or cur["scope"]) == "global" else cur["scope_key"]),
                                                           f.kind or cur["kind"])
            fields.update(scope=scope, scope_key=key, kind=kind)
        if f.status is not None:
            if f.status not in ("active", "archived"):
                raise HTTPException(400, "status는 active, archived 중 하나입니다")
            fields["status"] = f.status
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        row = repo.update_feedback(feedback_id, fields)
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return feedback_mod.with_labels([row], repo)[0]


@app.delete("/admin/feedback/{feedback_id}", dependencies=auth)
def admin_feedback_delete(feedback_id: str, repo: Repo = Depends(get_repo)) -> dict:
    try:
        repo.get_feedback(feedback_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    repo.delete_feedback(feedback_id)
    return {"ok": True}


# --- 대화형 에이전트 (관리 화면 우측 패널. Slack은 /webhooks/slack/events) ---

class ChatContext(BaseModel):
    screen: str | None = None
    message_id: str | None = None
    lead_id: str | None = None


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    context: ChatContext | None = None


@app.post("/admin/chat")
def admin_chat(c: ChatIn, p: admin_auth.Principal = Depends(principal), repo: Repo = Depends(get_repo)) -> dict:
    """에이전트에게 말을 건다. 답변과 이번에 실제로 일어난 일(actions)을 돌려준다. 10~60초 걸릴 수 있다 (스트리밍 없음)."""
    by = who(None, p)
    if not webform.allow(f"chat:{by}"):
        raise HTTPException(429, "잠시 후 다시 시도해 주세요")
    try:
        return chat.run_chat(c.message, user=by, channel="web", session_id=c.session_id,
                             context=c.context.model_dump() if c.context else None, repo=repo)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.get("/admin/chat/sessions/{session_id}", dependencies=auth)
def admin_chat_session(session_id: str, repo: Repo = Depends(get_repo)) -> dict:
    try:
        return chat.session_turns(repo, session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


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


class ConsentRecord(BaseModel):
    note: str  # 동의를 어떻게 확인했는지 (예: 세미나 현장 서면 동의)


@app.post("/admin/leads/{lead_id}/consent")
def admin_consent(lead_id: str, c: ConsentRecord, p: admin_auth.Principal = Depends(principal),
                  repo: Repo = Depends(get_repo)) -> dict:
    """담당자가 직접 확인한 수신 동의 근거를 남긴다. REQUIRE_CONSENT_TO_SEND가 켜져 있으면 이 기록이 있어야 발송된다."""
    try:
        return jobs.record_consent(lead_id, c.note, who(None, p), repo=repo)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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
