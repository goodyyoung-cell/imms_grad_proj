import { useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction, useQuery } from "../api/hooks";
import { kstDateTime } from "../lib/format";
import { nextAction } from "../lib/nextAction";
import { MESSAGE_STATUS, OUTCOME, REPLY_CLASS, SOURCE } from "../lib/labels";
import { useToast } from "../toast";
import type { Handoff, LeadDetail } from "../types";
import { ApprovalPanel } from "./ApprovalPanel";
import { CallPanel } from "./CallPanel";
import { DemoTools } from "./DemoTools";
import { FactList, ScoreBreakdown } from "./Evidence";
import { LeadNotes } from "./MemoryPanel";
import { RunList } from "./RunTrace";
import { AiMark, Badge, Card, ConfirmButton, ErrorBox, Loading, Score, StatusBadge } from "./ui";

export function HandoffView({ h }: { h: Handoff }) {
  const s = h.summary;
  return (
    <div className="handoff">
      <p><AiMark>AI 요약</AiMark> <span className="muted">{kstDateTime(h.created_at)} · 인계 사유: {s.handoff_reason}</span></p>
      {s.interests?.length > 0 && <><h5>관심사</h5><ul>{s.interests.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
      <h5>접촉 이력</h5>
      <ul>{s.contact_history?.map((e, i) => <li key={i}>{kstDateTime(e.at)} · {e.type} {e.subject ?? e.classification ?? e.outcome ?? ""}</li>)}</ul>
      {s.call_insights && s.call_insights.length > 0 && (
        <>
          <h5>전화 팔로업</h5>
          <ul>{s.call_insights.map((c, i) => <li key={i}>{c.outcome} · 희망 미팅 시기 {c.meeting_preference ?? "-"} · {c.compelling_reason ?? "-"}</li>)}</ul>
        </>
      )}
      <h5>권장 후속 조치</h5>
      <ul>{s.recommended_actions?.map((x, i) => <li key={i}>{x}</li>)}</ul>
      <h5>스크리닝에서 확인할 질문</h5>
      <ul>{h.screening_questions?.map((q) => <li key={q.topic}><strong>{q.label}</strong> {q.question}</li>)}</ul>
      <p className="muted small">{s.selection?.note}</p>
    </div>
  );
}

type Tab = "overview" | "timeline" | "calls" | "handoff" | "runs";

function timeline(d: LeadDetail) {
  const items: { at: string; kind: string; node: React.ReactNode }[] = [];
  d.messages.forEach((m) => items.push({ at: m.sent_at ?? m.created_at, kind: "메일",
    node: <><Badge tone={MESSAGE_STATUS[m.status].tone}>{MESSAGE_STATUS[m.status].label}</Badge> <strong>{m.subject}</strong><pre className="tl-body">{m.body}</pre></> }));
  d.replies.forEach((r) => items.push({ at: r.received_at, kind: "답장",
    node: <><Badge tone={r.classification ? REPLY_CLASS[r.classification].tone : "gray"}>{r.classification ? REPLY_CLASS[r.classification].label : "미분류"}</Badge>
      <span className="muted small"> 확신도 {r.confidence != null ? Math.round(r.confidence * 100) : "-"}% · {r.handled_by === "human" ? "사람이 처리" : "에이전트가 처리"}</span><p className="quote">{r.body}</p></> }));
  d.calls.filter((c) => c.status === "done").forEach((c) => items.push({ at: c.called_at ?? c.created_at as string, kind: "전화",
    node: <><Badge tone={c.outcome ? OUTCOME[c.outcome].tone : "gray"}>{c.outcome ? OUTCOME[c.outcome].label : "-"}</Badge> <span className="muted small">{c.called_by}</span>{c.compelling_reason && <p>{c.compelling_reason}</p>}</> }));
  d.handoffs.forEach((h) => items.push({ at: h.created_at, kind: "인계", node: <Badge tone="violet">인계 요약 생성</Badge> }));
  return items.sort((a, b) => b.at.localeCompare(a.at));
}

/** 리드 한 건의 전체 이력. 화면 구성(왼쪽 목록, 서랍 등)과 상관없이 같은 내용을 보여 준다. */
export function LeadDetailView({ leadId }: { leadId: string }) {
  const toast = useToast();
  const q = useQuery(() => api.lead(leadId), [leadId]);
  const [tab, setTab] = useState<Tab>("overview");
  const unsub = useAction(async () => { await api.confirmUnsubscribe(leadId, "담당자 확정"); toast("수신 거부를 확정했습니다. 남은 메일과 전화를 취소했습니다."); invalidate(); });
  const handoff = useAction(async () => { await api.handoff(leadId); toast("인계 요약을 만들었습니다."); invalidate(); });

  if (q.loading && !q.data) return <Loading />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.reload} />;
  const d = q.data!;
  const l = d.lead;
  const pendingMsgs = d.messages.filter((m) => m.status === "pending_approval").map((m) => ({ ...m, lead: l }));
  const pendingCalls = d.calls.filter((c) => c.status === "pending").map((c) => ({ ...c, lead: l }));
  const tabs: [Tab, string, number?][] = [["overview", "개요"], ["timeline", "이력", d.messages.length + d.replies.length],
    ["calls", "전화", d.calls.length], ["handoff", "인계", d.handoffs.length], ["runs", "에이전트 기록", d.runs.length]];

  return (
    <div className="lead-detail">
      <header className="lead-head">
        <div>
          <h2>{l.company_name}</h2>
          <p className="muted">{l.name} · {l.title} · {l.email}{l.phone ? ` · ${l.phone}` : ""}</p>
          <p className="muted small">{SOURCE[l.source] ?? l.source} · 유입 {kstDateTime(l.inbound_at)}{l.notes ? ` · ${l.notes}` : ""}</p>
        </div>
        <div className="lead-head-side"><StatusBadge status={l.status} /><Score value={d.score?.score} tags={d.score?.tags} /></div>
      </header>

      {(() => { const na = nextAction(d); return na.text ? <div className={`next-action ${na.tone}`}><strong>다음 할 일</strong> {na.text}</div> : null; })()}

      {l.status === "needs_human" && (
        <div className="notice warn">
          <strong>사람 확인이 필요합니다.</strong> 답장과 이력을 보고 아래에서 처리해 주세요.
          <div className="decision-actions">
            <ConfirmButton label="수신 거부 확정" confirmLabel="확정하고 연락 중단" onConfirm={() => unsub.run()} disabled={unsub.busy} note="남은 메일·전화가 모두 취소됩니다." />
            <ConfirmButton label="인계 요약 만들기" confirmLabel="만들기" onConfirm={() => handoff.run()} disabled={handoff.busy} className="btn" />
          </div>
          {(unsub.error || handoff.error) && <p className="notice err">{unsub.error || handoff.error}</p>}
        </div>
      )}

      {pendingMsgs.map((m) => <Card key={m.id} title="승인을 기다리는 메일"><ApprovalPanel message={m} /></Card>)}
      {pendingCalls.map((c) => <Card key={c.id} title="걸어야 할 전화"><CallPanel call={c} /></Card>)}

      <nav className="tabs" role="tablist">
        {tabs.map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}{n ? <span className="count">{n}</span> : null}</button>
        ))}
      </nav>

      {tab === "overview" && (
        <>
          <div className="two-col">
            <Card title="조사한 사실 (출처 포함)"><FactList facts={d.facts} /></Card>
            <Card title="선별 근거"><ScoreBreakdown score={d.score} /></Card>
          </div>
          <LeadNotes leadId={l.id} company={l.company_name} />
        </>
      )}
      {tab === "timeline" && (
        <ol className="timeline">
          {timeline(d).map((it, i) => <li key={i}><span className="tl-meta">{kstDateTime(it.at)} · {it.kind}</span>{it.node}</li>)}
          {timeline(d).length === 0 && <li className="muted">아직 접촉 이력이 없습니다.</li>}
        </ol>
      )}
      {tab === "calls" && (
        d.calls.filter((c) => c.status !== "pending").length === 0
          ? <p className="muted">{pendingCalls.length ? "예정된 전화는 위에 있습니다. 기록된 통화는 아직 없습니다." : "메일을 승인해 발송하면 1시간 뒤 전화 과제가 생깁니다."}</p>
          : d.calls.filter((c) => c.status !== "pending").map((c) => <Card key={c.id} title="완료된 전화"><CallPanel call={{ ...c, lead: l }} /></Card>)
      )}
      {tab === "handoff" && (d.handoffs.length === 0 ? <p className="muted">인계된 이력이 없습니다.</p> : d.handoffs.map((h) => <Card key={h.id}><HandoffView h={h} /></Card>))}
      {tab === "runs" && <RunList runs={d.runs} showLead={false} />}

      <DemoTools detail={d} />
    </div>
  );
}
