-- 리드 유입 경로와 개인정보 수집 동의 기록
-- intake_channel: 리드가 어떤 입구로 들어왔는지 (source는 '데모 요청·세미나' 같은 영업상 유입 유형)
-- consent_at: 웹 폼에서 개인정보 수집·이용과 AI 활용 고지에 동의한 시각. 직원 업로드는 비어 있다

alter table public.leads
  add column intake_channel text not null default 'manual'
    check (intake_channel in ('web_form', 'sheet_upload', 'manual', 'seed')),
  add column consent_at timestamptz;

create index leads_company_domain_idx on public.leads (lower(company_domain));
