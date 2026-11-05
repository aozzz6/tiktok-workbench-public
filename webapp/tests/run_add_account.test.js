/* 回归测试：一键运行页「快速新增对标账号」
 * 覆盖：
 *   1) 校验：空名/非法字符/重复账号 → runAddValidate false + 提示
 *   2) 合法名 → 通过
 *   3) submitRunAdd 成功：POST /api/accounts 载荷正确、/api/config 刷新、
 *      #run-account 自动选中新账号、表单清空、成功提示
 *   4) submitRunAdd 失败：服务端 400 → 提示保存失败
 * 运行：node tests/run_add_account.test.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync(path.resolve(__dirname, "..", "static", "app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "..", "static", "index.html"), "utf8");

let postCalls = [];
let configAccounts = ["gutpickde"];
let postShouldFail = false;

function ok(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }
function bad(detail) {
  return Promise.resolve({ ok: false, status: 400, json: () => Promise.resolve({ detail }) });
}

function makeDom() {
  postCalls = [];
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = (url, opts) => {
    const u = String(url);
    if (u === "/api/accounts" && opts && opts.method === "POST") {
      postCalls.push(JSON.parse(opts.body));
      if (postShouldFail) return bad("账号已存在（accounts.json）");
      configAccounts = [...configAccounts, JSON.parse(opts.body).account];
      return ok({ ok: true, account: { account: JSON.parse(opts.body).account } });
    }
    if (u.startsWith("/api/config")) return ok({ accounts: configAccounts, accounts_detail: [], local_steps: [], agent_steps: [], step_labels: {} });
    if (u.startsWith("/api/overview")) return ok({ ts: 1, accounts: [], idle_accounts: [], gate: {}, runs: [], active_runs: [], schedule: {}, diagnostics: [] });
    if (u.startsWith("/api/ledger/overview")) return ok({ summary: {}, files: [] });
    if (u.startsWith("/api/accounts")) return ok({ ts: 1, items: [], counts: {} });
    return ok({});
  };
  w.EventSource = class { constructor() {} close() {} };
  w.eval(
    appJs
      .replace(/\nboot\(\);/g, "\n")
      .replace(/^setInterval\(.*15000\);/mg, "")
  );
  return w;
}

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  ✓ " + name); pass++; }
  catch (e) { console.log("  ✗ " + name + " -> " + e.message); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m || "assertion failed"); }

(async () => {
  console.log("== 客户端校验 ==");
  const w = makeDom();
  await w.boot();          // 经真实 boot 注入闭包内 CFG（jsdom 顶层 let 陷阱绕法）
  const nameEl = w.document.querySelector("#run-add-name");

  nameEl.value = "";
  check("空名 → 校验失败", () => assert(w.runAddValidate() === false));
  check("空名 → 提示必填", () =>
    assert(w.document.querySelector("#run-add-hint").innerHTML.includes("账号名必填")));

  nameEl.value = "Bad Name!";
  check("非法字符 → 校验失败", () => assert(w.runAddValidate() === false));

  nameEl.value = "gutpickde";
  check("重复账号 → 校验失败", () => assert(w.runAddValidate() === false));

  nameEl.value = "new.acc_1";
  check("合法名 → 通过", () => assert(w.runAddValidate() === true));

  console.log("== 提交成功流 ==");
  postShouldFail = false;
  w.document.querySelector("#run-add-lang").value = "de";
  w.document.querySelector("#run-add-tags").value = "德国AI对标";
  await w.submitRunAdd();
  await new Promise((r) => setTimeout(r, 20));
  check("POST /api/accounts 载荷正确", () => {
    assert(postCalls.length === 1, "应发出 1 次 POST");
    assert(postCalls[0].account === "new.acc_1", JSON.stringify(postCalls[0]));
    assert(postCalls[0].lang === "de", "lang 应为 de");
    assert(postCalls[0].tags === "德国AI对标", "tags 应透传");
  });
  check("成功 → #run-account 自动选中新账号", () =>
    assert(w.document.querySelector("#run-account").value === "new.acc_1"));
  check("成功 → 表单清空", () =>
    assert(w.document.querySelector("#run-add-name").value === ""));
  check("成功 → 提示下一步操作", () =>
    assert(w.document.querySelector("#run-add-hint").innerHTML.includes("已创建")));

  console.log("== 提交失败流 ==");
  postShouldFail = true;
  const w2 = makeDom();
  w2.document.querySelector("#run-add-name").value = "new.acc_2";
  await w2.submitRunAdd();
  await new Promise((r) => setTimeout(r, 20));
  check("服务端 400 → 提示保存失败（含服务端原因）", () =>
    assert(w2.document.querySelector("#run-add-hint").innerHTML.includes("保存失败")));

  console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
