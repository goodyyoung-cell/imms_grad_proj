const KST = "Asia/Seoul";

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function kstDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  if (!d) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: KST, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
}

export function kstDate(iso: string | null | undefined): string {
  const d = parse(iso);
  if (!d) return "-";
  return new Intl.DateTimeFormat("ko-KR", { timeZone: KST, year: "numeric", month: "numeric", day: "numeric" }).format(d);
}

export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parse(iso);
  if (!d) return "-";
  const sec = Math.round((now.getTime() - d.getTime()) / 1000);
  const abs = Math.abs(sec);
  const unit = abs < 3600 ? [Math.max(1, Math.round(abs / 60)), "분"] : abs < 86400 ? [Math.round(abs / 3600), "시간"] : [Math.round(abs / 86400), "일"];
  return sec >= 0 ? `${unit[0]}${unit[1]} 전` : `${unit[0]}${unit[1]} 후`;
}

/** 전화 예정 시각 표현. 지났으면 "지금 걸 차례" */
export function dueLabel(iso: string, now: Date = new Date()): { text: string; overdue: boolean } {
  const d = parse(iso);
  if (!d) return { text: "-", overdue: false };
  const overdue = d.getTime() <= now.getTime();
  return { text: overdue ? `예정 시각 지남 (${kstDateTime(iso)})` : `${kstDateTime(iso)} 예정`, overdue };
}

export function durationMs(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}초`;
}

export function runSeconds(start: string, end: string | null): string {
  const a = parse(start), b = parse(end);
  if (!a || !b) return "-";
  return `${Math.max(1, Math.round((b.getTime() - a.getTime()) / 1000))}초`;
}

export function shortModel(model: string): string {
  return model.replace(/^claude:/, "Claude ").replace(/^gemini:/, "Gemini ");
}

export function domainOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

export function initials(name: string | null | undefined): string {
  return (name ?? "?").trim().slice(0, 1) || "?";
}
