"""Local desktop actions Jarvis does itself: commands, files, apps, URLs, bookmarks, windows, installs."""
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Never run these, even with approval.
CATASTROPHIC = re.compile(r"rm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$HOME)/?(\s|$)|mkfs|dd\s+.*of=/dev/(sd|nvme|vd)|:\(\)\s*\{|"
                          r">\s*/dev/(sd|nvme)", re.I)
# These change the system and need the user's OK first (Jarvis enforces it, see tools.py).
RISKY = re.compile(r"\b(sudo|pkexec|su|dnf|yum|rpm|flatpak\s+(install|uninstall|remove)|pip3?\s+install|npm\s+(i|install)\s+-g|"
                   r"rm|rmdir|shred|mv|chmod|chown|kill|pkill|killall|systemctl|reboot|shutdown|poweroff|"
                   r"gsettings\s+set|dconf\s+(write|reset)|git\s+(push|reset|clean))\b", re.I)


def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command with the user's full permissions and return its output."""
    if CATASTROPHIC.search(command):
        return "refused: this command could destroy the system"
    timeout = max(1, min(int(timeout or 30), 300))
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s (use delegate_to_claude for long jobs)"
    out = (r.stdout + r.stderr).strip()
    return f"exit {r.returncode}\n{out[-2500:]}"


def _detach(argv):
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_target(target: str) -> str:
    """Open a file, folder or URL with the default app."""
    if "://" not in target:
        target = os.path.expanduser(target)
        if not os.path.exists(target):
            hits = _search(os.path.basename(target.rstrip("/")), limit=5)
            return f"not found: {target}. Similar: {hits}" if hits else f"not found: {target}"
    _detach(["xdg-open", target])
    return f"opened {target}"


# ---- files ----------------------------------------------------------------
def _p(path: str) -> Path:
    p = Path(os.path.expanduser(path.strip() or "~"))
    return p if p.is_absolute() else Path.home() / p


def read_file(path: str, max_chars: int = 8000) -> str:
    p = _p(path)
    if not p.is_file():
        return f"not found: {p}"
    data = p.read_bytes()[:max_chars * 2]
    if b"\0" in data[:2000]:
        return f"error: {p} is a binary file ({p.stat().st_size} bytes)"
    text = data.decode(errors="replace")
    return text[:max_chars] + (f"\n…(truncated, {p.stat().st_size} bytes total)" if len(text) > max_chars else "")


def write_file(path: str, content: str, append: bool = False) -> str:
    """Create or update a text file. An overwritten file is kept as <name>.bak."""
    p = _p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    if p.exists() and not append:
        shutil.copy2(p, p.with_name(p.name + ".bak"))
        note = f" (previous version saved as {p.name}.bak)"
    with open(p, "a" if append else "w") as f:
        f.write(content)
    return f"{'appended to' if append else 'wrote'} {p} ({len(content)} chars){note}"


def list_dir(path: str = "~") -> str:
    p = _p(path)
    if not p.is_dir():
        return f"not found: {p}"
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    rows = [f"{x.name}/" if x.is_dir() else x.name for x in items if not x.name.startswith(".")][:80]
    return f"{p}:\n" + ("\n".join(rows) or "(empty)")


# ---- sound, media, clipboard, notifications --------------------------------
def volume(action: str, percent: int = 0) -> str:
    sink = "@DEFAULT_AUDIO_SINK@"
    cmds = {"up": ["wpctl", "set-volume", "-l", "1.0", sink, f"{percent or 10}%+"],
            "down": ["wpctl", "set-volume", sink, f"{percent or 10}%-"],
            "set": ["wpctl", "set-volume", "-l", "1.0", sink, f"{max(0, min(100, percent))}%"],
            "mute": ["wpctl", "set-mute", sink, "1"], "unmute": ["wpctl", "set-mute", sink, "0"]}
    if action not in cmds and action != "get":
        return "error: action must be up, down, set, mute, unmute or get"
    if action in cmds:
        subprocess.run(cmds[action], capture_output=True, timeout=5)
    out = subprocess.run(["wpctl", "get-volume", sink], capture_output=True, text=True, timeout=5).stdout.strip()
    m = re.search(r"([\d.]+)", out)
    return f"volume {round(float(m.group(1)) * 100)}%{' (muted)' if 'MUTED' in out else ''}" if m else out


def _mpris_players() -> list[str]:
    out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                          "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ListNames"],
                         capture_output=True, text=True, timeout=5).stdout
    return re.findall(r"org\.mpris\.MediaPlayer2\.[\w.\-]+", out)


