"""업종 분류와 업종 브리프.

메일 오프닝에 쓸 소재가 회사 단위로 없을 때, 그 회사가 속한 업종의 최근 화두를 쓴다.
업종 뉴스는 리드마다 검색하지 않는다. 업종 키 단위로 모아 industry_briefs에 두고 7일 동안 재사용한다.

분류는 규칙(키워드)으로 한다. 회사 소개·제품·고객층·업종 사실과 회사명만 보고, 뉴스 문장은 보지 않는다
(뉴스에는 다른 업종 단어가 자주 섞인다). 못 정하면 None이고, 이때는 업종 소재를 쓰지 않는다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

BRIEF_TTL_DAYS = 7          # 브리프를 다시 모으기까지의 기간
BRIEF_MAX_AGE_DAYS = 14     # 브리프에 넣는 기사의 최대 나이
USABLE_MAX_AGE_DAYS = 21    # 초안에서 소재 후보로 보여 주는 기사의 최대 나이
MAX_ITEMS = 5

# (키, 이름, 검색어, 키워드)
INDUSTRIES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("semiconductor", "반도체·전자부품", "반도체 업계 동향", ("반도체", "전자부품", "디스플레이", "PCB", "콘덴서", "웨이퍼", "메모리")),
    ("battery", "2차전지·배터리", "2차전지 업계 동향", ("2차전지", "배터리", "양극재", "음극재", "전해질")),
    ("bio_pharma", "바이오·제약·진단", "바이오 제약 업계 동향", ("바이오", "제약", "신약", "진단", "의료기기", "헬스케어", "임상")),
    ("auto_mobility", "자동차·모빌리티", "자동차 모빌리티 업계 동향", ("자동차", "전기차", "모빌리티", "UAM", "차량")),
    ("finance", "금융", "금융권 디지털 고객 서비스 동향", ("은행", "금융", "보험", "증권", "카드", "핀테크")),
    ("retail_commerce", "유통·이커머스", "유통 이커머스 업계 동향", ("유통", "이커머스", "쇼핑", "리테일", "마트", "커머스", "온라인몰")),
    ("game_entertainment", "게임·엔터테인먼트", "게임 엔터테인먼트 업계 동향", ("게임", "엔터", "음악", "콘텐츠", "웹툰", "K팝")),
    ("software_it", "소프트웨어·IT서비스", "소프트웨어 SaaS 업계 동향", ("소프트웨어", "SaaS", "클라우드", "IT서비스", "플랫폼", "솔루션")),
    ("manufacturing", "제조·기계·장비", "제조업 기계 장비 업계 동향", ("기계", "장비", "제조", "설비", "로봇", "산업용")),
    ("construction_realestate", "건설·부동산", "건설 부동산 업계 동향", ("건설", "부동산", "시공", "건축")),
    ("food_consumer", "식품·소비재", "식품 소비재 업계 동향", ("식품", "음료", "화장품", "소비재", "패션", "생활용품", "라면")),
    ("energy_chemical", "에너지·화학·소재", "에너지 화학 업계 동향", ("에너지", "화학", "정유", "철강", "태양광", "수소")),
    ("telecom", "통신·미디어", "통신 미디어 업계 동향", ("통신", "미디어", "방송", "5G", "텔레콤")),
]
LABELS = {k: label for k, label, _, _ in INDUSTRIES}
QUERIES = {k: q for k, _, q, _ in INDUSTRIES}

# 분류에 쓰는 사실 항목과 가중치
_WEIGHTS = {"industry": 3, "summary": 2, "products": 2, "customers": 1}

# 소재로 쓰기 조심스러운 소식. 사실 카드에 sensitive로 표시하고, 초안에서는 소재로 못 쓴다
SENSITIVE_WORDS = ("유출", "해킹", "사고", "소송", "횡령", "배임", "리콜", "구조조정", "감원", "적자", "부실", "제재",
                   "과징금", "압수수색", "논란", "사망", "화재", "중단 명령", "파산", "부도")


def industry_list() -> list[dict]:
    return [{"key": k, "label": label} for k, label, _, _ in INDUSTRIES]


def is_sensitive(text: str | None) -> bool:
    t = text or ""
    return any(w in t for w in SENSITIVE_WORDS)


def classify(lead: dict[str, Any], facts: list[dict]) -> str | None:
    """업종 키. 키워드 점수가 2 미만이면 None."""
    parts: list[tuple[str, int]] = [(lead.get("company_name") or "", 2)]
    for f in facts:
        w = _WEIGHTS.get(f.get("field"))
        if w:
            parts.append((f.get("value") or "", w))
    best, best_score = None, 0
    for key, _, _, words in INDUSTRIES:
        score = sum(w * text.count(word) for text, w in parts for word in words)
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= 2 else None


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def collect_items(key: str, naver: Any = None, google: Any = None) -> list[dict]:
    """업종 검색어로 최근 기사를 모은다. 네이버 우선, 부족하면 Google. 같은 제목은 하나만."""
    query = QUERIES[key]
    items: list[dict] = []
    seen: set[str] = set()
    for client in (naver, google):
        if client is None or len(items) >= MAX_ITEMS:
            continue
        try:
            found = client.search(query, max_age_days=BRIEF_MAX_AGE_DAYS)
        except Exception:
            logger.exception("업종 뉴스 검색 실패: %s", key)
            continue
        for it in found:
            title = (it.get("title") or "").strip()
            if title and title not in seen and it.get("url"):
                seen.add(title)
                items.append({k: it.get(k) for k in ("title", "summary", "url", "publisher", "published_at")})
    items.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    return items[:MAX_ITEMS]


def ensure_brief(repo: Any, key: str, naver: Any = None, google: Any = None, *,
                 now: datetime | None = None) -> dict | None:
    """7일 안에 모은 브리프가 있으면 쓰고, 없으면 새로 모아 저장한다. 실패하면 오래된 브리프(또는 None)."""
    now = now or datetime.now(timezone.utc)
    latest = None
    try:
        latest = repo.latest_industry_brief(key)
        if latest and now - _parse_ts(latest["collected_at"]) < timedelta(days=BRIEF_TTL_DAYS):
            return latest
        items = collect_items(key, naver, google)
        if items:
            return repo.insert_industry_brief(key, items)
    except Exception:
        logger.exception("업종 브리프 준비 실패: %s", key)
    return latest


def usable_items(brief: dict | None, *, today: date | None = None) -> list[dict]:
    """브리프의 기사 중 소재 후보로 쓸 수 있는 것(21일 이내, 민감하지 않은 것)에 로컬 id(ind1…)를 붙여 돌려준다."""
    if not brief:
        return []
    today = today or datetime.now(timezone.utc).date()
    out = []
    for n, it in enumerate(brief.get("items") or [], start=1):
        pub = it.get("published_at")
        if pub:
            try:
                if (today - date.fromisoformat(str(pub)[:10])).days > USABLE_MAX_AGE_DAYS:
                    continue
            except ValueError:
                pass
        if is_sensitive(f"{it.get('title')} {it.get('summary')}"):
            continue
        out.append({"id": f"ind{n}", "brief_id": brief.get("id"), **it})
    return out
