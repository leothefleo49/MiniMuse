/* MiniMuse Companion background service worker.
 * Maintains a WebSocket connection to MiniMuse (/ws/chrome) and drives a
 * user-selected background tab via chrome.debugger (CDP) so content scripts
 * from other Chrome extensions never break automation and Chrome keeps its focus.
 */

const DEFAULT_SERVER_URL = "http://127.0.0.1:8787";
const CDP_VERSION = "1.3";

let ws = null;
let reconnectTimer = null;
let attachedTabId = null;
let lastUrl = "";
let lastTitle = "";
let viewport = { width: 1280, height: 800 };

chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

async function getConfig() {
  const got = await chrome.storage.local.get(["serverUrl", "token"]);
  return {
    serverUrl: (got.serverUrl || DEFAULT_SERVER_URL).replace(/\/+$/, ""),
    token: got.token || "",
  };
}

function toWsUrl(httpUrl) {
  const u = new URL(httpUrl);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/ws/chrome";
  u.search = "";
  return u.toString();
}

function postStateToPanel() {
  chrome.runtime.sendMessage({
    type: "minimuse_status",
    connected: ws?.readyState === WebSocket.OPEN,
    attached: attachedTabId !== null,
    tabId: attachedTabId,
    url: lastUrl,
    title: lastTitle,
  }).catch(() => {});
}

function pushBridgeState() {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      kind: "chrome_state",
      attached: attachedTabId !== null,
      tab_id: attachedTabId,
      url: lastUrl,
      title: lastTitle,
      width: viewport.width,
      height: viewport.height,
      version: chrome.runtime.getManifest().version,
    }));
  }
  postStateToPanel();
}

function sendCdp(tabId, method, params = {}) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId }, method, params, (result) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(result || {});
      }
    });
  });
}

async function refreshTabMeta() {
  if (attachedTabId === null) return;
  try {
    const tab = await chrome.tabs.get(attachedTabId);
    lastUrl = tab.url || "";
    lastTitle = tab.title || "";
    if (tab.width && tab.height) {
      viewport = { width: tab.width, height: tab.height };
    }
  } catch {
    attachedTabId = null;
    lastUrl = "";
    lastTitle = "";
  }
}

async function attachTab(tabId) {
  if (attachedTabId !== null && attachedTabId !== tabId) {
    await detachTab();
  }
  await new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, CDP_VERSION, () => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve();
      }
    });
  });
  attachedTabId = tabId;
  await sendCdp(tabId, "Page.enable").catch(() => {});
  await sendCdp(tabId, "Runtime.enable").catch(() => {});
  await refreshTabMeta();
  pushBridgeState();
}

async function attachActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active Chrome tab found.");
  if ((tab.url || "").startsWith("chrome://") || (tab.url || "").startsWith("chrome-extension://")) {
    throw new Error("Chrome system tabs cannot be shared. Open a regular website tab first.");
  }
  await attachTab(tab.id);
}

async function detachTab() {
  const tid = attachedTabId;
  attachedTabId = null;
  lastUrl = "";
  lastTitle = "";
  if (tid !== null) {
    await new Promise((resolve) => {
      chrome.debugger.detach({ tabId: tid }, () => resolve());
    });
  }
  pushBridgeState();
}

async function evalInTab(tabId, fnSource, arg) {
  const serializedArg = arg === undefined ? "undefined" : JSON.stringify(arg);
  const expr = `Promise.resolve((${fnSource})(${serializedArg})).then((v) => JSON.stringify({ value: v === undefined ? null : v }))`;
  const res = await sendCdp(tabId, "Runtime.evaluate", {
    expression: expr,
    awaitPromise: true,
    returnByValue: true,
  });
  if (res.exceptionDetails) {
    throw new Error(res.exceptionDetails.text || res.exceptionDetails.exception?.description || "JS eval error");
  }
  const raw = res.result?.value;
  if (typeof raw === "string") {
    return JSON.parse(raw).value;
  }
  return raw;
}

