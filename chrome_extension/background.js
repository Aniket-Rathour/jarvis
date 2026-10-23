// Jarvis Hands v3: connects to the Jarvis app (ws://127.0.0.1:8765) and carries out its browser commands.
//  - tells Jarvis which tab you are looking at (so "open Aryan's chat" means the WhatsApp that is on screen)
//  - reuses an open tab instead of opening a duplicate
//  - clicks and types with ordinary page events (no "started debugging this browser" bar); the debugger is only a
//    last resort when a page ignores them, and is released at once
//  - looks inside iframes and shadow DOM, notices new tabs a click opens, waits for slow pages
const WS_URL = "ws://127.0.0.1:8765";
const LIB_V = 8;
let ws = null, connected = false;

// ---------------------------------------------------------------- connection
function setBadge(on) {
  connected = on;
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
}
let retry = 0, retryTimer = null;
function scheduleRetry() {                 // Jarvis restarted: be back within seconds, not at the next 30 s alarm
  clearTimeout(retryTimer);
  const delay = [1000, 2000, 3000, 5000, 8000, 10000][Math.min(retry++, 5)];
  retryTimer = setTimeout(connect, delay);
}
function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(WS_URL); } catch (e) { return; }
  ws.onopen = () => { setBadge(true); lastPushed = ""; ws.send(JSON.stringify({ hello: "jarvis-hands", version: 3 })); pushState(0); };
  ws.onopen = ((orig) => () => { retry = 0; orig(); })(ws.onopen);
  ws.onclose = () => { setBadge(false); scheduleRetry(); };
  ws.onerror = () => setBadge(false);
  ws.onmessage = async (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.pong) return;
    let reply;
    try { reply = { id: msg.id, ok: true, result: await run(msg) }; }
    catch (e) { reply = { id: msg.id, ok: false, error: String((e && e.message) || e) }; }
    logAction(msg, reply);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(reply));
  };
}
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ ping: 1 })); else connect(); }, 20000);
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onMessage.addListener((m, _s, send) => { if (m === "status") send({ connected }); });
connect();

// the popup shows what Jarvis did lately
async function logAction(msg, reply) {
  if (msg.cmd === "context" || msg.cmd === "ping" || msg.cmd === "wait") return;
  try {
    const { log = [] } = await chrome.storage.local.get("log");
    const what = [msg.cmd, msg.text || msg.url || msg.name || msg.field || msg.tab || ""].join(" ").trim().slice(0, 60);
    log.unshift({ t: Date.now(), what, ok: reply.ok, note: String(reply.ok ? reply.result : reply.error).slice(0, 80) });
    await chrome.storage.local.set({ log: log.slice(0, 15) });
  } catch { /* storage unavailable */ }
}

// ---------------------------------------------------------------- what is on screen
const APPS = { "web.whatsapp.com": "WhatsApp", "mail.google.com": "Gmail", "www.youtube.com": "YouTube", "youtube.com": "YouTube",
  "github.com": "GitHub", "www.notion.so": "Notion", "notion.so": "Notion", "www.linkedin.com": "LinkedIn", "x.com": "X",
  "twitter.com": "X", "www.instagram.com": "Instagram", "leetcode.com": "LeetCode", "docs.google.com": "Google Docs",
  "drive.google.com": "Google Drive", "calendar.google.com": "Google Calendar", "chatgpt.com": "ChatGPT", "claude.ai": "Claude",
  "www.google.com": "Google", "meet.google.com": "Google Meet", "discord.com": "Discord", "open.spotify.com": "Spotify",
  "www.amazon.in": "Amazon", "www.amazon.com": "Amazon", "www.flipkart.com": "Flipkart", "web.telegram.org": "Telegram" };
// "open WhatsApp", "open gmail", "open whatsapp.com": the app he means, not whatever that bare word would resolve to
const APP_URLS = { whatsapp: "https://web.whatsapp.com/", gmail: "https://mail.google.com/", mail: "https://mail.google.com/",
  youtube: "https://www.youtube.com/", github: "https://github.com/", notion: "https://www.notion.so/", linkedin: "https://www.linkedin.com/",
  twitter: "https://x.com/", x: "https://x.com/", instagram: "https://www.instagram.com/", leetcode: "https://leetcode.com/",
  "google docs": "https://docs.google.com/", docs: "https://docs.google.com/", drive: "https://drive.google.com/", "google drive": "https://drive.google.com/",
  calendar: "https://calendar.google.com/", "google calendar": "https://calendar.google.com/", chatgpt: "https://chatgpt.com/", claude: "https://claude.ai/",
  spotify: "https://open.spotify.com/", discord: "https://discord.com/app", telegram: "https://web.telegram.org/", amazon: "https://www.amazon.in/",
  flipkart: "https://www.flipkart.com/", meet: "https://meet.google.com/", "google meet": "https://meet.google.com/" };
