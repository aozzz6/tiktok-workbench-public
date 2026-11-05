/* 前端测试：流水线状态区块折叠（2026-10-19）
 * 覆盖：
 *   1) 标题可点击（foldable + 按钮语义/键盘可操作）
 *   2) 默认展开；点击折叠 → #pipe-panel 隐藏、箭头旋转、提示文字出现
 *   3) 再次点击展开 → 恢复
 *   4) 折叠状态写入 localStorage，重载后自动恢复折叠
 *   5) 折叠不影响数据渲染（#pipe-table 内容仍在）
 * 运行：NODE_PATH=... node tests/pipe_fold.test.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const HTML = path.resolve(__dirname, "..", "static", "index.html");
const APP = path.resolve(__dirname, "..", "static", "app.js");
const html = fs.readFileSync(HTML, "utf8");
const appJs = fs.readFileSync(APP, "utf8");

let pass = 0, fail = 0;
const ok = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ " + n + (extra !== undefined ? " — " + extra : "")); }
};

function makeDom(presetStorage) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, { runScripts: "outside-only", virtualConsole: vc, url: "http://127.0.0.1:8787/" });
  const w = dom.window;
  if (presetStorage) w.localStorage.setItem("wb.fold.pipeline", presetStorage);
  w.fetch = async (u) => ({
    ok: true, status: 200,
    json: async () => {
      const s = String(u);
      if (s.startsWith("/api/overview")) return { accounts: [{ account: "alpha", health_label: "正常" }], idle_accounts: [], runs: [], diagnostics: [], gate: {} };
      if (s.startsWith("/api/config")) return { accounts: ["alpha"], accounts_detail: [{ account: "alpha" }], steps: [], step_labels: {}, local_steps: [], agent_steps: [], daily_steps: [] };
      if (s.startsWith("/api/groups")) return { groups: [{ id: "default", name: "默认分组", builtin: true, count: 0, members: [] }] };
      return {};
    },
    text: async () => "",
  });
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs);            // 含 boot()
  return { w, errors };
}

(async () => {
  /* 1) 默认展开 */
  let { w, errors } = makeDom();
  await new Promise((r) => setTimeout(r, 30));
  const d = w.document;
  const head = d.querySelector("#pipe-fold");
  const panel = d.querySelector("#pipe-panel");
  ok("标题存在且标记为可折叠", !!(head && head.classList.contains("foldable")));
  ok("标题绑定点击事件（onclick=toggleFold('pipeline')）", /toggleFold\('pipeline'\)/.test(head.getAttribute("onclick") || ""));
  ok("键盘可操作（tabindex=0 + Enter/空格触发）",
    head.getAttribute("tabindex") === "0" && /toggleFold\('pipeline'\)/.test(head.getAttribute("onkeydown") || ""));
  ok("默认展开（面板可见）", !panel.classList.contains("hidden"));
  ok("默认箭头向下", (d.querySelector("#pipe-caret").style.transform || "") === "");
  ok("boot 无异常", errors.length === 0, errors.join(" | "));

  /* 2) 触发折叠（jsdom 不执行 inline onclick，直接调用函数） */
  w.togglePipeFold();
  ok("点击后折叠（面板隐藏）", panel.classList.contains("hidden"));
  ok("折叠后箭头旋转", d.querySelector("#pipe-caret").style.transform.includes("rotate"));
  ok("折叠后出现提示文字", d.querySelector("#pipe-fold-hint").textContent.includes("已折叠"));
  ok("折叠状态写入 localStorage", w.localStorage.getItem("wb.fold.pipeline") === "1");

  /* 3) 再点展开 */
  w.togglePipeFold();
  ok("再次点击展开", !panel.classList.contains("hidden"));
  ok("展开后清除提示", d.querySelector("#pipe-fold-hint").textContent === "");
  ok("展开后状态写回 0", w.localStorage.getItem("wb.fold.pipeline") === "0");

  /* 4) 折叠不影响数据渲染 */
  ok("折叠不改动数据容器", !!d.querySelector("#pipe-table"));

  /* 5) 重载后恢复折叠 */
  ({ w, errors } = makeDom("1"));
  await new Promise((r) => setTimeout(r, 30));
  const d2 = w.document;
  ok("重载后自动保持折叠", d2.querySelector("#pipe-panel").classList.contains("hidden"));
  ok("重载后箭头处于旋转态", d2.querySelector("#pipe-caret").style.transform.includes("rotate"));
  ok("重载无异常", errors.length === 0, errors.join(" | "));

  console.log(`\n通过 ${pass} / ${pass + fail}`);
  process.exit(fail ? 1 : 0);
})();