const KEY_CODE_MAP = {
  Enter: { code: "Enter", keyCode: 13 },
  Tab: { code: "Tab", keyCode: 9 },
  Escape: { code: "Escape", keyCode: 27 },
  Backspace: { code: "Backspace", keyCode: 8 },
  ArrowDown: { code: "ArrowDown", keyCode: 40 },
  ArrowUp: { code: "ArrowUp", keyCode: 38 },
  ArrowLeft: { code: "ArrowLeft", keyCode: 37 },
  ArrowRight: { code: "ArrowRight", keyCode: 39 },
  " ": { code: "Space", keyCode: 32 },
};

async function handleBridgeRpc(msg) {
  const action = msg.action;
  if (action === "ping") {
    await refreshTabMeta();
    return {
      attached: attachedTabId !== null,
      tab_id: attachedTabId,
      url: lastUrl,
      title: lastTitle,
      width: viewport.width,
      height: viewport.height,
    };
  }
  if (action === "tabs") {
    const tabs = await chrome.tabs.query({});
    return {
      tabs: tabs.map((t) => ({ id: t.id, title: t.title || "", url: t.url || "", active: !!t.active })),
    };
  }
  if (action === "attach_active") {
    await attachActiveTab();
    return { attached: true, tab_id: attachedTabId, url: lastUrl, title: lastTitle };
  }
  if (action === "detach") {
    await detachTab();
    return { attached: false };
  }
  if (attachedTabId === null) {
    throw new Error("No Chrome tab is shared with MiniMuse yet.");
  }
  const tabId = attachedTabId;
  if (action === "state") {
    await refreshTabMeta();
    return { url: lastUrl, title: lastTitle, width: viewport.width, height: viewport.height };
  }
  if (action === "navigate") {
    await sendCdp(tabId, "Page.navigate", { url: msg.url });
    await new Promise((r) => setTimeout(r, 900));
    await refreshTabMeta();
    pushBridgeState();
    return { url: lastUrl, title: lastTitle };
  }
  if (action === "back") {
    const hist = await sendCdp(tabId, "Page.getNavigationHistory");
    const idx = hist.currentIndex || 0;
    if (idx > 0 && hist.entries?.[idx - 1]) {
      await sendCdp(tabId, "Page.navigateToHistoryEntry", { entryId: hist.entries[idx - 1].id });
      await new Promise((r) => setTimeout(r, 700));
    }
    await refreshTabMeta();
    return { url: lastUrl, title: lastTitle };
  }
  if (action === "eval") {
    const value = await evalInTab(tabId, msg.fn, msg.arg);
    return { value };
  }
  if (action === "click") {
    const x = Number(msg.x || 0);
    const y = Number(msg.y || 0);
    await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    await refreshTabMeta();
    return { url: lastUrl, title: lastTitle };
  }
  if (action === "type") {
    const text = String(msg.text || "");
    for (const ch of text) {
      await sendCdp(tabId, "Input.dispatchKeyEvent", { type: "char", text: ch });
    }
    return {};
  }
  if (action === "key") {
    const k = String(msg.key || "Enter");
    const info = KEY_CODE_MAP[k] || { code: k, keyCode: k.length === 1 ? k.toUpperCase().charCodeAt(0) : 0 };
    await sendCdp(tabId, "Input.dispatchKeyEvent", {
      type: "keyDown",
      key: k,
      code: info.code,
      windowsVirtualKeyCode: info.keyCode,
      nativeVirtualKeyCode: info.keyCode,
    });
    if (k === "Enter") {
      await sendCdp(tabId, "Input.dispatchKeyEvent", { type: "char", text: "\r" });
    }
    await sendCdp(tabId, "Input.dispatchKeyEvent", {
      type: "keyUp",
      key: k,
      code: info.code,
      windowsVirtualKeyCode: info.keyCode,
      nativeVirtualKeyCode: info.keyCode,
    });
    return {};
  }
  if (action === "scroll") {
    const dy = Number(msg.dy || 0);
    await evalInTab(tabId, "(dy) => { window.scrollBy(0, dy); return true; }", dy);
    return {};
  }
  if (action === "screenshot") {
    await refreshTabMeta();
    const q = Math.max(25, Math.min(95, Number(msg.quality || 60)));
    const shot = await sendCdp(tabId, "Page.captureScreenshot", {
      format: "jpeg",
      quality: q,
      fromSurface: true,
    });
    return { jpeg: shot.data || "", url: lastUrl, title: lastTitle };
  }
  if (action === "fetch") {
    const out = await evalInTab(
      tabId,
      `async (p) => {
        const resp = await fetch(p.url, {
          method: p.method || "GET",
          headers: p.headers || {},
          body: p.method === "POST" ? p.body : undefined,
          credentials: "include"
        });
        const hdrs = {};
        resp.headers.forEach((v, k) => { hdrs[k] = v; });
        const body = await resp.text();
        return { status: resp.status, headers: hdrs, body, url: resp.url };
      }`,
      { url: msg.url, method: msg.method || "GET", headers: msg.headers || {}, body: msg.body ?? null },
    );
    return out;
  }
  throw new Error(`Unsupported action: ${action}`);
}

