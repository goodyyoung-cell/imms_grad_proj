"""리서치 에이전트가 쓰는 도구 모음.

할루시네이션 방지 장치: 이번 실행에서 도구가 실제로 돌려준 URL만 출처로 인정한다.
submit_findings에서 그 밖의 출처를 단 사실은 버리고, 버린 내용을 결과에 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..agent.tools import Tool, ToolRegistry
from ..scoring import score_lead
from .web import fetch_page

FACT_FIELDS = [
    "summary",            # 한 줄 회사 소개
    "products",           # 주요 제품·서비스
    "customers",          # 주요 고객층
    "employees",          # 직원 수
    "revenue",            # 매출
    "operating_margin",   # 영업이익률
    "recent_news",        # 최근 소식·공시
    "talking_point",      # 첫 연락 때 쓸 대화 소재
]


@dataclass
class ResearchContext:
    lead: dict[str, Any]
    dart: Any = None                      # DartClient 또는 None (키 없음)
    naver: Any = None                     # NaverNewsClient 또는 None (키 없음)
    google: Any = None                    # GoogleNewsClient (키 불필요) 또는 None
    fetch: Callable[[str], dict] = fetch_page
    seen_urls: set[str] = field(default_factory=set)
    score: dict[str, Any] | None = None

    def _remember(self, *urls: str | None) -> None:
        self.seen_urls.update(u for u in urls if u)

    # --- 도구 함수 ---

    def fetch_webpage(self, url: str) -> dict:
        page = self.fetch(url)
        self._remember(url, page.get("url"))
        if len(page.get("text", "")) < 100 and not page.get("links"):
            page["note"] = "본문이 거의 없습니다. 자바스크립트로 그리는 페이지일 수 있으니 뉴스나 DART를 쓰세요."
        return page

    def dart_search(self, company_name: str) -> dict:
        if self.dart is None:
            return {"error": "DART API 키가 없어 사용할 수 없습니다. 다른 출처를 쓰세요."}
        hits = self.dart.search_company(company_name)
        return {"candidates": hits, "note": "후보가 없으면 비상장 소기업일 수 있습니다."}

    def dart_company(self, corp_code: str) -> dict:
        if self.dart is None:
            return {"error": "DART를 사용할 수 없습니다."}
        info = self.dart.company(corp_code)
        self._remember(info.get("source_url"))
        return info

    def dart_disclosures(self, corp_code: str) -> dict:
        if self.dart is None:
            return {"error": "DART를 사용할 수 없습니다."}
        items = self.dart.recent_disclosures(corp_code)
        self._remember(*(i["source_url"] for i in items))
        return {"disclosures": items}

    def dart_financials(self, corp_code: str) -> dict:
        if self.dart is None:
            return {"error": "DART를 사용할 수 없습니다."}
        fin = self.dart.key_financials(corp_code)
        self._remember(fin.get("source_url"))
        return fin

    def fetch_company_homepage(self) -> dict:
        domain = (self.lead.get("company_domain") or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
        if not domain:
            return {"error": "리드에 회사 도메인이 없습니다. DART 기업개황의 홈페이지 주소나 뉴스를 쓰세요."}
        candidates = [f"https://{domain}"] + ([] if domain.startswith("www.") else [f"https://www.{domain}"])
        errors = []
        for url in candidates:
            try:
                return self.fetch_webpage(url)
            except Exception as exc:  # 다음 후보로
                errors.append(f"{url}: {type(exc).__name__}")
        return {"error": f"홈페이지를 열지 못했습니다 ({'; '.join(errors)}). 뉴스나 DART를 쓰세요."}

    def _news(self, client: Any, name: str, query: str) -> dict:
        if client is None:
            return {"error": f"{name} 뉴스 검색을 쓸 수 없습니다(키 없음). 다른 뉴스 출처나 홈페이지를 쓰세요."}
        items = client.search(query)
        self._remember(*(i["url"] for i in items))
        if not items:
            return {"articles": [], "note": "최근 1년 안의 기사가 없습니다. 검색어를 바꾸거나 다른 출처를 쓰세요."}
        return {"articles": items, "note": "제목·요약만 있습니다. 사실로 쓸 때는 기사 url을 출처로 달고, "
                                          "회사와 무관한 동명 기사는 버리세요."}

    def naver_news_search(self, query: str) -> dict:
        return self._news(self.naver, "네이버", query)

    def google_news_search(self, query: str) -> dict:
        return self._news(self.google, "Google", query)

    def compute_score(
        self,
        revenue_100m_krw: float | None = None,
        operating_margin_pct: float | None = None,
        recent_disclosure: bool = False,
    ) -> dict:
        result = score_lead(
            source=self.lead.get("source", "other"),
            inbound_at=self.lead["inbound_at"],
            title=self.lead.get("title"),
            revenue_100m_krw=revenue_100m_krw,
            operating_margin_pct=operating_margin_pct,
            recent_disclosure=recent_disclosure,
            channel_count=int(self.lead.get("channel_count", 1)),
        ).as_dict()
        self.score = result
        return result

    def submit_findings(self, facts: list[dict], missing: list[str] | None = None) -> dict:
        accepted, rejected = [], []
        for f in facts:
            url = (f.get("source_url") or "").strip()
            if f.get("field") in FACT_FIELDS and f.get("value") and url in self.seen_urls:
                accepted.append({k: f.get(k) for k in ("field", "value", "source_type", "source_url")})
            else:
                rejected.append({"fact": f, "reason": "출처가 이번 조사에서 확인한 URL이 아니거나 형식이 잘못됨"})
        return {"accepted": accepted, "rejected": rejected, "missing": missing or [], "score": self.score}

    # --- 레지스트리 ---

    def registry(self) -> ToolRegistry:
        corp = {"type": "object", "properties": {"corp_code": {"type": "string"}}, "required": ["corp_code"]}
        query = {"type": "object", "properties": {"query": {"type": "string", "description": "검색어. 보통 회사명, "
                 "필요하면 '회사명 고객센터', '회사명 AI' 처럼 좁힌다"}}, "required": ["query"]}
        return ToolRegistry([
            Tool("fetch_company_homepage", "리드가 남긴 회사 도메인의 홈페이지 첫 화면을 읽는다 (https, www 순으로 시도). "
                 "본문과 같은 사이트 링크 목록을 돌려준다.",
                 {"type": "object", "properties": {}}, self.fetch_company_homepage),
            Tool("fetch_webpage", "웹 페이지 하나를 읽어 제목, 본문(최대 6000자), 같은 사이트의 링크 목록을 돌려준다. "
                 "홈페이지 링크 중 회사 소개·제품·고객사례 페이지를 골라 더 읽거나, 뉴스 원문을 읽을 때 쓴다.",
                 {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, self.fetch_webpage),
            Tool("naver_news_search", "네이버 뉴스에서 최근 1년 기사를 최신순으로 최대 6건 찾는다 (제목, 요약, 언론사, 날짜, 원문 url). "
                 "국내 기업 소식에 강하다.", query, self.naver_news_search),
            Tool("google_news_search", "Google 뉴스에서 최근 1년 기사를 최신순으로 최대 6건 찾는다 (제목, 언론사, 날짜, url). "
                 "네이버에 없는 매체나 해외 보도를 보완한다.", query, self.google_news_search),
            Tool("dart_search", "DART에서 회사명으로 고유번호(corp_code) 후보를 찾는다. 상장·외감 법인 위주라 소기업은 없을 수 있다.",
                 {"type": "object", "properties": {"company_name": {"type": "string"}}, "required": ["company_name"]}, self.dart_search),
            Tool("dart_company", "DART 기업개황: 대표자, 업종, 설립일, 홈페이지 주소.", corp, self.dart_company),
            Tool("dart_disclosures", "DART 최근 6개월 공시 최대 5건 (제목, 날짜, 원문 링크).", corp, self.dart_disclosures),
            Tool("dart_financials", "DART 사업보고서 기준 직전 연도 매출액(억 원)과 영업이익률(%).", corp, self.dart_financials),
            Tool("compute_score", "규칙 기반 우선순위 점수와 근거 태그를 계산한다. 찾은 재무정보와 최근 공시 여부를 넣는다. "
                 "모르는 값은 비워 둔다 (중립 점수 처리).",
                 {"type": "object", "properties": {
                     "revenue_100m_krw": {"type": "number", "description": "매출, 억 원"},
                     "operating_margin_pct": {"type": "number", "description": "영업이익률, %"},
                     "recent_disclosure": {"type": "boolean"},
                 }}, self.compute_score),
            Tool("submit_findings", "조사를 마치고 결과를 제출한다. 각 사실에는 이번 조사에서 도구가 돌려준 URL을 출처로 단다. "
                 "출처가 없는 사실은 제출하지 말고, 찾지 못한 항목은 missing에 적는다.",
                 {"type": "object", "properties": {
                     "facts": {"type": "array", "items": {"type": "object", "properties": {
                         "field": {"type": "string", "enum": FACT_FIELDS},
                         "value": {"type": "string"},
                         "source_type": {"type": "string", "enum": ["dart", "homepage", "news", "lead_record", "other"]},
                         "source_url": {"type": "string"},
                     }, "required": ["field", "value", "source_type", "source_url"]}},
                     "missing": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["facts"]}, self.submit_findings, terminal=True),
        ])
