# 배포 가이드 (Supabase + Cloud Run)

## 1. Supabase

**현재 상태 (2026-11-21)**: 조직 `IMMS Graduation Project`에 프로젝트 `sdr-agent`를 만들었고(서울 `ap-northeast-2`), `supabase/migrations/`의 두 파일을 적용했다. 수신 거부·미승인 발송 트리거도 실제 DB에서 확인했다.

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

python -m sdr_agent.cli seed                            # 가상 리드 12건을 DB에 넣기 (한 번만)
python -m sdr_agent.cli research --limit 3              # 상태가 new인 리드 3건 조사
python -m sdr_agent.cli draft --limit 3                 # 조사 끝난 리드에 첫 메일 초안 (승인 대기)
python -m sdr_agent.cli pending                         # 승인 대기 메시지 보기
python -m sdr_agent.cli approve <message_id> --by 이름   # 승인 → 샌드박스 발송
python -m sdr_agent.cli reply --from <리드 이메일> "다음 주 미팅 가능합니다"   # 모의 답장 → 분류·인계
python -m sdr_agent.cli eval-replies                    # 모의 답장 24건 분류 정확도
uvicorn sdr_agent.app:app --reload                      # 웹 서버 (http://localhost:8000/health)
```

웹 API (모두 `Authorization: Bearer <JOB_TOKEN>` 필요)

| 경로 | 하는 일 |
|------|---------|
| `POST /jobs/research` | new 리드 조사 |
| `POST /jobs/draft` | researched 리드에 첫 메일 초안 |
| `GET /admin/messages` | 승인 대기 메시지 목록 |
| `POST /admin/messages/{id}/decision` | `{"decision": "approved"\|"rejected"\|"edited", "decided_by": "..."}` 승인하면 바로 샌드박스 발송 |
| `POST /webhooks/reply` | 답장 도착 `{"from_email": "...", "body": "..."}` |
| `POST /jobs/handoff` | `{"lead_id": "..."}` 인계 요약 |

## 3. Cloud Run

**현재 상태 (2026-11-22)**: GCP 프로젝트 `gen-lang-client-0136144273`, 리전 `asia-northeast3`(서울), 결제 계정 연결 완료. 팀원은 IAM에서 이 프로젝트에 초대받아 쓴다.

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

LLM·DART 키가 생기면 같은 방식으로 비밀값을 만들고 권한을 준 뒤 서비스에 추가한다:

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

확인:

```bash
URL=$(gcloud run services describe sdr-agent --region asia-northeast3 --format='value(status.url)')
curl $URL/health
curl -X POST $URL/jobs/research -H "Authorization: Bearer <JOB_TOKEN>" \
  -H "Content-Type: application/json" -d '{"limit": 1, "trigger": "manual"}'
```

## 4. Cloud Scheduler (평일 아침 리서치)

```bash
gcloud scheduler jobs create http sdr-morning-research --location asia-northeast3 \
  --schedule "30 7 * * 1-5" --time-zone "Asia/Seoul" \
  --uri "$URL/jobs/research" --http-method POST \
  --headers "Authorization=Bearer <JOB_TOKEN>,Content-Type=application/json" \
  --message-body '{"limit": 10, "trigger": "schedule"}' \
  --attempt-deadline 30m
```

조사 30분 뒤 초안을 만드는 작업도 같은 방식으로 둔다 (`--uri "$URL/jobs/draft"`, `--schedule "0 8 * * 1-5"`).

## 아직 검증하지 않은 것

- 이 문서의 gcloud 명령과 Dockerfile은 실제 배포로 확인하기 전이다. 첫 배포(11/22) 때 확인하고 고친다.
