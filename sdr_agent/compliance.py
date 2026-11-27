"""광고성 메일의 표시 의무 (정보통신망법 제50조 제4항, 시행령 제61조 별표 6).

- 제목 맨 앞에 "(광고)"를 붙인다.
- 본문에 전송자의 명칭과 연락처, 수신 거부·동의 철회 방법을 밝힌다. 수신 거부 안내는 한글과 영문을 함께 적는다.
- 수신 거부는 로그인 없이 한 번에 할 수 있어야 한다 → PUBLIC_BASE_URL이 있으면 리드별 수신 거부 링크를 넣는다.
  (링크는 서명한 주소이고, 메일 보안 검사기가 미리 열어도 거부되지 않도록 열기만 해서는 처리하지 않고 버튼을 한 번 누르게 한다.)

별표 6 원문은 직접 확인하지 못했고 협회 해설서(krema)가 옮긴 내용을 따랐다 (docs/research-ai-sdr.md).
실제 발송 전에 법제처 원문과 대조한다.
"""

from __future__ import annotations

import hashlib
import hmac

from .config import Settings
from .workflows.draft import FOOTER

AD_PREFIX = "(광고) "
FOOTER_START = "\n\n---\n이 메일은 AI의 도움을 받아 작성되었고"


def ensure_ad_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if s.startswith("(광고)") else AD_PREFIX + s


def _secret(s: Settings) -> bytes:
    return (s.admin_token_secret or s.job_token or "").encode()


def unsubscribe_token(s: Settings, lead_id: str) -> str:
    return hmac.new(_secret(s), f"unsub:{lead_id}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_token_ok(s: Settings, lead_id: str, token: str) -> bool:
    return bool(_secret(s)) and hmac.compare_digest(unsubscribe_token(s, lead_id), token or "")


def unsubscribe_url(s: Settings, lead_id: str | None) -> str | None:
    if not (s.public_base_url and lead_id and _secret(s)):
        return None
    return f"{s.public_base_url}/unsubscribe/{lead_id}/{unsubscribe_token(s, lead_id)}"


def render_footer(s: Settings, lead_id: str | None) -> str:
    """기본 안내(draft.FOOTER) 뒤에 영문 안내, 수신 거부 링크, 전송자 정보를 붙인다."""
    lines = [FOOTER,
             'If you do not want to receive further emails, reply "unsubscribe" to this message. We will stop immediately.']
    url = unsubscribe_url(s, lead_id)
    if url:
        lines.append(f"수신 거부 / Unsubscribe: {url}")
    sender = s.sender_name or "발신자 미설정"
    lines.append(f"발신: {sender}" + (f" | 연락처: {s.sender_contact}" if s.sender_contact else ""))
    return "\n".join(lines)


def apply_footer(body: str, s: Settings, lead_id: str | None) -> str:
    """본문 끝의 기존 안내를 걷어내고 새로 붙인다. 사람이 고치면서 지워도, 이미 붙어 있어도 한 번만 남는다."""
    cut = body.find(FOOTER_START)
    base = body[:cut] if cut >= 0 else body
    return base.rstrip() + render_footer(s, lead_id)


def finalize(subject: str, body: str, s: Settings, lead_id: str | None) -> tuple[str, str]:
    return ensure_ad_subject(subject), apply_footer(body, s, lead_id)


def _page(title: str, body: str) -> str:
    from . import webform

    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{title}</title><style>{webform.STYLE}</style></head><body><main>{body}</main></body></html>')


def unsubscribe_confirm_page(action_url: str) -> str:
    return _page("수신 거부", '<h1>수신 거부 / Unsubscribe</h1><p class="lead">앞으로 이 주소로 영업 메일을 보내지 않습니다.<br>'
                 'We will stop sending you sales emails.</p>'
                 f'<form method="post" action="{action_url}"><button type="submit">수신 거부 / Unsubscribe</button></form>')


def unsubscribed_page() -> str:
    return _page("처리 완료", '<h1>처리되었습니다 / Done</h1><p class="lead">수신 거부를 접수했고, 지금부터 발송을 중단합니다.<br>'
                 'Your request has been received. We have stopped sending emails to you.</p>')
