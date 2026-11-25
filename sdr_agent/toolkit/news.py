"""뉴스 검색: 네이버 뉴스(공식 검색 API)와 Google 뉴스(RSS).

- 네이버: https://developers.naver.com 에서 애플리케이션을 등록해 Client ID/Secret을 받는다 (검색 API, 무료 하루 25,000회).
- Google 뉴스: 키 없이 쓰는 공개 RSS. 링크는 news.google.com을 거쳐 원문으로 넘어가는 주소다.

두 출처 모두 같은 형식으로 돌려준다:
  {"title", "summary", "url", "publisher", "published_at", "provider"}
본문 전체가 아니라 제목·요약만 준다. 원문이 더 필요하면 에이전트가 fetch_webpage로 읽는다.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

TIMEOUT = 15
MAX_ITEMS = 6
MAX_AGE_DAYS = 365  # 1년보다 오래된 기사는 '최근 소식'으로 쓰지 않는다

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None, sep: str = " ") -> str:
    return " ".join(html.unescape(_TAG.sub(sep, text or "")).split())


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _recent(items: list[dict], max_age_days: int, now: datetime | None = None) -> list[dict]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    out = []
    for it in items:
        d = it.pop("_dt", None)
        if d is not None and d < cutoff:
            continue
        it["published_at"] = d.date().isoformat() if d else None
        out.append(it)
    return out[:MAX_ITEMS]


class NaverNewsClient:
    URL = "https://openapi.naver.com/v1/search/news.json"
    provider = "naver"

    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self.headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}

    def search(self, query: str, max_age_days: int = MAX_AGE_DAYS) -> list[dict[str, Any]]:
        resp = self.http.get(self.URL, params={"query": query, "display": 20, "sort": "date"}, headers=self.headers)
        resp.raise_for_status()
        items = []
        for it in resp.json().get("items", []):
            url = it.get("originallink") or it.get("link")
            # 네이버는 검색어를 <b>로 감싼다. 인라인 태그라 공백 없이 지운다
            items.append({"title": _clean(it.get("title"), ""), "summary": _clean(it.get("description"), ""), "url": url,
                          "publisher": urlparse(url or "").netloc, "provider": self.provider,
                          "_dt": _parse_date(it.get("pubDate"))})
        return _recent(items, max_age_days)


class GoogleNewsClient:
    URL = "https://news.google.com/rss/search"
    provider = "google"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; SDR-Agent research prototype)"})

    def search(self, query: str, max_age_days: int = MAX_AGE_DAYS) -> list[dict[str, Any]]:
        q = f"{query} when:{max_age_days}d"
        resp = self.http.get(self.URL, params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for it in root.iter("item"):
            title = _clean(it.findtext("title"))
            publisher = _clean(it.findtext("source"))
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3]
            items.append({"title": title, "summary": _clean(it.findtext("description")),
                          "url": (it.findtext("link") or "").strip(), "publisher": publisher,
                          "provider": self.provider, "_dt": _parse_date(it.findtext("pubDate"))})
        items.sort(key=lambda i: i["_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return _recent(items, max_age_days)
