import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useQuery } from "../api/hooks";
import { LEAD_STATUS } from "../lib/labels";
import { Score } from "./ui";

/** "/" 또는 Ctrl+K로 어디서든 회사를 찾아 바로 연다 */
export function QuickSearch({ toLead }: { toLead: (id: string) => string }) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const nav = useNavigate();

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = /input|textarea|select/i.test(t.tagName);
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); setOpen(true); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  useEffect(() => { if (open) { setTerm(""); setCursor(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);
  useEffect(() => { const t = setTimeout(() => setDebounced(term.trim()), 200); return () => clearTimeout(t); }, [term]);

  const q = useQuery(() => api.leads({ q: debounced, limit: 8 }), [debounced], { enabled: open && debounced.length > 0 });
  const rows = debounced ? (q.data?.leads ?? []) : [];
  const go = (id: string) => { setOpen(false); nav(toLead(id)); };

  if (!open) return null;
  return (
    <div className="qs-wrap" role="dialog" aria-label="회사 검색">
      <div className="qs-scrim" onClick={() => setOpen(false)} />
      <div className="qs">
        <input ref={input} value={term} placeholder="회사명을 입력하세요 (Esc로 닫기)" aria-label="회사명 검색"
          onChange={(e) => { setTerm(e.target.value); setCursor(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setCursor(Math.min(cursor + 1, rows.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setCursor(Math.max(cursor - 1, 0)); }
            if (e.key === "Enter" && rows[cursor]) go(rows[cursor].id);
          }} />
        <ul>
          {rows.map((l, i) => (
            <li key={l.id}>
              <button className={i === cursor ? "on" : ""} onMouseEnter={() => setCursor(i)} onClick={() => go(l.id)}>
                <strong>{l.company_name}</strong><span className="muted"> {l.name} · {l.title}</span>
                <span className="qs-right"><Score value={l.score} showTags={false} /><span className="muted small">{LEAD_STATUS[l.status].label}</span></span>
              </button>
            </li>
          ))}
          {debounced && !q.loading && rows.length === 0 && <li className="muted qs-none">검색 결과가 없습니다</li>}
        </ul>
      </div>
    </div>
  );
}
