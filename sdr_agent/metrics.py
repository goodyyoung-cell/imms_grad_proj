"""평가용 지표: 승인 화면과 실행 기록에서 자동으로 모은 자료를 계산한다.

계획서 평가 항목 중 이 코드가 다루는 것
- 고객당 처리 시간, 일정 시간 내 처리 가능 고객 수: 에이전트 실행 시간 + 사람이 검토한 시간 대 수작업 시간 (수작업은 POST /admin/baseline 으로 기록)
- 응답의 정확성·일관성: 수정 없이 승인된 비율, 수정한 정도(문자 단위 변화량), 반려 사유
- 인계·답장 처리: 답장 분류 분포, 사람에게 넘어간 비율

순수 함수라서 DB 없이 테스트한다. 실제 값은 가상 데이터로 돌린 시연의 결과이므로 매출 성과로 일반화하지 않는다.
"""

from __future__ import annotations

import difflib
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any

TASKS = {
    "research": "기업 리서치",
    "prioritize": "우선순위 선별",
    "draft": "메일 초안 작성",
    "handoff": "인계 요약",
    "reply_triage": "답장 분류",
}
# 수작업 과제 → 에이전트 실행(workflow). 선별은 리서치 실행 안에서 함께 처리되므로 따로 재지 않는다
TASK_WORKFLOW = {"research": "research", "draft": "draft", "handoff": "handoff", "reply_triage": "reply_triage"}


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _secs(start: Any, end: Any) -> float | None:
    a, b = _dt(start), _dt(end)
    return (b - a).total_seconds() if a and b and b >= a else None


def _med(xs: list[float]) -> float | None:
    return float(median(xs)) if xs else None


def _p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return float(s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))])


def change_ratio(original: str, edited: str) -> float:
    """수정 전 초안 대비 얼마나 바뀌었는지 (0이면 그대로, 1이면 완전히 다름)."""
    return 1.0 - difflib.SequenceMatcher(None, original or "", edited or "", autojunk=False).ratio()


def call_stats(calls: list[dict]) -> dict[str, Any]:
    """전화 팔로업 과제: 상태별 개수와 통화 결과 분포. 수신 확인 응답이 있는 통화 중 메일을 받았다고 한 비율."""
    done = [c for c in calls if c.get("status") == "done"]
    asked = [c for c in done if c.get("email_received") is not None]
    return {
        "total": len(calls), "done": len(done),
        "pending": sum(1 for c in calls if c.get("status") == "pending"),
        "cancelled": sum(1 for c in calls if c.get("status") == "cancelled"),
        "outcomes": dict(Counter(c.get("outcome") for c in done if c.get("outcome"))),
        "email_received_rate": (sum(1 for c in asked if c["email_received"]) / len(asked)) if asked else None,
    }


def compute(*, messages: list[dict], approvals: list[dict], replies: list[dict], runs: list[dict],
            leads: list[dict], timings: list[dict], calls: list[dict] | None = None) -> dict[str, Any]:
    msg_by_id = {m["id"]: m for m in messages}
    decided = [a for a in approvals if a["message_id"] in msg_by_id]

    waits = [w for a in decided if (w := _secs(msg_by_id[a["message_id"]].get("created_at"), a.get("decided_at"))) is not None]
    reviews = [float(a["review_seconds"]) for a in decided if a.get("review_seconds") is not None]
    initial_reviews = [float(a["review_seconds"]) for a in decided if a.get("review_seconds") is not None
                       and msg_by_id[a["message_id"]].get("kind") == "initial"]
    edits = [change_ratio(a["original_body"], a["edited_body"]) for a in decided
             if a["decision"] == "edited" and a.get("original_body") and a.get("edited_body")]
    n_ap = sum(1 for a in decided if a["decision"] == "approved")
    n_ed = sum(1 for a in decided if a["decision"] == "edited")
    n_rj = sum(1 for a in decided if a["decision"] == "rejected")
    approval = {
        "decisions": len(decided), "approved": n_ap, "edited": n_ed, "rejected": n_rj,
        "untouched_rate": n_ap / (n_ap + n_ed) if (n_ap + n_ed) else None,  # 고치지 않고 승인한 비율
        "reject_rate": n_rj / len(decided) if decided else None,
        "wait_median_s": _med(waits), "wait_p90_s": _p90(waits),
        "review_median_s": _med(reviews), "review_n": len(reviews),
        "edit_change_mean": (sum(edits) / len(edits)) if edits else None, "edit_n": len(edits),
        "by_channel": dict(Counter(a.get("channel") for a in decided)),
        "reject_reasons": dict(Counter(a.get("reason_code") or "unspecified" for a in decided if a["decision"] == "rejected")),
    }

    class_count = Counter(r.get("classification") or "unclassified" for r in replies)
    human = sum(1 for r in replies if r.get("handled_by") == "human")
    reply_stats = {"total": len(replies), "by_class": dict(class_count),
                   "human_rate": human / len(replies) if replies else None}

    run_stats: dict[str, dict] = {}
    for wf in sorted({r["workflow"] for r in runs}):
        rs = [r for r in runs if r["workflow"] == wf]
        durs = [d for r in rs if (d := _secs(r.get("started_at"), r.get("finished_at"))) is not None]
        run_stats[wf] = {"n": len(rs), "success_rate": sum(1 for r in rs if r.get("status") == "succeeded") / len(rs),
                         "median_s": _med(durs), "models": sorted({r.get("model") for r in rs if r.get("model")})}

    first = []
    first_msg: dict[str, str] = {}
    for m in sorted(messages, key=lambda m: str(m.get("created_at"))):
        if m.get("kind") == "initial":
            first_msg.setdefault(m["lead_id"], m.get("created_at"))
    for l in leads:
        if l["id"] in first_msg and (d := _secs(l.get("created_at"), first_msg[l["id"]])) is not None:
            first.append(d)

    manual: dict[str, list[float]] = {t: [] for t in TASKS}
    for t in timings:
        if t["task"] in manual:
            manual[t["task"]].append(float(t["seconds"]))
    compare = []
    for task, label in TASKS.items():
        m_med = _med(manual[task])
        agent = run_stats.get(TASK_WORKFLOW.get(task, ""), {}).get("median_s") if task in TASK_WORKFLOW else None
        review = (_med(initial_reviews) or approval["review_median_s"]) if task == "draft" else None
        ai = (agent + (review or 0)) if agent is not None else None
        compare.append({"task": task, "label": label, "manual_n": len(manual[task]), "manual_median_s": m_med,
                        "agent_median_s": agent, "review_median_s": review, "ai_assisted_s": ai,
                        "speedup": (m_med / ai) if (m_med and ai) else None})

    core = [c for c in compare if c["task"] in ("research", "prioritize", "draft")]
    per_lead = None
    if all(c["manual_median_s"] for c in core):
        manual_total = sum(c["manual_median_s"] for c in core)
        ai_parts = [c["ai_assisted_s"] for c in core if c["task"] != "prioritize"]  # 선별은 리서치 실행에 포함
        if all(p is not None for p in ai_parts):
            ai_total = sum(ai_parts)
            per_lead = {"manual_s": manual_total, "ai_s": ai_total, "manual_per_hour": 3600 / manual_total,
                        "ai_per_hour": 3600 / ai_total if ai_total else None}

    return {"approval": approval, "replies": reply_stats, "runs": run_stats, "calls": call_stats(calls or []),
            "first_draft": {"n": len(first), "median_s": _med(first)}, "compare": compare, "per_lead": per_lead,
            "leads": {"total": len(leads), "with_draft": len(first_msg)}}


