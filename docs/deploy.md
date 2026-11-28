# 배포 가이드 (Supabase + Cloud Run)

## 1. Supabase

**현재 상태 (2026-11-26)**: 조직 `IMMS Graduation Project`의 프로젝트 `sdr-agent`(서울 `ap-northeast-2`)에 `supabase/migrations/`의 마이그레이션 5개를 모두 적용했다 (리드 유입·동의, 콜 과제, 통화 기록 포함). 평가용 샘플 리드 106건 입력. 안전장치 트리거는 `scripts/smoke_supabase.py`로 실제 DB에서 확인한다.

- `SUPABASE_URL`: `https://huldtztohuzrkfgguyxv.supabase.co`

새로 세팅하거나 마이그레이션을 추가할 때:

1. `supabase/migrations/` 의 SQL을 파일 이름 순서대로 적용한다.
   - 대시보드 SQL Editor에 붙여 넣거나, Supabase CLI로 `supabase db push`
2. Project Settings → API Keys의 **Secret keys** 칸에서 `sb_secret_...` 키를 복사해 `.env`의 `SUPABASE_SECRET_KEY`에 넣는다.
   - 새 프로젝트에는 예전 `service_role` 키가 없다. 비밀 키가 그 역할을 대신한다.
   - 비밀 키는 RLS를 우회한다. 서버에서만 쓰고 절대 커밋하지 않는다.
3. 팀원은 조직 멤버로 초대한다 (Organization Settings → Team → Invite).

모든 테이블은 RLS가 켜져 있고 정책이 없다. 공개 키(anon)로는 아무것도 읽을 수 없다.

## 2. 로컬 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # 값 채우기
pytest                    # API 키 없이 도는 테스트
python scripts/smoke_supabase.py   # 실제 Supabase로 루프 전체 점검 (가짜 LLM, 임시 데이터는 끝나고 삭제)

python -m sdr_agent.cli seed                            # 가상 리드 12건을 DB에 넣기 (한 번만)
python -m sdr_agent.cli import-leads --file data/sample/leads_template.csv            # 시트 업로드 미리보기
python -m sdr_agent.cli import-leads --file 리드.xlsx --commit                        # 실제 저장
python -m sdr_agent.cli import-leads --google-sheet "https://docs.google.com/..."    # 구글 시트
python -m sdr_agent.cli research --limit 3              # 상태가 new인 리드 3건 조사
python -m sdr_agent.cli draft --limit 3                 # 조사 끝난 리드에 첫 메일 초안 (승인 대기)
python -m sdr_agent.cli pending                         # 승인 대기 메시지 보기
python -m sdr_agent.cli approve <message_id> --by 이름   # 승인 → 샌드박스 발송 → 1시간 뒤 전화 과제
python -m sdr_agent.cli calls                           # 지금 걸어야 할 전화와 콜 스크립트
python -m sdr_agent.cli call-outcome <task_id> interested --by 이름 --received yes --meeting "다음 주 화요일" --reason "성수기 전 지연 해소"
python -m sdr_agent.cli reply --from <리드 이메일> "다음 주 미팅 가능합니다"   # 모의 답장 → 분류·인계
python -m sdr_agent.cli eval-replies                    # 모의 답장 24건 분류 정확도
uvicorn sdr_agent.app:app --reload                      # 웹 서버 (http://localhost:8000/health)
```

웹 API (모두 `Authorization: Bearer <JOB_TOKEN>` 필요)

| 경로 | 하는 일 |
|------|---------|
| `POST /auth/login` | 관리 화면 로그인 `{"name", "password"}` → 8시간 토큰 (`/admin/*`에 사용). 화면용 API 전체는 [`frontend-handoff.md`](frontend-handoff.md) |
| `GET /form` | 웹사이트 데모 신청 폼 (공개, 토큰 불필요) |
| `POST /webhooks/lead` | 외부 폼 서비스가 보내는 리드 (JSON) |
| `POST /admin/leads/import` | 시트 업로드. `file`(CSV·xlsx) 또는 `google_sheet`, 기본은 미리보기 (`dry_run=false`로 저장) |
| `POST /jobs/research` | new 리드 조사 |
| `POST /jobs/draft` | researched 리드에 첫 메일 초안 |
| `GET /admin/messages` | 승인 대기 메시지 목록 |
| `POST /admin/messages/{id}/decision` | `{"decision": "approved"\|"rejected"\|"edited", "decided_by": "..."}` 승인하면 바로 샌드박스 발송 |
| `POST /admin/leads/{id}/unsubscribe` | 담당자가 수신 거부 확정 `{"decided_by": "...", "note": "..."}` |
| `GET /admin/calls` | 지금까지 걸어야 할 전화와 콜 스크립트 (`due_only=false`면 대기 중 전체) |
| `POST /admin/calls/{id}/outcome` | 통화 결과 `{"outcome", "called_by", "email_received", "meeting_preference", "compelling_reason", "notes"}` |
| `POST /webhooks/reply` | 답장 도착 `{"from_email": "...", "body": "..."}` |
| `POST /jobs/handoff` | `{"lead_id": "..."}` 인계 요약 |

## 3. Cloud Run

**현재 상태 (2026-11-22)**: GCP 프로젝트 `gen-lang-client-0136144273`, 리전 `asia-northeast3`(서울), 결제 계정 연결 완료. 팀원은 IAM에서 이 프로젝트에 초대받아 쓴다.

**현재 상태 (2026-11-26)**: 최신 코드 배포 완료 (리비전 `sdr-agent-00004`). Anthropic·Gemini·DART·네이버(API HUB) 키를 Secret Manager로 연결했다 (`scripts/sync_secrets.sh`). Cloud Run에서 실제 리서치 1건 성공. 예약 실행 2개 등록 (`scripts/setup_scheduler.sh`): 평일 07:30 리서치 5건, 08:00 초안 최대 10건. Gemini는 크레딧 충전 후 `GEMINI_MODEL=gemini-3.8-flash`로 설정 (리비전 `sdr-agent-00005`). `gemini-2.5-flash`는 신규 사용자에게 제공 종료(404).

### 첫 배포 (LLM 키 없이 DB 연결까지만 확인)

LLM·DART 키가 아직 없어도 배포할 수 있다. 처리할 리드가 없으면 `/jobs/research`가 LLM 없이 `{"processed": 0}`을 돌려주므로, Cloud Run → Supabase 연결과 토큰 인증을 확인할 수 있다. 저장소 폴더에서 실행한다.

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud config set run/region asia-northeast3
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com

# JOB_TOKEN을 만들어 .env에 기록 (키를 화면에 치지 않도록 .env에서 읽어 쓴다)
sed -i '' "s/^JOB_TOKEN=.*/JOB_TOKEN=$(openssl rand -hex 32)/" .env
set -a; source .env; set +a

