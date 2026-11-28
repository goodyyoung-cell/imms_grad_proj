// 소식 기반 초안과 피드백 메모리용 모의 데이터. fixtures.json(실제 응답 일부)의 승인 대기 메일에 후보 제목, 소재, 반영한 피드백을 덧붙인다.
// 회사는 fixtures.json과 같은 상장사, 담당자·연락처는 가상이다.
import type { FeedbackApplied, FeedbackNote, Hook, Industry, LeadDetail, Message, SubjectChoice, SubjectOption } from "../types";

export const INDUSTRIES: Industry[] = [
  { key: "semiconductor", label: "반도체" },
  { key: "battery", label: "2차전지" },
  { key: "bio_pharma", label: "바이오·제약" },
  { key: "auto_mobility", label: "자동차·모빌리티" },
  { key: "finance", label: "금융" },
  { key: "retail_commerce", label: "유통·커머스" },
  { key: "game_entertainment", label: "게임·엔터" },
  { key: "software_it", label: "소프트웨어·IT" },
  { key: "manufacturing", label: "제조" },
  { key: "construction_realestate", label: "건설·부동산" },
  { key: "food_consumer", label: "식품·소비재" },
  { key: "energy_chemical", label: "에너지·화학" },
  { key: "telecom", label: "통신" },
];

const LEAD = {
  alteogen: "4bec9642-a776-40b9-9e78-e3bf01f51861",
  ecopro: "76d081b3-969b-41fb-8863-0c9c4b568378",
};

export const SEED_NOTES: FeedbackNote[] = [
  { id: "fb-seed-1", scope: "global", scope_key: null, scope_label: "전체", kind: "tone", text: "존댓말을 쓰고 과장된 표현은 쓰지 않는다.",
    source: "manual", created_by: "민수", status: "active", applied_count: 14, created_at: "2026-11-20T01:20:00+00:00" },
  { id: "fb-seed-2", scope: "global", scope_key: null, scope_label: "전체", kind: "length", text: "제목은 30자 안으로 쓴다.",
    source: "chat_web", created_by: "영훈", status: "active", applied_count: 9, created_at: "2026-11-24T05:42:00+00:00" },
  { id: "fb-seed-3", scope: "industry", scope_key: "semiconductor", scope_label: "반도체", kind: "topic", text: "반도체 고객에게는 공정 투자와 가동률 소식을 오프닝 소재로 쓴다.",
    source: "chat_slack", created_by: "지민", status: "active", applied_count: 4, created_at: "2026-11-25T02:10:00+00:00" },
  { id: "fb-seed-4", scope: "industry", scope_key: "battery", scope_label: "2차전지", kind: "avoid", text: "2차전지 고객에게는 화재와 리콜 관련 보도를 언급하지 않는다.",
    source: "chat_web", created_by: "영훈", status: "active", applied_count: 3, created_at: "2026-11-25T07:35:00+00:00" },
  { id: "fb-seed-5", scope: "lead", scope_key: LEAD.alteogen, scope_label: "알테오젠", kind: "length", text: "임원에게는 첫 문단을 세 문장 안으로 짧게 쓴다.",
    source: "chat_web", created_by: "민수", status: "active", applied_count: 2, created_at: "2026-11-26T04:05:00+00:00" },
  { id: "fb-seed-6", scope: "lead", scope_key: LEAD.ecopro, scope_label: "에코프로비엠", kind: "avoid", text: "헝가리 사업장 제조 중단 보도는 메일에 쓰지 않는다.",
    source: "manual", created_by: "지민", status: "active", applied_count: 1, created_at: "2026-11-26T08:50:00+00:00" },
  { id: "fb-seed-7", scope: "industry", scope_key: "food_consumer", scope_label: "식품·소비재", kind: "topic", text: "식품 고객에게는 수출 소식을 오프닝 소재로 쓴다.",
    source: "manual", created_by: "민수", status: "archived", applied_count: 5, created_at: "2026-11-18T03:00:00+00:00" },
];

const applied = (...ids: string[]): FeedbackApplied[] => ids.map((id) => {
  const n = SEED_NOTES.find((x) => x.id === id)!;
  return { id: n.id, scope: n.scope, scope_key: n.scope_key, scope_label: n.scope_label, kind: n.kind, text: n.text };
});

interface Overlay {
  options: { company: string; hook: string };
  choice: SubjectChoice;
  hook: Hook | null;
  feedback: FeedbackApplied[];
  /** 챗으로 다시 쓰기 전의 이전 버전. 본문은 현재 본문을 조금 바꿔 만든다 */
  revision?: { by: string; instruction: string; at: string; replace: [string, string][] };
}

