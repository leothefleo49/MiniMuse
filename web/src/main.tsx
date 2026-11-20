import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { androidApp } from "./android";
import { initDebugLogCapture } from "./debugLog";
import { markDesktopShell } from "./desktop";
import { applyLang } from "./i18n";
import { StoreProvider } from "./store";
import { applyTheme } from "./theme";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";

initDebugLogCapture();
applyLang();
applyTheme();
markDesktopShell();
const android = androidApp();
if (android) {
  document.documentElement.classList.add("android-shell");
  if (android.isVrHeadset?.()) {
    document.documentElement.classList.add("vr-shell", "desktop");
  }
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary scope="root">
      <StoreProvider>
        <App />
      </StoreProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
