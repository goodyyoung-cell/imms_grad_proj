# 소식 기반 초안, 대화형 에이전트, 피드백 메모리: API 계약

관리 화면(web/)이 쓰는 새 필드와 엔드포인트. 모두 `Authorization: Bearer adm.…` 로그인 토큰이 필요하다.
서버에 아직 배포되지 않았으면 404가 오고, 화면은 "아직 서버에 배포되지 않았습니다"를 보여 준다 (기존 규칙).

## 1. 메일(messages)에 추가되는 필드

`GET /admin/messages`, `GET /admin/leads/{id}`의 messages 항목에 아래가 붙는다. 기존 필드(`subject`, `body`, `citations` 등)는 그대로다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `subject_options` | `{id, label, subject}[]` | 제목 후보. `id`는 `general` / `company` / `hook`. 첫 메일 초안에만 있고, 오래된 메일이나 답변 초안에는 빈 배열이다 |
| `subject_choice` | `"general" \| "company" \| "hook" \| "custom"` | 지금 `subject`가 어느 후보인지. 기본은 `general`(일반 제목). 사람이 직접 고쳤으면 `custom` |
| `hook` | `{type, summary, source_url, source_title} \| null` | 오프닝에 쓴 소재. `type`은 `company`(회사 소식) / `industry`(업종 화두) / `inquiry`(문의 사유). 없으면 null |
| `revisions` | `{at, by, instruction, subject, body}[]` | 챗으로 다시 쓰기 전의 이전 버전들 (오래된 순) |
| `feedback_applied` | `{id, scope, scope_key, scope_label, kind, text}[]` | 이 초안을 쓸 때 반영한 승인자 피드백. 없으면 빈 배열 |

`citations` 항목 유형이 늘어난다: `{"type":"fact"|"kb"|"feedback"|"industry","id":"…"}`. `industry`는 업종 브리프 기사.

## 2. 제목 후보 선택

```
POST /admin/messages/{message_id}/subject
{ "option_id": "general" | "company" | "hook" }
→ 갱신된 메시지 (subject, subject_choice 포함)
```

- 승인 대기(`pending_approval`) 메시지만 바꿀 수 있다. 아니면 400.
- 해당 후보가 없으면 400.
- 승인 요청 `POST /admin/messages/{id}/decision`에 선택 필드 `subject_option`(위와 같은 값)이 추가된다. 보내면 승인 직전에 그 제목으로 바꾼다.

## 3. 피드백 메모리

```
GET    /admin/feedback?scope=lead|industry|global&status=active|archived|all&lead_id=…&limit=200
       → { "notes": [Note, …] }   (기본 status=active, 최신순)
POST   /admin/feedback            { text, scope, scope_key?, kind? }   → Note
PATCH  /admin/feedback/{id}       { text?, scope?, scope_key?, kind?, status? }   → Note
DELETE /admin/feedback/{id}       → { "ok": true }
```

`Note`:

```json
{
  "id": "uuid",
  "scope": "lead" | "industry" | "global",
  "scope_key": "리드 id | 업종 키 | null",
  "scope_label": "회사명(리드) | 업종 이름 | 전체",
  "kind": "tone" | "length" | "topic" | "avoid" | "cta" | "other",
  "text": "한 줄 규칙. 예: 제목은 30자 안으로 쓴다",
  "source": "chat_web" | "chat_slack" | "manual",
  "created_by": "이름",
  "status": "active" | "archived",
  "applied_count": 3,
  "created_at": "ISO 8601"
}
```

- `scope=lead`이면 `scope_key`에 리드 id, `scope=industry`이면 업종 키(예: `semiconductor`), `global`이면 null.
- 업종 키와 이름 목록은 `GET /admin/industries` → `{ "industries": [{ "key", "label" }] }`.

## 4. 대화형 에이전트

```
POST /admin/chat
{
  "session_id": "uuid | null",        // 없으면 새 대화
  "message": "이 메일 제목을 더 짧게 다시 써줘",
  "context": { "screen": "message", "message_id": "…", "lead_id": "…" }   // 모두 선택. 지금 보는 화면
}
→ {
  "session_id": "uuid",
  "reply": "다시 썼습니다. …",       // 화면에 그대로 보여 줄 한국어 답변 (줄바꿈 포함 일반 텍스트)
  "actions": [Action, …]            // 이번 답변에서 실제로 일어난 일
}

GET /admin/chat/sessions/{session_id}
→ { "session_id", "turns": [{ "role": "user" | "assistant", "text", "actions": [Action], "created_at" }] }
```

`Action`: `{ "type", "label", "message_id"?, "lead_id"?, "feedback_id"? }`

| type | 의미 | 화면이 할 일 |
|---|---|---|
| `message_updated` | 메일 초안을 다시 씀 | 해당 메일 화면을 다시 불러온다 |
| `subject_changed` | 제목 후보를 바꿈 | 위와 같다 |
| `message_sent` | 승인하고 발송함(샌드박스) | 대기 목록과 해당 메일을 다시 불러온다 |
| `message_rejected` | 반려함 | 위와 같다 |
| `feedback_saved` | 피드백을 메모리에 저장함 | "메모리에 저장됨" 칩과 메모리 화면 링크를 보여 준다 |
| `feedback_deleted` | 피드백을 지움 | 메모리 목록을 다시 불러온다 |

- 응답에 10~60초가 걸릴 수 있다 (스트리밍 없음). 답을 기다리는 동안 "작업 중" 표시를 보여 준다.
- `context`의 `message_id`, `lead_id`는 "이 메일", "이 고객"이 무엇인지 서버가 알게 하려는 것이다. 화면을 옮길 때마다 최신 값을 보낸다.
- 챗으로 발송을 직접 지시하면 서버가 바로 발송한다 (확인 단계 없음). 에이전트가 스스로 발송하지는 않는다.
- 오류: 401(로그인 만료), 400(빈 메시지), 503(LLM 키 없음), 404(서버 미배포).

## 5. 기타

- `GET /admin/status`에 `chat: { "enabled": true, "slack_events": true | false }`와 `memory: { "active_notes": 숫자 }`가 추가된다.
- `/admin/approvals`의 `channel` 값에 `chat_web`, `chat_slack`이 추가된다 (챗으로 승인·반려한 기록).
