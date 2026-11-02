/* 回归测试：「被挂车最多的商品」列表项可点击交互
 * 覆盖：展开/收起、手风琴互斥、FastMoss 跳转链接、缺 product_id 降级、
 *       标题转义、二次渲染后事件仍生效、空态。
 * 运行： NODE_PATH=... node tests/bench_products.test.js
 */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");

// 真实后端结构（fields 与 benchmark.py products_top 对齐）
const PRODUCTS = [
  {
    title: "PUTARE Wireless Induction Audio Speaker Stand, 5in1 Cellphone Holder",
    product_id: "1731580743505383759",
    anchor_type: 35,
    fastmoss_url: "https://www.fastmoss.com/e-commerce/detail/1731580743505383759",
    vids: 85,
    pct: 8.9,
    total_plays: 12034567,
    accounts: ["charelttce", "kee.yaden"],
    videos: [
      { account: "charelttce", vid: "7692064138700328222", title: "best phone stand ever", plays: 900000, url: "https://www.tiktok.com/@charelttce/video/7692064138700328222", publish_time: "2026-10-03 12:34:56" },
      { account: "kee.yaden", vid: "7690000000000000001", title: "desk setup", plays: 300000, url: "", publish_time: "" },
    ],
  },
  {
    title: "PUTARE Remote Control for TikTok Scrolling with Smartphone Holder</span><script>alert(1)</script>",
    product_id: "",
    anchor_type: null,
    fastmoss_url: "",
    vids: 52,
    pct: 5.5,
    total_plays: 8000000,
    accounts: ["charelttce"],
    videos: [],
  },
];

function makeDom() {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const w = dom.window;
  w.fetch = () => Promise.reject(new Error("network disabled in test"));
  w.EventSource = class { constructor() {} close() {} };
  w.eval(appJs.replace(/\nboot\(\);/, "\n").replace(/^setInterval\(.*15000\);/mg, ""));
  return w;
}

let pass = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log("  ✓ " + name); pass++; }
  catch (e) { console.log("  ✗ " + name + " -> " + e.message); fail++; }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function click(w, el) { el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true })); }

/* 造一个可观测 window.open / fastmossFallback 的环境：
   openImpl 返回 falsy 即模拟「弹窗被拦截」。 */
function makeDomWithOpen(openImpl) {
  const w = makeDom();
  const opened = [], fallback = [];
  w.open = function () {
    const args = Array.prototype.slice.call(arguments);
    opened.push(args);
    return openImpl ? openImpl.apply(null, args) : { opener: "keep" };
  };
  w.fastmossFallback = function (u) { fallback.push(u); };
  return { w: w, opened: opened, fallback: fallback };
}

console.log("== 用例 1：渲染为可点击按钮 ==");
{
  const w = makeDom();
  w.renderBench({ kpis: {}, accounts: [], patterns: { products_top: PRODUCTS }, takeaways: [] });

  const box = w.document.querySelector("#bench-products");
  const heads = box.querySelectorAll(".prod-head");
  check("渲染出 2 个 prod-head 按钮", () => assert(heads.length === 2, "got " + heads.length));
  check("首条是 <button>（可聚焦/键盘可用）", () => assert(heads[0].tagName === "BUTTON"));
  check("初始 aria-expanded=false", () => assert(heads[0].getAttribute("aria-expanded") === "false"));
  check("显示挂车数与占比", () => assert(heads[0].textContent.includes("85 条") && heads[0].textContent.includes("8.9%")));
  check("详情块初始隐藏", () => assert(box.querySelectorAll(".prod-detail")[0].hasAttribute("hidden")));
  check("初始未填充详情内容（懒渲染）", () => assert(box.querySelectorAll(".prod-detail")[0].innerHTML === ""));
}

console.log("== 用例 2：点击展开详情 ==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  const head = box.querySelectorAll(".prod-head")[0];
  click(w, head);

  const detail = box.querySelectorAll(".prod-detail")[0];
  check("aria-expanded=true", () => assert(head.getAttribute("aria-expanded") === "true"));
  check("详情可见（hidden 移除）", () => assert(!detail.hasAttribute("hidden")));
  check("条目加 .open 类", () => assert(head.closest(".prod-item").classList.contains("open")));
  check("展示商品ID", () => assert(detail.innerHTML.includes("1731580743505383759")));
  check("展示关联账号", () => assert(detail.innerHTML.includes("charelttce") && detail.innerHTML.includes("kee.yaden")));
  check("展示关联播放合计", () => assert(detail.innerHTML.includes("12,034,567") || detail.innerHTML.includes("1203"), detail.innerHTML.slice(0, 120)));
  check("展示锚点类型", () => assert(detail.innerHTML.includes("35")));
  check("展示关联视频明细", () => assert(detail.innerHTML.includes("best phone stand ever")));
  check("含 TikTok 原视频外链", () => assert(detail.innerHTML.includes("https://www.tiktok.com/@charelttce/video/7692064138700328222")));
}

