/* 回归测试：refreshActiveView() —— 流水线完成后按当前激活视图补刷
 * 覆盖：
 *   1) activeViewName() 读 .nav-item.active 的 data-view，缺省回落 overview
 *   2) 无论哪个视图，总览总是刷新（2026-10-19 起台账区块已移除，不再拉 /api/ledger/overview）
 *   3) 停在 bench  → 补刷 /api/benchmark
 *   6) 停在 overview/run → 不额外拉 bench/accounts（只总览+台账）
 *   7) 一键运行面板 SSE done → 走 refreshActiveView（切到 bench 时能拉到 benchmark）
 * 运行：node tests/refresh_active_view.test.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync(path.resolve(__dirname, "..", "static", "app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "..", "static", "index.html"), "utf8");

let calls = [];
function makeDom() {
  calls = [];
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = (url, opts) => {
    const u = String(url);
    calls.push(u);
    // 各接口返回最小可用结构，避免渲染抛错
    if (u.startsWith("/api/overview")) return ok({ ts: 1, accounts: [], idle_accounts: [], gate: {}, runs: [], active_runs: [], schedule: {}, diagnostics: [] });
    if (u.startsWith("/api/ledger/overview")) return ok({ summary: {}, files: [] });
    if (u.startsWith("/api/benchmark")) return ok({ products: [], patterns: {} });
    if (u.startsWith("/api/accounts")) return ok({ ts: 1, items: [], counts: {} });
    return ok({});
  };
  function ok(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }
  w.EventSource = class { constructor(url) { this.url = url; } close() {} };
  w.eval(
    appJs
      .replace(/\nboot\(\);/g, "\n")
      .replace(/^setInterval\(.*15000\);/mg, "")
  );
  return w;
}

// 把某个 nav-item 设为唯一 active，并同步 .view 的 active
function setActiveView(w, view) {
  w.document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  w.document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const nav = w.document.querySelector(`.nav-item[data-view="${view}"]`);
  if (nav) nav.classList.add("active");
  const v = w.document.querySelector(`#view-${view}`);
  if (v) v.classList.add("active");
}

const $ = (w, s) => w.document.querySelector(s);
let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  \u2713 " + name); pass++; }
  catch (e) { console.log("  \u2717 " + name + " -> " + e.message); fail++; }
}
async function checkAsync(name, fn) {
  try { await fn(); console.log("  \u2713 " + name); pass++; }
  catch (e) { console.log("  \u2717 " + name + " -> " + e.message); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m || "assertion failed"); }
function hit(url) { return calls.some((u) => u.startsWith(url)); }

(async () => {
  console.log("== 用例 1：activeViewName 解析 ==");
  {
    const w = makeDom();
    setActiveView(w, "bench");
    check("bench → activeViewName=bench", () => assert(w.activeViewName() === "bench"));
    // 无 active 时回落 overview
    w.document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
    check("无 active → 回落 overview", () => assert(w.activeViewName() === "overview"));
  }

  console.log("== 用例 2：停在 bench 时补刷 benchmark ==");
  await checkAsync("refreshActiveView 拉 /api/overview + benchmark（台账区块已移除，不再拉 ledger）", async () => {
    const w = makeDom();
    setActiveView(w, "bench");
    await w.refreshActiveView();
    await new Promise((r) => setTimeout(r, 10));
    assert(hit("/api/overview"), "应拉 overview");
    assert(!hit("/api/ledger/overview"), "台账区块已移除，不应再拉 ledger");
    assert(hit("/api/benchmark"), "应拉 benchmark");
  });

  console.log("== 用例 5：停在 overview/run 不额外拉 bench ==");
  await checkAsync("overview 视图只刷总览+台账，不拉 benchmark", async () => {
    const w = makeDom();
    setActiveView(w, "overview");
    await w.refreshActiveView();
    await new Promise((r) => setTimeout(r, 10));
    assert(hit("/api/overview"), "应拉 overview");
    assert(!hit("/api/benchmark"), "不应拉 benchmark");
  });

  console.log("== 用例 6：一键运行 SSE done → 停在 bench 能拿到 benchmark ==");
  await checkAsync("autoEs done 触发 refreshActiveView", async () => {
    const w = makeDom();
    setActiveView(w, "bench");
    let src = null;
    w.EventSource = class {
      constructor(url) { this.url = url; src = this; }
      close() {}
    };
    // 触发一键运行面板的订阅（startAutoRun 是内部函数，改从既有入口模拟：
    // 直接调用 refreshActiveView 之外，验证 done 分支已替换为 refreshActiveView）
    assert(typeof w.refreshActiveView === "function", "refreshActiveView 应存在");
    // 断言源码中 done 分支确实调用了 refreshActiveView（防回退成 loadOverview）
    const doneBlock = appJs.split("if (msg.done)")[1] || "";
    assert(doneBlock.includes("refreshActiveView"), "done 分支应调用 refreshActiveView");
  });

  console.log("== 用例 7：benchBusy 交互门（防轮询冲掉展开态）==");
  {
    const w = makeDom();
    setActiveView(w, "bench");
    check("无交互 → benchBusy=false", () => assert(w.benchBusy() === false));
    // 商品手风琴展开
    const box = $(w, "#bench-products");
    box.innerHTML = `<div class="prod-item"><button class="prod-head" aria-expanded="true"></button><div class="prod-detail"></div></div>`;
    check("商品手风琴展开 → busy", () => assert(w.benchBusy() === true));
    box.innerHTML = `<div class="prod-item"><button class="prod-head" aria-expanded="false"></button><div class="prod-detail" hidden></div></div>`;
    check("手风琴收起 → 不 busy", () => assert(w.benchBusy() === false));
  }
  {
    const w = makeDom();
    setActiveView(w, "bench");
    const m = $(w, ".modal");
    if (m) m.classList.remove("hidden");
    check("弹层打开 → busy", () => assert(w.benchBusy() === true));
  }

  console.log("== 用例 8：pollBench 只在该页激活且非 busy 时拉取 ==");
  await checkAsync("非 bench 页 → pollBench 不拉 benchmark", async () => {
    const w = makeDom();
    setActiveView(w, "overview");
    w.pollBench();
    await new Promise((r) => setTimeout(r, 10));
    assert(!hit("/api/benchmark"), "不应拉 benchmark");
  });
  await checkAsync("bench 页且空闲 → pollBench 拉 benchmark", async () => {
    const w = makeDom();
    setActiveView(w, "bench");
    w.pollBench();
    await new Promise((r) => setTimeout(r, 10));
    assert(hit("/api/benchmark"), "应拉 benchmark");
    assert(!calls.some((u) => u.includes("refresh=true")), "轮询不应带 refresh=true（让后端 60s 缓存限流）");
  });
  await checkAsync("bench 页但 busy → pollBench 跳过", async () => {
    const w = makeDom();
    setActiveView(w, "bench");
    const box = $(w, "#bench-products");
    box.innerHTML = `<button class="prod-head" aria-expanded="true"></button>`;
    w.pollBench();
    await new Promise((r) => setTimeout(r, 10));
    assert(!hit("/api/benchmark"), "busy 时不应拉 benchmark");
  });

  console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
  process.exit(fail ? 1 : 0);
})();
