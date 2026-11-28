import type { LeadStatus, MessageStatus, Outcome, ReplyClass, Workflow } from "../types";

export const LEAD_STATUS: Record<LeadStatus, { label: string; hint: string; tone: Tone }> = {
  new: { label: "신규", hint: "조사 대기", tone: "gray" },
  researching: { label: "조사 중", hint: "에이전트가 조사하고 있습니다", tone: "blue" },
  researched: { label: "조사 완료", hint: "초안 대기", tone: "blue" },
  awaiting_approval: { label: "승인 대기", hint: "사람이 볼 차례", tone: "amber" },
  contacted: { label: "연락함", hint: "메일 발송됨", tone: "green" },
  replied: { label: "답장 받음", hint: "나중에 연락 등", tone: "green" },
  needs_human: { label: "사람 확인 필요", hint: "애매함·반려·수신 거부 의심", tone: "red" },
  handed_off: { label: "인계 완료", hint: "인간 SDR에게 전달", tone: "violet" },
  unsubscribed: { label: "수신 거부", hint: "모든 연락 중단", tone: "gray" },
};

export type Tone = "gray" | "blue" | "amber" | "green" | "red" | "violet";

// 파이프라인 순서. 에이전트가 하는 단계와 사람이 하는 단계를 구분한다.
export const PIPELINE: { status: LeadStatus; actor: "agent" | "human" }[] = [
  { status: "new", actor: "agent" },
  { status: "researched", actor: "agent" },
  { status: "awaiting_approval", actor: "human" },
  { status: "contacted", actor: "human" },
  { status: "replied", actor: "agent" },
  { status: "needs_human", actor: "human" },
  { status: "handed_off", actor: "human" },
];

export const MESSAGE_STATUS: Record<MessageStatus, { label: string; tone: Tone }> = {
  pending_approval: { label: "승인 대기", tone: "amber" },
  approved: { label: "승인됨", tone: "blue" },
  sent: { label: "발송됨(샌드박스)", tone: "green" },
  rejected: { label: "반려", tone: "red" },
  cancelled: { label: "취소(수신 거부)", tone: "gray" },
};

export const WORKFLOW: Record<Workflow, string> = {
  research: "리서치",
  draft: "메일 초안",
  reply_triage: "답장 분류",
  call_script: "콜 스크립트",
  handoff: "인계 요약",
  draft_revise: "초안 다시 쓰기",
  learn_feedback: "피드백 학습",
};

export const LESSON_SOURCE: Record<string, string> = { edit: "수정 승인", reject: "반려", chat: "대화형 피드백" };

export const SOURCE: Record<string, string> = {
  demo_request: "데모 요청",
  web_inquiry: "웹 문의",
  seminar: "세미나",
  business_card: "명함",
  other: "기타",
};

export const OUTCOME: Record<Outcome, { label: string; next: string; tone: Tone }> = {
  interested: { label: "관심 있음", next: "인계 요약이 만들어집니다", tone: "green" },
  not_now: { label: "지금은 어려움", next: "나중에 다시 확인합니다", tone: "gray" },
  needs_info: { label: "자료 필요", next: "담당자 확인 목록으로 갑니다", tone: "amber" },
  no_answer: { label: "부재중", next: "상태는 그대로, 메일 회신을 기다립니다", tone: "gray" },
  voicemail: { label: "음성 사서함", next: "상태는 그대로, 메일 회신을 기다립니다", tone: "gray" },
  wrong_contact: { label: "담당자 아님", next: "담당자 확인 목록으로 갑니다", tone: "amber" },
  unsubscribe: { label: "연락 거부", next: "이 고객의 모든 연락을 중단합니다", tone: "red" },
};

export const REPLY_CLASS: Record<ReplyClass, { label: string; tone: Tone }> = {
  interested: { label: "관심 표명", tone: "green" },
  question: { label: "질문", tone: "blue" },
  not_now: { label: "나중에", tone: "gray" },
  not_decision_maker: { label: "담당자 아님", tone: "amber" },
  out_of_office: { label: "부재 자동응답", tone: "gray" },
  unsubscribe: { label: "수신 거부", tone: "red" },
  unclear: { label: "판단 불명확", tone: "amber" },
};

export const NOTICE: Record<string, string> = {
  reply: "고객이 답장했습니다",
  handoff: "인계된 고객입니다",
  unsubscribe_suspected: "수신 거부가 의심됩니다. 통화를 보류하세요",
};

export const REASON_CODES: { value: string; label: string }[] = [
  { value: "factual_error", label: "사실과 다른 내용" },
  { value: "tone", label: "어조가 어색함" },
  { value: "wrong_target", label: "대상이 맞지 않음" },
  { value: "forbidden_phrase", label: "금지 표현" },
  { value: "other", label: "기타" },
];

export const FACT_FIELD: Record<string, string> = {
  summary: "기업 개요",
  revenue: "매출",
  operating_margin: "영업이익률",
  recent_news: "최근 소식",
  products: "제품·서비스",
  customers: "고객·거래처",
  employees: "임직원",
  talking_point: "대화 소재",
};

export const SOURCE_TYPE: Record<string, string> = {
  dart: "DART 공시",
  homepage: "홈페이지",
  news: "뉴스",
  naver_news: "네이버 뉴스",
  google_news: "Google 뉴스",
  web: "웹",
};

export const SCORE_PART: Record<string, string> = {
  fit: "기업 적합도",
  title: "직급",
  signal: "구매 신호",
  source: "유입 경로",
  elapsed: "경과 일수",
};

export const KB_DOC: Record<string, string> = {
  "01_product": "제품 소개",
  "02_pricing": "가격·플랜",
  "03_faq": "자주 묻는 질문",
  "04_objections": "반대 의견 응대",
  "05_compliance": "운영 원칙",
  "06_cases": "고객 사례(가상)",
};

export function kbLabel(id: string): string {
  const [doc, heading] = id.split("#");
  return `${KB_DOC[doc] ?? doc}${heading ? ` › ${heading}` : ""}`;
}
