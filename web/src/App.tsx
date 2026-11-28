import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { Login } from "./Login";
import { DesignA } from "./designs/a/DesignA";
import { DesignB } from "./designs/b/DesignB";

export const DESIGN_KEY = "sdr_design";
export const currentDesign = (): "a" | "b" => (localStorage.getItem(DESIGN_KEY) === "b" ? "b" : "a");

/** Slack 알림의 링크(/m/…, /l/…, /c/…)를 지금 쓰는 디자인의 화면으로 보낸다 */
function DeepLink({ kind }: { kind: "m" | "l" | "c" }) {
  const { id } = useParams();
  const d = currentDesign();
  const to = d === "a"
    ? { m: `/a/approvals/${id}`, l: `/a/leads/${id}`, c: `/a/calls/${id}` }[kind]
    : { m: `/b/message/${id}`, l: `/b/lead/${id}`, c: `/b/call/${id}` }[kind];
  return <Navigate to={to} replace />;
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { name } = useAuth();
  const loc = useLocation();
  if (!name) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return children;
}

export default function App() {
  const { name } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={name ? <Navigate to={`/${currentDesign()}`} replace /> : <Login />} />
      <Route path="/m/:id" element={<RequireAuth><DeepLink kind="m" /></RequireAuth>} />
      <Route path="/l/:id" element={<RequireAuth><DeepLink kind="l" /></RequireAuth>} />
      <Route path="/c/:id" element={<RequireAuth><DeepLink kind="c" /></RequireAuth>} />
      <Route path="/a/*" element={<RequireAuth><DesignA /></RequireAuth>} />
      <Route path="/b/*" element={<RequireAuth><DesignB /></RequireAuth>} />
      <Route path="*" element={<Navigate to={`/${currentDesign()}`} replace />} />
    </Routes>
  );
}
