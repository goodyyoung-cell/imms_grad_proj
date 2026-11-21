"""규칙 기반 리드 점수.

LLM은 점수를 매기지 않는다. 에이전트는 이 함수를 도구로 호출하고,
돌려받은 근거 태그로 "왜 이 순서인지"를 설명한다.

가중치는 초안이다 (docs/scope.md "아직 정하지 않은 것"). 팀 리뷰 후 조정한다.
만점은 100점이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 인입 유형·온도 (최대 30)
SOURCE_POINTS = {
    "demo_request": (30, "데모 요청"),
    "web_inquiry": (22, "웹 문의"),
    "seminar": (12, "세미나 참석"),
    "business_card": (6, "명함"),
    "other": (6, "기타 유입"),
}

# 경과일: 오래될수록 놓치기 전에 연락하도록 가점 (하루 3점, 최대 15)
DAYS_POINT_PER_DAY = 3
DAYS_POINT_MAX = 15

# 직급 사전 (최대 20). 위에서부터 먼저 맞는 단계를 쓴다
TITLE_LEVELS: list[tuple[int, str, tuple[str, ...]]] = [
    (20, "대표·임원", ("대표", "ceo", "cto", "cfo", "coo", "사장", "부사장", "전무", "상무", "이사", "임원", "본부장", "founder")),
    (15, "팀장·부장", ("팀장", "부장", "실장", "센터장", "head", "director")),
    (10, "과장·차장", ("차장", "과장", "책임", "수석", "선임")),
    (5, "실무자", ("대리", "주임", "사원", "연구원", "staff")),
]
TITLE_UNKNOWN_POINTS = 10  # 매니저·프로·크루 같은 수평 호칭은 중간 점수 + 확인 필요

# 회사 적합도 (최대 20). 재무정보가 없으면 중립 점수
FIT_MAX = 20
FIT_NEUTRAL = 10

# 소식·이력 (최대 15)
RECENT_DISCLOSURE_POINTS = 5
EXTRA_CHANNEL_POINTS = 5
SIGNAL_MAX = 15


@dataclass
class ScoreResult:
    score: float
    tags: list[str] = field(default_factory=list)
    breakdown: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"score": self.score, "tags": self.tags, "breakdown": self.breakdown}


def title_level(title: str | None) -> tuple[int, str | None]:
    """직급 원문을 점수와 단계 이름으로. 모르면 (중간 점수, None)."""
    t = (title or "").strip().lower()
    if t:
        for points, label, keywords in TITLE_LEVELS:
            if any(k in t for k in keywords):
                return points, label
    return TITLE_UNKNOWN_POINTS, None


def _fit_points(revenue_100m_krw: float | None, operating_margin_pct: float | None) -> tuple[float, list[str]]:
    if revenue_100m_krw is None and operating_margin_pct is None:
        return FIT_NEUTRAL, ["재무정보 없음"]
    tags: list[str] = []
    points = 0.0
    # 매출 (최대 12): 10억 미만 3, 100억 미만 7, 그 이상 12
    if revenue_100m_krw is None:
        points += 6
    elif revenue_100m_krw < 10:
        points += 3
    elif revenue_100m_krw < 100:
        points += 7
    else:
        points += 12
        tags.append("매출 100억 이상")
    # 영업이익률 (최대 8): 적자 1, 10% 미만 4, 그 이상 8
    if operating_margin_pct is None:
        points += 4
    elif operating_margin_pct < 0:
        points += 1
        tags.append("영업적자")
    elif operating_margin_pct < 10:
        points += 4
    else:
        points += 8
        tags.append("영업이익률 10% 이상")
    return min(points, FIT_MAX), tags


def score_lead(
    *,
    source: str,
    inbound_at: datetime | str,
    title: str | None = None,
    revenue_100m_krw: float | None = None,
    operating_margin_pct: float | None = None,
    recent_disclosure: bool = False,
    channel_count: int = 1,
    now: datetime | None = None,
) -> ScoreResult:
    now = now or datetime.now(timezone.utc)
    if isinstance(inbound_at, str):
        inbound_at = datetime.fromisoformat(inbound_at.replace("Z", "+00:00"))
    if inbound_at.tzinfo is None:
        inbound_at = inbound_at.replace(tzinfo=timezone.utc)

    tags: list[str] = []
    breakdown: dict[str, float] = {}

    src_points, src_tag = SOURCE_POINTS.get(source, SOURCE_POINTS["other"])
    breakdown["source"] = src_points
    tags.append(src_tag)

    days = max(0, (now - inbound_at).days)
    breakdown["elapsed"] = min(days * DAYS_POINT_PER_DAY, DAYS_POINT_MAX)
    if days >= 1:
        tags.append(f"{days}일 경과")

    t_points, t_label = title_level(title)
    breakdown["title"] = t_points
    tags.append(f"{t_label}급" if t_label else "직급 확인 필요")

    fit, fit_tags = _fit_points(revenue_100m_krw, operating_margin_pct)
    breakdown["fit"] = fit
    tags.extend(fit_tags)

    signal = 0.0
    if recent_disclosure:
        signal += RECENT_DISCLOSURE_POINTS
        tags.append("최근 공시")
    if channel_count > 1:
        signal += EXTRA_CHANNEL_POINTS * (channel_count - 1)
        tags.append(f"{channel_count}개 채널 유입")
    breakdown["signal"] = min(signal, SIGNAL_MAX)

    return ScoreResult(score=round(sum(breakdown.values()), 1), tags=tags, breakdown=breakdown)
