/* Run with node tests/daily_brief.test.js from webapp. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const read = name => fs.readFileSync(path.join(__dirname, "../static", name), "utf8");
const html = read("index.html");
const hasSetup = html.includes('id="view-setup"');
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
const video = {
  id: "12345", account: "sample", title: "手冲咖啡的三种做法",
  published_at: "2026-11-23T13:40:00+08:00", url: "https://www.tiktok.com/@sample/video/12345", plays: 2100
};
const empty = {
  date: "2026-11-23", coverage: { tracked_accounts: 2, observed_today_accounts: 0,
    observed_yesterday_accounts: 0, comparable_videos: 0, baseline_accounts: 0, notes: ["缺少昨天的播放记录。"] },
  new_videos: { count: 0, items: [] }, growing_videos: { count: 0, items: [] }, topics: { count: 0, items: [] }
};
const full = {
  ...empty, coverage: { tracked_accounts: 2, observed_today_accounts: 2, observed_yesterday_accounts: 2,
    comparable_videos: 4, baseline_accounts: 2, notes: [] },
  new_videos: { count: 24, items: [video, video, video, { ...video, title: "<img src=x onerror=alert(1)>",
    plays: null, published_at: "2026-11-23", url: "javascript:alert(1)" }] },
  growing_videos: { count: 1, items: [{ ...video, published_at: "2026-11-22", previous_plays: 1500, growth: 600 }] },
  topics: { count: 1, items: [{ tag: "咖啡", account_count: 2, video_count: 3, examples: [
    { ...video, baseline_size: 4, baseline_plays: 1000, multiplier: 2.1 },
    { ...video, account: "coffeelab", url: "https://www.tiktok.com/@coffeelab/video/222",
      baseline_size: 6, baseline_plays: 1050, multiplier: 2 }
  ] }] }
};

function fixture(hash = "") {
  const dom = new JSDOM(html, { url: "http://127.0.0.1/" + hash, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const calls = [];
  let reply = () => ok(full);
  const ok = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  w.fetch = url => {
    calls.push(String(url));
    if (url === "/api/daily-insights") return reply();
    if (url.startsWith("/api/setup/status")) return ok({ ready: false, network: { online: false } });
    if (url.startsWith("/api/overview")) return ok({ accounts: [], idle_accounts: [], runs: [], diagnostics: {} });
    return ok({});
  };
  w.scrollTo = () => {};
  w.eval(read("daily_brief.js"));
  w.eval(read("app.js").replace(/\nboot\(\);/, "\n"));
  if (hasSetup) w.eval(read("setup_wizard.js"));
  const $ = selector => w.document.querySelector(selector);
  return { dom, w, $, calls, set: body => { reply = () => ok(body); }, responder: fn => { reply = fn; } };
}

(async () => {
  const { w, $, dom, calls, set, responder } = fixture();
  try {
    await tick();
    assert.equal($(".view.active").id, "view-brief");
    assert.equal($(".nav-item[aria-current=page]").dataset.view, "brief");
    assert.equal(w.location.hash, "", "keep the default URL untouched");
    assert.ok(!calls.some(u => u.includes("/api/setup/status")), "offline insights do not require setup probes");
    assert.equal($("#brief-new-count").textContent, "24 条");
    assert.match($("#brief-new-list").textContent, /简报列出前 4 条/);
    assert.equal($("#brief-new-list a").href, video.url);
    assert.ok($("#brief-new-list details"));
    assert.equal($("#brief-new-list img"), null);
    assert.equal($("#brief-new-list details a"), null);
    assert.match($("#brief-new-list details").textContent, /播放 未记录/);
    assert.equal($("#brief-new-list details time").textContent, "2026 年 11 月 23 日 发布");
    assert.match($("#brief-growing-list").textContent, /\+600 播放.*昨日 1,500 → 今日 2,100/);
    assert.equal($("#brief-topics-list").querySelectorAll("a").length, 2);
    assert.match($("#brief-topics-list").textContent, /#咖啡.*2 个账号 · 3 条视频.*2.1 倍.*历史中位数 1,000/);

    $('.nav-item[data-view="overview"]').click();
    assert.equal($(".view.active").id, "view-overview");
    w.history.back();
    await tick();
    assert.equal($(".view.active").id, "view-brief");
    const reads = calls.filter(u => u === "/api/daily-insights").length;
    w.refreshActiveView();
    await tick();
    assert.equal(calls.filter(u => u === "/api/daily-insights").length, reads + 1);

    let resolveSlow;
    responder(() => new Promise(resolve => { resolveSlow = resolve; }));
    const old = w.dailyBrief.refresh();
    set(empty);
    await w.dailyBrief.refresh();
    resolveSlow({ ok: true, json: () => Promise.resolve(full) });
    await old;
    assert.equal($("#brief-new-count").textContent, "0 条");
    assert.match($("#brief-growing-list").textContent, /还不能计算增量/);
    assert.match($("#brief-topics-list").textContent, /历史作品/);
    assert.match($("#brief-notes").textContent, /缺少昨天/);
    assert.ok($('#brief-new-list a[href="#run"]'));

    responder(() => Promise.resolve({ ok: false, status: 500 }));
    await w.dailyBrief.refresh();
    assert.equal($("#brief-refresh").textContent, "重试");
    assert.match($("#brief-status").textContent, /上次读取/);
    set({ ...empty, coverage: { ...empty.coverage, comparable_videos: 5, baseline_accounts: 2 } });
    $("#brief-refresh").click();
    await tick();
    assert.equal($("#brief-status").textContent, "");
    assert.match($("#brief-growing-list").textContent, /对比了 5 条视频/);
    assert.match($("#brief-topics-list").textContent, /暂无两个账号同时达标/);
  } finally {
    dom.window.close();
  }

  const routes = ["#overview", "#schedule", "#brief"];
  if (hasSetup) routes.push("#setup");
  for (const route of routes) {
    const f = fixture(route);
    try {
      await tick();
      assert.equal(f.$(".view.active").id, "view-" + route.slice(1));
      assert.equal(f.calls.includes("/api/daily-insights"), route === "#brief");
      if (route === "#setup") assert.ok(f.calls.some(u => u.includes("/api/setup/status")));
    } finally {
      f.dom.window.close();
    }
  }
  console.log("PASS daily brief rendering, historical coverage, retry, refresh and routing");
})().catch(error => { console.error(error); process.exitCode = 1; });
