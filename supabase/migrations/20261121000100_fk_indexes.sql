-- 자주 조회하거나 삭제 시 연쇄되는 외래키에 인덱스 추가 (Supabase 성능 점검 권고)
create index leads_merged_into_idx on public.leads (merged_into);
create index agent_runs_lead_idx on public.agent_runs (lead_id);
create index approvals_message_idx on public.approvals (message_id);
create index replies_lead_idx on public.replies (lead_id);
create index replies_message_idx on public.replies (message_id);
create index handoffs_lead_idx on public.handoffs (lead_id);
