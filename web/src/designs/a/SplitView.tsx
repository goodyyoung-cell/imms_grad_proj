import { useEffect, type ReactNode } from "react";

/** 왼쪽 목록 + 오른쪽 상세. 좁은 화면에서는 한 번에 하나만 보여 준다. */
export function SplitView({ list, detail, hasDetail, listTitle }: { list: ReactNode; detail: ReactNode; hasDetail: boolean; listTitle: ReactNode }) {
  return (
    <div className={`split ${hasDetail ? "show-detail" : "show-list"}`}>
      <aside className="split-list" aria-label="목록">
        <div className="split-list-head">{listTitle}</div>
        <div className="split-list-body">{list}</div>
      </aside>
      <div className="split-detail">{detail}</div>
    </div>
  );
}

/** J/K로 목록을 오르내린다 */
export function useListKeys(ids: string[], current: string | undefined, go: (id: string) => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (/input|textarea|select/i.test(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      const i = current ? ids.indexOf(current) : -1;
      if (e.key === "j" && i < ids.length - 1) go(ids[i + 1]);
      if (e.key === "k" && i > 0) go(ids[i - 1]);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [ids, current, go]);
}
