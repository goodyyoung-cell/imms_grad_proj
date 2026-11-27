import { useEffect } from "react";
import { Link, NavLink, Route, Routes, Navigate } from "react-router-dom";
import { api } from "../../api/client";
import { useQuery } from "../../api/hooks";
import { useAuth } from "../../auth";
import { ActivityPage, ApprovalsPage, Briefing, CallsPage, ExceptionsPage, ImportPage, LeadsPage, MetricsPage } from "./pages";

/** 디자인 A: 업무함. 오늘 사람이 할 일을 종류별 목록에서 하나씩 처리한다. */
export function DesignA() {
  const { name, logout, mock } = useAuth();
  useEffect(() => { localStorage.setItem("sdr_design", "a"); }, []);
  const s = useQuery(() => api.summary(), [], { refreshMs: 30000 });
  const d = s.data;
  const human = d?.lead_status_counts.needs_human ?? 0;

  const link = (to: string, label: string, n?: number, end = false) => (
    <NavLink to={to} end={end} className={({ isActive }) => `nav-link ${isActive ? "on" : ""}`}>
      <span>{label}</span>{n ? <span className="nav-n">{n}</span> : null}
    </NavLink>
  );

  return (
    <div className="shell-a" data-design="a">
      <nav className="sidebar" aria-label="주 메뉴">
        <div className="brand"><span className="brand-mark" aria-hidden>S</span>SDR 에이전트</div>
        {link("/a", "오늘 할 일", undefined, true)}
        <div className="nav-group">처리할 일</div>
        {link("/a/approvals", "승인 대기", d?.pending_approvals)}
        {link("/a/calls", "전화", d?.calls_due)}
        {link("/a/exceptions", "확인 필요", human)}
        <div className="nav-group">전체</div>
        {link("/a/leads", "리드")}
        {link("/a/activity", "에이전트 기록")}
        {link("/a/metrics", "평가 지표")}
        {link("/a/import", "리드 올리기")}
        <div className="sidebar-foot">
          {mock && <div className="mock-pill">시연 데이터</div>}
          <div className="who">{name}</div>
          <Link className="switch" to="/b" onClick={() => localStorage.setItem("sdr_design", "b")}>디자인 B로 보기</Link>
          <button className="link" onClick={logout}>로그아웃</button>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route index element={<Briefing />} />
          <Route path="approvals/:id?" element={<ApprovalsPage />} />
          <Route path="calls/:id?" element={<CallsPage />} />
          <Route path="exceptions/:id?" element={<ExceptionsPage />} />
          <Route path="leads/:id?" element={<LeadsPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="metrics" element={<MetricsPage />} />
          <Route path="import" element={<ImportPage />} />
          <Route path="*" element={<Navigate to="/a" replace />} />
        </Routes>
      </main>
    </div>
  );
}
