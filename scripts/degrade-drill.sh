#!/usr/bin/env bash
# L4 降级演练：停 DB → 断言 /health 报降级且接口不 500 → 起 DB → 断言恢复。
# 为什么必须做：B18/B21/B28 都是「DB 模式静默失效」，内存态测试全绿抓不到。用法：bash scripts/degrade-drill.sh [API_PORT]
set -uo pipefail
API_PORT="${1:-8001}"
B="http://127.0.0.1:$API_PORT"
fail=0
echo "== 1) 停库 =="
docker stop novel-ink-db >/dev/null && echo "  stopped"
sleep 3
echo "== 2) 降级必须可见 =="
H="$(curl -s --max-time 8 "$B/health")"
echo "  /health: $H"
echo "$H" | grep -q '"active":true' && echo "  PASS 降级已上报" || { echo "  FAIL 未上报降级"; fail=1; }
C="$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$B/api/v1/books")"
echo "  /api/v1/books http=$C"
[ "$C" = "200" ] && echo "  PASS 降级期接口不 500" || { echo "  FAIL 降级期接口异常"; fail=1; }
echo "== 3) 恢复 =="
docker start novel-ink-db >/dev/null
for _ in $(seq 1 30); do h="$(docker inspect --format "{{.State.Health.Status}}" novel-ink-db 2>/dev/null)"; [ "$h" = "healthy" ] && break; sleep 2; done
echo "  db: $h"
sleep 2
H2="$(curl -s --max-time 8 "$B/health")"
echo "  /health: $H2"
echo "$H2" | grep -q '"active":false' && echo "  PASS 已恢复" || { echo "  FAIL 未恢复（重启后端：bash scripts/dev-up.sh $API_PORT）"; fail=1; }
if [ "$fail" -eq 0 ]; then echo "L4 PASS"; else echo "L4 FAIL"; fi
exit "$fail"
