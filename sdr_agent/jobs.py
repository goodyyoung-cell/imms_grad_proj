"""이벤트·예약 실행이 부르는 작업 단위. 웹 서버(app.py)와 CLI(cli.py)가 함께 쓴다.

핵심 루프
  research_leads  (new → researched)            자율
  draft_emails    (researched → awaiting_approval) 자율, 초안만
  decide_message  사람 승인 → send_message (샌드박스 발송, → contacted)
  handle_reply    답장 → 수신 거부 중단 / 사람 이관 / 답변 초안(승인 대기) / 인계
  handoff_lead    인계 요약 (→ handed_off)

테스트에서는 repo, llm, new_log, sender를 가짜로 넣는다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .agent import MemoryRunLog, RunLog, SupabaseRunLog
from .config import Settings, load_settings
from .db import Repo, get_client
from .llm import get_llm
from .llm.base import LLM
from .notify import notify
from .outbox import Sender, get_sender
from .toolkit.dart import DartClient
from .toolkit.news import GoogleNewsClient, NaverNewsClient
from .workflows.draft import compose_body, run_draft
from .workflows.handoff import render_text, run_handoff
from .workflows.reply import detect_unsubscribe, run_reply_triage
from .workflows.research import run_research

logger = logging.getLogger(__name__)

NewLog = Callable[[], RunLog]


def _wire(s: Settings, repo: Any, new_log: NewLog | None) -> tuple[Any, NewLog]:
    if repo is None:
        client = get_client(s)
        return Repo(client), new_log or (lambda: SupabaseRunLog(client))
    return repo, new_log or MemoryRunLog


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
    naver = (NaverNewsClient(s.naver_client_id, s.naver_client_secret)
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
            msg = repo.insert_message(lead_id, result["run_id"], "initial", result["draft"])
            repo.set_status(lead_id, "awaiting_approval")
            notify("approval", f"메일 승인 요청: {lead.get('company_name')} ({msg['id']})",
                   f"제목: {msg['subject']}\n\n{msg['body']}")
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
    settings: Settings | None = None,
    repo: Any = None,
    sender: Sender | None = None,
) -> dict[str, Any]:
    """사람의 승인 결정을 기록한다. 승인(또는 수정 승인)이면 바로 샌드박스로 발송한다."""
    if decision not in ("approved", "rejected", "edited"):
        raise ValueError("decision은 approved, rejected, edited 중 하나입니다")
    if decision == "edited" and not (edited_body and edited_body.strip()):
        raise ValueError("edited에는 수정한 본문이 필요합니다")
    s = settings or load_settings()
    repo, _ = _wire(s, repo, None)
    msg = repo.get_message(message_id)
    if msg["status"] != "pending_approval":
        raise ValueError(f"승인 대기 중인 메시지가 아닙니다 (status={msg['status']})")

    repo.insert_approval({"message_id": message_id, "decision": decision, "decided_by": decided_by,
                          "channel": channel, "edited_body": edited_body, "note": note})
    if decision == "rejected":
        repo.update_message(message_id, {"status": "rejected"})
        repo.set_status(msg["lead_id"], "needs_human")
        return {"message_id": message_id, "status": "rejected"}

    fields: dict[str, Any] = {"status": "approved"}
    if decision == "edited":
        fields["body"] = edited_body
    repo.update_message(message_id, fields)
    return send_message(message_id, repo=repo, sender=sender)


def send_message(message_id: str, *, repo: Any, sender: Sender | None = None) -> dict[str, Any]:
    """승인된 메시지를 보낸다. 수신 거부·미승인은 여기서 한 번, DB 트리거에서 한 번 더 막는다."""
    msg = repo.get_message(message_id)
    lead = repo.get_lead(msg["lead_id"])
    if lead.get("unsubscribed_at"):
        repo.update_message(message_id, {"status": "cancelled"})
        return {"message_id": message_id, "status": "cancelled", "reason": "수신 거부"}
    if msg["status"] != "approved":
        raise ValueError(f"승인되지 않은 메시지는 보낼 수 없습니다 (status={msg['status']})")
    if not lead.get("email"):
        raise ValueError("리드에 이메일 주소가 없습니다")
    provider_id = (sender or get_sender()).send(lead["email"], msg["subject"], msg["body"])
    repo.update_message(message_id, {"status": "sent", "provider_message_id": provider_id})
    repo.set_status(msg["lead_id"], "contacted")
    return {"message_id": message_id, "status": "sent", "provider_message_id": provider_id}


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
        "handled_by": "human" if action == "needs_human" else "agent",
    })
    out: dict[str, Any] = {"lead_id": lead_id, "reply_id": reply["id"], **{k: triage[k] for k in (
        "classification", "confidence", "reason", "action", "by", "run_id")}}

    if action == "unsubscribe":
        repo.unsubscribe(lead_id)
        notify("unsubscribe", f"수신 거부: {lead.get('company_name')}", "모든 발송을 중단했습니다.")
    elif action == "handoff":
        out["handoff"] = handoff_lead(lead_id, reason=f"답장에서 관심 표명: {triage['reason']}", trigger=trigger,
                                      settings=s, repo=repo, llm=llm, new_log=new_log)
    elif action == "answer_draft":
        answer = {**triage["answer"], "body": compose_body(triage["answer"]["body"], s.booking_url)}
        msg = repo.insert_message(lead_id, triage["run_id"], "followup", answer)
        repo.set_status(lead_id, "awaiting_approval")
        notify("approval", f"답변 승인 요청: {lead.get('company_name')} ({msg['id']})",
               f"고객 답장:\n{body}\n\n답변 초안:\n{msg['body']}")
        out["message_id"] = msg["id"]
    elif action == "followup_later":
        repo.set_status(lead_id, "replied")
        notify("followup", f"후속 시점 확인 필요: {lead.get('company_name')}", triage["reason"])
    else:  # needs_human
        repo.set_status(lead_id, "needs_human")
        notify("needs_human", f"사람 확인 필요: {lead.get('company_name')}", f"{triage['reason']}\n\n{body}")
    return out


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
    result = run_handoff(lead, repo.list_facts(lead_id), repo.latest_score(lead_id), repo.list_messages(lead_id),
                         repo.list_replies(lead_id), llm if use_llm else None, new_log() if use_llm and llm else None,
                         reason=reason, trigger=trigger)
    row = repo.insert_handoff({"lead_id": lead_id, "run_id": result["run_id"], "summary": result["summary"],
                               "screening_questions": result["screening_questions"],
                               "sent_to": s.handoff_to, "channel": "log"})
    repo.set_status(lead_id, "handed_off")
    text = render_text(result["summary"], result["screening_questions"])
    notify("handoff", f"인계: {lead.get('company_name')}", text)
    return {"handoff_id": row["id"], "run_id": result["run_id"], "by": result["by"], "text": text}
