export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export const NOT_DEPLOYED = "이 기능은 아직 서버에 배포되지 않았습니다. 백엔드 최신 버전을 배포하면 보입니다.";

/** 화면에 보여 줄 오류 문구. 서버에 아직 없는 기능(404 Not Found)은 배포 안내로 바꾼다 */
export function errorText(e: unknown, fallback = "요청에 실패했습니다"): string {
  if (!(e instanceof ApiError)) return fallback;
  return e.status === 404 && e.message === "Not Found" ? NOT_DEPLOYED : e.message;
}
