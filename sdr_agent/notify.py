"""사람에게 보내는 알림 (승인 요청, 인계 자료, 사람 확인 필요).

승인 채널(Slack 또는 이메일)이 아직 정해지지 않아 지금은 서버 로그에만 남긴다.
채널이 정해지면 이 함수만 바꾸면 된다. 그 전까지는 관리 API(/admin/*)나 CLI로 확인·승인한다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sdr_agent.notify")


def notify(kind: str, title: str, text: str = "") -> None:
    logger.info("[알림:%s] %s\n%s", kind, title, text)
