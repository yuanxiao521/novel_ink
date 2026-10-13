#!/usr/bin/env bash
# arq worker 监督循环（v1.44 补 · B51）
#
# 为什么要它：Docker/Redis 抖一下（连接被对端关闭），arq worker 会**直接退出且不自愈**，
# 而裸跑时没有任何东西会把它拉起来 → 主笔排批 / 审稿会 / 质量回环从此停在 queued，
# 前端要等到轮询超时（5 分钟）才报错。实测：worker 静默死了，队列里还留着一条
# 永远 running 的僵尸任务（_guard 只覆盖"任务内部抛异常"，覆盖不了"进程本身死掉"）。
#
# 用法：bash scripts/worker-supervise.sh
#       （建议交给 harness 后台任务或 nohup；dev-up.sh 后续也改走这里）
# 行为：worker 退出即重启（3s），每次重启打一行带时间的日志 —— 中断可见，不再静默。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend" || exit 1
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python"
while true; do
  echo "[supervisor] worker 启动 $(date '+%F %T')"
  "$PY" -m arq app.services.tasks_worker.WorkerSettings
  echo "[supervisor] worker 退出 code=$? $(date '+%F %T')，3s 后重启"
  sleep 3
done
