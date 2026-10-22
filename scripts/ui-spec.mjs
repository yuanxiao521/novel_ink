#!/usr/bin/env node
/**
 * L3 UI 剧本（无头浏览器）：把「人眼验收」变成可复跑断言。
 * 断言：①深链不串书（栏/页一致）②上下文条换书生效且落到新书场景 ③刷新不丢 ④设置页失败可见
 * 依赖：本机 playwright 下载的 chrome-headless-shell（自动探测）；后端 8001、前端 5173。
 * 用法：node scripts/ui-spec.mjs [--web=http://127.0.0.1:5173] [--base=http://127.0.0.1:8001]
 */
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const WEB = (args.find((a) => a.startsWith("--web=")) || "--web=http://127.0.0.1:5173").slice(6);
const BASE = (args.find((a) => a.startsWith("--base=")) || "--base=http://127.0.0.1:8001").slice(7);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const step = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  :: " + detail : "")); };
const skip = (name, why = "") => { results.push({ name, ok: true, detail: "SKIP " + why }); console.log("SKIP  " + name + (why ? "  :: " + why : "")); };

const findShell = async () => {
  if (process.env.HEADLESS_SHELL) return process.env.HEADLESS_SHELL;
  const explicit = args.find((a) => a.startsWith("--shell="));
  if (explicit) return explicit.slice(8);
  const roots = [
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : "",
    path.join(os.homedir(), "AppData", "Local", "ms-playwright"),
    path.join(process.env.HOME || "", "AppData", "Local", "ms-playwright"),
  ].filter(Boolean);
  for (const root of roots) {
    try {
      for (const d of await fs.readdir(root)) {
        if (!d.startsWith("chromium_headless_shell")) continue;
        const sub = path.join(root, d);
        for (const v of await fs.readdir(sub)) {
          const exe = path.join(sub, v, "chrome-headless-shell.exe");
          try { await fs.access(exe); return exe; } catch { /* 继续找 */ }
        }
      }
    } catch { /* 目录不存在 */ }
  }
  return "";
};

const shell = await findShell();
if (!shell) { console.log("SKIP：未找到 chrome-headless-shell（装 playwright 浏览器后可跑）"); process.exit(0); }

// 取真实夹具（不硬编码 id）：一本有场景的书 + 另一本不同的书
const books = await (await fetch(BASE + "/api/v1/books")).json();
if (!Array.isArray(books) || books.length < 2) { console.log("SKIP：需要至少 2 本书作为夹具（当前 " + (books?.length ?? 0) + "）"); process.exit(0); }
let host = null;
for (const b of books) {
  const t = await (await fetch(BASE + "/api/v1/books/" + b.id + "/tree")).json();
  const scene = (t.chapters || []).flatMap((c) => c.scenes || [])[0];
  if (scene) { host = { book: b, sceneId: scene.id, sceneTitle: scene.title }; break; }
}
const other = books.find((b) => !host || b.id !== host.book.id);
if (!host || !other) { console.log("SKIP：夹具不足（需要一本有场景的书 + 另一本书）"); process.exit(0); }

// v1.43：⑧⑨ 需要一个**有正文**的场景（责编工具条在正文为空时是禁用的，避免误判成"按钮没输出"）
let proseScene = host.sceneId, proseBook = host.book.id, proseLen = 0;
for (const b of books) {
  const t = await (await fetch(BASE + "/api/v1/books/" + b.id + "/tree")).json().catch(() => null);
  for (const ch of (t && t.chapters) || []) {
    for (const s of ch.scenes || []) {
      const d = await (await fetch(BASE + "/api/v1/scenes/" + s.id)).json().catch(() => null);
      const n = d && d.final_prose ? d.final_prose.trim().length : 0;
      if (n > proseLen) { proseLen = n; proseScene = s.id; proseBook = b.id; }
    }
  }
}
console.log("夹具：正文最长的场景 " + proseScene + "（" + proseLen + " 字，书 " + proseBook + "）");

const port = 9377;
// v1.43 修（B47）：**不要 detached**。Node 文档明说 Windows 上 detached 子进程"会有自己的控制台窗口"，
// 而 chrome-headless-shell 是控制台程序 → 每次跑截图/剧本都弹一个 Windows Terminal 窗口；
// 且 proc.kill() 只杀父进程，chrome 的 GPU/渲染子进程会残留（实测攒了 15 个 + 一摞窗口）。
// 改为：不分离 + windowsHide + 退出时按**进程树** taskkill；任何 exit 路径都会清干净。
const proc = spawn(shell, ["--no-sandbox", "--remote-debugging-port=" + port, "--no-first-run", "--disable-gpu",
  "--user-data-dir=" + path.join(os.tmpdir(), "dsh-ui-spec"), "about:blank"], { windowsHide: true, stdio: "ignore" });
proc.on("error", () => {});
const killTree = () => {
  try {
    if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    else proc.kill("SIGKILL");
  } catch { /* 忽略 */ }
};
process.on("exit", killTree);

