import type {
  Approval, CallOutcomeInput, CallTask, ChatContext, ChatReply, ChatSession, DecisionInput, FeedbackInput, FeedbackNote, FeedbackPatch,
  ImportResult, Industry, KbDoc, Lead, LeadDetail, LeadList, Message, ReplyRow, Run,
  RunDetail, SubjectOptionId, Summary, SystemStatus,
} from "../types";
import { ApiError } from "./errors";
import { mockRequest } from "./mock";

export const DEFAULT_API = "https://sdr-agent-840381264541.asia-northeast3.run.app";
const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) || DEFAULT_API).replace(/\/$/, "");

const TOKEN_KEY = "sdr_token";
const NAME_KEY = "sdr_name";
const MOCK_KEY = "sdr_mock";

// 토큰은 탭을 닫으면 사라지는 sessionStorage에만 둔다. JOB_TOKEN은 화면 코드에 넣지 않는다.
export const session = {
  get token() { return sessionStorage.getItem(TOKEN_KEY); },
  get name() { return sessionStorage.getItem(NAME_KEY); },
  set(token: string, name: string) { sessionStorage.setItem(TOKEN_KEY, token); sessionStorage.setItem(NAME_KEY, name); },
  clear() { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(NAME_KEY); },
};

export const mockMode = {
  get on() { return sessionStorage.getItem(MOCK_KEY) === "1"; },
  set(on: boolean) { if (on) sessionStorage.setItem(MOCK_KEY, "1"); else sessionStorage.removeItem(MOCK_KEY); },
};

export { ApiError };

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (mockMode.on) return mockRequest<T>(method, path, body);
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  if (session.token) headers.Authorization = `Bearer ${session.token}`;

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "서버에 연결할 수 없습니다. 네트워크 또는 서버 상태를 확인해 주세요.");
  }
  if (res.status === 401 && path !== "/auth/login") {
    session.clear();
    onUnauthorized();
    throw new ApiError(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.");
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* JSON이 아닌 응답 */ }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `요청에 실패했습니다 (${res.status})`);
  }
  return data as T;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&") : "";
};

export const api = {
  login: (name: string, password: string) =>
    request<{ token: string; name: string; expires_at: string }>("POST", "/auth/login", { name, password }),
  me: () => request<{ name: string; kind: string }>("GET", "/admin/me"),
  summary: () => request<Summary>("GET", "/admin/summary"),
  leads: (p: { status?: string; q?: string; limit?: number; offset?: number } = {}) =>
    request<LeadList>("GET", "/admin/leads" + qs({ limit: 50, ...p })),
  lead: (id: string) => request<LeadDetail>("GET", `/admin/leads/${id}`),
  messages: (status = "pending_approval") =>
    request<{ messages: Message[] }>("GET", "/admin/messages" + qs({ status })).then((r) => r.messages),
  calls: (dueOnly = true) =>
    request<{ calls: CallTask[] }>("GET", "/admin/calls" + qs({ due_only: dueOnly })).then((r) => r.calls),
  runs: (p: { lead_id?: string; workflow?: string; limit?: number } = {}) =>
    request<{ runs: Run[] }>("GET", "/admin/runs" + qs({ limit: 50, ...p })).then((r) => r.runs),
  run: (id: string) => request<RunDetail>("GET", `/admin/runs/${id}`),

  decide: (id: string, d: DecisionInput) =>
    request<Record<string, unknown>>("POST", `/admin/messages/${id}/decision`, d),
  setSubject: (id: string, optionId: SubjectOptionId) =>
    request<Message>("POST", `/admin/messages/${id}/subject`, { option_id: optionId }),

  // 피드백 메모리
  feedback: (p: { scope?: string; status?: string; lead_id?: string; limit?: number } = {}) =>
    request<{ notes: FeedbackNote[] }>("GET", "/admin/feedback" + qs({ limit: 200, ...p })).then((r) => r.notes),
  addFeedback: (n: FeedbackInput) => request<FeedbackNote>("POST", "/admin/feedback", n),
  updateFeedback: (id: string, p: FeedbackPatch) => request<FeedbackNote>("PATCH", `/admin/feedback/${id}`, p),
  deleteFeedback: (id: string) => request<{ ok: boolean }>("DELETE", `/admin/feedback/${id}`),
  industries: () => request<{ industries: Industry[] }>("GET", "/admin/industries").then((r) => r.industries),

  // AI 에이전트 대화. 답이 올 때까지 10~60초 걸릴 수 있다.
  chat: (message: string, sessionId: string | null, context: ChatContext) =>
    request<ChatReply>("POST", "/admin/chat", { session_id: sessionId, message, context }),
  chatSession: (id: string) => request<ChatSession>("GET", `/admin/chat/sessions/${encodeURIComponent(id)}`),
  callOutcome: (id: string, o: CallOutcomeInput) =>
    request<Record<string, unknown>>("POST", `/admin/calls/${id}/outcome`, o),
  confirmUnsubscribe: (leadId: string, note?: string) =>
    request<Record<string, unknown>>("POST", `/admin/leads/${leadId}/unsubscribe`, { note }),
  importLeads: (form: FormData) => request<ImportResult>("POST", "/admin/leads/import", form),
  replies: (p: { classification?: string; handled_by?: string; limit?: number } = {}) =>
    request<{ replies: ReplyRow[] }>("GET", "/admin/replies" + qs({ limit: 100, ...p })).then((r) => r.replies),
  approvals: () => request<{ approvals: Approval[] }>("GET", "/admin/approvals?limit=200").then((r) => r.approvals),
  kb: () => request<{ docs: KbDoc[] }>("GET", "/admin/kb").then((r) => r.docs),
  status: () => request<SystemStatus>("GET", "/admin/status"),
  notifyTest: () => request<{ posted: boolean; reason?: string | null }>("POST", "/admin/notify-test", {}),
  metrics: () => request<{ rows: string[][] }>("GET", "/admin/metrics"),
  baseline: () => request<{ tasks: Record<string, string>; timings: unknown[]; stats: Record<string, { n: number; median: number | null; mean: number | null }> }>("GET", "/admin/baseline"),
  addBaseline: (b: { task: string; minutes: number; label?: string }) => request<Record<string, unknown>>("POST", "/admin/baseline", b),

  // 시연용. LLM 비용이 드니 화면에서 확인을 받고 부른다.
  research: (leadId: string) =>
    request<Record<string, unknown>>("POST", "/jobs/research", { lead_ids: [leadId], trigger: "manual" }),
  draft: (leadId: string) =>
    request<Record<string, unknown>>("POST", "/jobs/draft", { lead_ids: [leadId], trigger: "manual" }),
  handoff: (leadId: string) => request<Record<string, unknown>>("POST", "/jobs/handoff", { lead_id: leadId }),
  simulateReply: (leadId: string, body: string) =>
    request<Record<string, unknown>>("POST", "/webhooks/reply", { lead_id: leadId, body }),
};

export type { Lead };
