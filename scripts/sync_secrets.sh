#!/bin/bash
# .env의 API 키를 Secret Manager에 올리고 Cloud Run 서비스(sdr-agent)에 연결한다.
# 키 값은 화면에 출력하지 않는다. 비어 있는 키는 건너뛴다. 여러 번 실행해도 된다 (새 버전으로 추가).
#
#   bash scripts/sync_secrets.sh
#
# 필요: gcloud 로그인, 프로젝트·리전 설정 (docs/deploy.md 3절)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

SERVICE=sdr-agent
PROJECT=$(gcloud config get-value project 2>/dev/null)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
SECRETS=""

push() {
  local var="$1" name="$2" val="${!1:-}"
  if [ -z "$val" ]; then echo "건너뜀: $var (비어 있음)"; return; fi
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$val" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  else
    printf '%s' "$val" | gcloud secrets create "$name" --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$name" --member="$SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  echo "등록: $var → Secret Manager '$name'"
  SECRETS="${SECRETS:+$SECRETS,}$var=$name:latest"
}

push ANTHROPIC_API_KEY anthropic-api-key
push GEMINI_API_KEY gemini-api-key
push DART_API_KEY dart-api-key
push NAVER_CLIENT_ID naver-client-id
push NAVER_CLIENT_SECRET naver-client-secret
push ADMIN_PASSWORD admin-password
push ADMIN_TOKEN_SECRET admin-token-secret

# 비밀이 아닌 설정은 환경변수로. 구분자를 @로 써서 값에 쉼표가 있어도 안전하게
ENVS="DEFAULT_LLM=${DEFAULT_LLM:-claude}@AGENT_MAX_STEPS=${AGENT_MAX_STEPS:-12}"
for v in ANTHROPIC_MODEL GEMINI_MODEL BOOKING_URL HANDOFF_TO CALL_DELAY_MINUTES CALL_HOURS_KST GOOGLE_NEWS_ENABLED NAVER_SEARCH_API ALLOWED_ORIGINS; do
  if [ -n "${!v:-}" ]; then ENVS="$ENVS@$v=${!v}"; fi
done

ARGS=(--update-env-vars "^@^$ENVS")
if [ -n "$SECRETS" ]; then ARGS+=(--update-secrets "$SECRETS"); fi
gcloud run services update "$SERVICE" "${ARGS[@]}"
echo "완료: Cloud Run '$SERVICE'에 반영했습니다 (새 리비전)."
