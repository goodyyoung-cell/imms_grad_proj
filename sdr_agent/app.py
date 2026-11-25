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

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import jobs
from .config import load_settings
from .db import Repo, get_client

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


@app.post("/admin/messages/{message_id}/decision", dependencies=auth)
def admin_decide(message_id: str, d: Decision) -> dict:
    try:
        return jobs.decide_message(message_id, d.decision, d.decided_by, channel="admin",
                                   edited_body=d.edited_body, note=d.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
