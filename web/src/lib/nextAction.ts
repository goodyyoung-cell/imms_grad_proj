import type { LeadDetail } from "../types";
import { dueLabel } from "./format";
import { REPLY_CLASS } from "./labels";

export interface NextAction { text: string; tone: "gray" | "blue" | "amber" | "green" | "red" | "violet" }

/** 고객 상태에 따라 담당자가 다음에 할 일을 한 줄로 알려 준다 */
export function nextAction(d: LeadDetail, now: Date = new Date()): NextAction {
  const l = d.lead;
  const lastReply = [...d.replies].sort((a, b) => b.received_at.localeCompare(a.received_at))[0];
  const pendingMsg = d.messages.find((m) => m.status === "pending_approval");
  const pendingCall = d.calls.find((c) => c.status === "pending");

  switch (l.status) {
    case "new": return { text: "아직 조사 전입니다. 평일 아침 7:30에 에이전트가 조사합니다.", tone: "gray" };
    case "researching": return { text: "에이전트가 조사하고 있습니다.", tone: "blue" };
    case "researched": return { text: "조사가 끝났습니다. 평일 아침 8:00에 첫 메일 초안이 만들어집니다.", tone: "blue" };
    case "awaiting_approval":
      return pendingMsg ? { text: "승인 대기 메일을 확인하고 승인, 수정, 반려를 정하세요.", tone: "amber" }
        : { text: "승인할 메일이 없습니다. 상태를 확인하세요.", tone: "gray" };
    case "contacted":
      if (pendingCall) {
        const due = dueLabel(pendingCall.due_at, now);
        return { text: due.overdue ? "지금 전화를 거세요. 스크립트와 결과 기록이 준비되어 있습니다." : `전화 예정: ${due.text}`, tone: due.overdue ? "red" : "blue" };
      }
      return { text: "메일을 보냈습니다. 고객 회신을 기다리는 중입니다.", tone: "green" };
    case "replied":
      if (lastReply?.classification === "question") return { text: "고객이 질문했습니다. 답변 초안이 올라오면 근거를 확인하고 승인하세요.", tone: "amber" };
      if (lastReply?.classification === "not_now") return { text: "고객이 나중에 연락해 달라고 했습니다. 언제가 좋은지 확인해 다시 연락하세요.", tone: "gray" };
      if (lastReply?.classification === "interested") return { text: "고객이 관심을 보였습니다. 인계 요약을 만들고 미팅을 제안하세요.", tone: "green" };
      return { text: `고객 답장이 있습니다${lastReply?.classification ? ` (${REPLY_CLASS[lastReply.classification].label})` : ""}. 내용을 확인하세요.`, tone: "green" };
    case "needs_human": return { text: "에이전트가 판단하지 못했습니다. 답장을 읽고 수신 거부 확정 또는 인계를 정하세요.", tone: "red" };
    case "handed_off": return { text: "인간 SDR이 미팅 일정을 잡을 차례입니다.", tone: "violet" };
    case "unsubscribed": return { text: "수신 거부 상태입니다. 어떤 방법으로도 연락하지 않습니다.", tone: "gray" };
    default: return { text: "", tone: "gray" };
  }
}
