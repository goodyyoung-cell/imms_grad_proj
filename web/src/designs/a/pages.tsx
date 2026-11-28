import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { ApprovalPanel } from "../../components/ApprovalPanel";
import { CallPanel } from "../../components/CallPanel";
import { ImportPanel } from "../../components/ImportPanel";
import { MetricsPanel } from "../../components/MetricsPanel";
import { ApprovalHistoryPanel, RepliesPanel } from "../../components/HistoryPanels";
import { KbPanel, StatusPanel } from "../../components/SettingsPanels";
import { LeadDetailView } from "../../components/LeadDetailView";
import { MemoryPanel } from "../../components/MemoryPanel";
import { RunList } from "../../components/RunTrace";
import { Badge, Card, Empty, ErrorBox, Loading, Score, StatusBadge } from "../../components/ui";
import { useAuth } from "../../auth";
import { dueLabel, kstDate, relative } from "../../lib/format";
import { LEAD_STATUS, PIPELINE, SOURCE, WORKFLOW } from "../../lib/labels";
import type { LeadStatus } from "../../types";
import { SplitView, useListKeys } from "./SplitView";

/** 아침에 가장 먼저 보는 화면: 오늘 사람이 할 일과 밤사이 에이전트가 한 일 */
export function Briefing() {
  const { name } = useAuth();
  const s = useQuery(() => api.summary(), [], { refreshMs: 30000 });
  if (s.loading && !s.data) return <Loading />;
  if (s.error) return <ErrorBox message={s.error} onRetry={s.reload} />;
  const d = s.data!;
  const counts = d.lead_status_counts;
  const todo = [
    { to: "/a/approvals", n: d.pending_approvals, title: "승인할 메일", sub: "에이전트가 쓴 초안입니다. 근거를 보고 승인하세요.", tone: "amber" },
    { to: "/a/calls", n: d.calls_due, title: "지금 걸 전화", sub: `예정된 전화 전체 ${d.calls_pending}건`, tone: "blue" },
    { to: "/a/exceptions", n: counts.needs_human ?? 0, title: "사람이 확인할 고객", sub: "답장이 애매하거나 반려된 고객입니다.", tone: "red" },
  ];
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
  return (
    <div className="page briefing">
      <header className="page-head">
        <h1>{name ? `${name}님, ` : ""}오늘 할 일</h1>
        <p className="muted">{kstDate(new Date().toISOString())} · 에이전트가 조사와 초안을 준비했고, 고객에게 나가는 일은 모두 사람의 승인을 거칩니다.</p>
      </header>

      <div className="todo-cards">
        {todo.map((t) => (
          <Link key={t.to} to={t.to} className={`todo-card ${t.tone} ${t.n === 0 ? "zero" : ""}`}>
            <span className="todo-n">{t.n}</span>
            <span className="todo-title">{t.title}</span>
            <span className="todo-sub">{t.n === 0 ? "지금은 할 일이 없습니다" : t.sub}</span>
          </Link>
        ))}
      </div>

      <div className="two-col">
        <Card title="리드 현황" aside={<span className="muted">전체 {total}건</span>}>
          <ul className="funnel">
            {PIPELINE.map(({ status, actor }) => (
              <li key={status}>
                <span className={`actor ${actor}`} title={actor === "agent" ? "에이전트가 하는 단계" : "사람이 확인하는 단계"} />
                <span>{LEAD_STATUS[status].label}</span>
                <b>{counts[status] ?? 0}</b>
              </li>
            ))}
          </ul>
          <p className="muted small"><span className="actor agent inline" /> 에이전트 단계 <span className="actor human inline" /> 사람이 확인하는 단계</p>
        </Card>
        <Card title="최근 에이전트 활동" aside={<Link to="/a/activity">전체 보기</Link>}>
          <RunList runs={d.recent_runs.slice(0, 5)} />
        </Card>
      </div>
    </div>
  );
}

