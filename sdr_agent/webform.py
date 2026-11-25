"""웹사이트에 게재하는 데모 신청 폼 (가상 회사 노바웍스).

실제 회사 웹사이트에서는 이 폼을 그대로 쓰거나, 같은 필드를 /form/submit 으로 보내면 된다.
외부 폼 서비스(구글 폼, Tally 등)는 서버에서 /webhooks/lead 로 JSON을 보낸다 (JOB_TOKEN 필요).

공개 경로라 다음으로 막는다
- 개인정보 수집·이용 및 AI 활용 고지 동의 필수 (동의 시각을 consent_at에 저장)
- 사람에게 안 보이는 칸(website)을 채운 제출은 봇으로 보고 버린다
- 같은 IP에서 1분에 5회까지
- 필드 길이 제한 (intake.LIMITS)
"""

from __future__ import annotations

import html
import time
from collections import defaultdict, deque

RATE_LIMIT = 5
RATE_WINDOW = 60.0
_hits: dict[str, deque] = defaultdict(deque)


def allow(ip: str, now: float | None = None) -> bool:
    now = now or time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    return True


STYLE = """
:root{--bg:#f5f7f6;--card:#fff;--fg:#17211e;--muted:#5d6b66;--line:#d3ddd9;--accent:#0e5a4b;--accent-fg:#fff;--err:#a3361b}
@media (prefers-color-scheme:dark){:root{--bg:#0f1513;--card:#161e1b;--fg:#e4ece9;--muted:#93a49e;--line:#2c3834;--accent:#3fae93;--accent-fg:#06231c;--err:#f08a6c;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:40px max(16px,env(safe-area-inset-left)) 56px}
.brand{font-weight:700;letter-spacing:.02em;color:var(--accent)}h1{font-size:1.6rem;margin:6px 0 4px}p.lead{color:var(--muted);margin:0 0 24px}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;display:grid;gap:16px}
label{display:grid;gap:6px;font-weight:600;font-size:.92rem}label b{font-weight:600}label span{font-weight:400;color:var(--muted);font-size:.82rem}
input,select,textarea{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;width:100%}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:520px){.row{grid-template-columns:1fr}}
.consent{display:flex;gap:10px;align-items:flex-start;font-weight:400;font-size:.88rem;color:var(--muted)}.consent input{width:auto;margin-top:4px}
.notice{font-size:.82rem;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}
button{font:inherit;font-weight:700;background:var(--accent);color:var(--accent-fg);border:0;border-radius:8px;padding:12px;cursor:pointer}
.hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}.err{color:var(--err);font-weight:600}
"""

FORM_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>노바데스크 데모 신청</title><style>{style}</style></head><body><main>
<div class="brand">노바웍스 · 노바데스크</div>
<h1>데모 신청</h1>
<p class="lead">고객 문의 응대를 줄이는 AI 헬프데스크. 남겨 주시면 담당자가 연락드립니다.<br>(연구용 가상 회사입니다)</p>
{error}
<form method="post" action="/form/submit">
  <div class="row">
    <label for="name">이름<input id="name" name="name" required maxlength="50" autocomplete="name"></label>
    <label for="title"><b>직급 <span>선택</span></b><input id="title" name="title" maxlength="50"></label>
  </div>
  <label for="email">회사 이메일<input id="email" name="email" type="email" required maxlength="120" autocomplete="email"></label>
  <div class="row">
    <label for="company_name">회사명<input id="company_name" name="company_name" required maxlength="100" autocomplete="organization"></label>
    <label for="company_domain"><b>회사 홈페이지 <span>선택</span></b><input id="company_domain" name="company_domain" maxlength="120" placeholder="example.co.kr"></label>
  </div>
  <label for="source">문의 유형<select id="source" name="source">
    <option value="demo_request">데모 요청</option><option value="web_inquiry">일반 문의</option></select></label>
  <label for="notes"><b>문의 내용 <span>선택 · 상담 인원, 월 문의량, 쓰는 채널 등을 적어 주시면 도움이 됩니다</span></b>
    <textarea id="notes" name="notes" rows="4" maxlength="2000"></textarea></label>
  <div class="hp" aria-hidden="true"><label for="website">웹사이트<input id="website" name="website" tabindex="-1" autocomplete="off"></label></div>
  <label class="consent" for="consent"><input id="consent" name="consent" type="checkbox" value="yes" required>
    <span>개인정보(이름, 이메일, 회사, 직급, 문의 내용) 수집·이용에 동의합니다. 상담 목적에만 쓰며, 상담 종료 후 파기합니다.</span></label>
  <p class="notice">회신 메일 초안 작성과 회사 공개 정보 조사에 AI를 활용합니다. 고객에게 보내는 모든 메일은 담당자가 검토한 뒤 발송합니다.
    언제든 "수신 거부"라고 답장하시면 연락을 중단합니다.</p>
  <button type="submit">신청하기</button>
</form></main></body></html>"""

DONE_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>신청 완료</title><style>{style}</style></head><body><main>
<div class="brand">노바웍스 · 노바데스크</div><h1>신청해 주셔서 감사합니다</h1>
<p class="lead">영업일 기준 하루 안에 담당자가 연락드립니다.</p></main></body></html>"""


def form_page(error: str | None = None) -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return FORM_HTML.format(style=STYLE, error=err)


def done_page() -> str:
    return DONE_HTML.format(style=STYLE)
