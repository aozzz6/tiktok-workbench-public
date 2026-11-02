/* 前端测试：四个区块的折叠（分组 / 流水线 / 最近运行 / 自动化）（2026-10-19）
 * 覆盖：
 *   1) 四个区块都有可折叠头（data-fold-head）与折叠体（data-fold-body）
 *   2) 各自默认展开
 *   3) 逐个折叠 → 只影响自己，其余区块不变
 *   4) 折叠状态分别持久化到 localStorage（wb.fold.<key>），重载按各自状态恢复
 *   5) 键盘可操作（tabindex=0 + Enter/空格）
 *   6) 折叠不破坏数据容器（#grp-tabs / #pipe-table / #recent-runs / #auto-grid 仍在 DOM）
 * 运行：NODE_PATH=... node tests/fold_blocks.test.js
 */
const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");

let pass = 0, fail = 0;
const ok = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ " + n + (extra !== undefined ? " — " + extra : "")); }
};

const KEYS = ["groups", "pipeline", "recent", "auto"];

function makeDom(storage) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, { runScripts: "outside-only", virtualConsole: vc, url: "http://127.0.0.1:8787/" });
  const w = dom.window;
  Object.entries(storage || {}).forEach(([k, v]) => w.localStorage.setItem(k, v));
  w.fetch = async (u) => ({
    ok: true, status: 200,
    json: async () => {
      const s = String(u);
      if (s.startsWith("/api/overview")) return { accounts: [{ account: "alpha", health_label: "正常" }], idle_accounts: [], runs: [], diagnostics: [], gate: {} };
      if (s.startsWith("/api/config")) return { accounts: ["alpha"], accounts_detail: [{ account: "alpha" }], steps: [], step_labels: {}, local_steps: [], agent_steps: [], daily_steps: [] };
      if (s.startsWith("/api/groups")) return { groups: [{ id: "default", name: "默认分组", builtin: true, count: 0, members: [] }] };
      if (s.startsWith("/api/automations")) return { flows: [] };
      return {};
    },
    text: async () => "",
  });
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs);
  return { w, errors };
}

const hidden = (w, key) => {
  const b = w.document.querySelector(`[data-fold-body="${key}"]`);
  return !b || b.classList.contains("hidden");
};

(async () => {
  let { w, errors } = makeDom();
  await new Promise((r) => setTimeout(r, 30));
  const d = w.document;

  /* 1) 结构 */
  KEYS.forEach((k) => {
    const head = d.querySelector(`[data-fold-head="${k}"]`);
    ok(`区块 ${k} 有折叠头`, !!head);
    ok(`区块 ${k} 有折叠体`, !!d.querySelector(`[data-fold-body="${k}"]`));
    ok(`区块 ${k} 头可点击 + 键盘可达`,
      !!head && head.getAttribute("tabindex") === "0" &&
      new RegExp(`toggleFold\\('${k}'\\)`).test(head.getAttribute("onclick") || "") &&
      new RegExp(`toggleFold\\('${k}'\\)`).test(head.getAttribute("onkeydown") || ""));
  });
  ok("四个区块默认均展开", KEYS.every((k) => !hidden(w, k)));
  ok("boot 无异常", errors.length === 0, errors.join(" | "));

  /* 2) 逐个折叠互不影响 */
  for (const k of KEYS) {
    w.toggleFold(k);
    ok(`折叠 ${k} 生效`, hidden(w, k));
    ok(`折叠 ${k} 不影响其他区块`, KEYS.filter((x) => x !== k).every((x) => !hidden(w, x)));
    ok(`${k} 箭头旋转`, (d.querySelector(`[data-fold-head="${k}"] .fold-caret`).style.transform || "").includes("rotate"));
    ok(`${k} 显示折叠提示`, d.querySelector(`[data-fold-head="${k}"] .fold-hint`).textContent.includes("已折叠"));
    ok(`${k} 状态已持久化`, w.localStorage.getItem(`wb.fold.${k}`) === "1");
    w.toggleFold(k);
    ok(`再展开 ${k}`, !hidden(w, k));
    ok(`${k} 状态写回 0`, w.localStorage.getItem(`wb.fold.${k}`) === "0");
  }

  /* 3) 重载后按各自状态恢复（混合状态） */
  ({ w, errors } = makeDom({ "wb.fold.groups": "1", "wb.fold.auto": "1" }));
  await new Promise((r) => setTimeout(r, 30));
  ok("重载：groups 保持折叠", hidden(w, "groups"));
  ok("重载：auto 保持折叠", hidden(w, "auto"));
  ok("重载：pipeline 仍展开", !hidden(w, "pipeline"));
  ok("重载：recent 仍展开", !hidden(w, "recent"));
  ok("重载无异常", errors.length === 0, errors.join(" | "));

  /* 4) 兼容旧键 wb.pipeCollapsed */
  ({ w } = makeDom({ "wb.pipeCollapsed": "1" }));
  await new Promise((r) => setTimeout(r, 30));
  ok("旧键 wb.pipeCollapsed 仍能恢复流水线折叠", hidden(w, "pipeline"));

  /* 5) 数据容器未被破坏 */
  ["#grp-tabs", "#pipe-table", "#recent-runs", "#auto-grid"].forEach((sel) => {
    ok(`数据容器 ${sel} 仍在`, !!w.document.querySelector(sel));
  });

  console.log(`\n通过 ${pass} / ${pass + fail}`);
  process.exit(fail ? 1 : 0);
})();
