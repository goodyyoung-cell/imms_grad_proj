"""실제 Supabase로 루프 전체를 한 바퀴 돌려 보는 점검 (LLM 키 불필요).

LLM만 각본대로 답하는 가짜로 바꾸고, DB·트리거·실행 로그·지식베이스·샌드박스 발송은 실제 코드를 쓴다.
임시 리드 2건을 만들고 끝나면 관련 기록을 모두 지운다 (평가용 샘플 리드는 건드리지 않음).

  python scripts/smoke_supabase.py

시나리오
  A. 웹 폼 유입 → 리서치(사실·점수 저장) → 초안 → 승인·발송 → 질문 답장 → 답변 초안 승인·발송
     → 관심 답장 → 인계 요약
  B. 초안 대기 중 수신 거부 답장 → 대기 메시지 자동 취소(DB 트리거)
     → 미승인 메시지 발송 시도, 수신 거부 리드에 발송 시도 모두 DB가 거부
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdr_agent import jobs  # noqa: E402
from sdr_agent.agent import SupabaseRunLog  # noqa: E402
from sdr_agent.db import Repo, get_client  # noqa: E402
from sdr_agent.llm.base import LLMResponse, ToolCall  # noqa: E402
from sdr_agent.outbox import SandboxSender  # noqa: E402


class Scripted:
    provider, model = "fake", "smoke-script"

    def __init__(self, *responses):
        self.responses = list(responses)

    def chat(self, system, messages, tools, max_tokens=2048):
        if not self.responses:
            raise RuntimeError("각본이 끝났는데 LLM이 또 불렸습니다")
        return self.responses.pop(0)


def call(name, **args):
    return ToolCall(f"smoke-{name}-{uuid.uuid4().hex[:6]}", name, args)


results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: object = "") -> None:
    results.append((bool(ok), label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail not in ("", None) and not ok else ""))


def main() -> int:
    client = get_client()
    repo = Repo(client)
    tag = uuid.uuid4().hex[:8]
    run_ids: list[str] = []
    lead_ids: list[str] = []

    def new_log():
        log = SupabaseRunLog(client)
        orig = log.start

        def start(*a, **kw):
            rid = orig(*a, **kw)
            run_ids.append(rid)
            return rid

        log.start = start
        return log

    sender = SandboxSender()
    try:
        # ---------- A ----------
        r = jobs.intake_leads([{"company_name": f"점검회사A-{tag}", "name": "점검A", "email": f"a@smoke-{tag}.example",
                                "company_domain": "example.com", "title": "CS팀장", "source": "demo_request",
                                "notes": "스모크 테스트"}],
                              channel="web_form", consent_at=datetime.now(timezone.utc).isoformat(), repo=repo)
        check(r["created"] == 1, "A1 웹 폼 유입 저장", r)
        a = r["details"]["created"][0]["lead_id"]
        lead_ids.append(a)
        row = client.table("leads").select("intake_channel,consent_at,status").eq("id", a).execute().data[0]
        check(row["intake_channel"] == "web_form" and row["consent_at"] and row["status"] == "new",
              "A2 유입 경로·동의 시각 기록", row)

        llm = Scripted(
            LLMResponse("홈페이지부터", [call("fetch_company_homepage")]),
            LLMResponse("", [call("compute_score")]),
            LLMResponse("제출", [call("submit_findings", facts=[
                {"field": "summary", "value": "예시 도메인 안내 페이지", "source_type": "homepage", "source_url": "https://example.com"},
                {"field": "revenue", "value": "지어낸 매출", "source_type": "news", "source_url": "https://made-up.example"},
            ])]),
        )
        [res] = jobs.research_leads([a], repo=repo, llm=llm, new_log=new_log)
        facts = repo.list_facts(a)
        check(res.get("status") == "succeeded" and len(facts) == 1 and res["rejected"] == 1,
              "A3 리서치: 확인한 출처 사실 1건 저장, 지어낸 출처 1건 폐기", res)
        check(repo.latest_score(a) is not None, "A4 규칙 점수 저장")
        steps = client.table("agent_steps").select("kind,tool_name").eq("run_id", res["run_id"]).execute().data
        check(len(steps) == 6, "A5 실행 로그 단계 기록 (LLM 3 + 도구 3)", len(steps))

        llm = Scripted(
            LLMResponse("", [call("search_kb", query="무료 체험")]),
            LLMResponse("", [call("submit_draft", subject="고객센터 응대 시간 관련", body="점검A 팀장님, 14일 무료 체험으로 확인해 보실 수 있습니다. 20분 통화 가능하실까요?",
                                  fact_ids_used=[facts[0]["id"]], kb_ids=["02_pricing#무료 체험"])]),
        )
        [d] = jobs.draft_emails([a], repo=repo, llm=llm, new_log=new_log)
        msg = repo.get_message(d.get("message_id", "")) if d.get("message_id") else {}
        check(d["status"] == "drafted" and msg.get("status") == "pending_approval" and len(msg.get("citations", [])) == 2,
              "A6 초안 저장: 승인 대기, 인용 2건", d)
        check("AI의 도움" in msg.get("body", "") and "수신 거부" in msg.get("body", ""), "A7 AI 고지·수신 거부 안내 첨부")
        check(repo.get_lead(a)["status"] == "awaiting_approval", "A8 리드 상태 awaiting_approval")

        out = jobs.decide_message(d["message_id"], "approved", "스모크 점검", channel="cli", repo=repo, sender=sender)
        sent = repo.get_message(d["message_id"])
        check(out["status"] == "sent" and sent["status"] == "sent" and sent["sent_at"] and len(sender.sent) == 1,
              "A9 승인 → 샌드박스 발송, sent_at 기록", out)
        appr = client.table("approvals").select("decision,decided_by").eq("message_id", d["message_id"]).execute().data
        check(appr == [{"decision": "approved", "decided_by": "스모크 점검"}], "A10 승인 기록", appr)

        llm = Scripted(
            LLMResponse("", [call("search_kb", query="카카오톡 채널 연동")]),
            LLMResponse("", [call("submit_triage", classification="question", confidence=0.9, reason="연동 질문",
                                  answer_body="네, 카카오톡 채널 문의도 통합 문의함에서 함께 관리하실 수 있습니다.",
                                  kb_ids=["01_product#연동"])]),
        )
        q = jobs.handle_reply("카카오톡 채널도 되나요?", from_email=f"A@SMOKE-{tag}.example", repo=repo, llm=llm, new_log=new_log)
        check(q["action"] == "answer_draft" and q.get("message_id"), "A11 질문 답장 → 근거 있는 답변 초안", q)
        out = jobs.decide_message(q["message_id"], "edited", "스모크 점검", edited_body="(수정) 네, 가능합니다.",
                                  repo=repo, sender=sender)
        edited = repo.get_message(q["message_id"])["body"]
        check(out["status"] == "sent" and edited.startswith("(수정) 네, 가능합니다.") and "수신 거부" in edited,
              "A12 수정 승인 → 수정본으로 발송, 고지·수신 거부 안내 유지", out)

        llm = Scripted(
            LLMResponse("", [call("submit_triage", classification="interested", confidence=0.92, reason="미팅 의사")]),
            LLMResponse("", [call("submit_handoff", interests=["카카오톡 채널 연동"], recommended_actions=["화요일 미팅 확정"],
                                  screening_questions={"purpose": "무엇을 줄이고 싶으신가요?", "environment": "상담 인원은?",
                                                       "decision": "결정은 누가?", "timing": "언제까지?"})]),
        )
        h = jobs.handle_reply("다음 주 화요일 미팅 가능합니다", lead_id=a, repo=repo, llm=llm, new_log=new_log)
        ho = client.table("handoffs").select("summary,screening_questions").eq("lead_id", a).execute().data
        check(h["action"] == "handoff" and repo.get_lead(a)["status"] == "handed_off" and len(ho) == 1
              and len(ho[0]["screening_questions"]) == 4 and len(ho[0]["summary"]["contact_history"]) == 4,
              "A13 관심 답장 → 인계 요약 저장 (접촉 이력: 메일 2 + 답장 2)", h.get("action"))
        reps = client.table("replies").select("classification,handled_by").eq("lead_id", a).order("received_at").execute().data
        check([r["classification"] for r in reps] == ["question", "interested"], "A14 답장 분류 기록", reps)

        # ---------- B ----------
        r = jobs.intake_leads([{"company_name": f"점검회사B-{tag}", "email": f"b@smoke-{tag}.example", "source": "web_inquiry"}],
                              channel="sheet_upload", repo=repo)
        b = r["details"]["created"][0]["lead_id"]
        lead_ids.append(b)
        repo.set_status(b, "researched")
        llm = Scripted(LLMResponse("", [call("submit_draft", subject="안녕하세요", body="문의 감사합니다. 20분 통화 가능하실까요?")]))
        [d] = jobs.draft_emails([b], repo=repo, llm=llm, new_log=new_log)
        u = jobs.handle_reply("수신 거부합니다", from_email=f"b@smoke-{tag}.example", repo=repo)
        lead_b = repo.get_lead(b)
        check(u["action"] == "unsubscribe" and u["by"] == "rule" and lead_b["status"] == "unsubscribed"
              and lead_b["unsubscribed_at"], "B1 수신 거부 규칙 감지 → 리드 중단 (LLM 호출 없음)", u)
        check(repo.get_message(d["message_id"])["status"] == "cancelled", "B2 대기 중 메시지 자동 취소 (DB 트리거)")

        try:
            repo.update_message(d["message_id"], {"status": "sent"})
            check(False, "B3 미승인 메시지 발송을 DB가 거부")
        except Exception as exc:
            check("not approved" in str(exc), "B3 미승인 메시지 발송을 DB가 거부", exc)

        m2 = repo.insert_message(b, None, "followup", {"subject": "s", "body": "b"})
        repo.update_message(m2["id"], {"status": "approved"})  # 앱 버그로 승인 상태가 됐다고 가정
        try:
            repo.update_message(m2["id"], {"status": "sent"})
            check(False, "B4 수신 거부 리드에 발송을 DB가 거부")
        except Exception as exc:
            check("unsubscribed" in str(exc), "B4 수신 거부 리드에 발송을 DB가 거부", exc)
        out = jobs.send_message(m2["id"], repo=repo, sender=sender)
        check(out["status"] == "cancelled" and len(sender.sent) == 2, "B5 앱 단에서도 발송 대신 취소", out)
    except Exception as exc:
        check(False, "예상치 못한 오류", f"{type(exc).__name__}: {exc}")
    finally:
        # 정리: 리드를 지우면 메시지·승인·답장·인계·사실·점수가 함께 지워진다. 실행 기록은 따로 지운다
        if lead_ids:
            client.table("leads").delete().in_("id", lead_ids).execute()
        if run_ids:
            client.table("agent_runs").delete().in_("id", run_ids).execute()
        left = client.table("leads").select("id", count="exact").like("email", f"%smoke-{tag}%").execute().count
        print(f"\n정리: 임시 리드 {len(lead_ids)}건, 실행 기록 {len(run_ids)}건 삭제 (남은 임시 리드 {left}건)")

    failed = [label for ok, label in results if not ok]
    print(f"\n결과: {len(results) - len(failed)}/{len(results)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
