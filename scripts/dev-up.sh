#!/usr/bin/env bash
# 墨卷 · 开发环境一键起（Git Bash / WSL 均可，不再依赖 PowerShell）
# 用法：
#   bash scripts/dev-up.sh             # 后端 8000 + 前端 5173（默认）
#   bash scripts/dev-up.sh 8001 5173   # 端口被别的项目占用时显式指定
# 说明：服务用 nohup 起在后台，日志落 logs/，不绑定任何会话任务。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${1:-8000}"
WEB_PORT="${2:-5173}"
mkdir -p "$ROOT/logs"

listening() { netstat -ano 2>/dev/null | grep -E "[:.]$1[[:space:]]" | grep -q LISTENING; }

echo "== 1/3 Postgres：容器 novel-ink-db（宿主 5433）=="
docker start novel-ink-db >/dev/null 2>&1 || true
docker update --restart unless-stopped novel-ink-db >/dev/null 2>&1 || true
h=""
for _ in $(seq 1 30); do
  h="$(docker inspect --format "{{.State.Health.Status}}" novel-ink-db 2>/dev/null || echo "")"
  [ "$h" = "healthy" ] && break
  sleep 2
done
echo "   db: ${h:-unknown}"

echo "== 2/3 后端：uvicorn 127.0.0.1:$API_PORT =="
if listening "$API_PORT"; then
  echo "   已在监听，跳过"
else
  ( cd "$ROOT/backend" && nohup "$ROOT/.venv/Scripts/python.exe" -m uvicorn app.main:app \
      --host 127.0.0.1 --port "$API_PORT" >"$ROOT/logs/api-$API_PORT.log" 2>&1 & )
  sleep 6
  echo "   已启动 -> logs/api-$API_PORT.log"
fi
if command -v curl >/dev/null; then
  echo -n "   /health: "; curl -s --max-time 6 "http://127.0.0.1:$API_PORT/health" || echo "(未就绪)"
  echo ""
fi

echo "== 2.5/3 任务队列 worker：arq（Redis db1，长 LLM 操作后台化）=="
if tasklist 2>/dev/null | grep -q "arq\|tasks_worker" || netstat -ano 2>/dev/null | grep -qE ":6379.*ESTABLISHED.*python"; then
  echo "   疑似已在运行，跳过（如需重启请手工杀 python 进程）"
else
  ( cd "$ROOT/backend" && nohup "$ROOT/.venv/Scripts/python.exe" -m arq app.services.tasks_worker.WorkerSettings \
      >"$ROOT/logs/arq-worker.log" 2>&1 & )
  sleep 3
  echo "   已启动 -> logs/arq-worker.log"
fi

echo "== 3/3 前端：vite $WEB_PORT =="
if listening "$WEB_PORT"; then
  echo "   已在监听，跳过"
else
  ( cd "$ROOT/frontend" && nohup cmd //c npm run dev >"$ROOT/logs/web-$WEB_PORT.log" 2>&1 & )
  sleep 10
  echo "   已启动 -> logs/web-$WEB_PORT.log"
fi

echo ""
echo "前端 http://127.0.0.1:$WEB_PORT    后端 http://127.0.0.1:$API_PORT"
grep -q "VITE_API_BASE=http://127.0.0.1:$API_PORT" "$ROOT/frontend/.env.local" 2>/dev/null \
  || echo "提示：frontend/.env.local 的 VITE_API_BASE 与后端端口不一致时，前端会连错后端（改完重启前端）。"