export function ApprovalsPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const msgs = useQuery(() => api.messages("pending_approval"), [], { refreshMs: 30000 });
  const leads = useQuery(() => api.leads({ status: "awaiting_approval", limit: 200 }), []);

  const items = useMemo(() => {
    const score = new Map((leads.data?.leads ?? []).map((l) => [l.id, l]));
    return (msgs.data ?? [])
      .map((m) => ({ m, lead: score.get(m.lead_id) }))
      .sort((a, b) => (b.lead?.score ?? 0) - (a.lead?.score ?? 0) || b.m.created_at.localeCompare(a.m.created_at));
  }, [msgs.data, leads.data]);
  const ids = items.map((i) => i.m.id);
  const go = useCallback((x: string) => nav(`/a/approvals/${x}`), [nav]);
  useListKeys(ids, id, go);
  useEffect(() => { if (!id && ids.length && window.innerWidth > 900) nav(`/a/approvals/${ids[0]}`, { replace: true }); }, [id, ids.join(",")]); // eslint-disable-line
  const selected = items.find((i) => i.m.id === id)?.m;

  const next = (cur: string) => { const i = ids.indexOf(cur); const n = ids[i + 1] ?? ids[i - 1]; nav(n ? `/a/approvals/${n}` : "/a/approvals"); };

  return (
    <SplitView
      hasDetail={!!id}
      listTitle={<><h2>승인 대기 <span className="count">{items.length}</span></h2><span className="muted small">점수 높은 순 · J/K 이동</span></>}
      list={
        msgs.loading && !msgs.data ? <Loading /> : msgs.error ? <ErrorBox message={msgs.error} onRetry={msgs.reload} /> :
        items.length === 0 ? <Empty title="승인할 메일이 없습니다">에이전트가 새 초안을 만들면 여기에 나타납니다.</Empty> :
        <ul className="rows">
          {items.map(({ m, lead }) => (
            <li key={m.id}>
              <Link to={`/a/approvals/${m.id}`} className={`row ${m.id === id ? "on" : ""}`}>
                <div className="row-top"><strong>{m.lead?.company_name}</strong><Score value={lead?.score} showTags={false} /></div>
                <div className="row-mid">{m.lead?.name} · {m.lead?.title}</div>
                <div className="row-sub">{m.subject}</div>
                <div className="row-tags">{lead?.tags?.slice(0, 3).map((t) => <span key={t} className="tag">{t}</span>)}</div>
              </Link>
            </li>
          ))}
        </ul>
      }
      detail={selected
        ? <ApprovalPanel key={selected.id} message={selected} shortcuts onDecided={() => next(selected.id)} />
        : <div className="split-empty"><Empty title={items.length ? "왼쪽에서 메일을 고르세요" : "오늘 승인할 메일을 모두 처리했습니다"}>{items.length ? "첫 번째 메일부터 보려면 J 키를 누르세요." : undefined}</Empty></div>}
    />
  );
}

export function CallsPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [all, setAll] = useState(false);
  const calls = useQuery(() => api.calls(!all), [all], { refreshMs: 30000 });
  const items = calls.data ?? [];
  const ids = items.map((c) => c.id);
  const go = useCallback((x: string) => nav(`/a/calls/${x}`), [nav]);
  useListKeys(ids, id, go);
  useEffect(() => { if (!id && ids.length && window.innerWidth > 900) nav(`/a/calls/${ids[0]}`, { replace: true }); }, [id, ids.join(",")]); // eslint-disable-line
  const selected = items.find((c) => c.id === id);

  const next = (cur: string) => { const i = ids.indexOf(cur); const n = ids[i + 1] ?? ids[i - 1]; nav(n ? `/a/calls/${n}` : "/a/calls"); };

  return (
    <SplitView
      hasDetail={!!id}
      listTitle={<><h2>전화 <span className="count">{items.length}</span></h2>
        <label className="inline small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> 예정된 것까지 보기</label></>}
      list={
        calls.loading && !calls.data ? <Loading /> : calls.error ? <ErrorBox message={calls.error} onRetry={calls.reload} /> :
        items.length === 0 ? <Empty title="지금 걸 전화가 없습니다">메일을 승인하면 1시간 뒤 전화 과제가 생깁니다.</Empty> :
        <ul className="rows">
          {items.map((c) => {
            const due = dueLabel(c.due_at);
            return (
              <li key={c.id}>
                <Link to={`/a/calls/${c.id}`} className={`row ${c.id === id ? "on" : ""}`}>
                  <div className="row-top"><strong>{c.lead?.company_name}</strong>{c.script.notices?.length > 0 && <Badge tone="red">확인 필요</Badge>}</div>
                  <div className="row-mid">{c.lead?.name} · {c.lead?.title}</div>
                  <div className={`row-sub ${due.overdue ? "due over" : "due"}`}>{due.text}</div>
                </Link>
              </li>
            );
          })}
        </ul>
      }
      detail={selected
        ? <CallPanel key={selected.id} call={selected} onDone={() => next(selected.id)} />
        : <div className="split-empty"><Empty title="왼쪽에서 전화를 고르세요">전화는 담당자가 직접 걸고, 끝나면 결과를 기록합니다.</Empty></div>}
    />
  );
}

export function ExceptionsPage() {
  const { id } = useParams();
  const q = useQuery(() => api.leads({ status: "needs_human", limit: 100 }), [], { refreshMs: 30000 });
  const items = q.data?.leads ?? [];
  return (
    <SplitView
      hasDetail={!!id}
      listTitle={<><h2>사람이 확인할 고객 <span className="count">{items.length}</span></h2><span className="muted small">답장이 애매하거나 반려·수신 거부가 의심되는 고객</span></>}
      list={
        q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> :
        items.length === 0 ? <Empty title="확인할 고객이 없습니다" /> :
        <ul className="rows">{items.map((l) => (
          <li key={l.id}><Link to={`/a/exceptions/${l.id}`} className={`row ${l.id === id ? "on" : ""}`}>
            <div className="row-top"><strong>{l.company_name}</strong><Score value={l.score} showTags={false} /></div>
            <div className="row-mid">{l.name} · {l.title}</div>
            <div className="row-sub">{relative(l.updated_at)} 변경</div>
          </Link></li>))}</ul>
      }
      detail={id ? <LeadDetailView key={id} leadId={id} /> : <div className="split-empty"><Empty title="왼쪽에서 고객을 고르세요" /></div>}
    />
  );
}

