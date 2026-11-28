import { useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction, useQuery } from "../api/hooks";
import { kstDateTime } from "../lib/format";
import { useToast } from "../toast";
import type { AppliedLesson, Message } from "../types";

/** 대화형 피드백: 승인 전 초안을 "이렇게 고쳐 줘"라고 말해 다시 쓰게 하고, 원하면 다음 초안부터 쓸 작성 지침으로 남긴다.
 *  다시 써도 발송되지 않는다 (여전히 승인 대기). 백엔드: POST /admin/messages/{id}/revise */
export function FeedbackBox({ message, disabled }: { message: Message; disabled?: boolean }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [save, setSave] = useState(true);
  const history = useQuery(() => api.revisions(message.id), [message.id]);

  const revise = useAction(async () => {
    const r = await api.revise(message.id, text.trim(), save);
    if (r.status !== "revised") {
      toast("다시 쓰지 못했습니다. 피드백을 조금 바꿔 다시 시도해 주세요.");
      return;
    }
    const saved = r.lessons?.saved?.length ?? 0;
    toast(`초안을 다시 썼습니다.${saved ? ` 작성 지침 ${saved}개를 배워 다음 초안부터 반영합니다.` : ""}`);
    setText("");
    invalidate();
  });

  if (message.kind !== "initial") return null;
  const items = history.data ?? [];

  return (
    <section className="feedback-box" aria-label="AI에게 고쳐 달라고 하기">
      <h4>AI에게 고쳐 달라고 하기</h4>
      {items.length > 0 && (
        <ol className="feedback-thread">
          {items.map((r) => (
            <li key={r.id}>
              <div className="bubble human"><span className="who">{r.requested_by}</span>{r.feedback}</div>
              <div className="bubble agent"><span className="who">AI</span>피드백대로 다시 썼습니다 · {kstDateTime(r.created_at)}</div>
            </li>
          ))}
        </ol>
      )}
      <textarea
        aria-label="초안에 대한 피드백"
        rows={2}
        value={text}
        maxLength={2000}
        disabled={disabled || revise.busy}
        placeholder="예: 더 짧게 써 줘. 회사 최근 소식으로 시작하고 가격 이야기는 빼 줘"
        onChange={(e) => setText(e.target.value)}
      />
      <div className="feedback-actions">
        <label className="small"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> 앞으로의 초안에도 반영 (작성 지침으로 저장)</label>
        <button className="btn" disabled={disabled || revise.busy || !text.trim()} onClick={() => revise.run()}>
          {revise.busy ? "다시 쓰는 중…" : "다시 쓰기"}
        </button>
      </div>
      {revise.error && <p className="notice err" role="alert">{revise.error}</p>}
      <p className="muted small">AI를 한 번 부릅니다 (수십 초). 다시 써도 발송되지 않고 승인을 기다립니다. 근거 규칙과 수신 거부 안내는 그대로 지켜집니다.</p>
    </section>
  );
}

/** 이 초안을 쓸 때 반영한 작성 지침 (지난 피드백에서 배운 것) */
export function AppliedLessons({ lessons }: { lessons?: AppliedLesson[] }) {
  if (!lessons?.length) return null;
  return (
    <details className="applied-lessons">
      <summary>지난 피드백에서 배운 작성 지침 {lessons.length}개를 반영한 초안입니다</summary>
      <ul>{lessons.map((l, i) => <li key={l.id ?? i}>{l.text}</li>)}</ul>
    </details>
  );
}
