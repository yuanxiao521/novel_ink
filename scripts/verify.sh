#!/usr/bin/env bash
# L1 闸门：pytest + tsc + vite build。任何一步失败即非零退出。用法：bash scripts/verify.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
fail=0
echo "== L1-1 pytest =="
( cd "$ROOT" && "$PY" -m pytest backend/tests -q 2>&1 | tail -3 ) || fail=1
echo "== L1-2 tsc --noEmit =="
( cd "$ROOT/frontend" && npx tsc --noEmit ) && echo "tsc: 0 错误" || fail=1
echo "== L1-3 vite build =="
( cd "$ROOT/frontend" && npm run build 2>&1 | tail -3 ) || fail=1
if [ "$fail" -eq 0 ]; then echo "L1 PASS"; else echo "L1 FAIL"; fi
exit "$fail"
