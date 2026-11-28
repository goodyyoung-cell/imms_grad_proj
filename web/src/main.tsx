import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { mockMode } from "./api/client";
import { resetMock } from "./api/mock";
import App from "./App";
import { AuthProvider } from "./auth";
import { ToastProvider } from "./toast";
import "./styles/base.css";
import "./styles/a.css";
import "./styles/b.css";

// ?mock=1 : 서버 없이 시연 데이터로 연다. ?mock=0 : 끈다.
const params = new URLSearchParams(location.search);
if (params.get("mock") === "1") { mockMode.set(true); resetMock(); sessionStorage.setItem("sdr_token", "mock"); sessionStorage.setItem("sdr_name", "시연"); }
if (params.get("mock") === "0") { mockMode.set(false); sessionStorage.removeItem("sdr_token"); sessionStorage.removeItem("sdr_name"); }

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