async function connectSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const cfg = await getConfig();
  let socketUrl;
  try {
    socketUrl = toWsUrl(cfg.serverUrl);
  } catch {
    return;
  }
  try {
    ws = new WebSocket(socketUrl);
  } catch {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    if (cfg.token) {
      ws.send(JSON.stringify({ kind: "auth", token: cfg.token }));
    }
    ws.send(JSON.stringify({
      kind: "chrome_hello",
      version: chrome.runtime.getManifest().version,
      browser: "Chrome",
    }));
    pushBridgeState();
  };
  ws.onmessage = async (ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (data.kind === "chrome_request") {
      try {
        const result = await handleBridgeRpc(data);
        ws?.send(JSON.stringify({ kind: "chrome_response", id: data.id, ok: true, result }));
      } catch (err) {
        ws?.send(JSON.stringify({
          kind: "chrome_response",
          id: data.id,
          ok: false,
          error: err?.message || String(err),
        }));
      }
    }
  };
  ws.onclose = () => {
    ws = null;
    postStateToPanel();
    scheduleReconnect();
  };
  ws.onerror = () => {
    try { ws?.close(); } catch {}
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connectSocket();
  }, 3000);
}

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId === attachedTabId) {
    attachedTabId = null;
    lastUrl = "";
    lastTitle = "";
    pushBridgeState();
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === attachedTabId) {
    attachedTabId = null;
    lastUrl = "";
    lastTitle = "";
    pushBridgeState();
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tabId === attachedTabId && (changeInfo.url || changeInfo.title)) {
    lastUrl = tab.url || lastUrl;
    lastTitle = tab.title || lastTitle;
    pushBridgeState();
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg?.type === "get_status") {
      sendResponse({
        connected: ws?.readyState === WebSocket.OPEN,
        attached: attachedTabId !== null,
        tabId: attachedTabId,
        url: lastUrl,
        title: lastTitle,
      });
    } else if (msg?.type === "attach_active") {
      await attachActiveTab();
      sendResponse({ ok: true, tabId: attachedTabId, url: lastUrl, title: lastTitle });
    } else if (msg?.type === "detach") {
      await detachTab();
      sendResponse({ ok: true });
    } else if (msg?.type === "reconnect") {
      try { ws?.close(); } catch {}
      ws = null;
      await connectSocket();
      sendResponse({ ok: true });
    }
  })().catch((err) => {
    sendResponse({ ok: false, error: err?.message || String(err) });
  });
  return true;
});

void connectSocket();
