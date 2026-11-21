"""로컬 실행용 명령.

  python -m sdr_agent.cli research --lead-id <uuid> [--provider gemini]
  python -m sdr_agent.cli research --limit 5          # 상태가 new인 리드 5건
"""

from __future__ import annotations

import argparse
import json

from .jobs import research_leads


def main() -> None:
    p = argparse.ArgumentParser(prog="sdr_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("research", help="리서치 에이전트 실행")
    r.add_argument("--lead-id", action="append", dest="lead_ids")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--provider", choices=["claude", "gemini"])
    args = p.parse_args()

    if args.cmd == "research":
        results = research_leads(args.lead_ids, limit=args.limit, provider=args.provider, trigger="manual")
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
