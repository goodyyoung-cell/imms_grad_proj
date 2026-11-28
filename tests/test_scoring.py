from datetime import datetime, timedelta, timezone

from sdr_agent.scoring import FIT_NEUTRAL, TITLE_UNKNOWN_POINTS, score_lead, title_level

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)


def test_demo_request_ceo_outranks_business_card_staff():
    hot = score_lead(source="demo_request", inbound_at=NOW, title="대표이사", now=NOW)
    cold = score_lead(source="business_card", inbound_at=NOW, title="사원", now=NOW)
    assert hot.score > cold.score
    assert "데모 요청" in hot.tags and "대표·임원급" in hot.tags


def test_missing_financials_get_neutral_points_and_tag():
    r = score_lead(source="web_inquiry", inbound_at=NOW, title="팀장", now=NOW)
    assert r.breakdown["fit"] == FIT_NEUTRAL
    assert "재무정보 없음" in r.tags


def test_flat_titles_are_middle_and_flagged():
    for t in ["매니저", "프로", "크루", "", None]:
        assert title_level(t) == (TITLE_UNKNOWN_POINTS, None)
    r = score_lead(source="seminar", inbound_at=NOW, title="프로", now=NOW)
    assert "직급 확인 필요" in r.tags


def test_title_levels():
    assert title_level("부사장")[1] == "대표·임원"
    assert title_level("마케팅팀장")[1] == "팀장·부장"
    assert title_level("책임연구원")[1] == "과장·차장"
    assert title_level("대리")[1] == "실무자"


def test_elapsed_days_capped():
    r = score_lead(source="demo_request", inbound_at=NOW - timedelta(days=30), now=NOW)
    assert r.breakdown["elapsed"] == 15
    assert "30일 경과" in r.tags


def test_signals_and_max_score():
    r = score_lead(
        source="demo_request", inbound_at=(NOW - timedelta(days=10)).isoformat(), title="CEO",
        revenue_100m_krw=500, operating_margin_pct=20, recent_disclosure=True, channel_count=4, now=NOW,
    )
    assert r.score == 100
    assert "최근 공시" in r.tags and "4개 채널 유입" in r.tags
