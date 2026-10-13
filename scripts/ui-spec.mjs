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
    step("⑨ 顶栏体检按钮：自动切责编 tab + 过程卡 + 结果富卡（明细可见，且**非回退**）",
      !rv.err && rv.editorTab === true && rv.card === true && /体检：\d+ 条问题/.test(String(rv.detail))
        && !String(rv.overall).includes("模型未接入")
        && (rv.subs || []).some((s) => String(s).includes("问题清单")),
      JSON.stringify(rv));
  }

  // ⑩ 写手流式（v1.44）：点顶栏「写手 · 初稿」→ 正文区**边收边写**（采样到中间态）+ 头部「未保存」
  if (llmDown) {
    skip("⑩ 写手流式（正文区边收边写）", "LLM 不可用：" + llmWhy);
  } else {
    const dr = JSON.parse((await ev("(async () => {"
      + "const find = () => Array.from(document.querySelectorAll('.s2-actions button')).find((x) => x.textContent.includes('写手'));"
      + "let b = find();"
      + "if (!b) return JSON.stringify({ err: 'no 写手 button' });"
      + "const readLen = () => ((document.querySelector('.s2-prose') || {}).innerText || '').replace(/\\s/g, '').length;"
      + "const initial = readLen();"
      + "/* 等按钮可用（上一次工具跑完才解禁；如实记录点击时的禁用态，不猜） */"
      + "const tWait = Date.now();"
      + "while (b.disabled && Date.now() - tWait < 8000) { await new Promise((r) => setTimeout(r, 200)); b = find(); }"
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
    step("⑩ 写手流式：正文区边收边写 +「未保存」标记 + 保存按钮转「保存*」",
      !dr.err && dr.disabledAtClick === false && dr.final > 50 && dr.final !== dr.initial
        && (dr.mid >= 1 || dr.sawBusyLabel === true)
        && dr.dirty === true && String(dr.saveLabel).includes("保存*"),
      JSON.stringify(dr));
  }
} catch (e) {
  step("UI 剧本异常", false, String(e));
} finally {
  ws.close(); killTree();
}

const bad = results.filter((r) => !r.ok);
console.log("");
console.log("L3 " + (bad.length ? "FAIL" : "PASS") + "：" + (results.length - bad.length) + "/" + results.length + " 项通过");
process.exit(bad.length ? 1 : 0);
