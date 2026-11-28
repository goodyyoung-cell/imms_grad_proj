import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { invalidate } from "../api/hooks";
import { useAuth } from "../auth";
import { chatErrorMessage, isSessionGone, needsRefresh, SCREEN_LABEL, suggestedPrompts } from "../lib/chat";
import { actionLink, designBase, useChatContext } from "../lib/chatContext";
import type { ChatAction, ChatTurn } from "../types";
import { Badge } from "./ui";

const SESSION_KEY = "sdr_chat_session";

interface ChatState {
  open: boolean;
  toggle: () => void;
  close: () => void;
  turns: ChatTurn[];
  sending: boolean;
  historyLoading: boolean;
  error: string | null;
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  send: (text: string) => Promise<boolean>;
  reset: () => void;
}

const Ctx = createContext<ChatState | null>(null);

export function useChat(): ChatState {
  const v = useContext(Ctx);
  if (!v) throw new Error("ChatProvider가 필요합니다");
  return v;
}

const readSession = () => { try { return localStorage.getItem(SESSION_KEY); } catch { return null; } };
const writeSession = (id: string | null) => {
  try { if (id) localStorage.setItem(SESSION_KEY, id); else localStorage.removeItem(SESSION_KEY); } catch { /* 저장 공간을 못 쓰면 이번 탭에서만 유지된다 */ }
};

