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

export interface Citation { id: string; type: "fact" | "kb" | "feedback" | "industry" }

export type SubjectOptionId = "general" | "company" | "hook";
export type SubjectChoice = SubjectOptionId | "custom";
export interface SubjectOption { id: SubjectOptionId; label: string; subject: string }

export type HookType = "company" | "industry" | "inquiry";
export interface Hook { type: HookType; summary: string; source_url: string | null; source_title: string | null }

export interface Revision { at: string; by: string; instruction: string; subject: string; body: string }

export type FeedbackScope = "lead" | "industry" | "global";
export type FeedbackKind = "tone" | "length" | "topic" | "avoid" | "cta" | "other";
export type FeedbackSource = "chat_web" | "chat_slack" | "manual";
export type FeedbackStatus = "active" | "archived";

export interface FeedbackApplied { id: string; scope: FeedbackScope; scope_key: string | null; scope_label: string; kind: FeedbackKind; text: string }

export interface FeedbackNote {
  id: string;
  scope: FeedbackScope;
  scope_key: string | null;
  scope_label: string;
  kind: FeedbackKind;
  text: string;
  source: FeedbackSource;
  created_by: string;
  status: FeedbackStatus;
  applied_count: number;
  created_at: string;
}

export interface FeedbackInput { text: string; scope: FeedbackScope; scope_key?: string | null; kind?: FeedbackKind }
export interface FeedbackPatch { text?: string; scope?: FeedbackScope; scope_key?: string | null; kind?: FeedbackKind; status?: FeedbackStatus }
export interface Industry { key: string; label: string }

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
  // 소식 기반 초안과 피드백 메모리 (구버전 서버에는 없다)
  subject_options?: SubjectOption[];
  subject_choice?: SubjectChoice;
  hook?: Hook | null;
  revisions?: Revision[];
  feedback_applied?: FeedbackApplied[];
}

export type ChatActionType = "message_updated" | "subject_changed" | "message_sent" | "message_rejected" | "feedback_saved" | "feedback_deleted";
export interface ChatAction { type: ChatActionType; label: string; message_id?: string; lead_id?: string; feedback_id?: string }
export interface ChatContext { screen?: string; message_id?: string; lead_id?: string }
export interface ChatTurn { role: "user" | "assistant"; text: string; actions: ChatAction[]; created_at: string }
export interface ChatReply { session_id: string; reply: string; actions: ChatAction[] }
export interface ChatSession { session_id: string; turns: ChatTurn[] }

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
  chat?: { enabled: boolean; slack_events: boolean };
  memory?: { active_notes: number };
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
  subject_option?: SubjectOptionId;
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