// 키는 fixtures.json의 승인 대기 메일 id
const OVERLAYS: Record<string, Overlay> = {
  // 리노공업: 웹 가격 문의
  "589e0a77-9255-4317-b758-15eaf2f00f93": {
    options: { company: "[노바웍스] 리노공업 노바데스크 가격 문의 안내", hook: "[노바웍스] 신공장 투자 시기에 맞춘 가격 안내드립니다" },
    choice: "general",
    hook: { type: "company", summary: "1,717억 원 규모 신공장 투자와 2026-11-10 신규시설투자 공시. 사업을 넓히는 시기에는 고객 문의도 늘 수 있습니다.",
      source_url: "http://www.thevaluenews.co.kr/news/view.php?idx=203595", source_title: "더밸류뉴스 보도 (2026-11-24)" },
    feedback: applied("fb-seed-1", "fb-seed-2", "fb-seed-3"),
  },
  // 알테오젠: 데모 요청. 소식 반영 제목을 골랐고 챗으로 한 번 다시 썼다
  "de414eb5-c537-4e39-be97-2743e98f3554": {
    options: { company: "[노바데스크] 알테오젠 상담원별 답변 품질 차이 관련 데모 요청 건", hook: "[노바데스크] 합병 이후 응대 기준 정리 관련 데모 요청 건" },
    choice: "hook",
    hook: { type: "company", summary: "2026-10 알테오젠바이오로직스 소규모합병 결정과 3분기 역대 최대 실적. 영업·마케팅 역량을 내재화하는 시기입니다.",
      source_url: "https://alteogen.com", source_title: "알테오젠 홈페이지 공시 요약" },
    feedback: applied("fb-seed-1", "fb-seed-2", "fb-seed-5"),
    revision: { by: "영훈", instruction: "첫 문장을 더 짧게, 담당자 소개를 넣어서 다시 써줘", at: "2026-11-27T00:12:00+00:00",
      replace: [["오지민 상무님. 노바웍스입니다.", "오지민 상무님. 노바웍스의 노바데스크 담당자입니다."],
        ["또 응대 지표 대시보드에서 첫 응답 시간, 처리 시간, 재문의율을 확인할 수 있습니다.", "응대 지표 대시보드로 첫 응답 시간, 처리 시간, 재문의율 같은 지표도 한눈에 확인하실 수 있습니다."]] },
  },
  // 에코프로비엠: 데모 요청. 소재는 문의 사유
  "65ba39fe-4485-48c9-8bd6-eccdfba77f77": {
    options: { company: "[노바데스크] 에코프로비엠 성수기 문의 급증 대응 관련 데모 요청 회신", hook: "[노바데스크] 성수기 문의 급증 대응, 데모 일정 안내드립니다" },
    choice: "general",
    hook: { type: "inquiry", summary: "데모 요청 메모: 성수기 문의 급증 대응 방안을 찾고 있습니다.", source_url: null, source_title: null },
    feedback: applied("fb-seed-1", "fb-seed-4", "fb-seed-6"),
  },
  // 삼양식품: 웹 가격 문의
  "c93f03db-e682-48b3-8ad6-2a9b4bdf43f3": {
    options: { company: "[노바데스크] 삼양식품 문의하신 가격 안내드립니다", hook: "[노바데스크] 중남미 판로 확대 시기, 문의 응대와 가격 안내드립니다" },
    choice: "general",
    hook: { type: "company", summary: "멕시코·브라질 등 중남미에서 불닭볶음면 판로를 넓히고 있다는 2026-11-26 보도. 해외 판매가 커질수록 문의 분류가 중요해집니다.",
      source_url: "https://www.businesspost.co.kr/BP?command=article_view&num=453051", source_title: "비즈니스포스트 보도 (2026-11-26)" },
    feedback: applied("fb-seed-1", "fb-seed-2"),
  },
  // 나노신소재: 웨비나 문의. 소재는 업종 이슈
  "13cc01d7-f287-42eb-952c-929587a7aab3": {
    options: { company: "나노신소재 웨비나에서 문의하신 상담 자동화 사례 공유드립니다", hook: "CNT 도전재 수요 확대 시기, 상담 자동화 사례 공유드립니다" },
    choice: "general",
    hook: { type: "industry", summary: "2차전지 소재 업종: CNT 도전재와 태양전지 소재 수요 확대 보도가 이어지고 있습니다.",
      source_url: "https://dealsite.co.kr/articles/173571", source_title: "딜사이트 보도 (2026-11-26)" },
    feedback: applied("fb-seed-1", "fb-seed-4"),
  },
  // LG에너지솔루션(3b4c9395...)은 일부러 비워 둔다: 후보 제목이 없는 오래된 초안 (화면에 아무것도 덧붙지 않아야 한다)
};

/** 승인 대기 메일에 후보 제목, 소재, 반영한 피드백, 이전 버전을 붙인다 */
export function decorateMessages(details: Record<string, LeadDetail>) {
  for (const d of Object.values(details)) {
    for (const m of d.messages as Message[]) {
      const o = OVERLAYS[m.id];
      if (!o) continue;
      const general = m.subject;
      const options: SubjectOption[] = [
        { id: "general", label: "일반 제목", subject: general },
        { id: "company", label: "회사명 포함", subject: o.options.company },
        { id: "hook", label: "소식 반영", subject: o.options.hook },
      ];
      const original = m.body;
      m.subject_options = options;
      m.subject_choice = o.choice;
      m.subject = options.find((x) => x.id === o.choice)!.subject;
      m.hook = o.hook;
      m.feedback_applied = o.feedback;
      m.revisions = [];
      if (o.revision) {
        const before = o.revision.replace.reduce((acc, [from, to]) => acc.replace(from, to), original);
        m.revisions.push({ at: o.revision.at, by: o.revision.by, instruction: o.revision.instruction, subject: general, body: before });
      }
    }
  }
}
