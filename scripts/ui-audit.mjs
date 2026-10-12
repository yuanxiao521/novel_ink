#!/usr/bin/env node
/**
 * 死按钮 / 空页面审计（诊断工具，非门禁）：
 *   ①路由巡检：逐条打开 --routes 里的页面，量正文字数、空状态文案、关键容器命中
 *   ②死按钮巡检：在 --url 页面上枚举可点元素（a[href] / button）→ 逐个跳转 → 看落点有没有内容
 * 用法：
 *   node scripts/ui-audit.mjs --routes=/maestro,/director,/studio,/dashboard,/characters,/settings --ls-book=<id>
 *   node scripts/ui-audit.mjs --url=/dashboard --ls-book=<id>
 * 输出：每个落点的 正文字数 / 空状态文案 / 关键容器命中数。
 */
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const arg = (k, d = "") => { const a = args.find((x) => x.startsWith("--" + k + "=")); return a ? a.slice(k.length + 3) : d; };
const WEB = arg("web", "http://127.0.0.1:5173");
const URLPATH = arg("url", "/dashboard");
const LS_BOOK = arg("ls-book", "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const findShell = async () => {
  if (process.env.HEADLESS_SHELL) return process.env.HEADLESS_SHELL;
  const roots = [process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : "", path.join(os.homedir(), "AppData", "Local", "ms-playwright")].filter(Boolean);
  for (const root of roots) {
    try {
      for (const d of await fs.readdir(root)) {
        if (!d.startsWith("chromium_headless_shell")) continue;
        const sub = path.join(root, d);
        for (const v of await fs.readdir(sub)) {
          const exe = path.join(sub, v, "chrome-headless-shell.exe");
          try { await fs.access(exe); return exe; } catch { /* 继续 */ }
        }
      }
    } catch { /* 目录不存在 */ }
  }
  return "";
};

const shell = await findShell();
if (!shell) { console.log("SKIP：未找到 chrome-headless-shell"); process.exit(0); }
const port = 9385;
const proc = spawn(shell, ["--no-sandbox", "--remote-debugging-port=" + port, "--no-first-run", "--disable-gpu", "--user-data-dir=" + path.join(os.tmpdir(), "dsh-audit"), "about:blank"], { detached: true, stdio: "ignore" });
proc.on("error", () => {}); proc.unref();
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
const goto = async (url, wait = 5000) => { await send("Page.navigate", { url }); await sleep(wait); };

await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await goto(WEB + "/", 3000);
if (LS_BOOK) await ev(`localStorage.setItem("mk.workspace", JSON.stringify({ bookId: "${LS_BOOK}", chapterId: "", sceneId: "" })); 1`);
await goto(WEB + URLPATH, 6000);

const ROUTES = arg("routes", "");
if (ROUTES) {
  const probe0 = `(() => {
    const main = document.querySelector(".main-col") || document.body;
    const txt = (main.innerText || "").replace(/\\s+/g, " ").trim();
    const empties = Array.from(main.querySelectorAll(".char-empty, .empty-state, .ws-empty")).map((e) => (e.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 36));
    const marks = [".outline-tree", ".tl-node", ".char-card", ".tension-panel", ".todo-item", ".quote-item", ".reader", ".s2-prose", ".ws-entry", ".kpi-grid"].filter((s) => main.querySelector(s));
    return JSON.stringify({ len: txt.length, empties, marks, head: txt.slice(0, 60) });
  })()`;
  console.log("== 路由巡检 ==");
  for (const route of ROUTES.split(",").map((s) => s.trim()).filter(Boolean)) {
    await goto(WEB + route, 6500);
    const p = JSON.parse((await ev(probe0)) || "{}");
    const url = await ev("location.pathname + location.search");
    const flag = p.len < 320 ? "  ← 内容偏少" : "";
    console.log(`${route}\n   落点 ${url}\n   字数 ${p.len} ｜ 容器 ${JSON.stringify(p.marks)} ｜ 空态 ${JSON.stringify(p.empties)}${flag}`);
  }
  process.exit(0);
}

const targets = await ev(`(() => {
  const out = [];
  const push = (kind, label, href) => out.push({ kind, label: (label || "").trim().slice(0, 18), href });
  document.querySelectorAll("a[href]").forEach((a) => { if (a.getAttribute("href").startsWith("/")) push("link", a.textContent, a.getAttribute("href")); });
  document.querySelectorAll("button").forEach((b) => push("button", b.textContent, ""));
  const search = document.querySelector(".header-search input");
  if (search) push("input", search.getAttribute("placeholder"), "");
  return JSON.stringify(out);
})()`);
const list = JSON.parse(targets || "[]");
console.log("页面 " + URLPATH + " 上可点元素 " + list.length + " 个：");

const probe = `(() => {
  const main = document.querySelector(".main-col") || document.body;
  const txt = (main.innerText || "").replace(/\\s+/g, " ").trim();
  const empties = Array.from(main.querySelectorAll(".char-empty, .empty-state, .ws-empty")).map((e) => (e.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 40));
  const marks = [".outline-tree", ".tl-node", ".char-card", ".tension-panel", ".todo-item", ".quote-item", ".reader", ".s2-prose", ".ws-entry"].filter((s) => main.querySelector(s));
  return JSON.stringify({ len: txt.length, head: txt.slice(0, 70), empties, marks });
})()`;

const seen = new Set();
for (const t of list) {
  if (t.kind !== "link" && t.kind !== "button") { console.log(`  [${t.kind}] 「${t.label}」 → 无跳转（未接线）`); continue; }
  if (t.kind === "button") { console.log(`  [button] 「${t.label}」 → 无 href`); continue; }
  if (seen.has(t.href)) continue;
  seen.add(t.href);
  await goto(WEB + t.href, 6000);
  const p = await ev(probe);
  const url = await ev("location.pathname + location.search");
  console.log(`  「${t.label}」 ${t.href}`);
  console.log(`     落点 ${url} ｜ ${p}`);
}
process.exit(0);
