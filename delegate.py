"""Launch Claude Code in a visible terminal (plain CLI or Claude in Chrome) and track the run."""
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import config

NOTE = ("You were started by Jarvis, {user}'s voice assistant. Nobody is watching this terminal, so never wait for "
        "keyboard input. For root commands use `pkexec <cmd>` (GNOME shows a password prompt) instead of sudo. "
        "Do the task completely, verify it worked, then end with a final message of at most 3 short sentences "
        "saying what you did and the outcome; it is read aloud to {user}. {user} is listening along: before each "
        "major step of a long task, write ONE short first-person sentence (under 15 words) saying what you are "
        "about to do or what you just found, e.g. 'Now I'm comparing the three sources.' Skip it for trivial steps.")
_counter = 0


def claude_bin() -> str:
    # `claude` may be a shell alias / fnm shim that a non-login process can't see.
    # Order: CLAUDE_BIN, PATH (unless it's fnm's temporary per-shell dir), newest fnm install.
    p = config.CLAUDE_BIN or shutil.which("claude")
    if p and "fnm_multishells" in p:
        p = None
    if not p:
        cands = sorted(Path.home().glob(".local/share/fnm/node-versions/*/installation/bin/claude"),
                       key=lambda c: c.stat().st_mtime, reverse=True)
        p = str(cands[0]) if cands else None
    if not p:
        raise RuntimeError("claude CLI not found; set CLAUDE_BIN in .env")
    return p


def _claude_cmd() -> str:
    """Shell prefix that runs claude. Uses the user's claude-trusted wrapper (pre-accepts the
    folder trust prompt) when present, with claude's node dir on PATH so the wrapper's exec works."""
    claude = claude_bin()
    trusted = shutil.which("claude-trusted") or str(Path.home() / ".local/bin/claude-trusted")
    if os.path.exists(trusted):
        return f'PATH={shlex.quote(str(Path(claude).parent))}:"$PATH" {shlex.quote(trusted)}'
    return shlex.quote(claude)


