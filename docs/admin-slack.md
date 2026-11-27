# 관리 웹과 Slack 승인 채널

고객에게 나가는 메일은 사람이 승인해야 발송된다. 승인하는 길은 세 가지이고 모두 같은 `jobs.decide_message()`를 거친다.
그래서 어느 길로 승인해도 DB 트리거(미승인·수신 거부 발송 차단)와 승인 기록(`approvals`)이 똑같이 적용된다.

| 길 | 쓰는 사람 | 경로 |
|----|-----------|------|
| 관리 웹 (`web/`, React) | 담당자 | 로그인 후 `/admin/*` JSON API 호출 (`POST /admin/messages/{id}/decision`) |
| Slack 버튼 | 담당자 | 채널의 승인 요청 메시지 → `POST /webhooks/slack/interactions` |
| 관리 API·CLI | 개발자 | JOB_TOKEN, `python -m sdr_agent.cli approve` |

백엔드는 JSON API만 제공한다. 화면은 별도 React 프론트엔드(`web/`)이고 `docs/frontend-handoff.md`를 따른다. 이 문서는 Slack 연동, 알림, 예약 실행, 평가 지표 API를 다룬다.

## 1. 구조

```
Cloud Scheduler --(JOB_TOKEN)--> /jobs/research, /jobs/draft, /jobs/notify-due-calls, /jobs/remind
jobs.py --notify()--> Slack 채널 (승인 요청, 사람 확인 필요, 수신 거부, 인계, 후속 시점, 전화 시각, 승인 대기)
담당자 --버튼--> Slack --(서명)--> POST /webhooks/slack/interactions --> jobs.decide_message()
담당자 --링크--> 관리 웹 ({ADMIN_WEB_URL}/m/{메일}, /l/{리드}, /c/{콜 과제}, /)
```

- `notify()`는 항상 서버 로그에 남기고, `SLACK_BOT_TOKEN`과 `SLACK_CHANNEL`이 모두 있고 종류가 `SLACK_NOTIFY_KINDS`에 있을 때만 Slack에 올린다. Slack 오류는 로그만 남기고 영업 흐름을 멈추지 않는다.
- 알림의 "관리 화면에서 열기" 버튼은 `ADMIN_WEB_URL`이 있을 때만 붙는다. 주소 규칙은 프론트엔드가 구현한 것과 같다: `/m/{message_id}`(메일 검토), `/l/{lead_id}`(리드), `/c/{call_task_id}`(전화 과제), `/`(홈).

## 2. Slack 승인 요청

초안이 승인 대기가 되면(첫 메일, 고객 질문에 대한 답변 초안 모두) 채널에 메시지가 올라온다.

```
메일 승인 요청: 한빛몰
담당자 김가상 CS팀장 | 점수 62점 | 근거 데모 요청, 팀장·부장급
제목: ...
(본문)
[승인] [수정 후 승인] [반려] [관리 화면에서 열기]
```

- **승인**: 승인 기록(`channel=slack`, `decided_by=slack:<사용자 이름>`)을 남기고 샌드박스로 발송한다. 메시지는 결과로 바뀌고 버튼이 사라져 두 번 누를 수 없다.
- **수정 후 승인**: 수정 창에 본문을 고쳐 제출하면 수정본으로 승인한다. 수정 전 초안(`original_body`)이 기록된다. 본문이 3000자를 넘으면 관리 웹에서 고치라고 안내한다.
- **반려**: 사유(사실과 다름, 어조, 연락하면 안 되는 대상, 금지 표현, 기타)를 고르는 창을 거쳐 반려한다. 사유는 `approvals.reason_code`에 남는다. 리드는 `needs_human`이 된다.
- 이미 처리된 메시지를 다시 누르면 본인에게만 "처리하지 못했습니다"가 보인다.
- 버튼 메시지는 결과로 바뀐다. `response_url`이 만료됐으면 `chat.update`로 바꾼다 (채널 ID와 메시지 ts를 수정·반려 창에 담아 둔다). 한도(429)에 걸리면 `Retry-After`만큼 기다렸다가 한 번 더 보낸다.
- 수신 동의 기록이 없는 리드는 승인 요청에 경고가 붙는다.
- 수정해서 승인해도 AI 사용 고지, 수신 거부 안내, 전송자 정보는 다시 붙는다.

## 3. 그 밖의 알림

