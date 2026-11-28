import type { FeedbackKind, FeedbackNote, FeedbackScope, FeedbackStatus } from "../types";

export interface NoteFilter { scope: FeedbackScope | ""; status: FeedbackStatus; term?: string }

/** 좁은 범위가 먼저. 고객 > 업종 > 전체 순으로 우선한다 */
export const SCOPE_ORDER: FeedbackScope[] = ["lead", "industry", "global"];

export function filterNotes(notes: FeedbackNote[], f: NoteFilter): FeedbackNote[] {
  const t = (f.term ?? "").trim();
  return notes.filter((n) =>
    n.status === f.status &&
    (!f.scope || n.scope === f.scope) &&
    (!t || n.text.includes(t) || n.scope_label.includes(t) || n.created_by.includes(t)));
}

/** 범위별 묶음. 비어 있는 범위는 뺀다 */
export function groupNotes(notes: FeedbackNote[]): { scope: FeedbackScope; notes: FeedbackNote[] }[] {
  return SCOPE_ORDER
    .map((scope) => ({ scope, notes: notes.filter((n) => n.scope === scope) }))
    .filter((g) => g.notes.length > 0);
}

export function countNotes(notes: FeedbackNote[], status: FeedbackStatus, scope: FeedbackScope | "" = ""): number {
  return notes.filter((n) => n.status === status && (!scope || n.scope === scope)).length;
}

/** 저장 전 입력 점검. 문제가 없으면 null */
export function validateNote(n: { text: string; scope: FeedbackScope; scope_key?: string | null }): string | null {
  if (!n.text.trim()) return "내용을 입력해 주세요.";
  if (n.scope === "lead" && !n.scope_key) return "적용할 고객을 골라 주세요.";
  if (n.scope === "industry" && !n.scope_key) return "적용할 업종을 골라 주세요.";
  return null;
}

export const KIND_OPTIONS: FeedbackKind[] = ["tone", "length", "topic", "avoid", "cta", "other"];
