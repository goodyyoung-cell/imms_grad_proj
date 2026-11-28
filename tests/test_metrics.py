"""평가 지표 계산: 승인 시간, 수정 정도, 반려 사유, 수작업 대비 배율."""

from sdr_agent import metrics as mt

T0 = "2026-11-25T01:00:00+00:00"


def at(minutes: float) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(T0) + timedelta(minutes=minutes)).isoformat()


MESSAGES = [
    {"id": "M1", "lead_id": "L1", "kind": "initial", "created_at": at(5)},
    {"id": "M2", "lead_id": "L2", "kind": "initial", "created_at": at(6)},
    {"id": "M3", "lead_id": "L3", "kind": "initial", "created_at": at(7)},
    {"id": "M4", "lead_id": "L1", "kind": "followup", "created_at": at(30)},
]
APPROVALS = [
    {"message_id": "M1", "decision": "approved", "decided_at": at(15), "channel": "admin", "review_seconds": 60},
    {"message_id": "M2", "decision": "edited", "decided_at": at(36), "channel": "slack", "review_seconds": 180,
     "original_body": "안녕하세요 체험 계정을 안내드립니다", "edited_body": "안녕하세요 체험 계정을 안내드립니다 감사합니다"},
    {"message_id": "M3", "decision": "rejected", "decided_at": at(67), "channel": "slack", "reason_code": "factual_error"},
    {"message_id": "M4", "decision": "rejected", "decided_at": at(31), "channel": "admin"},
]
REPLIES = [{"classification": "interested", "handled_by": "agent"}, {"classification": "unclear", "handled_by": "human"},
           {"classification": "interested", "handled_by": "agent"}, {"classification": "unsubscribe", "handled_by": "agent"}]
RUNS = [
    {"workflow": "research", "status": "succeeded", "model": "claude", "started_at": at(0), "finished_at": at(2)},
    {"workflow": "research", "status": "failed", "model": "claude", "started_at": at(10), "finished_at": at(11)},
    {"workflow": "draft", "status": "succeeded", "model": "claude", "started_at": at(3), "finished_at": at(4)},
]
LEADS = [{"id": "L1", "created_at": at(0)}, {"id": "L2", "created_at": at(0)}, {"id": "L3", "created_at": at(1)}, {"id": "L4", "created_at": at(0)}]
TIMINGS = [{"task": "research", "seconds": 900}, {"task": "research", "seconds": 1500}, {"task": "prioritize", "seconds": 300},
           {"task": "draft", "seconds": 600}]


def run():
    return mt.compute(messages=MESSAGES, approvals=APPROVALS, replies=REPLIES, runs=RUNS, leads=LEADS, timings=TIMINGS)


def test_approval_metrics():
    a = run()["approval"]
    assert (a["decisions"], a["approved"], a["edited"], a["rejected"]) == (4, 1, 1, 2)
    assert a["untouched_rate"] == 0.5 and a["reject_rate"] == 0.5
    assert a["wait_median_s"] == 20 * 60  # 대기 시간 1, 10, 30, 60분 → 가운데 두 값(10, 30분)의 평균
    assert a["review_median_s"] == 120 and a["review_n"] == 2
    assert a["by_channel"] == {"admin": 2, "slack": 2}
    assert a["reject_reasons"] == {"factual_error": 1, "unspecified": 1}


def test_wait_median_is_between_the_two_middle_values():
    assert run()["approval"]["wait_median_s"] == 20 * 60


def test_edit_change_ratio():
    assert mt.change_ratio("같은 글", "같은 글") == 0
    assert mt.change_ratio("가나다", "라마바") == 1
    assert 0 < run()["approval"]["edit_change_mean"] < 0.5


def test_reply_and_run_stats():
    m = run()
    assert m["replies"]["by_class"]["interested"] == 2 and m["replies"]["human_rate"] == 0.25
    r = m["runs"]["research"]
    assert r["n"] == 2 and r["success_rate"] == 0.5 and r["median_s"] == 90 and r["models"] == ["claude"]


def test_first_draft_time_counts_only_leads_with_an_initial_mail():
    f = run()["first_draft"]
    assert f["n"] == 3 and f["median_s"] == 6 * 60  # L1 5분, L2 6분, L3 6분. 첫 메일이 없는 L4는 세지 않는다


def test_compare_with_manual_baseline():
    c = {x["task"]: x for x in run()["compare"]}
    assert c["research"]["manual_median_s"] == 1200 and c["research"]["agent_median_s"] == 90
    assert c["research"]["speedup"] == 1200 / 90
    d = c["draft"]
    assert d["review_median_s"] == 60 * 2 and d["ai_assisted_s"] == 60 + 120  # 초안 실행 60초 + 첫 메일 검토(60, 180초)의 중앙값 120초
    assert c["handoff"]["manual_n"] == 0 and c["handoff"]["speedup"] is None


def test_per_lead_needs_all_core_baselines_and_agent_runs():
    p = run()["per_lead"]
    assert p["manual_s"] == 1200 + 300 + 600 and p["ai_s"] == 90 + 180
    assert round(p["manual_per_hour"], 2) == round(3600 / 2100, 2)
    partial = mt.compute(messages=MESSAGES, approvals=APPROVALS, replies=[], runs=RUNS, leads=LEADS, timings=TIMINGS[:2])
    assert partial["per_lead"] is None


def test_empty_data_does_not_crash():
    m = mt.compute(messages=[], approvals=[], replies=[], runs=[], leads=[], timings=[])
    assert m["approval"]["untouched_rate"] is None and m["per_lead"] is None and m["first_draft"]["median_s"] is None
    assert mt.to_rows(m)  # CSV도 만들어진다


def test_formatters():
    assert mt.fmt_secs(None) == "-" and mt.fmt_secs(45) == "45초" and mt.fmt_secs(600) == "10.0분" and mt.fmt_secs(7200) == "2.0시간"
    assert mt.fmt_pct(0.5) == "50%" and mt.fmt_pct(None) == "-"


def test_call_stats_and_timing_stats():
    calls = [{"status": "done", "outcome": "interested", "email_received": True},
             {"status": "done", "outcome": "no_answer", "email_received": None},
             {"status": "pending"}, {"status": "cancelled"}]
    c = mt.call_stats(calls)
    assert (c["total"], c["done"], c["pending"], c["cancelled"]) == (4, 2, 1, 1)
    assert c["outcomes"] == {"interested": 1, "no_answer": 1} and c["email_received_rate"] == 1.0
    assert mt.call_stats([])["email_received_rate"] is None
    m = mt.compute(messages=[], approvals=[], replies=[], runs=[], leads=[], timings=[], calls=calls)
    assert m["calls"]["total"] == 4 and ("전화", "과제 수", 4) in mt.to_rows(m)
    s = mt.timing_stats([{"task": "draft", "seconds": 60}, {"task": "draft", "seconds": 120}])
    assert s["draft"] == {"n": 2, "median": 90.0, "mean": 90.0} and s["research"]["n"] == 0
