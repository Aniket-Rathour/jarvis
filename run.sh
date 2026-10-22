#!/usr/bin/env bash
# Starts Jarvis. Logs: ~/.local/state/jarvis/jarvis.log (crash output from the app launcher: stderr.log there)
cd "$(dirname "$0")" || exit 1
if [ -t 1 ]; then exec .venv/bin/python agent.py "$@"; fi
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/jarvis"
exec .venv/bin/python agent.py "$@" 2>> "${XDG_STATE_HOME:-$HOME/.local/state}/jarvis/stderr.log" >/dev/null
