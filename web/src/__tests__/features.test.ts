import { describe, expect, it } from "vitest";
import { mockRequest, resetMock } from "../api/mock";
import { complianceChecks, extractNumbers, unsupportedNumbers } from "../lib/compliance";
import { lineDiff, toCsv } from "../lib/diff";
import { nextAction } from "../lib/nextAction";
import type { Approval, KbDoc, LeadDetail, ReplyRow, SystemStatus } from "../types";

describe("발송 전 점검", () => {
  const good = "안녕하세요.\n비즈니스는 59,000원입니다.\n이 메일은 AI의 도움을 받아 작성되었습니다.\n더 이상 원하지 않으시면 수신 거부라고 답장해 주세요.";
  it("규정 문구가 모두 있으면 통과한다", () => {
    const c = complianceChecks(good, ["비즈니스 59,000원"]);
    expect(c.every((x) => x.state === "ok")).toBe(true);
  });
  it("수신 거부 안내와 AI 고지가 없으면 경고한다", () => {
    const c = complianceChecks("안녕하세요", ["x"]);
    expect(c.find((x) => x.key === "optout")?.state).toBe("warn");
    expect(c.find((x) => x.key === "ai")?.state).toBe("warn");
  });
  it("근거에 없는 수치를 찾아낸다", () => {
    expect(extractNumbers("월 29,000원, 14일 무료, 15% 할인")).toEqual(["29,000원", "14일", "15%"]);
    expect(unsupportedNumbers("월 29,000원이고 30% 할인", ["스타터 29,000원"])).toEqual(["30%"]);
  });
  it("근거 자료를 못 불러오면 수치 점검을 보류한다", () => {
    expect(complianceChecks(good, null).find((x) => x.key === "numbers")?.state).toBe("info");
  });
});

describe("줄 비교와 CSV", () => {
  it("바뀐 줄만 표시한다", () => {
    const d = lineDiff("가\n나\n다", "가\n라\n다");
    expect(d.map((x) => x.kind)).toEqual(["same", "del", "add", "same"]);
  });
  it("CSV는 따옴표를 안전하게 처리한다", () => {
    expect(toCsv([["a\"b", 1]])).toContain('"a""b","1"');
  });
});

describe("다음 할 일", () => {
  const base = (status: string, extra: Partial<LeadDetail> = {}) => ({
    lead: { id: "l", status, company_name: "x", name: "n", title: "t", source: "other", inbound_at: null, updated_at: "" },
    merged_leads: [], facts: [], score: null, messages: [], replies: [], calls: [], handoffs: [], runs: [], ...extra,
  }) as unknown as LeadDetail;
  it("상태별 안내를 준다", () => {
    expect(nextAction(base("needs_human")).tone).toBe("red");
    expect(nextAction(base("handed_off")).text).toContain("미팅");
    expect(nextAction(base("unsubscribed")).text).toContain("연락하지 않습니다");
  });
  it("전화 시각이 지났으면 지금 걸라고 알린다", () => {
    const d = base("contacted", { calls: [{ status: "pending", due_at: "2026-11-27T00:00:00+00:00" }] as never });
    expect(nextAction(d, new Date("2026-11-27T05:00:00Z")).text).toContain("지금 전화");
  });
});

describe("확장 API 모의", () => {
  it("답장함, 승인 이력, 승인 자료, 상태를 돌려준다", async () => {
    resetMock();
    const r = await mockRequest<{ replies: ReplyRow[] }>("GET", "/admin/replies");
    expect(r.replies.length).toBeGreaterThan(0);
    expect((await mockRequest<{ replies: ReplyRow[] }>("GET", "/admin/replies?handled_by=human")).replies.every((x) => x.handled_by === "human")).toBe(true);
    const a = await mockRequest<{ approvals: Approval[] }>("GET", "/admin/approvals");
    expect(a.approvals.some((x) => x.decision === "edited" && x.original_body)).toBe(true);
    const kb = await mockRequest<{ docs: KbDoc[] }>("GET", "/admin/kb");
    expect(kb.docs.length).toBeGreaterThan(0);
    const s = await mockRequest<SystemStatus>("GET", "/admin/status");
    expect(s.mode.send).toBe("sandbox");
  });
  it("승인하면 승인 이력에 남는다", async () => {
    resetMock();
    const m = (await mockRequest<{ messages: { id: string }[] }>("GET", "/admin/messages")).messages[0];
    const before = (await mockRequest<{ approvals: Approval[] }>("GET", "/admin/approvals")).approvals.length;
    await mockRequest("POST", `/admin/messages/${m.id}/decision`, { decision: "approved", review_seconds: 30 });
    const after = (await mockRequest<{ approvals: Approval[] }>("GET", "/admin/approvals")).approvals;
    expect(after.length).toBe(before + 1);
    expect(after[0].review_seconds).toBe(30);
  });
});
