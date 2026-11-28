# 프론트엔드(관리 화면) 인계

작성: 2026-11-26 · 대상: 관리 화면을 맡은 팀원과 그 팀원의 Claude

백엔드는 Cloud Run에 배포되어 실제 키로 돌고 있다. 관리 화면은 **이 문서의 API만 부르면** 만들 수 있게 해 두었다. 백엔드 코드를 고칠 일은 거의 없어야 한다. 고쳐야 하면 `sdr_agent/app.py`만 건드리고, 결정은 `docs/decisions.md`에 남긴다.

## 시작하기 전에

1. **PR #2(`feat/skeleton` → `main`)를 리뷰하고 머지한다.** 백엔드 전체가 이 PR에 있다. PR #1의 내용은 PR #2에 이미 들어 있으므로 PR #1은 닫아도 된다.
2. `main`에서 브랜치를 딴다: `git checkout main && git pull && git checkout -b feat/admin-ui`
3. 프론트엔드 코드는 저장소의 `web/` 폴더에 둔다 (백엔드 `sdr_agent/`와 섞지 않는다).
4. 관리자 비밀번호(`ADMIN_PASSWORD`)는 저장소에 없다. 팀원에게 따로 받는다.

## 화면의 목적

시연(시나리오 영상·스크린캡처)에서 **에이전트가 무엇을 했고, 사람이 어디서 결정하는지**가 보이면 된다.
화려함보다 흐름이 한눈에 보이는 게 중요하다. 흐름도: https://claude.ai/artifact/BECcbsFH1if9dP1Uqo9skG (팀 공유 필요)

```
유입 → 리서치·점수(자율) → 첫 메일 초안(자율) → [사람 승인] → 샌드박스 발송
     → 1시간 뒤 SDR 전화(콜 스크립트, 자율 생성) → 통화 결과 기록(사람)
     → 답장 분류(자율, 애매하면 사람) → 인간 SDR 인계 요약(자율)
```

## 권장 화면 (우선순위 순)

| # | 화면 | 쓰는 API | 보여 줄 것 |
|---|------|---------|-----------|
| 1 | 로그인 | `POST /auth/login` | 이름 + 팀 비밀번호 |
| 2 | 대시보드 | `GET /admin/summary` | 상태별 리드 수, 승인 대기 수, 지금 걸 전화 수, 최근 에이전트 실행 |
| 3 | 승인 대기 메일 | `GET /admin/messages`, `POST /admin/messages/{id}/decision` | 제목·본문·인용 근거(`citations`), [승인] [수정 후 승인] [반려] |
| 4 | 오늘 걸 전화 | `GET /admin/calls`, `POST /admin/calls/{id}/outcome` | `script_text`(그대로 출력하면 됨), 결과 입력 폼(수신 여부·희망 미팅·Compelling Reason) |
| 5 | 리드 목록·상세 | `GET /admin/leads`, `GET /admin/leads/{id}` | 점수·근거 태그, 리서치 사실과 출처 링크, 메일·답장·통화·인계 이력 |
| 6 | 실행 로그 | `GET /admin/runs`, `GET /admin/runs/{id}` | 단계별로 LLM이 쓴 이유(`text`)와 고른 도구(`tool_name`) — **"에이전트가 스스로 골랐다"를 보여 주는 핵심 화면** |
| 7 | 시연 도구 (선택) | `POST /webhooks/reply`, `POST /jobs/research`, `POST /jobs/draft`, `POST /admin/leads/import` | 모의 답장 넣기, 리서치·초안 실행 버튼, 시트 업로드 |

## 연결

- **API 주소**: `https://sdr-agent-840381264541.asia-northeast3.run.app` (로컬 백엔드: `http://localhost:8000`)
- **API 문서(자동 생성)**: 위 주소 뒤에 `/docs` — 모든 요청·응답 형식을 브라우저에서 바로 시험할 수 있다
- **CORS**: `http://localhost:3000, 3001, 4173, 5173, 8080`(127.0.0.1 포함)은 이미 허용. 화면을 다른 주소(예: Vercel, Firebase Hosting)에 올리면 그 주소를 서버 설정 `ALLOWED_ORIGINS`에 추가해야 한다 (`.env`에 쉼표로 넣고 `bash scripts/sync_secrets.sh`).

### 로그인

