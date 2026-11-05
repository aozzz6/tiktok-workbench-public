/* 前端测试：提示词反推进度条 + 模式记忆（2026-10-23 验收修订 A4 / 进度条）
 * 覆盖：
 *   1) 任务在跑时渲染 .rv-prog：阶段名、真实百分比、已用时
 *   2) 步骤胶囊（.rv-steps li）数量与后端 steps 一致，当前步标 .cur，已过步标 .done
 *   3) busy 时进度条带 .rv-bar-live（动效＝进程还活着的视觉提示）
 *   4) 任务不 busy 时不画进度块
 *   5) rv-mode 的 change 即刻写入 localStorage（切完直接刷新不丢）
 *   6) 视图切回（visibilitychange）会主动再拉一次列表
 * 运行：NODE_PATH=<node workspace> node tests/reverse_progress_ui.test.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(ROOT + "/static/index.html", "utf8");
const revPath = ROOT + "/static/reverse.js";
const reverseJs = fs.readFileSync(revPath, "utf8");

let pass = 0, fail = 0;
const ok = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ " + n + (extra !== undefined ? " — " + extra : "")); }
};
const eq = (n, a, b) => ok(n, a === b, JSON.stringify(a) + " !== " + JSON.stringify(b));

const NOW = Math.floor(Date.now() / 1000);

function busyItem(over) {
  return Object.assign({
    video_id: "179267248485339344",
    stage: "analyzing",
    stage_label: "核对真实镜头与动作顺序",
    progress: 25,
    busy: true,
    busy_kind: "analyze",
    busy_since: NOW - 72,
    updated_at: NOW - 4,
    age_s: 4,
    mode: "restore",
    frames: 46,
    ai_frames: 26,
    shots: 0,
    meta: { author: "demo", duration: 12.98 },
    probe: { width: 720, height: 1280, fps: 30, duration: 12.98 },
    steps: [
      { label: "抽取参考图", at: 5 },
      { label: "核对镜头与动作", at: 25 },
      { label: "生成还原提示词", at: 55 },
      { label: "原音轨与参考片段", at: 82 },
      { label: "保存报告与素材包", at: 97 },
    ],
    log: [],
  }, over || {});
}

function makeDom(items, storage) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, { runScripts: "outside-only", virtualConsole: vc, url: "http://127.0.0.1:8787/" });
  const w = dom.window;
  Object.entries(storage || {}).forEach(([k, v]) => w.localStorage.setItem(k, v));
  const ready = new Promise((res) => {
    if (w.document.readyState !== "loading") return res();
    w.document.addEventListener("DOMContentLoaded", () => res());
  });
  const calls = [];
  w.$ = (sel) => w.document.querySelector(sel);
  w.$$ = (sel) => Array.from(w.document.querySelectorAll(sel));
  w.toast = () => {};
  w.api = async (url) => {
    calls.push(String(url));
    if (String(url).startsWith("/api/reverse/list")) return { items: typeof items === "function" ? items() : items };
    if (String(url).startsWith("/api/reverse/models")) return { items: [], default: "" };
    return {};
  };
  w.fetch = w.api;
  w.eval(reverseJs);
  return { w, calls, errors, ready, doc: w.document };
}

const tick = () => new Promise((r) => setTimeout(r, 30));

(async function main() {
  console.log("提示词反推进度条 / 模式记忆");

  {
    const { w, doc, ready } = makeDom([busyItem()]);
    await ready;
    w.RV_load();
    await tick();
    const prog = doc.querySelector(".rv-prog");
    ok("在跑的任务有进度块", !!prog);
    eq("步骤胶囊数量 == 后端 steps", doc.querySelectorAll(".rv-steps li").length, 5);
    eq("当前步是第 2 格", doc.querySelectorAll(".rv-steps li")[1].className, "cur");
    eq("第 1 格已过", doc.querySelectorAll(".rv-steps li")[0].className, "done");
    eq("第 3 格未到", doc.querySelectorAll(".rv-steps li")[2].className, "");
    ok("显示真实百分比", /25%/.test(prog.textContent), prog.textContent);
    ok("显示已用时", /已用 1:1\d|已用 1:2\d|已用 7\d 秒/.test(prog.querySelector("[data-elapsed]").textContent),
      prog.querySelector("[data-elapsed]").textContent);
    ok("显示更新于", /更新于 \d+ 秒前/.test(prog.querySelector("[data-age]").textContent));
    ok("busy 时进度条带动效类", !!doc.querySelector(".rv-bar i.rv-bar-live"));
    ok("说明文字如实交代长步骤无心跳", /模型读图是长步骤/.test(prog.textContent));
    const pct = doc.querySelector(".rv-bar i").getAttribute("style") || "";
    ok("条宽跟真实百分比一致", pct.indexOf("25%") >= 0, pct);
  }

  {
    const { w, doc, ready } = makeDom([busyItem({ busy: false, stage: "done", stage_label: "原片还原完成", progress: 100 })]);
    await ready;
    w.RV_load();
    await tick();
    ok("跑完的任务不画进度块", !doc.querySelector(".rv-prog"));
  }

  {
    const { w, doc, ready } = makeDom([busyItem({ progress: 60, stage_label: "生成还原提示词" })]);
    await ready;
    w.RV_load();
    await tick();
    const lis = doc.querySelectorAll(".rv-steps li");
    eq("60% 时当前步是第 3 格", lis[2].className, "cur");
    eq("60% 时第 2 格已完成", lis[1].className, "done");
  }

  {
    const { w, doc, ready } = makeDom([], { tkrev_mode: "ad" });
    await ready;
    eq("初始按已存模式回填", doc.querySelector("#rv-mode").value, "ad");
    const sel = doc.querySelector("#rv-mode");
    sel.value = "restore";
    sel.dispatchEvent(new w.Event("change"));
    eq("change 即刻落盘模式", w.localStorage.getItem("tkrev_mode"), "restore");
  }

  {
    const { w, calls, ready, doc } = makeDom([busyItem()]);
    await ready;
    doc.querySelector("#view-reverse").classList.add("active");   // 列表只在进入视图时刷新
    w.RV_load();
    await tick();
    const before = calls.filter((u) => u.startsWith("/api/reverse/list")).length;
    doc_visibility(w, "visible");
    await tick();
    const after = calls.filter((u) => u.startsWith("/api/reverse/list")).length;
    ok("切回标签页主动刷新", after > before, before + " -> " + after);
  }

  function doc_visibility(win, state) {
    Object.defineProperty(win.document, "visibilityState", { value: state, configurable: true });
    win.document.dispatchEvent(new win.Event("visibilitychange"));
  }

  console.log("\n通过 " + pass + " / 失败 " + fail);
  process.exit(fail ? 1 : 0);
})();
