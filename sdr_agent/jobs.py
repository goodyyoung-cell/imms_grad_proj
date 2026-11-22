"""이벤트·예약 실행이 부르는 작업 단위. 웹 서버(app.py)와 CLI(cli.py)가 함께 쓴다."""

from __future__ import annotations

import logging
from typing import Any

from .agent import SupabaseRunLog
from .config import Settings, load_settings
from .db import Repo, get_client
from .llm import get_llm
from .toolkit.dart import DartClient
from .workflows.research import run_research

logger = logging.getLogger(__name__)


def research_leads(
    lead_ids: list[str] | None = None,
    *,
    limit: int = 10,
    provider: str | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """리드를 하나씩 조사해 결과를 저장한다. lead_ids가 없으면 상태가 new인 리드를 오래된 순으로."""
    s = settings or load_settings()
    client = get_client(s)
    repo = Repo(client)
    ids = lead_ids or repo.list_lead_ids("new", limit)
    if not ids:
        return []  # 처리할 리드가 없으면 LLM 키 없이도 정상 종료 (첫 배포 점검용)
    llm = get_llm(provider, s)
    dart = DartClient(s.dart_api_key) if s.dart_api_key else None

    out: list[dict[str, Any]] = []
    for lead_id in ids:
        repo.set_status(lead_id, "researching")
        try:
            lead = repo.get_lead(lead_id)
            log = SupabaseRunLog(client)
            result = run_research(lead, llm, log, dart=dart, trigger=trigger, max_steps=s.agent_max_steps)
            repo.save_research(lead_id, result["run_id"], result["facts"], result["score"])
            repo.set_status(lead_id, "researched")
            out.append({"lead_id": lead_id, **{k: result[k] for k in ("run_id", "status", "stop_reason", "score")},
                        "facts": len(result["facts"]), "rejected": len(result["rejected"])})
        except Exception as exc:
            logger.exception("리서치 실패: %s", lead_id)
            repo.set_status(lead_id, "new")  # 다음 실행에서 다시 시도
            out.append({"lead_id": lead_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return out
