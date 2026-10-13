#!/usr/bin/env bash
# 复跑探针（v1.43）：点「审稿会」按钮（队列路径）→ 断言过程卡 / 裁决 / 明细富卡都长在责编流里。
# 为什么单列一个脚本：L3（ui-spec.mjs）刻意不依赖 arq worker 进程（⑨ 只覆盖同步按钮路径），
# 而「审稿会」是队列路径的旗舰按钮 —— 用这条命令单独看住它。
# 依赖：后端 8001 · 前端 5173 · arq worker 在跑 · chrome-headless-shell。
# 用法：bash scripts/ui-meeting-probe.sh [sceneId] [bookId]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SCENE="${1:-scene-85bc6bfe}"
BOOK="${2:-book-0938c02c}"
JS='(async () => { const b = Array.from(document.querySelectorAll(".ec-tool")).find((x) => x.textContent.includes("审稿会")); if (!b) return "no 审稿会 button"; if (b.disabled) return "button disabled（正文为空？）"; b.click(); const t0 = Date.now(); for (;;) { if (Date.now() - t0 > 180000) return "TIMEOUT " + ((document.querySelector(".ec-proc") || {}).innerText || ""); if (document.querySelector(".tr-verdict")) break; await new Promise((r) => setTimeout(r, 1500)); } return JSON.stringify({ verdict: ((document.querySelector(".tr-verdict") || {}).innerText || ""), steps: Array.from(document.querySelectorAll(".tr-step")).map((x) => x.textContent), folds: Array.from(document.querySelectorAll(".tr-fold > summary")).map((x) => x.textContent), stages: Array.from(document.querySelectorAll(".ec-stage-text")).map((x) => x.innerText), card: !!document.querySelector(".ec-result .tr-card"), items: document.querySelectorAll(".ec-result .tr-item").length }); })()'
node scripts/shot.mjs "--url=/studio/${SCENE}?book=${BOOK}&tab=editor" --ls-book="$BOOK" \
  --out=docs/mockups/p3/ink/23-editor-meeting.png --wait=9000 --js="$JS"
