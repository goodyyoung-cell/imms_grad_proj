"""Slack 대화: 앱 DM, 채널에서 @멘션, 승인 카드 스레드 답글로 에이전트에게 말을 건다.

흐름
  1. Slack이 /webhooks/slack/events 로 이벤트를 보낸다 (서명 검증은 app.py). 3초 안에 200을 돌려줘야 하므로
     plan()이 이벤트를 걸러 작업(Job)만 만들고 바로 응답한다.
  2. 작업은 응답 뒤 백그라운드에서 process()가 한다: "작업 중" 메시지를 올리고, chat.run_chat()으로 처리하고, 그 메시지를 답변으로 바꾼다.
     (Cloud Run은 응답 뒤에도 CPU가 계속 할당돼야 한다: --no-cpu-throttling)
  3. 답은 스레드에 올린다. 같은 스레드의 이어지는 말은 같은 대화(세션)로 처리한다.

어떤 말에 답하는가
  - DM: 항상.
  - 채널: 봇을 @멘션한 말. 그리고 이미 대화가 시작된 스레드, 또는 봇이 올린 승인 카드의 스레드에 단 멘션 없는 답글.
  - 승인 카드 스레드에서는 그 카드의 메일이 "이 메일"이 된다.
권한: SLACK_APPROVERS가 있으면 그 사용자만. 비어 있으면 봇이 있는 곳의 모든 사용자 (버튼 승인과 같은 기준).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import chat, slack
from .config import Settings

logger = logging.getLogger(__name__)

_SEEN: dict[str, float] = {}
_SEEN_TTL = 600
_LOCK = threading.Lock()
_MENTION = re.compile(r"<@[A-Z0-9]+>")


@dataclass
class Job:
    event_id: str
    channel: str
    ts: str
    thread_ts: str | None
    user: str
    text: str
    is_dm: bool
    denied: bool = False


def _first_time(event_id: str) -> bool:
    """같은 이벤트를 두 번 처리하지 않는다 (Slack은 3초 안에 응답이 없으면 재전송한다)."""
    now = time.time()
    with _LOCK:
        for k in [k for k, t in _SEEN.items() if now - t > _SEEN_TTL]:
            del _SEEN[k]
        if event_id in _SEEN:
            return False
        _SEEN[event_id] = now
        return True


def plan(payload: dict, *, settings: Settings, repo: Any) -> Job | None:
    """이벤트가 답할 대상이면 Job, 아니면 None. 빠르게 끝나야 한다 (DB 조회 한두 번)."""
    if payload.get("type") != "event_callback":
        return None
    ev = payload.get("event") or {}
    etype = ev.get("type")
    if ev.get("bot_id") or ev.get("subtype") or not ev.get("user"):
        return None  # 봇 자신의 말, 수정·삭제 같은 변형 이벤트
    event_id = payload.get("event_id") or ""
    text = ev.get("text") or ""
    channel, ts, thread_ts = ev.get("channel"), ev.get("ts"), ev.get("thread_ts")
    if not (channel and ts):
        return None
    bot_user = ((payload.get("authorizations") or [{}])[0]).get("user_id")
    mentioned = bool(bot_user and f"<@{bot_user}>" in text)

    is_dm = False
    if etype == "app_mention":
        pass
    elif etype == "message" and ev.get("channel_type") == "im":
        is_dm = True
    elif etype == "message":
        if mentioned or not thread_ts or thread_ts == ts:
            return None  # 멘션은 app_mention 이벤트로 따로 온다. 스레드 밖의 일반 대화는 무시
        if not (repo.find_chat_session(f"slack:{channel}:{thread_ts}") or repo.find_message_by_slack_ts(channel, thread_ts)):
            return None
    else:
        return None

    clean = _MENTION.sub("", text).strip()
    if not clean or not event_id or not _first_time(event_id):
        return None
    denied = bool(settings.slack_approvers and ev["user"] not in settings.slack_approvers)
    return Job(event_id, channel, ts, thread_ts, ev["user"], clean, is_dm, denied)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def reply_blocks(reply: str, actions: list[dict]) -> list[dict]:
    text = _esc(reply)
    chunks = [text[i:i + 2900] for i in range(0, len(text), 2900)] or [" "]
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks[:8]]
    if actions:
        label = "  |  ".join(_esc(a["label"]) for a in actions[:6])
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"처리: {label}"}]})
    return blocks


def _say(client: slack.SlackClient, job: Job, thread: str | None, blocks: list[dict], text: str, placeholder: dict | None) -> None:
    if placeholder:
        try:
            client.update_message(placeholder.get("channel") or job.channel, placeholder["ts"], blocks, text)
            return
        except Exception:
            logger.exception("Slack 답변 갱신 실패, 새 메시지로 보냅니다")
    client.post_message(job.channel, blocks, text, thread_ts=thread)


def process(job: Job, *, settings: Settings, repo: Any, client: slack.SlackClient | None = None) -> None:
    """작업 하나를 처리해 Slack 스레드에 답한다. 어떤 실패도 예외로 밖에 내보내지 않는다."""
    client = client or slack.get_client(settings)
    if not client:
        return
    thread = job.thread_ts or (None if job.is_dm else job.ts)
    try:
        if job.denied:
            client.post_message(job.channel, reply_blocks("권한이 없는 계정입니다. 담당자에게 문의해 주세요.", []),
                                "권한 없음", thread_ts=thread)
            return
        placeholder = None
        try:
            placeholder = client.post_message(job.channel, reply_blocks("작업 중입니다…", []), "작업 중", thread_ts=thread)
        except Exception:
            logger.exception("Slack 작업 중 표시 실패")
        by = f"slack:{client.user_name(job.user) or job.user}"
        ext = f"slack:{job.channel}:dm" if job.is_dm else f"slack:{job.channel}:{job.thread_ts or job.ts}"
        ctx: dict[str, Any] = {}
        if job.thread_ts and not job.is_dm:
            card = repo.find_message_by_slack_ts(job.channel, job.thread_ts)
            if card:
                ctx = {"message_id": card["id"], "lead_id": card["lead_id"], "screen": "slack_card"}
        try:
            out = chat.run_chat(job.text, user=by, channel="slack", external_key=ext, context=ctx, event_id=job.event_id,
                                settings=settings, repo=repo, slack_client=client)
            blocks, text = reply_blocks(out["reply"], out["actions"]), out["reply"]
        except RuntimeError as exc:
            logger.warning("Slack 대화 불가: %s", exc)
            text = "LLM 키가 설정되지 않아 대화를 쓸 수 없습니다. 관리자에게 문의해 주세요."
            blocks = reply_blocks(text, [])
        except Exception:
            logger.exception("Slack 대화 처리 실패")
            text = "처리 중 오류가 났습니다. 잠시 뒤 다시 시도하거나 관리 화면에서 확인해 주세요."
            blocks = reply_blocks(text, [])
        _say(client, job, thread, blocks, text, placeholder)
    except Exception:
        logger.exception("Slack 대화 응답 실패")
