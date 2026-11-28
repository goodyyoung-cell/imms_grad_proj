import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { invalidate, useAction, useQuery } from "../api/hooks";
import { useMemoryPath } from "../lib/chatContext";
import { kstDateTime } from "../lib/format";
import { FEEDBACK_KIND, FEEDBACK_SCOPE, FEEDBACK_SOURCE } from "../lib/labels";
import { countNotes, filterNotes, groupNotes, KIND_OPTIONS, validateNote } from "../lib/memory";
import { useToast } from "../toast";
import type { FeedbackKind, FeedbackNote, FeedbackScope, FeedbackStatus } from "../types";
import { Badge, Card, ConfirmButton, Empty, ErrorBox, Loading } from "./ui";

export const MEMORY_INTRO = "챗이나 이 화면에서 남긴 피드백은 다음 메일, 답장, 통화 스크립트를 만들 때 자동으로 반영됩니다. 고객 > 업종 > 전체 순으로 좁은 범위가 우선합니다.";

export interface ScopeValue { scope: FeedbackScope; scope_key: string | null; label: string }

function IndustrySelect({ value, onChange }: { value: string | null; onChange: (key: string | null, label: string) => void }) {
  const q = useQuery(() => api.industries(), []);
  if (q.error) return <ErrorBox message={q.error} onRetry={q.reload} />;
  return (
    <label>업종
      <select value={value ?? ""} disabled={q.loading && !q.data} onChange={(e) => {
        const hit = q.data?.find((i) => i.key === e.target.value);
        onChange(hit?.key ?? null, hit?.label ?? "");
      }}>
        <option value="">업종을 고르세요</option>
        {(q.data ?? []).map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
      </select>
    </label>
  );
}

function LeadPicker({ value, onChange }: { value: { id: string; label: string } | null; onChange: (v: { id: string; label: string } | null) => void }) {
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => { const t = setTimeout(() => setDebounced(term.trim()), 250); return () => clearTimeout(t); }, [term]);
  const q = useQuery(() => api.leads({ q: debounced, limit: 6 }), [debounced], { enabled: !value && debounced.length > 0 });
  if (value) {
    return (
      <div className="picked">
        <span className="muted small">고객</span> <strong>{value.label}</strong>
        <button type="button" className="btn sm ghost" onClick={() => onChange(null)}>바꾸기</button>
      </div>
    );
  }
  const rows = debounced ? (q.data?.leads ?? []) : [];
  return (
    <div className="lead-picker">
      <label>고객 검색
        <input value={term} placeholder="회사명을 입력하세요" onChange={(e) => setTerm(e.target.value)} />
      </label>
      {q.error && <ErrorBox message={q.error} onRetry={q.reload} />}
      {rows.length > 0 && (
        <ul className="pick-list" aria-label="검색 결과">
          {rows.map((l) => (
            <li key={l.id}>
              <button type="button" onClick={() => onChange({ id: l.id, label: l.company_name })}>
                <strong>{l.company_name}</strong> <span className="muted small">{l.name} · {l.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {debounced && !q.loading && !q.error && rows.length === 0 && <p className="muted small">검색 결과가 없습니다.</p>}
    </div>
  );
}

/** 적용 범위 고르기: 전체, 업종(목록에서), 고객(검색해서) */
export function ScopePicker({ value, onChange }: { value: ScopeValue; onChange: (v: ScopeValue) => void }) {
  return (
    <div className="scope-picker">
      <label>적용 범위
        <select value={value.scope} onChange={(e) => onChange({ scope: e.target.value as FeedbackScope, scope_key: null, label: "" })}>
          {(["global", "industry", "lead"] as FeedbackScope[]).map((s) => <option key={s} value={s}>{FEEDBACK_SCOPE[s].label}</option>)}
        </select>
      </label>
      {value.scope === "industry" && <IndustrySelect value={value.scope_key} onChange={(key, label) => onChange({ ...value, scope_key: key, label })} />}
      {value.scope === "lead" && (
        <LeadPicker value={value.scope_key ? { id: value.scope_key, label: value.label } : null}
          onChange={(v) => onChange({ ...value, scope_key: v?.id ?? null, label: v?.label ?? "" })} />
      )}
    </div>
  );
}

function KindSelect({ value, onChange }: { value: FeedbackKind; onChange: (k: FeedbackKind) => void }) {
  return (
    <label>종류
      <select value={value} onChange={(e) => onChange(e.target.value as FeedbackKind)}>
        {KIND_OPTIONS.map((k) => <option key={k} value={k}>{FEEDBACK_KIND[k]}</option>)}
      </select>
    </label>
  );
}

/** 메모 추가. 고객 상세에서는 고객이 정해져 있어(fixedLead) 범위를 고르지 않는다. */
export function NoteForm({ fixedLead, onSaved, onCancel }: { fixedLead?: { id: string; label: string }; onSaved?: () => void; onCancel?: () => void }) {
  const toast = useToast();
  const textId = useId();
  const [text, setText] = useState("");
  const [kind, setKind] = useState<FeedbackKind>("other");
  const [scope, setScope] = useState<ScopeValue>(fixedLead ? { scope: "lead", scope_key: fixedLead.id, label: fixedLead.label } : { scope: "global", scope_key: null, label: "" });
  const [problem, setProblem] = useState<string | null>(null);

  const save = useAction(async () => {
    const err = validateNote({ text, scope: scope.scope, scope_key: scope.scope_key });
    setProblem(err);
    if (err) return;
    await api.addFeedback({ text: text.trim(), scope: scope.scope, scope_key: scope.scope_key, kind });
    toast("메모리에 저장했습니다. 다음 초안부터 반영됩니다.");
    setText(""); setKind("other");
    invalidate();
    onSaved?.();
  });

  return (
    <form className="note-form" onSubmit={(e) => { e.preventDefault(); save.run(); }}>
      <div className="field">
        <label htmlFor={textId}>내용 (한 줄 규칙)</label>
        <textarea id={textId} rows={2} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={fixedLead ? "예: 가격 이야기는 빼고 무료 체험부터 안내한다" : "예: 제목은 30자 안으로 쓴다"} />
      </div>
      <div className="form-grid">
        {!fixedLead && <ScopePicker value={scope} onChange={setScope} />}
        <KindSelect value={kind} onChange={setKind} />
      </div>
      {(problem || save.error) && <p className="notice err" role="alert">{problem || save.error}</p>}
      <div className="decision-actions">
        <button type="submit" className="btn primary" disabled={save.busy}>{save.busy ? "저장 중" : "메모 저장"}</button>
        {onCancel && <button type="button" className="btn ghost" onClick={onCancel}>닫기</button>}
        {fixedLead && <span className="muted small">{fixedLead.label}에게만 적용됩니다.</span>}
      </div>
    </form>
  );
}

function NoteRow({ note }: { note: FeedbackNote }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note.text);
  const [kind, setKind] = useState<FeedbackKind>(note.kind);
  const [scope, setScope] = useState<ScopeValue>({ scope: note.scope, scope_key: note.scope_key, label: note.scope_label });
  const [problem, setProblem] = useState<string | null>(null);
  const archived = note.status === "archived";

  const save = useAction(async () => {
    const err = validateNote({ text, scope: scope.scope, scope_key: scope.scope_key });
    setProblem(err);
    if (err) return;
    await api.updateFeedback(note.id, { text: text.trim(), kind, scope: scope.scope, scope_key: scope.scope_key });
    toast("메모를 고쳤습니다.");
    setEditing(false);
    invalidate();
  });
  const toggle = useAction(async () => {
    await api.updateFeedback(note.id, { status: archived ? "active" : "archived" });
    toast(archived ? "다시 사용합니다." : "보관했습니다. 더 이상 반영하지 않습니다.");
    invalidate();
  });
  const remove = useAction(async () => { await api.deleteFeedback(note.id); toast("메모를 지웠습니다."); invalidate(); });

  const start = () => {
    setText(note.text); setKind(note.kind); setScope({ scope: note.scope, scope_key: note.scope_key, label: note.scope_label }); setProblem(null); setEditing(true);
  };

  if (editing) {
    return (
      <li className="note editing">
        <div className="field">
          <label>내용<textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} /></label>
        </div>
        <div className="form-grid"><ScopePicker value={scope} onChange={setScope} /><KindSelect value={kind} onChange={setKind} /></div>
        {(problem || save.error) && <p className="notice err" role="alert">{problem || save.error}</p>}
        <div className="decision-actions">
          <button type="button" className="btn primary sm" disabled={save.busy} onClick={() => save.run()}>{save.busy ? "저장 중" : "저장"}</button>
          <button type="button" className="btn ghost sm" onClick={() => setEditing(false)}>취소</button>
        </div>
      </li>
    );
  }

  const err = toggle.error || remove.error;
  return (
    <li className={`note ${archived ? "archived" : ""}`}>
      <div className="note-main">
        <div className="note-tags">
          <Badge tone={FEEDBACK_SCOPE[note.scope].tone} title={`적용 범위: ${FEEDBACK_SCOPE[note.scope].label}`}>{note.scope_label || FEEDBACK_SCOPE[note.scope].label}</Badge>
          <span className="tag">{FEEDBACK_KIND[note.kind] ?? note.kind}</span>
          {archived && <Badge tone="gray">보관</Badge>}
        </div>
        <p className="note-text">{note.text}</p>
        <p className="muted small">
          {FEEDBACK_SOURCE[note.source] ?? note.source} · {note.created_by} · {kstDateTime(note.created_at)} · {note.applied_count}회 반영
        </p>
        {err && <p className="notice err" role="alert">{err}</p>}
      </div>
      <div className="note-actions">
        <button type="button" className="btn sm" onClick={start}>수정</button>
        <button type="button" className="btn sm" disabled={toggle.busy} onClick={() => toggle.run()}>{archived ? "복원" : "보관"}</button>
        <ConfirmButton className="btn sm" label="삭제" confirmLabel="삭제 확정" disabled={remove.busy} onConfirm={() => remove.run()} />
      </div>
    </li>
  );
}

const SCOPE_CHIPS: [FeedbackScope | "", string][] = [["", "모든 범위"], ["global", "전체"], ["industry", "업종"], ["lead", "고객"]];

/** 피드백 메모리: 승인자가 남긴 규칙을 보고 고친다. 에이전트가 다음 글을 쓸 때 반영한다. */
export function MemoryPanel() {
  const q = useQuery(() => api.feedback({ status: "all" }), []);
  const [scope, setScope] = useState<FeedbackScope | "">("");
  const [status, setStatus] = useState<FeedbackStatus>("active");
  const [term, setTerm] = useState("");
  const [adding, setAdding] = useState(false);

  if (q.loading && !q.data) return <Loading />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.reload} />;
  const all = q.data ?? [];
  const shown = filterNotes(all, { scope, status, term });
  const groups = groupNotes(shown);

  return (
    <div className="memory">
      <p className="muted">{MEMORY_INTRO}</p>

      <div className="memory-bar">
        <div className="chips" role="radiogroup" aria-label="상태 거르기">
          {([["active", "사용 중"], ["archived", "보관"]] as [FeedbackStatus, string][]).map(([s, label]) => (
            <button type="button" key={s} role="radio" aria-checked={status === s} className={`chip ${status === s ? "on" : ""}`} onClick={() => setStatus(s)}>
              {label} {countNotes(all, s, scope)}
            </button>
          ))}
        </div>
        <div className="chips" role="radiogroup" aria-label="범위 거르기">
          {SCOPE_CHIPS.map(([s, label]) => (
            <button type="button" key={s || "all"} role="radio" aria-checked={scope === s} className={`chip ${scope === s ? "on" : ""}`} onClick={() => setScope(s)}>
              {label} {countNotes(all, status, s)}
            </button>
          ))}
        </div>
        <input className="search" placeholder="메모 검색" aria-label="메모 검색" value={term} onChange={(e) => setTerm(e.target.value)} />
        <button type="button" className="btn primary sm memory-add" aria-expanded={adding} onClick={() => setAdding(!adding)}>{adding ? "추가 닫기" : "메모 추가"}</button>
      </div>

      {adding && <Card title="메모 추가"><NoteForm onSaved={() => setAdding(false)} onCancel={() => setAdding(false)} /></Card>}

      {all.length === 0 ? (
        <Empty title="아직 남긴 메모가 없습니다">{MEMORY_INTRO}</Empty>
      ) : shown.length === 0 ? (
        <Empty title="조건에 맞는 메모가 없습니다">범위나 상태를 바꿔 보세요.</Empty>
      ) : groups.map((g) => (
        <section key={g.scope} className="note-group" aria-label={`${FEEDBACK_SCOPE[g.scope].label} 범위 메모`}>
          <h3><Badge tone={FEEDBACK_SCOPE[g.scope].tone}>{FEEDBACK_SCOPE[g.scope].label}</Badge><span className="count">{g.notes.length}</span></h3>
          <ul className="note-list">{g.notes.map((n) => <NoteRow key={n.id} note={n} />)}</ul>
        </section>
      ))}
    </div>
  );
}

/** 고객 상세 안의 작은 메모 구역: 이 고객에게만 적용되는 메모와 빠른 추가 */
export function LeadNotes({ leadId, company }: { leadId: string; company: string }) {
  const q = useQuery(() => api.feedback({ lead_id: leadId, status: "active" }), [leadId]);
  const memoryPath = useMemoryPath();
  const [adding, setAdding] = useState(false);
  const notes = q.data ?? [];
  return (
    <Card className="lead-notes" title={<>이 고객 메모 <span className="count">{notes.length}</span></>}
      aside={<span className="lead-notes-aside">
        <Link to={memoryPath} className="small">메모리 전체 보기</Link>
        <button type="button" className="btn sm" aria-expanded={adding} onClick={() => setAdding(!adding)}>{adding ? "닫기" : "메모 추가"}</button>
      </span>}>
      {q.loading && !q.data ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> : (
        <>
          {notes.length === 0
            ? <p className="muted">{company}에게만 적용되는 메모가 없습니다. 업종과 전체 메모는 이 고객에게도 함께 적용됩니다.</p>
            : <ul className="note-list compact">{notes.map((n) => <NoteRow key={n.id} note={n} />)}</ul>}
          {adding && <NoteForm fixedLead={{ id: leadId, label: company }} onSaved={() => setAdding(false)} onCancel={() => setAdding(false)} />}
        </>
      )}
    </Card>
  );
}
