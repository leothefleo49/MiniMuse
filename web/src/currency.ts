import { useEffect, useState } from "react";

export type CurrencyCode = "USD" | "EUR" | "GBP" | "CAD" | "AUD" | "JPY" | "CNY";

const CURRENCY_STORAGE_KEY = "nm.currency";

export const CURRENCIES: Array<{ code: CurrencyCode; symbol: string; label: string; perUsd: number }> = [
  { code: "USD", symbol: "$", label: "USD ($)", perUsd: 1.0 },
  { code: "EUR", symbol: "€", label: "EUR (€)", perUsd: 0.92 },
  { code: "GBP", symbol: "£", label: "GBP (£)", perUsd: 0.79 },
  { code: "CAD", symbol: "CA$", label: "CAD (CA$)", perUsd: 1.38 },
  { code: "AUD", symbol: "A$", label: "AUD (A$)", perUsd: 1.53 },
  { code: "JPY", symbol: "¥", label: "JPY (¥)", perUsd: 154.0 },
  { code: "CNY", symbol: "¥", label: "CNY (¥)", perUsd: 7.25 },
];

export function detectDefaultCurrency(): CurrencyCode {
  if (typeof navigator === "undefined") return "USD";
  const lang = (navigator.language || "en-US").toLowerCase();
  if (lang.startsWith("zh-cn") || lang === "zh") return "CNY";
  if (lang.startsWith("ja")) return "JPY";
  if (lang.endsWith("-gb")) return "GBP";
  if (lang.endsWith("-ca")) return "CAD";
  if (lang.endsWith("-au")) return "AUD";
  if (
    lang.startsWith("de") ||
    lang.startsWith("fr") ||
    lang.startsWith("es") ||
    lang.startsWith("it") ||
    lang.startsWith("nl") ||
    lang.startsWith("pt")
  ) {
    return "EUR";
  }
  return "USD";
}

export function getPreferredCurrency(): CurrencyCode {
  try {
    const saved = localStorage.getItem(CURRENCY_STORAGE_KEY);
    if (saved && CURRENCIES.some((c) => c.code === saved)) {
      return saved as CurrencyCode;
    }
  } catch {
    /* ignore storage errors */
  }
  return detectDefaultCurrency();
}

export function setPreferredCurrency(code: CurrencyCode): void {
  try {
    localStorage.setItem(CURRENCY_STORAGE_KEY, code);
  } catch {
    /* ignore storage errors */
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("nanomuse:currency", { detail: code }));
  }
}

export function usePreferredCurrency(): [CurrencyCode, (code: CurrencyCode) => void] {
  const [code, setCode] = useState<CurrencyCode>(() => getPreferredCurrency());
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onEv = (e: Event) => {
      const next = (e as CustomEvent<CurrencyCode>).detail;
      if (next) setCode(next);
    };
    window.addEventListener("nanomuse:currency", onEv);
    return () => window.removeEventListener("nanomuse:currency", onEv);
  }, []);
  const update = (next: CurrencyCode) => {
    setCode(next);
    setPreferredCurrency(next);
  };
  return [code, update];
}

export function formatFromCny(cny: number, target: CurrencyCode, usdCnyRate = 7.25): string {
  const safeRate = usdCnyRate > 0 ? usdCnyRate : 7.25;
  const info = CURRENCIES.find((c) => c.code === target) ?? CURRENCIES[0];
  if (target === "CNY") {
    return `¥${cny.toFixed(2)}`;
  }
  const usdVal = cny / safeRate;
  const converted = usdVal * info.perUsd;
  const decimals = target === "JPY" ? 0 : 2;
  return `${info.symbol}${converted.toFixed(decimals)} ${info.code}`;
}

export function formatCnyWithNative(cny: number, target: CurrencyCode, usdCnyRate = 7.25): string {
  if (target === "CNY") return `¥${cny.toFixed(2)}`;
  return `¥${cny.toFixed(2)} (≈ ${formatFromCny(cny, target, usdCnyRate)})`;
}

export function formatUsdInCurrency(usd: number, target: CurrencyCode): string {
  const info = CURRENCIES.find((c) => c.code === target) ?? CURRENCIES[0];
  const converted = usd * info.perUsd;
  if (target === "JPY") return `${info.symbol}${Math.round(converted)}`;
  return converted >= 1 ? `${info.symbol}${converted.toFixed(2)}` : `${info.symbol}${converted.toFixed(4)}`;
}
