import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { ApiError } from "./api/errors";

export function Login() {
  const { login, startMock } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [name, setName] = useState(localStorage.getItem("sdr_last_name") ?? "");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const from = (loc.state as { from?: string } | null)?.from;

  const go = () => nav(from ?? `/${localStorage.getItem("sdr_design") === "b" ? "b" : "a"}`, { replace: true });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(name, pw); localStorage.setItem("sdr_last_name", name.trim()); go(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "로그인하지 못했습니다"); }
    finally { setBusy(false); }
  };

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>SDR 에이전트 관리</h1>
        <p className="muted">에이전트가 준비한 메일과 전화를 확인하고 승인하는 화면입니다. 승인·통화 기록에는 아래 이름이 남습니다.</p>
        <label>이름<input value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" maxLength={30} required autoFocus /></label>
        <label>비밀번호<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        {error && <p className="notice err" role="alert">{error}</p>}
        <button className="btn primary block" disabled={busy || !name.trim() || !pw}>{busy ? "확인 중" : "로그인"}</button>
        <button type="button" className="btn ghost block" onClick={() => { startMock(); go(); }}>서버 없이 시연 데이터로 보기</button>
      </form>
    </main>
  );
}
