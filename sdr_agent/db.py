"""Supabase 접근. 비밀 키(sb_secret_...)로 서버에서만 쓴다 (모든 테이블이 RLS로 잠겨 있음)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
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
        return self.c.table("messages").insert({
            "lead_id": lead_id, "run_id": run_id, "kind": kind,
            "subject": draft["subject"], "body": draft["body"], "citations": draft.get("citations", []),
        }).execute().data[0]

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

    # --- 답장·인계 ---

    def insert_reply(self, row: dict) -> dict:
        return self.c.table("replies").insert(row).execute().data[0]

    def list_replies(self, lead_id: str) -> list[dict]:
        return self.c.table("replies").select("*").eq("lead_id", lead_id).order("received_at").execute().data

    def insert_handoff(self, row: dict) -> dict:
        return self.c.table("handoffs").insert(row).execute().data[0]

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
