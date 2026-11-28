import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useQuery } from "../api/hooks";
import { downloadCsv, lineDiff } from "../lib/diff";
import { kstDateTime, relative } from "../lib/format";
import { channelLabel, REASON_CODES, REPLY_CLASS } from "../lib/labels";
import type { Approval, ReplyClass, ReplyRow } from "../types";
import { Badge, Empty, ErrorBox, Loading, StatusBadge } from "./ui";

/** 답장함: 고객이 보낸 답장을 모아 보고, 에이전트가 어떻게 분류했는지 확인한다 */
export function RepliesPanel({ toLead, activeLeadId }: { toLead: (leadId: string) => string; activeLeadId?: string }) {
  const [filter, setFilter] = useState<string>("");
  const q = useQuery(() => api.replies(filter === "human" ? { handled_by: "human" } : filter ? { classification: filter } : {}), [filter], { refreshMs: 30000 });
  const chips: [string, string][] = [["", "전체"], ["human", "사람이 처리"], ...(["interested", "question", "not_now", "not_decision_maker", "unsubscribe", "unclear"] as ReplyClass[]).map((c): [string, string] => [c, REPLY_CLASS[c].label])];
  return (
    <div className="replies">
      <div className="chips">{chips.map(([k, l]) => <button key={k || "all"} className={`chip ${filter === k ? "on" : ""}`} onClick={() => setFilter(k)}>{l}</button>)}</div>
      {q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> :
        (q.data ?? []).length === 0 ? <Empty title="받은 답장이 없습니다">고객이 답장하면 여기에 모입니다.</Empty> :
        <ul className="reply-list">{(q.data as ReplyRow[]).map((r) => (
          <li key={r.id}>
            <Link to={toLead(r.lead_id)} className={`reply-row ${activeLeadId === r.lead_id ? "on" : ""}`}>
              <div className="row-top">
                <strong>{r.lead?.company_name}</strong>
                <span className="reply-badges">
                  <Badge tone={r.classification ? REPLY_CLASS[r.classification].tone : "gray"}>{r.classification ? REPLY_CLASS[r.classification].label : "미분류"}</Badge>
                  {r.handled_by === "human" && <Badge tone="amber">사람 확인</Badge>}
                </span>
              </div>
              <p className="quote">{r.body}</p>
              <div className="muted small">{r.lead?.name} · {relative(r.received_at)} · 확신도 {r.confidence != null ? Math.round(r.confidence * 100) : "-"}%{r.lead && <> · <StatusBadge status={r.lead.status} /></>}</div>
            </Link>
          </li>))}</ul>}
    </div>
  );
}

const DECISION = { approved: { label: "승인", tone: "green" }, edited: { label: "수정 승인", tone: "blue" }, rejected: { label: "반려", tone: "red" } } as const;

function Diff({ a }: { a: Approval }) {
  if (!a.original_body || !a.message) return null;
  const lines = lineDiff(a.original_body, a.message.body);
  return (
    <div className="diff" aria-label="수정 내용">
      {lines.map((l, i) => <div key={i} className={`diff-line ${l.kind}`}><span className="diff-mark">{l.kind === "add" ? "+" : l.kind === "del" ? "-" : " "}</span>{l.text || " "}</div>)}
    </div>
  );
}

/** 승인 이력: 누가 언제 무엇을 승인·수정·반려했는지. 책임 소재와 평가의 근거 */
export function ApprovalHistoryPanel() {
  const q = useQuery(() => api.approvals(), [], { refreshMs: 60000 });
  const [open, setOpen] = useState<string | null>(null);
  const rows = q.data ?? [];
  const exportCsv = () => downloadCsv("approval-history.csv", [
    ["시각", "회사", "담당자(고객)", "메일 제목", "결정", "결정한 사람", "검토 시간(초)", "채널", "사유", "메모"],
    ...rows.map((a) => [a.decided_at, a.lead?.company_name, a.lead?.name, a.message?.subject, DECISION[a.decision].label, a.decided_by, a.review_seconds,
      a.channel, REASON_CODES.find((r) => r.value === a.reason_code)?.label ?? a.reason_code, a.note]),
  ]);
  const secs = rows.map((a) => a.review_seconds).filter((x): x is number => x != null).sort((x, y) => x - y);
  const median = secs.length ? secs[Math.floor(secs.length / 2)] : null;
  const counts = { approved: 0, edited: 0, rejected: 0 };
  rows.forEach((a) => { counts[a.decision] += 1; });

  return (
    <div className="history">
      {q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> : (
        <>
          <div className="stat-row">
            <div className="stat"><b>{rows.length}</b><span>전체 결정</span></div>
            <div className="stat"><b>{counts.approved}</b><span>그대로 승인</span></div>
            <div className="stat"><b>{counts.edited}</b><span>수정 승인</span></div>
            <div className="stat"><b>{counts.rejected}</b><span>반려</span></div>
            <div className="stat"><b>{median != null ? `${median}초` : "-"}</b><span>검토 시간 중앙값</span></div>
            <button className="btn sm" onClick={exportCsv} disabled={!rows.length}>CSV로 내려받기</button>
          </div>
          {rows.length === 0 ? <Empty title="아직 승인 기록이 없습니다" /> : (
            <table className="table">
              <thead><tr><th>시각</th><th>고객</th><th>결정</th><th>결정한 사람</th><th className="num">검토</th><th>사유·메모</th></tr></thead>
              <tbody>{rows.map((a) => (
                <Fragment key={a.id}>
                  <tr className="clickable" onClick={() => setOpen(open === a.id ? null : a.id)}>
                    <td>{kstDateTime(a.decided_at)}</td>
                    <td><strong>{a.lead?.company_name}</strong><div className="muted small">{a.message?.subject}</div></td>
                    <td><Badge tone={DECISION[a.decision].tone}>{DECISION[a.decision].label}</Badge></td>
                    <td>{a.decided_by}<div className="muted small">{channelLabel(a.channel)}</div></td>
                    <td className="num">{a.review_seconds != null ? `${a.review_seconds}초` : "-"}</td>
                    <td>{[REASON_CODES.find((r) => r.value === a.reason_code)?.label, a.note].filter(Boolean).join(": ") || "-"}</td>
                  </tr>
                  {open === a.id && <tr><td colSpan={6}>
                    {a.original_body ? <Diff a={a} /> : <pre className="tl-body">{a.message?.body}</pre>}
                  </td></tr>}
                </Fragment>))}</tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
