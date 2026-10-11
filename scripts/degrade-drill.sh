#!/usr/bin/env bash
# L4 降级演练：验证「DB 挂了不能静默」这条铁律。
# 语义：degraded 是**自后端启动以来**的累计指示（不是"当前是否宕"）——所以必须先触发一次 DB 操作才会亮；
#       恢复后新的 DB 操作必须成功，且计数不再增长（横幅会保留到后端重启，这是有意为之：降级过就必须看得见）。
# 用法：bash scripts/degrade-drill.sh [API_PORT]
set -uo pipefail
API_PORT="${1:-8001}"
B="http://127.0.0.1:$API_PORT"
fail=0

# 读 /health 的 degraded 字段（用 node 解析 JSON，避免 sed 脆弱）
read_deg() {
  curl -s --max-time 8 "$B/health" | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const j=JSON.parse(s);console.log((j.degraded&&j.degraded.active)?1:0, (j.degraded&&j.degraded.count)||0)}catch{console.log('ERR 0')}})"
}

echo "== 1) 停库 =="
before="$(read_deg)"; echo "  停前 degraded(active,count): $before"
docker stop novel-ink-db >/dev/null && echo "  stopped"
sleep 3

echo "== 2) 触发一次 DB 操作 → 降级必须可见且接口不 500 =="
C="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$B/api/v1/books")"
echo "  /api/v1/books http=$C"
[ "$C" = "200" ] && echo "  PASS 降级期接口不 500" || { echo "  FAIL 降级期接口异常"; fail=1; }
after="$(read_deg)"; echo "  触发后 degraded(active,count): $after"
A1="$(echo "$after" | cut -d' ' -f1)"; C1="$(echo "$after" | cut -d' ' -f2)"
C0="$(echo "$before" | cut -d' ' -f2)"
[ "$A1" = "1" ] && echo "  PASS 降级已上报（active=true）" || { echo "  FAIL 未上报降级"; fail=1; }
[ "$C1" -gt "$C0" ] 2>/dev/null && echo "  PASS 降级计数增长（$C0 → $C1）" || { echo "  FAIL 计数未增长"; fail=1; }

echo "== 3) 恢复 =="
docker start novel-ink-db >/dev/null
h=""
for _ in $(seq 1 30); do h="$(docker inspect --format "{{.State.Health.Status}}" novel-ink-db 2>/dev/null)"; [ "$h" = "healthy" ] && break; sleep 2; done
echo "  db: $h"
sleep 2
C2="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$B/api/v1/books")"
echo "  恢复后 /api/v1/books http=$C2"
[ "$C2" = "200" ] && echo "  PASS 恢复后新操作成功" || { echo "  FAIL 恢复后仍异常"; fail=1; }
final="$(read_deg)"; C3="$(echo "$final" | cut -d' ' -f2)"
echo "  恢复后 degraded(active,count): $final（累计指示，横幅保留到后端重启）"
[ "$C3" = "$C1" ] && echo "  PASS 恢复后计数不再增长" || { echo "  FAIL 计数异常增长"; fail=1; }

if [ "$fail" -eq 0 ]; then echo "L4 PASS（如需清掉横幅：重启后端）"; else echo "L4 FAIL"; fi
exit "$fail"
