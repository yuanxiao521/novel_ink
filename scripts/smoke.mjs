#!/usr/bin/env node
/**
 * L2 接口冒烟：把「主线剧本」跑成真 HTTP 断言（可复跑，不是文档）。
 * 覆盖：建书 → 加章加场景 → 主笔生成角色 → 推演 1 回合 → 定稿 → 写手初稿 → 走工具链落库
 *       → 世界状态增/改/删 → 批注 → 任务总线 → 权限边界(409) → 成书导出 → 清理测试书。
 * 断言不变量：word_count>0 ⇔ export 200、增量 PUT 保留字段并记旧值、全程 /health 不降级。
 * 用法：node scripts/smoke.mjs [--quick] [--base=http://127.0.0.1:8001]
 */
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const QUICK = args.includes("--quick");
const BASE = (args.find((a) => a.startsWith("--base=")) || "--base=http://127.0.0.1:8001").slice(7);
const API = BASE + "/api/v1";
const CLEANUP = fileURLToPath(new URL("./cleanup-book.sh", import.meta.url));
const stamp = Date.now().toString(36);
const results = [];
let bookId = "";

const req = async (method, path, body, expect = 200) => {
  const res = await fetch(API + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* 导出是 markdown，不是 JSON */ }
  const ok = Array.isArray(expect) ? expect.includes(res.status) : res.status === expect;
  if (!ok) results.push({ step: method + " " + path, detail: "http=" + res.status + " " + text.slice(0, 120), ok: false });
  return { status: res.status, text, json };
};

const step = (name, ok, detail = "") => {
  results.push({ step: name, detail, ok: Boolean(ok) });
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  :: " + detail : ""));
};

const skipped = [];
const skip = (name, why) => { skipped.push(name); console.log("SKIP  " + name + "  :: " + why); };

const healthOk = async () => {
  const h = await (await fetch(BASE + "/health")).json();
  return !(h && h.degraded && h.degraded.active);
};

const cleanup = async () => {
  if (!bookId) return;
  const byApi = await fetch(API + "/books/" + bookId, { method: "DELETE" });
  if (byApi.ok) { console.log("cleanup: API 删除测试书 ok"); return; }
  await new Promise((res) => execFile("bash", [CLEANUP, bookId], { encoding: "utf8" }, () => res(null)));
  console.log("cleanup: psql 删除测试书 ok（API 删除返回 " + byApi.status + "，外键无 CASCADE）");
};

