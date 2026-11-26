#!/bin/bash
# Cloud Scheduler 예약 실행 등록 (평일 아침 리서치 → 초안). 이미 있으면 갱신한다.
# JOB_TOKEN은 .env에서 읽고 화면에 출력하지 않는다.
#
#   bash scripts/setup_scheduler.sh            # 기본: 하루 리서치 5건
#   RESEARCH_LIMIT=10 bash scripts/setup_scheduler.sh
#
# 초안은 만들기만 하고 발송하지 않는다 (사람 승인 대기). 콜 과제는 발송 시점에 생기므로 예약이 필요 없다.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

REGION=asia-northeast3
URL=$(gcloud run services describe sdr-agent --region "$REGION" --format='value(status.url)')
RESEARCH_LIMIT=${RESEARCH_LIMIT:-5}
DRAFT_LIMIT=${DRAFT_LIMIT:-10}

upsert() {
  local name="$1" schedule="$2" path="$3" body="$4"
  local verb=create
  if gcloud scheduler jobs describe "$name" --location "$REGION" >/dev/null 2>&1; then verb=update; fi
  gcloud scheduler jobs "$verb" http "$name" --location "$REGION" \
    --schedule "$schedule" --time-zone "Asia/Seoul" \
    --uri "$URL$path" --http-method POST \
    --$( [ "$verb" = create ] && echo headers || echo update-headers ) "Authorization=Bearer $JOB_TOKEN,Content-Type=application/json" \
    --message-body "$body" --attempt-deadline 30m >/dev/null
  echo "$verb: $name ($schedule, $path)"
}

upsert sdr-morning-research "30 7 * * 1-5" /jobs/research "{\"limit\": $RESEARCH_LIMIT, \"trigger\": \"schedule\"}"
upsert sdr-morning-draft    "0 8 * * 1-5"  /jobs/draft    "{\"limit\": $DRAFT_LIMIT, \"trigger\": \"schedule\"}"
gcloud scheduler jobs list --location "$REGION" --format='table(name.basename(), schedule, timeZone, state)'
