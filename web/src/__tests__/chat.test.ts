import { beforeEach, describe, expect, it } from "vitest";
import { mockRequest, resetMock, setMockChatDelay } from "../api/mock";
import { detectIntent, inferScope, ruleFromText, shortenSubject } from "../api/mockChat";
import { ApiError } from "../api/errors";
import { chatErrorMessage, isSessionGone, needsRefresh } from "../lib/chat";
import { actionLink, deriveChatContext } from "../lib/chatContext";
import { countNotes, filterNotes, groupNotes, validateNote } from "../lib/memory";
import { selectSubjectOption, subjectChoiceOf, subjectOptionsOf } from "../lib/subject";
import type { ChatReply, ChatSession, FeedbackNote, Industry, Message } from "../types";

const opts = [
  { id: "general" as const, label: "일반 제목", subject: "일반" },
  { id: "company" as const, label: "회사명 포함", subject: "회사" },
  { id: "hook" as const, label: "소식 반영", subject: "소식" },
];

describe("제목 후보 고르기", () => {
  it("후보가 없거나 선택값이 없으면 일반 제목으로 본다", () => {
    expect(subjectOptionsOf({ subject: "x" })).toEqual([]);
    expect(subjectChoiceOf({ subject: "x" })).toBe("general");
    expect(selectSubjectOption({ subject: "x" }, "hook")).toBeNull();
  });
  it("다른 후보를 고르면 제목과 선택값을 돌려주고, 이미 고른 후보면 null이다", () => {
    const m = { subject: "일반", subject_options: opts, subject_choice: "general" as const };
    expect(selectSubjectOption(m, "hook")).toEqual({ subject: "소식", subject_choice: "hook" });
    expect(selectSubjectOption(m, "general")).toBeNull();
  });
  it("직접 고친 제목(custom)에서는 어느 후보든 고를 수 있다", () => {
    const m = { subject: "직접", subject_options: opts, subject_choice: "custom" as const };
    expect(selectSubjectOption(m, "general")?.subject_choice).toBe("general");
  });
});

describe("경로에서 챗 context 만들기", () => {
  it("디자인 A 경로", () => {
    expect(deriveChatContext("/a")).toEqual({ screen: "inbox" });
    expect(deriveChatContext("/a/approvals/m1")).toEqual({ screen: "message", message_id: "m1" });
    expect(deriveChatContext("/a/leads/l1")).toEqual({ screen: "lead", lead_id: "l1" });
    expect(deriveChatContext("/a/memory")).toEqual({ screen: "memory" });
  });
  it("디자인 B 경로", () => {
    expect(deriveChatContext("/b/message/m2")).toEqual({ screen: "message", message_id: "m2" });
    expect(deriveChatContext("/b/lead/l2")).toEqual({ screen: "lead", lead_id: "l2" });
    expect(deriveChatContext("/b/settings")).toEqual({ screen: "settings" });
  });
  it("열려 있는 메일 패널이 있으면 그 메일과 고객을 채운다. 경로의 다른 메일은 덮어쓰지 않는다", () => {
    expect(deriveChatContext("/b", { message_id: "m3", lead_id: "l3" })).toEqual({ screen: "inbox", message_id: "m3", lead_id: "l3" });
    expect(deriveChatContext("/a/approvals/m1", { message_id: "m9", lead_id: "l9" })).toEqual({ screen: "message", message_id: "m1" });
  });
  it("알 수 없는 경로는 기타로 둔다", () => {
    expect(deriveChatContext("/login")).toEqual({ screen: "other" });
  });
  it("답변 칩은 행동에 따라 메모리 화면, 메일, 고객으로 연결한다", () => {
    expect(actionLink({ type: "feedback_saved", label: "x" }, "/b")).toBe("/b/memory");
    expect(actionLink({ type: "message_updated", label: "x", message_id: "m1" }, "/a")).toBe("/m/m1");
    expect(actionLink({ type: "message_sent", label: "x", message_id: "m1", lead_id: "l1" }, "/a")).toBe("/l/l1");
    expect(needsRefresh([{ type: "feedback_deleted", label: "x" }])).toBe(true);
    expect(needsRefresh([])).toBe(false);
  });
});

