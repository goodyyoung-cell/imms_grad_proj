// 백엔드 없이 화면을 시험하고 시연하기 위한 모의 서버. `?mock=1`로 켠다.
// 데이터는 실제 배포 서버의 응답 일부(회사는 상장사, 담당자·이메일은 가상)에 가상 상태 몇 건을 더한 것이다.
import type {
  Approval, KbDoc, ReplyRow, SystemStatus,
  CallOutcomeInput, CallTask, DecisionInput, Handoff, LeadDetail, LeadStatus, Message, Reply, ReplyClass,
  LearnResult, Lesson, Revision, Run, RunDetail, Summary,
} from "../types";
import fixtures from "./fixtures.json";
import { ApiError } from "./errors";

type Fixtures = {
  total: number;
  leads: LeadDetail["lead"][];
  details: Record<string, LeadDetail>;
  runs: Run[];
  runDetails: Record<string, RunDetail>;
};

let db: Fixtures = structuredClone(fixtures) as unknown as Fixtures;
let seq = 1000;
const uid = (p: string) => `${p}-${++seq}`;
const nowIso = () => new Date().toISOString();
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

let approvals: Approval[] = [];
let lessons: Lesson[] = [];
let revisions: Revision[] = [];

/** 모의 서버의 '피드백에서 배우기'. 실제 서버는 LLM이 일반화한 지침을 뽑는다. 여기서는 담당자 말을 다듬어 지침으로 둔다 */
function learn(source: Lesson["source"], msg: Message, text: string | null | undefined, by: string): LearnResult {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (!t) return { status: "skipped", reason: "배울 내용이 없음" };
  const lesson: Lesson = {
    id: uid("lesson"), text: t.length > 80 ? t.slice(0, 80) + "…" : t, scope: "draft", source, evidence: t,
    status: "active", message_id: msg.id, lead_id: msg.lead_id, created_by: by, created_at: nowIso(),
  };
  lessons.unshift(lesson);
  return { status: "ok", saved: [{ id: lesson.id, text: lesson.text }] };
}

function seedApprovals() {
  const sent = Object.values(db.details).flatMap((d) => d.messages).filter((m) => m.status === "sent");
  approvals = sent.map((m, i) => ({
    id: "appr-seed-${i}", message_id: m.id, decision: i === 0 ? "edited" : "approved", decided_by: i === 0 ? "민수" : "영훈",
    decided_at: m.sent_at ?? m.created_at, note: null, reason_code: null,
    original_body: i === 0 ? m.body.replace(/\n.*$/s, "") : null, review_seconds: i === 0 ? 94 : 41, channel: i === 0 ? "slack" : "admin",
    message: { id: m.id, lead_id: m.lead_id, kind: m.kind, subject: m.subject, body: m.body, status: m.status }, lead: brief(m.lead_id),
  }));
}

export function resetMock() { db = structuredClone(fixtures) as unknown as Fixtures; seq = 1000; seedApprovals(); lessons = []; revisions = []; }

function kbFromRuns(): KbDoc[] {
  const docs = new Map<string, KbDoc>();
  for (const rd of Object.values(db.runDetails)) {
    for (const s of rd.steps) {
      const results = (s.output as { results?: { id: string; doc: string; heading: string; text: string }[] } | null)?.results;
      if (s.tool_name !== "search_kb" || !results) continue;
      for (const r of results) {
        const key = r.id.split("#")[0];
        const d = docs.get(key) ?? { id: key, title: r.doc, sections: [] };
        if (!d.sections.some((x) => x.id === r.id)) d.sections.push({ id: r.id, heading: r.heading, text: r.text });
        docs.set(key, d);
      }
    }
  }
  return [...docs.values()].sort((a, b) => a.id.localeCompare(b.id));
}

seedApprovals();

function brief(id: string) {
  const l = db.leads.find((x) => x.id === id) ?? db.details[id]?.lead;
  if (!l) return null;
  return { id: l.id, name: l.name, company_name: l.company_name, title: l.title, status: l.status, email: l.email, phone: l.phone };
}

function setStatus(id: string, status: LeadStatus) {
  const l = db.leads.find((x) => x.id === id);
  if (l) { l.status = status; l.updated_at = nowIso(); }
  const d = db.details[id];
  if (d) { d.lead.status = status; d.lead.updated_at = nowIso(); }
}

