import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./errors";

const bus = new EventTarget();
/** 쓰기 동작 뒤에 호출하면 화면의 모든 조회가 다시 불러온다 */
export function invalidate() { bus.dispatchEvent(new Event("invalidate")); }

export interface QueryState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useQuery<T>(fn: () => Promise<T>, deps: unknown[], opts: { refreshMs?: number; enabled?: boolean } = {}): QueryState<T> {
  const { refreshMs, enabled = true } = opts;
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(() => {
    if (!enabled) return;
    const id = ++seq.current;
    setLoading(true);
    fnRef.current()
      .then((d) => { if (id === seq.current) { setData(d); setError(null); } })
      .catch((e) => { if (id === seq.current) setError(e instanceof ApiError ? e.message : "불러오지 못했습니다"); })
      .finally(() => { if (id === seq.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  useEffect(() => { setData(undefined); run(); }, [run]);
  useEffect(() => {
    bus.addEventListener("invalidate", run);
    return () => bus.removeEventListener("invalidate", run);
  }, [run]);
  useEffect(() => {
    if (!refreshMs || !enabled) return;
    const t = setInterval(run, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs, run, enabled]);

  return { data, error, loading, reload: run };
}

/** 버튼 한 번에 한 번만 실행되게 하고 오류 문구를 돌려준다 */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (...a: A): Promise<R | undefined> => {
    if (busy) return undefined;
    setBusy(true); setError(null);
    try { return await fn(...a); }
    catch (e) { setError(e instanceof ApiError ? e.message : "요청에 실패했습니다"); return undefined; }
    finally { setBusy(false); }
  };
  return { run, busy, error, clearError: () => setError(null) };
}