```http
POST /auth/login
{"name": "김담당", "password": "<팀 비밀번호>"}

→ 200 {"token": "adm.xxxx.yyyy", "name": "김담당", "expires_at": "2026-11-27T06:00:00+00:00"}
→ 401 비밀번호 틀림 · 429 1분에 5번 넘게 시도
```

이후 모든 요청에 `Authorization: Bearer adm.xxxx.yyyy`. 토큰은 8시간 유효하다. **메모리나 sessionStorage에 두고, 401이 오면 로그인 화면으로 보낸다.**
승인·통화 결과를 보낼 때 `decided_by`/`called_by`를 비우면 로그인 이름이 자동으로 기록된다.

**하지 말 것**: `JOB_TOKEN`(서버 공용 토큰)을 화면 코드나 `.env.local`에 넣지 않는다. 저장소가 공개이고, 그 토큰으로 승인·발송까지 할 수 있다.

## API

모든 응답은 JSON. 오류는 `{"detail": "한국어 설명"}` (FastAPI 기본 형식). 시각은 UTC ISO 8601이므로 화면에서 한국 시간으로 바꿔 보여 준다.

### 조회

| 요청 | 응답 요약 |
|------|----------|
| `GET /admin/me` | `{"name", "kind": "admin"}` |
| `GET /admin/summary` | `{"lead_status_counts": {"new": 97, ...}, "pending_approvals", "calls_due", "calls_pending", "recent_runs": [run + lead]}` |
| `GET /admin/leads?status=&q=&limit=50&offset=0` | `{"total", "leads": [{id, name, company_name, title, source, intake_channel, status, inbound_at, updated_at, score, tags}]}` — `q`는 회사명 검색 |
| `GET /admin/leads/{id}` | `{"lead", "merged_leads", "facts", "score", "messages", "replies", "calls"(+script_text), "handoffs", "runs"}` |
| `GET /admin/messages?status=pending_approval` | `{"messages": [{id, lead_id, kind, subject, body, citations, status, created_at, sent_at, lead}]}` |
| `GET /admin/calls?due_only=true` | `{"calls": [{id, lead_id, due_at, status, script, script_text, lead}]}` — `due_only=false`면 예정된 것까지 |
| `GET /admin/runs?lead_id=&workflow=&limit=50` | `{"runs": [{id, workflow, trigger, model, status, stop_reason, started_at, finished_at, lead}]}` |
| `GET /admin/runs/{id}` | `{..run, "steps": [{step_no, kind: "llm"\|"tool", text, tool_name, input, output, is_error, duration_ms}]}` |

### 사람의 결정

| 요청 | 본문 | 결과 |
|------|------|------|
| `POST /admin/messages/{id}/decision` | `{"decision": "approved"\|"edited"\|"rejected", "edited_body"?, "note"?}` | 승인하면 바로 샌드박스 발송. 첫 메일이면 `call_task`(전화 과제)가 함께 생긴다 |
| `POST /admin/calls/{id}/outcome` | `{"outcome", "email_received"?, "meeting_preference"?, "compelling_reason"?, "notes"?}` | outcome: `interested`(→인계) `not_now` `needs_info` `no_answer` `voicemail` `wrong_contact` `unsubscribe`(→연락 중단) |
| `POST /admin/leads/{id}/unsubscribe` | `{"note"?}` | 담당자가 수신 거부 확정. 남은 메일·전화 자동 취소 |
| `POST /admin/leads/import` | multipart: `file`(CSV·xlsx) 또는 `google_sheet`, `dry_run`(기본 true) | 미리보기 → 확인 후 `dry_run=false`로 다시 |
| `POST /admin/messages/{id}/revise` | `{"feedback": "더 짧게 써 줘", "save_as_lesson"?: true}` | 대화형 피드백으로 승인 전 첫 메일을 다시 씀 (발송 안 함, LLM 비용). `message`에 새 초안, `lessons.saved`에 저장된 지침 |
| `GET /admin/messages/{id}/revisions` | | 다시 쓴 기록 (피드백, 이전·이후 본문) |
| `GET /admin/lessons?status=active` | | 피드백에서 배운 작성 지침 (`source`: edit 수정 승인 · reject 반려 · chat 대화형) |
| `POST /admin/lessons/{id}/status` | `{"status": "active"\|"disabled"}` | 잘못 배운 지침을 끄거나 다시 켬 |

승인(`decision`) 응답의 `lessons.saved`에는 이번 수정·반려에서 배운 지침이 온다. 메일의 `applied_lessons`는 그 초안을 쓸 때 반영한 지침이다.

