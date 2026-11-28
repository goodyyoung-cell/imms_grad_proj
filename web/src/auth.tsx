import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, mockMode, session, setUnauthorizedHandler } from "./api/client";

interface AuthState {
  name: string | null;
  mock: boolean;
  login: (name: string, password: string) => Promise<void>;
  startMock: () => void;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [name, setName] = useState<string | null>(session.name);
  const [mock, setMock] = useState(mockMode.on);

  const logout = useCallback(() => { session.clear(); mockMode.set(false); setName(null); setMock(false); }, []);
  useEffect(() => { setUnauthorizedHandler(() => { setName(null); }); }, []);

  const value = useMemo<AuthState>(() => ({
    name, mock, logout,
    login: async (n, pw) => {
      const r = await api.login(n.trim(), pw);
      session.set(r.token, r.name);
      setName(r.name);
    },
    startMock: () => { mockMode.set(true); session.set("mock", "시연"); setMock(true); setName("시연"); },
  }), [name, mock, logout]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider가 필요합니다");
  return v;
}
