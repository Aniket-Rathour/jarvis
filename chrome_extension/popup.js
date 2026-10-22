chrome.runtime.sendMessage("status", (r) => {
  const s = document.getElementById("s"), on = r && r.connected;
  s.textContent = on ? "Connected to Jarvis" : "Jarvis is not running";
  s.className = on ? "on" : "off";
});
