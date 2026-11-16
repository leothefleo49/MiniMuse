const DEFAULT_URL = "http://127.0.0.1:8787";

const dot = document.getElementById("dot");
const statusTitle = document.getElementById("statusTitle");
const statusSub = document.getElementById("statusSub");
const shareBtn = document.getElementById("shareBtn");
const gearBtn = document.getElementById("gearBtn");
const settingsBox = document.getElementById("settingsBox");
const serverInput = document.getElementById("serverInput");
const tokenInput = document.getElementById("tokenInput");
const saveBtn = document.getElementById("saveBtn");
const appFrame = document.getElementById("appFrame");
const errBox = document.getElementById("errBox");

let isAttached = false;

function showError(msg) {
  if (!msg) {
    errBox.classList.remove("open");
    errBox.textContent = "";
    return;
  }
  errBox.textContent = msg;
  errBox.classList.add("open");
}

function renderStatus(st) {
  isAttached = !!st?.attached;
  dot.className = "dot" + (isAttached ? " shared" : st?.connected ? " on" : "");
  if (isAttached) {
    statusTitle.textContent = st.title || "Shared Chrome tab";
    statusSub.textContent = "Background mode active - you can use other tabs";
    shareBtn.textContent = "Stop Tab";
    shareBtn.className = "danger";
  } else if (st?.connected) {
    statusTitle.textContent = "MiniMuse Companion";
    statusSub.textContent = "Connected. Click Share Tab for background control.";
    shareBtn.textContent = "Share Tab";
    shareBtn.className = "primary";
  } else {
    statusTitle.textContent = "MiniMuse Disconnected";
    statusSub.textContent = "Start MiniMuse Desktop or nanomuse serve";
    shareBtn.textContent = "Share Tab";
    shareBtn.className = "primary";
  }
}

async function loadFrame() {
  const got = await chrome.storage.local.get(["serverUrl", "token"]);
  const base = (got.serverUrl || DEFAULT_URL).replace(/\/+$/, "");
  serverInput.value = base;
  tokenInput.value = got.token || "";
  appFrame.src = `${base}/?chrome_panel=1`;
}

shareBtn.addEventListener("click", async () => {
  showError("");
  const type = isAttached ? "detach" : "attach_active";
  const res = await chrome.runtime.sendMessage({ type });
  if (!res?.ok) {
    showError(res?.error || "Failed to update tab sharing");
  } else {
    const st = await chrome.runtime.sendMessage({ type: "get_status" });
    renderStatus(st);
  }
});

gearBtn.addEventListener("click", () => {
  settingsBox.classList.toggle("open");
});

saveBtn.addEventListener("click", async () => {
  showError("");
  const serverUrl = (serverInput.value || DEFAULT_URL).trim().replace(/\/+$/, "");
  const token = (tokenInput.value || "").trim();
  await chrome.storage.local.set({ serverUrl, token });
  settingsBox.classList.remove("open");
  appFrame.src = `${serverUrl}/?chrome_panel=1`;
  await chrome.runtime.sendMessage({ type: "reconnect" });
  const st = await chrome.runtime.sendMessage({ type: "get_status" });
  renderStatus(st);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "minimuse_status") {
    renderStatus(msg);
  }
});

void (async () => {
  await loadFrame();
  const st = await chrome.runtime.sendMessage({ type: "get_status" });
  renderStatus(st);
})();
