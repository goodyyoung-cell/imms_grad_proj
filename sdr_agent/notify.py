"""사람에게 보내는 알림 (승인 요청, 인계 자료, 사람 확인 필요, 전화 시각).

항상 서버 로그에 남기고, Slack이 설정돼 있으면 채널에도 올린다 (SLACK_BOT_TOKEN, SLACK_CHANNEL).
Slack 전송이 실패해도 워크플로우는 멈추지 않는다. 승인은 관리 웹(ADMIN_WEB_URL)에서도 할 수 있다.

승인 요청(kind="approval")에 message_id와 data를 함께 주면 승인·수정·반려 버튼이 달린 메시지가 된다.
딥링크는 관리 웹의 주소 규칙을 따른다: /m/{메일}, /l/{리드}, /c/{콜 과제}, /
"""

from __future__ import annotations

import logging
from typing import Any

from . import slack
from .config import Settings, load_settings

logger = logging.getLogger("sdr_agent.notify")


def deep_link(base: str | None, *, message_id: str | None = None, lead_id: str | None = None,
              call_id: str | None = None, home: bool = False) -> str | None:
    if not base:
        return None
    if call_id:
        return f"{base}/c/{call_id}"
    if message_id:
        return f"{base}/m/{message_id}"
    if lead_id:
        return f"{base}/l/{lead_id}"
    return f"{base}/" if home else None


def notify(
    kind: str,
    title: str,
    text: str = "",
    *,
    message_id: str | None = None,
    lead_id: str | None = None,
    call_id: str | None = None,
    data: dict[str, Any] | None = None,
    settings: Settings | None = None,
    client: slack.SlackClient | None = None,
) -> bool:
    """로그에 남기고 Slack에도 올렸으면 True. 절대 예외를 던지지 않는다."""
    logger.info("[알림:%s] %s\n%s", kind, title, text)
    try:
        s = settings or load_settings()
        if not (s.slack_channel and (client or s.slack_bot_token)):
            return False
        if kind != "test" and kind not in s.slack_notify_kinds:  # 연결 시험은 항상 보낸다
            return False
        client = client or slack.get_client(s)
        if kind == "approval" and message_id and data:
            blocks = slack.approval_blocks(data, message_id, s.admin_web_url)
            fallback = f"메일 승인 요청: {data.get('company') or title}"
        else:
            url = deep_link(s.admin_web_url, lead_id=lead_id, call_id=call_id, home=kind == "reminder")
            blocks = slack.notice_blocks(kind, title, text, url)
            fallback = f"{slack.KIND_LABEL.get(kind, kind)}: {title}"
        client.post_message(s.slack_channel, blocks, fallback)
        return True
    except Exception:  # 알림 실패가 영업 흐름을 막아서는 안 된다
        logger.exception("Slack 알림 실패 (%s)", kind)
    return False
