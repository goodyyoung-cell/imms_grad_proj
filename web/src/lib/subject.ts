import type { Message, SubjectChoice, SubjectOption, SubjectOptionId } from "../types";

type SubjectFields = Pick<Message, "subject" | "subject_options" | "subject_choice">;

/** 제목 후보. 오래된 메일이나 답변 초안에는 없다 */
export const subjectOptionsOf = (m: SubjectFields): SubjectOption[] => m.subject_options ?? [];

/** 지금 제목이 어느 후보인지. 서버가 알려 주지 않으면 일반 제목으로 본다 */
export const subjectChoiceOf = (m: SubjectFields): SubjectChoice => m.subject_choice ?? "general";

/** 고른 후보로 바꿨을 때의 제목과 선택값. 그런 후보가 없거나 이미 그 후보면 null (서버를 부를 필요가 없다) */
export function selectSubjectOption(m: SubjectFields, id: SubjectOptionId): { subject: string; subject_choice: SubjectChoice } | null {
  const opt = subjectOptionsOf(m).find((o) => o.id === id);
  if (!opt || subjectChoiceOf(m) === id) return null;
  return { subject: opt.subject, subject_choice: opt.id };
}