function detailOf(id: string): LeadDetail {
  const existing = db.details[id];
  if (existing) return existing;
  const lead = db.leads.find((x) => x.id === id);
  if (!lead) throw new ApiError(404, "리드를 찾을 수 없습니다");
  const d: LeadDetail = {
    lead, merged_leads: [], facts: [], score: lead.score != null ? { score: lead.score, tags: lead.tags ?? [], breakdown: {} } : null,
    messages: [], replies: [], calls: [], handoffs: [], runs: [],
  };
  db.details[id] = d;
  return d;
}

const allMessages = () => Object.values(db.details).flatMap((d) => d.messages);
const allCalls = () => Object.values(db.details).flatMap((d) => d.calls);

function render(script: CallTask["script"], lead: { company_name: string; name: string | null; title: string | null; phone?: string | null }) {
  const L: string[] = [`[콜 팔로업] ${lead.company_name} · ${lead.name ?? ""} (${lead.title ?? ""})`, `전화: ${lead.phone ?? "-"}`, "",
    "■ 시작 전", `- ${script.compliance.permission}`, "", "■ 첫 마디", script.opening, "", "■ 이야기할 것"];
  script.talking_points.forEach((t) => L.push(`- ${t.text}`));
  L.push("", "■ 계기를 묻는 질문");
  script.compelling_questions.forEach((q) => L.push(`- ${q.question}`));
  L.push("", "■ 미팅 제안", script.meeting_ask);
  return L.join("\n");
}

function makeCall(msg: Message): CallTask {
  const template = allCalls()[0];
  const lead = brief(msg.lead_id)!;
  const script = structuredClone(template?.script ?? ({} as CallTask["script"]));
  script.notices = [];
  script.phone = lead.phone ?? undefined;
  script.opening = `안녕하세요, ${lead.name} ${lead.title}님. 노바웍스 노바데스크 담당자 ○○○입니다. 어제 '${msg.subject}' 제목으로 메일을 드렸는데, 잘 받으셨는지 확인차 연락드렸습니다.`;
  script.voicemail = `안녕하세요, ${lead.name} ${lead.title}님. 노바웍스 노바데스크 담당자입니다. 보내 드린 메일 관련해서 연락드렸습니다. 편하실 때 메일을 확인해 주시면 감사하겠습니다.`;
  const call: CallTask = {
    id: uid("call"), lead_id: msg.lead_id, message_id: msg.id, run_id: null, script, script_text: "", status: "pending",
    due_at: new Date(Date.now() + 3600_000).toISOString(), outcome: null, outcome_notes: null, called_by: null, called_at: null,
    email_received: null, meeting_preference: null, compelling_reason: null,
  };
  call.script_text = render(script, { ...lead, phone: lead.phone });
  return call;
}

function addRun(leadId: string, workflow: Run["workflow"], text: string): Run {
  const run: Run = {
    id: uid("run"), lead_id: leadId, workflow, trigger: "manual", model: "claude:claude-sonnet-5-5", status: "succeeded",
    stop_reason: "terminal_tool", final_text: text, error: null, started_at: new Date(Date.now() - 20_000).toISOString(), finished_at: nowIso(),
  };
  db.runs.unshift(run);
  detailOf(leadId).runs.unshift(run);
  db.runDetails[run.id] = { ...run, steps: [{ step_no: 1, kind: "llm", tool_name: null, text, input: null, output: null, is_error: false, duration_ms: 3200 }] };
  return run;
}

function classify(text: string): { c: ReplyClass; conf: number } {
  if (/거부|연락.*(말|마)|메일.*(말|마)|수신/.test(text)) return { c: "unsubscribe", conf: 0.95 };
  if (/가능|좋습니다|해보고 싶|미팅|데모/.test(text)) return { c: "interested", conf: 0.9 };
  if (/나중|내년|다음 분기|지금은/.test(text)) return { c: "not_now", conf: 0.88 };
  if (/\?|얼마|어떻게|가격/.test(text)) return { c: "question", conf: 0.8 };
  return { c: "unclear", conf: 0.4 };
}