| 종류(`kind`) | 언제 | 비고 |
|--------------|------|------|
| `approval` | 초안·답변 초안이 승인 대기가 될 때 | 버튼 포함 |
| `needs_human` | 분류가 애매하거나 수신 거부가 의심될 때, 전화 후 확인이 필요할 때 | 리드 링크 |
| `unsubscribe` | 수신 거부(답장, 전화, 담당자 확정, 메일의 수신 거부 링크) | |
| `handoff` | 인간 SDR에게 인계 | |
| `followup` | 나중에 연락 달라는 답장 | |
| `call_due` | 전화 시각이 지난 대기 콜 과제 | 과제당 한 번, `/c/{id}` 링크 |
| `reminder` | 승인 대기가 `REMINDER_HOURS`(기본 4)를 넘긴 메일이 있을 때 | `/` 링크 |
| `intake` | 리드 유입 | 기본으로 끔 |

- 전화 시각 알림(`jobs.notify_due_calls`): 대기 중이고 `due_at`이 지난 콜 과제마다 한 번 알린다. 알렸다는 표시는 콜 과제의 `script.due_notified_at`에 남기므로 DB 스키마는 바꾸지 않았다. 실제로 Slack에 올라갔을 때만 표시하므로 Slack이 꺼져 있거나 실패하면 다음 실행에서 다시 시도한다. 두 번 동시에 실행되면 같은 알림이 두 번 갈 수 있다 (예약은 10분 간격이라 드물다).
- 승인 대기 알림(`jobs.remind_pending`): 오래 기다린 메일이 있을 때만 한 번 알린다.

## 4. Slack 설치 (약 10분)

1. https://api.slack.com/apps → **Create New App → From a manifest** → 워크스페이스 선택 → [`slack-app-manifest.yaml`](slack-app-manifest.yaml) 붙여 넣기. 이 파일의 `request_url`에 있는 `<CLOUD_RUN_URL>`을 서비스 주소로 바꾼다 (`https://<CLOUD_RUN_URL>/webhooks/slack/interactions`).
2. **Install to Workspace**. **OAuth & Permissions**의 Bot User OAuth Token(`xoxb-...`)과 **Basic Information**의 Signing Secret을 복사한다.
3. 승인 요청을 받을 채널(예: `#sdr-승인`)을 만들고 `/invite @SDR Agent`로 앱을 초대한다. 채널 이름을 우클릭, 링크 복사의 마지막 `C...`가 채널 ID.
4. Secret Manager에 넣고 Cloud Run에 연결한다 (`docs/deploy.md`의 방식). 값은 저장소나 채팅에 적지 않는다.
   ```bash
   printf '%s' "<bot token>" | gcloud secrets create slack-bot-token --data-file=-
   printf '%s' "<signing secret>" | gcloud secrets create slack-signing-secret --data-file=-
   gcloud run services update sdr-agent --region asia-northeast3 \
     --update-secrets SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest \
     --update-env-vars SLACK_CHANNEL=<채널 ID>,SLACK_APPROVERS=<사용자 ID>,ADMIN_WEB_URL=https://<관리 웹 주소>,PUBLIC_BASE_URL=https://<CLOUD_RUN_URL>
   ```
   `SLACK_APPROVERS`의 쉼표는 `--update-env-vars`에서 구분자와 겹치므로 `gcloud topic escaping`의 방식으로 구분자를 바꾸거나 콘솔에서 넣는다.
5. 연결 시험: 관리 웹에 로그인한 뒤 `POST /admin/notify-test`를 부르면 채널에 "연결 시험" 메시지가 올라온다 (`SLACK_NOTIFY_KINDS`에 없어도 보낸다). 응답의 `posted`가 false이면 `reason`에 이유가 적힌다. `GET /admin/status`로 토큰·서명·채널·승인자 설정 여부를 값 노출 없이 확인할 수 있다. 사용자당 1분에 5회까지다.
6. 시연 때는 첫 요청이 느리지 않도록 `--min-instances 1`을 권한다. Slack은 3초 안에 응답을 받아야 하는데, 콜드 스타트가 길면 버튼이 "실패"로 보일 수 있다 (처리는 이어서 끝나고 결과는 메시지에 반영된다).
7. 관리 웹이 다른 주소에서 뜨면 `ALLOWED_ORIGINS`에 그 주소를 넣는다 (CORS). Slack 경로는 브라우저가 부르지 않으므로 CORS와 무관하다.

## 4.1 로컬에서 먼저 시험하기 (DB·LLM 키 없이)

Cloud Run에 배포하기 전에 Slack 쪽만 따로 확인하는 방법이다. 가짜 DB에 가상 메일 3건을 넣고 실제 승인 코드가 도는 서버를 내 컴퓨터에 띄운다.