def media(action: str) -> str:
    """play | pause | toggle | next | previous for whatever is playing (Chrome/YouTube, Spotify, …)."""
    method = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next", "previous": "Previous"}.get(action)
    if not method:
        return "error: action must be play, pause, toggle, next or previous"
    players = _mpris_players()
    if not players:
        return "no media player is running"
    def status(p):
        r = subprocess.run(["gdbus", "call", "--session", "--dest", p, "--object-path", "/org/mpris/MediaPlayer2",
                            "--method", "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player",
                            "PlaybackStatus"], capture_output=True, text=True, timeout=5).stdout
        return "Playing" in r
    target = next((p for p in players if status(p)), players[0])
    r = subprocess.run(["gdbus", "call", "--session", "--dest", target, "--object-path", "/org/mpris/MediaPlayer2",
                        "--method", f"org.mpris.MediaPlayer2.Player.{method}"], capture_output=True, text=True, timeout=5)
    app = target.split(".")[3]
    return f"{action} sent to {app}" if r.returncode == 0 else f"error: {r.stderr.strip()[:150]}"


def clipboard(action: str, text: str = "") -> str:
    if action == "get":
        r = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=5)
        return r.stdout[:4000] if r.returncode == 0 else "clipboard is empty"
    if action == "set":
        subprocess.run(["wl-copy"], input=text, text=True, timeout=5)
        return f"copied {len(text)} chars to the clipboard"
    return "error: action must be get or set"


def notify(title: str, body: str = "") -> str:
    subprocess.run(["notify-send", "-a", "Jarvis", "-i", "audio-input-microphone", title, body], timeout=5)
    return "notification shown"
_SKIP = {"node_modules", "__pycache__", "site-packages", "venv", "snap", "go", "target", "build", "dist"}


def search_files(query: str, root: str = "~", limit: int = 10) -> str:
    """Find files/folders whose name contains every word of the query (newest first)."""
    return _search(query, root, limit) or f"no files match '{query}'"


def _search(query: str, root: str = "~", limit: int = 10) -> str:
    toks = query.lower().replace("_", " ").replace("-", " ").split()
    if not toks:
        return ""
    deadline = time.monotonic() + 6
    hits = []
    for dp, dns, fns in os.walk(os.path.expanduser(root)):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in _SKIP]
        for n in dns + fns:
            low = n.lower().replace("_", " ").replace("-", " ")
            if all(t in low for t in toks):
                p = os.path.join(dp, n)
                try:
                    hits.append((os.path.getmtime(p), p))
                except OSError:
                    pass
        if time.monotonic() > deadline:
            break
    hits.sort(reverse=True)
    return "\n".join(p for _, p in hits[:limit])


# ---- chrome bookmarks -----------------------------------------------------
CHROME_DIR = Path.home() / ".config/google-chrome"


def _bookmarks() -> list[dict]:
    """All bookmarks from every Chrome profile (local + account-synced files)."""
    out, seen = [], set()
    for prof in sorted(CHROME_DIR.glob("*")):
        if prof.name != "Default" and not prof.name.startswith("Profile "):
            continue
        for fn in ("Bookmarks", "AccountBookmarks"):
            try:
                roots = json.loads((prof / fn).read_text())["roots"]
            except (OSError, ValueError, KeyError):
                continue

            def walk(node, folder):
                if node.get("type") == "url":
                    if node["url"] not in seen:
                        seen.add(node["url"])
                        out.append({"name": node.get("name", ""), "url": node["url"],
                                    "folder": folder, "profile": prof.name})
                for c in node.get("children", []):
                    walk(c, folder + "/" + node.get("name", ""))
            for r in roots.values():
                if isinstance(r, dict):
                    walk(r, "")
    return out


def _score(bm: dict, query: str) -> float:
    q = query.lower().strip()
    name, hay = bm["name"].lower(), f'{bm["name"]} {bm["url"]} {bm["folder"]}'.lower()
    toks = q.split()
    if toks and all(t in hay for t in toks):
        return 2 + 3 * (name == q) + 1.5 * name.startswith(q) + 1 * (q in name) + 0.5 * all(t in name for t in toks)
    r = difflib.SequenceMatcher(None, q, name).ratio()
    return r if r > 0.6 else 0


def find_bookmarks(query: str, limit: int = 8) -> str:
    ranked = sorted(((_score(b, query), b) for b in _bookmarks()), key=lambda x: -x[0])
    ranked = [(s, b) for s, b in ranked if s > 0][:limit]
    if not ranked:
        names = ", ".join(b["name"][:25] for b in _bookmarks()[:15])
        return f"no bookmark matches '{query}'. Some bookmarks: {names}"
    return "\n".join(f'{b["name"]} -> {b["url"]}  [{b["folder"] or "/"}]' for _, b in ranked)


