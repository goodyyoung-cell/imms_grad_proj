import { ApiError, NOT_DEPLOYED } from "../api/errors";
import type { ChatAction } from "../types";

export { NOT_DEPLOYED };

/** 대화 오류를 화면에 보여 줄 문구로 바꾼다 */
export function chatErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "요청에 실패했습니다. 잠시 뒤 다시 시도해 주세요.";
  if (e.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (e.status === 503) return "LLM 키가 없어 대화를 쓸 수 없습니다.";
  if (e.status === 404) return NOT_DEPLOYED;
  if (e.status === 400) return "메시지를 입력해 주세요.";
  return e.message;
}

/** 서버가 모르는 대화(만료, 다른 사용자)와 아직 배포되지 않은 기능을 가른다 */
export const isSessionGone = (e: unknown): boolean =>
  e instanceof ApiError && (e.status === 403 || (e.status === 404 && e.message !== "Not Found"));

/** 화면 이름. 대화 패널이 "지금 어느 화면을 기준으로 말하는지" 알려 줄 때 쓴다 */
export const SCREEN_LABEL: Record<string, string> = {
  inbox: "업무함",
  message: "메일 검토",
  lead: "고객 상세",
  leads: "리드 목록",
  calls: "전화",
  replies: "답장함",
  memory: "메모리",
  settings: "설정·상태",
  other: "기타",
};

/** 화면을 다시 불러와야 하는 행동인지 */
export function needsRefresh(actions: ChatAction[]): boolean {
  return actions.some((a) => ["message_updated", "subject_changed", "message_sent", "message_rejected", "feedback_saved", "feedback_deleted"].includes(a.type));
}

/** 빈 대화에서 보여 줄 추천 질문. 지금 화면에 맞춰 바꾼다 */
export function suggestedPrompts(screen: string | undefined, hasMessage: boolean): string[] {
  const memory = "이 고객은 앞으로 가격 이야기를 빼줘 (기억해)";
  if (hasMessage || screen === "message") {
    return ["이 메일 제목을 더 짧게 다시 써줘", memory, "이 메일 발송해", "대기 중인 승인 요청 보여줘"];
  }
  if (screen === "lead") return [memory, "대기 중인 승인 요청 보여줘", "이 메일 제목을 더 짧게 다시 써줘"];
  return ["대기 중인 승인 요청 보여줘", "이 메일 제목을 더 짧게 다시 써줘", memory, "이 메일 발송해"];
}
