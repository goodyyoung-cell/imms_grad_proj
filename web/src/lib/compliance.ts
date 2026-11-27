export type CheckState = "ok" | "warn" | "info";
export interface Check { key: string; label: string; state: CheckState; detail?: string }

const norm = (s: string) => s.replace(/[\s,]/g, "");

/** "29,000원", "14일", "15%" 같은 수치 표현을 뽑는다 */
export function extractNumbers(text: string): string[] {
  const found = text.match(/\d[\d,.]*\s?(?:원|%|명|개|건|일|배|억|만|개월|년)/g) ?? [];
  return [...new Set(found.map((x) => x.trim()))];
}

/** 메일에 나온 수치 중 근거 자료에서 찾지 못한 것 */
export function unsupportedNumbers(body: string, support: string[]): string[] {
  const hay = norm(support.join("\n"));
  return extractNumbers(body).filter((n) => !hay.includes(norm(n)));
}

/** 발송 전에 사람이 놓치기 쉬운 규정·근거 항목을 기계적으로 점검한다 (최종 판단은 담당자) */
export function complianceChecks(body: string, support: string[] | null): Check[] {
  const checks: Check[] = [];
  checks.push(/수신\s?거부|unsubscribe/i.test(body)
    ? { key: "optout", label: "수신 거부 방법 안내", state: "ok" }
    : { key: "optout", label: "수신 거부 방법이 없습니다", state: "warn", detail: "본문에 수신 거부 방법이 있어야 합니다." });
  checks.push(/AI/.test(body) && /(작성|도움|생성)/.test(body)
    ? { key: "ai", label: "AI 사용 고지", state: "ok" }
    : { key: "ai", label: "AI 사용 고지가 없습니다", state: "warn", detail: "AI의 도움을 받아 작성했다는 문구를 넣어야 합니다." });
  if (support === null) {
    checks.push({ key: "numbers", label: "수치 근거 확인은 승인 자료를 불러온 뒤 가능합니다", state: "info" });
  } else {
    const bad = unsupportedNumbers(body, support);
    checks.push(bad.length === 0
      ? { key: "numbers", label: "본문 수치가 모두 근거 자료에 있음", state: "ok" }
      : { key: "numbers", label: `근거에서 찾지 못한 수치 ${bad.length}건`, state: "warn", detail: `${bad.join(", ")} (승인 자료나 조사 사실에 없습니다. 출처를 확인하세요.)` });
  }
  return checks;
}
