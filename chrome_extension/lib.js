// Page-side toolkit injected by Jarvis Hands (window.__jv). Plain DOM events, no debugger bar needed.
// Works through open shadow roots; background.js runs it inside iframes too (J.fid = frame id).
(() => {
  const V = 7;
  if (window.__jv && window.__jv.v === V) return;
  const J = (window.__jv = { v: V, n: (window.__jv && window.__jv.n) || 0, fid: (window.__jv && window.__jv.fid) || 0 });

  // ------------------------------------------------------------ basics
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const low = (s) => norm(s).toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const deep = (sel, root = document) => {                    // querySelectorAll that also looks inside open shadow roots
    const out = [], seen = new Set();
    const walk = (r) => {
      if (seen.has(r)) return; seen.add(r);
      for (const e of r.querySelectorAll(sel)) out.push(e);
      for (const h of r.querySelectorAll("*")) if (h.shadowRoot) walk(h.shadowRoot);
    };
    walk(root);
    return out;
  };
  const vis = (e) => {
    if (!e || !e.isConnected) return false;
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e);
    return s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const INTERACTIVE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=tab],' +
    '[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=checkbox],[role=radio],[role=switch],[role=option],' +
    '[role=combobox],[role=textbox],[role=searchbox],[role=listitem],[role=row],[role=gridcell],[role=treeitem],[role=slider],' +
    '[contenteditable=""],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"]),video,audio';
  const GENERIC = "span,div,li,p,h1,h2,h3,h4,h5,h6,td,th,label,img[alt],svg[aria-label],summary,dt,dd,legend";
  const EDITABLE = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=file]):not([type=range]),textarea,[contenteditable=""],[contenteditable=true],[role=textbox],[role=searchbox],[role=combobox]';
  const isEditable = (e) => !!e && e.nodeType === 1 && e.matches && e.matches(EDITABLE);

  const label = (e) => {
    let t = e.getAttribute("aria-label") || "";
    if (!t) {
      const lb = e.getAttribute("aria-labelledby");
      if (lb) t = lb.split(/\s+/).map((id) => { const x = document.getElementById(id); return x ? x.innerText : ""; }).join(" ");
    }
    if (!t && e.matches("[role=listitem],[role=row],[role=gridcell]")) {
      const tt = e.querySelector("span[title]");
      if (tt) t = tt.getAttribute("title") + " " + norm(e.innerText).slice(0, 40);
    }
    t = t || e.getAttribute("title") || e.placeholder || e.getAttribute("aria-placeholder") || "";
    if (!t && e.labels && e.labels[0]) {
      const c = e.labels[0].cloneNode(true);
      c.querySelectorAll("select,input,textarea,button,option").forEach((x) => x.remove());
      t = c.textContent;
    }
    if (!t) t = e.innerText || (e.tagName === "INPUT" && /button|submit|reset/.test(e.type) ? e.value : "") || e.alt || e.name || "";
    if (!t) { const im = e.querySelector && e.querySelector("img[alt],[aria-label],[data-icon],svg title"); if (im) t = im.alt || im.getAttribute("aria-label") || im.getAttribute("data-icon") || im.textContent || ""; }
    return norm(t).slice(0, 90);
  };
  const kindOf = (e) => {
    const t = e.tagName;
    if (t === "A") return "link";
    if (t === "INPUT") return "input:" + (e.type || "text");
    if (t === "TEXTAREA") return "textarea";
    if (t === "SELECT") return "select";
    if (t === "VIDEO" || t === "AUDIO") return t.toLowerCase();
    return e.getAttribute("role") || (e.isContentEditable ? "editor" : t.toLowerCase());
  };
  const tag = (e) => { if (!e.dataset.jv) { J.n++; e.dataset.jv = J.fid ? `f${J.fid}_${J.n}` : String(J.n); } return e.dataset.jv; };
  const describe = (e) => `${kindOf(e)} "${label(e)}"`;
  const state = (e) => {
    const x = [];
    if (e.disabled || e.getAttribute("aria-disabled") === "true") x.push("disabled");
    if (e.checked || e.getAttribute("aria-checked") === "true") x.push("checked");
    if (e.getAttribute("aria-selected") === "true") x.push("selected");
    if (e.getAttribute("aria-expanded")) x.push(e.getAttribute("aria-expanded") === "true" ? "expanded" : "collapsed");
    if (e.tagName === "SELECT") x.push(`value="${norm(e.selectedOptions[0] ? e.selectedOptions[0].text : "")}"`);
    else if (e.type === "password" || /^cc-/.test(e.getAttribute("autocomplete") || "")) x.push("secret field");
    else if (isEditable(e)) { const v = norm(e.value != null && e.tagName !== "DIV" ? e.value : e.innerText).slice(0, 50); if (v) x.push(`value="${v}"`); }
    return x;
  };

  // ------------------------------------------------------------ reading
  J.snapshot = (maxItems = 110, textLen = 1400) => {
    const items = []; let far = 0;
    for (const e of deep(INTERACTIVE)) {
      if (!vis(e)) continue;
      const r = e.getBoundingClientRect();
      if (r.bottom < -80 || r.top > innerHeight * 2.5 || items.length >= maxItems) { far++; continue; }
      const extra = state(e);
      if (r.top > innerHeight || r.bottom < 0) extra.push("below");
      items.push(`[${tag(e)}] ${describe(e)}${extra.length ? " (" + extra.join(", ") + ")" : ""}`);
    }
    const main = document.querySelector("main,[role=main]") || document.body;
    const sel = J.selection();
    const dlg = deep('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]').filter(vis)[0];
    const doc = document.documentElement, pct = Math.round(100 * (scrollY + innerHeight) / Math.max(doc.scrollHeight, 1));
    return `${document.title} - ${location.href}  (scrolled to ${pct}%)\n` +
      (dlg ? `Open dialog: "${label(dlg) || norm(dlg.innerText).slice(0, 60)}"\n` : "") +
      (sel ? `Selected text: "${sel.slice(0, 200)}"\n` : "") +
      (isEditable(document.activeElement) ? `Focused: ${describe(document.activeElement)}\n` : "") +
      `Interactive (${items.length} listed, ${far} further away; scroll for more):\n${items.join("\n")}\n\nPage text: ${norm(main.innerText).slice(0, textLen)}`;
  };
  J.text = (offset = 0, limit = 5000) => {
    const main = document.querySelector("article,main,[role=main]") || document.body;
    const heads = deep("h1,h2,h3").filter(vis).slice(0, 12).map((h) => "# " + norm(h.innerText)).join("\n");
    const t = (main.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
    return `${document.title} - ${location.href}\n${heads ? "Headings:\n" + heads + "\n\n" : ""}Text ${offset}-${Math.min(offset + limit, t.length)} of ${t.length}:\n${t.slice(offset, offset + limit)}`;
  };
  J.selection = () => {
    const a = document.activeElement;
    if (a && (a.tagName === "TEXTAREA" || a.tagName === "INPUT") && a.selectionStart != null && a.selectionEnd > a.selectionStart) return a.value.slice(a.selectionStart, a.selectionEnd);
    return norm(String(getSelection() || ""));
  };
  J.fields = () => deep(EDITABLE).filter(vis).slice(0, 14).map((e) => `[${tag(e)}] ${describe(e)}`).join("; ") || "no text fields";
  J.links = () => deep("a[href]").filter(vis).slice(0, 60).map((a) => `${label(a)} -> ${a.href}`).join("\n");

  // ------------------------------------------------------------ finding
  const SYN = {
    message: ["type a message", "message", "write a message", "write a reply", "reply", "compose", "comment", "chat"],
    search: ["search", "find", "look up", "query", "search or start"],
    email: ["email", "e-mail", "mail"], subject: ["subject"], name: ["name"], password: ["password"], to: ["to", "recipients"],
  };
  J.findField = (q) => {
    const want = low(q); const alts = SYN[want] || [];
    let best = null, bs = 0;
    for (const e of deep(EDITABLE + ",select")) {
      if (!vis(e)) continue;
      const l = low([label(e), e.name, e.id, e.getAttribute("data-tab"), e.getAttribute("data-testid"), e.getAttribute("autocomplete")].join(" "));
      let s = 0;
      if (l.includes(want)) s = 3;
      for (const a of alts) if (l.includes(a)) s = Math.max(s, 2);
      if (!s) continue;
      if (want === "message" && e.closest("footer,[data-testid*=compose],form")) s += 2;
      if (want === "message" && e.closest("#side,[role=search]")) s -= 5;                 // never the chat-search box
      if (want === "search" && e.closest("#side,[role=search],header")) s += 1;
      if (s > bs) { bs = s; best = e; }
    }
    return best;
  };
  const byRef = (ref) => { const e = deep(`[data-jv="${CSS.escape(String(ref))}"]`)[0]; return e && e.isConnected ? e : null; };

  J.find = (spec) => {
    if (spec.ref) return { el: byRef(spec.ref), count: 1 };
    const q = low(spec.text || ""), kindQ = low(spec.kind || "");
    if (!q && !kindQ) return { el: null };
    let scope = null;
    if (spec.within) { scope = J.find({ text: spec.within }).el; if (!scope) return { el: null, near: [], why: `container "${spec.within}" not found` }; }
    const pool = deep(INTERACTIVE + "," + GENERIC, scope || document);
    const cands = [], seen = new Set();
    pool.forEach((e, order) => {
      if (seen.has(e) || !vis(e)) return; seen.add(e);
      if (kindQ && !low(kindOf(e)).includes(kindQ)) return;
      const l = low(label(e));
      let c = 0;
      if (q) {
        if (!l || l.length > 220) return;
        c = l === q ? 0 : l.startsWith(q) ? 1 : new RegExp("\\b" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(l) ? 2 : l.includes(q) ? 3 : 9;
        if (c === 9) return;
      }
      const r = e.getBoundingClientRect();
      const inView = r.bottom > 0 && r.top < innerHeight;
      cands.push({ e, c, order, l, s: c * 1000 + (e.matches(INTERACTIVE) ? 0 : 200) + Math.min(l.length, 150) + (inView ? 0 : 400) });
    });
    if (!cands.length) return { el: null, near: [] };
    if (spec.nth) {                                                 // "the second Reply button"
      const minC = Math.min(...cands.map((c) => c.c));
      let grp = cands.filter((c) => c.c === minC && c.e.matches(INTERACTIVE));
      if (grp.length < spec.nth) grp = cands.filter((c) => c.c <= Math.max(minC, 2));
      grp.sort((a, b) => a.order - b.order);
      const pick = grp[spec.nth - 1];
      return { el: pick && pick.e, count: grp.length, near: grp.slice(0, 6).map((c) => c.l) };
    }
    cands.sort((a, b) => a.s - b.s);
    const best = cands[0];
    const twins = cands.filter((c) => c.c === best.c && c.l === best.l && c.e.matches(INTERACTIVE)).length;
    return { el: best.e, count: twins || 1, near: cands.slice(0, 6).map((c) => c.l) };
  };
  // SPAs render late: look for up to `ms` before giving up
  const waitEl = async (spec, ms) => {
    ms = spec && spec.wait != null ? spec.wait : (ms == null ? 2500 : ms);
    let f = J.find(spec);
    for (let t = 0; !f.el && t < ms; t += 150) { await sleep(150); f = J.find(spec); }
    return f;
  };

  // ------------------------------------------------------------ show what is being done (a brief green ring, no bar)
  const flash = async (el, what) => {
    try {
      const r = el.getBoundingClientRect(), d = document.createElement("div");
      d.style.cssText = `position:fixed;left:${r.left - 3}px;top:${r.top - 3}px;width:${r.width + 6}px;height:${r.height + 6}px;` +
        "border:2px solid #22c55e;border-radius:6px;box-shadow:0 0 12px #22c55e;z-index:2147483647;pointer-events:none;transition:opacity .35s";
      const c = document.createElement("div");
      c.textContent = "Jarvis: " + what;
      c.style.cssText = "position:absolute;top:-22px;left:0;background:#16a34a;color:#fff;font:11px system-ui;padding:2px 6px;border-radius:4px;white-space:nowrap";
      d.setAttribute("data-jv-ui", "1"); c.setAttribute("data-jv-ui", "1");
      d.appendChild(c); document.documentElement.appendChild(d);
      setTimeout(() => { d.style.opacity = "0"; setTimeout(() => d.remove(), 400); }, 450);
      await sleep(110);
    } catch (e) { /* purely cosmetic */ }
  };

  // ------------------------------------------------------------ mouse
  const fire = (el, type, x, y, extra = {}) => {
    const P = type.startsWith("pointer") && window.PointerEvent ? PointerEvent : MouseEvent;
    el.dispatchEvent(new P(type, { bubbles: !/enter|leave/.test(type), cancelable: true, composed: true, view: window, clientX: x, clientY: y,
      button: extra.button || 0, buttons: /down|move/.test(type) && !/over/.test(type) && extra.held !== false ? 1 : 0, detail: extra.detail || 1,
      pointerId: 1, pointerType: "mouse", isPrimary: true, ctrlKey: !!extra.ctrl, shiftKey: !!extra.shift, ...extra }));
  };
  const center = (el) => {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  };
  const targetAt = (el, x, y) => {
    let t = document.elementFromPoint(x, y);
    if (!t || !(el.contains(t) || t.contains(el) || (t.getRootNode && t.getRootNode().host && el.contains(t.getRootNode().host)))) t = el;   // covered: aim at the element
    return t;
  };
  const clickEl = async (el, { double = false, right = false, ctrl = false, shift = false } = {}) => {
    const { x, y } = center(el); await sleep(15);
    const t = targetAt(el, x, y), b = right ? 2 : 0, o = { button: b, ctrl, shift };
    for (const ty of ["pointerover", "mouseover", "pointermove", "mousemove", "pointerdown", "mousedown"]) fire(t, ty, x, y, o);
    const f = isEditable(el) ? el : t; if (f.focus) f.focus({ preventScroll: true });
    for (const ty of ["pointerup", "mouseup"]) fire(t, ty, x, y, o);
    if (right) fire(t, "contextmenu", x, y, o);
    else {
      fire(t, "click", x, y, o);
      if (double) {
        for (const ty of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) fire(t, ty, x, y, { ...o, detail: 2 });
        fire(t, "dblclick", x, y, { ...o, detail: 2 });
      }
    }
    return { x, y };
  };
  J.idle = (quiet = 250, max = 1500) => new Promise((res) => {
    let t = setTimeout(done, quiet); const cap = setTimeout(done, max);
    const mo = new MutationObserver(() => { clearTimeout(t); t = setTimeout(done, quiet); });
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    function done() { mo.disconnect(); clearTimeout(t); clearTimeout(cap); res(true); }
  });
  // count only page changes, not Jarvis' own highlight ring
  const realMutations = (recs) => recs.filter((r) => {
    const own = (n) => n && n.nodeType === 1 && (n.hasAttribute("data-jv-ui") || (n.closest && n.closest("[data-jv-ui]")));
    return !(own(r.target) || (r.target.parentElement && own(r.target.parentElement)) ||
             Array.from(r.addedNodes).some(own) || Array.from(r.removedNodes).some(own));
  }).length;
  const miss = (spec, f) => ({ ok: false, notFound: true, err: f.why || `nothing found for ${spec.ref ? "ref " + spec.ref : '"' + (spec.text || spec.kind) + '"'}`, near: f.near });

  J.click = async (spec) => {
    const f = await waitEl(spec);
    if (!f.el) return miss(spec, f);
    const before = location.href; let muts = 0;
    const mo = new MutationObserver((recs) => { muts += realMutations(recs); });
    if (spec.visual !== false) await flash(f.el, "click");
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    const pos = await clickEl(f.el, spec);
    await J.idle(150, 1000); mo.disconnect();
    const moved = location.href !== before;
    return { ok: true, desc: describe(f.el), count: f.count, url: location.href, moved, title: document.title,
             noEffect: muts === 0 && !moved && !(f.el.tagName === "A" && f.el.href), x: pos.x, y: pos.y };
  };
  J.hover = async (spec) => {
    const f = await waitEl(spec);
    if (!f.el) return miss(spec, f);
    const { x, y } = center(f.el); const t = targetAt(f.el, x, y);
    if (spec.visual !== false) await flash(f.el, "hover");
    let muts = 0; const mo = new MutationObserver((recs) => { muts += realMutations(recs); });
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    for (const ty of ["pointerover", "mouseover", "pointerenter", "mouseenter", "pointermove", "mousemove"]) fire(t, ty, x, y, { held: false });
    await J.idle(200, 1000); mo.disconnect();
    return { ok: true, desc: describe(f.el), noEffect: muts === 0, x, y };
  };
  J.drag = async (spec, to) => {
    const a = await waitEl(spec), b = await waitEl(to);
    if (!a.el) return miss(spec, a);
    if (!b.el) return miss(to, b);
    const p = center(a.el), q = center(b.el), dt = new DataTransfer();
    const ev = (el, type, pt) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, composed: true, clientX: pt.x, clientY: pt.y, dataTransfer: dt }));
    fire(a.el, "pointerdown", p.x, p.y); fire(a.el, "mousedown", p.x, p.y);
    ev(a.el, "dragstart", p);
    for (let i = 1; i <= 6; i++) {
      const pt = { x: p.x + ((q.x - p.x) * i) / 6, y: p.y + ((q.y - p.y) * i) / 6 };
      const over = document.elementFromPoint(pt.x, pt.y) || b.el;
      fire(over, "mousemove", pt.x, pt.y); ev(over, "dragover", pt);
      await sleep(25);
    }
    ev(b.el, "dragenter", q); ev(b.el, "dragover", q); ev(b.el, "drop", q); ev(a.el, "dragend", q);
    fire(b.el, "mouseup", q.x, q.y); fire(b.el, "pointerup", q.x, q.y);
    await J.idle(200, 1200);
    return { ok: true, desc: `${describe(a.el)} onto ${describe(b.el)}` };
  };
  J.check = async (spec, on = true) => {
    const f = await waitEl(spec);
    if (!f.el) return miss(spec, f);
    let el = f.el;
    if (el.tagName === "LABEL" && el.control) el = el.control;
    const now = el.checked != null && /checkbox|radio/.test(el.type || "") ? el.checked : el.getAttribute("aria-checked") === "true";
    if (now !== on) { if (spec.visual !== false) await flash(el, on ? "tick" : "untick"); await clickEl(el); await J.idle(150, 800); }
    const after = el.checked != null && /checkbox|radio/.test(el.type || "") ? el.checked : el.getAttribute("aria-checked") === "true";
    return after === on ? { ok: true, desc: `${describe(el)} is now ${on ? "checked" : "unchecked"}` } : { ok: false, err: `couldn't set ${describe(el)}` };
  };

  // ------------------------------------------------------------ keyboard & typing
  const ALIAS = { esc: "Escape", escape: "Escape", enter: "Enter", return: "Enter", tab: "Tab", space: " ", spacebar: " ", backspace: "Backspace", delete: "Delete", del: "Delete",
    up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight", pageup: "PageUp", pagedown: "PageDown", home: "Home", end: "End" };
  const CODES = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, PageDown: 34, PageUp: 33, Home: 36, End: 35, " ": 32 };
  const SUBMIT = /^(send|search|go|submit|post|reply|ask|save|done|ok|log ?in|sign ?in|next|continue)\b/i;
  const setNative = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
  };
  const textOf = (el) => norm(el.value != null && el.tagName !== "DIV" ? el.value : el.innerText);
  J.press = (el, spec) => {
    el = el || document.activeElement || document.body;
    const parts = String(spec).split("+").map((x) => x.trim()); let key = parts.pop();
    key = ALIAS[key.toLowerCase()] || (key.length > 1 ? key[0].toUpperCase() + key.slice(1) : key);
    const mods = parts.map((x) => x.toLowerCase());
    const ctrl = mods.includes("ctrl") || mods.includes("control") || mods.includes("cmd") || mods.includes("meta");
    const kc = CODES[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
    const init = { key, code: key.length === 1 ? (/[a-z]/i.test(key) ? "Key" + key.toUpperCase() : /\d/.test(key) ? "Digit" + key : "") : key, keyCode: kc, which: kc,
      ctrlKey: ctrl, shiftKey: mods.includes("shift"), altKey: mods.includes("alt"), metaKey: mods.includes("meta") || mods.includes("cmd"), bubbles: true, cancelable: true, composed: true };
    const go = el.dispatchEvent(new KeyboardEvent("keydown", init));
    el.dispatchEvent(new KeyboardEvent("keypress", init));
    el.dispatchEvent(new KeyboardEvent("keyup", init));
    if (ctrl && go && key.length === 1) {                                         // editing shortcuts the browser would do itself
      const cmd = { a: "selectAll", c: "copy", x: "cut", z: "undo", y: "redo", b: "bold", i: "italic", u: "underline" }[key.toLowerCase()];
      if (cmd) { document.execCommand(cmd); return { handled: true, how: cmd }; }
    }
    if (!go) return { handled: true, how: "page handled it" };
    if (key.length === 1 && !ctrl && isEditable(el)) { document.execCommand("insertText", false, key); return { handled: true, how: "typed it" }; }
    if (key === "Backspace" && isEditable(el)) { document.execCommand("delete"); return { handled: true, how: "deleted" }; }
    if (key !== "Enter") return { handled: true };
    if (el.tagName === "TEXTAREA" || (el.isContentEditable && !el.closest("form"))) {   // Enter in a multi-line box is a newline
      if (el.tagName === "TEXTAREA") { setNative(el, el.value + "\n"); el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertLineBreak" })); }
      else document.execCommand("insertLineBreak");
      return { handled: true, how: "added a new line" };
    }
    const form = el.form || el.closest("form");
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return { handled: true, how: "submitted the form" }; }
    for (let p = el, i = 0; p && i < 5; p = p.parentElement, i++) {                      // a nearby Send/Search button
      const b = deep("button,[role=button],input[type=submit]", p).find((x) => vis(x) && SUBMIT.test(label(x)) && !x.disabled);
      if (b) { b.click(); return { handled: true, how: `clicked "${label(b)}"` }; }
    }
    return { handled: false };
  };
  J.key = (spec) => J.press(document.activeElement, spec);

  const typeInto = async (el, text, append) => {
    el.scrollIntoView({ block: "center", behavior: "instant" });
    el.focus({ preventScroll: true });
    if (el.tagName === "SELECT") {
      const o = Array.from(el.options).find((x) => low(x.text) === low(text)) || Array.from(el.options).find((x) => low(x.text).includes(low(text)) || low(x.value) === low(text));
      if (!o) return false;
      el.value = o.value; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      const prev = append ? el.value : "";
      setNative(el, prev + text);
      el.setSelectionRange && (() => { try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* number inputs */ } })();
      el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: text }));
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      const sel = getSelection(), r = document.createRange();
      r.selectNodeContents(el);
      if (append) r.collapse(false);
      sel.removeAllRanges(); sel.addRange(r);
      if (!append && norm(el.innerText)) document.execCommand("delete");
      String(text).split("\n").forEach((p, i) => { if (i) document.execCommand("insertLineBreak"); if (p) document.execCommand("insertText", false, p); });
    }
    await sleep(40);
    return el.tagName === "INPUT" && /number|date|time|color|range/.test(el.type) ? true : low(textOf(el)).includes(low(text).slice(0, 40));
  };
  const resolveField = (spec) => {
    if (spec.ref) return { el: byRef(spec.ref), how: "ref " + spec.ref };
    if (spec.field) return { el: J.findField(spec.field), how: `field "${spec.field}"` };
    const all = deep(EDITABLE).filter(vis);
    if (all.length === 1) return { el: all[0], how: "" };
    if (isEditable(document.activeElement)) return { el: document.activeElement, how: "the focused field" };
    return { el: null, how: "" };
  };
  J.type = async (spec, text, opts = {}) => {
    let r = resolveField(spec);
    for (let t = 0; !r.el && t < (spec.wait != null ? spec.wait : 2000) && (spec.field || spec.ref); t += 150) { await sleep(150); r = resolveField(spec); }   // field may still be rendering
    const el = r.el;
    if (!el) return { ok: false, notFound: true, err: `no text field found${r.how ? " for " + r.how : ""}. Say which one with field=… Fields here: ${J.fields()}` };
    if (el.type === "password" || /^cc-|one-time-code/.test(el.getAttribute("autocomplete") || ""))
      return { ok: false, err: `${describe(el)} is a password, card or one-time-code field: I never type those. Please enter it yourself.` };
    if (spec.visual !== false) await flash(el, "typing");
    const ok = await typeInto(el, text, opts.append);
    if (!ok) return { ok: false, retry: "trusted", err: `typing into ${describe(el)} didn't take` };
    let pressed = null;
    if (opts.submit) { await sleep(60); pressed = J.press(el, "Enter"); await J.idle(200, 1200); }
    return { ok: true, field: describe(el), value: textOf(el).slice(0, 60), submitted: pressed, url: location.href };
  };
  J.fill = async (pairs) => {                                                    // ["Name=Aniket", "Subscribe=yes", "Country=India"]
    const out = [];
    for (const p of pairs) {
      const i = p.indexOf("="); const field = norm(p.slice(0, i)), val = p.slice(i + 1).trim();
      const bool = /^(true|yes|on|checked|tick|ticked)$/i.test(val) ? true : /^(false|no|off|unchecked|untick)$/i.test(val) ? false : null;
      const box = bool !== null ? J.find({ text: field, kind: "check" }).el || J.find({ text: field, kind: "radio" }).el || J.find({ text: field, kind: "switch" }).el : null;
      if (box) { const r = await J.check({ ref: tag(box) }, bool); out.push(`${field}: ${r.ok ? (bool ? "checked" : "unchecked") : r.err}`); continue; }
      const r = await J.type({ field }, val, {});
      out.push(`${field}: ${r.ok ? "set" : r.err}`);
    }
    return { ok: !out.some((x) => /no text field|didn't/.test(x)), report: out.join("; ") };
  };

  // ------------------------------------------------------------ scrolling, waiting, media
  J.scroll = (dir, amt, spec) => {
    if (spec && (spec.text || spec.ref)) { const f = J.find(spec); if (!f.el) return "not found to scroll to"; f.el.scrollIntoView({ block: "center", behavior: "instant" }); return `scrolled to ${describe(f.el)}`; }
    let el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
    while (el && el !== document.body && el !== document.documentElement) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 20) break;
      el = el.parentElement;
    }
    const box = el && el !== document.body && el !== document.documentElement ? el : null;
    const h = (box ? box.clientHeight : innerHeight) * (amt || 0.8);
    const by = (top, left) => (box || window).scrollBy({ top, left, behavior: "instant" });
    const to = (top) => (box || window).scrollTo({ top, behavior: "instant" });
    if (dir === "top") to(0);
    else if (dir === "bottom") to((box || document.documentElement).scrollHeight);
    else by(dir === "up" ? -h : dir === "down" ? h : 0, dir === "left" ? -h : dir === "right" ? h : 0);
    return box ? `scrolled ${dir} inside ${low(label(box)) || box.tagName.toLowerCase()}` : `scrolled ${dir}`;
  };
  J.waitFor = async (text, gone, ms = 8000) => {
    const q = low(text), has = () => low(document.body.innerText).includes(q) || !!J.find({ text }).el;
    for (let t = 0; t < ms; t += 150) { if (gone ? !has() : has()) return { ok: true }; await sleep(150); }
    return { ok: false, err: `"${text}" ${gone ? "is still" : "never"} on the page after ${Math.round(ms / 1000)}s` };
  };
  const mmss = (s) => !isFinite(s) || !s ? "live" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  J.media = async (action, value) => {
    const vids = deep("video,audio").filter((v) => v.readyState > 0 || !v.paused);
    const v = vids.sort((a, b) => (b.videoWidth * b.videoHeight) - (a.videoWidth * a.videoHeight))[0];
    if (!v) return { ok: false, err: "no video or audio on this page" };
    const st = () => `${v.paused ? "paused" : "playing"} at ${mmss(v.currentTime)} of ${mmss(v.duration || 0)}, speed ${v.playbackRate}x, ${v.muted ? "muted" : "volume " + Math.round(v.volume * 100) + "%"}`;
    const player = v.closest(".html5-video-player,[class*=player]") || v;
    switch (action) {
      case "play": try { await v.play(); } catch (e) { (player.querySelector(".ytp-play-button") || v).click(); } break;
      case "pause": v.pause(); break;
      case "toggle": if (v.paused) { try { await v.play(); } catch (e) { (player.querySelector(".ytp-play-button") || v).click(); } } else v.pause(); break;
      case "mute": v.muted = true; break;
      case "unmute": v.muted = false; break;
      case "seek": {
        const s = String(value || "0").trim();
        if (/^[+-]/.test(s)) v.currentTime = Math.max(0, v.currentTime + parseFloat(s));
        else if (s.includes(":")) v.currentTime = s.split(":").reduce((a, x) => a * 60 + parseFloat(x), 0);
        else v.currentTime = parseFloat(s);
        break;
      }
      case "speed": v.playbackRate = Math.min(4, Math.max(0.25, parseFloat(value) || 1)); break;
      case "volume": v.muted = false; v.volume = Math.min(1, Math.max(0, parseFloat(value) / (parseFloat(value) > 1 ? 100 : 1))); break;
      case "fullscreen": try { document.fullscreenElement ? await document.exitFullscreen() : await player.requestFullscreen(); } catch (e) { return { ok: false, err: "the page needs a real click to go fullscreen" }; } break;
      case "next": { const n = document.querySelector(".ytp-next-button,[aria-label*=Next i]"); if (n) n.click(); else return { ok: false, err: "no Next button" }; break; }
      default: break;
    }
    await sleep(120);
    return { ok: true, state: st() };
  };

  // ------------------------------------------------------------ WhatsApp Web
  const wa = {
    search: () => deep('#side [contenteditable="true"][role="textbox"], #side [contenteditable="true"][data-tab="3"], #side input[type=text], #side [contenteditable="true"]')[0],
    box: () => deep('footer [contenteditable="true"][role="textbox"], #main footer [contenteditable="true"], footer div[contenteditable="true"], [contenteditable="true"][data-tab="10"]')[0],
    rows: () => deep('#pane-side [role="listitem"], #pane-side [role="row"], #pane-side [data-testid="cell-frame-container"]').filter(vis),
    title: () => { const t = document.querySelector('#main header span[title], #main header [data-testid="conversation-info-header-chat-title"]'); return t ? (t.getAttribute("title") || norm(t.textContent)) : ""; },
    rowTitle: (r) => { const t = r.querySelector("span[title]"); return norm(t ? t.getAttribute("title") : r.innerText.split("\n")[0]); },
  };
  J.waState = () => ({ onWA: location.host.includes("whatsapp.com"), ready: !!wa.search(), chat: wa.title() });
  J.waChat = async (name) => {
    const sb = wa.search();
    if (!sb) return { ok: false, err: "WhatsApp Web isn't ready (chat list not found): is it loaded and logged in?" };
    const q = low(name);
    if (low(wa.title()) === q) return { ok: true, chat: wa.title(), note: "already open" };
    await flash(sb, "finding " + name);
    if (!(await typeInto(sb, name, false))) return { ok: false, err: "couldn't type into the WhatsApp search box" };
    let best = null, list = [], prev = "";
    for (let i = 0; i < 30; i++) {                                             // up to ~3 s for results
      await sleep(i ? 100 : 60);
      list = wa.rows().map((r) => ({ r, t: wa.rowTitle(r) }));
      const scored = list.map((x) => ({ ...x, s: low(x.t) === q ? 0 : low(x.t).startsWith(q) ? 1 : low(x.t).includes(q) ? 2 : q.split(" ").every((w) => low(x.t).includes(w)) ? 3 : 9 }))
        .filter((x) => x.s < 9).sort((a, b) => a.s - b.s);
      if (scored[0] && (scored[0].s === 0 || (scored[0].t === prev && i >= 2))) { best = scored[0]; break; }   // exact, or stable twice
      prev = scored[0] ? scored[0].t : "";
    }
    if (!best) return { ok: false, err: `no WhatsApp chat matching "${name}". Chats shown: ${list.slice(0, 6).map((x) => x.t).join(", ") || "none"}` };
    await clickEl(best.r);
    await J.idle(250, 1500);
    for (let i = 0; i < 10 && !low(wa.title()).includes(low(best.t).slice(0, 12)); i++) await sleep(100);
    const now = wa.title();
    if (now) {                                                   // leave WhatsApp tidy: full chat list back, cursor in the message box
      await typeInto(sb, "", false); J.press(sb, "Escape");
      const box = wa.box(); if (box) box.focus({ preventScroll: true });
    }
    return now ? { ok: true, chat: now } : { ok: false, err: `clicked "${best.t}" but the chat didn't open` };
  };
  J.waSend = async (text) => {
    const box = wa.box();
    if (!box) return { ok: false, err: "no WhatsApp message box: open a chat first (wa_chat)" };
    const chat = wa.title();
    if (!(await typeInto(box, text, false))) return { ok: false, err: "couldn't type into the message box" };
    await sleep(80);
    const btn = document.querySelector('footer button[aria-label="Send"], footer [data-icon="send"], [data-testid="send"], footer span[data-icon="wds-ic-send-filled"]');
    if (btn) await clickEl(btn.closest("button,[role=button]") || btn); else J.press(box, "Enter");
    await J.idle(250, 1500);
    const left = norm(box.innerText);
    if (left && left.includes(norm(text).slice(0, 20))) { J.press(box, "Enter"); await J.idle(250, 1000); }
    return norm(box.innerText).includes(norm(text).slice(0, 20)) ? { ok: false, err: "the message is still in the box; it may not have sent" } : { ok: true, chat };
  };
  J.has = (spec) => { const f = J.find(spec); return f.el ? describe(f.el) : null; };
  J.history = (n) => { setTimeout(() => history.go(n), 0); return true; };    // not skipped like chrome.tabs.goBack is
  J.setFrame = (id) => { J.fid = id; return true; };
})();