console.log("== 用例 3：FastMoss 跳转链接 ==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  const a = box.querySelectorAll(".prod-detail")[0].querySelector("a.btn.primary");
  check("存在「在 FastMoss 打开商品页」按钮", () => assert(a !== null));
  check("href 指向 FastMoss 商品页模板",
    () => assert(a.getAttribute("href") === "https://www.fastmoss.com/e-commerce/detail/1731580743505383759", a && a.getAttribute("href")));
  check("新窗口打开且 noopener", () => assert(a.getAttribute("target") === "_blank" && (a.getAttribute("rel") || "").includes("noopener")));
}

console.log("== 用例 4：再点收起 ==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  const head = box.querySelectorAll(".prod-head")[0];
  click(w, head); click(w, head);
  check("aria-expanded 回到 false", () => assert(head.getAttribute("aria-expanded") === "false"));
  check("详情再次隐藏", () => assert(box.querySelectorAll(".prod-detail")[0].hasAttribute("hidden")));
}

console.log("== 用例 5：手风琴互斥 ==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  const heads = box.querySelectorAll(".prod-head");
  click(w, heads[0]);
  click(w, heads[1]);
  check("第 1 条已自动收起", () => assert(heads[0].getAttribute("aria-expanded") === "false"));
  check("第 1 条详情隐藏", () => assert(box.querySelectorAll(".prod-detail")[0].hasAttribute("hidden")));
  check("第 2 条展开", () => assert(heads[1].getAttribute("aria-expanded") === "true"));
}

console.log("== 用例 6：缺 product_id 降级 + XSS 转义 ==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[1]);
  const d2 = box.querySelectorAll(".prod-detail")[1];
  check("无 FastMoss 链接", () => assert(d2.querySelector("a.btn.primary") === null));
  check("给出降级说明", () => assert(d2.innerHTML.includes("无法生成 FastMoss 链接")));
  check("无复制按钮", () => assert(d2.querySelector("[data-copy]") === null));
  check("标题中的 <script> 被转义（未注入真实脚本节点）",
    () => assert(d2.querySelector("script") === null));
  check("标题转义为实体", () => assert(d2.innerHTML.includes("&lt;script&gt;")));
}

console.log("== 用例 7：二次渲染后交互仍生效（事件委托只绑一次）==");
{
  const w = makeDom();
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const head = box.querySelectorAll(".prod-head")[0];
  click(w, head);
  check("重渲染后点击仍能展开", () => assert(head.getAttribute("aria-expanded") === "true"));
  // 若委托被重复绑定，会出现「展开又被收起」→ 断言仍处于展开态
  check("未出现重复绑定导致的秒收", () => assert(!box.querySelectorAll(".prod-detail")[0].hasAttribute("hidden")));
}

console.log("== 用例 8：空态与防御 ==");
{
  const w = makeDom();
  check("products_top 缺失不抛异常", () => w.renderBench({ patterns: {}, kpis: {}, accounts: [] }));
  check("空态文案", () => assert(w.document.querySelector("#bench-products").innerHTML.includes("暂无商品数据")));
  check("非数组入参不抛异常", () => w.renderBench({ patterns: { products_top: "oops" }, kpis: {}, accounts: [] }));
  check("缺 title 时用商品ID兜底", () => {
    w.renderBench({ patterns: { products_top: [{ product_id: "999", vids: 1, pct: 1 }] }, kpis: {}, accounts: [] });
    assert(w.document.querySelector("#bench-products").textContent.includes("999"));
  });
}

console.log("== 用例 9：行尾 FastMoss 一键跳转（点一下即跳，不必先展开）==");
{
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  const jumps = box.querySelectorAll(".prod-jump");

  check("每行都有行尾跳转位", () => assert(jumps.length === 2, "got " + jumps.length));
  check("有 product_id 的行渲染为 <a data-fm>", () => assert(jumps[0].tagName === "A" && jumps[0].getAttribute("data-fm") === PRODUCTS[0].fastmoss_url));
  check("href 同时写好（无 JS 也能用）", () => assert(jumps[0].getAttribute("href") === PRODUCTS[0].fastmoss_url));
  check("无 product_id 的行渲染为不可点提示", () => assert(jumps[1].classList.contains("disabled") && !jumps[1].hasAttribute("data-fm")));

  click(w, jumps[0]);
  check("点击调用了 window.open(url, _blank)", () => assert(env.opened.length === 1 && env.opened[0][0] === PRODUCTS[0].fastmoss_url && env.opened[0][1] === "_blank", JSON.stringify(env.opened)));
  check("跳转点击不会顺带展开详情", () => assert(box.querySelectorAll(".prod-head")[0].getAttribute("aria-expanded") === "false"));
  check("未走兜底（新窗口成功）", () => assert(env.fallback.length === 0));
}

