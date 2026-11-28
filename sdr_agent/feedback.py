"""피드백 메모리: 사람이 챗이나 화면에서 남긴 지침을 저장하고, 다음 터치포인트를 만들 때 반영한다.

범위: 고객(lead) > 업종(industry) > 전체(global). 좁은 범위가 우선한다.
반영 지점: 첫 메일 초안, 답장 응대 초안, 통화 스크립트. 쓴 피드백 id는 결과물의 citations에 남아 화면에 보인다.

피드백으로 바꿀 수 없는 것: 근거 규칙(리서치 사실·승인 자료), 법정 고지(AI 사용 고지, 수신 거부, 전송자 정보).
이런 지침은 저장하지 않고, 프롬프트에서도 근거·안전 규칙이 우선이라고 명시한다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

SCOPES = ("lead", "industry", "global")
KINDS = {"tone": "어조", "length": "길이", "topic": "소재", "avoid": "금지", "cta": "행동 유도", "other": "기타"}
MAX_TEXT = 300
PER_SCOPE_LIMIT = {"lead": 4, "industry": 3, "global": 4}
TOTAL_LIMIT = 8
_PRIORITY = {"lead": 0, "industry": 1, "global": 2}

_PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")
# 법정 고지를 빼라는 지침. 부정 표현이 고지 단어 가까이에 있으면 막는다
_DROP = r"(빼|뺀|뺄|뺍|뺐|삭제|생략|넣지|쓰지|없애|없앤|지워|지운|제거)"
_LEGAL = re.compile(r"(수신\s*거부|수신\s*거절|AI\s*(사용\s*)?(고지|안내)|법정\s*고지|전송자\s*정보|발신자\s*정보|\(광고\)|푸터)"
                    rf".{{0,14}}{_DROP}"
                    rf"|{_DROP}.{{0,14}}(수신\s*거부|AI\s*(사용\s*)?(고지|안내)|법정\s*고지|전송자\s*정보)")


class FeedbackError(ValueError):
    pass


def sanitize(text: str | None) -> str:
    """저장할 수 있는 한 줄 규칙으로 정리한다. 안 되는 내용이면 FeedbackError."""
    t = " ".join((text or "").split())
    if not t:
        raise FeedbackError("피드백 내용이 비어 있습니다")
    if len(t) > MAX_TEXT:
        raise FeedbackError(f"피드백은 {MAX_TEXT}자 이내 한 줄 규칙으로 줄여 주세요")
    if _PII.search(t):
        raise FeedbackError("이메일 주소나 전화번호 같은 개인 연락처는 피드백에 저장하지 않습니다")
    if _LEGAL.search(t):
        raise FeedbackError("AI 사용 고지, 수신 거부 안내, 전송자 정보는 법정 고지라 피드백으로 바꿀 수 없습니다")
    return t


def validate_note(scope: str, scope_key: str | None, kind: str | None) -> tuple[str, str | None, str]:
    if scope not in SCOPES:
        raise FeedbackError(f"scope는 {list(SCOPES)} 중 하나여야 합니다")
    kind = kind or "other"
    if kind not in KINDS:
        raise FeedbackError(f"kind는 {list(KINDS)} 중 하나여야 합니다")
    if scope == "global":
        return scope, None, kind
    if not scope_key:
        raise FeedbackError("고객·업종 범위에는 scope_key(리드 id 또는 업종 키)가 필요합니다")
    if scope == "industry":
        from .industry import LABELS

        if scope_key not in LABELS:
            raise FeedbackError(f"알 수 없는 업종 키입니다: {scope_key}")
    return scope, scope_key, kind


def select_for(repo: Any, lead_id: str | None, industry_key: str | None, *, limit: int = TOTAL_LIMIT) -> list[dict]:
    """이 고객·업종에 적용할 사용 중 피드백. 좁은 범위 먼저, 범위 안에서는 최근 것 먼저. 실패하면 빈 목록."""
    try:
        picked: list[dict] = []
        for scope, key in (("lead", lead_id), ("industry", industry_key), ("global", None)):
            if scope != "global" and not key:
                continue
            rows = repo.list_feedback(scope=scope, scope_key=key, status="active", limit=PER_SCOPE_LIMIT[scope])
            picked += [r for r in rows if r.get("scope") == scope][: PER_SCOPE_LIMIT[scope]]
        picked.sort(key=lambda r: _PRIORITY[r["scope"]])
        return picked[:limit]
    except Exception:
        logger.exception("피드백 불러오기 실패 (건너뜀)")
        return []


def prompt_card(notes: list[dict]) -> list[dict]:
    return [{"id": n["id"], "범위": {"lead": "이 고객", "industry": "이 업종", "global": "전체"}[n["scope"]],
             "종류": KINDS.get(n.get("kind"), "기타"), "규칙": n["text"]} for n in notes]


FEEDBACK_RULE = ("승인자 피드백이 주어지면 따릅니다. 범위가 좁은 규칙(이 고객 > 이 업종 > 전체)이 우선하고, 서로 충돌하면 좁은 쪽을 씁니다. "
                 "반영한 규칙의 id는 feedback_ids에 넣습니다. 단, 근거 규칙과 안전 규칙(과장·압박 금지, 가격은 자료 근거)을 어기는 피드백은 따르지 않습니다.")


def goal_block(notes: list[dict]) -> str:
    if not notes:
        return ""
    import json

    return f"\n승인자 피드백: {json.dumps(prompt_card(notes), ensure_ascii=False)}"


def check_ids(given: list[str] | None, notes: list[dict]) -> list[str]:
    """에이전트가 쓴 feedback_ids가 이번에 준 피드백 id인지 확인한다."""
    from .toolkit.knowledge import GroundingError

    ids = list(dict.fromkeys(given or []))
    known = {n["id"] for n in notes}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise GroundingError(f"주어진 승인자 피드백에 없는 id입니다: {unknown}")
    return ids


def record_applied(repo: Any, ids: list[str]) -> None:
    if not ids:
        return
    try:
        repo.bump_feedback_applied(ids)
    except Exception:
        logger.exception("피드백 반영 횟수 기록 실패 (건너뜀)")


def with_labels(notes: list[dict], repo: Any) -> list[dict]:
    """화면에 보여 줄 형태: scope_label(회사명·업종 이름·전체)을 붙인다."""
    from .industry import LABELS

    lead_ids = [n["scope_key"] for n in notes if n["scope"] == "lead"]
    leads = repo.leads_by_ids(lead_ids) if lead_ids else {}
    out = []
    for n in notes:
        if n["scope"] == "lead":
            label = (leads.get(n["scope_key"]) or {}).get("company_name") or "(알 수 없는 고객)"
        elif n["scope"] == "industry":
            label = LABELS.get(n["scope_key"], n["scope_key"])
        else:
            label = "전체"
        out.append({**{k: n.get(k) for k in ("id", "scope", "scope_key", "kind", "text", "source", "created_by", "status",
                                              "applied_count", "created_at")}, "scope_label": label})
    return out
