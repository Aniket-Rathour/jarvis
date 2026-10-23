chrome.runtime.sendMessage("status", (r) => {
  const s = document.getElementById("s"), on = r && r.connected;
  s.textContent = on ? "Connected to Jarvis" : "Jarvis is not running";
  s.className = on ? "on" : "off";
});
chrome.storage.local.get("log").then(({ log = [] }) => {
  if (!log.length) return;
  document.getElementById("log").innerHTML = log.map((e) =>
    `<li class="${e.ok ? "" : "bad"}">${e.ok ? "✓" : "✗"} ${e.what.replace(/</g, "&lt;")}<small>${new Date(e.t).toLocaleTimeString()} · ${e.note.replace(/</g, "&lt;")}</small></li>`).join("");
});
