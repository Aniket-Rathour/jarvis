#!/usr/bin/env bash
# Runs every Jarvis test. Safe while Jarvis is running: a temp memory file, and an isolated Chrome on port 8799.
cd "$(dirname "$0")/.." || exit 1
fail=0
for t in tests/test_memory.py tests/test_memory_learn.py tests/test_fastpath.py tests/test_tools_guard.py tests/test_call_routing.py tests/test_call_connect.py tests/test_turn_taking.py tests/test_main_turns.py tests/test_bridge_stale.py tests/extension/run.py tests/test_call_flow.py tests/test_call_attach.py tests/test_call_conversation.py; do
  echo "== $t"; .venv/bin/python "$t" || fail=1
done
.venv/bin/python tests/test_call_attach.py start || fail=1
[ $fail = 0 ] && echo "ALL PASSED" || { echo "SOME TESTS FAILED"; exit 1; }
