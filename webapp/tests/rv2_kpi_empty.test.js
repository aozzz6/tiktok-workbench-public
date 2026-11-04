/* 回归测试：提示词反推页在「0 个任务」时不得崩溃（2026-11-04 检测报告 D1）
 *
 * 现象：rcore.js 的 kpiData() 用 D0.tasks[D0.tasks.length - 1].createdDay 当「起累计」日期。
 *       全新机器上 /api/reverse/list 返回 {"items":[]}，tasks 为空数组 → first 为 undefined
 *       → TypeError: Cannot read properties of undefined (reading 'createdDay')
 *       → reverse_v2.js 的 renderAll 抛异常，整个反推页只剩壳。
 *
 * 本用例只测纯函数（不连后端、不需要真机）：
 *   1) 空任务列表：kpiData / kpis 不抛异常，KPI 上限文案退化为「暂无任务」
 *   2) 有任务时口径不变：仍然是「<createdDay> 起累计」
 *
 * 运行：cd webapp && node tests/rv2_kpi_empty.test.js
 */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const ROOT = __dirname + "/..";
const read = (p) => fs.readFileSync(ROOT + "/" + p, "utf8");

const dom = new JSDOM("<!doctype html><html><body></body></html>", { runScripts: "outside-only" });
const w = dom.window;
w.fetch = () => Promise.reject(new Error("network disabled in test"));

// 依赖顺序与 static/index.html 一致：ovlib.js（window.OV）→ rcore.js（window.RVU/U）
w.eval(read("static/rv2/ovlib.js"));
w.eval(read("static/rv2/rcore.js"));

const U = w.RVU;
let pass = 0, fail = 0;
const check = (name, fn) => {
  try { fn(); pass++; console.log("  ✓ " + name); }
  catch (e) { fail++; console.log("  ✗ " + name + " -> " + (e && e.message)); }
};
const assert = (c, m) => { if (!c) throw new Error(m || "assertion failed"); };

const empty = {
  tasks: [],
  reports: {},
  models: [],
  defaultModel: "",
  counts: { total: 0, done: 0, failed: 0, ready: 0, busy: 0 },
  avgSec: 0,
  refsTotal: 0,
  meta: { asOf: "2026-11-04 20:00" },
  diag: { maxMB: 500, maxSec: 60, proxy: "127.0.0.1:7892", note: "" },
};

console.log("== 用例 1：空任务列表（全新机器）==");
check("kpiData(空) 不抛异常，返回 6 张卡", () => {
  const k = U.kpiData(empty);
  assert(Array.isArray(k) && k.length === 6, "应返回 6 张卡，实得 " + (k && k.length));
});
check("空态上限写「暂无任务」，不再读 undefined.createdDay", () => {
  const cap = U.kpiData(empty)[0].cap;
  assert(typeof cap === "string" && cap.indexOf("暂无任务") >= 0, "实测 cap=" + JSON.stringify(cap));
});
check("kpis(空) 渲染成字符串并带 data-unit=\"kpi\"", () => {
  const h = U.kpis(empty);
  assert(typeof h === "string" && h.length > 0, "应返回 HTML 字符串");
  assert(h.indexOf('data-unit="kpi"') >= 0, "缺少 data-unit=\"kpi\"");
});

console.log("== 用例 2：有任务时口径不变 ==");
const one = JSON.parse(JSON.stringify(empty));
one.tasks = [{ id: "1", account: "a", stage: "done", shots: 2, createdDay: "10-30", title: "t" }];
one.counts = { total: 1, done: 1, failed: 0, ready: 0, busy: 0 };
check("有任务时仍写「10-30 起累计」", () => {
  const cap = U.kpiData(one)[0].cap;
  assert(cap === "10-30 起累计", "实测 cap=" + JSON.stringify(cap));
});
check("多段/单段计数仍按 tasks 计算", () => {
  const cap = U.kpiData(one)[1].cap;
  assert(cap.indexOf("多段 1") >= 0 && cap.indexOf("单段 0") >= 0, "实测 cap=" + JSON.stringify(cap));
});

console.log("\n通过 " + pass + " / " + (pass + fail));
process.exit(fail ? 1 : 0);
