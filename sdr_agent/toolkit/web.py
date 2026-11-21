"""웹 페이지 읽기. 본문 텍스트와 같은 사이트 안의 링크를 돌려준다.

링크 목록을 함께 주는 이유: 어떤 페이지(회사 소개, 제품, 고객사례)를 더 읽을지
에이전트가 스스로 고르게 하기 위해서다.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

import httpx

MAX_TEXT = 6000
MAX_LINKS = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SDR-Agent research prototype)"}

_DROP = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF = re.compile(r"""<a\s[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.S | re.I)


def extract(page_html: str, base_url: str) -> dict:
    title_m = _TITLE.search(page_html)
    title = html.unescape(title_m.group(1)).strip() if title_m else ""
    host = urlparse(base_url).netloc
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, label in _HREF.findall(page_html):
        url = urljoin(base_url, href.strip())
        if urlparse(url).netloc != host or url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        links.append({"url": url, "text": " ".join(html.unescape(_TAG.sub(" ", label)).split())[:60]})
        if len(links) >= MAX_LINKS:
            break
    body = _TAG.sub(" ", _DROP.sub(" ", page_html))
    text = " ".join(html.unescape(body).split())
    return {"url": base_url, "title": title, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT, "links": links}


def fetch_page(url: str, http: httpx.Client | None = None) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    client = http or httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS)
    resp = client.get(url)
    resp.raise_for_status()
    return extract(resp.text, str(resp.url))
