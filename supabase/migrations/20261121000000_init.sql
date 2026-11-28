-- 초기 스키마: 리드, 리서치 결과, 점수, 메시지·승인·답장, 인계, 에이전트 실행 로그
-- 모든 테이블은 RLS를 켜고 정책을 두지 않는다. 서버(service role)만 읽고 쓴다.
-- 공개 키(anon)로는 아무것도 읽을 수 없다.

-- 리드 상태
create type public.lead_status as enum (
  'new',                -- 막 들어옴
  'researching',        -- 리서치 에이전트 실행 중
  'researched',         -- 리서치·점수 완료
  'awaiting_approval',  -- 메일 초안이 승인 대기 중
  'contacted',          -- 첫 메일 발송됨
  'replied',            -- 답장 받음
  'needs_human',        -- 판단이 애매해 사람에게 넘김
  'handed_off',         -- 인간 SDR에게 인계 완료
  'unsubscribed'        -- 수신 거부. 모든 발송 중단
);

-- 에이전트 실행 기록 (다른 테이블이 참조하므로 먼저 만든다)
create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid,
  workflow text not null,              -- research, draft, reply_triage, handoff ...
  trigger text not null,               -- schedule, new_lead, approval, reply, manual
  model text,
  status text not null default 'running'
    check (status in ('running', 'succeeded', 'failed', 'stopped')),
  stop_reason text,                    -- finished, max_steps, error ...
  final_text text,
  error text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- 실행의 한 단계: LLM 응답 또는 도구 호출 하나. 평가와 보고서에 쓴다
create table public.agent_steps (
  id bigint generated always as identity primary key,
  run_id uuid not null references public.agent_runs(id) on delete cascade,
  step_no int not null,
  kind text not null check (kind in ('llm', 'tool')),
  tool_name text,
  input jsonb,
  output jsonb,
  text text,                           -- LLM이 남긴 설명(도구를 고른 이유 등)
  is_error boolean not null default false,
  duration_ms int,
  created_at timestamptz not null default now()
);
create index agent_steps_run_idx on public.agent_steps (run_id, step_no);

-- 리드 (가상·비식별 데이터만 넣는다)
create table public.leads (
  id uuid primary key default gen_random_uuid(),
  name text,
  email text,
  phone text,
  company_name text not null,
  company_domain text,
  biz_reg_no text,                     -- 사업자등록번호
  title text,                          -- 직급 원문 (점수 계산 시 사전으로 정규화)
  source text not null default 'other'
    check (source in ('demo_request', 'web_inquiry', 'seminar', 'business_card', 'other')),
  inbound_at timestamptz not null default now(),
  status public.lead_status not null default 'new',
  unsubscribed_at timestamptz,
  merged_into uuid references public.leads(id),  -- 중복으로 합쳐진 경우 대표 리드
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index leads_email_idx on public.leads (lower(email));
create index leads_status_idx on public.leads (status);

alter table public.agent_runs
  add constraint agent_runs_lead_fk foreign key (lead_id) references public.leads(id) on delete set null;

-- 리서치로 찾은 사실. 출처 없는 사실은 저장하지 않는다
create table public.research_facts (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  run_id uuid references public.agent_runs(id) on delete set null,
  field text not null,                 -- summary, products, revenue, operating_margin, recent_disclosure ...
  value text not null,
  source_type text not null check (source_type in ('dart', 'homepage', 'news', 'lead_record', 'other')),
  source_url text not null check (length(source_url) > 0),
  retrieved_at timestamptz not null default now()
);
create index research_facts_lead_idx on public.research_facts (lead_id);

-- 규칙 기반 점수와 근거 태그
create table public.lead_scores (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  run_id uuid references public.agent_runs(id) on delete set null,
  score numeric not null,
  tags text[] not null default '{}',
  breakdown jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index lead_scores_lead_idx on public.lead_scores (lead_id, created_at desc);

-- 고객에게 나갈 메시지. 사람 승인 없이는 sent가 될 수 없다
create table public.messages (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  run_id uuid references public.agent_runs(id) on delete set null,
  kind text not null check (kind in ('initial', 'followup')),
  subject text not null,
  body text not null,
  citations jsonb not null default '[]',   -- 초안에 쓴 근거 (research_facts id, 지식베이스 문서)
  status text not null default 'pending_approval'
    check (status in ('pending_approval', 'approved', 'rejected', 'sent', 'cancelled')),
  provider_message_id text,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
create index messages_lead_idx on public.messages (lead_id);
create index messages_status_idx on public.messages (status);

-- 승인 기록: 누가, 어디서, 무엇을 결정했는지
create table public.approvals (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages(id) on delete cascade,
  decision text not null check (decision in ('approved', 'rejected', 'edited')),
  decided_by text not null,
  channel text not null,               -- slack, email, admin
  edited_body text,
  note text,
  decided_at timestamptz not null default now()
);

-- 받은 답장과 분류 결과
create table public.replies (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  message_id uuid references public.messages(id) on delete set null,
  run_id uuid references public.agent_runs(id) on delete set null,
  body text not null,
  received_at timestamptz not null default now(),
  classification text check (classification in (
    'interested', 'question', 'not_now', 'not_decision_maker',
    'out_of_office', 'unsubscribe', 'unclear')),
  confidence numeric,
  handled_by text check (handled_by in ('agent', 'human'))
);

-- 인간 SDR 인계 자료
create table public.handoffs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  run_id uuid references public.agent_runs(id) on delete set null,
  summary jsonb not null,              -- 리서치, 접촉 이력, 관심사, 선별 근거, 권장 조치
  screening_questions jsonb not null default '[]',
  sent_to text,
  channel text,
  created_at timestamptz not null default now()
);

-- updated_at 자동 갱신
create function public.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end;
$$;
create trigger leads_touch before update on public.leads
  for each row execute function public.touch_updated_at();

-- 설계 원칙 1: 수신 거부 시 자동 중단.
-- 리드가 수신 거부되면 보내지 않은 메시지를 모두 취소한다.
create function public.cancel_messages_on_unsubscribe() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.unsubscribed_at is not null and old.unsubscribed_at is null then
    new.status := 'unsubscribed';
    update public.messages
      set status = 'cancelled'
      where lead_id = new.id and status in ('pending_approval', 'approved');
  end if;
  return new;
end;
$$;
create trigger leads_unsubscribe before update on public.leads
  for each row execute function public.cancel_messages_on_unsubscribe();

-- 앱 코드에 버그가 있어도 DB가 막는다:
-- 수신 거부 리드에는 발송 불가, 승인되지 않은 메시지는 발송 불가.
create function public.guard_message_send() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status = 'sent' and old.status is distinct from 'sent' then
    if old.status <> 'approved' then
      raise exception 'message % is not approved (status=%)', new.id, old.status;
    end if;
    if exists (select 1 from public.leads l where l.id = new.lead_id and l.unsubscribed_at is not null) then
      raise exception 'lead % has unsubscribed', new.lead_id;
    end if;
    new.sent_at := coalesce(new.sent_at, now());
  end if;
  return new;
end;
$$;
create trigger messages_guard_send before update on public.messages
  for each row execute function public.guard_message_send();

-- RLS: 정책 없이 켜 두면 service role만 접근 가능하다
alter table public.agent_runs enable row level security;
alter table public.agent_steps enable row level security;
alter table public.leads enable row level security;
alter table public.research_facts enable row level security;
alter table public.lead_scores enable row level security;
alter table public.messages enable row level security;
alter table public.approvals enable row level security;
alter table public.replies enable row level security;
alter table public.handoffs enable row level security;
