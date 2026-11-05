/* 回归测试：失败分析卡（人话版）—— parseRunLine 留存 + 规则匹配 + 渲染
 * 运行：node tests/fail_analysis.test.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync(path.resolve(__dirname, "..", "static", "app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "..", "static", "index.html"), "utf8");

function makeDom() {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = () => Promise.reject(new Error("network disabled"));
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs.replace(/\nboot\(\);/g, "\n").replace(/^setInterval\(.*15000\);/mg, ""));
  return w;
}

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  ✓ " + name); pass++; }
  catch (e) { console.log("  ✗ " + name + " -> " + e.message); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m || "assert failed"); }

const w = makeDom();
w.buildRunProgress(["s1_download", "s2_scrape", "s5_transcribe"]);

console.log("== 风控失败 → 人话分析卡（无需联系技术） ==");
w.parseRunLine("[15:18:40] s1_download | ok=False cost=25.8s | 门禁:FAIL | RuntimeError: 主页无内嵌数据（可能被风控，检查代理）");
check("失败 chip 触发分析卡", () => {
  const fa = w.document.querySelector("#fail-analysis");
  assert(fa && !fa.classList.contains("hidden"), "分析卡未显示");
});
check("标题 = 出了什么问题？怎么办？", () => {
  assert(w.document.querySelector("#fail-analysis").innerHTML.includes("出了什么问题"));
});
check("原因为人话（TikTok 限制/频繁）", () => {
  assert(w.document.querySelector("#fail-analysis").innerHTML.includes("访问太频繁"));
});
check("解决步骤编号化（3 步）", () => {
  assert(w.document.querySelectorAll(".fa-step").length === 3);
});
check("明确说不需要联系技术人员", () => {
  assert(w.document.querySelector(".fa-contact").innerHTML.includes("不需要联系技术人员"));
});

console.log("== 程序内部错误 → 建议联系技术 ==");
const w2 = makeDom();
w2.buildRunProgress(["s1_download"]);
w2.parseRunLine("[15:18:40] s1_download | ok=False cost=1.0s | exit=1 | NameError: name 'x' is not defined");
check("NameError → 提示联系技术人员", () => {
  assert(w2.document.querySelector(".fa-contact").innerHTML.includes("联系技术人员"));
});

console.log("== 台账占用 → 关 Excel ==");
const w3 = makeDom();
w3.buildRunProgress(["s7_backfill"]);
w3.parseRunLine("[10:00:00] s7_backfill | ok=False cost=1.0s | 门禁:FAIL | 台账被占用（~$ 锁文件存在）");
check("台账占用 → 关闭 Excel 步骤", () => {
  assert(w3.document.querySelector("#fail-analysis").innerHTML.includes("Excel"));
});

console.log("== 未知失败 → 兜底文案 ==");
const w4 = makeDom();
w4.buildRunProgress(["s1_download"]);
w4.parseRunLine("[10:00:00] s1_download | ok=False cost=1.0s | 门禁:FAIL | 未知神秘错误");
check("未知原因 → 兜底（重试 + 联系）", () => {
  const html4 = w4.document.querySelector("#fail-analysis").innerHTML;
  assert(html4.includes("重试") && html4.includes("联系技术人员"));
});

console.log("== 新运行清空分析卡 ==");
const w5 = makeDom();
w5.buildRunProgress(["s1_download"]);
w5.parseRunLine("[10:00:00] s1_download | ok=False cost=1.0s | 门禁:FAIL | NameError: x");
w5.buildRunProgress(["s1_download"]);
check("新运行 → 分析卡隐藏", () => {
  assert(w5.document.querySelector("#fail-analysis").classList.contains("hidden"));
});

console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
