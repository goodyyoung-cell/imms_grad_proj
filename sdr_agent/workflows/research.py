"""모듈 1: 리서치 에이전트.

목표: "이 리드의 카드를 출처 있는 사실로 채워라."
에이전트가 고객사 홈페이지·DART·네이버 뉴스·Google 뉴스 중 무엇을 볼지 스스로 고르고, 빈칸이 남으면 다른 출처를 찾는다.
출처마다 강점이 다르다: 홈페이지(사업·제품·고객), DART(재무·공시, 상장·외감 법인만), 뉴스(최근 소식·대화 소재).
결과는 submit_findings로 제출하며, 이번 실행에서 확인한 URL을 출처로 단 사실만 저장한다.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent import RunLog, run_agent
from ..llm.base import LLM
from ..toolkit.research import FACT_FIELDS, ResearchContext

SYSTEM = """당신은 한국 B2B SaaS 회사의 SDR(영업개발 담당자)을 돕는 리서치 에이전트입니다.
목표는 리드의 회사에 대해 출처가 확인된 사실만 모으는 것입니다.

출처와 쓰임:
- 고객사 홈페이지(fetch_company_homepage → fetch_webpage): 한 줄 소개, 주요 제품·서비스, 주요 고객층. 첫 화면 링크 중 회사 소개·제품·고객사례 페이지를 골라 더 읽습니다.
- DART(dart_search → dart_company/dart_disclosures/dart_financials): 매출, 영업이익률, 최근 공시. 없으면 비상장 소기업일 수 있습니다.
- 뉴스(naver_news_search, google_news_search): 최근 소식과 첫 연락 때 쓸 대화 소재(talking_point). 국내 소식은 네이버를 먼저, 부족하면 Google로 보완합니다.
  고객 응대·디지털 전환·AI 도입·채용·신사업처럼 우리 제품(AI 고객문의 관리)과 이어지는 소식을 우선합니다.

규칙:
- 도구가 실제로 돌려준 내용만 사실로 씁니다. 추측하거나 일반 지식으로 채우지 않습니다.
- 한 번에 여러 도구를 함께 호출해도 됩니다 (예: 홈페이지와 뉴스 검색을 동시에).
- 동명 회사나 무관한 기사는 버립니다. 같은 소식이 여러 기사에 있으면 하나만 씁니다.
- 한 도구가 실패하면 다른 출처를 시도합니다. 같은 실패를 반복하지 않습니다.
- 재무정보와 공시 여부를 확인했으면 compute_score를 호출합니다. 모르는 값은 비워 둡니다.
- 마지막에 반드시 submit_findings를 호출합니다. 찾지 못한 항목은 missing에 적습니다.
- 개인정보(담당자 연락처 등)는 조사하지 않습니다."""


def build_goal(lead: dict[str, Any]) -> str:
    card = {k: lead.get(k) for k in ("company_name", "company_domain", "biz_reg_no", "title", "source", "inbound_at")}
    return (
        "다음 리드의 회사를 조사해 카드를 채우세요.\n"
        f"리드 정보: {json.dumps(card, ensure_ascii=False, default=str)}\n"
        f"채울 항목: {', '.join(FACT_FIELDS)}"
    )


def run_research(
    lead: dict[str, Any],
    llm: LLM,
    log: RunLog,
    *,
    dart: Any = None,
    naver: Any = None,
    google: Any = None,
    trigger: str = "manual",
    max_steps: int = 12,
    fetch=None,
) -> dict[str, Any]:
    ctx = ResearchContext(lead=lead, dart=dart, naver=naver, google=google, **({"fetch": fetch} if fetch else {}))
    result = run_agent(
        llm, SYSTEM, build_goal(lead), ctx.registry(), log,
        workflow="research", trigger=trigger, lead_id=lead.get("id"), max_steps=max_steps,
    )
    findings = result.terminal_output or {"accepted": [], "rejected": [], "missing": FACT_FIELDS, "score": None}
    # 에이전트가 점수 도구를 부르지 않았으면 리드 정보만으로 계산한다 (재무는 중립 처리)
    if findings.get("score") is None:
        findings["score"] = ctx.score or ctx.compute_score()
    return {
        "run_id": log.run_id,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "facts": findings["accepted"],
        "rejected": findings["rejected"],
        "missing": findings.get("missing", []),
        "score": findings["score"],
    }
