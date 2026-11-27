import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction, useQuery } from "../api/hooks";
import { complianceChecks } from "../lib/compliance";
import { kstDateTime } from "../lib/format";
import { MESSAGE_STATUS, REASON_CODES } from "../lib/labels";
import { useToast } from "../toast";
import type { Message } from "../types";
import { Evidence, ScoreBreakdown } from "./Evidence";
import { AiMark, Badge, ErrorBox, Loading, Score } from "./ui";

interface Props {
  message: Message;
  onDecided?: (result: "approved" | "edited" | "rejected") => void;
  shortcuts?: boolean;
}

/** 메일 한 통을 검토하고 승인·수정 승인·반려하는 패널. 발송은 이 버튼으로만 일어난다. */
export function ApprovalPanel({ message, onDecided, shortcuts = false }: Props) {
  const toast = useToast();
  const detail = useQuery(() => api.lead(message.lead_id), [message.lead_id]);
  const kb = useQuery(() => api.kb(), []);
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(message.body);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const shownAt = useRef(Date.now());

  useEffect(() => {
    setEditing(false); setBody(message.body); setRejecting(false); setReason(""); setNote("");
    shownAt.current = Date.now();
  }, [message.id, message.body]);

  const pending = message.status === "pending_approval";
  const changed = body.trim() !== message.body.trim();
  const seconds = () => Math.round((Date.now() - shownAt.current) / 1000);

  const decide = useAction(async (decision: "approved" | "edited" | "rejected") => {
    await api.decide(message.id, {
      decision,
      edited_body: decision === "edited" ? body : undefined,
      note: [decision === "rejected" ? REASON_CODES.find((r) => r.value === reason)?.label : null, note.trim() || null].filter(Boolean).join(": ") || undefined,
      reason_code: decision === "rejected" ? reason || undefined : undefined,
      review_seconds: seconds(),
    });
    toast(decision === "rejected" ? "반려했습니다. 담당자 확인 목록으로 옮겼습니다." : "승인했습니다. 샌드박스로 발송했고 1시간 뒤 전화 과제가 생깁니다.");
    invalidate();
    onDecided?.(decision);
  });

  const approve = () => decide.run(changed ? "edited" : "approved");

  useEffect(() => {
    if (!shortcuts || !pending) return;
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (/input|textarea|select/i.test(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "a" && !editing && !rejecting) { e.preventDefault(); approve(); }
      if (e.key === "e") { e.preventDefault(); setEditing(true); }
      if (e.key === "r") { e.preventDefault(); setRejecting(true); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const lead = message.lead;
  const d = detail.data;
  const st = MESSAGE_STATUS[message.status];
  const kbReady = !!kb.data || !!kb.error || message.citations.every((c) => c.type !== "kb");
  const support = useMemo(() => {
    if (!d || !kbReady) return null;
    const ids = new Set(message.citations.map((c) => c.id));
    const facts = d.facts.filter((f) => ids.has(f.id)).map((f) => f.value);
    const all = (kb.data ?? []).flatMap((x) => x.sections);
    const kbText = all.filter((s) => ids.has(s.id)).map((s) => s.heading + " " + s.text);
    const missing = message.citations.some((c) => c.type === "kb" && !all.some((s) => s.id === c.id));
    if (missing && !kb.error) return null;
    return kb.error ? null : [...facts, ...kbText, lead?.company_name ?? "", lead?.name ?? ""];
  }, [d, kb.data, kb.error, kbReady, message.citations, lead]);
  const checks = complianceChecks(body, support);

  return (
    <div className="approval">
      <header className="approval-head">
        <div>
          <h2>{lead?.company_name} <span className="muted">· {lead?.name} {lead?.title}</span></h2>
          <p className="muted">받는 사람 {lead?.email} · 작성 {kstDateTime(message.created_at)}</p>
        </div>
        <div className="approval-meta">
          <Score value={d?.score?.score} showTags={false} />
          <AiMark />
          {!pending && <Badge tone={st.tone}>{st.label}</Badge>}
        </div>
      </header>

      <div className="approval-grid">
        <div className="approval-mail">
          <div className="mail">
            <div className="mail-subject">{message.subject}</div>
            {editing ? (
              <textarea className="mail-edit" value={body} rows={16} onChange={(e) => setBody(e.target.value)} aria-label="메일 본문 수정" />
            ) : (
              <div className="mail-body">{body}</div>
            )}
          </div>
          {changed && <p className="notice warn">본문을 고쳤습니다. 승인하면 수정본이 발송되고 원문과 함께 기록됩니다.</p>}
        </div>

        <aside className="approval-side">
          {detail.loading && !d && <Loading text="근거를 불러오는 중" />}
          {detail.error && <ErrorBox message={detail.error} onRetry={detail.reload} />}
          {d && (
            <>
              <Evidence citations={message.citations} facts={d.facts} kbDocs={kb.data} />
              <h4>발송 전 점검</h4>
              <ul className="checks">
                {checks.map((c) => (
                  <li key={c.key} className={`check ${c.state}`}>
                    <span className="check-mark" aria-hidden>{c.state === "ok" ? "✓" : c.state === "warn" ? "!" : "·"}</span>
                    <span>{c.label}{c.detail && <span className="muted small"> {c.detail}</span>}</span>
                  </li>
                ))}
              </ul>
              <p className="muted small">자동 점검은 참고용이며, 최종 판단은 담당자가 합니다.</p>
              <h4>선별 근거</h4>
              <ScoreBreakdown score={d.score} />
            </>
          )}
        </aside>
      </div>

      {pending ? (
        <footer className="decision-bar">
          {decide.error && <p className="notice err" role="alert">{decide.error}</p>}
          {rejecting ? (
            <div className="reject-box">
              <label>반려 사유
                <select value={reason} onChange={(e) => setReason(e.target.value)}>
                  <option value="">선택</option>
                  {REASON_CODES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
              </label>
              <label>메모 <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="무엇을 고치면 좋을까요" /></label>
              <button className="btn danger" disabled={decide.busy || !reason} onClick={() => decide.run("rejected")}>반려 확정</button>
              <button className="btn ghost" onClick={() => setRejecting(false)}>취소</button>
            </div>
          ) : (
            <div className="decision-actions">
              <button className="btn primary" disabled={decide.busy || (editing && !body.trim())} onClick={approve}>
                {decide.busy ? "처리 중" : changed ? "수정본으로 승인·발송" : "승인·발송"}
                {shortcuts && <kbd>A</kbd>}
              </button>
              <button className="btn" onClick={() => setEditing(!editing)}>{editing ? "수정 닫기" : "수정"}{shortcuts && <kbd>E</kbd>}</button>
              <button className="btn" onClick={() => setRejecting(true)}>반려{shortcuts && <kbd>R</kbd>}</button>
              <span className="muted hint">발송은 샌드박스 계정으로만 나갑니다. 승인하면 1시간 뒤 걸 전화 과제가 생깁니다.</span>
            </div>
          )}
        </footer>
      ) : (
        <footer className="decision-bar"><p className="muted">이미 처리된 메일입니다 ({st.label}).</p></footer>
      )}
    </div>
  );
}
