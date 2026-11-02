/* 回归测试：一键运行「人话进度面板」（parseRunLine 状态机）
 * 覆盖：
 *   1) buildRunProgress 渲染所选步骤、面板可见
 *   2) 步骤开始行 → running；结束行 → ok/fail + 人话耗时
 *   3) [进度] 行 → 更新当前运行中步骤的 note
 *   4) 脚本摘要 JSON → 翻译成人话（s1 下载/s2 抓取/s5 转写）
 *   5) HALT / DONE 全局提示
 *   6) 步骤名短前缀（s1 开始 → s1_download 结束）映射到同一 chip
 * 运行：node tests/run_progress.test.js
 */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");

function makeDom() {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = () => Promise.reject(new Error("network disabled in test"));
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs.replace(/\nboot\(\);/g, "\n").replace(/^setInterval\(.*15000\);/mg, ""));
  return w;
}

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  ✓ " + name); pass++; }
  catch (e) { console.log("  ✗ " + name + " -> " + e.message); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m || "assertion failed"); }

const START_S1 = "[14:37:14] s1 | 开始: D:/x/tk_audio_download.py --account ...";
const END_S1_OK = '[14:37:50] s1_download | ok=True cost=35.6s | 门禁:PASS | {"account": "gutpickde"}';
const END_S1_FAIL = "[14:37:50] s1_download | ok=False cost=3.0s | 门禁:FAIL | {}";
const JSON_S1 = '{"account": "gutpickde", "range": "a~b", "matched": 5, "no_timestamp": 0, "downloaded": 4, "skipped": 0, "failed": [{"id": "x"}]}';
const JSON_S2 = '{"policy": "strict", "errors": [{"id": "e1"}], "items": [{"id": "1"}, {"id": "2"}]}';
const JSON_S5 = '{"total": 5, "done": ["a", "b", "c", "d", "e"], "failed": []}';

(async () => {
  console.log("== 基础渲染与状态机 ==");
  const w = makeDom();
  w.buildRunProgress(["s1_download", "s2_scrape", "s5_transcribe"]);
  check("面板可见且渲染 3 个步骤",
    () => {
      assert(w.document.querySelector("#run-progress-panel").hidden === false);
      assert(w.document.querySelectorAll("#run-progress .rp-step").length === 3);
    });
  check("步骤显示中文名（音频下载）",
    () => assert(w.document.querySelector("#run-progress").innerHTML.includes("音频下载")));

  w.parseRunLine(START_S1);
  check("开始行 → 运行中", () => {
    assert(w.document.querySelector('[data-rp-key="s1_download"]').classList.contains("running"));
  });

  w.parseRunLine("[进度] 音频下载 2/5：完成");
  check("[进度] 行 → note 更新为人话",
    () => assert(w.document.querySelector('[data-rp-key="s1_download"]').innerHTML.includes("音频下载 2/5：完成")));

  w.parseRunLine(JSON_S1);
  check("s1 摘要 JSON → 人话（发现 5 条，新下载 4 条，失败 1 条）",
    () => assert(w.document.querySelector('[data-rp-key="s1_download"]').innerHTML.includes("发现 5 条视频，新下载 4 条") &&
                w.document.querySelector('[data-rp-key="s1_download"]').innerHTML.includes("失败 1 条")));

  w.parseRunLine(END_S1_OK);
  check("结束行 ok=True → 完成 chip",
    () => assert(w.document.querySelector('[data-rp-key="s1_download"]').classList.contains("ok") &&
                w.document.querySelector('[data-rp-key="s1_download"]').innerHTML.includes("耗时 36 秒")));

  console.log("== s2 / s5 摘要翻译 ==");
  w.parseRunLine("[14:38:00] s2 | 开始: ...");
  w.parseRunLine(JSON_S2);
  check("s2 摘要 → 抓取成功 2 条，失败/下架 1 条",
    () => assert(w.document.querySelector('[data-rp-key="s2_scrape"]').innerHTML.includes("抓取成功 2 条") &&
                w.document.querySelector('[data-rp-key="s2_scrape"]').innerHTML.includes("失败/下架 1 条")));
  w.parseRunLine("[14:39:00] s2_scrape | ok=True cost=28.1s | 门禁:PASS | {}");

  w.parseRunLine("[14:40:00] s5 | 开始: ...");
  w.parseRunLine(JSON_S5);
  check("s5 摘要 → 转写 5/5 条完成",
    () => assert(w.document.querySelector('[data-rp-key="s5_transcribe"]').innerHTML.includes("转写 5/5 条完成")));

  console.log("== 失败 / HALT / DONE ==");
  const w2 = makeDom();
  w2.buildRunProgress(["s1_download"]);
  w2.parseRunLine(START_S1);
  w2.parseRunLine(END_S1_FAIL);
  check("ok=False → 失败 chip",
    () => assert(w2.document.querySelector('[data-rp-key="s1_download"]').classList.contains("fail")));

  const w3 = makeDom();
  w3.buildRunProgress(["s1_download"]);
  w3.parseRunLine("[14:38:00] HALT | s1_download 门禁未过，链路停止");
  check("HALT → 全局提示", () =>
    assert(w3.document.querySelector("#run-progress-note").textContent.includes("流程已停止")));

  const w4 = makeDom();
  w4.buildRunProgress(["s1_download"]);
  w4.parseRunLine("[14:38:00] DONE | 全链完成");
  check("DONE → 🎉 全部完成", () =>
    assert(w4.document.querySelector("#run-progress-note").textContent.includes("全部步骤执行完毕")));

  console.log("== daily 模式 ==");
  const w5 = makeDom();
  w5.buildRunProgress(["daily"]);
  check("daily → 单 chip 显示每日增量",
    () => assert(w5.document.querySelector("#run-progress").innerHTML.includes("每日增量")));

  console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
