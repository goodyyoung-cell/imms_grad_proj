"""관리 화면(브라우저)용 로그인 토큰.

- 브라우저 화면: POST /auth/login {name, password} → 8시간짜리 토큰. 이후 `Authorization: Bearer <토큰>`.
  비밀번호는 ADMIN_PASSWORD 하나를 팀이 공유한다. name은 승인·통화 기록의 '누가'에 자동으로 들어간다.
- 예약 실행·CLI·외부 서버: 지금처럼 JOB_TOKEN을 그대로 쓴다.

JOB_TOKEN을 브라우저 코드에 넣지 않기 위한 장치다 (저장소가 공개이고, 이 토큰으로 승인·발송까지 할 수 있다).
토큰은 서명만 하고 서버에 저장하지 않는다. 서명 키는 ADMIN_TOKEN_SECRET, 없으면 JOB_TOKEN에서 만든다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

TOKEN_TTL_SECONDS = 8 * 3600
PREFIX = "adm."


@dataclass(frozen=True)
class Principal:
    name: str
    kind: str  # "admin" (화면 로그인) | "system" (JOB_TOKEN)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signing_key(admin_token_secret: str | None, job_token: str | None) -> bytes:
    base = admin_token_secret or job_token
    if not base:
        raise RuntimeError("ADMIN_TOKEN_SECRET 또는 JOB_TOKEN이 필요합니다")
    return hmac.new(base.encode(), b"sdr-agent admin token v1", hashlib.sha256).digest()


def issue(name: str, key: bytes, now: float | None = None, ttl: int = TOKEN_TTL_SECONDS) -> tuple[str, int]:
    exp = int((now or time.time()) + ttl)
    payload = _b64(json.dumps({"sub": name, "exp": exp}, ensure_ascii=False).encode())
    sig = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{PREFIX}{payload}.{sig}", exp


def verify(token: str, key: bytes, now: float | None = None) -> str | None:
    """유효하면 이름, 아니면 None."""
    if not token.startswith(PREFIX):
        return None
    try:
        payload, sig = token[len(PREFIX):].split(".", 1)
        expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data["exp"]) < (now or time.time()):
            return None
        return str(data["sub"])
    except (ValueError, KeyError, TypeError):
        return None


def check_password(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())
