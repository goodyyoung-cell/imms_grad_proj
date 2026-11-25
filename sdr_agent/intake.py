"""리드 유입: 정규화, 중복 판정, 저장. 웹 폼과 시트 업로드가 같은 규칙을 쓴다.

중복 규칙 (위에서부터 먼저 맞는 것)
1. 같은 이메일 + 같은 유입 유형   → 이미 있는 리드 (재업로드·중복 제출). 저장하지 않는다
2. 같은 이메일                    → 같은 사람이 다른 경로로 다시 들어옴. 대표 리드에 병합
3. 같은 회사 (도메인 또는 정규화한 회사명) → 같은 회사의 다른 담당자. 대표 리드에 병합
4. 그 외                          → 새 리드

병합된 리드는 merged_into로 대표 리드를 가리키고, 대표 리드만 에이전트가 처리한다.
병합 수는 점수의 '여러 채널 유입' 가점이 된다. 회사당 한 곳에만 연락해 같은 회사에 메일이 몰리지 않게 한다.
수신 거부한 사람이 다시 들어오면 자동으로 되살리지 않고 사람에게 알린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .notify import notify

SOURCES = ("demo_request", "web_inquiry", "seminar", "business_card", "other")
SOURCE_ALIASES = {
    "데모": "demo_request", "데모요청": "demo_request", "데모신청": "demo_request", "demo": "demo_request",
    "웹문의": "web_inquiry", "문의": "web_inquiry", "홈페이지문의": "web_inquiry", "웹사이트": "web_inquiry", "web": "web_inquiry",
    "세미나": "seminar", "웨비나": "seminar", "행사": "seminar", "전시회": "business_card", "명함": "business_card",
    "기타": "other", "소개": "other", "지인소개": "other",
}
# 개인 메일 도메인은 회사 도메인으로 쓰지 않는다
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com", "outlook.com", "hotmail.com",
    "yahoo.com", "yahoo.co.kr", "icloud.com", "me.com", "live.com", "msn.com", "proton.me", "protonmail.com",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CORP_MARKS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(사\)|co\.?,?\s*ltd\.?|inc\.?|corp\.?|corporation|limited", re.I)
LIMITS = {"name": 50, "email": 120, "phone": 30, "company_name": 100, "company_domain": 120, "title": 50,
          "biz_reg_no": 20, "notes": 2000}


def norm_email(value: Any) -> str | None:
    v = str(value or "").strip().lower()
    return v or None


def norm_company(value: Any) -> str:
    v = _CORP_MARKS.sub("", str(value or ""))
    return re.sub(r"[^0-9a-z가-힣]", "", v.lower())


def norm_domain(value: Any) -> str | None:
    v = str(value or "").strip().lower()
    if not v:
        return None
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split("?")[0].split(":")[0]
    v = v.removeprefix("www.")
    return v if "." in v else None


def domain_from_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    d = email.rsplit("@", 1)[1]
    return None if d in PUBLIC_EMAIL_DOMAINS else d


def norm_source(value: Any) -> str:
    v = str(value or "").strip()
    if v in SOURCES:
        return v
    key = re.sub(r"\s+", "", v).lower()
    return SOURCE_ALIASES.get(key, "other")


def norm_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        d = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return d.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    m = re.match(r"^\s*(\d{4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", str(value))
    if not m:
        raise ValueError(f"날짜 형식을 알 수 없습니다: {value}")
    y, mo, d = map(int, m.groups())
    return datetime(y, mo, d, tzinfo=timezone.utc).isoformat()


def clean_lead(raw: dict[str, Any]) -> dict[str, Any]:
    """입력 한 건을 leads 테이블 형식으로. 문제가 있으면 ValueError."""
    company = str(raw.get("company_name") or "").strip()
    if not company:
        raise ValueError("회사명이 없습니다")
    email = norm_email(raw.get("email"))
    if email and not _EMAIL.match(email):
        raise ValueError(f"이메일 형식이 잘못됐습니다: {email}")
    lead = {
        "name": str(raw.get("name") or "").strip() or None,
        "email": email,
        "phone": re.sub(r"[^\d+]", "", str(raw.get("phone") or "")) or None,
        "company_name": company,
        "company_domain": norm_domain(raw.get("company_domain")) or domain_from_email(email),
        "biz_reg_no": re.sub(r"\D", "", str(raw.get("biz_reg_no") or "")) or None,
        "title": str(raw.get("title") or "").strip() or None,
        "source": norm_source(raw.get("source")),
        "notes": str(raw.get("notes") or "").strip() or None,
    }
    inbound = norm_date(raw.get("inbound_at"))
    if inbound:
        lead["inbound_at"] = inbound
    for k, limit in LIMITS.items():
        if lead.get(k) and len(lead[k]) > limit:
            raise ValueError(f"{k}가 너무 깁니다 (최대 {limit}자)")
    return lead


@dataclass
class Match:
    kind: str                 # existing | same_person | same_company | none
    lead: dict | None = None  # 맞은 리드 (병합이면 대표 리드)


def find_match(lead: dict, candidates: list[dict]) -> Match:
    """candidates: 이메일·도메인·회사명이 비슷한 기존 리드들 (대표 리드 여부와 무관)."""
    by_id = {c["id"]: c for c in candidates}

    def root(c: dict) -> dict:
        seen = set()
        while c.get("merged_into") and c["merged_into"] in by_id and c["id"] not in seen:
            seen.add(c["id"])
            c = by_id[c["merged_into"]]
        return c

    email = lead.get("email")
    if email:
        same = [c for c in candidates if norm_email(c.get("email")) == email]
        if any(c.get("source") == lead["source"] for c in same):
            return Match("existing", root(next(c for c in same if c.get("source") == lead["source"])))
        if same:
            return Match("same_person", root(same[0]))
    domain, company = lead.get("company_domain"), norm_company(lead["company_name"])
    for c in candidates:
        if (domain and norm_domain(c.get("company_domain")) == domain) or (company and norm_company(c.get("company_name")) == company):
            return Match("same_company", root(c))
    return Match("none")


@dataclass
class IntakeReport:
    created: list[dict] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)   # 이미 있음
    errors: list[dict] = field(default_factory=list)    # 입력 오류

    def as_dict(self) -> dict:
        return {"created": len(self.created), "merged": len(self.merged), "skipped": len(self.skipped),
                "errors": len(self.errors), "details": {"created": self.created, "merged": self.merged,
                                                         "skipped": self.skipped, "errors": self.errors}}


def ingest(rows: list[dict], repo: Any, *, channel: str, consent_at: str | None = None,
           dry_run: bool = False) -> IntakeReport:
    """여러 건을 순서대로 넣는다. 같은 파일 안의 중복도 앞에서 넣은 리드와 비교해 처리한다."""
    report = IntakeReport()
    staged: list[dict] = []  # dry_run일 때 앞 줄에서 '넣었다고 친' 리드
    for i, raw in enumerate(rows, start=1):
        row_no = raw.pop("_row", i)
        try:
            lead = clean_lead(raw)
        except ValueError as exc:
            report.errors.append({"row": row_no, "error": str(exc)})
            continue
        candidates = repo.find_candidates(lead.get("email"), lead.get("company_domain"), lead["company_name"]) + [
            s for s in staged if _could_match(lead, s)]
        match = find_match(lead, candidates)
        label = {"row": row_no, "company_name": lead["company_name"], "email": lead.get("email")}
        if match.kind == "existing":
            report.skipped.append({**label, "lead_id": match.lead["id"], "reason": "같은 이메일·유입 유형의 리드가 이미 있음"})
            continue
        row = {**lead, "intake_channel": channel, "consent_at": consent_at}
        if match.kind in ("same_person", "same_company"):
            row["merged_into"] = match.lead["id"]
        saved = {**row, "id": f"dry-{row_no}"} if dry_run else repo.insert_lead(row)
        staged.append(saved)
        if match.kind == "none":
            report.created.append({**label, "lead_id": saved["id"]})
            continue
        reason = "같은 사람의 다른 유입 경로" if match.kind == "same_person" else "같은 회사의 다른 담당자"
        report.merged.append({**label, "lead_id": saved["id"], "merged_into": match.lead["id"], "reason": reason})
        if match.lead.get("unsubscribed_at") and match.kind == "same_person" and not dry_run:
            notify("needs_human", f"수신 거부했던 리드가 다시 들어옴: {lead['company_name']}",
                   f"{lead.get('email')} ({lead['source']}). 자동으로 연락을 재개하지 않습니다. 담당자가 확인해 주세요.")
    return report


def _could_match(lead: dict, other: dict) -> bool:
    return bool((lead.get("email") and norm_email(other.get("email")) == lead["email"])
                or (lead.get("company_domain") and norm_domain(other.get("company_domain")) == lead["company_domain"])
                or norm_company(other.get("company_name")) == norm_company(lead["company_name"]))
