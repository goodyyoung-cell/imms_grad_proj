# 배포 가이드 (Supabase + Cloud Run)

## 1. Supabase

1. 졸업프로젝트용 조직에 프로젝트를 만든다 (리전: Seoul `ap-northeast-2`).
2. `supabase/migrations/` 의 SQL을 순서대로 적용한다.
   - 대시보드 SQL Editor에 붙여 넣거나, Supabase CLI로 `supabase db push`
3. Project Settings → API에서 `SUPABASE_URL`과 `service_role` 키를 `.env`에 넣는다.
   - service role 키는 RLS를 우회한다. 서버에서만 쓰고 절대 커밋하지 않는다.
4. 팀원은 조직 멤버로 초대한다.

모든 테이블은 RLS가 켜져 있고 정책이 없다. 공개 키(anon)로는 아무것도 읽을 수 없다.

## 2. 로컬 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # 값 채우기
pytest                    # API 키 없이 도는 테스트

python -m sdr_agent.cli research --limit 3              # 상태가 new인 리드 3건 조사
uvicorn sdr_agent.app:app --reload                      # 웹 서버 (http://localhost:8000/healthz)
```

## 3. Cloud Run

처음 한 번 (GCP 프로젝트, 결제 계정 연결 후):

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com

# 비밀값을 Secret Manager에 넣기 (값마다 반복)
printf '%s' "<값>" | gcloud secrets create anthropic-api-key --data-file=-
# gemini-api-key, supabase-url, supabase-service-role-key, dart-api-key, job-token 도 같은 방식

# Cloud Run 서비스 계정이 비밀값을 읽을 수 있게 (기본 컴퓨트 서비스 계정 기준)
PROJECT_NUMBER=$(gcloud projects describe <PROJECT_ID> --format='value(projectNumber)')
for s in anthropic-api-key gemini-api-key supabase-url supabase-service-role-key dart-api-key job-token; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

배포 (코드가 바뀔 때마다):

```bash
gcloud run deploy sdr-agent --source . --region asia-northeast3 \
  --allow-unauthenticated --timeout 3600 --memory 1Gi \
  --set-env-vars DEFAULT_LLM=claude,ANTHROPIC_MODEL=claude-sonnet-5-5,GEMINI_MODEL=<모델명> \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,SUPABASE_URL=supabase-url:latest,SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest,DART_API_KEY=dart-api-key:latest,JOB_TOKEN=job-token:latest
```

- `--allow-unauthenticated`: 승인 버튼 웹훅이 외부에서 들어와야 해서 공개로 둔다. 대신 `/jobs/*`는 `JOB_TOKEN`으로 막는다.
- `--timeout 3600`: 에이전트가 리드 여러 건을 조사하면 수 분이 걸린다.

확인:

```bash
URL=$(gcloud run services describe sdr-agent --region asia-northeast3 --format='value(status.url)')
curl $URL/healthz
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

## 아직 검증하지 않은 것

- 이 문서의 gcloud 명령과 Dockerfile은 실제 배포로 확인하기 전이다. 첫 배포(11/22) 때 확인하고 고친다.
