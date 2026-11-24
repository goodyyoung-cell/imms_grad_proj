"""Cloud Run에서 도는 웹 서버.

- Cloud Scheduler가 /jobs/* 를 정해진 시간에 호출한다 (예: 평일 아침 리서치)
- 승인 버튼, 답장 도착 같은 외부 이벤트는 /webhooks/* 로 들어온다

/jobs/* 는 공개 URL이라 JOB_TOKEN으로 보호한다. Cloud Scheduler 작업에
`Authorization: Bearer <JOB_TOKEN>` 헤더를 넣는다.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .config import load_settings
from .jobs import research_leads

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="SDR Agent")


def require_job_token(authorization: str | None = Header(default=None)) -> None:
    expected = load_settings().job_token
    if not expected:
        raise HTTPException(503, "JOB_TOKEN이 설정되지 않았습니다")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "인증 실패")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


class ResearchJob(BaseModel):
    lead_ids: list[str] | None = None
    limit: int = 10
    provider: str | None = None  # "claude" | "gemini". 비우면 DEFAULT_LLM
    trigger: str = "schedule"


@app.post("/jobs/research", dependencies=[Depends(require_job_token)])
def job_research(job: ResearchJob) -> dict:
    results = research_leads(job.lead_ids, limit=job.limit, provider=job.provider, trigger=job.trigger)
    return {"processed": len(results), "results": results}


@app.post("/webhooks/approval")
def webhook_approval() -> dict:
    # 승인 채널(Slack 또는 이메일)이 정해지면 구현한다
    raise HTTPException(501, "승인 채널 미정")