const HOST_ALIAS = { "whatsapp.com": "https://web.whatsapp.com/", "gmail.com": "https://mail.google.com/", "youtube.com": "https://www.youtube.com/",
  "twitter.com": "https://x.com/", "docs.google.com": null };
function resolveApp(u) {                     // returns a full URL, or undefined when u is not an app name / alias
  if (!u) return undefined;
  const key = String(u).trim().toLowerCase().replace(/^(the|my)\s+/, "").replace(/\s+(web|app|website|page)$/, "");
  if (APP_URLS[key]) return APP_URLS[key];
  try {
    const x = new URL(/^[a-z][a-z0-9+.-]*:/i.test(u) ? u : "https://" + u);
    const h = x.hostname.replace(/^www\./, "");
    if (HOST_ALIAS[h] && (x.pathname === "/" || x.pathname === "")) return HOST_ALIAS[h];
  } catch { /* not a URL */ }
  return undefined;
}
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
const appOf = (u) => { try { const h = new URL(u).hostname; return APPS[h] || APPS["www." + h] || h.replace(/^www\./, ""); } catch { return ""; } };

const APPS_KNOWN = new Set(Object.values(APPS));
let pushTimer, lastPushed = "";
function pushState(delay = 120) {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(async () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      const t = await activeTab();
      const st = { id: t.id, title: t.title || "", url: t.url || "", app: appOf(t.url || "") };
      if (hostOf(t.url) === "web.whatsapp.com") { try { const w = await inPage(t, "waState"); if (w && w.chat) st.chat = w.chat; } catch { /* loading */ } }
      const key = JSON.stringify(st);
      if (key === lastPushed) return;
      lastPushed = key;
      ws.send(JSON.stringify({ state: st }));
    } catch { /* no normal window focused */ }
  }, delay);
}
chrome.tabs.onActivated.addListener(() => pushState());
chrome.tabs.onUpdated.addListener((_id, ch, tab) => { if (tab.active && (ch.status === "complete" || ch.url || ch.title)) pushState(); });
chrome.windows.onFocusChanged.addListener(() => pushState());
// chats switched by hand inside WhatsApp change nothing Chrome can see: look every 3 s while WhatsApp is in front
setInterval(async () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try { const t = await activeTab(); if (hostOf(t.url) === "web.whatsapp.com") pushState(0); } catch { /* none */ }
}, 3000);

// a click can open a new tab (target=_blank, window.open): remember it so we can say so and keep working there
let lastOpened = null;
chrome.tabs.onCreated.addListener((t) => { lastOpened = { id: t.id, opener: t.openerTabId, at: Date.now() }; });

// ---------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function callTabs() {
  return (await chrome.tabs.query({})).filter((t) => /^https:\/\/(web\.whatsapp\.com|meet\.google\.com)\//.test(t.url || ""));
}

async function activeTab() {
  let w; try { w = await chrome.windows.getLastFocused({ windowTypes: ["normal"] }); } catch { /* none */ }
  const [t] = await chrome.tabs.query(w ? { active: true, windowId: w.id } : { active: true, lastFocusedWindow: true });
  if (!t) throw new Error("no active tab");
  return t;
}
async function focusTab(t) {
  await chrome.tabs.update(t.id, { active: true });
  try { await chrome.windows.update(t.windowId, { focused: true }); } catch { /* ignore */ }
}
async function pickTab(m) {
  if (m.tab == null || m.tab === "current") return activeTab();
  if (typeof m.tab === "number" || /^\d+$/.test(String(m.tab))) return chrome.tabs.get(Number(m.tab));
  const q = String(m.tab).toLowerCase();
  const all = await chrome.tabs.query({});
  const hit = all.find((t) => appOf(t.url || "").toLowerCase() === q) ||
    all.find((t) => (t.title || "").toLowerCase().includes(q) || (t.url || "").toLowerCase().includes(q) || appOf(t.url || "").toLowerCase().includes(q));
  if (!hit) throw new Error(`no tab matching "${m.tab}". Open tabs: ${all.slice(0, 12).map((t) => appOf(t.url) || t.title).join(", ")}`);
  return hit;
}
const brief = (t) => `[${t.id}] ${t.active ? "* " : ""}${t.pinned ? "(pinned) " : ""}${t.audible ? "(playing sound) " : ""}${(t.title || "").slice(0, 60)} - ${(t.url || "").slice(0, 80)}`;
const normUrl = (u) => (!u ? undefined : /^[a-z][a-z0-9+.-]*:/i.test(u) ? u : "https://" + u);
const whereNow = async (tab) => { const t = await chrome.tabs.get(tab.id); return `${(t.title || "").slice(0, 50)} - ${(t.url || "").slice(0, 70)}`; };

