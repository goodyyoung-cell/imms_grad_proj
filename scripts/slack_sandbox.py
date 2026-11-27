"""Slack 승인 채널을 DB·LLM 키 없이 시험하는 로컬 샌드박스.

가짜 DB(메모리)에 가상 리드와 승인 대기 메일 3건을 넣고 서버를 띄운 뒤, 채널에 승인 요청을 올린다.
버튼을 누르면 실제 승인 코드(jobs.decide_message)가 그대로 돈다. 발송은 샌드박스라 고객에게 나가지 않는다.
서버를 끄면 데이터가 사라진다.

필요한 것 (모두 .env 또는 환경변수):
  SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_CHANNEL   (docs/admin-slack.md 4절)
  버튼이 동작하려면 Slack이 이 서버에 닿아야 한다: 터널 주소를 앱의 Interactivity 요청 주소에
  https://<터널 주소>/webhooks/slack/interactions 로 넣는다.

실행:  pip install -e ".[dev]"  후  python scripts/slack_sandbox.py [--port 8000] [--no-notify]
확인:  http://localhost:8000/sandbox/state  (메일과 리드의 현재 상태)
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = [
    ("S1", "알테오젠", "오지민", "상무", "데모 요청", 90.0, ["데모 요청", "대표·임원급", "최근 공시"],
     "[노바웍스] 상담 품질 편차를 줄이는 방법을 말씀드립니다",
     "안녕하세요, 오지민 상무님. 노바웍스의 노바데스크 담당자입니다.\n데모를 요청해 주셔서 감사합니다.\n노바데스크는 이메일, 웹 채팅, 카카오톡 문의를 한 화면에 모으고, 상담원이 확인한 뒤 보내는 AI 답변 초안을 만들어 드립니다.\n괜찮으시다면 20분 정도 짧은 화상 미팅으로 현재 운영 방식을 먼저 듣고 싶습니다."),
    ("S2", "삼양식품", "정하은", "선임", "웹 문의", 72.0, ["웹 문의", "14일 경과"],
     "[노바데스크] 문의하신 가격 안내드립니다",
     "안녕하세요, 정하은 선임님.\n요금은 상담원 1인당 월 기준으로, 스타터는 29,000원, 비즈니스는 59,000원입니다.\n신규 고객께는 14일 무료 체험이 있고 카드 등록은 필요 없습니다.\n상담 인력 규모를 알려 주시면 맞는 플랜을 안내드리겠습니다."),
    ("S3", "나노신소재", "송소율", "프로", "웨비나 참석", 58.0, ["세미나 참석"],
     "웨비나에서 문의하신 상담 자동화 사례 공유드립니다",
     "안녕하세요, 송소율 프로님.\n웨비나에서 상담 자동화 사례를 질문해 주셔서 감사합니다.\n참고 사례로 물류 B사의 배송 문의 분류 자동화가 있습니다 (가상 사례입니다).\n괜찮으시다면 20분 정도 이야기 나눌 수 있을까요?"),
]


def build_repo():
    from sdr_agent.workflows.draft import FOOTER
    from tests.test_email_loop import NOW, FakeRepo

    leads = [{"id": sid, "name": name, "email": f"{sid.lower()}@sandbox.example", "company_name": company, "title": title,
              "source": "demo_request", "inbound_at": NOW, "notes": "", "status": "awaiting_approval", "unsubscribed_at": None,
              "phone": "010-0000-0000"} for sid, company, name, title, *_ in SAMPLES]
    repo = FakeRepo(leads)
    repo.facts, repo.scores = {}, {}
    ids = []
    for sid, company, _name, _title, _src, score, tags, subject, body in SAMPLES:
        repo.facts[sid] = [{"id": f"F-{sid}", "field": "summary", "value": f"{company} (가상 시연용 기업 개요)", "source_url": "https://opendart.fss.or.kr/"}]
        repo.scores[sid] = {"score": score, "tags": tags, "breakdown": {}}
        msg = repo.insert_message(sid, None, "initial", {"subject": subject, "body": body + FOOTER})
        ids.append(msg["id"])
    return repo, ids


def post_approvals(repo, ids):
    from sdr_agent import jobs
    from sdr_agent.notify import notify

    for mid in ids:
        msg = repo.get_message(mid)
        lead = repo.get_lead(msg["lead_id"])
        notify("approval", f"메일 승인 요청: {lead['company_name']} ({mid})", f"제목: {msg['subject']}\n\n{msg['body']}",
               message_id=mid, lead_id=lead["id"], data=jobs._approval_data(lead, msg, repo.latest_score(lead["id"])))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-notify", action="store_true", help="시작할 때 승인 요청을 올리지 않는다")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("JOB_TOKEN", secrets.token_urlsafe(24))

    import uvicorn

    from sdr_agent.app import app, get_repo
    from sdr_agent.config import load_settings

    s = load_settings()
    missing = [n for n, v in (("SLACK_BOT_TOKEN", s.slack_bot_token), ("SLACK_SIGNING_SECRET", s.slack_signing_secret),
                              ("SLACK_CHANNEL", s.slack_channel)) if not v]
    if missing:
        print(f"[경고] 설정이 없어 Slack에는 올라가지 않습니다: {', '.join(missing)} (docs/admin-slack.md 4절)")

    repo, ids = build_repo()
    app.dependency_overrides[get_repo] = lambda: repo

    @app.get("/sandbox/state")
    def sandbox_state() -> dict:
        return {"messages": [{"id": m["id"], "company": repo.leads[m["lead_id"]]["company_name"], "status": m["status"]} for m in repo.messages.values()],
                "leads": {i: l["status"] for i, l in repo.leads.items()}, "approvals": repo.approvals, "call_tasks": list(repo.calls.values())}

    if not args.no_notify:
        post_approvals(repo, ids)
        print(f"승인 요청 {len(ids)}건을 올렸습니다 (Slack 설정이 있을 때).")
    print(f"샌드박스 서버: http://localhost:{args.port}  상태: /sandbox/state  Interactivity 주소: /webhooks/slack/interactions")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
