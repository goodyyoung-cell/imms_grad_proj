import { useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction } from "../api/hooks";
import { dueLabel, kstDateTime } from "../lib/format";
import { NOTICE, OUTCOME } from "../lib/labels";
import { useToast } from "../toast";
import type { CallTask, Outcome } from "../types";
import { AiMark, Badge, Collapsible, ConfirmButton, CopyButton } from "./ui";

/** 전화 한 건: 스크립트를 읽고, 끝나면 결과를 기록한다. 전화는 담당자가 직접 건다. */
export function CallPanel({ call, onDone }: { call: CallTask; onDone?: () => void }) {
  const toast = useToast();
  const s = call.script;
  const lead = call.lead;
  const due = dueLabel(call.due_at);
  const pending = call.status === "pending";

  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [emailReceived, setEmailReceived] = useState<boolean | null>(null);
  const [meeting, setMeeting] = useState("");
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");

  const save = useAction(async () => {
    if (!outcome) return;
    await api.callOutcome(call.id, {
      outcome, email_received: emailReceived, meeting_preference: meeting.trim() || undefined,
      compelling_reason: reason.trim() || undefined, notes: notes.trim() || undefined,
    });
    toast(`통화 결과를 기록했습니다. ${OUTCOME[outcome].next}.`);
    invalidate();
    onDone?.();
  });

  return (
    <div className="call">
      <header className="approval-head">
        <div>
          <h2>{lead?.company_name} <span className="muted">· {lead?.name} {lead?.title}</span></h2>
          <p className="muted">전화 {lead?.phone ?? s.phone ?? "-"} · <span className={due.overdue ? "due over" : "due"}>{due.text}</span></p>
        </div>
        <div className="approval-meta">
          <AiMark>AI 스크립트</AiMark>
          {!pending && <Badge tone="green">기록 완료</Badge>}
        </div>
      </header>

      {s.notices?.map((n, i) => (
        <div key={i} className={`notice ${n.kind === "unsubscribe_suspected" ? "err" : "warn"}`} role="alert">
          <strong>{NOTICE[n.kind] ?? n.kind}</strong> {n.text}
        </div>
      ))}

      <div className="script">
        <section>
          <h4>시작 전</h4>
          <p>{s.compliance?.permission}</p>
          <p className="muted small">{s.compliance?.recording}</p>
        </section>
        <section>
          <h4>첫 마디</h4>
          <p className="quote">{s.opening}</p>
        </section>
        <section>
          <h4>보낸 메일 요약</h4>
          <p>{s.email_summary}</p>
          <p className="muted small">메일을 못 받았다고 하면: {s.if_not_received}</p>
        </section>
        <section>
          <h4>이야기할 것</h4>
          <ul>{s.talking_points?.map((t, i) => <li key={i}>{t.text}</li>)}</ul>
        </section>
        <section>
          <h4>계기를 묻는 질문 (Compelling Reason)</h4>
          <ul className="qa">
            {s.compelling_questions?.map((q, i) => (
              <li key={i}><Badge>{q.angle}</Badge> {q.question}<span className="listen">들을 것: {q.listen_for}</span></li>
            ))}
          </ul>
        </section>
        <section>
          <h4>미팅 제안</h4>
          <p className="quote">{s.meeting_ask}</p>
        </section>
        <Collapsible title="반대 의견이 나오면" badge={<span className="count">{s.objections?.length ?? 0}</span>}>
          <ul className="qa">{s.objections?.map((o, i) => <li key={i}><strong>{o.objection}</strong><span className="listen">{o.response}</span></li>)}</ul>
        </Collapsible>
        <Collapsible title="부재중이면 (음성 사서함)"><p>{s.voicemail}</p></Collapsible>
        <Collapsible title="지켜야 할 것">
          <ul>
            <li>{s.compliance?.stop}</li>
            <li>{s.compliance?.no_promises}</li>
          </ul>
        </Collapsible>
        <div className="row-end"><CopyButton text={call.script_text} label="스크립트 전체 복사" /></div>
      </div>

      {pending ? (
        <form className="outcome" onSubmit={(e) => { e.preventDefault(); save.run(); }}>
          <h4>통화 결과 기록</h4>
          <div className="outcome-grid" role="radiogroup" aria-label="통화 결과">
            {(Object.keys(OUTCOME) as Outcome[]).filter((o) => !s.outcomes || s.outcomes.includes(o)).map((o) => (
              <button type="button" key={o} role="radio" aria-checked={outcome === o}
                className={`outcome-pill ${OUTCOME[o].tone} ${outcome === o ? "on" : ""}`} onClick={() => setOutcome(o)}>
                {OUTCOME[o].label}
              </button>
            ))}
          </div>
          {outcome && <p className="muted small">기록하면: {OUTCOME[outcome].next}</p>}
          {outcome && outcome !== "no_answer" && outcome !== "voicemail" && (
            <div className="form-grid">
              <fieldset>
                <legend>고객이 메일을 받았다고 했나요</legend>
                {[[true, "받았다"], [false, "못 받았다"], [null, "확인 안 함"]].map(([v, l]) => (
                  <label key={String(l)} className="inline"><input type="radio" name={`er-${call.id}`} checked={emailReceived === v} onChange={() => setEmailReceived(v as boolean | null)} />{l as string}</label>
                ))}
              </fieldset>
              <label>희망 미팅 시기<input value={meeting} onChange={(e) => setMeeting(e.target.value)} placeholder="예: 다음 주 화요일 오후" /></label>
              <label className="wide">왜 지금 바꾸려 하나요 (Compelling Reason)
                <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="고객이 한 말을 그대로 적어 주세요" />
              </label>
              <label className="wide">메모<textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
            </div>
          )}
          {save.error && <p className="notice err" role="alert">{save.error}</p>}
          <div className="decision-actions">
            {outcome === "unsubscribe"
              ? <ConfirmButton className="btn primary" label="기록" confirmLabel="연락 거부로 기록하고 중단" onConfirm={() => save.run()} note="이 고객의 모든 연락이 멈춥니다." />
              : <button className="btn primary" type="submit" disabled={!outcome || save.busy}>{save.busy ? "기록 중" : "결과 기록"}</button>}
          </div>
        </form>
      ) : (
        <div className="outcome done">
          <h4>기록된 결과</h4>
          <p><Badge tone={call.outcome ? OUTCOME[call.outcome].tone : "gray"}>{call.outcome ? OUTCOME[call.outcome].label : "-"}</Badge> {call.called_by} · {kstDateTime(call.called_at)}</p>
          {call.compelling_reason && <p>Compelling Reason: {call.compelling_reason}</p>}
          {call.meeting_preference && <p>희망 미팅 시기: {call.meeting_preference}</p>}
          {call.outcome_notes && <p>메모: {call.outcome_notes}</p>}
        </div>
      )}
    </div>
  );
}

