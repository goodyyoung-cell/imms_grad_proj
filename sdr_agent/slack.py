"""Slack 승인 채널: 승인 요청·알림 보내기, 버튼 클릭 처리.

흐름
  1. 초안이 승인 대기가 되면 notify()가 채널에 메시지를 올린다 (승인 / 수정 후 승인 / 반려 / 관리 화면 열기).
  2. 담당자가 버튼을 누르면 Slack이 /webhooks/slack/interactions 로 보낸다 (서명 검증 필수).
  3. handle_interaction()이 jobs.decide_message()를 불러 승인 기록과 샌드박스 발송을 하고, 메시지를 결과로 바꾼다.

원칙은 관리 웹 화면과 같다. 고객에게 나가는 행동은 사람이 승인하고, 누가 어디서 결정했는지 approvals에 남긴다.
이 모듈은 jobs를 맨 위에서 불러오지 않는다 (jobs → notify → slack 순환을 피하려고 함수 안에서 불러온다).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

API = "https://slack.com/api"
SIGNATURE_MAX_AGE = 300  # 초. 오래된 요청을 다시 보내는 공격을 막는다
KIND_LABEL = {
    "approval": "승인 요청",
    "needs_human": "사람 확인 필요",
    "unsubscribe": "수신 거부",
    "handoff": "인계",
    "followup": "후속 시점 확인",
    "intake": "리드 유입",
    "reminder": "승인 대기 알림",
    "call_due": "전화 팔로업 시각",
    "call": "전화 팔로업 예정",
}


class SlackError(RuntimeError):
    pass


def verify_signature(secret: str | None, timestamp: str | None, body: bytes, signature: str | None,
                     *, now: float | None = None) -> bool:
    """Slack 요청 서명(v0)을 검증한다. 비밀값이 없거나 5분이 지난 요청은 거부한다."""
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_MAX_AGE:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class SlackClient:
    """Slack Web API 호출. 테스트에서는 같은 메서드를 가진 가짜 객체로 바꿔 끼운다."""

    def __init__(self, token: str, http: Any = None):
        self.token = token
        self.http = http or httpx

    def _call(self, method: str, payload: dict) -> dict:
        for attempt in (1, 2):
            r = self.http.post(f"{API}/{method}", json=payload, timeout=10,
                               headers={"Authorization": f"Bearer {self.token}"})
            if r.status_code == 429 and attempt == 1:  # 분당 한도에 걸리면 Retry-After만큼 기다렸다가 한 번 더
                time.sleep(min(int(r.headers.get("Retry-After", "1")), 5))
                continue
            break
        data = r.json()
        if not data.get("ok"):
            raise SlackError(f"{method} 실패: {data.get('error')}")
        return data

    def post_message(self, channel: str, blocks: list[dict], text: str) -> dict:
        return self._call("chat.postMessage", {"channel": channel, "blocks": blocks, "text": text})

    def update_message(self, channel: str, ts: str, blocks: list[dict], text: str) -> dict:
        """봇이 올린 메시지를 고친다. blocks를 함께 주지 않으면 기존 블록이 지워지므로 항상 같이 보낸다."""
        return self._call("chat.update", {"channel": channel, "ts": ts, "blocks": blocks, "text": text})

    def open_modal(self, trigger_id: str, view: dict) -> dict:
        return self._call("views.open", {"trigger_id": trigger_id, "view": view})

    def respond(self, response_url: str, body: dict) -> None:
        """버튼이 달린 원래 메시지를 바꾸거나 본인에게만 보이는 안내를 보낸다 (response_url에는 토큰이 필요 없다).
        response_url은 30분 안에 5번까지만 쓸 수 있어서, 막히면 오류를 일으켜 호출한 쪽이 chat.update로 바꾸게 한다."""
        r = self.http.post(response_url, json=body, timeout=10)
        r.raise_for_status()


def get_client(settings: Settings) -> SlackClient | None:
    return SlackClient(settings.slack_bot_token) if settings.slack_bot_token else None


# --- 메시지 구성 (순수 함수) ---

def _trunc(text: str | None, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _plain(text: str, n: int = 2900) -> dict:
    return {"type": "plain_text", "text": _trunc(text, n) or " ", "emoji": False}


def _mrkdwn(text: str, n: int = 2900) -> dict:
    return {"type": "mrkdwn", "text": _trunc(text, n) or " "}


def _link(base_url: str | None, path: str) -> str | None:
    return f"{base_url}{path}" if base_url else None


def approval_blocks(data: dict, message_id: str, base_url: str | None) -> list[dict]:
    """승인 요청 메시지. data: company, name, title, subject, body, score, tags, kind, customer_reply."""
    follow = data.get("kind") == "followup"
    head = f"{'답변' if follow else '첫 메일'} 승인 요청: {data.get('company') or '(회사 미상)'}"
    meta = []
    who = " ".join(x for x in (data.get("name"), data.get("title")) if x)
    if who:
        meta.append(f"*담당자* {who}")
    if data.get("score") is not None:
        meta.append(f"*점수* {data['score']:g}점")
    if data.get("tags"):
        meta.append("*근거* " + ", ".join(data["tags"][:6]))
    blocks: list[dict] = [{"type": "header", "text": _plain(head, 150)}]
    if meta:
        blocks.append({"type": "section", "text": _mrkdwn("  |  ".join(meta))})
    if data.get("customer_reply"):
        blocks.append({"type": "section", "text": _plain("고객 답장\n" + data["customer_reply"], 1200)})
    blocks.append({"type": "section", "text": _plain(f"제목: {data.get('subject', '')}\n\n{data.get('body', '')}")})
    if data.get("no_consent"):
        blocks.append({"type": "context", "elements": [_mrkdwn("이 리드는 수신 동의 기록이 없습니다. 실제 발송 단계에서는 보내지 않습니다")]})
    blocks.append({"type": "context", "elements": [_mrkdwn("고객에게 나가기 전에 사람이 확인합니다. 승인하면 발송합니다")]})
    actions: list[dict] = [
        {"type": "button", "action_id": "approve", "value": message_id, "style": "primary",
         "text": _plain("승인", 75)},
        {"type": "button", "action_id": "edit", "value": message_id, "text": _plain("수정 후 승인", 75)},
        {"type": "button", "action_id": "reject", "value": message_id, "style": "danger", "text": _plain("반려", 75)},
    ]
    url = _link(base_url, f"/m/{message_id}")
    if url:
        actions.append({"type": "button", "action_id": "open_admin", "url": url, "text": _plain("관리 화면에서 열기", 75)})
    blocks.append({"type": "actions", "block_id": f"approval:{message_id}", "elements": actions})
    return blocks


def notice_blocks(kind: str, title: str, text: str, url: str | None = None) -> list[dict]:
    blocks: list[dict] = [
        {"type": "header", "text": _plain(f"{KIND_LABEL.get(kind, kind)}: {title}", 150)},
    ]
    if text:
        blocks.append({"type": "section", "text": _plain(text)})
    if url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "action_id": "open_admin", "url": url, "text": _plain("관리 화면에서 열기", 75)}]})
    return blocks


def decided_blocks(company: str | None, subject: str | None, outcome: str, by: str) -> list[dict]:
    """버튼을 눌러 처리한 뒤 원래 메시지를 바꿔 넣는 결과 메시지. 버튼이 사라져 두 번 누를 수 없다."""
    return [
        {"type": "header", "text": _plain(f"메일 {outcome}: {company or ''}", 150)},
        {"type": "section", "text": _plain(f"제목: {subject or ''}")},
        {"type": "context", "elements": [_mrkdwn(f"처리: {by}")]},
    ]




def _meta(message_id: str, payload: dict) -> str:
    """수정·반려 창이 닫힌 뒤 원래 메시지를 바꾸는 데 쓸 값. response_url이 막히면 chat.update로 대신한다."""
    return json.dumps({"message_id": message_id, "response_url": payload.get("response_url"),
                       "channel": (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id"),
                       "ts": (payload.get("container") or {}).get("message_ts") or (payload.get("message") or {}).get("ts")})


def edit_modal(message: dict, meta: str) -> dict:
    return {
        "type": "modal", "callback_id": "edit_approve",
        "title": _plain("수정 후 승인", 24), "submit": _plain("승인", 24), "close": _plain("취소", 24),
        "private_metadata": meta,
        "blocks": [
            {"type": "section", "text": _plain(f"제목: {message.get('subject', '')}", 300)},
            {"type": "input", "block_id": "body_block", "label": _plain("본문", 100),
             "hint": _plain("AI 사용 고지, 수신 거부 안내, 전송자 정보는 지워도 다시 붙습니다.", 150),
             "element": {"type": "plain_text_input", "action_id": "body", "multiline": True,
                         "initial_value": message.get("body", "")}},
        ],
    }


def reject_modal(message: dict, meta: str) -> dict:
    """반려 사유를 고르게 한다. 어떤 오류가 많은지 세어 평가와 프롬프트 개선에 쓴다."""
    from .jobs import REASON_CODES

    return {
        "type": "modal", "callback_id": "reject_reason",
        "title": _plain("메일 반려", 24), "submit": _plain("반려", 24), "close": _plain("취소", 24),
        "private_metadata": meta,
        "blocks": [
            {"type": "section", "text": _plain(f"제목: {message.get('subject', '')}\n이 메일은 발송되지 않습니다.", 300)},
            {"type": "input", "block_id": "reason_block", "label": _plain("반려 사유", 100),
             "element": {"type": "static_select", "action_id": "reason", "placeholder": _plain("사유를 고르세요", 100),
                         "options": [{"text": _plain(label, 75), "value": code} for code, label in REASON_CODES.items()]}},
            {"type": "input", "block_id": "note_block", "optional": True, "label": _plain("메모", 100),
             "element": {"type": "plain_text_input", "action_id": "note", "max_length": 300}},
        ],
    }


# --- 버튼 클릭 처리 ---

_OUTCOME = {"sent": "승인 후 발송(샌드박스)", "rejected": "반려", "cancelled": "취소", "approved": "승인(아직 발송 전)"}
MODAL_BODY_LIMIT = 3000


def _safe_respond(client: SlackClient | None, url: str | None, body: dict) -> None:
    if not (client and url):
        return
    try:
        client.respond(url, body)
    except Exception:  # 안내를 못 보내도 결정은 이미 기록됐다
        logger.exception("Slack 응답 실패")


def _ephemeral(client: SlackClient | None, url: str | None, text: str) -> None:
    _safe_respond(client, url, {"response_type": "ephemeral", "replace_original": False, "text": text})


def _replace_original(client: SlackClient | None, meta: dict, blocks: list[dict], text: str) -> None:
    """버튼 메시지를 결과로 바꾼다. response_url이 만료됐거나 막히면 chat.update로 바꾼다."""
    if not client:
        return
    try:
        if meta.get("response_url"):
            client.respond(meta["response_url"], {"replace_original": True, "blocks": blocks, "text": text})
            return
    except Exception:
        logger.warning("response_url로 바꾸지 못해 chat.update를 씁니다")
    if meta.get("channel") and meta.get("ts"):
        try:
            client.update_message(meta["channel"], meta["ts"], blocks, text)
        except Exception:
            logger.exception("Slack 메시지 갱신 실패")


def _apply(mid: str, decision: str, by: str, *, repo: Any, settings: Settings, client: SlackClient | None, meta: dict,
           edited_body: str | None = None, reason_code: str | None = None, note: str | None = None,
           decide: Callable | None = None) -> None:
    from . import jobs

    decide = decide or jobs.decide_message
    try:
        out = decide(mid, decision, by, channel="slack", edited_body=edited_body, reason_code=reason_code,
                     note=note or "Slack에서 결정", settings=settings, repo=repo)
        msg = repo.get_message(mid)
        lead = repo.get_lead(msg["lead_id"])
        outcome = _OUTCOME.get(out["status"], out["status"])
        blocks = decided_blocks(lead.get("company_name"), msg.get("subject"), outcome, by)
        _replace_original(client, meta, blocks, f"메일 {outcome}: {lead.get('company_name')}")
    except (ValueError, LookupError) as exc:  # 이미 처리됨, 본문 없음, 발송 한도 등
        _ephemeral(client, meta.get("response_url"), f"처리하지 못했습니다: {exc}")
    except Exception:
        logger.exception("Slack 결정 처리 실패: %s", mid)
        _ephemeral(client, meta.get("response_url"), "서버 오류로 처리하지 못했습니다. 관리 화면에서 확인해 주세요.")


def _open_modal(kind: str, mid: str, payload: dict, *, settings: Settings, repo: Any, client: SlackClient | None) -> None:
    url = payload.get("response_url")
    try:
        msg = repo.get_message(mid)
        if msg["status"] != "pending_approval":
            _ephemeral(client, url, f"이미 처리된 메시지입니다 (status={msg['status']})")
        elif kind == "edit" and len(msg.get("body", "")) > MODAL_BODY_LIMIT:
            link = _link(settings.admin_web_url, f"/m/{mid}")
            _ephemeral(client, url, "본문이 길어 Slack에서 고칠 수 없습니다." + (f" 관리 화면에서 수정하세요: {link}" if link else ""))
        else:
            meta = _meta(mid, payload)
            view = edit_modal(msg, meta) if kind == "edit" else reject_modal(msg, meta)
            client.open_modal(payload.get("trigger_id", ""), view)
    except Exception:
        logger.exception("Slack 창 열기 실패: %s", mid)
        _ephemeral(client, url, "창을 열지 못했습니다. 관리 화면에서 처리해 주세요.")


def handle_interaction(payload: dict, *, settings: Settings, repo: Any, client: SlackClient | None = None,
                       decide: Callable | None = None) -> dict:
    """Slack이 보낸 버튼·모달 이벤트를 처리하고, Slack에 돌려줄 응답 본문(dict)을 반환한다.

    "관리 화면에서 열기" 같은 url 버튼도 클릭 이벤트가 오므로 아무것도 하지 않고 빈 응답으로 받아 준다.
    """
    client = client or get_client(settings)
    user = payload.get("user") or {}
    uid = user.get("id", "")
    by = f"slack:{user.get('username') or user.get('name') or uid}"
    response_url = payload.get("response_url")
    if settings.slack_approvers and uid not in settings.slack_approvers:
        _ephemeral(client, response_url, "승인 권한이 없는 계정입니다. 담당자에게 문의해 주세요.")
        return {}

    kind = payload.get("type")
    if kind == "block_actions":
        action = (payload.get("actions") or [{}])[0]
        mid, action_id = action.get("value"), action.get("action_id")
        meta = json.loads(_meta(mid or "", payload))
        if action_id == "approve" and mid:
            _apply(mid, "approved", by, repo=repo, settings=settings, client=client, meta=meta, decide=decide)
        elif action_id in ("edit", "reject") and mid and client:
            _open_modal(action_id, mid, payload, settings=settings, repo=repo, client=client)
        return {}

    if kind == "view_submission":
        view = payload.get("view") or {}
        meta = json.loads(view.get("private_metadata") or "{}")
        values = (view.get("state") or {}).get("values", {})
        if view.get("callback_id") == "edit_approve":
            body = ((values.get("body_block", {}).get("body")) or {}).get("value") or ""
            if not body.strip():
                return {"response_action": "errors", "errors": {"body_block": "본문을 입력해 주세요."}}
            _apply(meta.get("message_id", ""), "edited", by, repo=repo, settings=settings, client=client, meta=meta,
                   edited_body=body, decide=decide)
            return {"response_action": "clear"}
        if view.get("callback_id") == "reject_reason":
            reason = (((values.get("reason_block", {}).get("reason")) or {}).get("selected_option") or {}).get("value")
            if not reason:
                return {"response_action": "errors", "errors": {"reason_block": "반려 사유를 골라 주세요."}}
            note = ((values.get("note_block", {}).get("note")) or {}).get("value")
            _apply(meta.get("message_id", ""), "rejected", by, repo=repo, settings=settings, client=client, meta=meta,
                   reason_code=reason, note=note, decide=decide)
            return {"response_action": "clear"}
    return {}