def open_bookmark(query: str) -> str:
    """Open the best matching bookmark in Chrome; if the query is a bookmark folder, open its bookmarks."""
    bms = _bookmarks()
    q = query.lower().strip()
    folder = [b for b in bms if b["folder"].lower().rstrip("/").endswith("/" + q)]
    if folder and not any(b["name"].lower() == q for b in bms):
        for b in folder[:10]:
            _chrome_open(b["url"], b["profile"])
        return f"opened {min(len(folder), 10)} bookmarks from folder '{query}'"
    ranked = sorted(((_score(b, query), b) for b in bms), key=lambda x: -x[0])
    if not ranked or ranked[0][0] <= 0:
        return f"no bookmark matches '{query}'. Try find_bookmarks or ask the user."
    best = ranked[0][1]
    _chrome_open(best["url"], best["profile"])
    others = [b["name"] for s, b in ranked[1:4] if s >= ranked[0][0] - 0.5 and b["url"] != best["url"]]
    extra = f" (other close matches: {', '.join(others)})" if others else ""
    return f'opened bookmark "{best["name"]}" -> {best["url"]}{extra}'


def _chrome_open(url: str, profile: str = "Default", new: bool = False):
    try:                                         # through the Jarvis Hands extension: reuses a tab that is already open
        from browser_bridge import open_in_chrome
        if open_in_chrome(url, new) is not None:
            return
    except Exception:
        pass
    _detach(["google-chrome", f"--profile-directory={profile}", url])


def _find_desktop_app(name: str):
    """Match an installed .desktop launcher by display name, return its id."""
    q = name.lower()
    best = None
    for d in (Path("/usr/share/applications"), Path.home() / ".local/share/applications",
              Path("/var/lib/flatpak/exports/share/applications"),
              Path.home() / ".local/share/flatpak/exports/share/applications"):
        for f in d.glob("*.desktop"):
            try:
                txt = f.read_text(errors="ignore")
            except OSError:
                continue
            m = re.search(r"^Name=(.+)$", txt, re.M)
            if m and (q == m.group(1).lower() or q in m.group(1).lower()) and "NoDisplay=true" not in txt:
                if best is None or q == m.group(1).lower():
                    best = f.stem
    return best


APP_ALIASES = {
    "terminal": ["ptyxis", "gnome-terminal", "kgx"], "console": ["ptyxis", "gnome-terminal"],
    "files": ["nautilus"], "file manager": ["nautilus"], "explorer": ["nautilus"],
    "browser": ["google-chrome"], "chrome": ["google-chrome"], "google chrome": ["google-chrome"],
    "settings": ["gnome-control-center"], "text editor": ["gnome-text-editor"], "editor": ["gnome-text-editor"],
    "vs code": ["code"], "vscode": ["code"], "visual studio code": ["code"], "calculator": ["gnome-calculator"],
    "system monitor": ["gnome-system-monitor"], "task manager": ["gnome-system-monitor"],
}


def open_app(name: str) -> str:
    """Launch an application by command name (code, nautilus) or display name (Postman, Spotify)."""
    for cmd in APP_ALIASES.get(name.lower().strip(), []):
        if shutil.which(cmd):
            _detach([cmd])
            return f"launched {name} ({cmd})"
    argv = shlex.split(name)
    if shutil.which(argv[0]):
        _detach(argv)
        return f"launched {name}"
    app_id = _find_desktop_app(name)
    if app_id and shutil.which("gtk-launch"):
        _detach(["gtk-launch", app_id])
        return f"launched {name} ({app_id})"
    return f"no app found called '{name}'"


def open_url(url: str, new_window: bool = False) -> str:
    """Open a website in the user's Chrome (same profile as their bookmarks)."""
    url = url.strip()
    if not url:
        _detach(["google-chrome"])
        return "chrome opened"
    if "://" not in url:
        url = "https://" + url
    if not new_window:
        _chrome_open(url, "Default")
    else:
        _detach(["google-chrome", "--profile-directory=Default", "--new-window", url])
    return f"opened {url}"


def web_search(query: str) -> str:
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    _chrome_open(url, "Default", new=True)          # a search always gets its own tab
    return f"opened {url}"


