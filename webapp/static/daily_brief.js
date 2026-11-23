/* 每日简报只读本地数据。来源文本通过 textContent 写入。 */
(function () {
  "use strict";
  const byId = id => document.getElementById("brief-" + id);
  const view = document.getElementById("view-brief");
  if (!view) return;
  const numbers = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const pubTime = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  });
  let requestId = 0;
  let lastPayload = "";

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function metric(value) {
    return Number.isFinite(value) ? numbers.format(value) : "未记录";
  }

  function displayDate(date) {
    const [year, month, day] = date.split("-");
    return year + " 年 " + Number(month) + " 月 " + Number(day) + " 日";
  }

  function videoEntry(video, kind) {
    const row = element("li", "brief-entry");
    const title = video.title || ("视频 " + video.id);
    const safe = /^https:\/\/www\.tiktok\.com\/@[\w.-]+\/video\/\d+\/?$/.test(video.url);
    const link = element(safe ? "a" : "span", "brief-video-title", title);
    link.title = title;
    if (safe) {
      link.href = video.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    const info = element("div", "brief-source");
    info.append(element("span", "brief-account", "@" + video.account));
    if (video.published_at) {
      const published = new Date(video.published_at);
      if (Number.isFinite(published.getTime())) {
        const label = /^\d{4}-\d{2}-\d{2}$/.test(video.published_at)
          ? displayDate(video.published_at) : pubTime.format(published);
        const time = element("time", "", label + " 发布");
        time.dateTime = video.published_at;
        info.append(time);
      }
    }
    row.append(link, info);
    const stats = element("div", "brief-stats");
    if (kind === "growing") {
      stats.append(element("strong", "brief-gain", "+" + metric(video.growth) + " 播放"),
        element("span", "", "昨日 " + metric(video.previous_plays) + " → 今日 " + metric(video.plays)));
    } else if (kind === "topic") {
      stats.append(element("strong", "brief-ratio", metric(video.multiplier) + " 倍"),
        element("span", "", metric(video.plays) + " 播放 · 历史中位数 " + metric(video.baseline_plays) +
          "（" + metric(video.baseline_size) + " 条视频）"));
    } else {
      stats.append(element("span", "", "播放 " + metric(video.plays)));
    }
    row.append(stats);
    return row;
  }

  function topicEntry(topic) {
    const row = element("li", "brief-topic");
    const head = element("div", "brief-topic-head");
    head.append(element("h3", "", "#" + topic.tag),
      element("span", "", metric(topic.account_count) + " 个账号 · " + metric(topic.video_count) + " 条视频"));
    const examples = element("ul", "brief-examples");
    for (const video of topic.examples) examples.append(videoEntry(video, "topic"));
    row.append(head, examples);
    return row;
  }

  function renderSection(key, data, unit, empty, makeItem, preview) {
    const host = byId(key + "-list");
    const wasOpen = !!host.querySelector("details[open]");
    host.replaceChildren();
    byId(key + "-count").textContent = metric(data.count) + " " + unit;
    if (!data.items.length) {
      host.append(element("p", "brief-empty", empty));
      return;
    }
    const makeList = items => {
      const list = element("ul", "brief-list");
      for (const item of items) list.append(makeItem(item));
      return list;
    };
    host.append(makeList(data.items.slice(0, preview)));
    if (data.items.length > preview) {
      const more = element("details", "brief-more");
      more.open = wasOpen;
      more.append(element("summary", "", "更多" + (unit === "个" ? "选题" : "视频") +
        "（" + numbers.format(data.items.length - preview) + " " + unit + "）"), makeList(data.items.slice(preview)));
      host.append(more);
    }
    if (data.count > data.items.length) {
      host.append(element("p", "brief-limit", "共 " + metric(data.count) + " " + unit +
        "，简报列出前 " + metric(data.items.length) + " " + unit + "。"));
    }
  }

  function render(data) {
    const c = data.coverage;
    if (!c || !/^\d{4}-\d{2}-\d{2}$/.test(data.date) ||
        ![data.new_videos, data.growing_videos, data.topics].every(s => Number.isInteger(s?.count) && Array.isArray(s.items))) {
      throw new Error("Invalid daily briefing");
    }
    byId("date").textContent = displayDate(data.date) + " · 北京时间";
    byId("coverage").textContent = "跟踪 " + metric(c.tracked_accounts) + " 个账号 · 今日有播放记录 " +
      metric(c.observed_today_accounts) + " 个 · 昨日 " + metric(c.observed_yesterday_accounts) + " 个";
    renderSection("new", data.new_videos, "条", c.tracked_accounts
      ? "已收录的视频中，暂无今天发布的作品。" : "添加对标账号并完成采集后，可在这里查看当天发布的视频。",
      v => videoEntry(v, "new"), 3);
    renderSection("growing", data.growing_videos, "条", c.comparable_videos
      ? "对比了 " + metric(c.comparable_videos) + " 条视频，暂未看到播放增长。"
      : "需要同一条旧视频在昨天和今天的播放记录，目前还不能计算增量。", v => videoEntry(v, "growing"), 3);
    renderSection("topics", data.topics, "个", c.baseline_accounts
      ? "目前可比较的作品中，暂无两个账号同时达标的标签。"
      : "暂无可与历史表现比较的带标签作品。每个账号至少需要 3 条在相近发布时长有播放记录的历史作品。", topicEntry, 2);
    const notes = byId("notes");
    notes.replaceChildren();
    for (const text of c.notes || []) notes.append(element("p", "", text));
    if (!c.observed_today_accounts && !data.new_videos.count) {
      const links = element("p", "brief-start");
      if (document.getElementById("view-setup")) {
        const setup = element("a", "", "环境配置");
        setup.href = "#setup";
        links.append(setup, document.createTextNode(" · "));
      }
      const action = element("a", "", c.tracked_accounts ? "一键运行" : "账号管理");
      action.href = c.tracked_accounts ? "#run" : "#accounts";
      links.append(action);
      byId("new-list").append(links);
    }
    notes.hidden = !notes.childElementCount;
  }

  async function refresh(quiet = false) {
    const current = ++requestId;
    const status = byId("status");
    byId("refresh").disabled = true;
    byId("sections").setAttribute("aria-busy", "true");
    if (!quiet) status.textContent = lastPayload ? "正在刷新…" : "正在读取简报…";
    try {
      const response = await fetch("/api/daily-insights", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Could not load daily briefing");
      const data = await response.json();
      if (current !== requestId) return;
      const payload = JSON.stringify(data);
      if (payload !== lastPayload) {
        render(data);
        lastPayload = payload;
      }
      status.textContent = "";
      byId("refresh").textContent = "刷新简报";
    } catch {
      if (current !== requestId) return;
      status.textContent = lastPayload ? "刷新失败，当前显示上次读取的简报。" : "简报读取失败，请重试。";
      byId("refresh").textContent = "重试";
    } finally {
      if (current === requestId) {
        byId("refresh").disabled = false;
        byId("sections").setAttribute("aria-busy", "false");
      }
    }
  }

  function refreshIfVisible() {
    if (!document.hidden && view.classList.contains("active") && !byId("refresh").disabled) refresh(true);
  }
  byId("refresh").addEventListener("click", () => refresh());
  document.addEventListener("visibilitychange", refreshIfVisible);
  setInterval(refreshIfVisible, 5 * 60 * 1000);
  window.dailyBrief = { refresh };
})();