printf '%s' "$SUPABASE_URL"        | gcloud secrets create supabase-url --data-file=-
printf '%s' "$SUPABASE_SECRET_KEY" | gcloud secrets create supabase-secret-key --data-file=-
printf '%s' "$JOB_TOKEN"           | gcloud secrets create job-token --data-file=-

PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" --format='value(projectNumber)')
for s in supabase-url supabase-secret-key job-token; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done

gcloud run deploy sdr-agent --source . --allow-unauthenticated --timeout 3600 --memory 1Gi \
  --set-secrets SUPABASE_URL=supabase-url:latest,SUPABASE_SECRET_KEY=supabase-secret-key:latest,JOB_TOKEN=job-token:latest

URL=$(gcloud run services describe sdr-agent --format='value(status.url)')
curl "$URL/health"                                   # {"ok":true}  (/healthz처럼 z로 끝나는 경로는 Cloud Run이 예약해 쓸 수 없다)
curl -X POST "$URL/jobs/research" -H "Authorization: Bearer $JOB_TOKEN" \
  -H "Content-Type: application/json" -d '{"trigger":"manual"}'   # {"processed":0,"results":[]}
```

`/jobs/research`가 `인증 실패`를 돌려주면 터미널을 새로 열어 `$JOB_TOKEN`이 비어 있는 경우가 많다. 저장소 폴더에서 `set -a; source .env; set +a`를 다시 실행한다.

LLM·DART·네이버 키가 생기면 `.env`에 넣고 `bash scripts/sync_secrets.sh`를 실행한다. 비밀값 등록, 권한 부여, Cloud Run 연결을 한 번에 한다 (키는 화면에 나오지 않음). 수동으로 할 때는:

```bash
gcloud run services update sdr-agent \
  --update-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest,DART_API_KEY=dart-api-key:latest \
  --update-env-vars DEFAULT_LLM=claude,ANTHROPIC_MODEL=claude-sonnet-5-5
```

### 전체 구성 (모든 키가 준비된 뒤)

처음 한 번 (GCP 프로젝트, 결제 계정 연결 후):

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com

# 비밀값을 Secret Manager에 넣기 (값마다 반복)
printf '%s' "<값>" | gcloud secrets create anthropic-api-key --data-file=-
# gemini-api-key, supabase-url, supabase-secret-key, dart-api-key, naver-client-id, naver-client-secret, job-token 도 같은 방식

# Cloud Run 서비스 계정이 비밀값을 읽을 수 있게 (기본 컴퓨트 서비스 계정 기준)
PROJECT_NUMBER=$(gcloud projects describe <PROJECT_ID> --format='value(projectNumber)')
for s in anthropic-api-key gemini-api-key supabase-url supabase-secret-key dart-api-key naver-client-id naver-client-secret job-token; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

배포 (코드가 바뀔 때마다):

```bash
gcloud run deploy sdr-agent --source . --region asia-northeast3 \
  --allow-unauthenticated --timeout 3600 --memory 1Gi \
  --set-env-vars DEFAULT_LLM=claude,ANTHROPIC_MODEL=claude-sonnet-5-5,GEMINI_MODEL=<모델명>,AGENT_MAX_STEPS=12 \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,SUPABASE_URL=supabase-url:latest,SUPABASE_SECRET_KEY=supabase-secret-key:latest,DART_API_KEY=dart-api-key:latest,NAVER_CLIENT_ID=naver-client-id:latest,NAVER_CLIENT_SECRET=naver-client-secret:latest,JOB_TOKEN=job-token:latest
