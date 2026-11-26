"""로컬 실행용 명령.

  python -m sdr_agent.cli seed                                  # 가상 리드를 DB에 넣기 (data/sample/leads.json)
  python -m sdr_agent.cli import-leads --file leads.xlsx        # 직원 시트 업로드 미리보기 (--commit 으로 저장)
  python -m sdr_agent.cli import-leads --google-sheet <주소>     # 구글 시트에서 (서비스 계정에 보기 권한 공유)
  python -m sdr_agent.cli research --limit 5                    # 상태가 new인 리드 5건 조사
  python -m sdr_agent.cli draft --limit 5                       # 조사 끝난 리드에 첫 메일 초안 (승인 대기)
  python -m sdr_agent.cli pending                               # 승인 대기 메시지 보기
  python -m sdr_agent.cli approve <message_id> --by 홍길동       # 승인 → 샌드박스 발송
  python -m sdr_agent.cli reject <message_id> --by 홍길동 --note "톤이 과함"
  python -m sdr_agent.cli calls                                 # 오늘 걸어야 할 전화와 콜 스크립트
  python -m sdr_agent.cli call-outcome <task_id> interested --by 홍길동 --received yes \
      --meeting "다음 주 화요일 오후" --reason "12월 성수기 전 응답 지연 해소"   # 통화 결과 기록
  python -m sdr_agent.cli reply --from a@b.example "다음 주에 미팅 가능합니다"   # 모의 답장
  python -m sdr_agent.cli handoff <lead_id>                     # 인계 요약
  python -m sdr_agent.cli kb "가격 플랜"                         # 지식베이스 검색 확인 (DB·키 불필요)
  python -m sdr_agent.cli eval-replies                          # 모의 답장 분류 정확도 (DB 불필요, LLM 키 필요)

공통 옵션: --provider claude|gemini
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import jobs

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
SAMPLE_LEADS = SAMPLE_DIR / "leads.json"
SAMPLE_REPLIES = SAMPLE_DIR / "replies.json"


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def seed(path: Path) -> list[dict]:
    """가상 리드를 넣는다. merged_into_key로 중복 리드를 대표 리드에 연결한다."""
    from datetime import datetime, timedelta, timezone

    from .db import Repo, get_client

    repo = Repo(get_client())
    rows = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    ids: dict[str, str] = {}
    out = []
    for r in sorted(rows, key=lambda r: bool(r.get("merged_into_key"))):  # 대표 리드 먼저
        row = {k: v for k, v in r.items() if k not in ("key", "days_ago", "merged_into_key")}
        row["intake_channel"] = "seed"
        row["inbound_at"] = (now - timedelta(days=r.get("days_ago", 0))).isoformat()
        if r.get("merged_into_key"):
            row["merged_into"] = ids[r["merged_into_key"]]
        lead = repo.insert_lead(row)
        ids[r["key"]] = lead["id"]
        out.append({"key": r["key"], "id": lead["id"], "company_name": lead["company_name"]})
    return out


def eval_replies(path: Path, provider: str | None = None, llm=None) -> dict:
    """모의 답장마다 분류해 정답과 비교한다. 실행 로그는 메모리에만 남긴다."""
    from .agent import MemoryRunLog
    from .llm import get_llm
    from .workflows.reply import detect_unsubscribe, run_reply_triage

    cases = json.loads(path.read_text(encoding="utf-8"))
    rows, correct = [], 0
    for c in cases:
        use_llm = None if detect_unsubscribe(c["body"]) else (llm or get_llm(provider))
        r = run_reply_triage({"id": None, "company_name": "가상회사"}, c["body"], use_llm,
                             MemoryRunLog() if use_llm else None, trigger="eval")
        ok = r["classification"] == c["expected"]
        correct += ok
        rows.append({"id": c["id"], "expected": c["expected"], "got": r["classification"],
                     "confidence": r["confidence"], "action": r["action"], "by": r["by"], "ok": ok,
                     "has_answer": bool(r.get("answer"))})
    return {"total": len(cases), "correct": correct, "accuracy": round(correct / max(1, len(cases)), 3),
            "to_human": sum(r["action"] == "needs_human" for r in rows), "cases": rows}


def main() -> None:
    p = argparse.ArgumentParser(prog="sdr_agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="가상 리드를 DB에 넣기")
    s.add_argument("--file", type=Path, default=SAMPLE_LEADS)

    for name, help_ in (("research", "리서치 에이전트 실행"), ("draft", "첫 메일 초안 만들기")):
        r = sub.add_parser(name, help=help_)
        r.add_argument("--lead-id", action="append", dest="lead_ids")
        r.add_argument("--limit", type=int, default=10)
        r.add_argument("--provider", choices=["claude", "gemini"])

    sub.add_parser("pending", help="승인 대기 메시지")

    for name in ("approve", "reject"):
        a = sub.add_parser(name)
        a.add_argument("message_id")
        a.add_argument("--by", required=True, help="결정한 사람")
        a.add_argument("--note")
        a.add_argument("--edited-body-file", type=Path, help="수정해서 승인할 본문 파일 (approve만)")

    cl = sub.add_parser("calls", help="걸어야 할 전화와 콜 스크립트 보기")
    cl.add_argument("--all", action="store_true", help="예정 시각이 안 된 것까지")

    co = sub.add_parser("call-outcome", help="통화 결과 기록")
    co.add_argument("task_id")
    co.add_argument("outcome", choices=["interested", "not_now", "needs_info", "no_answer", "voicemail",
                                        "wrong_contact", "unsubscribe"])
    co.add_argument("--by", required=True, help="통화한 사람")
    co.add_argument("--notes")
    co.add_argument("--received", choices=["yes", "no"], help="메일을 받았다고 했는지")
    co.add_argument("--meeting", help="희망 미팅 시기 (예: 다음 주 화요일 오후)")
    co.add_argument("--reason", dest="compelling_reason", help="Compelling Reason (왜 지금 바꿔야 하는지)")

    rp = sub.add_parser("reply", help="모의 답장 넣기")
    rp.add_argument("body")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="from_email")
    g.add_argument("--lead-id")
    rp.add_argument("--provider", choices=["claude", "gemini"])

    h = sub.add_parser("handoff", help="인계 요약")
    h.add_argument("lead_id")
    h.add_argument("--reason", default="담당자 요청")
    h.add_argument("--provider", choices=["claude", "gemini"])

    im = sub.add_parser("import-leads", help="CSV·엑셀 파일 또는 구글 시트의 리드 올리기 (기본은 미리보기)")
    src = im.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, dest="sheet_file")
    src.add_argument("--google-sheet", help="구글 시트 주소 또는 ID")
    im.add_argument("--sheet", help="시트(탭) 이름")
    im.add_argument("--commit", action="store_true", help="실제로 DB에 저장 (없으면 미리보기만)")

    k = sub.add_parser("kb", help="지식베이스 검색")
    k.add_argument("query")

    e = sub.add_parser("eval-replies", help="정답이 달린 모의 답장으로 분류 정확도 측정 (DB 불필요)")
    e.add_argument("--file", type=Path, default=SAMPLE_REPLIES)
    e.add_argument("--provider", choices=["claude", "gemini"])

    args = p.parse_args()

    if args.cmd == "seed":
        _print(seed(args.file))
    elif args.cmd == "research":
        _print(jobs.research_leads(args.lead_ids, limit=args.limit, provider=args.provider, trigger="manual"))
    elif args.cmd == "draft":
        _print(jobs.draft_emails(args.lead_ids, limit=args.limit, provider=args.provider, trigger="manual"))
    elif args.cmd == "pending":
        from .db import Repo, get_client

        for m in Repo(get_client()).list_messages(status="pending_approval"):
            print(f"\n=== {m['id']} (lead {m['lead_id']}, {m['kind']})\n제목: {m['subject']}\n\n{m['body']}")
    elif args.cmd in ("approve", "reject"):
        edited = args.edited_body_file.read_text(encoding="utf-8") if getattr(args, "edited_body_file", None) else None
        decision = "rejected" if args.cmd == "reject" else ("edited" if edited else "approved")
        _print(jobs.decide_message(args.message_id, decision, args.by, channel="cli", edited_body=edited, note=args.note))
    elif args.cmd == "calls":
        from .db import Repo, get_client
        from .workflows.call_script import render_text as render_call

        repo = Repo(get_client())
        tasks = repo.list_call_tasks(status="pending") if args.all else jobs.due_calls(repo=repo)
        for t in tasks:
            print(f"\n=== {t['id']} (예정 {t['due_at']})\n{render_call(t['script'], repo.get_lead(t['lead_id']))}")
        if not tasks:
            print("걸어야 할 전화가 없습니다." + ("" if args.all else " (--all 로 예정된 것까지 보기)"))
    elif args.cmd == "call-outcome":
        received = {"yes": True, "no": False}.get(args.received) if args.received else None
        _print(jobs.record_call_outcome(args.task_id, args.outcome, args.by, notes=args.notes, email_received=received,
                                        meeting_preference=args.meeting, compelling_reason=args.compelling_reason))
    elif args.cmd == "reply":
        _print(jobs.handle_reply(args.body, from_email=args.from_email, lead_id=args.lead_id, provider=args.provider))
    elif args.cmd == "handoff":
        result = jobs.handoff_lead(args.lead_id, reason=args.reason, provider=args.provider)
        print(result["text"])
    elif args.cmd == "import-leads":
        from . import sheets

        rows, info = (sheets.read_file(args.sheet_file, args.sheet) if args.sheet_file
                      else sheets.read_google_sheet(args.google_sheet, args.sheet))
        _print({**info, **jobs.intake_leads(rows, channel="sheet_upload", dry_run=not args.commit)})
        if not args.commit:
            print("\n미리보기입니다. 저장하려면 --commit을 붙여 다시 실행하세요.")
    elif args.cmd == "kb":
        from .kb import default_kb

        _print(default_kb().search(args.query))
    elif args.cmd == "eval-replies":
        _print(eval_replies(args.file, args.provider))


if __name__ == "__main__":
    main()
