"""메일 발송. 이번 학기는 실제로 보내지 않는 샌드박스만 쓴다.

발송은 도구가 아니다. 에이전트는 이 코드를 부를 수 없고, 사람 승인 뒤 jobs.py가 직접 부른다.
실제 서비스(이메일 API)를 붙일 때는 같은 send()를 가진 클래스를 만들어 바꿔 끼운다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

logger = logging.getLogger(__name__)


class Sender(Protocol):
    def send(self, to: str, subject: str, body: str) -> str:
        """보내고 서비스의 메시지 id를 돌려준다."""


class SandboxSender:
    """아무 데도 보내지 않고 기록만 남긴다. 보낸 내용은 messages 테이블에 남는다."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, body: str) -> str:
        message_id = f"sandbox-{uuid.uuid4()}"
        self.sent.append({"id": message_id, "to": to, "subject": subject, "body": body})
        logger.info("샌드박스 발송: %s → %s (%s)", message_id, to, subject)
        return message_id


def get_sender() -> Sender:
    return SandboxSender()
