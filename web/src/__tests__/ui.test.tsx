// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockMode } from "../api/client";
import { mockRequest, resetMock } from "../api/mock";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { CallPanel } from "../components/CallPanel";
import { ToastProvider } from "../toast";
import type { CallTask, Message } from "../types";

beforeEach(() => { mockMode.set(true); resetMock(); });
afterEach(() => cleanup());

const wrap = (ui: React.ReactElement) => render(<MemoryRouter><ToastProvider>{ui}</ToastProvider></MemoryRouter>);

describe("승인 패널", () => {
  it("근거를 보여 주고 승인 버튼으로 승인한다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    const done = vi.fn();
    wrap(<ApprovalPanel message={m} onDecided={done} />);
    await screen.findByText("이 메일의 근거");
    expect(screen.getAllByText("AI 초안").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /승인·발송/ }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("approved"));
  });

  it("본문을 고치면 수정본으로 승인한다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    const done = vi.fn();
    wrap(<ApprovalPanel message={m} onDecided={done} />);
    await userEvent.click(screen.getByRole("button", { name: "수정" }));
    await userEvent.type(screen.getByLabelText("메일 본문 수정"), " 추가 문장");
    await userEvent.click(screen.getByRole("button", { name: /수정본으로 승인/ }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("edited"));
  });

  it("반려는 사유를 골라야 확정할 수 있다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    const done = vi.fn();
    wrap(<ApprovalPanel message={m} onDecided={done} />);
    await userEvent.click(screen.getByRole("button", { name: "반려" }));
    const confirm = screen.getByRole("button", { name: "반려 확정" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    await userEvent.selectOptions(screen.getByRole("combobox"), "tone");
    await userEvent.click(confirm);
    await waitFor(() => expect(done).toHaveBeenCalledWith("rejected"));
  });
});

describe("피드백에서 배우기", () => {
  it("대화형 피드백으로 초안을 다시 쓰고 작성 지침으로 저장한다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    wrap(<ApprovalPanel message={m} />);
    const box = await screen.findByLabelText("초안에 대한 피드백");
    const btn = screen.getByRole("button", { name: "다시 쓰기" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(box, "더 짧게 써 줘");
    await userEvent.click(btn);
    await screen.findByText("더 짧게 써 줘");
    expect(await screen.findByText(/작성 지침 1개를 배워/)).toBeTruthy();
    const { lessons } = await mockRequest<{ lessons: { text: string; source: string }[] }>("GET", "/admin/lessons?status=active");
    expect(lessons[0]).toMatchObject({ text: "더 짧게 써 줘", source: "chat" });
    const after = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages.find((x) => x.id === m.id)!;
    expect(after.status).toBe("pending_approval"); // 다시 써도 발송하지 않는다
  });

  it("저장하지 않기를 고르면 지침을 남기지 않는다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    wrap(<ApprovalPanel message={m} />);
    await userEvent.type(await screen.findByLabelText("초안에 대한 피드백"), "인사를 바꿔 줘");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "다시 쓰기" }));
    await screen.findByText("인사를 바꿔 줘");
    const { lessons } = await mockRequest<{ lessons: unknown[] }>("GET", "/admin/lessons?status=active");
    expect(lessons).toHaveLength(0);
  });

  it("반영한 지침을 초안 아래에 보여 준다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    wrap(<ApprovalPanel message={{ ...m, applied_lessons: [{ id: "k1", text: "본문은 5문장 이내로 쓴다" }] }} />);
    expect(await screen.findByText(/작성 지침 1개를 반영한 초안/)).toBeTruthy();
    expect(screen.getByText("본문은 5문장 이내로 쓴다")).toBeTruthy();
  });

  it("수정 승인하면 배운 지침 수를 알려 준다", async () => {
    const [m] = (await mockRequest<{ messages: Message[] }>("GET", "/admin/messages")).messages;
    wrap(<ApprovalPanel message={m} onDecided={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "수정" }));
    await userEvent.type(screen.getByLabelText("메일 본문 수정"), " 추가 문장");
    await userEvent.click(screen.getByRole("button", { name: /수정본으로 승인/ }));
    expect(await screen.findByText(/작성 지침 1개를 배웠습니다/)).toBeTruthy();
  });
});

describe("전화 패널", () => {
  it("결과를 고르면 다음에 일어날 일을 알려 주고 기록한다", async () => {
    const [c] = (await mockRequest<{ calls: CallTask[] }>("GET", "/admin/calls")).calls;
    const done = vi.fn();
    wrap(<CallPanel call={c} onDone={done} />);
    const submit = screen.getByRole("button", { name: "결과 기록" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("radio", { name: "관심 있음" }));
    expect(screen.getByText(/인계 요약이 만들어집니다/)).toBeTruthy();
    await userEvent.click(submit);
    await waitFor(() => expect(done).toHaveBeenCalled());
  });

  it("연락 거부는 한 번 더 확인한다", async () => {
    const [c] = (await mockRequest<{ calls: CallTask[] }>("GET", "/admin/calls")).calls;
    const done = vi.fn();
    wrap(<CallPanel call={c} onDone={done} />);
    await userEvent.click(screen.getByRole("radio", { name: "연락 거부" }));
    await userEvent.click(screen.getByRole("button", { name: "기록" }));
    expect(done).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /연락 거부로 기록하고 중단/ }));
    await waitFor(() => expect(done).toHaveBeenCalled());
  });
});
