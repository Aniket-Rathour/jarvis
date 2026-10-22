// Jarvis Hands: connects to the Jarvis app on this PC (ws://127.0.0.1:8765) and carries out its browser commands.
// Same approach as Claude in Chrome: page reading and element lookup through scripting, real (trusted) clicks and
// keystrokes through the debugger API, tabs through the tabs API.
const URL = "ws://127.0.0.1:8765";
let ws = null, connected = false;

// ---------------------------------------------------------------- connection
function setBadge(on) {
  connected = on;
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(URL); } catch (e) { return; }
  ws.onopen = () => { setBadge(true); ws.send(JSON.stringify({ hello: "jarvis-hands", version: 1 })); };
  ws.onclose = () => setBadge(false);
  ws.onerror = () => setBadge(false);
  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.pong) return;
    let reply;
    try { reply = { id: msg.id, ok: true, result: await run(msg) }; }
    catch (e) { reply = { id: msg.id, ok: false, error: String(e && e.message || e) }; }
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(reply));
  };
}
// a live websocket message every 20 s keeps the MV3 service worker awake; the alarm revives it if it was stopped
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ ping: 1 })); else connect(); }, 20000);
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onMessage.addListener((m, _s, send) => { if (m === "status") send({ connected }); });
connect();

// ---------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!t) throw new Error("no active tab");
  return t;
}

async function pickTab(m) {
  if (m.tab == null || m.tab === "current") return activeTab();
  if (typeof m.tab === "number") return chrome.tabs.get(m.tab);
  const q = String(m.tab).toLowerCase();
  const hit = (await chrome.tabs.query({})).find((t) => (t.title || "").toLowerCase().includes(q) || (t.url || "").toLowerCase().includes(q));
  if (!hit) throw new Error(`no tab matching "${m.tab}"`);
  return hit;
}

const brief = (t) => `[${t.id}] ${t.active ? "* " : ""}${(t.title || "").slice(0, 70)} - ${(t.url || "").slice(0, 90)}`;

async function waitLoaded(tabId, ms = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete") return;
    await sleep(150);
  }
}

async function inPage(tabId, func, args = []) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return r && r.result;
}

// ---- debugger (trusted input). Detaches by itself a few seconds after the last use so the banner goes away.
const attached = new Set(); const timers = {};
async function cdp(tabId, method, params = {}) {
  if (!attached.has(tabId)) {
    await chrome.debugger.attach({ tabId }, "1.3");
    attached.add(tabId);
  }
  clearTimeout(timers[tabId]);
  timers[tabId] = setTimeout(() => chrome.debugger.detach({ tabId }).catch(() => {}).finally(() => attached.delete(tabId)), 4000);
  return chrome.debugger.sendCommand({ tabId }, method, params);
}
chrome.debugger.onDetach.addListener((src) => attached.delete(src.tabId));

// ---------------------------------------------------------------- page-side functions (self-contained)
function pageSnapshot() {
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=switch],[role=option],[role=combobox],[contenteditable=""],[contenteditable=true],[onclick]';
  window.__jv = window.__jv || { n: 0, map: new Map() };
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none"; };
  const label = (e) => (e.getAttribute("aria-label") || e.innerText || e.value || e.placeholder || e.title || e.alt || e.name || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const out = []; let hidden = 0;
  for (const e of document.querySelectorAll(SEL)) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight * 3) { hidden++; continue; }
    if (out.length >= 90) break;
    let id = e.dataset.jv;
    if (!id) { id = String(++window.__jv.n); e.dataset.jv = id; }
    const kind = e.tagName === "A" ? "link" : e.tagName === "INPUT" ? "input:" + (e.type || "text") : e.tagName === "TEXTAREA" ? "textarea" : e.tagName === "SELECT" ? "select" : (e.getAttribute("role") || e.tagName.toLowerCase());
    out.push(`[${id}] ${kind} "${label(e)}"`);
  }
  const text = (document.body.innerText || "").replace(/\s+/g, " ").trim().slice(0, 1200);
  return `${document.title} - ${location.href}\nInteractive (${out.length} listed, ${hidden} further away; scroll for more):\n${out.join("\n")}\n\nPage text: ${text}`;
}

