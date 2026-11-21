"""Supabase 접근. service role 키로 서버에서만 쓴다 (모든 테이블이 RLS로 잠겨 있음)."""

from __future__ import annotations

from typing import Any

from .config import Settings, load_settings


def get_client(settings: Settings | None = None):
    s = settings or load_settings()
    if not s.supabase_url or not s.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다")
    from supabase import create_client

    return create_client(s.supabase_url, s.supabase_service_role_key)


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

    def save_research(self, lead_id: str, run_id: str | None, facts: list[dict], score: dict | None) -> None:
        if facts:
            self.c.table("research_facts").insert([{**f, "lead_id": lead_id, "run_id": run_id} for f in facts]).execute()
        if score:
            self.c.table("lead_scores").insert({
                "lead_id": lead_id, "run_id": run_id,
                "score": score["score"], "tags": score["tags"], "breakdown": score["breakdown"],
            }).execute()
