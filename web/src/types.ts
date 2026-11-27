// 백엔드 응답 형태. 기준: docs/frontend-handoff.md 와 실제 응답 (sdr_agent/app.py)

export type LeadStatus =
  | "new" | "researching" | "researched" | "awaiting_approval" | "contacted"
  | "replied" | "needs_human" | "handed_off" | "unsubscribed";

export type MessageStatus = "pending_approval" | "approved" | "sent" | "rejected" | "cancelled";
export type Workflow = "research" | "draft" | "reply_triage" | "call_script" | "handoff";
export type Outcome = "interested" | "not_now" | "needs_info" | "no_answer" | "voicemail" | "wrong_contact" | "unsubscribe";
export type ReplyClass = "interested" | "question" | "not_now" | "not_decision_maker" | "out_of_office" | "unsubscribe" | "unclear";

export interface LeadBrief {
  id: string;
  name: string | null;
  company_name: string;
  title: string | null;
  status: LeadStatus;
  email?: string | null;
  phone?: string | null;
}

export interface Lead extends LeadBrief {
  company_domain?: string | null;
  source: string;
  intake_channel?: string;
  inbound_at: string | null;
  updated_at: string;
  created_at?: string;
  notes?: string | null;
  unsubscribed_at?: string | null;
  consent_at?: string | null;
  score?: number | null;
  tags?: string[];
}

export interface Citation { id: string; type: "fact" | "kb" }

export interface Message {
  id: string;
  lead_id: string;
  run_id?: string | null;
  kind: string; // initial | reply 등
  subject: string;
  body: string;
  citations: Citation[];
  status: MessageStatus;
  provider_message_id?: string | null;
  created_at: string;
  sent_at: string | null;
  lead?: LeadBrief | null;
}

export interface Fact {
  id: string;
  lead_id: string;
  field: string;
  value: string;
  source_type: string;
  source_url: string | null;
  retrieved_at: string;
}

export interface Score {
  score: number;
  tags: string[];
  breakdown: Record<string, number>;
  created_at?: string;
}

export interface TalkingPoint { text: string; fact_ids?: string[] }
export interface CallScript {
  phone?: string;
  notices: { kind: "reply" | "handoff" | "unsubscribe_suspected"; text: string; at?: string }[];
  opening: string;
  email_summary: string;
  talking_points: TalkingPoint[];
  compelling_questions: { angle: string; question: string; listen_for: string }[];
  objections: { objection: string; response: string; kb_ids?: string[] }[];
  meeting_ask: string;
  if_not_received: string;
  voicemail: string;
  compliance: { stop: string; recording: string; permission: string; no_promises: string };
  record_fields: string[];
  outcomes: Outcome[];
  citations: Citation[];
}

export interface CallTask {
  id: string;
  lead_id: string;
  message_id?: string | null;
  run_id?: string | null;
  script: CallScript;
  script_text: string;
  status: "pending" | "done" | "cancelled";
  created_at?: string;
  due_at: string;
  outcome: Outcome | null;
  outcome_notes: string | null;
  called_by: string | null;
  called_at: string | null;
  email_received: boolean | null;
  meeting_preference: string | null;
  compelling_reason: string | null;
  lead?: LeadBrief | null;
}

export interface Reply {
  id: string;
  lead_id: string;
  message_id?: string | null;
  body: string;
  received_at: string;
  classification: ReplyClass | null;
  confidence: number | null;
  handled_by: "agent" | "human" | null;
}

export interface ReplyRow extends Reply { run_id?: string | null; lead?: LeadBrief | null }

export interface Approval {
  id: string;
  message_id: string;
  decision: "approved" | "edited" | "rejected";
  decided_by: string;
  decided_at: string;
  note: string | null;
  reason_code: string | null;
  original_body: string | null;
  review_seconds: number | null;
  channel: string | null;
  message: { id: string; lead_id: string; kind: string; subject: string; body: string; status: MessageStatus } | null;
  lead: LeadBrief | null;
}

export interface KbDoc { id: string; title: string; sections: { id: string; heading: string; text: string }[] }

export interface SystemStatus {
  mode: { send: string; llm_default: string; providers: Record<string, boolean> };
  slack: { configured: boolean; signing_configured: boolean; channel: string | null; approvers: number; notify_kinds: string[]; web_url: string | null };
  safeguards: { require_consent: boolean; max_sends_per_day: number; sent_today: number; sender_name: string | null; sender_contact: string | null };
  knowledge_docs: number;
}

export interface HandoffSummary {
  lead: Record<string, unknown>;
  handoff_reason: string;
  research: { field: string; value: string; source_url: string | null }[];
  selection: { score: number | null; tags: string[]; breakdown: Record<string, number>; note: string };
  contact_history: { at?: string; type: string; subject?: string; classification?: string; outcome?: string }[];
  call_insights?: { called_at?: string; outcome?: string; email_received?: boolean | null; meeting_preference?: string | null; compelling_reason?: string | null }[];
  interests: string[];
  recommended_actions: string[];
}

export interface Handoff {
  id: string;
  lead_id: string;
  summary: HandoffSummary;
  screening_questions: { topic: string; label: string; question: string }[];
  sent_to?: string | null;
  created_at: string;
}

export interface Run {
  id: string;
  lead_id: string | null;
  workflow: Workflow;
  trigger: string;
  model: string;
  status: "running" | "succeeded" | "failed" | string;
  stop_reason?: string | null;
  final_text?: string | null;
  error?: string | null;
  started_at: string;
  finished_at: string | null;
  lead?: LeadBrief | null;
}

export interface RunStep {
  id?: number;
  step_no: number;
  kind: "llm" | "tool";
  tool_name: string | null;
  text: string | null;
  input: unknown;
  output: unknown;
  is_error: boolean;
  duration_ms: number | null;
}
export interface RunDetail extends Run { steps: RunStep[] }

export interface Summary {
  lead_status_counts: Partial<Record<LeadStatus, number>>;
  pending_approvals: number;
  calls_due: number;
  calls_pending: number;
  recent_runs: Run[];
}

export interface LeadDetail {
  lead: Lead;
  merged_leads: Lead[];
  facts: Fact[];
  score: Score | null;
  messages: Message[];
  replies: Reply[];
  calls: CallTask[];
  handoffs: Handoff[];
  runs: Run[];
}

export interface LeadList { total: number; leads: Lead[] }

export interface DecisionInput {
  decision: "approved" | "edited" | "rejected";
  edited_body?: string;
  note?: string;
  reason_code?: string;
  review_seconds?: number;
}

export interface CallOutcomeInput {
  outcome: Outcome;
  email_received?: boolean | null;
  meeting_preference?: string;
  compelling_reason?: string;
  notes?: string;
}

export interface ImportResult {
  dry_run: boolean;
  created: number; merged: number; skipped: number; errors: number;
  details: { created: unknown[]; merged: unknown[]; skipped: unknown[]; errors: { row?: number; error?: string }[] };
  columns?: Record<string, string>;
  ignored_columns?: string[];
}