def _terminal(cmd: str):
    for argv in (["gnome-terminal", "--"], ["ptyxis", "--new-window", "--"], ["kgx", "--"]):
        if shutil.which(argv[0]):
            subprocess.Popen(argv + ["bash", "-c", cmd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
    raise RuntimeError("no supported terminal (gnome-terminal / ptyxis / kgx)")


def resolve_workdir(workdir: str) -> Path:
    wd = Path(os.path.expanduser(workdir or config.DEFAULT_WORKDIR))
    if not wd.is_absolute():            # "Documents" -> ~/Documents
        wd = Path.home() / wd
    return wd if wd.is_dir() else Path.home()


STEPS_NOTE = ("\n\nThis work is part of a plan the user sees as a checklist:\n{steps}\nEach time you finish one of these "
              "steps, print a line with exactly [[STEP n DONE]] (or [[STEP n FAILED]]) using the numbers above. "
              "Only mark steps you actually did.")


def _spawn_headless(cmd: str):
    """Run without any terminal window (routines: the Jarvis window shows the progress)."""
    subprocess.Popen(["bash", "-c", cmd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env={**os.environ, "JARVIS_HEADLESS": "1"})


def start(task: str, browser: bool = False, interactive: bool = False, workdir: str = "",
          steps: list[tuple[int, str]] | None = None, model: str = "", effort: str = "",
          session_id: str = "", resume: str = "", system_note: str | None = None, headless: bool = False, extra_flags: str = "") -> dict:
    """Open a terminal running Claude on `task`. browser=True adds --chrome (Claude in Chrome).
    Non-interactive runs stream progress into tasks/<id>/ and close the terminal when finished.
    session_id starts a conversation with a known id; resume continues one (the task is then the next message);
    system_note replaces the default instructions; headless runs with no terminal window."""
    global _counter
    _counter += 1
    tid = f"t{int(time.time())}{_counter}"
    d = config.TASKS_DIR / tid
    d.mkdir(parents=True)
    if steps and not interactive:
        task += STEPS_NOTE.format(steps="\n".join(f"{n}. {t}" for n, t in steps))
    (d / "prompt.txt").write_text(task)
    (d / "system.txt").write_text(system_note if system_note is not None else NOTE.format(user=config.USER_NAME))
    q = lambda p: shlex.quote(str(p))                       # noqa: E731
    claude = _claude_cmd()
    chrome = "--chrome" if browser else ""
    perm = f"--permission-mode {shlex.quote(config.CLAUDE_PERMISSION_MODE)}"
    if model:
        perm += f" --model {shlex.quote(model)}"
    if effort:
        perm += f" --effort {shlex.quote(effort)}"
    if resume:
        perm += f" --resume {shlex.quote(resume)}"
    elif session_id:
        perm += f" --session-id {shlex.quote(session_id)}"
    if extra_flags:
        perm += f" {extra_flags}"
    sysp = f'--append-system-prompt "$(cat {q(d / "system.txt")})"'
    if interactive:
        body = f'{claude} {chrome} {perm} "$(cat {q(d / "prompt.txt")})"'
    else:
        viewer = f"{q(sys.executable)} {q(config.ROOT / 'claude_view.py')} {q(d)}"
        body = (f'{claude} {chrome} -p "$(cat {q(d / "prompt.txt")})" --output-format stream-json --verbose '
                f'{perm} {sysp} < /dev/null 2> {q(d / "stderr.txt")} | {viewer}')
    title = "Jarvis · Claude in Chrome" if browser else "Jarvis · Claude"
    (_spawn_headless if headless else _terminal)(
        f'echo $$ > {q(d / "pid")}; printf "\\033]0;{title}\\007"; cd {q(resolve_workdir(workdir))} && {body}')
    return {"task_id": tid, "dir": str(d), "interactive": interactive}


OC_NOTE = ("You are OpenCode, started by Jarvis, {user}'s voice assistant. Nobody is at the keyboard, so never wait for "
           "input or ask questions: make sensible choices. {user} is listening along: before each major step print ONE "
           "short first-person sentence (under 15 words) about what you are doing, e.g. 'Now I'm editing app.py.' "
           "Verify your work (run it / the tests). End with at most 3 short sentences: what you did and the outcome.\n\n"
           "TASK:\n")


def opencode_bin() -> str:
    p = config.OPENCODE_BIN or shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    if not os.path.exists(p):
        raise RuntimeError("opencode not found; set OPENCODE_BIN in .env")
    return p


def start_opencode(task: str, workdir: str = "", interactive: bool = False, model: str = "",
                   steps: list[tuple[int, str]] | None = None, headless: bool = False) -> dict:
    """Run OpenCode (on the local Qwen model) on `task` in a terminal. Same task-dir protocol as start():
    pid / progress / result.txt / exitcode, so Jobs can watch it. interactive=True opens the OpenCode TUI instead."""
    global _counter
    _counter += 1
    tid = f"t{int(time.time())}{_counter}"
    d = config.TASKS_DIR / tid
    d.mkdir(parents=True)
    if steps and not interactive:
        task += STEPS_NOTE.format(steps="\n".join(f"{n}. {t}" for n, t in steps))
    full = task if interactive else OC_NOTE.format(user=config.USER_NAME) + task
    (d / "prompt.txt").write_text(full)
    q = lambda p: shlex.quote(str(p))                       # noqa: E731
    oc, model = q(opencode_bin()), q(model or config.OPENCODE_MODEL)
    if interactive:
        body = f'{oc} --auto --prompt "$(cat {q(d / "prompt.txt")})"'
    else:
        viewer = f"{q(sys.executable)} {q(config.ROOT / 'oc_view.py')} {q(d)}"
        body = (f'{oc} run --auto -m {model} "$(cat {q(d / "prompt.txt")})" < /dev/null 2>&1 | {viewer}; '
                f'rc=${{PIPESTATUS[0]}}; echo $rc > {q(d / "exitcode")}; [ $rc = 0 ] && sleep 5')
    (_spawn_headless if headless else _terminal)(
        f'echo $$ > {q(d / "pid")}; printf "\\033]0;Jarvis · OpenCode (Qwen)\\007"; '
        f'cd {q(resolve_workdir(workdir))} && {body}')
    return {"task_id": tid, "dir": str(d), "interactive": interactive}


def _pid(d: Path):
    try:
        return int((d / "pid").read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def step_marks(tid: str) -> dict[int, str]:
    """Plan steps Claude reported, e.g. {1: "done", 2: "failed"}."""
    try:
        return {int(k): v for k, v in json.loads((config.TASKS_DIR / tid / "steps.json").read_text()).items()}
    except (OSError, ValueError):
        return {}


def poll(tid: str, started: float) -> tuple[str, str, str]:
    """(status, result_text, progress_line). status: running | done | failed | cancelled"""
    d = config.TASKS_DIR / tid
    progress = (d / "progress").read_text().strip() if (d / "progress").exists() else ""
    if (d / "exitcode").exists():
        text = (d / "result.txt").read_text()[-20000:] if (d / "result.txt").exists() else ""
        if not text and (d / "stderr.txt").exists():
            text = (d / "stderr.txt").read_text()[-1500:]
        return ("done" if (d / "exitcode").read_text().strip() == "0" else "failed"), text, progress
    pid = _pid(d)
    if pid is None:
        if time.time() - started > 60:
            return "failed", "the terminal never started", progress
        return "running", "", progress
    if not _alive(pid):
        return "cancelled", "the Claude terminal was closed before the task finished", progress
    if time.time() - started > config.CLAUDE_TIMEOUT_MIN * 60:
        cancel(tid)
        return "failed", f"timed out after {config.CLAUDE_TIMEOUT_MIN} minutes", progress
    return "running", "", progress


def cancel(tid: str):
    pid = _pid(config.TASKS_DIR / tid)
    if pid and _alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
