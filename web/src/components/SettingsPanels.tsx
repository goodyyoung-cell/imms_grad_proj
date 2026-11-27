import { useState } from "react";
import { api } from "../api/client";
import { useAction, useQuery } from "../api/hooks";
import { useToast } from "../toast";
import { Badge, Card, Empty, ErrorBox, Loading } from "./ui";

/** 승인 자료: 에이전트가 고객에게 말할 수 있는 내용의 전부. 여기에 없는 주장은 메일에 쓰지 못한다. */
export function KbPanel() {
  const q = useQuery(() => api.kb(), []);
  const [term, setTerm] = useState("");
  if (q.loading && !q.data) return <Loading />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.reload} />;
  const t = term.trim();
  const docs = (q.data ?? []).map((d) => ({ ...d, sections: d.sections.filter((s) => !t || (s.heading + s.text).includes(t)) })).filter((d) => d.sections.length);
  return (
    <div className="kb">
      <p className="muted">메일 초안은 이 자료와 출처가 있는 조사 사실만 근거로 씁니다. 자료가 틀리면 메일도 틀리므로, 가격이나 기능이 바뀌면 여기부터 고칩니다.</p>
      <input className="search" placeholder="자료 안에서 검색" value={term} onChange={(e) => setTerm(e.target.value)} aria-label="승인 자료 검색" />
      {docs.length === 0 ? <Empty title="찾는 내용이 없습니다" /> : docs.map((d) => (
        <Card key={d.id} title={<>{d.title} <span className="muted small">{d.id}</span></>}>
          <ul className="kb-sections">{d.sections.map((s) => <li key={s.id}><h5>{s.heading}</h5><p>{s.text}</p></li>)}</ul>
        </Card>
      ))}
    </div>
  );
}

const RULES = [
  "내부 작업(조사, 점수, 초안)은 에이전트가 자율로 하고, 고객에게 나가는 행동은 모두 사람이 승인합니다.",
  "점수는 규칙으로 계산합니다. AI는 점수를 바꾸지 않고 근거만 설명합니다.",
  "출처가 없는 사실은 메일에 쓰지 않고 버립니다.",
  "수신 거부 의사가 확인되면 남은 메일과 전화를 모두 취소합니다.",
  "판단이 애매하면 에이전트가 결정하지 않고 사람에게 넘깁니다.",
  "모든 에이전트 실행은 단계별로 기록되며 화면에서 다시 볼 수 있습니다.",
];

/** 운영 설정·상태: 안전장치, 연동 상태, 연결 시험. 연동을 켠 날 가장 먼저 확인하는 화면 */
export function StatusPanel() {
  const toast = useToast();
  const q = useQuery(() => api.status(), [], { refreshMs: 30000 });
  const unsub = useQuery(() => api.leads({ status: "unsubscribed", limit: 100 }), []);
  const test = useAction(async () => {
    const r = await api.notifyTest();
    toast(r.posted ? "Slack 채널로 시험 메시지를 보냈습니다." : (r.reason ?? "보내지 못했습니다."), r.posted ? "ok" : "err");
  });

  const s = q.data;
  return (
    <>
      <Card title="운영 원칙">
        <ul>{RULES.map((r) => <li key={r}>{r}</li>)}</ul>
      </Card>

      {q.loading && !s ? <Loading /> : q.error ? <ErrorBox message={q.error} onRetry={q.reload} /> : s && (
        <div className="two-col">
          <Card title="발송과 모델">
            <dl className="kv">
              <dt>발송 방식</dt><dd><Badge tone={s.mode.send === "sandbox" ? "amber" : "red"}>{s.mode.send === "sandbox" ? "샌드박스 (실제 고객에게 나가지 않음)" : s.mode.send}</Badge></dd>
              <dt>기본 모델</dt><dd>{s.mode.llm_default}</dd>
              <dt>모델 키</dt><dd>{Object.entries(s.mode.providers).map(([k, v]) => <Badge key={k} tone={v ? "green" : "gray"}>{k} {v ? "연결됨" : "없음"}</Badge>)}</dd>
              <dt>승인 자료</dt><dd>{s.knowledge_docs}개 문서</dd>
            </dl>
          </Card>
          <Card title="안전장치">
            <dl className="kv">
              <dt>오늘 발송</dt><dd>{s.safeguards.sent_today} / {s.safeguards.max_sends_per_day}건
                <span className="gauge"><i style={{ width: `${Math.min(100, (s.safeguards.sent_today / Math.max(1, s.safeguards.max_sends_per_day)) * 100)}%` }} /></span></dd>
              <dt>수신 동의 확인</dt><dd>{s.safeguards.require_consent ? <Badge tone="green">동의 기록이 있어야 발송</Badge> : <Badge tone="gray">끔</Badge>}</dd>
              <dt>발송자 정보</dt><dd>{s.safeguards.sender_name ?? "-"} · {s.safeguards.sender_contact ?? "-"}</dd>
            </dl>
          </Card>
          <Card title="Slack 승인 채널" aside={<button className="btn sm" disabled={test.busy || !s.slack.configured} onClick={() => test.run()}>{test.busy ? "보내는 중" : "연결 시험"}</button>}>
            <dl className="kv">
              <dt>연결</dt><dd><Badge tone={s.slack.configured ? "green" : "amber"}>{s.slack.configured ? "연결됨" : "설정 필요"}</Badge></dd>
              <dt>서명 검증</dt><dd><Badge tone={s.slack.signing_configured ? "green" : "amber"}>{s.slack.signing_configured ? "설정됨" : "설정 필요"}</Badge></dd>
              <dt>채널</dt><dd>{s.slack.channel ?? "-"}</dd>
              <dt>승인 가능한 사람</dt><dd>{s.slack.approvers}명</dd>
              <dt>알림 종류</dt><dd className="tags">{s.slack.notify_kinds.map((k) => <span key={k} className="tag">{k}</span>)}</dd>
              <dt>화면 주소</dt><dd>{s.slack.web_url ?? "-"}</dd>
            </dl>
            {!s.slack.configured && <p className="muted small">토큰과 채널을 Cloud Run에 등록하면 연결 시험을 할 수 있습니다. 방법은 docs/admin-slack.md에 있습니다.</p>}
          </Card>
        </div>
      )}

      <Card title="수신 거부 명단" aside={<span className="count">{unsub.data?.total ?? 0}</span>}>
        {unsub.loading && !unsub.data ? <Loading /> : unsub.error ? <ErrorBox message={unsub.error} onRetry={unsub.reload} /> :
          (unsub.data?.leads.length ?? 0) === 0 ? <p className="muted">수신 거부한 고객이 없습니다.</p> :
          <table className="table"><thead><tr><th>회사</th><th>담당자</th><th>이메일</th></tr></thead>
            <tbody>{unsub.data!.leads.map((l) => <tr key={l.id}><td>{l.company_name}</td><td>{l.name}</td><td>{l.email}</td></tr>)}</tbody></table>}
        <p className="muted small">이 명단의 고객에게는 메일도 전화도 나가지 않습니다.</p>
      </Card>
    </>
  );
}