function pageFind(ref, text, scroll) {
  const SEL = 'a[href],button,input,select,textarea,summary,label,[role],[onclick],[contenteditable=true],h1,h2,h3,h4,li,span,div,p,td';
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none"; };
  const label = (e) => (e.getAttribute("aria-label") || e.innerText || e.value || e.placeholder || e.title || e.alt || e.name || "").replace(/\s+/g, " ").trim().toLowerCase();
  let el = null;
  if (ref) el = document.querySelector(`[data-jv="${ref}"]`);
  else if (text) {
    const q = text.toLowerCase(); let best = 1e9;
    for (const e of document.querySelectorAll(SEL)) {
      if (!vis(e)) continue;
      const l = label(e); if (!l || l.length > 200) continue;
      let s = l === q ? 0 : l.startsWith(q) ? 1 : l.includes(q) ? 2 : 1e9;
      if (s === 1e9) continue;
      s = s * 1000 + l.length + (/^(a|button|input|select|textarea)$/i.test(e.tagName) ? 0 : 50);
      if (s < best) { best = s; el = e; }
    }
  }
  if (!el) return null;
  if (scroll) el.scrollIntoView({ block: "center", inline: "center" });
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), label: label(el).slice(0, 60), tag: el.tagName.toLowerCase() };
}

function pageFocus(ref, text) {
  const q = (text || "").toLowerCase();
  let el = ref ? document.querySelector(`[data-jv="${ref}"]`) : null;
  if (!el && q) {
    for (const e of document.querySelectorAll("input,textarea,[contenteditable=true],[role=textbox],[role=combobox]")) {
      const l = (e.getAttribute("aria-label") || e.placeholder || e.name || e.id || e.title || "").toLowerCase();
      if (l.includes(q)) { el = e; break; }
    }
  }
  if (!el) el = document.activeElement && document.activeElement !== document.body ? document.activeElement
    : document.querySelector("input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable=true]");
  if (!el) return null;
  el.scrollIntoView({ block: "center" }); el.focus();
  if (el.select) el.select(); else { document.execCommand("selectAll", false); }
  return (el.tagName + " " + (el.placeholder || el.name || el.getAttribute("aria-label") || "")).trim();
}

// ---------------------------------------------------------------- keys
const KEYS = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8, Delete: 46, ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, PageDown: 34, PageUp: 33, Home: 36, End: 35, " ": 32 };
async function pressKey(tabId, spec) {
  const parts = String(spec).split("+"); const key = parts.pop();
  const mods = parts.reduce((m, p) => m | ({ alt: 1, ctrl: 2, control: 2, meta: 4, cmd: 4, shift: 8 }[p.toLowerCase()] || 0), 0);
  const k = key.length === 1 ? key : (key[0].toUpperCase() + key.slice(1));
  const vk = KEYS[k] || (k.length === 1 ? k.toUpperCase().charCodeAt(0) : 0);
  const code = k.length === 1 ? (/[a-z]/i.test(k) ? "Key" + k.toUpperCase() : /\d/.test(k) ? "Digit" + k : "") : k;
  const text = mods & 6 ? undefined : k === "Enter" ? "\r" : k.length === 1 ? k : undefined;
  const base = { key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods };
  await cdp(tabId, "Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...base, ...(text ? { text } : {}) });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

async function clickAt(tabId, x, y, count = 1, button = "left") {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  for (let i = 1; i <= count; i++) {
    await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button, clickCount: i });
    await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button, clickCount: i });
  }
}

