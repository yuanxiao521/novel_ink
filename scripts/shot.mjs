#!/usr/bin/env node
/**
 * 截图助手（可复跑）：用本机 chrome-headless-shell 打开某个前端路由并截图，给 UI 改动留证据。
 * 与 L3（ui-spec.mjs）共用同一套 CDP 探测/连接逻辑，但不做断言——只出图。
 * 用法：
 *   node scripts/shot.mjs --url=/maestro --ls-book=<bookId> --out=docs/mockups/p3/ink/xx.png
 *   可选：--wait=6000（导航后等待毫秒）· --js='<表达式>'（截图前执行，可点按钮/改 DOM）· --w=1440 --h=900
 *   可选：--theme=ink|paper（设 <html data-theme>，P3-2 纸墨双模式基线靠它复跑）
 *   可选：--shell=<chrome-headless-shell 路径>（或环境变量 HEADLESS_SHELL）
 */
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const arg = (k, d = "") => { const a = args.find((x) => x.startsWith("--" + k + "=")); return a ? a.slice(k.length + 3) : d; };
const WEB = arg("web", "http://127.0.0.1:5173");
const URLPATH = arg("url", "/");
const OUT = arg("out", "docs/mockups/p3/ink/shot.png");
const WAIT = Number(arg("wait", "6000"));
const JS = arg("js", "");
const LS_BOOK = arg("ls-book", "");
const THEME = arg("theme", "");
const W = Number(arg("w", "1440"));
const H = Number(arg("h", "900"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const findShell = async () => {
  if (process.env.HEADLESS_SHELL) return process.env.HEADLESS_SHELL;
  const explicit = arg("shell", "");
  if (explicit) return explicit;
  const roots = [
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : "",
    path.join(os.homedir(), "AppData", "Local", "ms-playwright"),
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

const port = 9381;
const proc = spawn(shell, ["--no-sandbox", "--remote-debugging-port=" + port, "--no-first-run", "--disable-gpu",
  "--user-data-dir=" + path.join(os.tmpdir(), "dsh-shot"), "about:blank"], { detached: true, stdio: "ignore" });
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

await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: WEB + "/" });
await sleep(3500);
if (LS_BOOK) await ev(`localStorage.setItem("mk.workspace", JSON.stringify({ bookId: "${LS_BOOK}", chapterId: "", sceneId: "" })); 1`);
await send("Page.navigate", { url: WEB + URLPATH });
await sleep(WAIT);
// 主题必须在**目标页加载后**再设：ThemeProvider 挂载时会把 data-theme 写回初始值，
// 导航前设置会被整页加载重置（实测纸模式截出来还是墨模式）。
if (THEME) await ev(`document.documentElement.dataset.theme = "${THEME}"; 1`);
if (THEME) await sleep(600);   // 等一帧让 CSS 变量生效
if (JS) { console.log("js →", await ev(JS)); await sleep(WAIT); }
const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
const b64 = shot.result?.data || "";
if (!b64) { console.log("FAIL：截图失败（无 data）"); process.exit(1); }
const abs = path.resolve(OUT);
await fs.mkdir(path.dirname(abs), { recursive: true });
await fs.writeFile(abs, Buffer.from(b64, "base64"));
console.log("SHOT " + abs + "  " + (Buffer.from(b64, "base64").length / 1024).toFixed(1) + " KB");
process.exit(0);
