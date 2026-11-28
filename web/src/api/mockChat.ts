// 모의 서버의 AI 에이전트가 쓰는 문장 규칙. 키워드로 의도를 가려 정해진 답을 만든다 (실제 에이전트는 LLM이 한다).
import type { FeedbackKind, FeedbackScope } from "../types";

export type ChatIntent = "remember" | "forget" | "send" | "rewrite" | "pending" | "help";

/** 순서가 중요하다. "앞으로 제목은 짧게"는 다시 쓰기가 아니라 기억하라는 뜻이다. */
export function detectIntent(text: string): ChatIntent {
  const t = text.replace(/\s+/g, " ");
  if (/(메모리|기억|규칙).*(지워|삭제|잊)|(지워|삭제|잊).*(메모리|기억|규칙)/.test(t)) return "forget";
  if (/기억|앞으로|다음부터|항상|늘 /.test(t)) return "remember";
  if (/발송|보내|승인해|승인하고/.test(t)) return "send";
  if (/다시 ?써|다시 ?작성|고쳐|짧게|더 길게|바꿔/.test(t)) return "rewrite";
  if (/대기|승인 ?(목록|요청|건)|목록|보여/.test(t)) return "pending";
  return "help";
}

/** 제목을 더 짧게. 말머리, 군더더기 어미를 떼고 28자 안으로 줄인다 */
export function shortenSubject(subject: string): string {
  let s = subject
    .replace(/^\s*\[[^\]]*\]\s*/, "")
    .replace(/\s*관련(하여)?\s*/g, " ")
    .replace(/\s*(안내|소개|공유|말씀)?\s*드립니다\s*$/, "")
    .replace(/\s*(건|회신)\s*$/, "")
    .replace(/\s+/g, " ")
    .trim();
  if (s.length > 28) {
    const cut = s.slice(0, 28);
    const at = cut.lastIndexOf(" ");
    s = (at > 12 ? cut.slice(0, at) : cut).trim();
  }
  return s && s !== subject.trim() ? s : `${subject.trim()} 안내`;
}

/** 사용자가 한 말에서 한 줄 규칙을 뽑는다. 예: "이 고객은 앞으로 가격 이야기를 빼줘" -> "가격 이야기를 뺀다." */
export function ruleFromText(text: string): string {
  let s = text
    .replace(/\(.*?\)/g, " ")
    .replace(/(기억해 ?줘|기억해 ?주세요|기억해|메모해 ?줘|저장해 ?줘)/g, " ")
    .replace(/(앞으로|다음부터|항상)\s*/g, "")
    .replace(/^\s*이 (고객|회사|담당자)(은|는|에게는|에는|한테는)?\s*/, "")
    .replace(/[.!\s]+$/, "")
    .replace(/\s+/g, " ")
    .trim();
  const endings: [RegExp, string][] = [
    [/빼 ?(줘|주세요)?$/, "뺀다"],
    [/(쓰지|하지|넣지|언급하지) ?(마|말아) ?(줘|주세요)?$/, "$1 않는다"],
    [/(써|해|작성해) ?(줘|주세요)$/, "쓴다"],
  ];
  for (const [re, to] of endings) if (re.test(s)) { s = s.replace(re, to); break; }
  if (!s) s = text.trim();
  return s.endsWith(".") ? s : `${s}.`;
}

export function inferKind(text: string): FeedbackKind {
  if (/제목|짧|길이|줄여|문장/.test(text)) return "length";
  if (/어조|톤|존댓말|부드|딱딱/.test(text)) return "tone";
  if (/소식|소재|오프닝|보도|공시/.test(text)) return "topic";
  if (/미팅|제안|데모|링크/.test(text)) return "cta";
  if (/가격|빼|말고|금지|쓰지|언급|이야기/.test(text)) return "avoid";
  return "other";
}

/** 말에서 범위를 짐작한다. 특정 고객을 가리키면 고객, 업종이면 업종, 아니면 전체 */
export function inferScope(text: string, hasLead: boolean): FeedbackScope {
  if (hasLead && /이 (고객|회사|담당자|메일)|이 분/.test(text)) return "lead";
  if (/업종|이 산업/.test(text)) return "industry";
  return "global";
}