1. Slack 앱과 채널을 4절 1~3번대로 만든다. **Slack은 설치하지 않아도 되고 웹 브라우저(app.slack.com)로 충분하다.** 무료 워크스페이스로 시험할 수 있다.
2. `.env`(또는 환경변수)에 `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL`을 넣는다. 내 Slack 사용자 ID를 `SLACK_APPROVERS`에 넣으면 승인자 제한까지 시험할 수 있다.
3. 서버를 띄운다: `pip install -e ".[dev]"` 후 `python scripts/slack_sandbox.py`. 시작하면 승인 요청 3건이 채널에 올라간다. 4번을 하기 전에는 버튼을 눌러도 Slack이 서버에 닿지 못해 오류로 보인다.
4. 버튼이 서버에 닿으려면 공개 주소가 필요하다. 계정이 필요 없는 방법: `winget install Cloudflare.cloudflared` 후 `cloudflared tunnel --url http://localhost:8000`. 출력되는 `https://....trycloudflare.com`을 Slack 앱의 **Interactivity & Shortcuts > Request URL**에 `https://<그 주소>/webhooks/slack/interactions`로 저장한다. 터널을 다시 켜면 주소가 바뀌므로 다시 저장한다. (ngrok도 되지만 계정이 필요하다.)
5. 채널에서 **승인**, **수정 후 승인**, **반려**를 눌러 본다. `http://localhost:8000/sandbox/state`에서 메일 상태, 승인 기록(`channel=slack`), 생긴 전화 과제를 확인한다. 서버를 끄면 데이터가 사라진다.

이 방법으로 확인되는 것: 서명 검증, 버튼과 수정·반려 창, 메시지가 결과로 바뀌는 동작, 승인자 제한, Slack의 3초 응답 제한을 내 컴퓨터 속도에서 넘는지. 확인되지 않는 것: 실제 Supabase, Cloud Run의 콜드 스타트, 실제 LLM 초안, 전화 시각 알림(스케줄러가 부름), 관리 웹 링크(`ADMIN_WEB_URL`을 넣으면 링크 버튼 주소까지는 볼 수 있다).

## 5. 환경변수

| 이름 | 설명 |
|------|------|
| `SLACK_BOT_TOKEN` | 봇 토큰 (`xoxb-`). `SLACK_CHANNEL`과 함께 있어야 채널에 올린다 |
| `SLACK_SIGNING_SECRET` | 버튼 클릭의 서명을 검증한다. 없으면 `/webhooks/slack/interactions`가 503 |
| `SLACK_CHANNEL` | 알림을 올릴 채널 ID |
| `SLACK_APPROVERS` | 승인·반려할 수 있는 Slack 사용자 ID (쉼표). 비우면 채널 구성원 모두 (운영에서는 채운다) |
| `SLACK_NOTIFY_KINDS` | 알릴 종류. 기본 `approval,needs_human,unsubscribe,handoff,followup,call_due,reminder` |
| `ADMIN_WEB_URL` | 관리 웹(React) 주소. 딥링크 기준, 끝에 `/` 없음 |
| `PUBLIC_BASE_URL` | 이 백엔드의 공개 주소. 메일 하단의 수신 거부 링크에만 쓴다 |
| `REMINDER_HOURS` | 승인 대기 알림 기준 시간 (기본 4) |
| `SENDER_NAME`, `SENDER_CONTACT` | 메일 하단에 밝히는 전송자 명칭과 연락처 (정보통신망법 제50조 제4항) |
| `MAX_SENDS_PER_DAY` | 하루 발송 상한 (기본 50, 한국 시간 자정 기준). 넘으면 승인한 메일이 승인 상태로 남아 다시 보낼 수 있다 |
| `REQUIRE_CONSENT_TO_SEND` | `true`면 수신 동의 기록이 없는 리드에게는 보내지 않는다. **실제 발송을 켤 때 함께 켠다** |
| `AD_PREFIX` | `true`면 제목 앞에 `(광고)`를 붙인다. 기본 끔 (팀 결정, `decisions.md` 2026-11-28) |

## 6. 예약 실행 (Cloud Scheduler)

`bash scripts/setup_scheduler.sh`가 아래 네 개를 등록한다 (모두 `Authorization: Bearer <JOB_TOKEN>`, 시간대 Asia/Seoul).

| 이름 | 일정 | 호출 |
|------|------|------|
| `sdr-morning-research` | 평일 07:30 | `/jobs/research` |
| `sdr-morning-draft` | 평일 08:00 | `/jobs/draft` |
| `sdr-call-due-notice` | 평일 9~17시 10분마다 | `/jobs/notify-due-calls` |
| `sdr-approval-remind` | 평일 9~18시 매시 정각 | `/jobs/remind` |

## 7. 수신 거부 안내와 링크

