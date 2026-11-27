import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { useAuth } from "../../auth";
import { CallPanel } from "../../components/CallPanel";
import { ImportPanel } from "../../components/ImportPanel";
import { MetricsPanel } from "../../components/MetricsPanel";
import { ApprovalHistoryPanel, RepliesPanel } from "../../components/HistoryPanels";
import { KbPanel, StatusPanel } from "../../components/SettingsPanels";
import { QuickSearch } from "../../components/QuickSearch";
import { LeadDetailView } from "../../components/LeadDetailView";
import { RunList } from "../../components/RunTrace";
import { Badge, Empty, ErrorBox, Loading, Score } from "../../components/ui";
import { dueLabel, relative } from "../../lib/format";
import { LEAD_STATUS, PIPELINE, WORKFLOW } from "../../lib/labels";
import type { Lead, LeadStatus } from "../../types";

const STAGE_NAME: Record<string, string> = {
  new: "신규 유입", researched: "조사 완료", awaiting_approval: "승인 대기", contacted: "메일 발송", replied: "답장 받음",
  needs_human: "사람 확인", handed_off: "인계 완료",
};
const STAGE_NOTE: Record<string, string> = {
  new: "에이전트가 조사합니다", researched: "에이전트가 초안을 씁니다", awaiting_approval: "사람이 승인합니다", contacted: "1시간 뒤 전화",
  replied: "에이전트가 분류합니다", needs_human: "사람이 판단합니다", handed_off: "영업 담당자에게 전달",
};
const MAX_CARDS = 8;

function LeadCard({ lead }: { lead: Lead }) {
  const cta = lead.status === "awaiting_approval" ? "승인하기" : lead.status === "needs_human" ? "확인하기" : null;
  return (
    <Link to={`/b/lead/${lead.id}`} className={`b-card ${cta ? "action" : ""}`}>
      <div className="b-card-top"><strong>{lead.company_name}</strong><Score value={lead.score} showTags={false} /></div>
      <div className="b-card-mid">{lead.name} · {lead.title}</div>
      <div className="b-card-tags">{lead.tags?.slice(0, 2).map((t) => <span key={t} className="tag">{t}</span>)}</div>
      <div className="b-card-foot"><span className="muted small">{relative(lead.updated_at)}</span>{cta && <span className="b-cta">{cta} →</span>}</div>
    </Link>
  );
}

