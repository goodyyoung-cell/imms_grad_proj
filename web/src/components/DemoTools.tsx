import { useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction } from "../api/hooks";
import { useToast } from "../toast";
import type { LeadDetail } from "../types";
import { Collapsible, ConfirmButton } from "./ui";

/** 시연·점검용 동작. LLM 비용이 드니 한 번 더 확인한 뒤 실행한다. */
export function DemoTools({ detail }: { detail: LeadDetail }) {
  const toast = useToast();
  const id = detail.lead.id;
  const [reply, setReply] = useState("");
  const act = useAction(async (kind: "research" | "draft" | "handoff" | "reply") => {
    if (kind === "research") await api.research(id);
    if (kind === "draft") await api.draft(id);
    if (kind === "handoff") await api.handoff(id);
    if (kind === "reply") { await api.simulateReply(id, reply); setReply(""); }
    toast({ research: "리서치를 실행했습니다.", draft: "초안을 만들었습니다. 승인 목록에서 확인하세요.", handoff: "인계 요약을 만들었습니다.", reply: "모의 답장을 처리했습니다." }[kind]);
    invalidate();
  });
  return (
    <Collapsible title="시연 도구 (에이전트 직접 실행)">
      <p className="muted small">실제 LLM을 호출하므로 비용이 듭니다. 한 건씩 실행합니다.</p>
      <div className="tool-row">
        <ConfirmButton label="리서치 실행" confirmLabel="실행" onConfirm={() => act.run("research")} disabled={act.busy} />
        <ConfirmButton label="첫 메일 초안 만들기" confirmLabel="실행" onConfirm={() => act.run("draft")} disabled={act.busy} />
        <ConfirmButton label="인계 요약 만들기" confirmLabel="실행" onConfirm={() => act.run("handoff")} disabled={act.busy} />
      </div>
      <label className="wide">모의 답장 (고객이 보낸 것처럼 에이전트에 넣습니다)
        <textarea rows={2} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="예: 다음 주 화요일 오후에 미팅 가능합니다" />
      </label>
      <ConfirmButton label="모의 답장 처리" confirmLabel="실행" disabled={!reply.trim() || act.busy} onConfirm={() => act.run("reply")} />
      {act.error && <p className="notice err" role="alert">{act.error}</p>}
    </Collapsible>
  );
}
