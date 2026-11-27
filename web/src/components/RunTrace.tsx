import { useState } from "react";
import { api } from "../api/client";
import { useQuery } from "../api/hooks";
import { durationMs, kstDateTime, runSeconds, shortModel } from "../lib/format";
import { WORKFLOW } from "../lib/labels";
import type { Run, RunStep } from "../types";
import { Badge, ErrorBox, Loading } from "./ui";

function json(v: unknown): string {
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function Step({ step }: { step: RunStep }) {
  const [open, setOpen] = useState(false);
  const isTool = step.kind === "tool";
  return (
    <li className={`step ${step.kind} ${step.is_error ? "err" : ""}`}>
      <div className="step-head">
        <span className="step-no">{step.step_no}</span>
        <Badge tone={isTool ? "violet" : "blue"}>{isTool ? `도구 · ${step.tool_name}` : "AI 판단"}</Badge>
        <span className="muted small">{durationMs(step.duration_ms)}</span>
      </div>
      {step.text && <p className="step-text">{step.text}</p>}
      {isTool && (
        <button type="button" className="link" onClick={() => setOpen(!open)}>{open ? "입력·결과 접기" : "입력·결과 보기"}</button>
      )}
      {open && <pre className="step-io">{json({ input: step.input, output: step.output })}</pre>}
    </li>
  );
}

/** 에이전트가 어떤 도구를 왜 골랐는지 단계별로 보여 준다 (평가의 근거 자료) */
export function RunSteps({ runId }: { runId: string }) {
  const q = useQuery(() => api.run(runId), [runId]);
  if (q.loading && !q.data) return <Loading />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.reload} />;
  const r = q.data!;
  return (
    <div className="run-steps">
      <ol>{r.steps.map((s) => <Step key={s.step_no} step={s} />)}</ol>
      {r.final_text && <p className="run-final"><strong>마무리</strong> {r.final_text}</p>}
      {r.error && <p className="notice err">{r.error}</p>}
    </div>
  );
}

export function RunList({ runs, showLead = true }: { runs: Run[]; showLead?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  if (runs.length === 0) return <p className="muted">실행 기록이 없습니다.</p>;
  return (
    <ul className="run-list">
      {runs.map((r) => (
        <li key={r.id} className={open === r.id ? "open" : ""}>
          <button type="button" className="run-row" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
            <Badge tone="blue">{WORKFLOW[r.workflow] ?? r.workflow}</Badge>
            {showLead && <strong>{r.lead?.company_name ?? "-"}</strong>}
            <span className="run-text">{r.final_text}</span>
            <span className="muted small">{kstDateTime(r.started_at)} · {runSeconds(r.started_at, r.finished_at)} · {shortModel(r.model)}</span>
            {r.status !== "succeeded" && <Badge tone="red">{r.status === "failed" ? "실패" : r.status}</Badge>}
          </button>
          {open === r.id && <RunSteps runId={r.id} />}
        </li>
      ))}
    </ul>
  );
}
