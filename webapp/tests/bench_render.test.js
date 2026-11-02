/* 回归测试：对标分析二次渲染不再崩（#bench-accounts 节点被 outerHTML 销毁的 bug）
 * 复现真实触发路径：进入「对标分析」(loadBench→renderBench) → 离开 → 再次进入 → 崩溃
 */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");

// 真实后端返回结构（greenforu，已实测）
const payload = {
  kpis: { accounts: 6, videos: 892, total_plays: 102528441, avg_engagement: 0.75, with_products: 638, product_coverage: 71.5 },
  accounts: [{ account: "greenforu", lang: "en", videos: 101, total_plays: 1, avg_plays: 1, avg_engagement: 1, product_coverage: 10, top_tags: ["a"], top_hooks: ["b"] }],
  patterns: { play_buckets: [{ label: "0-1k", count: 185, pct: 20.7 }], top_tags: [{ tag: "fyp", count: 1, pct: 1 }], hook_words: ["your"], top_videos: [], products_top: [] },
  takeaways: [{ confidence: "启发式", text: "t", basis: "b" }],
};
const emptyPayload = { kpis: {}, accounts: [], patterns: {}, takeaways: [] };

function makeDom() {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  // 加载 app.js：屏蔽真实网络与 boot 副作用
  w.fetch = () => Promise.reject(new Error("network disabled in test"));
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs.replace(/\nboot\(\);/, "\n").replace(/^setInterval\(.*15000\);/mg, ""));
  return w;
}

function navigateTo(w, view) {
  const nav = w.document.querySelector('.nav-item[data-view="' + view + '"]');
  nav.dispatchEvent(new w.Event("click"));
}

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  ✓ " + name); pass++; }
  catch (e) { console.log("  ✗ " + name + " -> " + e.message); fail++; }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }

console.log("== 用例 1：连续两次渲染（复现原始崩溃路径）==");
{
  const w = makeDom();
  w.CFG = { accounts: [], agent_steps: [], step_labels: {}, local_steps: [] };
  check("第一次 renderBench 不抛异常", () => w.renderBench(payload));
  check("节点 #bench-accounts 仍存在（未被 outerHTML 销毁）",
    () => assert(w.document.querySelector("#bench-accounts") !== null));
  check("第二次 renderBench 不抛异常（原 bug 在此崩）", () => w.renderBench(payload));
  check("表体已渲染出账号行",
    () => assert(w.document.querySelector("#bench-accounts tbody").innerHTML.includes("greenforu")));
}

console.log("== 用例 2：空账号列表渲染后再渲染 ==");
{
  const w = makeDom();
  w.CFG = { accounts: [], agent_steps: [], step_labels: {}, local_steps: [] };
  check("空账号 → 不抛异常", () => w.renderBench(emptyPayload));
  check("节点保留", () => assert(w.document.querySelector("#bench-accounts") !== null));
  check("空态文案写入表格内", () => assert(w.document.querySelector("#bench-accounts").innerHTML.includes("暂无账号数据")));
  check("空态后再次有数据渲染正常", () => w.renderBench(payload));
  check("恢复后渲染出账号", () => assert(w.document.querySelector("#bench-accounts tbody").innerHTML.includes("greenforu")));
}

console.log("== 用例 3：从空账号切到有账号（下拉切换场景）==");
{
  const w = makeDom();
  w.CFG = { accounts: [], agent_steps: [], step_labels: {}, local_steps: [] };
  check("空 → 有数据 不抛异常", () => { w.renderBench(emptyPayload); w.renderBench(payload); });
  check("有数据 → 空 不抛异常", () => { w.renderBench(payload); w.renderBench(emptyPayload); });
  check("节点始终存在", () => assert(w.document.querySelector("#bench-accounts") !== null));
}

console.log("== 用例 4：防御空 payload / 缺字段 ==");
{
  const w = makeDom();
  w.CFG = { accounts: [], agent_steps: [], step_labels: {}, local_steps: [] };
  check("renderBench(null)", () => w.renderBench(null));
  check("renderBench({})", () => w.renderBench({}));
  check("patterns 缺字段不崩", () => w.renderBench({ kpis: {}, accounts: [], patterns: {}, takeaways: [] }));
}

console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