console.log("== 用例 10：新窗口被拦截 → 退回当前窗口导航 ==");
{
  const env = makeDomWithOpen(() => null); // 模拟弹窗拦截
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-jump")[0]);
  check("调用了 fastmossFallback（不会静默失败）",
    () => assert(env.fallback.length === 1 && env.fallback[0] === PRODUCTS[0].fastmoss_url, JSON.stringify(env.fallback)));
  check("给出被拦截提示", () => assert(w.document.querySelector("#toast").textContent.includes("拦截")));
}

console.log("== 用例 11：保留浏览器原生点击语义 ==");
{
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const a = w.document.querySelector("#bench-products .prod-jump");

  const ctrl = new w.MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
  a.dispatchEvent(ctrl);
  check("Ctrl+点击 不拦截（交给浏览器后台开标签）", () => assert(!ctrl.defaultPrevented && env.opened.length === 0));

  const mid = new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 1 });
  a.dispatchEvent(mid);
  check("中键点击 不拦截", () => assert(!mid.defaultPrevented && env.opened.length === 0));

  const plain = new w.MouseEvent("click", { bubbles: true, cancelable: true });
  a.dispatchEvent(plain);
  check("普通左键 才走 JS 接管", () => assert(plain.defaultPrevented && env.opened.length === 1));
}

console.log("== 用例 12：详情面板内的按钮走同一路径 ==");
{
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  const btn = box.querySelectorAll(".prod-detail")[0].querySelector("a.btn.primary");
  check("详情按钮也带 data-fm", () => assert(btn && btn.getAttribute("data-fm") === PRODUCTS[0].fastmoss_url));
  click(w, btn);
  check("点击触发 window.open", () => assert(env.opened.length === 1 && env.opened[0][0] === PRODUCTS[0].fastmoss_url));
}

console.log("== 用例 13：复制商品ID ==");
{
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  const cp = box.querySelectorAll(".prod-detail")[0].querySelector("[data-copy]");
  check("复制按钮存在", () => assert(cp !== null));
  const copied = [];
  // 同步 thenable：让 copyText 的 .then 回调立即执行，便于同步断言 toast
  w.navigator.clipboard = { writeText: (t) => { copied.push(t); return { then: (fn) => { fn(); return { then: () => {} }; } }; } };
  click(w, cp);
  check("复制的是 product_id", () => assert(copied.length === 1 && copied[0] === "1731580743505383759", JSON.stringify(copied)));
  check("复制不会触发跳转", () => assert(env.opened.length === 0));
}

console.log("== 用例 14：详情「关联视频」标题可点击 → 打开 TikTok 原视频 ==");
{
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  const detail = box.querySelectorAll(".prod-detail")[0];
  const lis = detail.querySelectorAll(".pd-videos li");

  check("渲染出 2 条关联视频", () => assert(lis.length === 2, "got " + lis.length));
  const t0 = lis[0].querySelector(".pvd-t");
  check("有 URL 的标题渲染为 <a.pvd-t data-open>", () => assert(t0.tagName === "A" && t0.getAttribute("data-open") === PRODUCTS[0].videos[0].url));
  check("同时写好 href（无 JS 可用）", () => assert(t0.getAttribute("href") === PRODUCTS[0].videos[0].url));
  check("行加 .clickable", () => assert(lis[0].classList.contains("clickable")));
  check("展示发布时间（用于区分同款话术视频）", () => assert(lis[0].textContent.includes("2026-10-03 12:34:56"), lis[0].textContent));

  const t1 = lis[1].querySelector(".pvd-t");
  check("无 URL 的标题退化为 <span>（不可点）", () => assert(t1.tagName === "SPAN" && !lis[1].classList.contains("clickable")));
  check("无视频链接 → 不出 TikTok 链（出单量按钮仍靠 vid 渲染）", () => {
    assert(lis[1].querySelector(".pvd-a") === null, "不应有 TikTok 链接");
    assert(lis[1].querySelector(".pvd-sales") !== null, "vid 在 → 出单量按钮应渲染");
  });
  check("无 publish_time 时不显示空段", () => assert(!lis[1].textContent.includes("·  ·"), lis[1].textContent));

  click(w, t0);
  check("点标题调用 window.open 打开原视频",
    () => assert(env.opened.length === 1 && env.opened[0][0] === PRODUCTS[0].videos[0].url, JSON.stringify(env.opened)));
  check("点标题不会收起手风琴", () => assert(box.querySelectorAll(".prod-head")[0].getAttribute("aria-expanded") === "true"));

  const small = lis[0].querySelector(".pvd-a");
  check("TikTok 小链接也带 data-open", () => assert(small && small.getAttribute("data-open") === PRODUCTS[0].videos[0].url));
  click(w, small);
  check("点小链接再开一次", () => assert(env.opened.length === 2));
}