// ---------------------------------------------------------------- commands
async function run(m) {
  switch (m.cmd) {
    case "tabs": {
      return (await chrome.tabs.query({})).map(brief).join("\n") || "no tabs";
    }
    case "open": {
      const url = m.url ? (/^[a-z]+:/i.test(m.url) ? m.url : "https://" + m.url) : undefined;
      const t = await chrome.tabs.create({ url, active: true });
      if (url) await waitLoaded(t.id);
      return "opened " + brief(await chrome.tabs.get(t.id));
    }
    case "close": {
      if (m.tab === "others") {
        const keep = (await activeTab()).id;
        const ids = (await chrome.tabs.query({ currentWindow: true })).filter((t) => t.id !== keep).map((t) => t.id);
        await chrome.tabs.remove(ids); return `closed ${ids.length} other tabs`;
      }
      if (m.tab === "all_matching" && m.match) {
        const q = m.match.toLowerCase();
        const ids = (await chrome.tabs.query({})).filter((t) => (t.title + t.url).toLowerCase().includes(q)).map((t) => t.id);
        await chrome.tabs.remove(ids); return `closed ${ids.length} tabs matching "${m.match}"`;
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
      await chrome.tabs.update(t.id, { active: true }); await chrome.windows.update(t.windowId, { focused: true });
      return "switched to " + brief(t);
    }
    case "navigate": {
      const t = await pickTab(m);
      const url = /^[a-z]+:/i.test(m.url) ? m.url : "https://" + m.url;
      await chrome.tabs.update(t.id, { url }); await waitLoaded(t.id);
      return "went to " + brief(await chrome.tabs.get(t.id));
    }
    case "back": { const t = await pickTab(m); await chrome.tabs.goBack(t.id); await waitLoaded(t.id); return "went back"; }
    case "forward": { const t = await pickTab(m); await chrome.tabs.goForward(t.id); await waitLoaded(t.id); return "went forward"; }
    case "reload": { const t = await pickTab(m); await chrome.tabs.reload(t.id); await waitLoaded(t.id); return "reloaded"; }
    case "read": {
      const t = await pickTab(m);
      if (/^(chrome|edge|about|chrome-extension):/.test(t.url || "")) return `${t.title} - ${t.url}\n(a browser page; its contents can't be read)`;
      return await inPage(t.id, pageSnapshot);
    }
    case "click": {
      const t = await pickTab(m);
      if (m.x != null && m.y != null) { await clickAt(t.id, m.x, m.y, m.double ? 2 : 1); return `clicked at ${m.x},${m.y}`; }
      let hit = await inPage(t.id, pageFind, [m.ref || null, m.text || null, true]);
      if (!hit) { await inPage(t.id, pageSnapshot); hit = await inPage(t.id, pageFind, [m.ref || null, m.text || null, true]); }
      if (!hit) throw new Error(`nothing found to click for ${m.ref ? "ref " + m.ref : '"' + m.text + '"'}`);
      await sleep(120);                                           // let smooth scrolling settle
      hit = await inPage(t.id, pageFind, [m.ref || null, m.text || null, false]) || hit;
      try { await clickAt(t.id, hit.x, hit.y, m.double ? 2 : 1, m.right ? "right" : "left"); }
      catch (e) {                                                 // debugger refused (DevTools open, special page): plain DOM click
        await inPage(t.id, (x, y) => { const el = document.elementFromPoint(x, y); el && el.click(); }, [hit.x, hit.y]);
      }
      await sleep(250);
      return `clicked ${hit.tag} "${hit.label}"`;
    }
    case "type": {
      const t = await pickTab(m);
      const f = await inPage(t.id, pageFocus, [m.ref || null, m.field || null]);
      if (!f) throw new Error("no text field found to type into");
      await sleep(60);
      if (m.text) await cdp(t.id, "Input.insertText", { text: m.text });
      if (m.submit) { await sleep(80); await pressKey(t.id, "Enter"); }
      return `typed into ${f}${m.submit ? " and pressed Enter" : ""}`;
    }
    case "key": { const t = await pickTab(m); await pressKey(t.id, m.key); return "pressed " + m.key; }
    case "scroll": {
      const t = await pickTab(m);
      await inPage(t.id, (dir, amt) => {
        const h = innerHeight * (amt || 0.8);
        if (dir === "top") scrollTo({ top: 0, behavior: "smooth" });
        else if (dir === "bottom") scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        else scrollBy({ top: dir === "up" ? -h : dir === "down" ? h : 0, left: dir === "left" ? -h : dir === "right" ? h : 0, behavior: "smooth" });
      }, [m.direction || "down", m.amount]);
      return "scrolled " + (m.direction || "down");
    }
    case "find": {
      const t = await pickTab(m);
      const hit = await inPage(t.id, pageFind, [null, m.text, true]);
      return hit ? `found ${hit.tag} "${hit.label}"` : `"${m.text}" not found on the page`;
    }
    case "screenshot": {
      const t = await pickTab(m);
      return await chrome.tabs.captureVisibleTab(t.windowId, { format: "jpeg", quality: 60 });   // data: URL
    }
    case "wait": await sleep(Math.min(m.ms || 1000, 10000)); return "waited";
    default: throw new Error("unknown command " + m.cmd);
  }
}