# ---- installs (run as background jobs by jobs.py) -------------------------
def install_argv(packages: list[str], source: str) -> list[str]:
    pk = [p for p in packages if re.fullmatch(r"[A-Za-z0-9._+:@/-]+", p)]
    if not pk or len(pk) != len(packages):
        raise ValueError(f"invalid package names: {packages}")
    if source == "dnf":
        return ["pkexec", "dnf", "install", "-y", *pk]
    if source == "flatpak":
        return ["flatpak", "install", "-y", "--noninteractive", "--user", "flathub", *pk]
    raise ValueError("source must be dnf or flatpak (use delegate_to_claude for anything else)")


def resolve_extension(name_or_uuid: str) -> tuple[str, str]:
    """('uuid', 'Display Name') from extensions.gnome.org; accepts a uuid or a search phrase."""
    q = name_or_uuid.strip()
    if "@" in q:
        return q, q
    url = "https://extensions.gnome.org/extension-query/?" + urllib.parse.urlencode(
        {"search": q, "shell_version": _shell_version(), "n_per_page": 5})
    with urllib.request.urlopen(url, timeout=10) as r:
        exts = json.load(r).get("extensions", [])
    if not exts:
        raise LookupError(f"no GNOME extension found for '{q}'")
    best = next((e for e in exts if e["name"].lower() == q.lower()), exts[0])
    return best["uuid"], best["name"]


def _shell_version() -> str:
    try:
        out = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True, timeout=5).stdout
        return re.search(r"(\d+)", out).group(1)
    except (OSError, AttributeError, subprocess.SubprocessError):
        return ""


def extension_argv(uuid: str) -> list[str]:
    """GNOME shows its own 'Install extension?' dialog, then loads it live (no logout needed)."""
    return ["gdbus", "call", "--session", "--timeout", "600", "--dest", "org.gnome.Shell.Extensions",
            "--object-path", "/org/gnome/Shell/Extensions",
            "--method", "org.gnome.Shell.Extensions.InstallRemoteExtension", uuid]


# ---- window control ------------------------------------------------------
# GNOME on Wayland blocks scripted window control. Order of attempts:
#   1. "Window Calls" GNOME extension (D-Bus)  -> works on Wayland
#   2. wmctrl / xdotool                        -> works for X11 / XWayland windows
WC = ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
      "--object-path", "/org/gnome/Shell/Extensions/Windows",
      "--method"]


def _wc(method, *args):
    r = subprocess.run(WC + [f"org.gnome.Shell.Extensions.Windows.{method}", *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def _wc_find(query: str):
    raw = _wc("List")
    # gdbus prints ('<json>',)
    start, end = raw.index("'") + 1, raw.rindex("'")
    wins = json.loads(raw[start:end].encode().decode("unicode_escape"))
    q = query.lower()
    for w in wins:
        if q in (w.get("wm_class", "") + " " + w.get("title", "")).lower():
            return w["id"]
    return None


def window(action: str, app: str = "", width: int = 0, height: int = 0, x: int = 0, y: int = 0) -> str:
    """action: maximize|unmaximize|minimize|close|focus|resize|move|list"""
    try:
        if action == "list":
            return _wc("List")[:3000]
        wid = _wc_find(app)
        if wid is None:
            return f"no window matching '{app}'"
        if action == "resize":
            _wc("Resize", wid, width, height)
        elif action == "move":
            _wc("Move", wid, x, y)
        elif action == "focus":
            _wc("Activate", wid)
        else:
            _wc({"maximize": "Maximize", "unmaximize": "Unmaximize",
                 "minimize": "Minimize", "close": "Close"}[action], wid)
        return f"{action} done on '{app}'"
    except Exception as e:  # extension missing -> fall back
        err = str(e)
    if shutil.which("wmctrl") and app:
        flags = {"maximize": "add,maximized_vert,maximized_horz",
                 "unmaximize": "remove,maximized_vert,maximized_horz",
                 "minimize": "add,hidden"}
        if action in flags:
            r = subprocess.run(["wmctrl", "-r", app, "-b", flags[action]])
        elif action == "resize":
            r = subprocess.run(["wmctrl", "-r", app, "-e", f"0,-1,-1,{width},{height}"])
        elif action == "close":
            r = subprocess.run(["wmctrl", "-c", app])
        elif action == "focus":
            r = subprocess.run(["wmctrl", "-a", app])
        else:
            return "unsupported fallback action"
        return "done via wmctrl" if r.returncode == 0 else "wmctrl found no matching X11 window"
    return ("window control unavailable: GNOME Wayland needs the 'Window Calls' extension "
            "(uuid window-calls@domandoman.xyz). Offer to install it with install_gnome_extension. "
            f"detail: {err[:120]}")
