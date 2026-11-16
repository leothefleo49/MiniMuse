import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { androidApp } from "./android";
import { markDesktopShell } from "./desktop";
import { applyLang } from "./i18n";
import { StoreProvider } from "./store";
import { applyTheme } from "./theme";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";

applyLang();
applyTheme();
markDesktopShell();
if (androidApp()) document.documentElement.classList.add("android-shell");
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary scope="root">
      <StoreProvider>
        <App />
      </StoreProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
