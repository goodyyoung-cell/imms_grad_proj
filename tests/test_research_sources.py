"""리서치 출처: 고객사 홈페이지, 네이버 뉴스, Google 뉴스. 네트워크 없이 가짜 응답으로 검증한다."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

from sdr_agent.agent import MemoryRunLog
from sdr_agent.llm.base import LLMResponse, ToolCall
from sdr_agent.toolkit.news import GoogleNewsClient, NaverNewsClient
from sdr_agent.toolkit.research import ResearchContext
from sdr_agent.toolkit.web import fetch_page, redirect_target
from sdr_agent.workflows.research import run_research
from tests.test_research_agent import LEAD, ScriptedLLM

NOW = datetime.now(timezone.utc)
RECENT = format_datetime(NOW - timedelta(days=3))
OLD = format_datetime(NOW - timedelta(days=800))


def mock_http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_naver_parses_strips_tags_and_drops_old_articles():
    def handler(req):
        assert str(req.url).startswith("https://naverapihub.apigw.ntruss.com/search/v1/news")
        assert req.headers["X-NCP-APIGW-API-KEY-ID"] == "id" and req.headers["X-NCP-APIGW-API-KEY"] == "secret"
        return httpx.Response(200, json={"items": [
            {"title": "<b>예시SaaS</b>, AI 상담 도입", "description": "고객센터에 &quot;AI&quot; 적용",
             "originallink": "https://news.example/a1", "link": "https://n.news.naver.com/a1", "pubDate": RECENT},
            {"title": "오래된 기사", "description": "", "originallink": "https://news.example/old", "pubDate": OLD},
        ]})

    items = NaverNewsClient("id", "secret", http=mock_http(handler)).search("예시SaaS")
    assert len(items) == 1
    assert items[0]["title"] == "예시SaaS, AI 상담 도입"
    assert items[0]["summary"] == '고객센터에 "AI" 적용'
    assert items[0]["url"] == "https://news.example/a1" and items[0]["provider"] == "naver"


def test_google_rss_parses_and_removes_publisher_suffix():
    rss = f"""<?xml version="1.0"?><rss><channel>
      <item><title>예시SaaS 신규 채용 - 가상일보</title><link>https://news.google.com/rss/articles/x1</link>
        <pubDate>{RECENT}</pubDate><source url="https://gasang.example">가상일보</source></item>
    </channel></rss>"""
    items = GoogleNewsClient(http=mock_http(lambda req: httpx.Response(200, text=rss))).search("예시SaaS")
    assert items == [{"title": "예시SaaS 신규 채용", "summary": "", "url": "https://news.google.com/rss/articles/x1",
                      "publisher": "가상일보", "provider": "google", "published_at": (NOW - timedelta(days=3)).date().isoformat()}]


def test_homepage_tries_www_after_failure():
    calls = []

    def fetch(url):
        calls.append(url)
        if "www." not in url:
            raise httpx.ConnectError("no")
        return {"url": url, "title": "t", "text": "회사 소개 " * 30, "links": []}

    ctx = ResearchContext(lead={**LEAD, "company_domain": "example-saas.kr"}, fetch=fetch)
    page = ctx.fetch_company_homepage()
    assert calls == ["https://example-saas.kr", "https://www.example-saas.kr"]
    assert "https://www.example-saas.kr" in ctx.seen_urls and "error" not in page


def test_js_redirect_found_only_inside_scripts():
    page = """<html><head><link rel="icon" href="/favicon.ico"><script>var href = "/ko/main"; location.href = href;</script>
              </head><body></body></html>"""
    assert redirect_target(page, "https://a.example/") == "https://a.example/ko/main"
    assert redirect_target('<link href="/favicon.ico">', "https://a.example/") is None


def test_blocked_site_raises_permission_error():
    http = mock_http(lambda req: httpx.Response(403, text="Access Denied"))
    try:
        fetch_page("https://blocked.example", http=http)
        assert False
    except PermissionError as exc:
        assert "우회하지" in str(exc)


class FakeNews:
    def __init__(self, provider, items):
        self.provider, self.items = provider, items

    def search(self, query):
        return [dict(i) for i in self.items]


def test_agent_uses_news_and_can_cite_article_urls():
    google = FakeNews("google", [{"title": "예시SaaS, 고객센터 AI 도입 검토", "summary": "", "provider": "google",
                                  "url": "https://news.google.com/rss/articles/x1", "publisher": "가상일보",
                                  "published_at": "2026-11-20"}])
    llm = ScriptedLLM([
        LLMResponse("홈페이지와 뉴스를 함께 봅니다", [
            ToolCall("c1", "fetch_company_homepage", {}),
            ToolCall("c2", "naver_news_search", {"query": "예시SaaS"}),
            ToolCall("c3", "google_news_search", {"query": "예시SaaS"}),
        ]),
        LLMResponse("제출", [ToolCall("c4", "submit_findings", {"facts": [
            {"field": "recent_news", "value": "고객센터 AI 도입 검토 (가상일보, 2026-11-20)", "source_type": "news",
             "source_url": "https://news.google.com/rss/articles/x1"},
            {"field": "talking_point", "value": "AI 도입 검토 소식", "source_type": "news",
             "source_url": "https://news.google.com/rss/articles/made-up"},
        ]})]),
    ])

    def fetch(url):
        return {"url": url, "title": "예시", "text": "중소기업용 회계 SaaS " * 10, "links": []}

    lead = {**LEAD, "company_domain": "example-saas.kr"}
    out = run_research(lead, llm, MemoryRunLog(), naver=None, google=google, fetch=fetch)
    assert [f["field"] for f in out["facts"]] == ["recent_news"]   # 검색 결과에 없던 기사 url은 버린다
    assert len(out["rejected"]) == 1
    # 네이버 키가 없다는 사실이 LLM에게 전달됐다 (Google로 보완할 근거)
    naver_result = llm.seen[1][-1].tool_results[1]
    assert naver_result.name == "naver_news_search" and "키 없음" in naver_result.content["error"]


def test_naver_legacy_mode_and_hub_error_message():
    def legacy(req):
        assert "openapi.naver.com" in str(req.url) and req.headers["X-Naver-Client-Id"] == "id"
        return httpx.Response(200, json={"items": []})

    assert NaverNewsClient("id", "secret", http=mock_http(legacy), api="legacy").search("x") == []

    def hub_error(req):
        return httpx.Response(401, json={"error": {"errorCode": "200", "message": "Authentication Failed"}})

    try:
        NaverNewsClient("id", "bad", http=mock_http(hub_error)).search("x")
        raise AssertionError("오류가 나야 합니다")
    except RuntimeError as exc:
        assert "HTTP 401" in str(exc) and "Authentication Failed" in str(exc)


def test_won_text_formats_eok_amounts():
    from sdr_agent.toolkit.dart import won_text

    assert won_text(49658.0) == "4조 9,658억 원"
    assert won_text(9658.4) == "9,658억 원"
    assert won_text(20000) == "2조 원"
    assert won_text(-350.2) == "-350억 원"
    assert won_text(None) is None
