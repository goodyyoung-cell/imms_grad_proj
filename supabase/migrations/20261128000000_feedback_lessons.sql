-- 피드백에서 배우기: 담당자의 수정·반려·대화형 피드백에서 '작성 지침'을 뽑아 다음 초안에 반영한다.
-- 모델을 다시 훈련하지 않는다 (파인튜닝은 이번 학기 범위 밖). 지침을 저장해 두고 초안을 쓸 때 함께 준다.
-- 근거 검사·수신 거부·승인 같은 안전장치는 코드가 하므로 지침이 덮어쓸 수 없다.

create table public.feedback_lessons (
  id uuid primary key default gen_random_uuid(),
  text text not null check (length(text) between 5 and 200),   -- 지침 한 문장 (예: 첫 메일에서는 가격을 언급하지 않는다)
  scope text not null default 'draft' check (scope in ('draft')),
  source text not null check (source in ('edit', 'reject', 'chat')),  -- 수정 승인 / 반려 / 대화형 피드백
  evidence text,                       -- 무엇을 보고 만든 지침인지 (담당자 피드백 원문, 반려 사유 등)
  status text not null default 'active' check (status in ('active', 'disabled')),
  message_id uuid references public.messages(id) on delete set null,
  lead_id uuid references public.leads(id) on delete set null,
  run_id uuid references public.agent_runs(id) on delete set null,
  created_by text not null,            -- 지침을 만든 사람의 결정 (승인자·피드백 입력자)
  created_at timestamptz not null default now()
);
create index feedback_lessons_active_idx on public.feedback_lessons (scope, status, created_at desc);
create index feedback_lessons_message_idx on public.feedback_lessons (message_id);
create index feedback_lessons_lead_idx on public.feedback_lessons (lead_id);
create index feedback_lessons_run_idx on public.feedback_lessons (run_id);
alter table public.feedback_lessons enable row level security;

-- 대화형 피드백으로 초안을 다시 쓴 기록 (승인 전 단계)
create table public.message_revisions (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages(id) on delete cascade,
  run_id uuid references public.agent_runs(id) on delete set null,
  feedback text not null check (length(feedback) between 1 and 2000),
  before_subject text,
  before_body text,
  after_subject text,
  after_body text,
  requested_by text not null,
  created_at timestamptz not null default now()
);
create index message_revisions_message_idx on public.message_revisions (message_id, created_at);
create index message_revisions_run_idx on public.message_revisions (run_id);
alter table public.message_revisions enable row level security;

-- 초안을 쓸 때 적용한 지침 [{id, text}] (어떤 피드백이 초안에 영향을 줬는지 설명하기 위해)
alter table public.messages add column applied_lessons jsonb not null default '[]';
