import { domainOf } from "../lib/format";
import { FACT_FIELD, kbLabel, SCORE_PART, SOURCE_TYPE } from "../lib/labels";
import type { Citation, Fact, Score } from "../types";

/** 메일이 어떤 근거로 쓰였는지. 사실은 출처 링크와 함께, 승인된 자료는 문서 이름으로 보여 준다. */
export function Evidence({ citations, facts }: { citations: Citation[]; facts: Fact[] }) {
  const byId = new Map(facts.map((f) => [f.id, f]));
  const used = citations.filter((c) => c.type === "fact").map((c) => byId.get(c.id)).filter((f): f is Fact => !!f);
  const kb = [...new Set(citations.filter((c) => c.type === "kb").map((c) => c.id))];
  const missing = citations.filter((c) => c.type === "fact" && !byId.has(c.id)).length;
  return (
    <div className="evidence">
      <h4>이 메일의 근거</h4>
      {used.length === 0 && kb.length === 0 && <p className="muted">연결된 근거가 없습니다. 내용을 직접 확인해 주세요.</p>}
      {used.length > 0 && (
        <ul className="fact-list">
          {used.map((f) => <FactItem key={f.id} fact={f} />)}
        </ul>
      )}
      {missing > 0 && <p className="muted">출처를 찾지 못한 근거 {missing}건</p>}
      {kb.length > 0 && (
        <>
          <h5>승인된 회사 자료</h5>
          <ul className="kb-list">{kb.map((id) => <li key={id}>{kbLabel(id)}</li>)}</ul>
        </>
      )}
    </div>
  );
}

export function FactItem({ fact }: { fact: Fact }) {
  return (
    <li className="fact">
      <span className="fact-field">{FACT_FIELD[fact.field] ?? fact.field}</span>
      <span className="fact-value">{fact.value}</span>
      {fact.source_url ? (
        <a className="fact-src" href={fact.source_url} target="_blank" rel="noreferrer noopener">
          {SOURCE_TYPE[fact.source_type] ?? fact.source_type} · {domainOf(fact.source_url)} ↗
        </a>
      ) : <span className="fact-src muted">출처 없음</span>}
    </li>
  );
}

export function FactList({ facts }: { facts: Fact[] }) {
  if (facts.length === 0) return <p className="muted">아직 조사한 사실이 없습니다.</p>;
  return <ul className="fact-list">{facts.map((f) => <FactItem key={f.id} fact={f} />)}</ul>;
}

export function ScoreBreakdown({ score }: { score: Score | null }) {
  if (!score) return <p className="muted">점수가 아직 없습니다.</p>;
  const parts = Object.entries(score.breakdown ?? {});
  return (
    <div className="breakdown">
      <div className="breakdown-total"><strong>{Math.round(score.score)}</strong><span>점 (규칙 기반)</span></div>
      {parts.length > 0 && (
        <ul>{parts.map(([k, v]) => <li key={k}><span>{SCORE_PART[k] ?? k}</span><span className="bar"><i style={{ width: `${Math.min(100, Number(v) * 3)}%` }} /></span><b>{v}</b></li>)}</ul>
      )}
      <p className="tags">{score.tags.map((t) => <span key={t} className="tag">{t}</span>)}</p>
    </div>
  );
}
