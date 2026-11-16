/* nanoMuse service worker: Web Push, the app badge, and the offline shell.
 *
 * The server (nanomuse/server/push.py) sends {title, body, tag, url, badge, kind}. A
 * reminder arrives as kind "alarm" and shows with requireInteraction and a hard buzz:
 * it stays on screen until dismissed, the way a reminder should. Any other kind
 * shows a normal notification, unless a window here is visible *on the chat the push is
 * about*
 * (its card would be under the banner anyway); any other chat, or every screen off,
 * buzzes. Chrome may rotate a subscription's endpoint on its own (pushsubscriptionchange):
 * the worker re-subscribes with the server's key and hands the new one over, so actions
 * and reminders keep arriving after the rotation. Tapping a notification focuses the
 * app on the right chat.
 *
 * Offline, the worker stands in for the network: hashed build files are cache-first (they
 * are renamed on every release, so a copy is its content), and API reads are network-first
 * with the last good copy as the stand-in, so the app still opens and shows its recent
 * screen over the phone's data gap. Sends and uploads are POSTs and stay with the page,
 * which queues them itself (web/src/offline.ts) and flushes them on reconnect.
 */

const SHELL = "nm-shell-v5";
const DATA = "nm-data-v5";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(
  (async () => {
    await self.clients.claim();
    // yesterday's cache names go out with the trash; nothing reads them any more
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== SHELL && n !== DATA).map((n) => caches.delete(n)));
  })(),
));

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // sends and uploads: the app queues and flushes these
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // a hashed file is its own content: cache first, the network only the first time
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.open(SHELL).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      }),
    );
    return;
  }

  // pages: the network wins online (bypassing HTTP cache so upgraded bundles load at once);
  // offline, the entry page already seen comes back
  if (req.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const res = await fetch(new Request(req, { cache: "no-store" }));
          if (res.ok) {
            caches.open(DATA).then((cache) => {
              cache.put("/", res.clone());
              cache.put(req, res.clone());
            }).catch(() => {});
          }
          return res;
        } catch {
          const cache = await caches.open(DATA);
          return (await cache.match("/")) || (await cache.match(req, { ignoreSearch: true })) || missed(req);
        }
      })(),
    );
    return;
  }

  // API reads: live state/update/health must never serve stale cached versions;
  // other reads cache best-effort for offline resilience
  if (url.pathname.startsWith("/api/")) {
    if (
      url.pathname === "/api/health" ||
      url.pathname === "/api/update" ||
      url.pathname === "/api/state" ||
      url.pathname === "/api/settings"
    ) {
      return;
    }
    event.respondWith(
      (async () => {
        try {
          const res = await fetch(req);
          if (res.ok) {
            caches.open(DATA).then((cache) => cache.put(req, res.clone())).catch(() => {});
          }
          return res;
        } catch {
          const cache = await caches.open(DATA);
          return (await cache.match(req)) || missed(req);
        }
      })(),
    );
  }
  // the rest (icons, manifest): tiny and stable; the browser's own cache handles them
});

function missed(req) {
  return new Response(JSON.stringify({ detail: "This device is offline and this read has no saved copy." }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });
}

async function setBadge(n) {
  if (typeof n !== "number" || !("setAppBadge" in navigator)) return;
  try {
    if (n > 0) await navigator.setAppBadge(n);
    else await navigator.clearAppBadge();
  } catch {
    /* badges are best effort */
  }
}

/** which chat a location is on: the thread query param, or "" for the main chat */
function threadOf(url) {
  try {
    const u = new URL(url, self.location.origin);
    if (u.pathname !== "/") return u.pathname;
    return u.searchParams.get("thread") || "";
  } catch {
    return "";
  }
}

function keyBytes(key) {
  const clean = (key || "").replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(clean.padEnd(clean.length + ((4 - (clean.length % 4)) % 4), "="));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

let serverKeyCache = { key: "", at: 0 };

async function serverKey() {
  if (serverKeyCache.key && Date.now() - serverKeyCache.at < 60000) return serverKeyCache.key;
  const res = await fetch("/api/push", { cache: "no-cache" });
  if (!res.ok) throw new Error(res.status);
  const view = await res.json();
  if (!view.public_key) throw new Error("no server key");
  serverKeyCache = { key: view.public_key, at: Date.now() };
  return view.public_key;
}

/** a fresh subscription with the server's current key, handed to the server */
async function renewSubscription() {
  const old = await self.registration.pushManager.getSubscription();
  if (old) await old.unsubscribe();
  const sub = await self.registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(await serverKey()),
  });
  const res = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: sub.toJSON() }),
  });
  if (!res.ok) throw new Error(res.status);
}

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(renewSubscription().catch(() => {}));
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "MiniMuse", body: event.data ? event.data.text() : "" };
  }
  data.url = data.url || "/";
  data.kind = data.kind || "";
  event.waitUntil(
    (async () => {
      await setBadge(data.badge);
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const here = threadOf(data.url);
      // the test always shows: that is the point of sending one
      const sameChatVisible =
        data.kind !== "test" &&
        windows.some((c) => c.visibilityState === "visible" && threadOf(c.url) === here);
      if (sameChatVisible) return;
      // A reminder arrives as kind "alarm": it buzzes hard and stays on screen until
      // dismissed (requireInteraction), instead of sliding into the tray quietly.
      const alarm = data.kind === "alarm";
      await self.registration.showNotification(data.title || "MiniMuse", {
        body: data.body || "",
        tag: data.tag || undefined,
        renotify: !!data.tag,
        icon: "/icon-192.png",
        badge: "/icon-192.png",
        timestamp: data.ts ? Date.parse(data.ts) : Date.now(),
        requireInteraction: alarm || undefined,
        vibrate: alarm ? [400, 200, 400, 200, 400] : undefined,
        // the emergency stop, one tap from any notification: tells the open page to stop
        // the running turn right now
        actions: [{ action: "stop", title: "Stop now" }],
        data: { url: data.url, kind: data.kind },
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  // the Stop now action: the open page (or one we open) stops the running turn
  const stop = event.action === "stop";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if ("focus" in client) {
          await client.focus();
          client.postMessage(stop ? { type: "stop-now" } : { type: "open", url });
          return;
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});
