-- 승인 화면에서 평가용 자료를 모으고(반려 사유, 수정 전 본문, 사람이 검토한 시간),
-- 수작업 기준 시간을 기록하며, 수신 동의 근거를 남긴다.

-- approvals: 어떤 이유로 반려했는지, 수정 승인이면 수정 전 초안, 승인 화면에서 쓴 시간(초)
alter table public.approvals
  add column reason_code text
    check (reason_code in ('factual_error', 'tone', 'wrong_target', 'forbidden_phrase', 'other')),
  add column original_body text,
  add column review_seconds int check (review_seconds is null or review_seconds >= 0);

-- 같은 일을 사람이 직접 했을 때 걸린 시간. 평가에서 에이전트 방식과 비교한다
create table public.manual_timings (
  id uuid primary key default gen_random_uuid(),
  task text not null check (task in ('research', 'draft', 'prioritize', 'handoff', 'reply_triage')),
  seconds int not null check (seconds > 0),
  label text,                          -- 어떤 리드·시나리오로 쟀는지
  recorded_by text not null,
  created_at timestamptz not null default now()
);
create index manual_timings_task_idx on public.manual_timings (task);
alter table public.manual_timings enable row level security;

-- leads: 담당자가 직접 확인해 남긴 수신 동의 근거 (웹 폼 동의는 consent_at만 쓴다)
alter table public.leads add column consent_note text;