```

- `--allow-unauthenticated`: 승인 버튼 웹훅이 외부에서 들어와야 해서 공개로 둔다. 대신 `/jobs/*`는 `JOB_TOKEN`으로 막는다.
- `--timeout 3600`: 에이전트가 리드 여러 건을 조사하면 수 분이 걸린다.
- `--no-cpu-throttling`: Slack 대화(`/webhooks/slack/events`)는 이벤트에 먼저 응답하고 뒤에서 처리한다. 요청이 끝난 뒤에도 CPU가 줄지 않아야 답이 나간다 (`--min-instances 1`과 함께 쓴다).
- LLM을 OpenAI로 바꾸려면 `OPENAI_API_KEY`(Secret Manager `openai-api-key`로 연결), `OPENAI_MODEL`을 넣고 `DEFAULT_LLM=openai`로 바꾼다. ChatGPT 구독이 아니라 platform.openai.com의 API 결제가 필요하다. 호환 서비스(Azure OpenAI 등)는 `OPENAI_BASE_URL`에 그 `/v1` 주소를 넣는다. 추론 모델이 느리면 `OPENAI_REASONING_EFFORT=low`.
- 2026-11-28부터 LLM을 쓰는 예약 작업 `sdr-morning-research`, `sdr-morning-draft`는 **일시중지** 상태다 (이 프로젝트는 가상 데이터라 매일 크레딧을 쓸 이유가 없다). 다시 켜려면 `gcloud scheduler jobs resume <이름> --location asia-northeast3`. `scripts/setup_scheduler.sh`를 다시 돌린 뒤에는 `gcloud scheduler jobs list`로 상태를 확인하고, 켜져 있으면 다시 pause한다.
- 환경변수를 바꿀 때는 `--update-env-vars`나 `--flags-file`을 쓴다. `--env-vars-file`은 기존 환경변수를 모두 지우고 파일의 값으로 바꾼다.
- 새 기능(`20261128000000_chat_memory.sql`)은 마이그레이션을 먼저 적용한 뒤 배포한다. 표가 없으면 제목 후보 저장과 메모리 API가 실패한다.

확인:

```bash
URL=$(gcloud run services describe sdr-agent --region asia-northeast3 --format='value(status.url)')
curl $URL/health
curl -X POST $URL/jobs/research -H "Authorization: Bearer <JOB_TOKEN>" \
  -H "Content-Type: application/json" -d '{"limit": 1, "trigger": "manual"}'
```

## 4. Cloud Scheduler (평일 아침 리서치)

`bash scripts/setup_scheduler.sh`로 등록·갱신한다 (하루 처리량: `RESEARCH_LIMIT=10 bash scripts/setup_scheduler.sh`). 아래는 수동으로 할 때의 명령이다.

```bash
gcloud scheduler jobs create http sdr-morning-research --location asia-northeast3 \
  --schedule "30 7 * * 1-5" --time-zone "Asia/Seoul" \
  --uri "$URL/jobs/research" --http-method POST \
  --headers "Authorization=Bearer <JOB_TOKEN>,Content-Type=application/json" \
  --message-body '{"limit": 10, "trigger": "schedule"}' \
  --attempt-deadline 30m
```

조사 30분 뒤 초안을 만드는 작업도 같은 방식으로 둔다 (`--uri "$URL/jobs/draft"`, `--schedule "0 8 * * 1-5"`).

## 5. 구글 시트 리드 업로드 권한

시트를 '링크가 있는 모든 사용자'로 공개하지 않는다. 대신 읽는 계정에만 보기 권한을 준다.

- Cloud Run: 서비스 계정 `<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`에 시트를 공유(보기 권한). GCP 프로젝트에서 Sheets API를 켠다: `gcloud services enable sheets.googleapis.com`
- 로컬: `gcloud auth application-default login --scopes=https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/cloud-platform` 로 로그인한 계정이 시트를 볼 수 있으면 된다.

## 아직 검증하지 않은 것

- 구글 시트 읽기는 가짜 응답으로만 테스트했다. 실제 시트로 한 번 확인한다.

- 이 문서의 gcloud 명령과 Dockerfile은 실제 배포로 확인하기 전이다. 첫 배포(11/22) 때 확인하고 고친다.