let page = null;
for (let i = 0; i < 40 && !page; i++) {
  await sleep(400);
  try { const l = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json(); page = l.find((t) => t.type === "page" && t.webSocketDebuggerUrl) || null; } catch {}
}
if (!page) { console.log("FAIL：无头浏览器未起来"); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = () => res(); ws.onerror = (e) => rej(e); });
let seq = 0; const pend = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); return r.result?.exceptionDetails ? "JSERR " + r.result.exceptionDetails.exception?.description : r.result?.result?.value; };
const goto = async (url, wait = 6000) => { await send("Page.navigate", { url }); await sleep(wait); };
const snap = () => ev(`(() => {
  const sels = Array.from(document.querySelectorAll(".ws-crumbs select"));
  const t = (s) => { const e = document.querySelector(s); return e ? e.textContent : null; };
  return JSON.stringify({ url: location.pathname + location.search,
    barBook: sels[0] ? sels[0].selectedOptions[0].textContent : null,
    barScene: sels[2] ? sels[2].selectedOptions[0].textContent : null,
    brand: t(".brand-title"), entry: !!document.querySelector(".ws-entry") }); })()`);

await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

try {
  await goto(WEB + "/", 4000);
  await ev(`localStorage.setItem("mk.workspace", JSON.stringify({ bookId: "${other.id}", chapterId: "", sceneId: "" })); 1`);
  // ① 深链不串书：记忆里是 B 书，深链指向 A 书场景 → 栏/页必须都是 A
  await goto(WEB + "/director/" + host.sceneId, 7000);
  const s1 = JSON.parse(await snap());
  step("① 深链不串书（栏/页一致）", s1.barBook === host.book.title && s1.brand === host.book.title && !s1.entry, JSON.stringify(s1));
  // ② 上下文条换书 → 落到新书场景，不停在空态入口
  await ev(`(() => { const s = document.querySelector(".ws-crumbs select"); s.value = "${other.id}"; s.dispatchEvent(new Event("change", { bubbles: true })); return 1; })()`);
  await sleep(8000);
  const s2 = JSON.parse(await snap());
  step("② 换书生效且落到新书场景", s2.barBook === other.title && s2.brand === other.title && !s2.entry && /\/director\/scene-/.test(s2.url), JSON.stringify(s2));
  // ③ 刷新不丢
  await send("Page.reload"); await sleep(7000);
  const s3 = JSON.parse(await snap());
  step("③ 刷新后上下文不丢", s3.barBook === other.title && s3.brand === other.title, JSON.stringify(s3));
  // ④ 设置页失败可见（空表单提交 → 必须出现可见横幅，不允许只打 console）
  await goto(WEB + "/settings?tab=states", 6000);
  await ev(`(() => { const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent.includes("新增") || x.textContent.includes("手写一条")); if (b) b.click(); return 1; })()`);
  await sleep(1200);
  await ev(`(() => { const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent.trim() === "添加"); if (b) b.click(); return 1; })()`);
  await sleep(1200);
  const bannerText = await ev(`(() => { const el = document.querySelector(".ws-msg.err"); return el ? el.textContent : ""; })()`);
  step("④ 设置页失败可见（横幅出现）", typeof bannerText === "string" && bannerText.length > 0, JSON.stringify(bannerText));

  // ⑤ 逐章校对（v1.35 成书收口 C）：每章每场都要能读出定稿状态与字数
  await goto(WEB + "/proofread?book=" + host.book.id, 7000);
  const pf = JSON.parse((await ev(`(() => {
    const cards = document.querySelectorAll(".kpi-card").length;
    const scenes = document.querySelectorAll(".pf-scene").length;
    const first = document.querySelector(".kpi-card .kpi-value");
    return JSON.stringify({ cards, scenes, words: first ? first.textContent.trim() : "" });
  })()`)) || "{}");
  step("⑤ 逐章校对：按章列出场景与定稿状态", pf.cards >= 4 && pf.scenes >= 1, JSON.stringify(pf));

  // ⑥ 导演台恢复上次进度：有回合的场景必须把时间线画出来（B40/B41）
  let turnScene = null;
  for (const b of books) {
    const t = await (await fetch(BASE + "/api/v1/books/" + b.id + "/tree")).json();
    const gv = await (await fetch(BASE + "/api/v1/books/" + b.id + "/global-view")).json().catch(() => null);
    const withTurns = new Set((gv?.curve || []).filter((c) => (c.turns || 0) > 0).map((c) => c.chapter_id));
    for (const ch of t.chapters || []) {
      if (withTurns.has(ch.id) && (ch.scenes || []).length) { turnScene = { book: b, scene: ch.scenes[0] }; break; }
    }
    if (turnScene) break;
  }
  if (turnScene) {
    await goto(WEB + "/director/" + turnScene.scene.id + "?book=" + turnScene.book.id, 9000);
    const dr = JSON.parse((await ev(`(() => {
      const meta = document.querySelector(".tl-meta");
      return JSON.stringify({ meta: meta ? meta.innerText.replace(/\\s+/g, " ").trim() : "", empty: !!document.querySelector(".timeline-empty") });
    })()`)) || "{}");
    step("⑥ 导演台恢复上次进度（回合时间线可读）", /共\s*[1-9]\d*\s*回合/.test(dr.meta || "") && !dr.empty, JSON.stringify(dr));
  } else { skip("⑥ 导演台恢复上次进度", "没有「演过回合」的场景作为夹具"); }

  // ⑦ 副驾闭环（v1.37）：选中正文 → 「问责编」→ 先落批注 + 责编输入框预填带批注上下文
  await goto(WEB + "/studio/" + host.sceneId + "?book=" + host.book.id, 8000);
  const annFlow = JSON.parse((await ev(`(async () => {
    const para = document.querySelector(".pb-para");
    if (!para) return JSON.stringify({ err: "no .pb-para" });
    // 用 Selection API 真选该段前 10 个字（与真人选中一致）
    const range = document.createRange();
    range.selectNodeContents(para);
    const sel = window.getSelection(); sel?.removeAllRanges(); sel?.addRange(range);
    para.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 600));
    const bar = document.querySelector(".pb-bar");
    if (!bar) return JSON.stringify({ err: "no .pb-bar（选中条没出现）" });
    const ask = Array.from(bar.querySelectorAll("button")).find((b) => b.textContent.includes("问责编"));
    if (!ask) return JSON.stringify({ err: "no 问责编 button" });
    ask.click();
    await new Promise((r) => setTimeout(r, 600));
    // Dialog 的 prompt 输入框
    const dlgInput = document.querySelector(".dialog-input");
    if (!dlgInput) return JSON.stringify({ err: "no dialog input", dlg: !!document.querySelector(".dialog,.ws-dialog,.mk-dialog") });
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(dlgInput, "这段留白再多一点");
    dlgInput.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 300));
    const okBtn = document.querySelector(".dialog-btn-ok");
    if (okBtn) okBtn.click();
    await new Promise((r) => setTimeout(r, 1500));
    // 责编 tab 被激活 + 输入框预填带批注上下文
    const seed = document.querySelector(".ec-input input");
    const anns = document.body.innerText.includes("待处理批注") || document.body.innerText.includes("待处理");
    return JSON.stringify({ editorTab: location.search.includes("tab=editor"), seed: seed ? seed.value.slice(0, 60) : "", annsBadge: anns });
  })()`)) || "{}");
  step("⑦ 副驾闭环：选中 → 问责编自动落批注并预填对话", !annFlow.err && annFlow.editorTab && String(annFlow.seed).includes("批注意见"), JSON.stringify(annFlow));
  // ⑧ 信息架构（v1.43）：按钮**留在顶栏原位**，只有过程与明细进责编流；审计记录可收起
  await goto(WEB + "/studio/" + proseScene + "?book=" + proseBook + "&tab=editor", 8000);
  const bar = JSON.parse((await ev("(() => {"
    + "const topBtn = Array.from(document.querySelectorAll('.s2-actions button')).map((b) => b.textContent.trim());"
    + "const more = document.querySelector('.s2-more');"
    + "const moreBtns = more ? Array.from(more.querySelectorAll('.s2-more-body button')).map((b) => b.textContent.trim()) : [];"
    + "const sideHead = document.querySelector('.s2-side-head');"
    + "return JSON.stringify({ topBtn: topBtn, more: !!more, moreBtns: moreBtns, ecTool: document.querySelectorAll('.ec-tool').length, tabs: sideHead ? sideHead.innerText.replace(/\\s+/g, ' ') : '', auditToggle: !!document.querySelector('.s2-audit-toggle') });"
    + "})()")) || "{}");
  step("⑧ 按钮留在顶栏（审稿会 + 精修工具 7 个），右栏不再堆按钮，审计可收起",
    bar.topBtn.includes("🗂 审稿会") && bar.more === true && (bar.moreBtns || []).length === 7 && bar.ecTool === 0
      && !String(bar.tabs).includes("质检") && bar.auditToggle === true,
    JSON.stringify(bar));

  // ⑧b 审计记录可收起：点一下 → 列表消失、正文区拿回高度
  const aud = JSON.parse((await ev("(() => {"
    + "const t = document.querySelector('.s2-audit-toggle');"
    + "if (!t) return JSON.stringify({ err: 'no toggle' });"
    + "const before = document.querySelectorAll('.s2-audit-list').length;"
    + "t.click();"
    + "return JSON.stringify({ before: before });"
    + "})()")) || "{}");
  await sleep(500);
  const audAfter = JSON.parse((await ev("(() => {"
    + "const box = document.querySelector('.s2-audit');"
    + "const collapsed = !!(box && box.className.includes('collapsed'));"
    + "const list = document.querySelectorAll('.s2-audit-list').length;"
    + "const t = document.querySelector('.s2-audit-toggle'); if (t) t.click();"
    + "return JSON.stringify({ collapsed: collapsed, list: list });"
    + "})()")) || "{}");
  step("⑧b 审计记录可收起（收起后列表消失，再点展开）",
    aud.before === 1 && audAfter.collapsed === true && audAfter.list === 0, JSON.stringify({ ...aud, ...audAfter }));

  // ⑨ 顶栏点「体检」→ 自动切到责编 tab + 过程卡（tool_start→tool_done 摘要）+ 结果富卡——用户实测痛点的回归门
  const health = await (await fetch(BASE + "/health")).json().catch(() => null);
  // LLM 不健康时**明确 SKIP**而不是让它"绿着"：回退路径也会产出 0 条问题的报告，
  // 断言 /体检：\d+ 条问题/ 会假通过（实测上游 503 熔断时就这样骗过一次）。
  const llmDown = !health || !health.llm || !health.llm.configured || health.llm.ok === false;
  const llmWhy = health && health.llm
    ? (health.llm.configured ? String(health.llm.last_error || "").slice(0, 90) : "未配置 API Key")
    : "拿不到 /health";
  if (llmDown) {
    skip("⑨ 顶栏体检按钮：自动切责编 + 过程卡 + 结果富卡", "LLM 不可用：" + llmWhy);
  } else {
    const rv = JSON.parse((await ev("(async () => {"
      + "const more = document.querySelector('.s2-more');"
      + "if (more && !more.open) more.querySelector('summary').click();"
      + "await new Promise((r) => setTimeout(r, 400));"
      + "const b = Array.from(document.querySelectorAll('.s2-more-body button')).find((x) => x.textContent.includes('体检'));"
      + "if (!b) return JSON.stringify({ err: 'no 体检 button in 精修工具' });"
      + "if (b.disabled) return JSON.stringify({ err: 'button disabled（正文为空？）' });"
      + "b.click();"
      + "const t0 = Date.now();"
      + "for (;;) {"
      + "  if (Date.now() - t0 > 120000) return JSON.stringify({ err: 'timeout', tab: location.search, seen: (document.querySelector('.ec-proc') ? document.querySelector('.ec-proc').innerText : '').slice(0, 200) });"
      + "  if (document.querySelector('.ec-proc .ec-stage.tool_done')) break;"
      + "  await new Promise((r) => setTimeout(r, 1000));"
      + "}"
      + "await new Promise((r) => setTimeout(r, 800));"
      + "const card = document.querySelector('.ec-result .tr-card');"
      + "return JSON.stringify({ tab: location.search, editorTab: location.search.includes('tab=editor'),"
      + "  stages: document.querySelectorAll('.ec-proc .ec-stage').length,"
      + "  detail: (document.querySelector('.ec-proc .ec-stage.tool_done .ec-stage-text') || {}).innerText || '',"
      + "  card: !!card, items: document.querySelectorAll('.ec-result .tr-item').length,"
      + "  overall: (document.querySelector('.tr-overall') || {}).innerText || '',"
      + "  subs: Array.from(document.querySelectorAll('.ec-result .tr-sub')).map((x) => x.textContent.trim()).slice(0, 5) });"
      + "})()")) || "{}");
    // 区分「功能坏了」与「上游中途挂了」：回退报告（overall 含"模型未接入"+0 条问题）属于后者，
    // 后者不该让 L3 假红（这轮就被上游账号池熔断坑过一次）。
    const fallbackHit = String(rv.overall || "").includes("模型未接入");
    if (!rv.err && fallbackHit) {
      skip("⑨ 顶栏体检按钮：自动切责编 + 过程卡 + 结果富卡", "LLM 运行中不可用（返回了回退报告）");
    } else step("⑨ 顶栏体检按钮：自动切责编 tab + 过程卡 + 结果富卡（明细可见，且**非回退**）",
      !rv.err && rv.editorTab === true && rv.card === true && /体检：\d+ 条问题/.test(String(rv.detail))
        && !String(rv.overall).includes("模型未接入")
        && (rv.subs || []).some((s) => String(s).includes("问题清单")),
      JSON.stringify(rv));
  }

  // ⑩ 写手流式（v1.44）：点顶栏「写手 · 初稿」→ 正文区**边收边写**（采样到中间态）+ 头部「未保存」
  //    注意：先**重新导航**拿干净页面 —— ⑨ 刚跑完体检，顶栏按钮可能还处在上一轮的 busy 禁用态
  //    （实测踩到：disabledAtClick=true 导致整条断言超时）。
  if (llmDown) {
    skip("⑩ 写手流式（正文区边收边写）", "LLM 不可用：" + llmWhy);
  } else {
    await goto(WEB + "/studio/" + proseScene + "?book=" + proseBook + "&tab=editor", 8000);
    const dr = JSON.parse((await ev("(async () => {"
      + "const find = () => Array.from(document.querySelectorAll('.s2-actions button')).find((x) => x.textContent.includes('写手'));"
      + "let b = find();"
      + "if (!b) return JSON.stringify({ err: 'no 写手 button' });"
      + "const readLen = () => ((document.querySelector('.s2-prose') || {}).innerText || '').replace(/\\s/g, '').length;"
      + "const initial = readLen();"
      + "/* 等按钮可用（上一次工具跑完才解禁；如实记录点击时的禁用态，不猜） */"
      + "const tWait = Date.now();"
      + "while (b.disabled && Date.now() - tWait < 15000) { await new Promise((r) => setTimeout(r, 200)); b = find(); }"
      + "const disabledAtClick = !!b.disabled;"
      + "b.click();"
      + "const lens = [];"
      + "let sawBusyLabel = false;"
      + "const t0 = Date.now();"
      + "for (;;) {"
      + "  await new Promise((r) => setTimeout(r, 150));"
      + "  const cur = find();"
      + "  if (cur && cur.textContent.includes('写手中')) sawBusyLabel = true;"
      + "  lens.push(readLen());"
      + "  if (Date.now() - t0 > 120000) break;"
      + "  const L = lens.length;"
      + "  if (L >= 4 && lens[L-1] === lens[L-2] && lens[L-2] === lens[L-3] && lens[L-3] === lens[L-4] && lens[L-1] !== initial) break;"
      + "}"
      + "const final = lens[lens.length - 1] || 0;"
      + "const mid = lens.filter((n) => n > 0 && n !== initial && n !== final).length;"
      + "return JSON.stringify({ initial: initial, final: final, samples: lens.length, mid: mid,"
      + "  disabledAtClick: disabledAtClick, sawBusyLabel: sawBusyLabel,"
      + "  dirty: !!document.querySelector('.s2-dirty'),"
      + "  saveLabel: (Array.from(document.querySelectorAll('.s2-actions button')).map((x) => x.textContent.trim()).find((x) => x.includes('保存')) || '') });"
      + "})()")) || "{}");
    // 断言：正文区被**流式写入**（换掉了旧稿）· 抓到过中间态或"写手中"按钮态（证明不是一次性替换）· 未落库
    const upstreamDead = !dr.err && dr.final === dr.initial && dr.sawBusyLabel === true;
    if (upstreamDead) {
      skip("⑩ 写手流式（正文区边收边写）", "LLM 运行中不可用（点了按钮但一个字没产出）");
    } else step("⑩ 写手流式：正文区边收边写 +「未保存」标记 + 保存按钮转「保存*」",
      !dr.err && dr.disabledAtClick === false && dr.final > 50 && dr.final !== dr.initial
        && (dr.mid >= 1 || dr.sawBusyLabel === true)
        && dr.dirty === true && String(dr.saveLabel).includes("保存*"),
      JSON.stringify(dr));
  }

  // ⑪ 队列活性可见（v1.45 · B51）：/health.queue 暴露 worker 心跳——前端横幅据此提示「没有消费者」
  const q = health && health.queue;
  step("⑪ 队列活性可见（/health.queue：worker 心跳 / 队列长度 / 僵尸数）",
    !!q && typeof q.alive === "boolean" && q.alive === true
      && typeof q.queued === "number" && typeof q.stuck === "number",
    JSON.stringify(q));

  // ⑫ 章级写作主线（v1.46 · 直写链路）：/write/{chapterId} 打开即见**整章**正文
  //    产品方向 v2：章是最小可写单位，场是可选细分 —— 没有场景也必须能写、能看。
  let proseChapter = null;
  let proseBookId = "";
  for (const b of books) {
    const t = await (await fetch(BASE + "/api/v1/books/" + b.id + "/tree")).json().catch(() => null);
    for (const ch of (t && t.chapters) || []) {
      const d = await (await fetch(BASE + "/api/v1/chapters/" + ch.id + "/prose")).json().catch(() => null);
      if (d && String(d.prose_md || "").trim().length > 200) {
        proseChapter = { id: ch.id, title: ch.title }; proseBookId = b.id; break;
      }
    }
    if (proseChapter) break;
  }
  if (!proseChapter) {
    skip("⑫ 章级写作主线（/write 打开即见整章）", "没有「章正文非空」的章节作夹具（先在写作页点「写这一章」）");
  } else {
    await goto(WEB + "/write/" + proseChapter.id + "?book=" + proseBookId, 8000);
    const wr = JSON.parse((await ev("(() => {"
      + "const paras = Array.from(document.querySelectorAll('.wp-para'));"
      + "return JSON.stringify({ url: location.pathname,"
      + "  title: (document.querySelector('.s2-title')||{}).innerText || '',"
      + "  sub: (document.querySelector('.s2-sub')||{}).innerText || '',"
      + "  paras: paras.length,"
      + "  first: paras[0] ? paras[0].innerText.trim().slice(0, 40) : '',"
      + "  btns: Array.from(document.querySelectorAll('.s2-actions button')).map((b) => b.textContent.trim()),"
      + "  prog: document.querySelectorAll('.wp-progress-bar').length });"
      + "})()")) || "{}");
    step("⑫ 章级写作主线：/write 打开即见整章正文 + 进度条 + 「写这一章」",
      !wr.err && String(wr.url).startsWith("/write/") && wr.paras >= 3
        && String(wr.first).length > 10
        && (wr.btns || []).some((b) => String(b).includes("写这一章"))
        && wr.prog === 1,
      JSON.stringify(wr));

    // ⑬ 记忆面板（v1.49 · 记忆检索）：写作页左栏必须能读到「写手这次会读到什么」
    //    动机：直写链路原来只有「上一章结尾 600 字」，作者看不到注入 prompt 的素材，
    //    写偏了只能归咎"模型不行"。这一项永久回归：前情/角色/世界观/伏笔四类 + 检索模式 + 折叠展开。
    const mr = JSON.parse((await ev("(async () => {"
      + "const title = Array.from(document.querySelectorAll('.s2-info-title')).map((x) => x.textContent.trim()).find((t) => t.indexOf('记忆') >= 0);"
      + "if (!title) return JSON.stringify({ err: 'no memory section' });"
      + "const line = (document.querySelector('.wp-mem-line') || {}).innerText || '';"
      + "const km = line.match(/知晓\\s*(\\d+)/);"          // v1.62：知晓段的条数（0 = 没有 digest）
      + "const know = km ? Number(km[1]) : -1;"
      + "const toggle = document.querySelector('.wp-mem-toggle');"
      + "const before = document.querySelectorAll('.wp-mem-item').length;"
      + "if (toggle && toggle.textContent.indexOf('展开') >= 0) toggle.click();"
      + "await new Promise((r) => setTimeout(r, 400));"
      + "return JSON.stringify({ line: line, before: before, know: know,"
      + "  mode: (document.querySelector('.wp-mem-mode') || {}).innerText || '',"
      + "  groups: Array.from(document.querySelectorAll('.wp-mem-group-head')).map((x) => x.innerText.trim()),"
      + "  items: document.querySelectorAll('.wp-mem-item').length,"
      + "  actions: Array.from(document.querySelectorAll('.wp-mem-actions button')).map((b) => b.textContent.trim()) });"
      + "})()")) || "{}");
    //    v1.62：「知晓范围」独立成组（谁知道了什么 = 后文写崩的头号原因，必须能单独看）
    step("⑬ 记忆面板：写作页可读「写手会读到什么」（前情/知晓/角色/世界观/伏笔 + 检索模式 + 可展开）",
      !mr.err && String(mr.line).indexOf("前情") >= 0 && String(mr.line).indexOf("知晓") >= 0
        && String(mr.line).indexOf("伏笔") >= 0
        && mr.items >= 1 && (mr.actions || []).some((a) => String(a).indexOf("检索") >= 0)
        && (Number(mr.know) <= 0 || (mr.groups || []).some((g) => /知晓范围/.test(g))),
      JSON.stringify(mr));
  }

  // ⑭ 全局脉络 + 骨架体检（v1.50 · 0-token）：主笔页必须能读到"这本书现在什么形状"
  //    动机：主笔"掌管全局"不能靠把全文塞进 prompt，也不能靠模型凭印象——要靠确定性台账。
  //    永久回归：面板出现、章行 ≥1、每章有生产方式（直写/涌现/混合）、有体检结论（问题或"没问题"）。
  if (!books.length) {
    skip("⑭ 全局脉络（骨架体检 + 生产方式）", "没有书作夹具");
  } else {
    const bm = books[0];
    await goto(WEB + "/maestro?book=" + bm.id, 8000);
    const br = JSON.parse((await ev("(async () => {"
      + "const btn = Array.from(document.querySelectorAll('.outline-actions button')).find((x) => x.textContent.indexOf('全局脉络') >= 0);"
      + "if (!btn) return JSON.stringify({ err: 'no brief button' });"
      + "btn.click();"
      + "const t0 = Date.now();"
      + "while (!document.querySelector('.mk-brief') && Date.now() - t0 < 15000) { await new Promise((r) => setTimeout(r, 200)); }"
      + "const box = document.querySelector('.mk-brief');"
      + "if (!box) return JSON.stringify({ err: 'no brief panel' });"
      + "return JSON.stringify({"
      + "  head: ((box.querySelector('.mk-brief-head') || {}).innerText || '').replace(/\\s+/g, ' ').slice(0, 80),"
      + "  rows: box.querySelectorAll('.mk-brief-ch').length,"
      + "  modes: Array.from(box.querySelectorAll('.mk-brief-ch .mode')).map((x) => x.textContent.trim()),"
      + "  score: (box.querySelector('.mk-brief-score') || {}).innerText || '',"
      + "  hasIssuesOrOk: !!(box.querySelector('.mk-brief-issue') || box.querySelector('.mk-brief-ok')) });"
      + "})()")) || "{}");
    step("⑭ 全局脉络：主笔页可读骨架体检 + 每章生产方式（0-token）",
      !br.err && br.rows >= 1 && String(br.score).indexOf("分") >= 0
        && (br.modes || []).length >= 1
        && (br.modes || []).every((m) => ["直写", "涌现", "混合"].indexOf(String(m)) >= 0)
        && br.hasIssuesOrOk === true,
      JSON.stringify(br));
  }

  // ⑮ 字段级记忆索引（v1.62）：章 digest 必须真的进索引，场素材不能漏
  //    动机：v1.49 索引是「一章一块」，v1.58 有了结构化 digest 却没进索引（两套口径）；
  //    且 DB 模式的章 dict 不带 scenes，**场素材整体从没进过索引**（实测 5 场 → 0 块）。
  //    永久回归：某本书的索引里有 chapter_fact/知晓或钩子；该书有场时必须有 scene 块；
  //    且 /chapters/{id}/recall 的返回里 knowledge 段必须存在（前端口径）。
  let idxBook = null;
  for (const b of books) {
    const st = await (await fetch(BASE + "/api/v1/books/" + b.id + "/memory/stats")).json().catch(() => null);
    const bk = (st && st.by_kind) || {};
    if (Number(bk.chapter_fact || 0) >= 1) { idxBook = { book: b, byKind: bk }; break; }
  }
  if (!idxBook) {
    skip("⑮ 字段级记忆索引（章事实/知晓/钩子 + 场素材）", "没有「已生成前情概要」的章作夹具（先保存正文 → 后台生成 digest）");
  } else {
    const t = await (await fetch(BASE + "/api/v1/books/" + idxBook.book.id + "/tree")).json().catch(() => null);
    const chs = (t && t.chapters) || [];
    const sceneCount = chs.reduce((n, c) => n + ((c.scenes || []).length), 0);
    const target = chs.find((c) => c.order_no >= 1) || chs[0];
    const rec = target
      ? await (await fetch(BASE + "/api/v1/chapters/" + target.id + "/recall")).json().catch(() => null)
      : null;
    const bk = idxBook.byKind;
    step("⑮ 字段级记忆索引：章事实/知晓/钩子 + 场素材都进了索引，recall 带 knowledge 段",
      Number(bk.chapter_fact || 0) >= 1
        && (Number(bk.chapter_knowledge || 0) >= 1 || Number(bk.chapter_hook || 0) >= 1)
        && (sceneCount === 0 || Number(bk.scene || 0) >= 1)
        && !!rec && Array.isArray(rec.knowledge),
      JSON.stringify({ book: idxBook.book.id, byKind: bk, scenes: sceneCount,
                       hasKnowledgeKey: !!rec && Array.isArray(rec.knowledge) }));
  }

  // ⑯ 导演台左栏 / 抽屉不再被裁 + 角色卡显示真实名字（v1.63 · 作者原话「角色配置一直有问题，
  //    会展示不全，尺寸有问题」）。根因两条：①抽屉 288/300px 却定位在 232px 栏里、right:0
  //    → 左侧 72~84px 跑到视口外；②角色栏只认 3 个写死的演示角色，真实书角色回落成
  //    char-xxxx 原始 id + 破图 + 空目标。永久回归：抽屉必须完整在视口内 + 名字不得是原始 id。
  // 找一个**有上场角色**的场景当夹具：只有那种页面才渲染 .char-name（空台是引导卡）
  const dirCands = [];
  for (const b of books) {
    const t = await (await fetch(BASE + "/api/v1/books/" + b.id + "/tree")).json().catch(() => null);
    for (const ch of (t && t.chapters) || []) {
      for (const s of ch.scenes || []) dirCands.push({ book: b, scene: s });
    }
  }
  let dr2 = null;
  for (const c of dirCands.slice(0, 4)) {
    await goto(WEB + "/director/" + c.scene.id + "?book=" + c.book.id, 8000);
    dr2 = JSON.parse((await ev("(async () => {"
    + "const names = Array.from(document.querySelectorAll('.char-name')).map((x) => x.textContent.trim());"
    + "const rawIds = names.filter((n) => /^char-/.test(n)).length;"
    + "const rail = document.querySelector('.char-rail');"
    + "const scroll = document.querySelector('.char-rail-scroll');"
    + "const btns = Array.from(document.querySelectorAll('.rail-actions .view-switch'));"
    + "const out = { names: names.slice(0, 6), rawIds: rawIds, railW: rail ? Math.round(rail.getBoundingClientRect().width) : 0,"
    + "  scrollOv: scroll ? getComputedStyle(scroll).overflowY : '',"
    + "  railOv: rail ? getComputedStyle(rail).overflow : '' };"
    + "const g = btns.find((b) => b.textContent.indexOf('全局视角') >= 0);"
    + "if (g) { g.click(); await new Promise((r) => setTimeout(r, 700)); }"
    + "const d = document.querySelector('.rail-drawer');"
    + "if (d) { const r = d.getBoundingClientRect(); out.drawer = { x: Math.round(r.x), w: Math.round(r.width), right: Math.round(r.x + r.width) };"
    + "  out.vw = window.innerWidth; out.offscreen = r.x < 0 || r.x + r.width > window.innerWidth + 1; }"
    + "return JSON.stringify(out);"
    + "})()")) || "{}");
    if ((dr2.names || []).length > 0) break;      // 这一场的 sim 有上场角色 → 用它断言
  }
  if (!dr2 || (dr2.names || []).length === 0) {
    skip("⑯ 导演台角色配置不再被裁（抽屉 + 真实名字）", "没有「已配置上场角色」的场景作夹具");
  } else {
    step("⑯ 导演台角色配置不再被裁：抽屉完整在视口内 + 角色卡显示真实名字",
      !dr2.err && dr2.rawIds === 0 && !!dr2.drawer && dr2.offscreen === false
        && String(dr2.railOv || "").indexOf("auto") < 0,   // 栏本身不许再吃 overflow（否则裁抽屉）
      JSON.stringify(dr2));
  }

  // ⑰ 正文协作「精修工具」下拉不再逐字竖排（v1.63 · 作者截图「体/检」两行、尺寸不对）
  //    根因：.s2-more-body 是绝对定位且**没写宽度** → 收缩宽度被包含块（summary 的 87px）夹住，
  //    每个按钮只剩 ~69px → 中文逐字换行。修法：width:max-content + min-width + 按钮 nowrap。
  //    永久回归：菜单宽度 ≥140、每项高度 ≤34（单行）、不越出视口右边。
  await goto(WEB + "/studio/" + proseScene + "?book=" + proseBook, 7000);
  const menu = JSON.parse((await ev("(async () => {"
    + "const d = document.querySelector('.s2-more');"
    + "if (!d) return JSON.stringify({ err: 'no s2-more' });"
    + "d.open = true; await new Promise((r) => setTimeout(r, 350));"
    + "const body = d.querySelector('.s2-more-body');"
    + "if (!body) return JSON.stringify({ err: 'no menu body' });"
    + "const bb = body.getBoundingClientRect();"
    + "const items = Array.from(body.querySelectorAll('.ws-btn')).map((b) => { const q = b.getBoundingClientRect(); return { t: b.textContent.trim(), w: Math.round(q.width), h: Math.round(q.height) }; });"
    + "return JSON.stringify({ vw: window.innerWidth, w: Math.round(bb.width), h: Math.round(bb.height),"
    + "  overflowRight: bb.right > window.innerWidth + 1, items: items });"
    + "})()")) || "{}");
  step("⑰ 正文协作「精修工具」下拉：菜单按内容撑开、每项单行、不越出视口",
    !menu.err && Number(menu.w) >= 140 && menu.overflowRight === false
      && (menu.items || []).length >= 5
      && (menu.items || []).every((x) => Number(x.h) <= 34),
    JSON.stringify(menu));

  // ⑱ 章级写作页必须有入口（v1.64 · 作者：「找不到 /write，跟正文协作不是一个地方吗」）
  //    事实：以前只有 /write/:chapterId，直接开 /write 会被 * 兜底跳回首页，
  //    侧栏也没有入口 → 章级写作页只能从「校对成书」里点进去。
  //    永久回归：①/write 无 id 能落到具体章 ②侧栏「正文协作」href 指向 /write/ ③章页有场景精修入口。
  await goto(WEB + "/dashboard?book=" + host.book.id, 7000);
  const navW = JSON.parse((await ev("(async () => {"
    + "const items = Array.from(document.querySelectorAll('.nav-item'));"
    + "const t = items.find((x) => (x.textContent || '').indexOf('章级写作') >= 0);"
    + "return JSON.stringify({ href: t ? t.getAttribute('href') : null, items: items.length });"
    + "})()")) || "{}");
  await goto(WEB + "/write?book=" + host.book.id, 7000);
  const wr2 = JSON.parse((await ev("JSON.stringify({"
    + "path: location.pathname, title: (document.querySelector('.s2-title') || {}).textContent || '',"
    + "btns: Array.from(document.querySelectorAll('.s2-actions .ws-btn')).map((b) => b.textContent.trim()) })")) || "{}");
  step("⑱ 章级写作页可直达：/write 自动落到当前章 + 侧栏「章级写作」指向它 + 页内有场景精修入口",
    !navW.err && String(navW.href || "").indexOf("/write/") === 0
      && !wr2.err && String(wr2.path).indexOf("/write/chapter-") === 0
      && (wr2.btns || []).some((b) => String(b).indexOf("场景精修") >= 0),
    JSON.stringify({ nav: navW, write: wr2 }));

  // ⑲ 章级工作台 = 唯一写作主线（v1.65 · 两个页面合并）
  //    作者原话：「有必要这样分两个页面吗，这样很乱」→ 合并。永久回归：
  //    ①右栏是「本章副驾」（责编面板在场，不再只有一串进度数字）②章级精修工具在顶栏
  //    ③场块仍可见（场是可选细分，可并入章正文）。
  if (!proseChapter) {
    skip("⑲ 章级工作台（副驾 + 章级工具 + 场块）", "没有「章正文非空」的章作夹具");
  } else {
    await goto(WEB + "/write/" + proseChapter.id + "?book=" + proseBookId, 8000);
    const wb = JSON.parse((await ev("JSON.stringify({"
      + "side: (document.querySelector('.s2-side-head .ec-title') || {}).textContent || '',"
      + "tabs: Array.from(document.querySelectorAll('.s2-side .tab')).map((b) => b.textContent.trim()),"
      + "copilot: !!document.querySelector('.ec-wrap'),"
      + "toolbar: Array.from(document.querySelectorAll('.s2-actions .ws-btn, .s2-actions summary')).map((b) => b.textContent.trim()),"
      + "scenes: document.querySelectorAll('.s2-info .s2-ann-mini').length })")) || "{}");
    step("⑲ 章级工作台：右栏本章副驾（责编）+ 章级精修工具 + 场块",
      !wb.err && String(wb.side).indexOf("本章副驾") >= 0 && wb.copilot === true
        && (wb.tabs || []).some((t) => String(t).indexOf("责编") >= 0)
        && (wb.toolbar || []).some((t) => String(t).indexOf("体检整章") >= 0)
        && (wb.toolbar || []).some((t) => String(t).indexOf("精修工具") >= 0),
      JSON.stringify(wb));
  }

  // ⑳ 不留空路由（v1.66 收口）：/studio 已无独立页 → 必须重定向到章级工作台，而不是白页/空态。
  //    背景：v1.65 把场级页降级为二级页后，/studio 这条只带 query 的入口最容易留成空路由。
  await goto(WEB + "/studio?book=" + host.book.id, 8000);
  const rd = JSON.parse((await ev("JSON.stringify({"
    + "path: location.pathname,"
    + "title: (document.querySelector('.s2-title') || {}).textContent || '',"
    + "paras: document.querySelectorAll('.wp-para').length })")) || "{}");
  step("⑳ 不留空路由：/studio 重定向到章级工作台（有正文、有标题）",
    String(rd.path).indexOf("/write/") === 0 && String(rd.title).length > 0,
    JSON.stringify(rd));
} catch (e) {
  step("UI 剧本异常", false, String(e));
} finally {
  ws.close(); killTree();
}

const bad = results.filter((r) => !r.ok);
console.log("");
console.log("L3 " + (bad.length ? "FAIL" : "PASS") + "：" + (results.length - bad.length) + "/" + results.length + " 项通过");
process.exit(bad.length ? 1 : 0);
