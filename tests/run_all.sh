#!/usr/bin/env bash
# Runs every Jarvis test. Safe while Jarvis is running: a temp memory file, and an isolated Chrome on port 8799.
cd "$(dirname "$0")/.." || exit 1
fail=0
for t in tests/test_memory.py tests/test_fastpath.py tests/extension/run.py; do
  echo "== $t"; .venv/bin/python "$t" || fail=1
done
[ $fail = 0 ] && echo "ALL PASSED" || { echo "SOME TESTS FAILED"; exit 1; }