console.log("== 用例 15：视频标题跳转同样享受弹窗拦截兜底 ==");
{
  const env = makeDomWithOpen(() => null);
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  click(w, box.querySelectorAll(".prod-detail")[0].querySelector(".pvd-t"));
  check("走 fastmossFallback（不会静默失败）",
    () => assert(env.fallback.length === 1 && env.fallback[0] === PRODUCTS[0].videos[0].url, JSON.stringify(env.fallback)));
}

console.log("== 用例 16：「视频出单量」按钮 → 直达 FastMoss 视频页 ==");
{
  const FMV = "https://www.fastmoss.com/media-source/video/7692064138700328222";
  const env = makeDomWithOpen();
  const w = env.w;
  w.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  const lis = box.querySelectorAll(".pd-videos li");
  const btn = lis[0].querySelector(".pvd-sales");

  check("按钮命名为「视频出单量」", () => assert(btn && btn.textContent.trim() === "视频出单量", btn && btn.textContent));
  check("type=button（不触发表单语义）", () => assert(btn.getAttribute("type") === "button"));
  check("data-open 指向该视频的 FastMoss 页（vid 拼模板）",
    () => assert(btn.getAttribute("data-open") === FMV, btn.getAttribute("data-open")));

  click(w, btn);
  check("点击直达该视频的 FastMoss 页",
    () => assert(env.opened.length === 1 && env.opened[0][0] === FMV, JSON.stringify(env.opened)));
  check("点击不会收起手风琴", () => assert(box.querySelectorAll(".prod-head")[0].getAttribute("aria-expanded") === "true"));

  const ctrl = new w.MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
  btn.dispatchEvent(ctrl);
  check("Ctrl+点击不接管", () => assert(!ctrl.defaultPrevented && env.opened.length === 1));

  check("不再重复渲染「FastMoss 视频页」文本链（合并进按钮）",
    () => assert(lis[0].querySelectorAll(".pvd-a").length === 1));
  check("TikTok 原视频链接仍在",
    () => assert(lis[0].querySelector(".pvd-a").getAttribute("data-open") === PRODUCTS[0].videos[0].url));
  check("只依赖 vid：无 url 的视频也要有出单量按钮",
    () => assert(lis[1].querySelector(".pvd-sales") !== null));
}

console.log("== 用例 17：无 vid 时不渲染出单量按钮 + 弹窗拦截兜底 ==");
{
  const env = makeDomWithOpen(() => null);
  const w = env.w;
  const noVid = [{
    title: "no-vid product", product_id: "9", fastmoss_url: "https://www.fastmoss.com/e-commerce/detail/9",
    vids: 1, pct: 1, accounts: ["a"], total_plays: 1, anchor_type: null,
    videos: [{ account: "a", vid: "", title: "t", plays: 1, url: "https://www.tiktok.com/@a/video/1", publish_time: "" }],
  }];
  w.renderBench({ patterns: { products_top: noVid }, kpis: {}, accounts: [] });
  const box = w.document.querySelector("#bench-products");
  click(w, box.querySelectorAll(".prod-head")[0]);
  check("无 vid → 不渲染出单量按钮", () => assert(box.querySelector(".pvd-sales") === null));
  check("无 vid → 仍渲染 TikTok 链接", () => assert(box.querySelector(".pvd-a") !== null));

  const env2 = makeDomWithOpen(() => null);
  const w2 = env2.w;
  w2.renderBench({ patterns: { products_top: PRODUCTS }, kpis: {}, accounts: [] });
  const box2 = w2.document.querySelector("#bench-products");
  click(w2, box2.querySelectorAll(".prod-head")[0]);
  click(w2, box2.querySelectorAll(".pd-videos li")[0].querySelector(".pvd-sales"));
  check("走 fastmossFallback 且落到该视频页",
    () => assert(env2.fallback.length === 1 && env2.fallback[0] === "https://www.fastmoss.com/media-source/video/7692064138700328222", JSON.stringify(env2.fallback)));
}

console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
