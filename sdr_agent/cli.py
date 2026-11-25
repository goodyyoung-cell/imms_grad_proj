"""로컬 실행용 명령.

  python -m sdr_agent.cli seed                                  # 가상 리드를 DB에 넣기 (data/sample/leads.json)
  python -m sdr_agent.cli research --limit 5                    # 상태가 new인 리드 5건 조사
  python -m sdr_agent.cli draft --limit 5                       # 조사 끝난 리드에 첫 메일 초안 (승인 대기)
  python -m sdr_agent.cli pending                               # 승인 대기 메시지 보기
  python -m sdr_agent.cli approve <message_id> --by 홍길동       # 승인 → 샌드박스 발송
  python -m sdr_agent.cli reject <message_id> --by 홍길동 --note "톤이 과함"
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
    elif args.cmd == "reply":
        _print(jobs.handle_reply(args.body, from_email=args.from_email, lead_id=args.lead_id, provider=args.provider))
    elif args.cmd == "handoff":
        result = jobs.handoff_lead(args.lead_id, reason=args.reason, provider=args.provider)
        print(result["text"])
    elif args.cmd == "kb":
        from .kb import default_kb

        _print(default_kb().search(args.query))
    elif args.cmd == "eval-replies":
        _print(eval_replies(args.file, args.provider))


if __name__ == "__main__":
    main()
