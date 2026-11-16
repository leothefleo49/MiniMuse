import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "../i18n";

interface Props {
  children: ReactNode;
  scope?: string;
}

interface State {
  error: Error | null;
}

/** Prevents uncaught render or lazy-chunk errors from leaving an empty background-only screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ErrorBoundary${this.props.scope ? `:${this.props.scope}` : ""}]`, error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    const msg = this.state.error.message || t("Something unexpected interrupted this view.");
    const isChunk = /chunk|loading css|dynamically imported module|failed to fetch/i.test(msg);
    return (
      <div className="flex min-h-[60vh] w-full items-center justify-center bg-bg px-5 py-10 text-fg">
        <div className="w-full max-w-md rounded-3xl border border-border/80 bg-surface p-6 text-center shadow-sm">
          <h2 className="text-[18px] font-bold">
            {isChunk ? t("App updated or connection reset") : t("Something interrupted the screen")}
          </h2>
          <p className="mt-2 text-[13.5px] leading-relaxed text-muted">{msg}</p>
          <div className="mt-5 flex flex-wrap justify-center gap-2.5">
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="rounded-2xl bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-fg"
            >
              {t("Try again")}
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-2xl border border-border px-4 py-2 text-[13.5px] font-medium text-muted hover:text-fg"
            >
              {t("Reload app")}
            </button>
          </div>
        </div>
      </div>
    );
  }
}
