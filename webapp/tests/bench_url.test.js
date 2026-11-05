/* 回归测试：loadBench 生成的请求 URL 必须合法
 *
 * 已修复的 bug：`"/api/benchmark" + (acct ? "?account=..." : "") + (force ? "&refresh=true" : "")`
 * 「全部账号」+ force 时拼出 `/api/benchmark&refresh=true`（缺 `?`）→ FastAPI 404
 *   → 页面显示「对标分析加载失败：404 {"detail":"Not Found"}」
 *
 * 断言：任何 (账号, force) 组合下，查询串都必须以 `?` 开头。
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync(path.resolve(__dirname, "..", "static", "app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "..", "static", "index.html"), "utf8");

const ACCOUNTS = ["vivi_ashop", "kee.yaden", "charelttce", "mini.cases3", "greenforu", "selsky88", "lifewillcool"];

function makeDom(captured) {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = (url) => {
    captured.push(url);
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ kpis: {}, accounts: [], patterns: {}, takeaways: [] }),
    });
  };
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs.replace(/\nboot\(\);/, "\n").replace(/^setInterval\(.*15000\);/mg, ""));
  w.CFG = { accounts: ACCOUNTS, agent_steps: [], step_labels: {}, local_steps: [] };
  const sel = w.document.querySelector("#bench-account");
  sel.innerHTML = `<option value="">全部账号</option>` +
    ACCOUNTS.map((a) => `<option value="${a}">${a}</option>`).join("");
  return w;
}

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else { console.log("  ✗ " + name + (extra ? " -> " + extra : "")); fail++; }
}

/** 提取查询串（无 = ""） */
function queryOf(u) {
  const i = u.indexOf("?");
  if (i >= 0) return u.slice(i + 1);
  // 若出现 `&` 但无 `?`，说明是非法拼接
  return u.includes("&") ? "INVALID:" + u : "";
}

(async () => {
  console.log("== 用例 1：所有 (账号 × force) 组合的 URL 均合法 ==");
  const cases = [];
  for (const acct of ["", ...ACCOUNTS]) {
    for (const force of [true, false]) cases.push({ acct, force });
  }
  const captured = [];
  const w = makeDom(captured);
  const sel = w.document.querySelector("#bench-account");
  for (const c of cases) {
    sel.value = c.acct;
    await w.loadBench(c.force);
  }
  check("请求次数 = %d".replace("%d", cases.length), captured.length === cases.length,
    "got " + captured.length);

  const bad = captured.filter((u) => u.includes("&") && !u.includes("?"));
  check("没有任何请求出现 `&` 而无 `?` 的非法形态", bad.length === 0, JSON.stringify(bad));

  const wrongPath = captured.filter((u) => !u.startsWith("/api/benchmark"));
  check("所有请求路径均以 /api/benchmark 开头", wrongPath.length === 0, JSON.stringify(wrongPath));

  console.log("== 用例 2：关键组合逐一核对 ==");
  const byKey = {};
  cases.forEach((c, i) => { byKey[(c.acct || "__all__") + "|" + c.force] = captured[i]; });
  check("全部账号 + refresh → ?refresh=true",
    byKey["__all__|true"] === "/api/benchmark?refresh=true", byKey["__all__|true"]);
  check("全部账号 + 非 refresh → /api/benchmark",
    byKey["__all__|false"] === "/api/benchmark", byKey["__all__|false"]);
  check("vivi_ashop + refresh → ?account=vivi_ashop&refresh=true",
    byKey["vivi_ashop|true"] === "/api/benchmark?account=vivi_ashop&refresh=true", byKey["vivi_ashop|true"]);
  check("vivi_ashop + 非 refresh → ?account=vivi_ashop",
    byKey["vivi_ashop|false"] === "/api/benchmark?account=vivi_ashop", byKey["vivi_ashop|false"]);

  console.log("== 用例 3：特殊字符账号名被正确转义 ==");
  const w2 = makeDom([]);
  const sel2 = w2.document.querySelector("#bench-account");
  const weird = "a b&c=d";
  sel2.innerHTML += `<option value="${weird.replace(/&/g, "&amp;")}">x</option>`;
  sel2.value = weird;
  const cap2 = [];
  w2.fetch = (url) => { cap2.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({ kpis: {}, accounts: [], patterns: {}, takeaways: [] }) }); };
  await w2.loadBench(true);
  check("含 & / 空格 的账号名被转义，不破坏查询串结构",
    cap2[0] === "/api/benchmark?account=a+b%26c%3Dd&refresh=true", cap2[0]);

  console.log("== 用例 4：无 `?` 时 queryOf 不得误判 ==");
  check("queryOf('/api/benchmark') = ''", queryOf("/api/benchmark") === "");
  check("queryOf('/api/benchmark&refresh=true') 判为非法",
    queryOf("/api/benchmark&refresh=true").startsWith("INVALID"), queryOf("/api/benchmark&refresh=true"));

  console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
  process.exit(fail ? 1 : 0);
})();