async function waitLoaded(tabId, ms = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    let t; try { t = await chrome.tabs.get(tabId); } catch { return; }
    if (t.status === "complete") return;
    await sleep(80);
  }
}
// loaded is not ready: single-page apps keep building after "complete". WhatsApp is ready when its chat list exists.
async function ready(tab) {
  await waitLoaded(tab.id, 8000);
  let t = await chrome.tabs.get(tab.id).catch(() => tab);
  if (hostOf(t.url) === "web.whatsapp.com") {
    for (let i = 0; i < 100; i++) {                                  // up to ~25 s (QR / loading screen)
      let s = null; try { s = await inPage(t, "waState"); } catch { /* not injectable yet */ }
      if (s && s.ready) return;
      await sleep(250);
    }
  } else {
    try { await inPage(t, "idle", [250, 1500]); } catch { /* browser page */ }
  }
}

// ---- page toolkit (lib.js defines window.__jv in the page; one per frame)
const READ_ONLY = new Set(["snapshot", "has", "scroll", "waState", "fields", "text", "selection", "links"]);
const FRAMED = new Set(["click", "hover", "drag", "check", "type", "clear", "has", "scroll"]);

async function execIn(tabId, frameId, name, args) {
  const target = frameId ? { tabId, frameIds: [frameId] } : { tabId };
  const call = () => chrome.scripting.executeScript({
    target, args: [name, args, frameId || 0],
    func: async (n, a, fid) => {
      if (!(window.__jv && window.__jv.v === 8)) return "__NOLIB__";
      window.__jv.fid = fid;
      return await window.__jv[n](...a);
    },
  });
  let [r] = await call();
  if (r && r.result === "__NOLIB__") {
    await chrome.scripting.executeScript({ target, files: ["lib.js"] });
    [r] = await call();
  }
  return r && r.result;
}
const refFrame = (a0) => {                                            // refs from other frames look like f3_12
  const ref = a0 && typeof a0 === "object" ? a0.ref : null;
  const m = ref && /^f(\d+)_/.exec(String(ref));
  return m ? Number(m[1]) : null;
};
async function subframes(tabId) {
  try { return (await chrome.webNavigation.getAllFrames({ tabId })).filter((f) => f.frameId !== 0 && /^https?:|^about:srcdoc/.test(f.url)).slice(0, 8); }
  catch { return []; }
}
async function runOne(tab, frameId, name, args) {
  // a click/Enter that navigates destroys the page's script context and executeScript may never answer: race it
  let off = () => {}; const nav = new Promise((res) => {
    const l = (id, ch) => { if (id === tab.id && ch.status === "loading") res({ navigated: true }); };
    chrome.tabs.onUpdated.addListener(l); off = () => chrome.tabs.onUpdated.removeListener(l);
  });
  try {
    const r = await Promise.race([execIn(tab.id, frameId, name, args), nav]);
    if (r && r.navigated && READ_ONLY.has(name)) {                    // a read that raced a page change: wait, then read the new page
      await waitLoaded(tab.id, 6000); return await execIn(tab.id, frameId, name, args);
    }
    return r;
  } catch (e) {
    if (/Frame with ID|context|destroyed|removed|closed/i.test(String(e.message))) return { navigated: true };
    throw e;
  } finally { off(); }
}
async function inPage(tab, name, args = []) {
  const url = tab.url || "";
  if (/^(chrome|edge|about|chrome-extension|devtools|view-source):/.test(url) || /chromewebstore\.google\.com|chrome\.google\.com\/webstore/.test(url))
    throw new Error("that is a browser page, which extensions can't control");
  const rf = refFrame(args[0]);
  const frames = rf == null && FRAMED.has(name) ? await subframes(tab.id) : [];
  // with iframes present, look quickly everywhere first, then give the main page the full wait
  const quick = frames.length ? args.map((a) => (a && typeof a === "object" && !Array.isArray(a) ? { ...a, wait: 300 } : a)) : args;
  let r = await runOne(tab, rf == null ? 0 : rf, name, quick);
  if (r && r.notFound && frames.length) {
    for (const f of frames) {
      const x = await runOne(tab, f.frameId, name, quick);
      if (x && !x.notFound) { x.frame = f.frameId; return x; }
    }
    r = await runOne(tab, 0, name, args.map((a) => (a && typeof a === "object" && !Array.isArray(a) ? { ...a, wait: 1800 } : a)));
  }
  return r;
}
async function snapshotAll(tab, opts = []) {
  let out = await inPage(tab, "snapshot", opts);
  if (typeof out !== "string") return out;
  for (const f of await subframes(tab.id)) {
    try {
      const s = await runOne(tab, f.frameId, "snapshot", opts);
      if (typeof s === "string" && !/Interactive \(0 listed/.test(s)) out += `\n\n--- inside an embedded frame (${hostOf(f.url) || "frame"}) ---\n` + s.replace(/\nPage text:.*$/s, "");
    } catch { /* cross-origin or gone */ }
  }
  return out;
}

// ---- last resort: trusted input through the debugger (shows Chrome's bar for a moment)
async function withDebugger(tabId, fn) {
  await chrome.debugger.attach({ tabId }, "1.3");
  try { return await fn((method, params) => chrome.debugger.sendCommand({ tabId }, method, params)); }
  finally { try { await chrome.debugger.detach({ tabId }); } catch { /* already gone */ } }
}
const trustedClick = (tabId, x, y, double) => withDebugger(tabId, async (send) => {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  for (let i = 1; i <= (double ? 2 : 1); i++) {
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: i });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: i });
  }
});
const trustedMove = (tabId, x, y) => withDebugger(tabId, async (send) => {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x - 2, y: y - 2 });
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
});
const VK = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, PageDown: 34, PageUp: 33, Home: 36, End: 35, " ": 32 };
const trustedKey = (tabId, spec) => withDebugger(tabId, async (send) => {
  const parts = String(spec).split("+"); let key = parts.pop();
  const alias = { esc: "Escape", enter: "Enter", return: "Enter", tab: "Tab", space: " ", backspace: "Backspace", delete: "Delete", up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight" };
  key = alias[key.toLowerCase()] || (key.length > 1 ? key[0].toUpperCase() + key.slice(1) : key);
  const mods = parts.reduce((m, p) => m | ({ alt: 1, ctrl: 2, control: 2, meta: 4, cmd: 4, shift: 8 }[p.toLowerCase()] || 0), 0);
  const vk = VK[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
  const base = { key, code: key.length === 1 ? (/[a-z]/i.test(key) ? "Key" + key.toUpperCase() : "") : key, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods };
  const text = mods & 6 ? undefined : key === "Enter" ? "\r" : key.length === 1 ? key : undefined;
  await send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...base, ...(text ? { text } : {}) });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
});
const trustedText = (tabId, text) => withDebugger(tabId, (send) => send("Input.insertText", { text }));

