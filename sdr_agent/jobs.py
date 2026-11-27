"""이벤트·예약 실행이 부르는 작업 단위. 웹 서버(app.py)와 CLI(cli.py)가 함께 쓴다.

핵심 루프
  research_leads  (new → researched)            자율
  draft_emails    (researched → awaiting_approval) 자율, 초안만
  decide_message  사람 승인 → send_message (샌드박스 발송, → contacted)
                  첫 메일이면 create_call_task: 발송 1시간 뒤 전화 팔로업 과제 + 콜 스크립트 (자율, 전화는 사람이)
  record_call_outcome  통화 결과 기록 → 인계 / 수신 거부 / 사람 확인
  handle_reply    답장 → 수신 거부 중단 / 사람 이관 / 답변 초안(승인 대기) / 인계
  handoff_lead    인계 요약 (→ handed_off)
  notify_due_calls  전화 시각이 된 콜 과제를 Slack에 한 번씩 알림
  remind_pending  오래 기다린 승인 대기를 Slack에 알림

테스트에서는 repo, llm, new_log, sender를 가짜로 넣는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .agent import MemoryRunLog, RunLog, SupabaseRunLog
from .config import Settings, load_settings
from .compliance import apply_footer, finalize
from .db import Repo, get_client
from .intake import ingest
from .llm import get_llm
from .llm.base import LLM
from .notify import notify
from .outbox import Sender, get_sender
from .toolkit.dart import DartClient
from .toolkit.news import GoogleNewsClient, NaverNewsClient
from .workflows.draft import compose_body, run_draft
from .workflows.call_script import render_text as render_call_script
from .workflows.call_script import run_call_script, sent_phrase
from .workflows.handoff import render_text, run_handoff
from .workflows.reply import detect_unsubscribe, run_reply_triage
from .workflows.research import run_research

logger = logging.getLogger(__name__)

NewLog = Callable[[], RunLog]

# 반려 사유. 어떤 오류가 많은지 세어 평가와 프롬프트 개선에 쓴다
REASON_CODES = {
    "factual_error": "사실과 다름",
    "tone": "어조가 맞지 않음",
    "wrong_target": "연락하면 안 되는 대상",
    "forbidden_phrase": "금지 표현·과장",
    "other": "기타",
}


def _wire(s: Settings, repo: Any, new_log: NewLog | None) -> tuple[Any, NewLog]:
    if repo is None:
        client = get_client(s)
        return Repo(client), new_log or (lambda: SupabaseRunLog(client))
    if new_log is None and isinstance(repo, Repo):
        # 실제 DB를 쓰면 실행 로그도 DB에 남긴다 (메모리 로그의 가짜 run_id가 DB에 들어가지 않게)
        return repo, (lambda: SupabaseRunLog(repo.c))
    return repo, new_log or MemoryRunLog


def _approval_data(lead: dict[str, Any], msg: dict[str, Any], score: dict | None,
                   customer_reply: str | None = None) -> dict[str, Any]:
    """승인 요청 알림(Slack)에 싣는 내용."""
    return {"company": lead.get("company_name"), "name": lead.get("name"), "title": lead.get("title"),
            "subject": msg.get("subject"), "body": msg.get("body"), "kind": msg.get("kind"),
            "score": (score or {}).get("score"), "tags": (score or {}).get("tags") or [],
            "customer_reply": customer_reply, "no_consent": not lead.get("consent_at")}


def intake_leads(
    rows: list[dict[str, Any]],
    *,
    channel: str,
    consent_at: str | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
    repo: Any = None,
) -> dict[str, Any]:
    """웹 폼·시트 업로드로 들어온 리드를 정규화·중복 병합해 저장한다.

    새 리드는 상태 new로 들어가고, 예약 실행(/jobs/research)이 오래된 순으로 조사한다.
    """
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    report = ingest(rows, repo, channel=channel, consent_at=consent_at, dry_run=dry_run).as_dict()
    if not dry_run and (report["created"] or report["merged"]):
        notify("intake", f"리드 유입 ({channel}): 신규 {report['created']}건, 병합 {report['merged']}건",
               f"건너뜀 {report['skipped']}건, 오류 {report['errors']}건")
    return {"dry_run": dry_run, **report}


def research_leads(
    lead_ids: list[str] | None = None,
    *,
    limit: int = 10,
    provider: str | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> list[dict[str, Any]]:
    """리드를 하나씩 조사해 결과를 저장한다. lead_ids가 없으면 상태가 new인 리드를 오래된 순으로."""
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    ids = lead_ids or repo.list_lead_ids("new", limit)
    if not ids:
        return []  # 처리할 리드가 없으면 LLM 키 없이도 정상 종료 (첫 배포 점검용)
    llm = llm or get_llm(provider, s)
    dart = DartClient(s.dart_api_key) if s.dart_api_key else None
    naver = (NaverNewsClient(s.naver_client_id, s.naver_client_secret, api=s.naver_search_api)
             if s.naver_client_id and s.naver_client_secret else None)
    google = GoogleNewsClient() if s.google_news_enabled else None

    out: list[dict[str, Any]] = []
    for lead_id in ids:
        repo.set_status(lead_id, "researching")
        try:
            lead = repo.get_lead(lead_id)
            log = new_log()
            result = run_research(lead, llm, log, dart=dart, naver=naver, google=google, trigger=trigger,
                                  max_steps=s.agent_max_steps)
            repo.save_research(lead_id, result["run_id"], result["facts"], result["score"])
            repo.set_status(lead_id, "researched")
            out.append({"lead_id": lead_id, **{k: result[k] for k in ("run_id", "status", "stop_reason", "score")},
                        "facts": len(result["facts"]), "rejected": len(result["rejected"])})
        except Exception as exc:
            logger.exception("리서치 실패: %s", lead_id)
            repo.set_status(lead_id, "new")  # 다음 실행에서 다시 시도
            out.append({"lead_id": lead_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return out


def draft_emails(
    lead_ids: list[str] | None = None,
    *,
    limit: int = 10,
    provider: str | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> list[dict[str, Any]]:
    """리서치가 끝난 리드에게 보낼 첫 메일 초안을 만들어 승인 대기로 둔다. 발송하지 않는다."""
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    ids = lead_ids or repo.list_lead_ids("researched", limit)
    if not ids:
        return []
    llm = llm or get_llm(provider, s)

    out: list[dict[str, Any]] = []
    for lead_id in ids:
        try:
            lead = repo.get_lead(lead_id)
            if lead.get("unsubscribed_at"):
                out.append({"lead_id": lead_id, "status": "skipped", "reason": "수신 거부"})
                continue
            if any(m["kind"] == "initial" and m["status"] != "rejected" for m in repo.list_messages(lead_id)):
                out.append({"lead_id": lead_id, "status": "skipped", "reason": "이미 첫 메일 초안이 있음"})
                continue
            log = new_log()
            result = run_draft(lead, repo.list_facts(lead_id), repo.latest_score(lead_id), llm, log,
                               booking_url=s.booking_url, trigger=trigger)
            if not result["draft"]:
                out.append({"lead_id": lead_id, "status": "no_draft", "run_id": result["run_id"],
                            "stop_reason": result["stop_reason"]})
                continue
            subject, body = finalize(result["draft"]["subject"], result["draft"]["body"], s, lead_id)
            msg = repo.insert_message(lead_id, result["run_id"], "initial", {**result["draft"], "subject": subject, "body": body})
            repo.set_status(lead_id, "awaiting_approval")
            notify("approval", f"메일 승인 요청: {lead.get('company_name')} ({msg['id']})",
                   f"제목: {msg['subject']}\n\n{msg['body']}", message_id=msg["id"], lead_id=lead_id,
                   data=_approval_data(lead, msg, repo.latest_score(lead_id)))
            out.append({"lead_id": lead_id, "status": "drafted", "message_id": msg["id"], "run_id": result["run_id"]})
        except Exception as exc:
            logger.exception("초안 실패: %s", lead_id)
            out.append({"lead_id": lead_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return out


def decide_message(
    message_id: str,
    decision: str,
    decided_by: str,
    *,
    channel: str = "admin",
    edited_body: str | None = None,
    note: str | None = None,
    reason_code: str | None = None,
    review_seconds: int | None = None,
    settings: Settings | None = None,
    repo: Any = None,
    sender: Sender | None = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> dict[str, Any]:
    """사람의 승인 결정을 기록한다. 승인(또는 수정 승인)이면 바로 샌드박스로 발송한다.
    첫 메일이 나가면 전화 팔로업 과제와 콜 스크립트를 만든다.

    평가용으로 반려 사유(reason_code), 수정 승인이면 수정 전 초안, 사람이 검토에 쓴 시간(review_seconds)도 남긴다."""
    if decision not in ("approved", "rejected", "edited"):
        raise ValueError("decision은 approved, rejected, edited 중 하나입니다")
    if decision == "edited" and not (edited_body and edited_body.strip()):
        raise ValueError("edited에는 수정한 본문이 필요합니다")
    if reason_code and reason_code not in REASON_CODES:
        raise ValueError(f"알 수 없는 반려 사유입니다: {reason_code}")
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    msg = repo.get_message(message_id)
    if msg["status"] != "pending_approval":
        raise ValueError(f"승인 대기 중인 메시지가 아닙니다 (status={msg['status']})")

    approval: dict[str, Any] = {"message_id": message_id, "decision": decision, "decided_by": decided_by,
                                "channel": channel, "edited_body": edited_body, "note": note}
    if decision == "rejected" and reason_code:
        approval["reason_code"] = reason_code
    if decision == "edited":
        approval["original_body"] = msg["body"]
    if review_seconds is not None:
        approval["review_seconds"] = max(0, min(int(review_seconds), 3600))
    repo.insert_approval(approval)
    if decision == "rejected":
        repo.update_message(message_id, {"status": "rejected"})
        repo.set_status(msg["lead_id"], "needs_human")
        return {"message_id": message_id, "status": "rejected"}

    fields: dict[str, Any] = {"status": "approved"}
    if decision == "edited":
        # 사람이 고쳐도 AI 사용 고지, 수신 거부 안내, 전송자 정보는 빠질 수 없다
        fields["body"] = apply_footer(edited_body, s, msg["lead_id"])
    repo.update_message(message_id, fields)
    out = send_message(message_id, repo=repo, sender=sender, settings=s)
    if out["status"] == "sent" and msg["kind"] == "initial":
        try:
            out["call_task"] = create_call_task(message_id, settings=s, repo=repo, llm=llm, new_log=new_log)
        except Exception as exc:  # 콜 스크립트 실패가 발송 결과를 바꾸지 않게
            logger.exception("콜 과제 생성 실패: %s", message_id)
            out["call_task"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return out


def send_message(message_id: str, *, repo: Any, sender: Sender | None = None,
                 settings: Settings | None = None) -> dict[str, Any]:
    """승인된 메시지를 보낸다. 수신 거부·미승인은 여기서 한 번, DB 트리거에서 한 번 더 막는다."""
    s = settings or load_settings()
    msg = repo.get_message(message_id)
    lead = repo.get_lead(msg["lead_id"])
    if lead.get("unsubscribed_at"):
        repo.update_message(message_id, {"status": "cancelled"})
        return {"message_id": message_id, "status": "cancelled", "reason": "수신 거부"}
    if msg["status"] != "approved":
        raise ValueError(f"승인되지 않은 메시지는 보낼 수 없습니다 (status={msg['status']})")
    if s.require_consent_to_send and not lead.get("consent_at"):
        repo.update_message(message_id, {"status": "cancelled"})
        return {"message_id": message_id, "status": "cancelled", "reason": "수신 동의 기록 없음"}
    if not lead.get("email"):
        raise ValueError("리드에 이메일 주소가 없습니다")
    if s.max_sends_per_day > 0 and repo.count_sent_since(_start_of_day_utc()) >= s.max_sends_per_day:
        raise ValueError(f"오늘 발송 한도({s.max_sends_per_day}건)에 도달했습니다. 내일 다시 보내거나 한도를 조정하세요.")
    provider_id = (sender or get_sender()).send(lead["email"], msg["subject"], msg["body"])
    repo.update_message(message_id, {"status": "sent", "provider_message_id": provider_id})
    repo.set_status(msg["lead_id"], "contacted")
    return {"message_id": message_id, "status": "sent", "provider_message_id": provider_id}


def _start_of_day_utc() -> str:
    """하루 발송 상한을 세는 기준 (한국 시간 자정)."""
    return datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def record_consent(lead_id: str, note: str, recorded_by: str, *, repo: Any = None,
                   settings: Settings | None = None) -> dict:
    """담당자가 직접 확인한 수신 동의를 남긴다 (예: 세미나 현장 서면 동의). 근거 메모가 필수다."""
    if not (note and note.strip()):
        raise ValueError("동의를 어떻게 확인했는지 적어 주세요")
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    repo.get_lead(lead_id)
    stamp = f"{note.strip()} (기록: {recorded_by})"
    repo.update_lead(lead_id, {"consent_at": datetime.now(timezone.utc).isoformat(), "consent_note": stamp})
    return {"lead_id": lead_id, "consent_note": stamp}


def handle_reply(
    body: str,
    *,
    lead_id: str | None = None,
    from_email: str | None = None,
    message_id: str | None = None,
    provider: str | None = None,
    trigger: str = "reply",
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> dict[str, Any]:
    """답장을 저장하고 분류해 다음 행동을 실행한다."""
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    if lead_id:
        lead = repo.get_lead(lead_id)
    elif from_email:
        lead = repo.find_lead_by_email(from_email)
        if not lead:
            raise LookupError(f"이 주소의 리드가 없습니다: {from_email}")
    else:
        raise ValueError("lead_id 또는 from_email이 필요합니다")
    lead_id = lead["id"]

    sent = [m for m in repo.list_messages(lead_id) if m["status"] == "sent"]
    last = sent[-1] if sent else None
    # 수신 거부는 규칙으로 먼저 본다. 걸리면 LLM 키가 없어도 멈춘다
    if detect_unsubscribe(body):
        llm, log = None, None
    else:
        llm = llm or get_llm(provider, s)
        log = new_log()
    triage = run_reply_triage(lead, body, llm, log, last_message=last, trigger=trigger)
    action = triage["action"]

    reply = repo.insert_reply({
        "lead_id": lead_id, "message_id": message_id or (last or {}).get("id"), "run_id": triage["run_id"],
        "body": body, "classification": triage["classification"], "confidence": triage["confidence"],
        "handled_by": "human" if action in ("needs_human", "confirm_unsubscribe") else "agent",
    })
    out: dict[str, Any] = {"lead_id": lead_id, "reply_id": reply["id"], **{k: triage[k] for k in (
        "classification", "confidence", "reason", "action", "by", "run_id")}}
    # 전화는 SDR이 직접 걸므로 취소하지 않는다. 통화 전에 답장 사실과 분류를 볼 수 있게 알림을 붙인다
    if action == "confirm_unsubscribe":
        out["calls_noticed"] = _notice_pending_calls(
            repo, lead_id, f"수신 거부 의심 (확신도 {triage['confidence']:.2f}). 담당자 확인 전까지 통화를 보류하세요. "
                           f"답장: \"{body[:120]}\"", "unsubscribe_suspected")
    elif action != "unsubscribe":  # 수신 거부는 DB 트리거가 콜 과제를 취소한다
        out["calls_noticed"] = _notice_pending_calls(
            repo, lead_id, f"고객이 메일로 답장함 (분류: {triage['classification']}, 처리: {action}). "
                           f"답장: \"{body[:120]}\"", "reply")

    if action == "unsubscribe":
        repo.unsubscribe(lead_id)
        notify("unsubscribe", f"수신 거부: {lead.get('company_name')}", "모든 발송을 중단했습니다.", lead_id=lead_id)
    elif action == "handoff":
        out["handoff"] = handoff_lead(lead_id, reason=f"답장에서 관심 표명: {triage['reason']}", trigger=trigger,
                                      settings=s, repo=repo, llm=llm, new_log=new_log)
    elif action == "answer_draft":
        answer = {**triage["answer"], "body": compose_body(triage["answer"]["body"], s.booking_url)}
        answer["subject"], answer["body"] = finalize(answer["subject"], answer["body"], s, lead_id)
        msg = repo.insert_message(lead_id, triage["run_id"], "followup", answer)
        repo.set_status(lead_id, "awaiting_approval")
        notify("approval", f"답변 승인 요청: {lead.get('company_name')} ({msg['id']})",
               f"고객 답장:\n{body}\n\n답변 초안:\n{msg['body']}", message_id=msg["id"], lead_id=lead_id,
               data=_approval_data(lead, msg, repo.latest_score(lead_id), customer_reply=body))
        out["message_id"] = msg["id"]
    elif action == "followup_later":
        repo.set_status(lead_id, "replied")
        notify("followup", f"후속 시점 확인 필요: {lead.get('company_name')}", triage["reason"], lead_id=lead_id)
    elif action == "confirm_unsubscribe":
        repo.set_status(lead_id, "needs_human")
        notify("needs_human", f"수신 거부 의심, 확인 필요: {lead.get('company_name')}",
               f"AI가 수신 거부로 보았지만 확신도가 낮습니다 ({triage['confidence']:.2f}). 맞으면 수신 거부로 확정하세요:\n"
               f"  python -m sdr_agent.cli unsubscribe {lead_id} --by <이름>\n\n답장: {body}", lead_id=lead_id)
    else:  # needs_human
        repo.set_status(lead_id, "needs_human")
        notify("needs_human", f"사람 확인 필요: {lead.get('company_name')}", f"{triage['reason']}\n\n{body}",
               lead_id=lead_id)
    return out


def confirm_unsubscribe(
    lead_id: str,
    decided_by: str,
    *,
    note: str | None = None,
    settings: Settings | None = None,
    repo: Any = None,
) -> dict[str, Any]:
    """담당자가 수신 거부를 확정한다. 보내지 않은 메일과 걸지 않은 전화는 DB 트리거가 취소한다."""
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    lead = repo.get_lead(lead_id)
    if lead.get("unsubscribed_at"):
        return {"lead_id": lead_id, "status": "already_unsubscribed"}
    repo.unsubscribe(lead_id)
    logger.info("수신 거부 확정: %s by %s (%s)", lead_id, decided_by, note or "")
    notify("unsubscribe", f"수신 거부 확정: {lead.get('company_name')}", f"확정한 사람: {decided_by}. {note or ''}".strip(),
           lead_id=lead_id)
    return {"lead_id": lead_id, "status": "unsubscribed", "decided_by": decided_by}


def handoff_lead(
    lead_id: str,
    *,
    reason: str = "담당자 요청",
    provider: str | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """인계 요약을 만들어 저장하고 담당자에게 알린다."""
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    lead = repo.get_lead(lead_id)
    if use_llm and llm is None:
        try:
            llm = get_llm(provider, s)
        except RuntimeError:
            llm = None  # 키가 없으면 사실 부분과 기본 질문만으로 인계
    _notice_pending_calls(repo, lead_id, f"인간 SDR에게 인계됨 ({reason}). 인계 담당자와 통화 여부를 맞추세요.", "handoff")
    result = run_handoff(lead, repo.list_facts(lead_id), repo.latest_score(lead_id), repo.list_messages(lead_id),
                         repo.list_replies(lead_id), llm if use_llm else None, new_log() if use_llm and llm else None,
                         reason=reason, trigger=trigger, calls=repo.list_call_tasks(lead_id=lead_id))
    row = repo.insert_handoff({"lead_id": lead_id, "run_id": result["run_id"], "summary": result["summary"],
                               "screening_questions": result["screening_questions"],
                               "sent_to": s.handoff_to, "channel": "log"})
    repo.set_status(lead_id, "handed_off")
    text = render_text(result["summary"], result["screening_questions"])
    notify("handoff", f"인계: {lead.get('company_name')}", text, lead_id=lead_id)
    return {"handoff_id": row["id"], "run_id": result["run_id"], "by": result["by"], "text": text}


# --- 전화 팔로업 ---
# 전화는 SDR이 직접 건다 (인터넷·유선 전화와 연동하지 않음). 콜 과제는 SDR의 할 일 목록이다.
# 시스템이 콜 과제를 취소하는 경우는 수신 거부 하나뿐이다 (DB 트리거).
# 답장이 오거나 인계되면 취소하지 않고, 통화 전에 볼 수 있게 과제에 알림을 붙인다.

KST = timezone(timedelta(hours=9))
CALL_OUTCOMES = {
    "interested": "handoff",        # 미팅·데모 의사 → 인계
    "not_now": "replied",           # 나중에 → 시점 확인
    "needs_info": "needs_human",    # 자료·답변 필요 → 담당자 확인
    "wrong_contact": "needs_human", # 담당자 아님 → 담당자 확인
    "no_answer": None,              # 상태 유지 (메일 회신 대기)
    "voicemail": None,
    "unsubscribe": "unsubscribe",   # 연락 거부 → 모든 발송·전화 중단
}


def call_due_at(sent_at: datetime, delay_minutes: int, hours: tuple[int, int]) -> datetime:
    """발송 시각 + delay_minutes. 평일 hours(시작 이상, 끝 미만) 밖이면 다음 평일 시작 시각으로 (공휴일 미고려)."""
    start_h, end_h = hours
    due = sent_at.astimezone(KST) + timedelta(minutes=delay_minutes)
    if due.weekday() < 5 and start_h <= due.hour < end_h:
        return due
    d = due.date()
    if due.weekday() >= 5 or due.hour >= end_h:
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, start_h, tzinfo=KST)


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _notice_pending_calls(repo: Any, lead_id: str, text: str, kind: str) -> int:
    """대기 중인 콜 과제에 통화 전 알림을 붙인다 (취소하지 않음)."""
    pending = repo.list_call_tasks(lead_id=lead_id, status="pending")
    for t in pending:
        script = dict(t["script"])
        script["notices"] = [*script.get("notices", []),
                             {"kind": kind, "text": text, "at": datetime.now(timezone.utc).isoformat()}]
        repo.update_call_task(t["id"], {"script": script})
    return len(pending)


def create_call_task(
    message_id: str,
    *,
    provider: str | None = None,
    trigger: str = "email_sent",
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> dict[str, Any]:
    """발송된 메일을 1시간쯤 뒤 전화로 팔로업할 과제와 콜 스크립트를 만든다. 키가 없으면 기본 스크립트."""
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    msg = repo.get_message(message_id)
    if msg["status"] != "sent":
        raise ValueError(f"발송된 메일만 팔로업할 수 있습니다 (status={msg['status']})")
    lead = repo.get_lead(msg["lead_id"])
    if lead.get("unsubscribed_at"):
        return {"status": "skipped", "reason": "수신 거부"}
    if llm is None:
        try:
            llm = get_llm(provider, s)
        except RuntimeError:
            llm = None
    sent_at = _parse_ts(msg.get("sent_at") or datetime.now(timezone.utc))
    due = call_due_at(sent_at, s.call_delay_minutes, s.call_hours_kst)
    result = run_call_script(lead, repo.list_facts(lead["id"]), repo.latest_score(lead["id"]), msg, llm,
                             new_log() if llm else None, booking_url=s.booking_url, trigger=trigger,
                             when_sent=sent_phrase(sent_at, due))
    task = repo.insert_call_task({"lead_id": lead["id"], "message_id": message_id, "run_id": result["run_id"],
                                  "script": result["script"], "due_at": due.isoformat()})
    text = render_call_script(result["script"], lead, due.strftime("%Y-%m-%d %H:%M KST"))
    notify("call", f"전화 팔로업 예정: {lead.get('company_name')} ({due:%m/%d %H:%M})", text,
           lead_id=lead["id"], call_id=task["id"])
    return {"task_id": task["id"], "status": "pending", "due_at": due.isoformat(), "by": result["by"],
            "run_id": result["run_id"], "text": text}


def due_calls(*, until: datetime | None = None, settings: Settings | None = None, repo: Any = None) -> list[dict]:
    """지금(또는 until)까지 걸어야 할 전화 목록."""
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    until = until or datetime.now(timezone.utc)
    return repo.list_call_tasks(status="pending", due_before=until.isoformat())


def record_call_outcome(
    task_id: str,
    outcome: str,
    called_by: str,
    *,
    notes: str | None = None,
    email_received: bool | None = None,
    meeting_preference: str | None = None,
    compelling_reason: str | None = None,
    settings: Settings | None = None,
    repo: Any = None,
    llm: LLM | None = None,
    new_log: NewLog | None = None,
) -> dict[str, Any]:
    """SDR이 통화 결과를 기록한다. 결과에 따라 인계·수신 거부·사람 확인으로 이어진다.

    이미 인계된 리드는 상태를 되돌리지 않는다 (통화 내용만 남긴다). 수신 거부는 언제든 적용한다.
    """
    if outcome not in CALL_OUTCOMES:
        raise ValueError(f"outcome은 {list(CALL_OUTCOMES)} 중 하나입니다")
    s = settings or load_settings()
    repo, new_log = _wire(s, repo, new_log)
    task = repo.get_call_task(task_id)
    if task["status"] != "pending":
        raise ValueError(f"대기 중인 콜 과제가 아닙니다 (status={task['status']})")
    repo.update_call_task(task_id, {
        "status": "done", "outcome": outcome, "outcome_notes": notes, "called_by": called_by,
        "called_at": datetime.now(timezone.utc).isoformat(), "email_received": email_received,
        "meeting_preference": (meeting_preference or "").strip() or None,
        "compelling_reason": (compelling_reason or "").strip() or None,
    })
    lead_id = task["lead_id"]
    lead = repo.get_lead(lead_id)
    action = CALL_OUTCOMES[outcome]
    out: dict[str, Any] = {"task_id": task_id, "outcome": outcome, "action": action or "keep"}
    if action == "unsubscribe":
        repo.unsubscribe(lead_id)
        notify("unsubscribe", f"전화에서 연락 거부: {lead.get('company_name')}", "모든 발송과 전화를 중단했습니다.",
               lead_id=lead_id, call_id=task_id)
    elif lead.get("status") in ("handed_off", "unsubscribed"):
        out["action"] = "keep"  # 이미 인계·중단된 리드는 기록만 남긴다
    elif action == "handoff":
        reason = "전화 팔로업에서 관심 표명" + (f" (희망 미팅: {meeting_preference})" if meeting_preference else "")
        out["handoff"] = handoff_lead(lead_id, reason=reason, trigger="call", settings=s, repo=repo,
                                      llm=llm, new_log=new_log)
    elif action:
        repo.set_status(lead_id, action)
        if action == "needs_human":
            notify("needs_human", f"전화 팔로업 후 확인 필요: {lead.get('company_name')}", f"{outcome}: {notes or ''}",
                   lead_id=lead_id, call_id=task_id)
    return out


def notify_due_calls(*, settings: Settings | None = None, repo: Any = None, client: Any = None) -> dict[str, Any]:
    """전화 시각이 지난 대기 콜 과제를 과제마다 한 번씩 Slack에 알린다 (Cloud Scheduler가 10분마다 부른다).

    알렸다는 표시는 script JSON의 due_notified_at에 남겨 스키마를 바꾸지 않는다. 실제로 Slack에 올라갔을 때만 표시하므로
    Slack이 아직 설정되지 않았거나 전송이 실패하면 다음 실행에서 다시 시도한다.
    """
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    now = datetime.now(timezone.utc)
    announced = 0
    due = repo.list_call_tasks(status="pending", due_before=now.isoformat())
    for t in due:
        script = dict(t["script"])
        if script.get("due_notified_at"):
            continue
        lead = repo.get_lead(t["lead_id"])
        headline = " ".join(str(script.get("email_summary") or "").split())
        if len(headline) > 140:
            headline = headline[:139] + "…"
        due_kst = _parse_ts(t["due_at"]).astimezone(KST)
        text = "\n".join(x for x in (
            f"고객: {lead.get('name') or '(이름 미상)'} {lead.get('title') or ''}".rstrip(),
            f"연락처: {lead.get('phone')}" if lead.get("phone") else "",
            f"예정 시각: {due_kst:%m/%d %H:%M} KST",
            f"요약: {headline}" if headline else "",
        ) if x)
        if notify("call_due", f"전화할 시간입니다: {lead.get('company_name')}", text, lead_id=t["lead_id"],
                  call_id=t["id"], settings=s, client=client):
            script["due_notified_at"] = now.isoformat()
            repo.update_call_task(t["id"], {"script": script})
            announced += 1
    return {"due": len(due), "announced": announced}


def remind_pending(*, hours: int | None = None, settings: Settings | None = None, repo: Any = None,
                   client: Any = None) -> dict[str, Any]:
    """오래 기다린 승인 대기 메일이 있으면 Slack에 한 번 알린다 (Cloud Scheduler가 매시간 부른다)."""
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    limit = timedelta(hours=hours if hours is not None else s.reminder_hours)
    now = datetime.now(timezone.utc)
    stale = []
    for m in repo.list_messages(status="pending_approval", limit=200):
        waited = now - _parse_ts(m["created_at"])
        if waited >= limit:
            stale.append((waited, m))
    if stale:
        stale.sort(key=lambda t: t[0], reverse=True)
        oldest = int(stale[0][0].total_seconds() // 3600)
        lines = []
        for _, m in stale[:10]:
            lead = repo.get_lead(m["lead_id"])
            lines.append(f"- {lead.get('company_name')}: {m['subject']}")
        notify("reminder", f"승인 대기 {len(stale)}건 (가장 오래된 것 {oldest}시간)", "\n".join(lines),
               settings=s, client=client)
    return {"pending_over_limit": len(stale)}
