import { useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction, useQuery } from "../api/hooks";
import { useToast } from "../toast";
import { Card, ErrorBox, Loading } from "./ui";

function fmt(sec: number | null | undefined): string {
  if (sec == null) return "-";
  const s = Math.round(sec);
  return s < 90 ? `${s}초` : `${(s / 60).toFixed(1)}분`;
}

function download(rows: string[][]) {
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = "\uFEFF" + rows.map((r) => r.map(esc).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = "sdr-agent-metrics.csv"; a.click();
  URL.revokeObjectURL(url);
}

/** 평가 지표와 수작업 기준 시간. 에이전트 방식과 사람 방식을 같은 조건에서 비교하는 데 쓴다. */
export function MetricsPanel() {
  const toast = useToast();
  const m = useQuery(() => api.metrics(), []);
  const b = useQuery(() => api.baseline(), []);
  const [task, setTask] = useState("research");
  const [minutes, setMinutes] = useState("");
  const [label, setLabel] = useState("");
  const add = useAction(async () => {
    await api.addBaseline({ task, minutes: Number(minutes), label: label.trim() || undefined });
    toast("수작업 시간을 기록했습니다."); setMinutes(""); setLabel(""); invalidate();
  });

  const groups = new Map<string, string[][]>();
  (m.data?.rows ?? []).forEach((r) => groups.set(r[0], [...(groups.get(r[0]) ?? []), r]));

  return (
    <>
      <Card title="평가 지표" aside={m.data && <button className="btn sm" onClick={() => download(m.data!.rows)}>CSV로 내려받기</button>}>
        {m.loading && !m.data ? <Loading /> : m.error ? <ErrorBox message={m.error} onRetry={m.reload} /> : (
          <div className="metrics">
            {[...groups.entries()].map(([g, rows]) => (
              <section key={g}>
                <h4>{g}</h4>
                <table className="table"><tbody>{rows.map((r, i) => <tr key={i}><td>{r[1]}</td><td className="num">{r[2]}</td></tr>)}</tbody></table>
              </section>
            ))}
            {groups.size === 0 && <p className="muted">아직 집계할 데이터가 없습니다.</p>}
          </div>
        )}
        <p className="muted small">모의 환경의 결과이며 실제 매출 성과로 일반화하지 않습니다.</p>
      </Card>

      <Card title="수작업 기준 시간">
        <p className="muted">같은 일을 사람이 직접 했을 때 걸린 시간을 적어 두면 에이전트 처리 시간과 비교할 수 있습니다.</p>
        {b.data && (
          <table className="table">
            <thead><tr><th>과제</th><th className="num">건수</th><th className="num">중앙값</th><th className="num">평균</th></tr></thead>
            <tbody>{Object.entries(b.data.tasks).map(([k, name]) => {
              const s = b.data!.stats[k];
              return <tr key={k}><td>{name}</td><td className="num">{s?.n ?? 0}</td><td className="num">{fmt(s?.median)}</td><td className="num">{fmt(s?.mean)}</td></tr>;
            })}</tbody>
          </table>
        )}
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); add.run(); }}>
          <label>과제
            <select value={task} onChange={(e) => setTask(e.target.value)}>
              {Object.entries(b.data?.tasks ?? { research: "기업 리서치" }).map(([k, n]) => <option key={k} value={k}>{n}</option>)}
            </select>
          </label>
          <label>걸린 시간(분)<input type="number" min="0.5" max="480" step="0.5" value={minutes} onChange={(e) => setMinutes(e.target.value)} required /></label>
          <label className="wide">어떤 리드로 쟀나요<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="예: 삼양식품 첫 메일" /></label>
          <div className="decision-actions wide"><button className="btn primary" disabled={add.busy || !minutes}>기록</button></div>
        </form>
        {add.error && <p className="notice err" role="alert">{add.error}</p>}
      </Card>
    </>
  );
}