### 시연용 동작 (LLM 비용이 든다 — 버튼에는 "실행" 확인을 둔다)

| 요청 | 본문 | 설명 |
|------|------|------|
| `POST /jobs/research` | `{"lead_ids": [..]}` 또는 `{"limit": 1}`, `"trigger": "manual"` | 리서치 에이전트 실행 (리드당 수십 초) |
| `POST /jobs/draft` | 같음 | 조사 끝난 리드의 첫 메일 초안 |
| `POST /webhooks/reply` | `{"lead_id" 또는 "from_email", "body": "다음 주 화요일 가능합니다"}` | 모의 답장. 분류 결과와 다음 행동이 돌아온다 |
| `POST /jobs/handoff` | `{"lead_id"}` | 인계 요약 생성 |

## 화면에 쓸 한국어 라벨

**리드 상태** (`status`)

| 값 | 라벨 | 의미 |
|----|------|------|
| `new` | 신규 | 조사 대기 |
| `researching` | 조사 중 | |
| `researched` | 조사 완료 | 초안 대기 |
| `awaiting_approval` | 승인 대기 | 사람이 볼 차례 |
| `contacted` | 연락함 | 메일 발송됨 |
| `replied` | 답장 받음 | 나중에 연락 등 |
| `needs_human` | 사람 확인 필요 | 애매함·반려·수신 거부 의심 |
| `handed_off` | 인계 완료 | |
| `unsubscribed` | 수신 거부 | 모든 연락 중단 |

**실행 종류** (`workflow`): `research` 리서치 · `draft` 메일 초안 · `reply_triage` 답장 분류 · `call_script` 콜 스크립트 · `handoff` 인계 요약

**유입 경로** (`source`): `demo_request` 데모 요청 · `web_inquiry` 웹 문의 · `seminar` 세미나 · `business_card` 명함 · `other` 기타

**메일 상태**: `pending_approval` 승인 대기 · `approved` 승인됨 · `sent` 발송됨(샌드박스) · `rejected` 반려 · `cancelled` 취소(수신 거부)

**콜 과제 알림** (`script.notices[].kind`): `reply` 고객이 답장함 · `handoff` 인계됨 · `unsubscribe_suspected` 수신 거부 의심 — 통화 보류. 알림이 있으면 스크립트 위에 눈에 띄게 보여 준다.

## 지금 DB에 있는 시연용 데이터

- 리드 106건 (대표 100 + 중복 병합 6). 회사는 실제 상장사, 담당자는 가상, 이메일은 `.example`이라 발송될 수 없다
- 리서치 완료 3건(LS ELECTRIC, SK이노베이션, 삼양식품), 발송된 메일 2건, 예정된 전화 2건, 승인 대기 1건
- 평일 07:30 리서치 5건, 08:00 초안이 자동으로 돈다 (Cloud Scheduler)

## 로컬에서 백엔드까지 띄우려면 (선택)

화면만 만들 때는 배포된 API를 쓰면 되고, 백엔드를 고칠 때만 필요하다.

GCP 프로젝트 `gen-lang-client-0136144273`에 개발 권한이 있으면(Cloud Run 배포·로그 보기·Secret Manager 읽기·Scheduler 관리) 키를 메신저로 받을 필요 없이 `.env`를 만들 수 있다.

```bash
gcloud auth login && gcloud config set project gen-lang-client-0136144273
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
python scripts/pull_env.py              # 배포된 Cloud Run 설정·Secret Manager에서 .env 생성 (키는 화면에 안 나옴)
uvicorn sdr_agent.app:app --reload     # http://localhost:8000/docs
pytest                                  # 키 없이 도는 테스트
```

`.env`는 실제 Supabase와 실제 LLM 키를 쓴다. 로컬에서 돌린 작업도 같은 DB에 남고 LLM 비용이 든다.

## 지켜야 할 것 (CLAUDE.md 요약)

- 고객에게 나가는 행동(발송)은 사람 승인 버튼으로만. 화면에 "자동 발송" 같은 기능을 만들지 않는다.
- 점수는 규칙이 매긴다. 화면에 점수와 함께 근거 태그(`tags`)를 보여 준다.
- 리서치 사실은 출처 링크(`source_url`)와 함께 보여 준다.
- AI가 쓴 초안·스크립트임을 화면에서 알 수 있게 한다.


