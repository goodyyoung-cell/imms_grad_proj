"""웹 페이지 읽기. 본문 텍스트와 같은 사이트 안의 링크를 돌려준다.

링크 목록을 함께 주는 이유: 어떤 페이지(회사 소개, 제품, 고객사례)를 더 읽을지
에이전트가 스스로 고르게 하기 위해서다.
"""

from __future__ import annotations

import html
import re
import ssl
from urllib.parse import urljoin, urlparse

import httpx

MAX_TEXT = 6000
MAX_LINKS = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SDR-Agent research prototype)"}

_DROP = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF = re.compile(r"""<a\s[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.S | re.I)


def _site(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def extract(page_html: str, base_url: str) -> dict:
    title_m = _TITLE.search(page_html)
    title = html.unescape(title_m.group(1)).strip() if title_m else ""
    host = _site(base_url)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, label in _HREF.findall(page_html):
        url = urljoin(base_url, href.strip())
        if _site(url) != host or url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        links.append({"url": url, "text": " ".join(html.unescape(_TAG.sub(" ", label)).split())[:60]})
        if len(links) >= MAX_LINKS:
            break
    body = _TAG.sub(" ", _DROP.sub(" ", page_html))
    text = " ".join(html.unescape(body).split())
    return {"url": base_url, "title": title, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT, "links": links}


# 본문이 거의 없는 첫 화면이 자바스크립트나 meta refresh로 다른 경로로 보내는 경우 (예: "/" → "/ko/main")
_META_REFRESH = re.compile(r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*url=([^"'>]+)""", re.I)
_JS_REDIRECT = re.compile(r"""(?:location(?:\.href)?|\bhref)\s*=\s*["']([^"']+)["']|location\.replace\(\s*["']([^"']+)["']""")
THIN_TEXT = 200


_SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)
_ASSET = re.compile(r"\.(ico|css|js|png|jpe?g|gif|svg|webp|woff2?)(\?|$)", re.I)


def redirect_target(page_html: str, base_url: str) -> str | None:
    m = _META_REFRESH.search(page_html)
    candidates = [m.group(1)] if m else []
    for script in _SCRIPT.findall(page_html):  # HTML 속성(href=...)이 아니라 스크립트 안에서만 찾는다
        candidates += [a or b for a, b in _JS_REDIRECT.findall(script)]
    for c in candidates:
        url = urljoin(base_url, c.strip())
        if (url.startswith("http") and _site(url) == _site(base_url) and not _ASSET.search(url)
                and url.rstrip("/") != base_url.rstrip("/")):
            return url
    return None


def _legacy_tls_client() -> httpx.Client:
    """오래된 TLS 설정의 공개 홈페이지용. 읽기 전용 공개 페이지에만 쓴다."""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS, verify=ctx)


def _get(client: httpx.Client, url: str) -> httpx.Response:
    try:
        return client.get(url)
    except httpx.ConnectError as exc:
        if "SSL" not in str(exc) and "handshake" not in str(exc).lower():
            raise
        with _legacy_tls_client() as legacy:
            return legacy.get(url)


def fetch_page(url: str, http: httpx.Client | None = None) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    client = http or httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS)
    resp = _get(client, url)
    if resp.status_code in (401, 403, 429):
        raise PermissionError(f"사이트가 자동 접근을 막았습니다 (HTTP {resp.status_code}). 우회하지 말고 다른 출처를 쓰세요.")
    resp.raise_for_status()
    page = extract(resp.text, str(resp.url))
    if len(page["text"]) < THIN_TEXT and not page["links"]:
        target = redirect_target(resp.text, str(resp.url))
        if target:
            nxt = _get(client, target)
            if nxt.status_code < 400:
                return extract(nxt.text, str(nxt.url))
    return page
