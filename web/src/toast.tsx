import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

interface Toast { id: number; text: string; tone: "ok" | "err" }
const Ctx = createContext<(text: string, tone?: "ok" | "err") => void>(() => {});
let n = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "err" = "ok") => {
    const id = ++n;
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