/** 대화 상태는 화면(경로)이 바뀌어도, 디자인 A와 B를 오가도 유지되도록 앱 맨 위에 둔다. */
export function ChatProvider({ children }: { children: ReactNode }) {
  const { name } = useAuth();
  const ctx = useChatContext();
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(readSession);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  const sendingRef = useRef(false);
  // 기록을 이미 불러온(또는 이번 탭에서 만든) 대화. 같은 대화를 다시 불러와 덮어쓰지 않는다.
  const loaded = useRef<string | null>(null);

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  const reset = useCallback(() => {
    loaded.current = null;
    sessionRef.current = null;
    setSessionId(null); setTurns([]); setError(null); setDraft("");
    writeSession(null);
  }, []);

  // 로그아웃하면 화면의 대화를 비운다 (대화 번호는 남겨 두어 다시 로그인하면 이어서 본다)
  useEffect(() => {
    if (name) return;
    loaded.current = null;
    setOpen(false); setTurns([]); setError(null); setDraft("");
  }, [name]);

  // 열 때 저장된 대화 기록을 불러온다
  useEffect(() => {
    if (!open || !name || !sessionId || loaded.current === sessionId) return;
    let cancelled = false;
    setHistoryLoading(true); setError(null);
    api.chatSession(sessionId)
      .then((r) => { if (!cancelled) { setTurns(r.turns ?? []); loaded.current = sessionId; } })
      .catch((e) => {
        if (cancelled) return;
        if (isSessionGone(e)) { reset(); return; }
        setError(chatErrorMessage(e));
      })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });
    return () => { cancelled = true; };
  }, [open, name, sessionId, reset]);

  // 열려 있는 동안 화면 내용이 패널 폭만큼 비켜 앉는다 (CSS의 html.chat-open)
  useEffect(() => {
    const on = open && !!name;
    document.documentElement.classList.toggle("chat-open", on);
    return () => document.documentElement.classList.remove("chat-open");
  }, [open, name]);

  // Ctrl+J(맥은 Cmd+J)로 열고 닫는다. 검색 단축키(/, Ctrl+K)와 겹치지 않는다.
  useEffect(() => {
    if (!name) return;
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "j") { e.preventDefault(); setOpen((v) => !v); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [name]);

  const send = useCallback(async (text: string): Promise<boolean> => {
    const message = text.trim();
    if (!message || sendingRef.current) return false;
    sendingRef.current = true;
    setSending(true); setError(null);
    setTurns((t) => [...t, { role: "user", text: message, actions: [], created_at: new Date().toISOString() }]);
    try {
      const r = await api.chat(message, sessionRef.current, ctxRef.current);
      const actions = r.actions ?? [];
      loaded.current = r.session_id;
      sessionRef.current = r.session_id;
      writeSession(r.session_id);
      setSessionId(r.session_id);
      setTurns((t) => [...t, { role: "assistant", text: r.reply, actions, created_at: new Date().toISOString() }]);
      if (needsRefresh(actions)) invalidate();
      return true;
    } catch (e) {
      setTurns((t) => t.slice(0, -1));
      setError(chatErrorMessage(e));
      return false;
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }, []);

  return (
    <Ctx.Provider value={{ open, toggle, close, turns, sending, historyLoading, error, draft, setDraft, send, reset }}>
      {children}
    </Ctx.Provider>
  );
}

/** 헤더 오른쪽 위의 "AI 에이전트" 버튼 */
export function ChatButton() {
  const { open, toggle } = useChat();
  return (
    <button type="button" className={`chat-btn ${open ? "on" : ""}`} aria-expanded={open} aria-controls="chat-panel" onClick={toggle}
      title="AI 에이전트 열기·닫기 (Ctrl+J)">
      <svg className="chat-btn-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden focusable="false">
        <path d="M8 1l1.6 4.4L14 7l-4.4 1.6L8 13l-1.6-4.4L2 7l4.4-1.6z" fill="currentColor" />
      </svg>
      AI 에이전트<kbd>Ctrl J</kbd>
    </button>
  );
}

function useElapsed(active: boolean): number {
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!active) { setS(0); return; }
    const t0 = Date.now();
    const id = setInterval(() => setS(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return s;
}

const ACTION_TONE: Record<ChatAction["type"], "blue" | "green" | "red" | "violet" | "gray"> = {
  message_updated: "blue", subject_changed: "blue", message_sent: "green", message_rejected: "red", feedback_saved: "violet", feedback_deleted: "gray",
};

/** 답변 아래에 "무슨 일이 일어났는지"를 칩으로 보여 준다. 누르면 해당 화면으로 간다. */
export function ActionChips({ actions }: { actions: ChatAction[] }) {
  const { pathname } = useLocation();
  if (actions.length === 0) return null;
  const base = designBase(pathname);
  return (
    <ul className="chat-actions" aria-label="이번 답변에서 한 일">
      {actions.map((a, i) => {
        const to = actionLink(a, base);
        // 메모리 저장 칩은 서버 문구가 길어도 같은 이름으로 보여 주고, 내용은 마우스를 올리면 보인다
        const text = a.type === "feedback_saved" ? "메모리에 저장됨" : a.label;
        const chip = <Badge tone={ACTION_TONE[a.type] ?? "gray"} title={a.label}>{text}{to ? " →" : ""}</Badge>;
        return <li key={`${a.type}-${i}`}>{to ? <Link to={to} className="chat-chip">{chip}</Link> : chip}</li>;
      })}
    </ul>
  );
}

function ContextLine() {
  const ctx = useChatContext();
  return (
    <p className="chat-context muted small" aria-label="대화에 함께 보내는 현재 화면">
      지금 화면: {SCREEN_LABEL[ctx.screen ?? "other"] ?? "기타"}
      {ctx.message_id && <span className="tag">이 메일</span>}
      {ctx.lead_id && <span className="tag">이 고객</span>}
    </p>
  );
}

/** 오른쪽에서 밀려 나오는 대화 패널. 열려 있는 동안 화면을 옮겨도 그대로 남는다. */
export function ChatPanel() {
  const { open, close, turns, sending, historyLoading, error, draft, setDraft, send, reset } = useChat();
  const ctx = useChatContext();
  const { pathname } = useLocation();
  const titleId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const elapsed = useElapsed(sending);

  // 열면 입력칸으로, 닫으면 열기 전에 있던 곳으로 초점을 옮긴다
  useEffect(() => {
    if (!open) return;
    const back = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => input.current?.focus(), 0);
    return () => { clearTimeout(t); if (back && document.contains(back)) back.focus(); };
  }, [open]);

  useEffect(() => { const el = log.current; if (el) el.scrollTop = el.scrollHeight; }, [turns.length, sending, historyLoading, open]);

  if (!open) return null;

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || historyLoading) return;
    setDraft("");
    const ok = await send(text);
    if (!ok) setDraft((d) => d || text);
  };

  const prompts = suggestedPrompts(ctx.screen, !!ctx.message_id);
  const empty = turns.length === 0 && !sending && !historyLoading;

  return (
    <section id="chat-panel" className={`chat-panel ${designBase(pathname) === "/b" ? "theme-b" : ""}`} role="dialog" aria-modal="false" aria-labelledby={titleId}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } }}>
      <header className="chat-head">
        <div>
          <h2 id={titleId}>AI 에이전트</h2>
          <ContextLine />
        </div>
        <div className="chat-head-actions">
          <button type="button" className="btn sm" disabled={sending || (turns.length === 0 && !draft)} onClick={reset}>새 대화</button>
          <button type="button" className="btn sm ghost" onClick={close} aria-label="AI 에이전트 닫기">닫기 <kbd>Esc</kbd></button>
        </div>
      </header>

      <div className="chat-log" ref={log} role="log" aria-live="polite" aria-relevant="additions" aria-label="대화 내용">
        {historyLoading && <div className="state loading"><span className="spinner" aria-hidden />대화를 불러오는 중</div>}
        {empty && (
          <div className="chat-empty">
            <p><strong>무엇을 도와 드릴까요?</strong></p>
            <p className="muted small">메일 제목 다시 쓰기, 피드백 저장, 승인 대기 목록 확인을 말로 시킬 수 있습니다. 아래 예시를 누르면 입력칸에 채워집니다.</p>
            <ul className="chat-prompts">
              {prompts.map((p) => (
                <li key={p}><button type="button" className="chip" onClick={() => { setDraft(p); input.current?.focus(); }}>{p}</button></li>
              ))}
            </ul>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`chat-turn ${t.role}`}>
            <div className="chat-bubble">{t.text}</div>
            {t.role === "assistant" && <ActionChips actions={t.actions ?? []} />}
          </div>
        ))}
        {sending && (
          <div className="chat-working" role="status">
            <span className="spinner" aria-hidden />
            <span>작업 중입니다{elapsed > 0 ? ` (${elapsed}초)` : ""}. 답변까지 최대 1분 정도 걸릴 수 있습니다.</span>
          </div>
        )}
      </div>

      {error && <p className="notice err chat-error" role="alert">{error}</p>}

      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="sr-only" htmlFor={`${titleId}-input`}>AI 에이전트에게 보낼 메시지</label>
        <textarea id={`${titleId}-input`} ref={input} rows={3} value={draft} placeholder="예: 이 메일 제목을 더 짧게 다시 써줘"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // 한글 입력 중(조합 중)의 Enter는 글자를 확정하는 키이므로 보내지 않는다
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
          }} />
        <div className="chat-form-foot">
          <span className="muted small">Enter 보내기 · Shift+Enter 줄바꿈</span>
          <button type="submit" className="btn primary sm" disabled={sending || historyLoading || !draft.trim()}>{sending ? "작업 중" : "보내기"}</button>
        </div>
      </form>
    </section>
  );
}
