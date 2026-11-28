import { beforeEach, describe, expect, it } from "vitest";
import { mockRequest, resetMock } from "../api/mock";
import { dueLabel, kstDateTime, relative } from "../lib/format";
import { kbLabel } from "../lib/labels";
import type { CallTask, LeadDetail, Message } from "../types";

const get = <T,>(p: string) => mockRequest<T>("GET", p);

describe("시간 표시", () => {
  it("UTC를 한국 시간으로 바꾼다", () => {
    expect(kstDateTime("2026-11-27T00:00:00+00:00")).toContain("09:00");
  });
  it("지난 전화 시각은 지났다고 알린다", () => {
    expect(dueLabel("2026-11-27T00:00:00+00:00", new Date("2026-11-27T05:00:00Z")).overdue).toBe(true);
    expect(dueLabel("2026-11-27T09:00:00+00:00", new Date("2026-11-27T05:00:00Z")).overdue).toBe(false);
  });
  it("상대 시간", () => {
    expect(relative("2026-11-27T03:00:00Z", new Date("2026-11-27T05:00:00Z"))).toBe("2시간 전");
  });
});

describe("라벨", () => {
  it("승인된 자료 문서를 읽기 쉽게 보여 준다", () => {
    expect(kbLabel("02_pricing#플랜")).toBe("가격·플랜 › 플랜");
  });
});

describe("모의 서버 (실제 API와 같은 규칙)", () => {
  beforeEach(() => resetMock());

  it("승인하면 발송되고 전화 과제가 생긴다", async () => {
    const [m] = (await get<{ messages: Message[] }>("/admin/messages?status=pending_approval")).messages;
    await mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "approved" });
    const lead = await get<LeadDetail>(`/admin/leads/${m.lead_id}`);
    expect(lead.lead.status).toBe("contacted");
    expect(lead.messages.find((x) => x.id === m.id)?.status).toBe("sent");
    expect(lead.calls.length).toBe(1);
  });

  it("이미 처리한 메일은 다시 처리할 수 없다", async () => {
    const [m] = (await get<{ messages: Message[] }>("/admin/messages")).messages;
    await mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "rejected" });
    await expect(mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "approved" })).rejects.toThrow("이미 처리");
  });

  it("반려하면 사람 확인 목록으로 간다", async () => {
    const [m] = (await get<{ messages: Message[] }>("/admin/messages")).messages;
    await mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "rejected", note: "어조" });
    expect((await get<LeadDetail>(`/admin/leads/${m.lead_id}`)).lead.status).toBe("needs_human");
  });

  it("수정 승인에는 본문이 필요하다", async () => {
    const [m] = (await get<{ messages: Message[] }>("/admin/messages")).messages;
    await expect(mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "edited" })).rejects.toThrow("본문");
  });

  it("관심 있음으로 기록하면 인계 상태가 된다", async () => {
    const [c] = (await get<{ calls: CallTask[] }>("/admin/calls")).calls;
    await mockRequest("POST", `/admin/calls/${c.id}/outcome`, { outcome: "interested" });
    expect((await get<LeadDetail>(`/admin/leads/${c.lead_id}`)).lead.status).toBe("handed_off");
  });

  it("수신 거부를 확정하면 남은 메일과 전화가 취소된다", async () => {
    const [m] = (await get<{ messages: Message[] }>("/admin/messages")).messages;
    await mockRequest("POST", `/admin/leads/${m.lead_id}/unsubscribe`, {});
    const d = await get<LeadDetail>(`/admin/leads/${m.lead_id}`);
    expect(d.lead.status).toBe("unsubscribed");
    expect(d.messages.every((x) => x.status !== "pending_approval")).toBe(true);
  });
});
