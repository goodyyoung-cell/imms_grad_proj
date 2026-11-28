-- 소식 기반 초안(제목 후보·소재), 업종 브리프, 피드백 메모리, 대화형 에이전트(웹·Slack).
-- 적용: Supabase SQL Editor에 통째로 붙여 넣고 실행한다. 여러 번 실행해도 안전하다.

-- messages: 제목 후보, 소재, 이전 버전, Slack 승인 카드 위치
alter table public.messages
  add column if not exists subject_options jsonb not null default '[]',
  add column if not exists subject_choice text not null default 'general',
  add column if not exists hook jsonb,
  add column if not exists revisions jsonb not null default '[]',
  add column if not exists slack_ref jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'messages_subject_choice_check') then
    alter table public.messages
      add constraint messages_subject_choice_check
      check (subject_choice in ('general', 'company', 'hook', 'custom'));
  end if;
end $$;

-- 업종 브리프: 업종별 최근 화두 기사. 리드마다 검색하지 않고 업종 단위로 모아 두고 쓴다
create table if not exists public.industry_briefs (
  id uuid primary key default gen_random_uuid(),
  industry_key text not null,
  items jsonb not null default '[]',      -- [{title, summary, url, publisher, published_at}]
  collected_at timestamptz not null default now()
);
create index if not exists industry_briefs_key_idx on public.industry_briefs (industry_key, collected_at desc);
alter table public.industry_briefs enable row level security;

-- 피드백 메모리: 챗이나 화면에서 남긴 지침. 다음 메일·답장·통화 스크립트를 만들 때 반영한다
create table if not exists public.feedback_notes (
  id uuid primary key default gen_random_uuid(),
  scope text not null check (scope in ('lead', 'industry', 'global')),
  scope_key text,                           -- lead: 리드 id, industry: 업종 키, global: null
  kind text not null default 'other' check (kind in ('tone', 'length', 'topic', 'avoid', 'cta', 'other')),
  text text not null check (length(text) between 1 and 300),
  source text not null default 'manual' check (source in ('chat_web', 'chat_slack', 'manual')),
  source_ref text,                          -- 챗 세션 id 또는 메시지 id
  created_by text not null,
  status text not null default 'active' check (status in ('active', 'archived')),
  applied_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((scope = 'global' and scope_key is null) or (scope <> 'global' and scope_key is not null))
);
create index if not exists feedback_notes_scope_idx on public.feedback_notes (scope, scope_key, status);
alter table public.feedback_notes enable row level security;

-- 대화형 에이전트: 대화 세션과 발화. Slack은 채널·스레드마다 세션 하나 (external_key)
create table if not exists public.chat_sessions (
  id uuid primary key default gen_random_uuid(),
  channel text not null check (channel in ('web', 'slack')),
  external_key text unique,                 -- slack:{채널}:{스레드 ts}
  user_name text not null,
  context jsonb not null default '{}',      -- 마지막으로 다룬 message_id, lead_id
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.chat_sessions enable row level security;

create table if not exists public.chat_turns (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  text text not null,
  actions jsonb not null default '[]',
  run_id uuid references public.agent_runs(id) on delete set null,
  event_id text unique,                     -- Slack 이벤트 id. 재전송된 이벤트를 한 번만 처리한다
  created_at timestamptz not null default now()
);
create index if not exists chat_turns_session_idx on public.chat_turns (session_id, created_at);
alter table public.chat_turns enable row level security;