// ---------------------------------------------------------------- commands
const spec = (m) => ({ ref: m.ref, text: m.text, nth: m.nth, within: m.within, kind: m.kind, double: !!m.double, right: !!m.right, ctrl: !!m.ctrl, visual: m.quiet ? false : undefined });
const fail = (r, dflt) => { throw new Error((r && r.err || dflt) + (r && r.near && r.near.length ? `. Closest matches: ${r.near.join(" | ")}` : "")); };

async function run(m) {
  switch (m.cmd) {
    case "ping": return `Jarvis Hands v3 (lib ${LIB_V})`;

    // ------------------------------------------------ seeing
    case "tabs": {
      const q = (m.text || "").toLowerCase();
      return (await chrome.tabs.query({})).filter((t) => !q || (t.title + t.url + appOf(t.url)).toLowerCase().includes(q))
        .map((t) => brief(t) + (appOf(t.url) ? ` <${appOf(t.url)}>` : "")).join("\n") || "no tabs";
    }
    case "context": {      // what he is looking at right now: active tab, its controls, and the other tabs
      const t = await activeTab();
      const others = (await chrome.tabs.query({})).filter((x) => x.id !== t.id).slice(0, 14)
        .map((x) => `[${x.id}] ${appOf(x.url) || (x.title || "").slice(0, 30)}`).join(", ");
      let page = "";
      try { page = await snapshotAll(t, [70, 700]); } catch (e) { page = `${t.title} - ${t.url}\n(${e.message})`; }
      let wa = "";
      if (hostOf(t.url) === "web.whatsapp.com") { try { const s = await inPage(t, "waState"); wa = s && s.chat ? `\nWhatsApp chat open: ${s.chat}` : "\nWhatsApp: no chat open"; } catch { /* ignore */ } }
      return `ACTIVE TAB [${t.id}] <${appOf(t.url)}>${wa}\n${page}\n\nOther tabs: ${others || "none"}`;
    }
    case "read": return await snapshotAll(await pickTab(m));
    case "text": return await inPage(await pickTab(m), "text", [Number(m.offset) || 0, 5000]);
    case "selected": {
      const t = await pickTab(m);
      const s = await inPage(t, "selection");
      return s ? `Selected text: "${s}"` : "nothing is selected on the page";
    }
    case "links": return await inPage(await pickTab(m), "links");
    case "find": {
      const r = await inPage(await pickTab(m), "has", [spec(m)]);
      return r ? `found ${r}` : `"${m.text}" is not on the page`;
    }
    case "screenshot": {
      const t = await pickTab(m);
      return await chrome.tabs.captureVisibleTab(t.windowId, { format: "jpeg", quality: 60 });
    }

    // ------------------------------------------------ tabs and windows
    case "open": {         // reuse a tab that already has this site; only a new tab when asked ("another", new:true)
      const url = resolveApp(m.url) || normUrl(m.url);
      if (url && !/^(https?|file|about):/i.test(url)) throw new Error(`"${m.url}" isn't a web address or a known app`);
      if (url && !m.new) {
        const host = hostOf(url);
        const app = appOf(url);
        const same = (await chrome.tabs.query({})).filter((t) => host && (hostOf(t.url) === host || (APPS_KNOWN.has(app) && appOf(t.url) === app)));
        if (same.length) {
          const t = same.find((x) => x.active) || same[0];
          let wantPath = ""; try { const u = new URL(url); wantPath = u.pathname.replace(/\/$/, "") + u.search; } catch { /* ignore */ }
          const havePath = (() => { try { const u = new URL(t.url); return u.pathname.replace(/\/$/, "") + u.search; } catch { return ""; } })();
          if (wantPath && wantPath !== havePath && !/whatsapp|mail\.google/.test(host)) { await chrome.tabs.update(t.id, { url }); await ready(t); }
          await focusTab(t);
          return `switched to the ${appOf(url) || host} tab that was already open: ${await whereNow(t)}`;
        }
      }
      const t = await chrome.tabs.create({ url, active: true });
      if (url) await ready(t);
      return `opened ${await whereNow(t)}`;
    }
    case "new_window": {
      const w = await chrome.windows.create({ url: normUrl(m.url), focused: true, incognito: !!m.incognito });
      return `opened a new ${m.incognito ? "private " : ""}window`;
    }
    case "close": {
      if (m.tab === "others") {
        const keep = (await activeTab()).id;
        const ids = (await chrome.tabs.query({ currentWindow: true })).filter((t) => t.id !== keep).map((t) => t.id);
        await chrome.tabs.remove(ids); return `closed ${ids.length} other tabs`;
      }
      if (m.tab === "duplicates") {
        const seen = new Set(), ids = [];
        for (const t of await chrome.tabs.query({})) { if (seen.has(t.url)) ids.push(t.id); else seen.add(t.url); }
        if (ids.length) await chrome.tabs.remove(ids); return `closed ${ids.length} duplicate tabs`;
      }
      if (m.all && m.tab) {                           // "close all the YouTube tabs"
        const q = String(m.tab).toLowerCase();
        const ids = (await chrome.tabs.query({})).filter((t) => (t.title + t.url + appOf(t.url)).toLowerCase().includes(q)).map((t) => t.id);
        if (ids.length) await chrome.tabs.remove(ids); return `closed ${ids.length} tabs matching "${m.tab}"`;
      }
      const t = await pickTab(m); await chrome.tabs.remove(t.id); return "closed " + brief(t);
    }
    case "switch": {
      let t;
      if (m.tab === "next" || m.tab === "previous") {
        const all = (await chrome.tabs.query({ currentWindow: true })).sort((a, b) => a.index - b.index);
        const i = all.findIndex((x) => x.active);
        t = all[(i + (m.tab === "next" ? 1 : all.length - 1)) % all.length];
      } else t = await pickTab(m);
      await focusTab(t); return "switched to " + brief(t);
    }
    case "navigate": {
      const t = await pickTab(m);
      await chrome.tabs.update(t.id, { url: normUrl(m.url) }); await ready(t);
      return "went to " + await whereNow(t);
    }
    case "back": case "forward": {
      const t = await pickTab(m), n = m.cmd === "back" ? -1 : 1, before = t.url;
      try { await inPage(t, "history", [n]); }
      catch { await (n < 0 ? chrome.tabs.goBack(t.id) : chrome.tabs.goForward(t.id)); }
      await sleep(250); await waitLoaded(t.id, 6000);
      const now = await chrome.tabs.get(t.id);
      if (now.url === before) throw new Error(`nothing to go ${m.cmd} to`);
      return `went ${m.cmd}: ` + await whereNow(t);
    }
    case "reload": { const t = await pickTab(m); await chrome.tabs.reload(t.id, { bypassCache: !!m.hard }); await ready(t); return "reloaded"; }
    case "pin": case "unpin": { const t = await pickTab(m); await chrome.tabs.update(t.id, { pinned: m.cmd === "pin" }); return `${m.cmd}ned ${appOf(t.url) || t.title}`.replace("unpinned", "unpinned").replace("pinned", "pinned"); }
    case "mute": case "unmute": { const t = await pickTab(m); await chrome.tabs.update(t.id, { muted: m.cmd === "mute" }); return `${m.cmd}d ${appOf(t.url) || t.title}`; }
    case "duplicate": { const t = await pickTab(m); const d = await chrome.tabs.duplicate(t.id); return "duplicated: " + brief(d); }
    case "reopen": {
      const [s] = await chrome.sessions.getRecentlyClosed({ maxResults: 1 });
      if (!s) throw new Error("there is no recently closed tab to reopen");
      await chrome.sessions.restore(s.sessionId);
      return "reopened " + ((s.tab && s.tab.title) || "the last closed window");
    }
    case "zoom": {
      const t = await pickTab(m); const cur = await chrome.tabs.getZoom(t.id);
      const v = String(m.value || "in");
      const nz = v === "in" ? cur + 0.1 : v === "out" ? cur - 0.1 : v === "reset" ? 1 : parseFloat(v) > 5 ? parseFloat(v) / 100 : parseFloat(v);
      await chrome.tabs.setZoom(t.id, Math.min(5, Math.max(0.25, nz)));
      return `zoom is ${Math.round((await chrome.tabs.getZoom(t.id)) * 100)}%`;
    }
    case "group": {
      const words = String(m.tabs || m.tab || "current").split(",").map((x) => x.trim());
      const ids = [];
      for (const w of words) { try { ids.push((await pickTab({ tab: w })).id); } catch { /* skip */ } }
      if (!ids.length) throw new Error("no tabs matched to group");
      const g = await chrome.tabs.group({ tabIds: ids });
      if (m.name) await chrome.tabGroups.update(g, { title: m.name });
      return `grouped ${ids.length} tab(s)${m.name ? ` as "${m.name}"` : ""}`;
    }
    case "history": {
      const items = await chrome.history.search({ text: m.text || "", maxResults: 10, startTime: Date.now() - 90 * 864e5 });
      return items.map((h) => `${(h.title || "").slice(0, 60)} - ${h.url.slice(0, 90)} (${new Date(h.lastVisitTime).toLocaleString()})`).join("\n") || "nothing in history for that";
    }
    case "bookmark": {
      if (m.text && !m.url) {
        const found = await chrome.bookmarks.search(m.text);
        return found.filter((b) => b.url).slice(0, 8).map((b) => `${b.title} - ${b.url}`).join("\n") || "no bookmark matches";
      }
      const t = await pickTab(m);
      await chrome.bookmarks.create({ title: t.title, url: t.url });
      return `bookmarked "${(t.title || "").slice(0, 50)}"`;
    }

    // ------------------------------------------------ doing things on the page
    case "click": {
      const t = await pickTab(m);
      const known = lastOpened && lastOpened.at;
      let r = await inPage(t, "click", [spec(m)]);
      if (r && r.navigated) { await ready(t); return "clicked; the page changed to " + await whereNow(t); }
      if (!r || !r.ok) fail(r, "click failed");
      let how = "";
      if (r.noEffect && m.trusted !== false) {                    // nothing reacted: wait for a slow reaction, else click like a real mouse
        await sleep(350);
        const cur = await chrome.tabs.get(t.id);
        const opened = lastOpened && lastOpened.at !== known && Date.now() - lastOpened.at < 2000;
        if (!opened && cur.status !== "loading" && cur.url === t.url && !r.frame) {
          try { await trustedClick(t.id, r.x, r.y, m.double); how = ` (the page ignored a normal click, so I used a real mouse click at ${r.x},${r.y})`; await sleep(250); }
          catch (e) { how = " (no visible reaction; a real click wasn't possible)"; }
        }
      }
      if (r.moved) await ready(t);
      let extra = "";
      if (lastOpened && lastOpened.at !== known && Date.now() - lastOpened.at < 2500) {
        try { const nt = await chrome.tabs.get(lastOpened.id); await waitLoaded(nt.id, 5000); extra = ` -> it opened a new tab: ${await whereNow(nt)}`; } catch { /* closed again */ }
      }
      const multi = r.count > 1 && !m.nth && !m.ref ? ` (${r.count} matches; used the best one, use nth to pick another)` : "";
      return `clicked ${r.desc}${r.moved ? ` -> now ${await whereNow(t)}` : ""}${extra}${how}${multi}`;
    }
    case "hover": {
      const t = await pickTab(m);
      const r = await inPage(t, "hover", [spec(m)]);
      if (!r || !r.ok) fail(r, "hover failed");
      let how = "";
      if (r.noEffect && !r.frame && m.trusted !== false) {        // CSS :hover menus only react to a real mouse move
        try { await trustedMove(t.id, r.x, r.y); await sleep(350); how = " (moved the real mouse there)"; } catch { /* no debugger */ }
      }
      return `hovering over ${r.desc}${how}`;
    }
    case "drag": {
      if (!m.to) throw new Error("drag needs `to`: the text of where to drop it");
      const r = await inPage(await pickTab(m), "drag", [spec(m), { text: m.to }]);
      if (!r || !r.ok) fail(r, "drag failed");
      return `dragged ${r.desc}`;
    }
    case "check": {
      const r = await inPage(await pickTab(m), "check", [spec(m), m.on !== false]);
      if (!r || !r.ok) fail(r, "couldn't set that checkbox");
      return r.desc;
    }
    case "type": case "clear": {
      const t = await pickTab(m);
      const text = m.cmd === "clear" ? "" : String(m.text == null ? "" : m.text);
      if (m.cmd === "type" && text === "") throw new Error("nothing to type: give text");
      const s = { ref: m.ref, field: m.field, visual: m.quiet ? false : undefined };
      let r = await inPage(t, "type", [s, text, { append: !!m.append, submit: !!m.submit }]);
      if (r && r.navigated) { await ready(t); return "typed; the page changed to " + await whereNow(t); }
      if (r && r.retry === "trusted") {              // the page ignored normal input: use real keystrokes once
        await inPage(t, "type", [s, "", {}]);        // focus + clear
        await trustedText(t.id, text);
        r = await inPage(t, "type", [s, "", { append: true, submit: !!m.submit }]) || r;
        if (r && r.ok === false && r.err) throw new Error(r.err);
      }
      if (!r || !r.ok) fail(r, "typing failed");
      let extra = "";
      if (m.submit && r.submitted && r.submitted.handled === false) {   // nothing reacted to Enter: send a real one
        await trustedKey(t.id, "Enter"); await sleep(300); extra = " (real Enter key)";
      } else if (m.submit && r.submitted) extra = r.submitted.how ? ` and pressed Enter (${r.submitted.how})` : " and pressed Enter";
      return `${m.cmd === "clear" ? "cleared" : "typed into"} ${r.field}: "${r.value}"${extra}`;
    }
    case "fill": {
      const t = await pickTab(m);
      const pairs = Array.isArray(m.fields) ? m.fields : String(m.fields || "").split(";").filter(Boolean);
      if (!pairs.length) throw new Error('fill needs fields like ["Name=Aniket", "Subscribe=yes"]');
      const r = await inPage(t, "fill", [pairs]);
      if (r && r.navigated) { await ready(t); return "filled; the page changed"; }
      if (!r || !r.ok) throw new Error(r && r.report || "couldn't fill the form");
      return r.report;
    }
    case "key": {
      const t = await pickTab(m);
      if (m.trusted) { await trustedKey(t.id, m.key); return "pressed " + m.key + " (real key)"; }
      const r = await inPage(t, "key", [m.key]);
      if (r && r.navigated) { await ready(t); return `pressed ${m.key}; the page changed to ` + await whereNow(t); }
      if (r && r.handled === false) { await trustedKey(t.id, m.key); return `pressed ${m.key} (real key)`; }
      return "pressed " + m.key + (r && r.how ? ` (${r.how})` : "");
    }
    case "scroll": {
      const t = await pickTab(m);
      return await inPage(t, "scroll", [m.direction || "down", m.amount, m.text ? { text: m.text } : null]);
    }
    case "wait_for": {
      const t = await pickTab(m);
      const r = await inPage(t, "waitFor", [m.text, !!m.gone, Math.min((m.secs || m.timeout || 8) * 1000, 30000)]);
      if (!r || !r.ok) throw new Error(r && r.err || "timed out");
      return `"${m.text}" ${m.gone ? "is gone" : "is on the page"}`;
    }
    case "media": {
      const t = await pickTab(m);
      const r = await inPage(t, "media", [m.media_action || m.action || "toggle", m.value]);
      if (!r || !r.ok) throw new Error(r && r.err || "media control failed");
      return r.state;
    }

    // ------------------------------------------------ WhatsApp Web
    case "wa_chat": {      // open a chat by name (finds the open WhatsApp tab, or opens one and waits for it)
      let t = (await chrome.tabs.query({ url: "https://web.whatsapp.com/*" }))[0];
      if (!t) { t = await chrome.tabs.create({ url: "https://web.whatsapp.com/", active: true }); await ready(t); }
      else if (!t.active) await focusTab(t);
      const r = await inPage(t, "waChat", [m.name || m.text || ""]);
      if (!r || !r.ok) throw new Error(r && r.err || "couldn't open that chat");
      return `opened WhatsApp chat "${r.chat}"` + (r.note ? ` (${r.note})` : "");
    }
    case "wa_send": {      // send text in the chat that is open
      const t = (await chrome.tabs.query({ url: "https://web.whatsapp.com/*" }))[0];
      if (!t) throw new Error("WhatsApp Web isn't open");
      const r = await inPage(t, "waSend", [m.text || ""]);
      if (!r || !r.ok) throw new Error(r && r.err || "couldn't send");
      return `sent to ${r.chat || "the open chat"}: "${(m.text || "").slice(0, 60)}"`;
    }

    case "batch": {        // several steps in ONE round trip: [{"do":"click","text":"Sign in"},{"do":"type","field":"email","text":"x"}]
      const steps = (Array.isArray(m.steps) ? m.steps : []).map((x) => { try { return typeof x === "string" ? JSON.parse(x) : x; } catch { return null; } }).filter(Boolean);
      if (!steps.length) throw new Error('batch needs steps like ["{\"do\":\"click\",\"text\":\"Sign in\"}"]');
      const out = [];
      for (const st of steps.slice(0, 12)) {
        const cmd = st.do || st.cmd || st.action;
        if (!cmd || cmd === "batch") { out.push("skipped a bad step"); continue; }
        try { out.push(`${out.length + 1}. ${cmd}: ${await run({ ...st, cmd, tab: st.tab != null ? st.tab : m.tab })}`); }
        catch (e) { out.push(`${out.length + 1}. ${cmd} FAILED: ${e.message} (stopped here)`); break; }
      }
      return out.join("\n");
    }
    // ------------------------------------------------ calls
    case "call_state": {   // looks at every WhatsApp / Meet tab and reports the most advanced call state
      const rank = { none: 0, ended: 1, prejoin: 2, incoming: 3, ringing: 4, in_call: 5 };
      let best = null;
      for (const t of await callTabs()) {
        try { const r = await inPage(t, "callState"); if (r && r.phase && (!best || rank[r.phase] > rank[best.phase])) best = { ...r, tab: t.id }; } catch { /* not injectable */ }
      }
      return best ? `phase: ${best.phase}; mic: ${best.mic}; platform: ${best.platform}; tab: ${best.tab}${best.buttons ? "; buttons: " + best.buttons : ""}` : "phase: none";
    }
    case "call_start": {
      const t = (await chrome.tabs.query({ url: "https://web.whatsapp.com/*" }))[0];
      if (!t) throw new Error("WhatsApp Web isn't open");
      const r = await inPage(t, "callStart");
      if (!r || !r.ok) throw new Error(r && r.err || "couldn't start the call");
      return "call started";
    }
    case "call_answer": case "call_end": case "call_mic": {
      const fn = { call_answer: "callAnswer", call_end: "callEnd", call_mic: "callMic" }[m.cmd];
      let done = "", err = "";
      for (const t of await callTabs()) {
        try { const r = await inPage(t, fn, m.cmd === "call_mic" ? [m.on !== false] : []); if (r && r.ok) { done = r.mic ? `mic ${r.mic}` : m.cmd.replace("call_", "") + " done"; break; } err = r && r.err || err; } catch { /* skip */ }
      }
      if (!done) throw new Error(err || "no call page found");
      return done;
    }
    case "meet_join": {
      let code = String(m.url || "").trim();
      if (/^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(code)) code = "https://meet.google.com/" + code;
      if (!/^https:\/\/meet\.google\.com\//.test(code)) throw new Error("that isn't a Google Meet link or code");
      const t = await chrome.tabs.create({ url: code, active: true });
      await ready(t);
      let last = "the Join button never appeared";
      for (let i = 0; i < 30; i++) {
        const cur = await chrome.tabs.get(t.id);
        let r = null; try { r = await inPage(cur, "meetJoin", [`Jarvis (${m.owner || "assistant"})`]); } catch { /* still loading */ }
        if (r && r.ok) return r.state;
        if (r && r.err) last = r.err;
        await sleep(1000);
      }
      throw new Error(last);
    }
    case "wait": await sleep(Math.min(m.ms || 1000, 10000)); return "waited";
    default: throw new Error("unknown command " + m.cmd);
  }
}