function Board() {
  const [term, setTerm] = useState("");
  const [focus, setFocus] = useState<LeadStatus | null>(null);
  const summary = useQuery(() => api.summary(), [], { refreshMs: 30000 });
  const leads = useQuery(() => api.leads({ limit: 200 }), [], { refreshMs: 30000 });
  const calls = useQuery(() => api.calls(true), [], { refreshMs: 30000 });

  const grouped = useMemo(() => {
    const g = new Map<LeadStatus, Lead[]>();
    (leads.data?.leads ?? []).filter((l) => !term.trim() || l.company_name.includes(term.trim())).forEach((l) => {
      g.set(l.status, [...(g.get(l.status) ?? []), l]);
    });
    g.forEach((v) => v.sort((a, b) => (b.score ?? 0) - (a.score ?? 0)));
    return g;
  }, [leads.data, term]);

  const counts = summary.data?.lead_status_counts ?? {};
  return (
    <div className="b-control">
      <section className="b-flow" aria-label="파이프라인">
        {PIPELINE.map(({ status, actor }, i) => (
          <button key={status} className={`b-stage ${actor} ${focus === status ? "on" : ""}`} onClick={() => setFocus(focus === status ? null : status)}>
            <span className="b-stage-n">{counts[status] ?? 0}</span>
            <span className="b-stage-name">{STAGE_NAME[status]}</span>
            <span className="b-stage-note">{STAGE_NOTE[status]}</span>
            {i < PIPELINE.length - 1 && <span className="b-arrow" aria-hidden>›</span>}
          </button>
        ))}
        <div className="b-legend"><span className="actor agent inline" /> 에이전트 <span className="actor human inline" /> 사람</div>
      </section>

      <section className="b-calls">
        <h2>지금 걸 전화 <span className="count">{calls.data?.length ?? 0}</span></h2>
        {calls.loading && !calls.data ? <Loading /> : (calls.data?.length ?? 0) === 0
          ? <p className="muted">지금 걸 전화가 없습니다. 메일을 승인하면 1시간 뒤 과제가 생깁니다.</p>
          : <div className="b-call-row">{calls.data!.map((c) => (
              <Link key={c.id} to={`/b/call/${c.id}`} className="b-call">
                <strong>{c.lead?.company_name}</strong>
                <span>{c.lead?.name} {c.lead?.title}</span>
                <span className="due over">{dueLabel(c.due_at).text}</span>
                {c.script.notices?.length > 0 && <Badge tone="red">확인 필요</Badge>}
              </Link>))}</div>}
      </section>

      <div className="b-boardhead">
        <h2>고객 파이프라인</h2>
        <input className="search" placeholder="회사명 검색" value={term} onChange={(e) => setTerm(e.target.value)} aria-label="회사명 검색" />
      </div>
      {leads.error && <ErrorBox message={leads.error} onRetry={leads.reload} />}
      {leads.loading && !leads.data ? <Loading /> : (
        <div className="b-board">
          {PIPELINE.filter(({ status }) => !focus || status === focus).map(({ status, actor }) => {
            const list = grouped.get(status) ?? [];
            const shown = focus ? list : list.slice(0, MAX_CARDS);
            return (
              <div key={status} className={`b-col ${actor}`}>
                <h3><span className={`actor ${actor}`} />{LEAD_STATUS[status].label}<span className="count">{counts[status] ?? list.length}</span></h3>
                <div className="b-col-body">
                  {shown.map((l) => <LeadCard key={l.id} lead={l} />)}
                  {list.length === 0 && <p className="muted small">없음</p>}
                  {!focus && list.length > MAX_CARDS && <button className="link" onClick={() => setFocus(status)}>나머지 {list.length - MAX_CARDS}건 더 보기</button>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Feed() {
  const s = useQuery(() => api.summary(), [], { refreshMs: 30000 });
  return (
    <aside className="b-feed" aria-label="에이전트 활동">
      <h2>에이전트 활동</h2>
      {s.loading && !s.data ? <Loading /> : s.error ? <ErrorBox message={s.error} onRetry={s.reload} /> : (
        <ul>
          {s.data!.recent_runs.map((r) => (
            <li key={r.id}>
              <Link to={r.lead_id ? `/b/lead/${r.lead_id}` : "/b"}>
                <span className="feed-meta"><Badge tone="blue">{WORKFLOW[r.workflow] ?? r.workflow}</Badge><span className="muted small">{relative(r.started_at)}</span></span>
                <strong>{r.lead?.company_name ?? "-"}</strong>
                <span className="feed-text">{r.final_text}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Link to="/b/activity" className="feed-more">전체 기록 보기</Link>
    </aside>
  );
}

function Drawer({ children, title }: { children: React.ReactNode; title: string }) {
  const nav = useNavigate();
  const close = () => nav("/b");
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  return (
    <div className="b-drawer-wrap">
      <div className="b-scrim" onClick={close} />
      <aside className="b-drawer" role="dialog" aria-label={title}>
        <button className="btn ghost b-close" onClick={close} aria-label="닫기">닫기 (Esc)</button>
        {children}
      </aside>
    </div>
  );
}

function LeadDrawer() {
  const { id } = useParams();
  return <Drawer title="고객 상세"><LeadDetailView key={id} leadId={id!} /></Drawer>;
}

function CallDrawer() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useQuery(() => api.calls(false), []);
  const call = q.data?.find((c) => c.id === id);
  return (
    <Drawer title="전화">
      {q.loading && !q.data ? <Loading /> : call ? <CallPanel call={call} onDone={() => nav("/b")} /> : <Empty title="이 전화 과제를 찾을 수 없습니다">이미 기록되었거나 취소되었을 수 있습니다.</Empty>}
    </Drawer>
  );
}

function MessageRedirect() {
  const { id } = useParams();
  const q = useQuery(() => api.messages("pending_approval"), []);
  if (q.loading && !q.data) return <Drawer title="메일"><Loading /></Drawer>;
  const m = q.data?.find((x) => x.id === id);
  if (m) return <Navigate to={`/b/lead/${m.lead_id}`} replace />;
  return <Drawer title="메일"><Empty title="이미 처리된 메일입니다">승인 대기 목록에서 찾을 수 없습니다.</Empty></Drawer>;
}

/** 디자인 B: 관제 센터. 파이프라인 전체와 에이전트 활동을 한눈에 보고, 필요한 고객만 열어 처리한다. */
export function DesignB() {
  const { name, logout, mock } = useAuth();
  const nav = useNavigate();
  useEffect(() => { localStorage.setItem("sdr_design", "b"); }, []);
  return (
    <div className="shell-b" data-design="b">
      <header className="b-top">
        <div className="brand"><span className="brand-mark" aria-hidden>S</span>SDR 에이전트 관제</div>
        <nav>
          <NavLink to="/b" end>관제</NavLink>
          <NavLink to="/b/replies">답장함</NavLink>
          <NavLink to="/b/activity">에이전트 기록</NavLink>
          <NavLink to="/b/history">승인 이력</NavLink>
          <NavLink to="/b/kb">승인 자료</NavLink>
          <NavLink to="/b/metrics">평가 지표</NavLink>
          <NavLink to="/b/settings">설정·상태</NavLink>
          <NavLink to="/b/import">리드 올리기</NavLink>
        </nav>
        <div className="b-user">
          {mock && <span className="mock-pill">시연 데이터</span>}
          <span>{name}</span>
          <button className="link" onClick={() => { localStorage.setItem("sdr_design", "a"); nav("/a"); }}>디자인 A로 보기</button>
          <button className="link" onClick={logout}>로그아웃</button>
        </div>
      </header>
      <main className="b-main">
        <Routes>
          <Route path="*" element={<div className="b-layout"><Board /><Feed /></div>} />
          <Route path="activity" element={<ActivityB />} />
          <Route path="metrics" element={<div className="page narrow"><MetricsPanel /></div>} />
          <Route path="replies" element={<div className="page narrow"><header className="page-head"><h1>답장함</h1><p className="muted">고객이 보낸 답장과 에이전트의 분류입니다. 눌러서 고객 상세를 엽니다.</p></header><RepliesPanel toLead={(id) => `/b/lead/${id}`} /></div>} />
          <Route path="history" element={<div className="page"><header className="page-head"><h1>승인 이력</h1><p className="muted">누가 언제 어떤 메일을 승인, 수정, 반려했는지 기록합니다. 행을 누르면 수정한 내용을 볼 수 있습니다.</p></header><ApprovalHistoryPanel /></div>} />
          <Route path="kb" element={<div className="page narrow"><header className="page-head"><h1>승인 자료</h1></header><KbPanel /></div>} />
          <Route path="settings" element={<div className="page"><header className="page-head"><h1>설정·상태</h1></header><StatusPanel /></div>} />
          <Route path="import" element={<div className="page narrow"><ImportPanel /></div>} />
        </Routes>
        <Routes>
          <Route path="lead/:id" element={<LeadDrawer />} />
          <Route path="call/:id" element={<CallDrawer />} />
          <Route path="message/:id" element={<MessageRedirect />} />
        </Routes>
      </main>
      <QuickSearch toLead={(id) => `/b/lead/${id}`} />
    </div>
  );
}

function ActivityB() {
  const q = useQuery(() => api.runs({ limit: 60 }), [], { refreshMs: 30000 });
  return (
    <div className="page">
      <header className="page-head"><h1>에이전트 기록</h1><p className="muted">에이전트가 어떤 도구를 왜 골랐는지 단계별로 보여 줍니다.</p></header>
      {q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> : <RunList runs={q.data ?? []} />}
    </div>
  );
}