describe("모의 챗의 말 알아듣기", () => {
  it("키워드로 의도를 가른다", () => {
    expect(detectIntent("대기 중인 승인 요청 보여줘")).toBe("pending");
    expect(detectIntent("이 메일 제목을 더 짧게 다시 써줘")).toBe("rewrite");
    expect(detectIntent("이 고객은 앞으로 가격 이야기를 빼줘")).toBe("remember");
    expect(detectIntent("앞으로 제목은 짧게 써줘")).toBe("remember");
    expect(detectIntent("이 메일 발송해")).toBe("send");
    expect(detectIntent("방금 저장한 기억 지워줘")).toBe("forget");
    expect(detectIntent("안녕")).toBe("help");
  });
  it("제목은 줄이고, 한 줄 규칙과 범위를 뽑는다", () => {
    const s = shortenSubject("[노바웍스] 알테오젠 상담원별 답변 품질 차이 관련 데모 요청 건");
    expect(s.length).toBeLessThanOrEqual(28);
    expect(s).not.toContain("[");
    expect(ruleFromText("이 고객은 앞으로 가격 이야기를 빼줘")).toBe("가격 이야기를 뺀다.");
    expect(inferScope("이 고객은 앞으로 가격 이야기를 빼줘", true)).toBe("lead");
    expect(inferScope("이 업종은 앞으로 공시를 쓰지 마", true)).toBe("industry");
    expect(inferScope("앞으로 제목은 짧게", true)).toBe("global");
  });
});

