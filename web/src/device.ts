/**
 * Which device this very page is running on, and the words for a device's kind. The
 * labels on the Permissions page and the Devices screen read from here, so the phone's
 * own rows say "This phone" and every device is named by its nickname and its type,
 * with the official model name only where it is edited.
 */

export type DeviceKind = "phone" | "tablet" | "computer" | "web";

/** What this browser is: a phone, a tablet, or the computer the runtime runs on.
 *  Mirrors the server's own classification of the live web clients (service.py). */
export function thisDeviceKind(): DeviceKind {
  if (typeof navigator === "undefined") return "computer";
  const ua = navigator.userAgent || "";
  const android = /Android/i.test(ua);
  const mobile = /Mobile/i.test(ua) || /iPhone|iPod/i.test(ua);
  if (/iPad/i.test(ua) || (android && !mobile)) return "tablet";
  if (mobile) return "phone";
  return "computer";
}

/** The lowercase word for a device's kind: phone, tablet, computer, browser. */
export function kindWord(kind: string, t: (s: string) => string): string {
  switch (kind) {
    case "phone":
      return t("phone");
    case "tablet":
      return t("tablet");
    case "computer":
      return t("computer");
    case "web":
      return t("browser");
    default:
      return t("device");
  }
}

/** The title-case word for a device's kind: Phone, Tablet, Computer, Browser. */
export function kindTitle(kind: string, t: (s: string) => string): string {
  switch (kind) {
    case "phone":
      return t("Phone");
    case "tablet":
      return t("Tablet");
    case "computer":
      return t("Computer");
    case "web":
      return t("Browser");
    default:
      return t("Device");
  }
}
