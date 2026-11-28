"""Supabase 접근. 비밀 키(sb_secret_...)로 서버에서만 쓴다 (모든 테이블이 RLS로 잠겨 있음)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings, load_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_client(settings: Settings | None = None):
    s = settings or load_settings()
    if not s.supabase_url or not s.supabase_secret_key:
        raise RuntimeError("SUPABASE_URL과 SUPABASE_SECRET_KEY가 필요합니다")
    from supabase import create_client

    return create_client(s.supabase_url, s.supabase_secret_key)


class Repo:
    """워크플로우가 쓰는 DB 작업 모음. 테스트에서는 가짜 객체로 바꿔 끼운다."""

    def __init__(self, client):
        self.c = client

    def get_lead(self, lead_id: str) -> dict[str, Any]:
        rows = self.c.table("leads").select("*").eq("id", lead_id).limit(1).execute().data
        if not rows:
            raise LookupError(f"리드 없음: {lead_id}")
        lead = rows[0]
        # 같은 사람이 여러 채널로 들어온 수 (중복으로 합쳐진 리드 포함)
        merged = self.c.table("leads").select("id", count="exact").eq("merged_into", lead_id).execute()
        lead["channel_count"] = 1 + (merged.count or 0)
        return lead

    def list_lead_ids(self, status: str, limit: int = 10) -> list[str]:
        rows = (self.c.table("leads").select("id").eq("status", status).is_("merged_into", "null")
                .order("inbound_at").limit(limit).execute().data)
        return [r["id"] for r in rows]

    def set_status(self, lead_id: str, status: str) -> None:
        self.c.table("leads").update({"status": status}).eq("id", lead_id).execute()

    def find_lead_by_email(self, email: str) -> dict[str, Any] | None:
        rows = (self.c.table("leads").select("*").ilike("email", email.strip()).is_("merged_into", "null")
                .order("inbound_at").limit(1).execute().data)
        return rows[0] if rows else None

    def find_candidates(self, email: str | None, domain: str | None, company_name: str) -> list[dict]:
        """중복일 수 있는 기존 리드. 최종 판정은 intake.find_match가 정규화해서 한다."""
        cols = "id,email,company_name,company_domain,source,merged_into,unsubscribed_at,status"
        found: dict[str, dict] = {}

        def add(rows):
            for r in rows:
                found[r["id"]] = r

        t = self.c.table("leads")
        if email:
            add(t.select(cols).ilike("email", email).limit(50).execute().data)
        if domain:
            add(t.select(cols).ilike("company_domain", domain).limit(50).execute().data)
        core = re.sub(r"\(주\)|㈜|주식회사", "", company_name).strip()
        if core:
            add(t.select(cols).ilike("company_name", f"%{core}%").limit(50).execute().data)
        # 병합된 리드가 걸리면 대표 리드도 함께 가져온다
        roots = {r["merged_into"] for r in found.values() if r.get("merged_into")} - set(found)
        if roots:
            add(t.select(cols).in_("id", list(roots)).execute().data)
        return list(found.values())

    def unsubscribe(self, lead_id: str) -> None:
        # DB 트리거가 상태를 unsubscribed로 바꾸고 보내지 않은 메시지를 모두 취소한다
        self.c.table("leads").update({"unsubscribed_at": _now()}).eq("id", lead_id).execute()

    def list_facts(self, lead_id: str) -> list[dict]:
        return (self.c.table("research_facts").select("*").eq("lead_id", lead_id)
                .order("retrieved_at").execute().data)

    def latest_score(self, lead_id: str) -> dict | None:
        rows = (self.c.table("lead_scores").select("*").eq("lead_id", lead_id)
                .order("created_at", desc=True).limit(1).execute().data)
        return rows[0] if rows else None

    # --- 메시지·승인 ---

    def insert_message(self, lead_id: str, run_id: str | None, kind: str, draft: dict) -> dict:
        row = {"lead_id": lead_id, "run_id": run_id, "kind": kind,
               "subject": draft["subject"], "body": draft["body"], "citations": draft.get("citations", [])}
        # 제목 후보·소재는 첫 메일 초안에만 있다. 없으면 열을 보내지 않아 마이그레이션 전에도 저장된다
        if draft.get("subject_options"):
            row["subject_options"] = draft["subject_options"]
            row["subject_choice"] = draft.get("subject_choice", "general")
        if draft.get("hook"):
            row["hook"] = draft["hook"]
        return self.c.table("messages").insert(row).execute().data[0]

    def find_message_by_slack_ts(self, channel: str, ts: str) -> dict | None:
        rows = (self.c.table("messages").select("*").eq("slack_ref->>ts", ts).eq("slack_ref->>channel", channel)
                .limit(1).execute().data)
        return rows[0] if rows else None

    def get_message(self, message_id: str) -> dict:
        rows = self.c.table("messages").select("*").eq("id", message_id).limit(1).execute().data
        if not rows:
            raise LookupError(f"메시지 없음: {message_id}")
        return rows[0]

    def list_messages(self, lead_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        q = self.c.table("messages").select("*")
        if lead_id:
            q = q.eq("lead_id", lead_id)
        if status:
            q = q.eq("status", status)
        return q.order("created_at").limit(limit).execute().data

    def update_message(self, message_id: str, fields: dict) -> dict:
        return self.c.table("messages").update(fields).eq("id", message_id).execute().data[0]

    def insert_approval(self, row: dict) -> None:
        self.c.table("approvals").insert(row).execute()

    def count_sent_since(self, since_iso: str) -> int:
        res = self.c.table("messages").select("id", count="exact").eq("status", "sent").gte("sent_at", since_iso).execute()
        return res.count or 0

    def update_lead(self, lead_id: str, fields: dict) -> dict:
        return self.c.table("leads").update(fields).eq("id", lead_id).execute().data[0]

    # --- 평가 지표 (관리 화면의 /admin/metrics) ---

    def list_all_approvals(self, limit: int = 5000) -> list[dict]:
        return self.c.table("approvals").select("*").order("decided_at").limit(limit).execute().data

    def list_recent_approvals(self, limit: int = 200) -> list[dict]:
        return self.c.table("approvals").select("*").order("decided_at", desc=True).limit(limit).execute().data

    def messages_by_ids(self, ids: list[str]) -> dict[str, dict]:
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        rows = self.c.table("messages").select("id,lead_id,kind,subject,body,status").in_("id", ids).execute().data
        return {r["id"]: r for r in rows}

    def list_recent_replies(self, classification: str | None = None, handled_by: str | None = None,
                            limit: int = 100) -> list[dict]:
        q = self.c.table("replies").select("*")
        if classification:
            q = q.eq("classification", classification)
        if handled_by:
            q = q.eq("handled_by", handled_by)
        return q.order("received_at", desc=True).limit(limit).execute().data

    def count_sent_today(self) -> int:
        """오늘(한국 시간 기준) 발송된 메시지 수."""
        kst = timezone(timedelta(hours=9))
        start = datetime.now(kst).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.count_sent_since(start.astimezone(timezone.utc).isoformat())

    def list_all_replies(self, limit: int = 5000) -> list[dict]:
        return self.c.table("replies").select("*").order("received_at").limit(limit).execute().data

    def list_all_leads(self, limit: int = 5000) -> list[dict]:
        return (self.c.table("leads").select("id,status,created_at,inbound_at,consent_at,unsubscribed_at")
                .is_("merged_into", "null").limit(limit).execute().data)

    def insert_manual_timing(self, row: dict) -> dict:
        return self.c.table("manual_timings").insert(row).execute().data[0]

    def list_manual_timings(self, limit: int = 1000) -> list[dict]:
        return self.c.table("manual_timings").select("*").order("created_at", desc=True).limit(limit).execute().data

    # --- 답장·인계 ---

    def insert_reply(self, row: dict) -> dict:
        return self.c.table("replies").insert(row).execute().data[0]

    def list_replies(self, lead_id: str) -> list[dict]:
        return self.c.table("replies").select("*").eq("lead_id", lead_id).order("received_at").execute().data

    def insert_handoff(self, row: dict) -> dict:
        return self.c.table("handoffs").insert(row).execute().data[0]

    # --- 콜 팔로업 ---

    def insert_call_task(self, row: dict) -> dict:
        return self.c.table("call_tasks").insert(row).execute().data[0]

    def get_call_task(self, task_id: str) -> dict:
        rows = self.c.table("call_tasks").select("*").eq("id", task_id).limit(1).execute().data
        if not rows:
            raise LookupError(f"콜 과제 없음: {task_id}")
        return rows[0]

    def list_call_tasks(self, lead_id: str | None = None, status: str | None = None,
                        due_before: str | None = None, limit: int = 100) -> list[dict]:
        q = self.c.table("call_tasks").select("*")
        if lead_id:
            q = q.eq("lead_id", lead_id)
        if status:
            q = q.eq("status", status)
        if due_before:
            q = q.lte("due_at", due_before)
        return q.order("due_at").limit(limit).execute().data

    def update_call_task(self, task_id: str, fields: dict) -> dict:
        return self.c.table("call_tasks").update(fields).eq("id", task_id).execute().data[0]

    # --- 관리 화면 조회 ---

    LEAD_LIST_COLS = ("id,name,email,company_name,company_domain,title,source,intake_channel,status,"
                      "inbound_at,updated_at,unsubscribed_at,consent_at")

    def list_leads(self, status: str | None = None, q: str | None = None,
                   limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """대표 리드(병합되지 않은 리드) 목록과 전체 개수. 최근 변경 순."""
        query = (self.c.table("leads").select(self.LEAD_LIST_COLS, count="exact").is_("merged_into", "null"))
        if status:
            query = query.eq("status", status)
        if q:
            query = query.ilike("company_name", f"%{q.strip()}%")
        res = query.order("updated_at", desc=True).range(offset, offset + limit - 1).execute()
        return res.data, res.count or 0

    def latest_scores(self, lead_ids: list[str]) -> dict[str, dict]:
        if not lead_ids:
            return {}
        rows = (self.c.table("lead_scores").select("lead_id,score,tags,created_at").in_("lead_id", lead_ids)
                .order("created_at", desc=True).execute().data)
        out: dict[str, dict] = {}
        for r in rows:
            out.setdefault(r["lead_id"], r)
        return out

    def leads_by_ids(self, ids: list[str]) -> dict[str, dict]:
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        rows = self.c.table("leads").select("id,name,company_name,title,status,email,phone").in_("id", ids).execute().data
        return {r["id"]: r for r in rows}

    def status_counts(self) -> dict[str, int]:
        rows = self.c.table("leads").select("status").is_("merged_into", "null").execute().data
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return counts

    def merged_leads(self, lead_id: str) -> list[dict]:
        return (self.c.table("leads").select("id,name,email,company_name,title,source,intake_channel,inbound_at")
                .eq("merged_into", lead_id).order("inbound_at").execute().data)

    def list_handoffs(self, lead_id: str) -> list[dict]:
        return self.c.table("handoffs").select("*").eq("lead_id", lead_id).order("created_at").execute().data

    def list_runs(self, lead_id: str | None = None, workflow: str | None = None, limit: int = 50) -> list[dict]:
        q = self.c.table("agent_runs").select("*")
        if lead_id:
            q = q.eq("lead_id", lead_id)
        if workflow:
            q = q.eq("workflow", workflow)
        return q.order("started_at", desc=True).limit(limit).execute().data

    def get_run(self, run_id: str) -> dict:
        rows = self.c.table("agent_runs").select("*").eq("id", run_id).limit(1).execute().data
        if not rows:
            raise LookupError(f"실행 기록 없음: {run_id}")
        run = rows[0]
        run["steps"] = (self.c.table("agent_steps").select("*").eq("run_id", run_id)
                        .order("step_no").execute().data)
        return run

    def insert_lead(self, row: dict) -> dict:
        return self.c.table("leads").insert(row).execute().data[0]

    def save_research(self, lead_id: str, run_id: str | None, facts: list[dict], score: dict | None) -> None:
        if facts:
            self.c.table("research_facts").insert([{**f, "lead_id": lead_id, "run_id": run_id} for f in facts]).execute()
        if score:
            self.c.table("lead_scores").insert({
                "lead_id": lead_id, "run_id": run_id,
                "score": score["score"], "tags": score["tags"], "breakdown": score["breakdown"],
            }).execute()

    # --- 업종 브리프 ---

    def latest_industry_brief(self, industry_key: str) -> dict | None:
        rows = (self.c.table("industry_briefs").select("*").eq("industry_key", industry_key)
                .order("collected_at", desc=True).limit(1).execute().data)
        return rows[0] if rows else None

    def insert_industry_brief(self, industry_key: str, items: list[dict]) -> dict:
        return self.c.table("industry_briefs").insert({"industry_key": industry_key, "items": items}).execute().data[0]

    # --- 피드백 메모리 ---

    def list_feedback(self, scope: str | None = None, scope_key: str | None = None, status: str | None = "active",
                      limit: int = 200) -> list[dict]:
        q = self.c.table("feedback_notes").select("*")
        if scope:
            q = q.eq("scope", scope)
        if scope_key:
            q = q.eq("scope_key", scope_key)
        if status and status != "all":
            q = q.eq("status", status)
        return q.order("created_at", desc=True).limit(limit).execute().data

    def feedback_by_ids(self, ids: list[str]) -> dict[str, dict]:
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        rows = self.c.table("feedback_notes").select("*").in_("id", ids).execute().data
        return {r["id"]: r for r in rows}

    def get_feedback(self, feedback_id: str) -> dict:
        rows = self.c.table("feedback_notes").select("*").eq("id", feedback_id).limit(1).execute().data
        if not rows:
            raise LookupError(f"피드백 없음: {feedback_id}")
        return rows[0]

    def insert_feedback(self, row: dict) -> dict:
        return self.c.table("feedback_notes").insert(row).execute().data[0]

    def update_feedback(self, feedback_id: str, fields: dict) -> dict:
        rows = self.c.table("feedback_notes").update({**fields, "updated_at": _now()}).eq("id", feedback_id).execute().data
        if not rows:
            raise LookupError(f"피드백 없음: {feedback_id}")
        return rows[0]

    def delete_feedback(self, feedback_id: str) -> None:
        self.c.table("feedback_notes").delete().eq("id", feedback_id).execute()

    def bump_feedback_applied(self, ids: list[str]) -> None:
        for fid, row in self.feedback_by_ids(ids).items():
            self.c.table("feedback_notes").update({"applied_count": (row.get("applied_count") or 0) + 1}).eq("id", fid).execute()

    def count_feedback(self, status: str = "active") -> int:
        res = self.c.table("feedback_notes").select("id", count="exact").eq("status", status).execute()
        return res.count or 0

    # --- 대화형 에이전트 ---

    def get_chat_session(self, session_id: str) -> dict | None:
        rows = self.c.table("chat_sessions").select("*").eq("id", session_id).limit(1).execute().data
        return rows[0] if rows else None

    def find_chat_session(self, external_key: str) -> dict | None:
        rows = self.c.table("chat_sessions").select("*").eq("external_key", external_key).limit(1).execute().data
        return rows[0] if rows else None

    def insert_chat_session(self, row: dict) -> dict:
        return self.c.table("chat_sessions").insert(row).execute().data[0]

    def update_chat_session(self, session_id: str, fields: dict) -> None:
        self.c.table("chat_sessions").update({**fields, "updated_at": _now()}).eq("id", session_id).execute()

    def insert_chat_turn(self, row: dict) -> dict:
        return self.c.table("chat_turns").insert(row).execute().data[0]

    def list_chat_turns(self, session_id: str, limit: int = 200) -> list[dict]:
        return (self.c.table("chat_turns").select("*").eq("session_id", session_id)
                .order("created_at").limit(limit).execute().data)

    def chat_event_seen(self, event_id: str) -> bool:
        rows = self.c.table("chat_turns").select("id").eq("event_id", event_id).limit(1).execute().data
        return bool(rows)
