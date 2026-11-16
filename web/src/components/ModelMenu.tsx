import { Bot, Check, ChevronDown, Cpu, Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { useT } from "../i18n";
import { useClosable } from "../back";
import type { SavedModelOption } from "../types";
import { cx } from "../util";

/**
 * The model picker, by the agent's name in the chat header: a small pill with a caret
 * that opens the list of the runtime's aliases. The pick is sticky — it stays chosen for
 * every message until it is changed (or switched back to Default), not for one message
 * only. The values are the runtime's aliases (`service.py`'s SWITCH_WORDS plus the
 * cloud relay's "gemini"); Default is "" and sends nothing, so the configured model
 * answers.
 */

export const MODEL_KEY = "nanomuse_model_choice";

export const MODEL_CHOICES: { value: string; label: string; hint: string }[] = [
  { value: "", label: "Default", hint: "" },
  { value: "flash", label: "Flash", hint: "deepseek-flash, thinking off" },
  { value: "pro", label: "Pro", hint: "deepseek-v4-pro, thinking on" },
  { value: "local", label: "Local", hint: "qwen3.5:9b-32k, Ollama, this computer" },
  { value: "gemini", label: "Gemini", hint: "gemini-3.1-flash-lite, free tier, at most 200 a day" },
];

/** The sticky pick: "" when none (the configured model answers). */
export function readModelChoice(): string {
  try {
    return localStorage.getItem(MODEL_KEY) || "";
  } catch {
    return "";
  }
}

export function writeModelChoice(value: string): void {
  try {
    if (value) localStorage.setItem(MODEL_KEY, value);
    else localStorage.removeItem(MODEL_KEY);
  } catch {
    /* storage unavailable: the pick just does not stick */
  }
}

export function ModelMenu({ className }: { className?: string }) {
  const { state, toast } = useStore();
  const t = useT();
  const [choice, setChoice] = useState(() => state.settings?.model_choice ?? readModelChoice());
  const [open, setOpen] = useState(false);
  const [lineup, setLineup] = useState<Awaited<ReturnType<typeof api.models>> | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);
  useEffect(() => {
    api
      .models()
      .then(setLineup)
      .catch(() => undefined); // the menu falls back to the four built-ins
  }, [state.connectionsVersion, state.settings?.llm.model]);
  useClosable("model-menu", () => {
    setOpen(false);
    return true;
  }, open);
  // The pick is one value for every device: a change made on the phone arrives here in
  // the settings push and becomes this device's choice too.
  useEffect(() => {
    const serverChoice = state.settings?.model_choice;
    if (serverChoice !== undefined && serverChoice !== readModelChoice()) {
      writeModelChoice(serverChoice);
      setChoice(serverChoice);
    }
  }, [state.settings?.model_choice]);
  // a press anywhere outside the pill and its menu closes the menu
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!(e.target instanceof Element) || !e.target.closest("[data-model-menu]")) setOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  const pick = (value: string) => {
    writeModelChoice(value);
    setChoice(value);
    setOpen(false);
    api
      .updateSettings({ model_choice: value })
      .catch(() => undefined); // the pick stays on this device; it just does not reach the others
  };
  // picking an added AI or saved model option makes it the active chat model
  const pickEndpoint = (endpointId: string, model: string) => {
    setSwitching(endpointId + model);
    api
      .setLLM({ endpoint: endpointId, model })
      .then(() => {
        writeModelChoice("");
        setChoice("");
        setOpen(false);
        toast(t("Switched to {model}", { model }));
      })
      .catch(() => undefined)
      .finally(() => setSwitching(null));
  };
  const pickOption = (opt: SavedModelOption) => {
    const key = `opt:${opt.endpoint || opt.base_url || ""}:${opt.model}`;
    setSwitching(key);
    const body: Record<string, unknown> = { model: opt.model };
    if (opt.endpoint) body.endpoint = opt.endpoint;
    else {
      if (opt.provider) body.provider = opt.provider;
      if (opt.base_url) body.base_url = opt.base_url;
    }
    api
      .setLLM(body)
      .then(() => {
        writeModelChoice("");
        setChoice("");
        setOpen(false);
        toast(t("Switched to {model}", { model: opt.label || opt.model }));
      })
      .catch(() => undefined)
      .finally(() => setSwitching(null));
  };
  const aliases = lineup?.aliases ?? MODEL_CHOICES;
  const options = lineup?.options ?? [];
  const endpoints = lineup?.endpoints ?? [];
  const activeModel = lineup?.default?.model || state.settings?.llm?.model || "";
  const activeBaseUrl = (lineup?.default?.base_url || "").replace(/\/+$/, "");
  const current = aliases.find((c) => c.value === choice);
  const shortModel = (m: string) => (m.includes("/") ? m.split("/").pop() || m : m);
  const pillText = choice
    ? t(current?.label || choice)
    : options.length > 0 && activeModel
      ? shortModel(activeModel)
      : t("Default");
  return (
    <div className={cx("relative", className)} data-model-menu>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("Model")}
        title={activeModel || t("Model")}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cx(
          "flex max-w-full items-center gap-1 rounded-full bg-surface-2/80 px-2.5 py-0.5 text-[11.5px] font-medium text-fg/80 transition active:scale-95",
          open && "bg-surface-2 text-fg",
        )}
      >
        <Cpu size={11} className="shrink-0 text-muted" />
        <span className="truncate">{pillText}</span>
        <ChevronDown size={11} className={cx("shrink-0 text-muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-30 mt-1.5 w-72 max-h-[70vh] overflow-y-auto -translate-x-1/2 rounded-2xl border border-border/70 bg-surface p-1 shadow-lg" role="menu">
          {options.length > 0 && (
            <div className="pb-1 mb-1 border-b border-border/60">
              <div className="px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted flex items-center gap-1">
                <Sparkles size={11} /> {t("Your Models")}
              </div>
              {options.map((opt) => {
                const key = `opt:${opt.endpoint || opt.base_url || ""}:${opt.model}`;
                const optBase = (opt.base_url || "").replace(/\/+$/, "");
                const isSelected =
                  !choice &&
                  activeModel === opt.model &&
                  (!optBase || !activeBaseUrl || optBase === activeBaseUrl);
                return (
                  <button
                    key={key}
                    type="button"
                    role="menuitem"
                    onClick={() => pickOption(opt)}
                    className={cx(
                      "flex w-full items-start gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-surface-2/70 active:bg-surface-2",
                      isSelected && "bg-accent/10",
                    )}
                  >
                    {switching === key ? (
                      <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-accent" />
                    ) : isSelected ? (
                      <Check size={14} className="mt-0.5 shrink-0 text-accent" />
                    ) : (
                      <span className="h-[14px] w-[14px] shrink-0" />
                    )}
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{shortModel(opt.label || opt.model)}</span>
                      <span className="block truncate text-[11.5px] text-muted">{opt.model}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {aliases.map((item) => (
            <button
              key={item.value || "default"}
              type="button"
              role="menuitem"
              onClick={() => pick(item.value)}
              className={cx(
                "flex w-full items-start gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-surface-2/70 active:bg-surface-2",
                choice === item.value && "bg-accent/10",
              )}
            >
              {choice === item.value ? (
                <Check size={14} className="mt-0.5 shrink-0 text-accent" />
              ) : (
                <span className="h-[14px] w-[14px] shrink-0" />
              )}
              <span className="min-w-0">
                <span className="block text-[13px] font-medium">{t(item.label)}</span>
                <span className="block truncate text-[11.5px] text-muted">
                  {item.value === "" ? lineup?.default?.model || state.settings?.llm?.model : t(item.hint)}
                </span>
              </span>
            </button>
          ))}
          {endpoints.map((ep) =>
            ((ep.models ?? []).length ? ep.models : [""]).map((model) => (
              <button
                key={ep.id + model}
                type="button"
                role="menuitem"
                onClick={() => pickEndpoint(ep.id, model)}
                className="flex w-full items-start gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-surface-2/70 active:bg-surface-2"
              >
                {switching === ep.id + model ? (
                  <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-accent" />
                ) : (
                  <Bot size={14} className="mt-0.5 shrink-0 text-muted" />
                )}
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-medium">{ep.label || t("Added AI")}</span>
                  <span className="block truncate text-[11.5px] text-muted">{model || ep.id}</span>
                </span>
              </button>
            )),
          )}
          <div className="px-2.5 pb-1 pt-1.5 text-[11px] text-muted">{t("Stays until you pick another.")}</div>
        </div>
      )}
    </div>
  );
}