시장·법 조사(`research-ai-sdr.md`)에서 나온 요구를 코드가 대신 지키게 했다.
- 모든 고객용 메일(첫 메일, 답변 초안, 수정 승인본) 본문 끝에 AI 사용 고지, 한글·영문 수신 거부 안내, 전송자 명칭·연락처를 붙인다.
- `PUBLIC_BASE_URL`이 있으면 리드별 **수신 거부 링크**(`/unsubscribe/<리드>/<서명>`)가 들어간다. 로그인 없이 열리고, 열기만 해서는 거부되지 않으며(메일 보안 검사기가 미리 열어도 안전) 버튼을 누르면 처리된다. 처리하면 보내지 않은 메일과 걸지 않은 전화가 취소되고 Slack에 알린다. 요청 횟수는 IP당 제한한다.
- 담당자가 직접 확인한 동의(예: 세미나 서면 동의)는 `POST /admin/leads/{id}/consent`로 남긴다. 근거 메모가 필수다.
- 별표 6 원문은 확인하지 못했다. 실제 발송 전에 법제처 원문과 대조한다.

## 8. 평가 자료 모으기

- 자동: 승인까지 대기 시간, 승인 화면에서 검토한 시간(`review_seconds`), 수정 승인한 초안과 원본의 차이, 반려 사유, 답장 분류와 사람이 처리한 비율, 에이전트 실행 시간·성공률, 유입에서 첫 초안까지 시간, 전화 결과 분포.
- 직접: `POST /admin/baseline`으로 같은 일을 사람이 했을 때 걸린 시간(기업 리서치, 우선순위 선별, 메일 초안 작성, 인계 요약, 답장 분류)을 기록한다. 에이전트를 쓰기 전에, 구현에 쓰지 않은 시나리오로 재는 것이 좋다.
- `GET /admin/metrics`가 지표와 CSV용 `rows`를 돌려준다. 모두 가상·비식별 데이터의 모의 실험이므로 매출 성과로 일반화하지 않는다.

## 9. 검증 상태

확인한 것 (자동 테스트, 가짜 DB·가짜 Slack 클라이언트):
- 서명 검증(틀림, 5분 초과, 누락), 메시지 구성과 글자 수 제한, 알림 전송·건너뛰기·실패 무시, 딥링크.
- 승인·수정·반려(사유 창)·권한 없음·중복 클릭·수신 거부 리드, 수정 창, response_url 만료 시 chat.update, 429 재시도.
- 엔드포인트: 서명 없이는 401, 시크릿 없으면 503, Bearer 없이 서명만으로 통과, `/jobs/*` 는 Bearer 필요, CORS 유지.
- 전화 시각 알림의 중복 방지, 승인 대기 알림, 평가 지표 계산·API, 반려 사유·검토 시간 기록, 광고 표시·수신 거부 링크·동의 게이트·발송 상한.

실제 Slack 워크스페이스에서 확인한 것 (2026-11-28 새벽, `scripts/slack_sandbox.py` + 터널, 가짜 DB):
- 승인 요청 3건이 채널에 올라갔고, **승인**(발송 처리, 전화 과제 생성, 승인 기록 `channel=slack`), **수정 후 승인**(수정 창, 수정 전 본문 기록), **반려**(사유 창, `reason_code` 기록, 리드는 `needs_human`)가 모두 동작했다. 처리 뒤 메시지가 결과로 바뀌고 버튼이 사라졌다.

Cloud Run에서 확인한 것 (2026-11-28 새벽, 리비전 00013): Slack 설정과 최소 인스턴스 1로 배포, 전화 시각 알림 2건과 승인 대기 알림이 채널에 도착, 새 초안의 승인 요청(버튼 포함)이 채널에 올라감, Interactivity 요청 주소를 Cloud Run 주소로 변경, 서명된 요청은 200(0.2초)이고 틀린 서명은 401. 관리 화면은 Vercel(https://sdr-admin-web-xi.vercel.app)이며 ALLOWED_ORIGINS와 ADMIN_WEB_URL에 등록했다.

확인하지 못한 것:
- Cloud Run이 올린 메시지의 버튼을 사람이 눌러 보는 확인, 허용 목록 밖 사용자 거절, 알림 링크가 관리 화면으로 열리는지.
- Slack 토큰 2개는 이 계정에 Secret Manager 권한이 없어 일반 환경변수로 넣었다. Secret Manager로 옮기는 것이 안전하다.
- Supabase는 한 번에 최대 1000행만 돌려주므로 데이터가 많아지면 지표가 잘릴 수 있다.
- DB 변경(`20261127000100_review_metrics.sql`: 승인 기록 컬럼, 수작업 시간 테이블, 동의 메모)을 **먼저 적용한 뒤** 배포한다. 적용 전에 반려 사유·검토 시간을 보내면 승인 기록 저장이 실패한다.
