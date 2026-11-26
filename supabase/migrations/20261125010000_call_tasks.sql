-- 메일 발송 뒤 SDR이 전화로 팔로업하는 과제와 콜 스크립트
-- 스크립트는 에이전트가 만들고(내부 준비 자료), 전화는 사람이 건다. 결과도 사람이 기록한다.

create table public.call_tasks (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  message_id uuid references public.messages(id) on delete set null,   -- 팔로업 대상 메일
  run_id uuid references public.agent_runs(id) on delete set null,
  script jsonb not null,               -- 오프닝, 메일 요약, 대화 소재, 질문, 반대 의견 대응, 음성 메시지, 인용
  status text not null default 'pending'
    check (status in ('pending', 'done', 'cancelled')),
  due_at timestamptz not null,         -- 전화할 시각 (발송 후 N영업일)
  outcome text check (outcome in (
    'interested', 'not_now', 'needs_info', 'no_answer', 'voicemail', 'wrong_contact', 'unsubscribe')),
  outcome_notes text,
  called_by text,
  called_at timestamptz,
  created_at timestamptz not null default now()
);
create index call_tasks_lead_idx on public.call_tasks (lead_id);
create index call_tasks_message_idx on public.call_tasks (message_id);
create index call_tasks_run_idx on public.call_tasks (run_id);
create index call_tasks_due_idx on public.call_tasks (status, due_at);
alter table public.call_tasks enable row level security;

-- 설계 원칙 1 확장: 수신 거부 시 보내지 않은 메시지와 함께 걸지 않은 전화도 취소한다.
create or replace function public.cancel_messages_on_unsubscribe() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.unsubscribed_at is not null and old.unsubscribed_at is null then
    new.status := 'unsubscribed';
    update public.messages
      set status = 'cancelled'
      where lead_id = new.id and status in ('pending_approval', 'approved');
    update public.call_tasks
      set status = 'cancelled'
      where lead_id = new.id and status = 'pending';
  end if;
  return new;
end;
$$;
