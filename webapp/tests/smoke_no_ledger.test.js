/* 一次性冒烟：删除台账区块后，页面能正常 boot、各视图可切换、无 jsdom 报错 */
const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push(e.message));

const dom = new JSDOM(html, { runScripts: "outside-only", virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async (u) => ({
  ok: true,
  status: 200,
  json: async () => {
    const s = String(u);
    if (s.startsWith("/api/overview")) return { accounts: [], idle_accounts: [], runs: [], diagnostics: [], gate: {} };
    if (s.startsWith("/api/groups")) return { groups: [{ id: "default", name: "默认分组", builtin: true, count: 0, members: [] }] };
    if (s.startsWith("/api/config")) return { accounts: ["a"], accounts_detail: [{ account: "a" }], steps: [], step_labels: {}, local_steps: [], agent_steps: [], daily_steps: [] };
    if (s.startsWith("/api/automations")) return { flows: [] };
    if (s.startsWith("/api/benchmark")) return { products: [], patterns: {} };
    return {};
  },
  text: async () => "",
});
w.EventSource = class { constructor() {} close() {} };

let pass = 0, fail = 0;
const ok = (n, c, extra) => { c ? (pass++, console.log("✓ " + n)) : (fail++, console.log("✗ " + n + (extra ? " — " + extra : ""))); };

try {
  w.eval(appJs);   // 含 boot()
} catch (e) {
  console.log("EVAL 异常:", e.message);
}

setTimeout(async () => {
  const d = w.document;
  ok("JS 无未捕获异常", errors.length === 0, errors.join(" | "));
  const toastEl = d.querySelector("#toast");
  const toastText = toastEl ? toastEl.textContent.trim() : "";
  ok("总览加载无错误提示（无 undefined/失败 toast）",
    !/加载失败|undefined|not defined/.test(toastText), toastText);
  ok("台账表格标题已移除", !d.body.textContent.includes("台账表格"));
  ok("#ledger-grid 已移除", !d.querySelector("#ledger-grid"));
  ok("#ledger-summary 已移除", !d.querySelector("#ledger-summary"));
  ok("新增/导入按钮已移除", !d.body.textContent.includes("导入其他账号"));
  ok("迁移工具条已移除", !d.querySelector("#acct-target") && !d.querySelector("#acct-migrate"));

  // 其余区块仍在
  ok("总览：分组面板仍在", !!d.querySelector("#grp-tabs"));
  ok("总览：流水线状态仍在", !!d.querySelector("#pipe-table"));
  ok("总览：最近运行仍在", !!d.querySelector("#recent-runs"));
  ok("总览：自动化面板仍在", !!d.querySelector("#auto-grid"));
  ok("侧边栏三个导航项仍在", d.querySelectorAll(".nav-item[data-view]").length >= 3);

  // 视图切换
  const click = (view) => {
    const n = d.querySelector(`.nav-item[data-view="${view}"]`);
    n.dispatchEvent(new w.Event("click"));
    return d.querySelector(`#view-${view}`).classList.contains("active");
  };
  try {
    ok("切到「对标分析」", click("bench"));
    ok("切到「一键运行」", click("run"));
    ok("切回「总览看板」", click("overview"));
  } catch (e) {
    ok("视图切换无异常", false, e.message);
  }

  // 一键运行页的账号新增表单还在（唯一剩余的新增入口）
  ok("「一键运行」新增账号表单仍在", !!d.querySelector("#run-add-toggle"));

  // ★ 2026-10-24（D1-A）：报表与台账视图已按 A 方案下线（nav + view + 接口一起删），
  //   所以这里从「仍存在」反转为「确实不在了」——若哪天又冒出 #view-reports，
  //   说明下线被回退，这条会立刻变红。
  ok("报表台账视图已下线（D1-A）", !d.querySelector("#view-reports"));
  ok("报表导航项已下线（D1-A）", !d.querySelector('.nav-item[data-view="reports"]'));

  console.log(`\n通过 ${pass} / ${pass + fail}`);
  process.exit(fail ? 1 : 0);
}, 60);