const STATUS_FILTERS: (LeadStatus | "")[] = ["", "new", "researched", "awaiting_approval", "contacted", "replied", "needs_human", "handed_off", "unsubscribed"];

export function LeadsPage() {
  const { id } = useParams();
  const [status, setStatus] = useState<LeadStatus | "">("");
  const [term, setTerm] = useState("");
  const q = useQuery(() => api.leads({ status: status || undefined, q: term.trim() || undefined, limit: 100 }), [status, term]);
  const items = q.data?.leads ?? [];
  return (
    <SplitView
      hasDetail={!!id}
      listTitle={<><h2>리드 <span className="count">{q.data?.total ?? 0}</span></h2>
        <input className="search" placeholder="회사명 검색" value={term} onChange={(e) => setTerm(e.target.value)} aria-label="회사명 검색" />
        <div className="chips">{STATUS_FILTERS.map((s) => <button key={s || "all"} className={`chip ${status === s ? "on" : ""}`} onClick={() => setStatus(s)}>{s ? LEAD_STATUS[s].label : "전체"}</button>)}</div></>}
      list={
        q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> :
        items.length === 0 ? <Empty title="조건에 맞는 리드가 없습니다" /> :
        <ul className="rows">{items.map((l) => (
          <li key={l.id}><Link to={`/a/leads/${l.id}`} className={`row ${l.id === id ? "on" : ""}`}>
            <div className="row-top"><strong>{l.company_name}</strong><StatusBadge status={l.status} /></div>
            <div className="row-mid">{l.name} · {l.title} · {SOURCE[l.source] ?? l.source}</div>
            <div className="row-tags"><Score value={l.score} tags={l.tags?.slice(0, 2)} /></div>
          </Link></li>))}</ul>
      }
      detail={id ? <LeadDetailView key={id} leadId={id} /> : <div className="split-empty"><Empty title="왼쪽에서 리드를 고르세요" /></div>}
    />
  );
}

export function ActivityPage() {
  const [wf, setWf] = useState("");
  const q = useQuery(() => api.runs({ workflow: wf || undefined, limit: 50 }), [wf], { refreshMs: 30000 });
  return (
    <div className="page">
      <header className="page-head">
        <h1>에이전트 기록</h1>
        <p className="muted">에이전트가 어떤 도구를 왜 골랐는지 단계별로 확인할 수 있습니다. 평가와 책임 소재의 근거 자료입니다.</p>
        <div className="chips">{["", ...Object.keys(WORKFLOW)].map((w) => <button key={w || "all"} className={`chip ${wf === w ? "on" : ""}`} onClick={() => setWf(w)}>{w ? WORKFLOW[w as keyof typeof WORKFLOW] : "전체"}</button>)}</div>
      </header>
      {q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> : <RunList runs={q.data ?? []} />}
    </div>
  );
}

export function MetricsPage() {
  return <div className="page narrow"><header className="page-head"><h1>평가 지표</h1></header><MetricsPanel /></div>;
}

export function RepliesPage() {
  const { id } = useParams();
  return (
    <SplitView
      hasDetail={!!id}
      listTitle={<><h2>답장함</h2><span className="muted small">고객이 보낸 답장과 에이전트의 분류</span></>}
      list={<RepliesPanel toLead={(l) => `/a/replies/${l}`} activeLeadId={id} />}
      detail={id ? <LeadDetailView key={id} leadId={id} /> : <div className="split-empty"><Empty title="왼쪽에서 답장을 고르세요">분류가 맞는지 확인하고, 필요하면 고객 상세에서 처리합니다.</Empty></div>}
    />
  );
}

export function HistoryPage() {
  return <div className="page"><header className="page-head"><h1>승인 이력</h1><p className="muted">누가 언제 어떤 메일을 승인, 수정, 반려했는지 기록합니다. 행을 누르면 수정한 내용을 볼 수 있습니다.</p></header><ApprovalHistoryPanel /></div>;
}

export function KbPage() {
  return <div className="page narrow"><header className="page-head"><h1>승인 자료</h1></header><KbPanel /></div>;
}

export function MemoryPage() {
  return <div className="page"><header className="page-head"><h1>메모리</h1></header><MemoryPanel /></div>;
}

export function SettingsPage() {
  return <div className="page"><header className="page-head"><h1>설정·상태</h1></header><StatusPanel /></div>;
}

export function ImportPage() {
  return <div className="page narrow"><header className="page-head"><h1>리드 올리기</h1></header><ImportPanel /></div>;
}
