import { useId, useState } from "react";
import { lineDiff } from "../lib/diff";
import { domainOf, kstDateTime } from "../lib/format";
import { FEEDBACK_KIND, FEEDBACK_SCOPE, HOOK_TYPE, SUBJECT_CHOICE } from "../lib/labels";
import type { FeedbackApplied, Hook, Revision, SubjectChoice, SubjectOption } from "../types";
import { Badge, Collapsible } from "./ui";

/** 제목 후보를 칩으로 고른다. 후보가 없으면(오래된 메일, 답변 초안) 아무것도 그리지 않는다. */
export function SubjectPicker({ options, choice, disabled, error, onSelect }: {
  options: SubjectOption[]; choice: SubjectChoice; disabled?: boolean; error?: string | null; onSelect: (o: SubjectOption) => void;
}) {
  const [preview, setPreview] = useState<SubjectOption | null>(null);
  const labelId = useId();
  if (options.length === 0) return null;
  return (
    <div className="subject-picker">
      <div className="subject-picker-head">
        <span id={labelId} className="subject-picker-title">제목 후보</span>
        <Badge tone={choice === "custom" ? "amber" : "blue"} title="지금 메일 제목이 어느 후보인지">현재 {SUBJECT_CHOICE[choice]}</Badge>
      </div>
      <div className="subject-chips" role="radiogroup" aria-labelledby={labelId}>
        {options.map((o) => (
          <button type="button" key={o.id} role="radio" aria-checked={choice === o.id} disabled={disabled}
            className={`subject-chip ${choice === o.id ? "on" : ""}`} title={o.subject}
            onClick={() => onSelect(o)}
            onMouseEnter={() => setPreview(o)} onMouseLeave={() => setPreview(null)}
            onFocus={() => setPreview(o)} onBlur={() => setPreview(null)}>
            {o.label}
          </button>
        ))}
      </div>
      {preview && preview.id !== choice && <p className="subject-preview small muted">{preview.subject}</p>}
      {error && <p className="notice err" role="alert">{error}</p>}
    </div>
  );
}

/** 오프닝에 쓴 소재. 어디서 가져왔는지 출처 링크와 함께 보여 준다 */
export function HookBox({ hook }: { hook: Hook }) {
  return (
    <section className="hook-box" aria-label="소재">
      <div className="hook-head"><strong>소재</strong><Badge tone="violet">{HOOK_TYPE[hook.type] ?? hook.type}</Badge></div>
      <p className="hook-summary">{hook.summary}</p>
      {hook.source_url && (
        <a className="small" href={hook.source_url} target="_blank" rel="noreferrer noopener">
          {hook.source_title ?? "출처"} · {domainOf(hook.source_url)} ↗
        </a>
      )}
    </section>
  );
}

/** 이 초안을 쓸 때 반영한 승인자 피드백 */
export function FeedbackAppliedList({ items }: { items: FeedbackApplied[] }) {
  if (items.length === 0) return null;
  return (
    <Collapsible title={`반영된 피드백 ${items.length}건`}>
      <ul className="applied-list">
        {items.map((f) => (
          <li key={f.id}>
            <span className="applied-tags">
              <Badge tone={FEEDBACK_SCOPE[f.scope]?.tone ?? "gray"}>{f.scope_label}</Badge>
              <span className="tag">{FEEDBACK_KIND[f.kind] ?? f.kind}</span>
            </span>
            <span>{f.text}</span>
          </li>
        ))}
      </ul>
    </Collapsible>
  );
}

const joined = (r: { subject: string; body: string }) => `${r.subject}\n\n${r.body}`;

/** 챗으로 다시 쓰기 전의 이전 버전. 고른 버전이 다음 버전(마지막은 지금 초안)으로 어떻게 바뀌었는지 비교한다 */
export function RevisionViewer({ revisions, current }: { revisions: Revision[]; current: { subject: string; body: string } }) {
  const [index, setIndex] = useState(revisions.length - 1);
  const [mode, setMode] = useState<"diff" | "before">("diff");
  if (revisions.length === 0) return null;
  const i = Math.min(index, revisions.length - 1);
  const rev = revisions[i];
  const after = revisions[i + 1] ?? current;
  const lines = lineDiff(joined(rev), joined(after));
  return (
    <Collapsible title={`이전 버전 ${revisions.length}개`}>
      <div className="rev-viewer">
        {revisions.length > 1 && (
          <div className="chips" role="radiogroup" aria-label="이전 버전 고르기">
            {revisions.map((r, n) => (
              <button type="button" key={r.at} role="radio" aria-checked={n === i} className={`chip ${n === i ? "on" : ""}`} onClick={() => setIndex(n)}>
                {n + 1}번째 버전
              </button>
            ))}
          </div>
        )}
        <p className="muted small">{kstDateTime(rev.at)} · {rev.by}의 요청: {rev.instruction}</p>
        <div className="chips" role="radiogroup" aria-label="보기 방식">
          <button type="button" role="radio" aria-checked={mode === "diff"} className={`chip ${mode === "diff" ? "on" : ""}`} onClick={() => setMode("diff")}>바뀐 부분</button>
          <button type="button" role="radio" aria-checked={mode === "before"} className={`chip ${mode === "before" ? "on" : ""}`} onClick={() => setMode("before")}>이전 내용</button>
        </div>
        {mode === "diff" ? (
          <div className="diff" aria-label="이전 버전과 비교">
            {lines.map((l, n) => <div key={n} className={`diff-line ${l.kind}`}><span className="diff-mark">{l.kind === "add" ? "+" : l.kind === "del" ? "-" : " "}</span>{l.text || " "}</div>)}
          </div>
        ) : (
          <div className="rev-before">
            <div className="rev-subject"><span className="muted small">제목</span> {rev.subject}</div>
            <pre className="tl-body">{rev.body}</pre>
          </div>
        )}
        <p className="muted small">빨간 줄은 이전 버전, 초록 줄은 {revisions[i + 1] ? `${i + 2}번째 버전` : "지금 초안"}에 새로 들어간 내용입니다.</p>
      </div>
    </Collapsible>
  );
}