export async function mockRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  await wait(180);
  const url = new URL(path, "http://mock");
  const p = url.pathname;
  const q = url.searchParams;
  const b = (body ?? {}) as Record<string, unknown>;
  const out = (v: unknown) => structuredClone(v) as T;

  if (method === "POST" && p === "/auth/login") {
    if (!String(b.name ?? "").trim()) throw new ApiError(400, "이름을 1~30자로 입력해 주세요");
    return out({ token: "mock", name: String(b.name), expires_at: new Date(Date.now() + 8 * 3600_000).toISOString() });
  }
  if (p === "/admin/me") return out({ name: "시연", kind: "admin" });

  if (method === "GET" && p === "/admin/summary") {
    const counts: Record<string, number> = {};
    db.leads.forEach((l) => { counts[l.status] = (counts[l.status] ?? 0) + 1; });
    const pend = allCalls().filter((c) => c.status === "pending");
    const s: Summary = {
      lead_status_counts: counts,
      pending_approvals: allMessages().filter((m) => m.status === "pending_approval").length,
      calls_due: pend.filter((c) => new Date(c.due_at).getTime() <= Date.now()).length,
      calls_pending: pend.length,
      recent_runs: db.runs.slice(0, 10).map((r) => ({ ...r, lead: r.lead_id ? brief(r.lead_id) : null })),
    };
    return out(s);
  }

  if (method === "GET" && p === "/admin/leads") {
    let rows = db.leads.slice().sort((a, b2) => b2.updated_at.localeCompare(a.updated_at));
    const st = q.get("status"); if (st) rows = rows.filter((l) => l.status === st);
    const term = q.get("q"); if (term) rows = rows.filter((l) => l.company_name.includes(term));
    const off = Number(q.get("offset") ?? 0), lim = Number(q.get("limit") ?? 50);
    return out({ total: rows.length, leads: rows.slice(off, off + lim) });
  }
  if (method === "GET" && p.startsWith("/admin/leads/")) return out(detailOf(p.split("/")[3]));

  if (method === "GET" && p === "/admin/messages") {
    const st = q.get("status") ?? "pending_approval";
    const rows = allMessages().filter((m) => m.status === st).sort((a, b2) => b2.created_at.localeCompare(a.created_at));
    return out({ messages: rows.map((m) => ({ ...m, lead: brief(m.lead_id) })) });
  }
  if (method === "GET" && p === "/admin/calls") {
    const dueOnly = q.get("due_only") !== "false";
    const rows = allCalls().filter((c) => c.status === "pending" && (!dueOnly || new Date(c.due_at).getTime() <= Date.now()));
    return out({ calls: rows.map((c) => ({ ...c, lead: brief(c.lead_id) })) });
  }
  if (method === "GET" && p === "/admin/runs") {
    let rows = db.runs;
    const lid = q.get("lead_id"); if (lid) rows = rows.filter((r) => r.lead_id === lid);
    const wf = q.get("workflow"); if (wf) rows = rows.filter((r) => r.workflow === wf);
    return out({ runs: rows.slice(0, Number(q.get("limit") ?? 50)).map((r) => ({ ...r, lead: r.lead_id ? brief(r.lead_id) : null })) });
  }
  if (method === "GET" && p.startsWith("/admin/runs/")) {
    const id = p.split("/")[3];
    const d = db.runDetails[id];
    if (d) return out(d);
    const r = db.runs.find((x) => x.id === id);
    if (!r) throw new ApiError(404, "실행 기록을 찾을 수 없습니다");
    return out({ ...r, steps: [{ step_no: 1, kind: "llm", tool_name: null, text: r.final_text ?? "", input: null, output: null, is_error: false, duration_ms: null }] });
  }

  const decision = p.match(/^\/admin\/messages\/([^/]+)\/decision$/);
  if (method === "POST" && decision) {
    const d = b as unknown as DecisionInput;
    const msg = allMessages().find((m) => m.id === decision[1]);
    if (!msg) throw new ApiError(404, "메일을 찾을 수 없습니다");
    if (msg.status !== "pending_approval") throw new ApiError(400, "이미 처리된 메일입니다");
    const original = msg.body;
    const record = (decision: Approval["decision"]) => approvals.unshift({
      id: uid("appr"), message_id: msg.id, decision, decided_by: "시연", decided_at: nowIso(), note: d.note ?? null, reason_code: d.reason_code ?? null,
      original_body: decision === "edited" ? original : null, review_seconds: d.review_seconds ?? null, channel: "admin",
      message: { id: msg.id, lead_id: msg.lead_id, kind: msg.kind, subject: msg.subject, body: d.edited_body ?? msg.body, status: msg.status }, lead: brief(msg.lead_id),
    });
    if (d.decision === "rejected") {
      record("rejected");
      msg.status = "rejected"; setStatus(msg.lead_id, "needs_human");
      const learned = d.note ? learn("reject", msg, d.note, "시연") : undefined;
      return out({ message_id: msg.id, status: "rejected", lessons: learned });
    }
    if (d.decision === "edited") {
      if (!d.edited_body?.trim()) throw new ApiError(400, "수정한 본문이 필요합니다");
      msg.body = d.edited_body;
    }
    msg.status = "sent"; msg.sent_at = nowIso(); msg.provider_message_id = uid("sandbox"); record(d.decision);
    setStatus(msg.lead_id, "contacted");
    const detail = detailOf(msg.lead_id);
    const call = msg.kind === "initial" ? makeCall(msg) : null;
    if (call) detail.calls.push(call);
    const learned = d.decision === "edited" ? learn("edit", msg, d.note || "담당자가 고친 방식대로 쓴다", "시연") : undefined;
    return out({ message_id: msg.id, status: "sent", call_task_id: call?.id ?? null, lessons: learned });
  }

  const revise = p.match(/^\/admin\/messages\/([^/]+)\/revise$/);
  if (method === "POST" && revise) {
    const feedback = String(b.feedback ?? "").trim();
    if (!feedback) throw new ApiError(400, "피드백을 입력해 주세요");
    const msg = allMessages().find((m) => m.id === revise[1]);
    if (!msg) throw new ApiError(404, "메일을 찾을 수 없습니다");
    if (msg.status !== "pending_approval") throw new ApiError(400, "승인 대기 중인 메일만 다시 쓸 수 있습니다");
    const cut = msg.body.indexOf("\n---");
    const main = (cut >= 0 ? msg.body.slice(0, cut) : msg.body).split("\n").filter((l) => l.trim());
    const footer = cut >= 0 ? msg.body.slice(cut) : "";
    const after = [main[0], ...main.slice(1, 3), "20분 정도 통화 가능하실까요?"].filter(Boolean).join("\n") + footer;
    revisions.push({ id: uid("rev"), message_id: msg.id, feedback, before_subject: msg.subject, before_body: msg.body,
      after_subject: msg.subject, after_body: after, requested_by: "시연", created_at: nowIso() });
    msg.body = after;
    addRun(msg.lead_id, "draft_revise", `담당자 피드백("${feedback}")에 맞춰 초안을 다시 썼습니다.`);
    const learned = b.save_as_lesson ? learn("chat", msg, feedback, "시연") : undefined;
    return out({ message_id: msg.id, status: "revised", message: msg, lessons: learned });
  }
  const revs = p.match(/^\/admin\/messages\/([^/]+)\/revisions$/);
  if (method === "GET" && revs) return out({ revisions: revisions.filter((r) => r.message_id === revs[1]) });
  if (method === "GET" && p === "/admin/lessons") {
    const st = q.get("status");
    return out({ lessons: lessons.filter((l) => !st || l.status === st) });
  }
  const lessonSt = p.match(/^\/admin\/lessons\/([^/]+)\/status$/);
  if (method === "POST" && lessonSt) {
    const l = lessons.find((x) => x.id === lessonSt[1]);
    if (!l) throw new ApiError(404, "지침을 찾을 수 없습니다");
    l.status = b.status === "disabled" ? "disabled" : "active";
    return out(l);
  }

  const outcome = p.match(/^\/admin\/calls\/([^/]+)\/outcome$/);
  if (method === "POST" && outcome) {
    const o = b as unknown as CallOutcomeInput;
    const call = allCalls().find((c) => c.id === outcome[1]);
    if (!call) throw new ApiError(404, "콜 과제를 찾을 수 없습니다");
    if (call.status !== "pending") throw new ApiError(400, "이미 기록된 통화입니다");
    Object.assign(call, { status: "done", outcome: o.outcome, outcome_notes: o.notes ?? null, called_by: "시연", called_at: nowIso(),
      email_received: o.email_received ?? null, meeting_preference: o.meeting_preference ?? null, compelling_reason: o.compelling_reason ?? null });
    const next: Partial<Record<string, LeadStatus>> = { interested: "handed_off", not_now: "replied", needs_info: "needs_human", wrong_contact: "needs_human", unsubscribe: "unsubscribed" };
    const st = next[o.outcome]; if (st) setStatus(call.lead_id, st);
    return out({ task_id: call.id, outcome: o.outcome, lead_status: st ?? null });
  }

  const unsub = p.match(/^\/admin\/leads\/([^/]+)\/unsubscribe$/);
  if (method === "POST" && unsub) {
    const d = detailOf(unsub[1]);
    setStatus(unsub[1], "unsubscribed");
    d.messages.filter((m) => m.status === "pending_approval").forEach((m) => { m.status = "cancelled"; });
    d.calls.filter((c) => c.status === "pending").forEach((c) => { c.status = "cancelled"; });
    return out({ lead_id: unsub[1], status: "unsubscribed" });
  }

  if (method === "POST" && p === "/admin/leads/import") {
    const dry = (b as unknown as FormData).get?.("dry_run") !== "false";
    return out({ dry_run: dry, created: 8, merged: 2, skipped: 1, errors: 1, columns: { 이름: "name", 이메일: "email", 회사명: "company_name" }, ignored_columns: ["비고2"],
      details: { created: [], merged: [], skipped: [], errors: [{ row: 11, error: "이메일 형식이 올바르지 않습니다" }] } });
  }

  if (method === "GET" && p === "/admin/replies") {
    const cls = q.get("classification"), by = q.get("handled_by");
    const rows: ReplyRow[] = Object.values(db.details).flatMap((dd) => dd.replies.map((r) => ({ ...r, lead: brief(r.lead_id) })))
      .filter((r) => (!cls || r.classification === cls) && (!by || r.handled_by === by)).sort((a, b2) => b2.received_at.localeCompare(a.received_at));
    return out({ replies: rows });
  }
  if (method === "GET" && p === "/admin/approvals") return out({ approvals });
  if (method === "GET" && p === "/admin/kb") return out({ docs: kbFromRuns() });
  if (method === "GET" && p === "/admin/status") {
    const s: SystemStatus = {
      mode: { send: "sandbox", llm_default: "claude", providers: { claude: true, gemini: false } },
      slack: { configured: false, signing_configured: false, channel: null, approvers: 0, notify_kinds: ["approval", "needs_human", "unsubscribe", "handoff", "followup", "call_due", "reminder"], web_url: null },
      safeguards: { require_consent: false, max_sends_per_day: 50, sent_today: allMessages().filter((m) => m.status === "sent").length, sender_name: "노바웍스", sender_contact: "sales@novaworks.example" },
      knowledge_docs: kbFromRuns().length,
    };
    return out(s);
  }
  if (method === "POST" && p === "/admin/notify-test") return out({ posted: false, reason: "시연 데이터에서는 Slack으로 보내지 않습니다" });

  if (method === "GET" && p === "/admin/metrics") {
    return out({ rows: [["승인", "승인 대기를 제외한 처리 건수", "4"], ["승인", "그대로 승인 / 수정 승인 / 반려", "2 / 1 / 1"], ["승인", "검토 시간 중앙값", "38초"],
      ["에이전트", "초안 1건당 처리 시간 중앙값", "17초"], ["에이전트", "근거 없는 사실이 걸러진 비율", "0%"], ["답장 분류", "사람 확인으로 넘긴 비율", "25%"]] });
  }
  if (method === "GET" && p === "/admin/baseline") {
    const tasks = { research: "기업 리서치", prioritize: "우선순위 선별", draft: "메일 초안 작성", handoff: "인계 요약", reply_triage: "답장 분류" };
    return out({ tasks, timings: [], stats: { research: { n: 2, median: 1080, mean: 1140 }, prioritize: { n: 0, median: null, mean: null }, draft: { n: 3, median: 600, mean: 640 }, handoff: { n: 0, median: null, mean: null }, reply_triage: { n: 0, median: null, mean: null } } });
  }
  if (method === "POST" && p === "/admin/baseline") return out({ ok: true });

  if (method === "POST" && p === "/jobs/research") {
    const id = (b.lead_ids as string[])[0];
    setStatus(id, "researched");
    detailOf(id).facts.push({ id: uid("fact"), lead_id: id, field: "summary", value: "시연용으로 새로 조사한 기업 개요입니다.", source_type: "dart", source_url: "https://opendart.fss.or.kr/", retrieved_at: nowIso() });
    addRun(id, "research", "홈페이지와 공시에서 확인한 사실을 저장했습니다.");
    return out({ processed: 1, results: [] });
  }
  if (method === "POST" && p === "/jobs/draft") {
    const id = (b.lead_ids as string[])[0];
    const lead = detailOf(id).lead;
    const run = addRun(id, "draft", "승인된 자료를 근거로 초안을 제출합니다.");
    const m: Message = {
      id: uid("msg"), lead_id: id, run_id: run.id, kind: "initial", subject: `[노바웍스] ${lead.company_name} 상담 자동화 관련 말씀드립니다`,
      body: `안녕하세요, ${lead.name} ${lead.title}님. 노바웍스의 노바데스크 담당자입니다.\n문의 채널이 여러 곳일 때 응대 품질을 맞추기 어려우시다는 이야기를 자주 듣습니다.\n괜찮으시다면 20분 정도 짧은 화상 미팅으로 현재 운영 방식을 먼저 듣고 싶습니다.\n\n---\n이 메일은 AI의 도움을 받아 작성되었고, 노바웍스 담당자가 검토한 뒤 발송했습니다.\n더 이상 연락을 원하지 않으시면 이 메일에 "수신 거부"라고 답장해 주세요. 즉시 발송을 중단합니다.`,
      citations: [{ id: "01_product#한 줄 소개", type: "kb" }], status: "pending_approval", created_at: nowIso(), sent_at: null,
    };
    detailOf(id).messages.unshift(m);
    setStatus(id, "awaiting_approval");
    return out({ processed: 1, results: [] });
  }
  if (method === "POST" && p === "/webhooks/reply") {
    const id = String(b.lead_id);
    const { c, conf } = classify(String(b.body));
    const reply: Reply = { id: uid("reply"), lead_id: id, message_id: null, body: String(b.body), received_at: nowIso(), classification: c, confidence: conf, handled_by: conf >= 0.8 ? "agent" : "human" };
    detailOf(id).replies.unshift(reply);
    const st: LeadStatus = c === "unsubscribe" ? "unsubscribed" : c === "interested" ? "replied" : conf >= 0.8 ? "replied" : "needs_human";
    setStatus(id, st);
    addRun(id, "reply_triage", `답장을 '${c}'로 분류했습니다 (확신도 ${conf}).`);
    return out({ classification: c, confidence: conf, lead_status: st });
  }
  if (method === "POST" && p === "/jobs/handoff") {
    const id = String(b.lead_id);
    const d = detailOf(id);
    const h: Handoff = {
      id: uid("handoff"), lead_id: id, created_at: nowIso(), sent_to: "sales-lead",
      screening_questions: [{ topic: "purpose", label: "도입 목적", question: "어떤 문의 채널의 응대가 가장 큰 문제인가요?" }],
      summary: { lead: { company_name: d.lead.company_name }, handoff_reason: "담당자 요청",
        research: d.facts.map((f) => ({ field: f.field, value: f.value, source_url: f.source_url })),
        selection: { score: d.score?.score ?? null, tags: d.score?.tags ?? [], breakdown: {}, note: "점수는 규칙 기반이며, 영업기회 등록 여부는 담당자가 결정합니다." },
        contact_history: d.messages.map((m) => ({ at: m.sent_at ?? m.created_at, type: "메일", subject: m.subject })), call_insights: [], interests: [], recommended_actions: ["스크리닝 미팅 일정을 잡는다."] },
    };
    d.handoffs.unshift(h);
    setStatus(id, "handed_off");
    addRun(id, "handoff", "인계 요약을 만들었습니다.");
    return out({ handoff_id: h.id });
  }
  throw new ApiError(404, `모의 서버에 없는 요청입니다: ${method} ${p}`);
}