describe("모의 챗이 모의 데이터를 실제로 바꾼다", () => {
  beforeEach(() => { resetMock(); setMockChatDelay(() => 0); });

  const pendingList = async () => (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages?status=pending_approval")).messages;
  const chat = (message: string, context: object = {}, session_id: string | null = null) =>
    mockRequest<ChatReply>("POST", "/admin/chat", { session_id, message, context });

  it("대기 목록을 보여 주고 대화 기록을 다시 불러올 수 있다", async () => {
    const r = await chat("대기 중인 승인 요청 보여줘");
    expect(r.reply).toContain("승인을 기다리는 메일");
    const s = await mockRequest<ChatSession>("GET", `/admin/chat/sessions/${r.session_id}`);
    expect(s.turns.map((t) => t.role)).toEqual(["user", "assistant"]);
  });

  it("다시 써 달라고 하면 제목이 바뀌고 이전 버전이 남는다", async () => {
    const m = (await pendingList()).find((x) => (x.subject_options?.length ?? 0) > 0)!;
    const r = await chat("이 메일 제목을 더 짧게 다시 써줘", { screen: "message", message_id: m.id });
    expect(r.actions.map((a) => a.type)).toEqual(["message_updated"]);
    const after = (await pendingList()).find((x) => x.id === m.id)!;
    expect(after.subject).not.toBe(m.subject);
    expect(after.subject_choice).toBe("custom");
    expect(after.revisions?.at(-1)?.subject).toBe(m.subject);
  });

  it("메일을 모르면 어느 메일인지 되묻는다", async () => {
    const r = await chat("이 메일 제목을 더 짧게 다시 써줘");
    expect(r.actions).toEqual([]);
    expect(r.reply).toContain("어느 메일");
  });

  it("앞으로 기억하라고 하면 메모리에 저장되고 칩이 붙는다", async () => {
    const before = (await mockRequest<{ notes: FeedbackNote[] }>("GET", "/admin/feedback")).notes.length;
    const r = await chat("앞으로 제목은 짧게 써줘");
    expect(r.actions[0].type).toBe("feedback_saved");
    const after = (await mockRequest<{ notes: FeedbackNote[] }>("GET", "/admin/feedback")).notes;
    expect(after.length).toBe(before + 1);
    expect(after[0].source).toBe("chat_web");
  });

  it("발송하라고 하면 승인되어 대기 목록에서 빠진다", async () => {
    const m = (await pendingList())[0];
    const n = (await pendingList()).length;
    const r = await chat("이 메일 발송해", { screen: "message", message_id: m.id });
    expect(r.actions[0]).toMatchObject({ type: "message_sent", message_id: m.id });
    expect((await pendingList()).length).toBe(n - 1);
  });
});

describe("모의 제목 선택과 메모리 API", () => {
  beforeEach(() => resetMock());

  it("제목 후보를 고르면 바뀌고, 없는 후보는 400이다", async () => {
    const msgs = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages?status=pending_approval")).messages;
    const m = msgs.find((x) => x.subject_choice === "general" && (x.subject_options?.length ?? 0) > 0)!;
    const r = await mockRequest<Message>("POST", `/admin/messages/${m.id}/subject`, { option_id: "hook" });
    expect(r.subject_choice).toBe("hook");
    expect(r.subject).toBe(m.subject_options!.find((o) => o.id === "hook")!.subject);
    await expect(mockRequest("POST", `/admin/messages/${m.id}/subject`, { option_id: "nope" })).rejects.toMatchObject({ status: 400 });
  });

  it("업종 목록은 13개이고 메모는 추가, 보관, 삭제된다", async () => {
    const ind = (await mockRequest<{ industries: Industry[] }>("GET", "/admin/industries")).industries;
    expect(ind).toHaveLength(13);
    const n = await mockRequest<FeedbackNote>("POST", "/admin/feedback", { text: "테스트 규칙", scope: "industry", scope_key: "telecom", kind: "tone" });
    expect(n.scope_label).toBe("통신");
    const archived = await mockRequest<FeedbackNote>("PATCH", `/admin/feedback/${n.id}`, { status: "archived" });
    expect(archived.status).toBe("archived");
    expect((await mockRequest<{ notes: FeedbackNote[] }>("GET", "/admin/feedback")).notes.some((x) => x.id === n.id)).toBe(false);
    expect((await mockRequest<{ notes: FeedbackNote[] }>("GET", "/admin/feedback?status=all")).notes.some((x) => x.id === n.id)).toBe(true);
    await mockRequest("DELETE", `/admin/feedback/${n.id}`);
    expect((await mockRequest<{ notes: FeedbackNote[] }>("GET", "/admin/feedback?status=all")).notes.some((x) => x.id === n.id)).toBe(false);
  });
});

describe("챗 오류 문구", () => {
  it("상태 코드별로 안내한다", () => {
    expect(chatErrorMessage(new ApiError(401, "x"))).toContain("다시 로그인");
    expect(chatErrorMessage(new ApiError(503, "x"))).toBe("LLM 키가 없어 대화를 쓸 수 없습니다.");
    expect(chatErrorMessage(new ApiError(404, "Not Found"))).toContain("배포되지 않았습니다");
  });
  it("서버가 모르는 대화와 미배포 기능을 구분한다", () => {
    expect(isSessionGone(new ApiError(404, "대화를 찾을 수 없습니다"))).toBe(true);
    expect(isSessionGone(new ApiError(404, "Not Found"))).toBe(false);
  });
});

describe("메모리 목록 거르기", () => {
  const note = (over: Partial<FeedbackNote>): FeedbackNote => ({
    id: "n", scope: "global", scope_key: null, scope_label: "전체", kind: "other", text: "규칙", source: "manual", created_by: "민수",
    status: "active", applied_count: 0, created_at: "2026-11-20T00:00:00Z", ...over,
  });
  const all = [
    note({ id: "1" }),
    note({ id: "2", scope: "industry", scope_key: "battery", scope_label: "2차전지", text: "화재 언급 금지" }),
    note({ id: "3", scope: "lead", scope_key: "l1", scope_label: "알테오젠", text: "짧게" }),
    note({ id: "4", status: "archived", text: "보관된 규칙" }),
  ];
  it("상태와 범위와 검색어로 거른다", () => {
    expect(filterNotes(all, { scope: "", status: "active" }).map((n) => n.id)).toEqual(["1", "2", "3"]);
    expect(filterNotes(all, { scope: "industry", status: "active" }).map((n) => n.id)).toEqual(["2"]);
    expect(filterNotes(all, { scope: "", status: "archived" }).map((n) => n.id)).toEqual(["4"]);
    expect(filterNotes(all, { scope: "", status: "active", term: "알테오젠" }).map((n) => n.id)).toEqual(["3"]);
  });
  it("고객, 업종, 전체 순으로 묶고 개수를 센다", () => {
    expect(groupNotes(filterNotes(all, { scope: "", status: "active" })).map((g) => g.scope)).toEqual(["lead", "industry", "global"]);
    expect(countNotes(all, "active", "lead")).toBe(1);
    expect(countNotes(all, "archived")).toBe(1);
  });
  it("저장 전에 내용과 고객·업종 선택을 점검한다", () => {
    expect(validateNote({ text: " ", scope: "global" })).toContain("내용");
    expect(validateNote({ text: "a", scope: "lead", scope_key: null })).toContain("고객");
    expect(validateNote({ text: "a", scope: "industry", scope_key: null })).toContain("업종");
    expect(validateNote({ text: "a", scope: "industry", scope_key: "battery" })).toBeNull();
  });
});