def timing_stats(timings: list[dict]) -> dict[str, dict]:
    """수작업 기준 시간을 과제별로 요약한다 (건수, 중앙값, 평균)."""
    out: dict[str, dict] = {}
    for task in TASKS:
        xs = [float(t["seconds"]) for t in timings if t["task"] == task]
        out[task] = {"n": len(xs), "median": _med(xs), "mean": (sum(xs) / len(xs)) if xs else None}
    return out


def fmt_secs(x: float | None) -> str:
    if x is None:
        return "-"
    x = int(round(x))
    if x < 90:
        return f"{x}초"
    if x < 5400:
        return f"{x / 60:.1f}분"
    return f"{x / 3600:.1f}시간"


def fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def to_rows(m: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """보고서에 붙일 수 있게 (구분, 지표, 값) 행으로 편다. CSV로 내려받는다."""
    a = m["approval"]
    rows: list[tuple[str, str, Any]] = [
        ("승인", "결정 수", a["decisions"]), ("승인", "그대로 승인", a["approved"]), ("승인", "수정 후 승인", a["edited"]),
        ("승인", "반려", a["rejected"]), ("승인", "수정 없이 승인한 비율", a["untouched_rate"]),
        ("승인", "대기 시간 중앙값(초)", a["wait_median_s"]), ("승인", "대기 시간 90분위(초)", a["wait_p90_s"]),
        ("승인", "사람이 검토한 시간 중앙값(초)", a["review_median_s"]), ("승인", "수정한 정도 평균(0~1)", a["edit_change_mean"]),
    ]
    rows += [("반려 사유", k, v) for k, v in a["reject_reasons"].items()]
    rows += [("승인 경로", k or "-", v) for k, v in a["by_channel"].items()]
    rows += [("답장 분류", k, v) for k, v in m["replies"]["by_class"].items()]
    rows.append(("답장", "사람이 처리한 비율", m["replies"]["human_rate"]))
    for wf, s in m["runs"].items():
        rows += [("에이전트 실행", f"{wf} 횟수", s["n"]), ("에이전트 실행", f"{wf} 성공률", s["success_rate"]),
                 ("에이전트 실행", f"{wf} 소요 시간 중앙값(초)", s["median_s"])]
    rows.append(("처리 속도", "리드 유입에서 첫 초안까지 중앙값(초)", m["first_draft"]["median_s"]))
    c = m.get("calls")
    if c:
        rows += [("전화", "과제 수", c["total"]), ("전화", "통화 완료", c["done"]), ("전화", "대기", c["pending"]),
                 ("전화", "메일 수신 확인 비율", c["email_received_rate"])]
        rows += [("통화 결과", k, v) for k, v in c["outcomes"].items()]
    for c in m["compare"]:
        rows += [("수작업 대비", f"{c['label']} 수작업 중앙값(초)", c["manual_median_s"]),
                 ("수작업 대비", f"{c['label']} 에이전트 실행 중앙값(초)", c["agent_median_s"]),
                 ("수작업 대비", f"{c['label']} 에이전트+검토(초)", c["ai_assisted_s"]),
                 ("수작업 대비", f"{c['label']} 배율(수작업/AI 보조)", c["speedup"])]
    if m["per_lead"]:
        p = m["per_lead"]
        rows += [("처리 속도", "리드 1건 수작업(초)", p["manual_s"]), ("처리 속도", "리드 1건 AI 보조(초)", p["ai_s"]),
                 ("처리 속도", "시간당 처리 리드 수 수작업", p["manual_per_hour"]), ("처리 속도", "시간당 처리 리드 수 AI 보조", p["ai_per_hour"])]
    return rows