## Slack·지표·동의 API

Slack 알림의 딥링크는 `ADMIN_WEB_URL` 기준으로 아래 주소를 연다. 라우트를 이 규칙으로 만들어 주세요.
`/m/{message_id}`(메일 검토), `/l/{lead_id}`(리드), `/c/{call_task_id}`(전화 과제), `/`(홈). 설정은 `docs/admin-slack.md`.

| 메서드·주소 | 설명 |
|-------------|------|
| `POST /admin/messages/{id}/decision` | 기존 본문에 선택 항목 추가: `reason_code`(`factual_error`, `tone`, `wrong_target`, `forbidden_phrase`, `other`. 반려 때 보낸다), `review_seconds`(화면에서 검토한 초) |
| `GET /admin/metrics` | 평가 지표(`approval`, `replies`, `runs`, `calls`, `first_draft`, `compare`, `per_lead`, `leads`)와 CSV용 `rows`(`[구분, 지표, 값]` 목록) |
| `GET /admin/baseline` | 수작업 기준 시간 목록(`timings`), 과제 이름(`tasks`), 과제별 통계(`stats`: n, median, mean) |
| `POST /admin/baseline` | `{task, minutes 또는 seconds, label?}`. `task`는 `research`, `prioritize`, `draft`, `handoff`, `reply_triage`. 기록자는 로그인 이름. 0초 초과 8시간 이하 |
| `POST /admin/leads/{id}/consent` | `{note}` 수신 동의를 직접 확인한 근거. 로그인 이름이 함께 남는다 |
| `GET /admin/replies?classification=&handled_by=&limit=100` | `{replies:[{id, lead_id, message_id, body, received_at, classification, confidence, handled_by, run_id, lead}]}` 최근 순. 한도 200. `classification`은 `interested`, `question`, `not_now`, `not_decision_maker`, `out_of_office`, `unsubscribe`, `unclear`, `handled_by`는 `agent`, `human`. 잘못된 값은 400 |
| `GET /admin/approvals?limit=200` | `{approvals:[{id, message_id, decision, decided_by, decided_at, note, reason_code, original_body, review_seconds, channel, message, lead}]}` 최근 순. 한도 500. `message`는 `{id, lead_id, kind, subject, body, status}`, 메일이 없으면 `message`와 `lead`는 null |
| `GET /admin/kb` | `{docs:[{id, title, sections:[{id, heading, text}]}]}` 에이전트가 인용할 수 있는 승인된 자료. 파일 이름 순. 메일 근거의 `02_pricing#플랜` 같은 id가 `sections[].id`와 같다. 읽기 전용 |
| `GET /admin/status` | 연결·안전장치 상태. `mode`(`send`는 항상 `sandbox`, `llm_default`, `providers.claude·gemini` 키 유무), `slack`(`configured`, `signing_configured`, `channel`, `approvers` 수, `notify_kinds`, `web_url`), `safeguards`(`require_consent`, `max_sends_per_day`, `sent_today` 한국 시간 오늘 발송 수, `sender_name`, `sender_contact`), `knowledge_docs` 수. 비밀값은 싣지 않는다 |
| `POST /admin/notify-test` | 본문은 선택 `{text}`. Slack에 연결 시험 메시지를 보내고 `{posted, reason}`을 돌려준다. 보내지 못하면 `reason`에 한국어 사유. 사용자당 1분에 5회, 넘으면 429. 로그인 이름이 필요하다 (JOB_TOKEN만으로는 400) |
| `POST /jobs/notify-due-calls`, `POST /jobs/remind` | 예약 실행용 (JOB_TOKEN). 화면에서 부를 필요는 없다 |

- 메일의 수신 거부 링크(`/unsubscribe/...`)와 Slack 버튼(`/webhooks/slack/interactions`)은 백엔드가 직접 처리하므로 프론트엔드가 다룰 것이 없다.
- 본문 끝에 AI 고지와 수신 거부 안내가 붙는다. 수정 화면에서 지워도 승인할 때 다시 붙는다고 안내해 주세요. 제목의 `(광고)`는 서버 설정 `AD_PREFIX=true`일 때만 붙는다 (기본 끔).
- 승인했지만 하루 발송 한도(`MAX_SENDS_PER_DAY`)로 발송되지 못하면 결정 API가 400을 돌려주고 메일은 승인 상태로 남는다. 같은 메일을 다시 보내는 화면은 아직 없다.
