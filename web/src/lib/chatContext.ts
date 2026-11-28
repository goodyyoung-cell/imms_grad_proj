import { useMemo, useSyncExternalStore } from "react";
import { useLocation } from "react-router-dom";
import type { ChatAction, ChatContext } from "../types";

/** 승인 패널처럼 화면 한가운데 열려 있는 메일. 경로만으로는 알 수 없는 "이 메일"을 채운다 */
export interface ChatFocus { message_id?: string; lead_id?: string }

let stack: { focus: ChatFocus }[] = [];
let current: ChatFocus = {};
const listeners = new Set<() => void>();

/** 메일 패널이 열려 있는 동안 등록한다. 돌려주는 함수를 부르면 해제된다 */
export function pushChatFocus(focus: ChatFocus): () => void {
  const entry = { focus };
  stack = [...stack, entry];
  current = entry.focus;
  listeners.forEach((l) => l());
  return () => {
    stack = stack.filter((e) => e !== entry);
    current = stack.length ? stack[stack.length - 1].focus : {};
    listeners.forEach((l) => l());
  };
}

const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const snapshot = () => current;

/** 경로(/a/approvals/ID, /b/lead/ID 등)에서 서버에 보낼 현재 화면 정보를 만든다 */
export function deriveChatContext(pathname: string, focus: ChatFocus = {}): ChatContext {
  const [design, page, id] = pathname.split("/").filter(Boolean);
  let ctx: ChatContext;
  if (design === "a") {
    if (!page) ctx = { screen: "inbox" };
    else if (page === "approvals") ctx = id ? { screen: "message", message_id: id } : { screen: "inbox" };
    else if (page === "calls") ctx = { screen: "calls" };
    else if (page === "leads" || page === "exceptions") ctx = id ? { screen: "lead", lead_id: id } : { screen: "leads" };
    else if (page === "replies") ctx = id ? { screen: "lead", lead_id: id } : { screen: "replies" };
    else if (page === "memory" || page === "settings") ctx = { screen: page };
    else ctx = { screen: "other" };
  } else if (design === "b") {
    if (!page) ctx = { screen: "inbox" };
    else if (page === "lead" && id) ctx = { screen: "lead", lead_id: id };
    else if (page === "message" && id) ctx = { screen: "message", message_id: id };
    else if (page === "call") ctx = { screen: "calls" };
    else if (page === "replies" || page === "memory" || page === "settings") ctx = { screen: page };
    else ctx = { screen: "other" };
  } else {
    ctx = { screen: "other" };
  }

  // 경로의 메일과 다른 메일이 등록되어 있으면 이전 화면의 흔적이므로 무시한다
  const usable = !ctx.message_id || !focus.message_id || ctx.message_id === focus.message_id;
  if (usable) {
    if (!ctx.message_id && focus.message_id) ctx.message_id = focus.message_id;
    if (!ctx.lead_id && focus.lead_id) ctx.lead_id = focus.lead_id;
  }
  return ctx;
}

/** 지금 화면의 context. 화면을 옮길 때마다 새 값이 된다 */
export function useChatContext(): ChatContext {
  const { pathname } = useLocation();
  const focus = useSyncExternalStore(subscribe, snapshot);
  return useMemo(() => deriveChatContext(pathname, focus), [pathname, focus]);
}

/** 지금 쓰는 디자인의 경로 머리 (/a 또는 /b) */
export const designBase = (pathname: string): "/a" | "/b" => (pathname.startsWith("/b") ? "/b" : "/a");

export function useMemoryPath(): string {
  const { pathname } = useLocation();
  return `${designBase(pathname)}/memory`;
}

/** 답변 아래 칩이 누르면 가는 곳. 메일과 고객은 Slack 링크와 같은 /m, /l 주소를 쓴다 */
export function actionLink(a: ChatAction, base: "/a" | "/b"): string | null {
  if (a.type === "feedback_saved" || a.type === "feedback_deleted") return `${base}/memory`;
  if ((a.type === "message_sent" || a.type === "message_rejected") && a.lead_id) return `/l/${a.lead_id}`;
  if (a.message_id) return `/m/${a.message_id}`;
  if (a.lead_id) return `/l/${a.lead_id}`;
  return null;
}
