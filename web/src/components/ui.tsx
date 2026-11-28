import { useState, type ReactNode } from "react";
import { LEAD_STATUS, type Tone } from "../lib/labels";
import type { LeadStatus } from "../types";

export function Badge({ tone = "gray", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function StatusBadge({ status }: { status: LeadStatus }) {
  const s = LEAD_STATUS[status];
  return <Badge tone={s?.tone ?? "gray"} title={s?.hint}>{s?.label ?? status}</Badge>;
}

export function AiMark({ children = "AI 초안" }: { children?: ReactNode }) {
  return <span className="ai-mark" title="AI가 작성했습니다. 사람이 검토하기 전에는 고객에게 나가지 않습니다.">{children}</span>;
}

export function Score({ value, tags, showTags = true }: { value: number | null | undefined; tags?: string[]; showTags?: boolean }) {
  if (value == null) return <span className="muted">점수 없음</span>;
  const level = value >= 80 ? "hi" : value >= 60 ? "mid" : "lo";
  return (
    <span className="score-wrap">
      <span className={`score ${level}`} title="규칙으로 계산한 우선순위 점수입니다">{Math.round(value)}</span>
      {showTags && tags && tags.length > 0 && (
        <span className="tags">{tags.map((t) => <span key={t} className="tag">{t}</span>)}</span>
      )}
    </span>
  );
}

export function Loading({ text = "불러오는 중" }: { text?: string }) {
  return <div className="state loading"><span className="spinner" aria-hidden />{text}</div>;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  if (message === "Not Found") message = "이 기능은 아직 서버에 배포되지 않았습니다. 백엔드 최신 버전을 배포하면 보입니다.";
  return (
    <div className="state error" role="alert">
      <span>{message}</span>
      {onRetry && <button className="btn sm" onClick={onRetry}>다시 불러오기</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="state empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || aside) && <header className="card-head"><h3>{title}</h3>{aside}</header>}
      {children}
    </section>
  );
}

export function Collapsible({ title, defaultOpen = false, children, badge }: { title: ReactNode; defaultOpen?: boolean; children: ReactNode; badge?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapsible ${open ? "open" : ""}`}>
      <button type="button" className="collapsible-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="chev" aria-hidden>{open ? "▾" : "▸"}</span>{title}{badge}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

export function CopyButton({ text, label = "복사" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="btn sm ghost" onClick={async () => {
      try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* 권한 없음 */ }
    }}>{done ? "복사됨" : label}</button>
  );
}

/** 위험한 동작은 한 번 더 눌러야 실행된다 */
export function ConfirmButton({ label, confirmLabel, onConfirm, disabled, className = "btn", note }: {
  label: string; confirmLabel: string; onConfirm: () => void; disabled?: boolean; className?: string; note?: string;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) return <button type="button" className={className} disabled={disabled} onClick={() => setArmed(true)}>{label}</button>;
  return (
    <span className="confirm-inline">
      {note && <span className="muted">{note}</span>}
      <button type="button" className={`${className} danger`} onClick={() => { setArmed(false); onConfirm(); }}>{confirmLabel}</button>
      <button type="button" className="btn ghost" onClick={() => setArmed(false)}>취소</button>
    </span>
  );
}