try {
  console.log("L2 主线冒烟 @ " + BASE + (QUICK ? " (quick)" : ""));
  step("起始 /health 未降级", await healthOk());

  const book = await req("POST", "/books", { title: "冒烟测试书-" + stamp, genre: "玄幻", status: "planned" }, 201);
  bookId = (book.json && book.json.id) || "";
  step("建书", bookId, bookId);
  const ch = await req("POST", "/books/" + bookId + "/chapters", { title: "冒烟第 1 章", order_no: 1 }, 201);
  const chapterId = (ch.json && ch.json.id) || "";
  const sc = await req("POST", "/chapters/" + chapterId + "/scenes", { title: "冒烟场景" }, 201);
  const sceneId = (sc.json && sc.json.id) || "";
  step("加章 + 加场景", chapterId && sceneId, chapterId + " / " + sceneId);
  const tree = await req("GET", "/books/" + bookId + "/tree");
  step("书树可读且含新场景", tree.text.includes(sceneId));

  // LLM 可用性闸门（B32）：全链路唯一「没有确定性回退」的环节是写手·初稿；
  // 它失败就说明 LLM 其实没在工作（其它环节会静默走回退脚本 → 假成功）。
  let llmOk = true;
  if (!QUICK) {
    const probe = await req("POST", "/scenes/" + sceneId + "/tools/prose.draft", { args: {} }, [200, 409]);
    llmOk = probe.status === 200;
    step("LLM 可用性闸门（写手·初稿不走回退）", llmOk,
      llmOk ? "ok" : "LLM 不可用 → 后面的 LLM 断言不再计入 PASS：" + probe.text.slice(0, 110));
  }

  if (!QUICK && llmOk) {
    const gen = await req("POST", "/books/" + bookId + "/characters/generate", {}, [200, 201]);
    const cards = await req("GET", "/books/" + bookId + "/characters");
    const n = Array.isArray(cards.json) ? cards.json.length : 0;
    step("主笔生成角色卡", gen.status < 300 && n > 0, "cards=" + n);
  } else if (!QUICK) { skip("主笔生成角色卡", "LLM 不可用（角色卡也可能是回退产物）"); }

  const sim = await req("POST", "/sims", { book_id: bookId, chapter_id: chapterId, scene_id: sceneId, resume: false }, 201);
  const simId = (sim.json && sim.json.sim_id) || "";
  step("创建 sim", simId, simId);
  const stepped = await req("POST", "/sims/" + simId + "/step?n=1", undefined, [200, 201]);
  const st2 = await req("GET", "/sims/" + simId + "/state");
  const turn = (st2.json && st2.json.turn) || 0;
  step("推演 1 回合（turn>0）", stepped.status < 300 && turn > 0, "turn=" + turn + (llmOk ? "" : "（LLM 不可用 → 回退产物）"));
  const fin = await req("POST", "/sims/" + simId + "/finalize", undefined, [200, 201]);
  const words = (fin.json && fin.json.word_count) || 0;
  step("定稿聚合（word_count>0）", fin.status < 300 && words > 0, "words=" + words);

  if (!QUICK && llmOk) {
    const draft = await req("POST", "/scenes/" + sceneId + "/tools/prose.draft", { args: {} });
    const text = (draft.json && draft.json.data && draft.json.data.text) || "";
    step("写手·初稿产出候选（>=20 字）", draft.status === 200 && text.length >= 20, text.length + " 字");
    const saved = await req("POST", "/scenes/" + sceneId + "/tools/prose.save", { args: { scene_id: sceneId, text }, confirm: true });
    const prose = await req("GET", "/scenes/" + sceneId + "/prose");
    const kept = ((prose.json && (prose.json.prose || prose.json.final_prose)) || "").length;
    step("走工具链保存正文并读回", saved.status === 200 && kept >= 20, "落库 " + kept + " 字");
  }

  const ws = await req("POST", "/books/" + bookId + "/world-states", { kind: "item", name: "冒烟道具", value: "在" }, 201);
  const wsId = (ws.json && ws.json.id) || "";
  const wsPut = await req("PUT", "/world-states/" + wsId, { value: "遗失" });
  const wsList = await req("GET", "/books/" + bookId + "/world-states");
  const states = (wsList.json && wsList.json.states) || [];
  const row = states.find((x) => x.id === wsId) || {};
  step("世界状态：增量 PUT 保留 name/key 且记旧值", wsPut.status === 200 && row.name === "冒烟道具" && row.previous_value === "在", "value=" + row.value);
  step("世界状态：删除 204", (await req("DELETE", "/world-states/" + wsId, undefined, 204)).status === 204);

  await req("POST", "/scenes/" + sceneId + "/annotations", { note: "冒烟批注", para_index: 1, quote: "测试" }, 201);
  const anns = await req("GET", "/scenes/" + sceneId + "/annotations");
  step("批注创建且可读回", Array.isArray(anns.json) && anns.json.length > 0);
  const task = await req("POST", "/scenes/" + sceneId + "/agent/tasks", { kind: "rehearsal", from_agent: "author" }, [200, 201]);
  const tasks = await req("GET", "/scenes/" + sceneId + "/agent/tasks");
  step("任务总线建单可读", task.status < 300 && Array.isArray(tasks.json) && tasks.json.length > 0);

  const unknown = await req("POST", "/scenes/" + sceneId + "/tools/no.such_tool", { args: {} }, 409);
  const noConfirm = await req("POST", "/scenes/" + sceneId + "/tools/prose.save", { args: { scene_id: sceneId, text: "x".repeat(30) } }, 409);
  step("未知工具 409 且理由明确", unknown.status === 409 && unknown.text.includes("未知工具"));
  step("破坏性操作未确认 409", noConfirm.status === 409 && noConfirm.text.includes("确认"));

  const dash = await req("GET", "/books/" + bookId + "/dashboard");
  const wc = (dash.json && dash.json.kpi && dash.json.kpi.word_count) || 0;
  const ex = await req("GET", "/books/" + bookId + "/export", undefined, [200, 400]);
  const headOk = ex.status === 200 && ex.text.includes("全书约") && ex.text.includes("冒烟测试书-" + stamp);
  step("导出成书：书名 + 全书约 N 字", headOk, "http=" + ex.status);
  step("不变量 word_count>0 ⇔ export 200", (wc > 0) === (ex.status === 200), "wc=" + wc);
  step("结束 /health 未降级", await healthOk());
} catch (e) {
  step("冒烟执行异常", false, String(e));
} finally {
  try { await cleanup(); } catch (e) { step("cleanup 失败（需手工清）", false, String(e)); }
}

const bad = results.filter((r) => !r.ok);
const llmDown = bad.some((b) => b.step.includes("LLM 可用性闸门"));
console.log("");
console.log("L2 " + (bad.length ? (llmDown ? "BLOCKED" : "FAIL") : "PASS") + "：" + (results.length - bad.length) + "/" + results.length + " 项通过" + (skipped.length ? "，" + skipped.length + " 项跳过" : ""));
if (bad.length) { console.log("失败项："); for (const b of bad) console.log("  - " + b.step + "  " + b.detail); }
if (llmDown) {
  console.log("");
  console.log("→ LLM 不可用：检查前端/后端用的 provider 与账号（.env 的 NOVEL_OPENAI_BASE_URL / NOVEL_MODEL_* / KEY）。");
  console.log("  常见：模型名不存在（404 Model not exist）、账号异常/欠费（400 Access denied）、额度用尽。");
}
process.exit(bad.length ? 1 : 0);
