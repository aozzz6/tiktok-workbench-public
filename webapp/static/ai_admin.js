/* AI 接入（2026-10-28 · S041 纸本重构）
   真相源：~/.workbuddy/models.json（apiKey 明文；本页只显示掩码，不回传原文）。
   读：GET  /api/ai/models/admin · GET /api/ai/calls?limit=200
   写：POST /api/ai/models · DELETE /api/ai/models/{id} · POST /api/ai/config
   探活：POST /api/ai/test · 备份回滚：POST /api/ai/models/restore（后端支持时出现）
   依赖 app.js 的全局函数：esc() / api() / toast()。 */
(function () {
  "use strict";

  var HOST_ID = "ai-conf-host";

  var S = null;            // /api/ai/models/admin
  var CALLS = null;        // /api/ai/calls
  var sel = "";            // 选中的模型 id
  var filter = "all";      // all | default | fallback | nokey | cooling
  var q = "";              // 搜索
  var draft = null;        // { model, fallbacks[], enabled } 未保存的配置草稿
  var drawer = null;       // { mode:'new'|'edit', id:'', busy:false, err:'' }
  var confirmBox = null;   // { kind, id, title, body, okText, danger }
  var tests = {};          // id -> { busy, ok, latency, json, at, err }
  var testingAll = false;
  var usageWin = "24h";
  var loadErr = "";
  var listTop = 0;

  var PURPOSES = [
    ["repair.diagnose", "一键修复 · 诊断"],
    ["repair.attribute", "一键修复 · 属性归因"],
    ["prompt_optimizer", "提示词优化"],
    ["ps_product_scan", "产品图识别"],
    ["reverse", "提示词反推"]
  ];
  var P_LABEL = {
    "repair.diagnose": "修复诊断",
    "repair.attribute": "属性归因",
    "prompt_optimizer": "提示词优化",
    "ps_product_scan": "图片识别",
    "probe": "连通测试",
    "probe_vision": "视觉探活",
    "reverse": "提示词反推"
  };

  function host() { return document.getElementById(HOST_ID); }
  function models() { return (S && S.models) || []; }
  function byId(id) {
    var list = models();
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }
  function nameOf(id) { var m = byId(id); return m ? (m.name || m.id) : id; }
  function tok(v) {
    var n = Number(v);
    if (!isFinite(n) || n <= 0) return "—";
    if (n >= 1e6) return (n / 1e6).toFixed(n % 1e6 ? 1 : 0) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "K";
    return String(n);
  }
  function num(v) { return Number(v || 0).toLocaleString("zh-CN"); }
  function fmtTs(ts) {
    try { return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }); }
    catch (e) { return String(ts); }
  }
  function fmtClock(ts) {
    try {
      var d = new Date(ts * 1000);
      return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    } catch (e) { return String(ts); }
  }
  function fmtDay(ts) {
    try {
      var d = new Date(ts * 1000);
      return (d.getMonth() + 1) + "-" + ("0" + d.getDate()).slice(-2);
    } catch (e) { return String(ts); }
  }

  /* ───────────── 状态派生 ───────────── */

  function keyInfo() {
    var seen = {}, n = 0;
    models().forEach(function (m) {
      if (m.has_key && !seen[m.key_masked]) { seen[m.key_masked] = 1; n++; }
    });
    return n;
  }
  function isDirty() {
    if (!S || !draft) return false;
    if (draft.model !== (S.default || "")) return true;
    if (!!draft.enabled !== !!S.enabled) return true;
    var a = draft.fallbacks || [], b = S.fallbacks || [];
    if (a.length !== b.length) return true;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return true;
    return purposeDirty();
  }
  function purposeDirty() {
    if (!S || !S.purposes_supported) return false;
    var a = draft.purposes || {}, b = S.purposes || {};
    var keys = {};
    Object.keys(a).forEach(function (k) { keys[k] = 1; });
    Object.keys(b).forEach(function (k) { keys[k] = 1; });
    return Object.keys(keys).some(function (k) { return (a[k] || "") !== (b[k] || ""); });
  }
  function syncDraft() {
    draft = {
      model: (S && S.default) || "",
      fallbacks: ((S && S.fallbacks) || []).slice(),
      enabled: !!(S && S.enabled)
    };
  }
  function usageStats(win) {
    if (!CALLS || !CALLS.calls) return null;
    var span = win === "7d" ? 7 * 86400e3 : 86400e3;
    var now = Date.now();
    var rows = CALLS.calls.filter(function (r) { return (Number(r.ts) * 1000) >= now - span; });
    if (!rows.length) {
      return { n: 0, ok: 0, rate: 0, avg: 0, p95: 0, tin: 0, tout: 0, byPurpose: [], byModel: [], rows: [] };
    }
    var ok = 0, tin = 0, tout = 0, lats = [];
    var pMap = {}, mMap = {};
    rows.forEach(function (r) {
      if (r.ok) ok++;
      tin += Number(r.tokens_in || 0);
      tout += Number(r.tokens_out || 0);
      var lat = Number(r.latency_ms || 0);
      if (lat > 0) lats.push(lat);
      var p = r.purpose || "unknown";
      pMap[p] = (pMap[p] || 0) + 1;
      var m = r.model || "?";
      mMap[m] = (mMap[m] || 0) + 1;
    });
    lats.sort(function (a, b) { return a - b; });
    var sum = lats.reduce(function (a, b) { return a + b; }, 0);
    var p95 = lats.length ? lats[Math.min(lats.length - 1, Math.floor(lats.length * 0.95))] : 0;
    function pairs(map) {
      return Object.keys(map).map(function (k) { return [k, map[k]]; })
        .sort(function (a, b) { return b[1] - a[1]; });
    }
    return {
      n: rows.length, ok: ok, rate: Math.round(100 * ok / rows.length),
      avg: lats.length ? Math.round(sum / lats.length) : 0, p95: p95,
      tin: tin, tout: tout,
      byPurpose: pairs(pMap), byModel: pairs(mMap), rows: rows
    };
  }

  function keyAge(ts) {
    if (!ts) return null;
    var d = Math.floor((Date.now() / 1000 - Number(ts)) / 86400);
    if (d <= 0) return { text: "今天更新", warn: false };
    if (d < 90) return { text: d + " 天前更新", warn: false };
    return { text: d + " 天前更新 · 建议轮换", warn: true };
  }

  /* ───────────── 页头 ───────────── */

  function headHtml() {
    if (!S) {
      return '<header class="ai-head"><div><h1>AI 接入</h1>' +
        '<p class="ai-sub-line">正在读取模型清单…</p></div></header>';
    }
    var st = usageStats("24h");
    var def = S.default ? nameOf(S.default) : "自动挑选";
    var badge = !S.enabled
      ? '<span class="ai-badge gray">AI 已停用</span>'
      : (S.available ? '<span class="ai-badge green">AI 可用</span>' : '<span class="ai-badge red">AI 不可用</span>');
    var sub = '<b>' + models().length + '</b> 个模型 · <b>' + keyInfo() + '</b> 把 key · 默认 ' +
      '<b>' + esc(def) + '</b>' +
      (st ? ' · 近 24h <b>' + st.n + '</b> 次调用（' + st.rate + '% 成功）' : '');
    return '<header class="ai-head">' +
      '<div><h1>AI 接入</h1><p class="ai-sub-line">' + sub + '</p></div>' +
      '<div class="ai-head-act">' + badge +
      '<label class="ai-toggle" title="启用 / 停用 AI（停用后业务侧全部降级）">' +
      '<input type="checkbox" id="ai-enabled" data-act="toggle-enabled"' + (draft.enabled ? " checked" : "") + '>' +
      '<span class="ai-track"><i></i></span><span>AI 启用</span></label>' +
      '<button class="btn ghost" data-act="refresh">刷新</button>' +
      '<button class="btn primary" data-act="new">＋ 新增模型</button>' +
      '</div></header>';
  }

  /* ───────────── 左栏索引 ───────────── */

  function counts() {
    var c = { all: models().length, default: 0, fallback: 0, nokey: 0, cooling: 0 };
    models().forEach(function (m) {
      if (m.is_default) c.default++;
      if (m.in_fallbacks) c.fallback++;
      if (!m.has_key) c.nokey++;
      if (Number(m.cooling_ms) > 0) c.cooling++;
    });
    return c;
  }

  function visibleModels() {
    var s = q.trim().toLowerCase();
    return models().filter(function (m) {
      if (filter === "default" && !m.is_default) return false;
      if (filter === "fallback" && !m.in_fallbacks) return false;
      if (filter === "nokey" && m.has_key) return false;
      if (filter === "cooling" && !(Number(m.cooling_ms) > 0)) return false;
      if (!s) return true;
      return (m.name || "").toLowerCase().indexOf(s) >= 0 ||
        (m.id || "").toLowerCase().indexOf(s) >= 0 ||
        (m.vendor || "").toLowerCase().indexOf(s) >= 0;
    });
  }

  function rowHtml(m) {
    var dot = "ai-dot";
    if (!m.has_key) dot += " is-nokey";
    else if (m.is_default) dot += " is-default";
    else if (m.in_fallbacks) dot += " is-fb";
    var flags = "";
    if (m.supportsImages) flags += "图";
    if (m.supportsReasoning) flags += "推";
    if (m.supportsToolCall) flags += "工";
    var t = tests[m.id];
    var tail = t && !t.busy ? (t.ok ? " · " + t.latency + "ms" : " · 不通") : "";
    return '<button class="ai-row' + (sel === m.id ? " is-on" : "") + '" data-act="pick" data-id="' + esc(m.id) + '">' +
      '<span class="' + dot + '"></span>' +
      '<span class="ai-row-main"><b>' + esc(m.name || m.id) + '</b>' +
      '<em>' + esc(m.id) + (m.has_key ? ' · ' + esc(m.key_masked.slice(-4)) : ' · 缺 key') + tail + '</em></span>' +
      '<span class="ai-row-flags">' + flags + '</span></button>';
  }

  function indexHtml() {
    var c = counts();
    var chips = [
      ["all", "全部", c.all],
      ["default", "默认", c.default],
      ["fallback", "回退", c.fallback],
      ["nokey", "缺 key", c.nokey]
    ];
    if (c.cooling) chips.push(["cooling", "冷却", c.cooling]);
    var chipsHtml = chips.map(function (x) {
      return '<button class="ai-chip' + (filter === x[0] ? " is-on" : "") + '" data-act="filter" data-v="' + x[0] + '">' +
        x[1] + ' <b>' + x[2] + '</b></button>';
    }).join("");
    var rows = visibleModels();
    var list = rows.length
      ? rows.map(rowHtml).join("")
      : '<p class="ai-index-empty">没有匹配的模型</p>';
    return '<aside class="panel ai-index">' +
      '<input class="ai-search" id="ai-q" placeholder="搜索 名称 / id / 厂商" value="' + esc(q) + '" autocomplete="off">' +
      '<div class="ai-chips">' + chipsHtml + '</div>' +
      '<div class="ai-index-list" id="ai-index-list">' + list + '</div>' +
      '<div class="ai-index-foot">' +
      '<button class="btn ghost sm" data-act="testall" style="width:100%;margin-bottom:6px"' +
      (testingAll ? " disabled" : "") + '>' + (testingAll ? "全测中…" : "全测（" + models().filter(function (m) { return m.has_key; }).length + "）") + '</button>' +
      '<button class="ai-add" data-act="new">＋ 新增模型</button>' +
      '</div>' +
      '</aside>';
  }

  function renderIndex() {
    var el = document.getElementById("ai-index-list");
    if (!el) { render(); return; }
    var rows = visibleModels();
    el.innerHTML = rows.length ? rows.map(rowHtml).join("") : '<p class="ai-index-empty">没有匹配的模型</p>';
    var chips = document.querySelectorAll("#view-aiconf .ai-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].classList.toggle("is-on", chips[i].getAttribute("data-v") === filter);
    }
  }

  /* ───────────── ① 默认与回退 ───────────── */

  function configHtml() {
    var opts = ['<option value="">自动（按回退链挑）</option>'].concat(models().map(function (m) {
      return '<option value="' + esc(m.id) + '"' + (draft.model === m.id ? " selected" : "") + '>' +
        esc(m.name || m.id) + '（' + esc(m.id) + '）</option>';
    })).join("");

    var inFb = {};
    (draft.fallbacks || []).forEach(function (id) { inFb[id] = 1; });

    var fbRows = (draft.fallbacks || []).map(function (id, i) {
      var m = byId(id);
      var meta = m ? (m.has_key ? esc(m.vendor || m.id) : '<span style="color:#b91c1c">缺 key</span>') : '<span style="color:#b91c1c">已不存在</span>';
      return '<li class="ai-fb-item" draggable="true" data-id="' + esc(id) + '">' +
        '<span class="ai-fb-n">' + (i + 1) + '</span>' +
        '<span class="ai-fb-name">' + esc(m ? (m.name || m.id) : id) + '</span>' +
        '<span class="ai-fb-meta">' + meta + '</span>' +
        '<span class="ai-fb-act">' +
        '<button class="ai-iconbtn" data-act="fb-up" data-id="' + esc(id) + '" title="上移">↑</button>' +
        '<button class="ai-iconbtn" data-act="fb-down" data-id="' + esc(id) + '" title="下移">↓</button>' +
        '<button class="ai-iconbtn" data-act="fb-del" data-id="' + esc(id) + '" title="移除">×</button>' +
        '</span></li>';
    }).join("");

    var candidates = models().filter(function (m) {
      return m.has_key && !inFb[m.id];
    });
    var addSel = candidates.length
      ? '<select id="ai-fb-pick">' + candidates.map(function (m) {
        return '<option value="' + esc(m.id) + '">' + esc(m.name || m.id) + '</option>';
      }).join("") + '</select><button class="btn ghost sm" data-act="fb-add">加入</button>'
      : '<span class="muted">没有可加入的模型（都已加入或都缺 key）</span>';

    var full = (draft.fallbacks || []).length >= 5;
    var dirty = isDirty();

    return '<section class="panel" id="ai-cfg-panel">' +
      '<div class="ai-panel-head"><h2>默认与回退</h2>' +
      '<span class="muted">默认模型失败时，按下面的顺序依次尝试（最多 5 个）</span></div>' +
      '<div class="ai-cfg-row">' +
      '<label class="ai-field">默认模型<select id="ai-default">' + opts + '</select></label>' +
      '<div class="ai-field">回退链（拖拽或 ↑↓ 调顺序，第 1 条最先尝试）' +
      (fbRows ? '<ol class="ai-fb-list" id="ai-fb-list">' + fbRows + '</ol>'
        : '<div class="ai-fb-empty">还没有回退链：默认模型失败时直接降级。</div>') +
      (full ? '<p class="ai-hint">已满 5 条，先移除一条才能再加。</p>' : '<div class="ai-fb-add">' + addSel + '</div>') +
      '</div></div>' +
      '<div class="ai-savebar">' +
      (dirty ? '<span class="ai-dirty">● 未保存改动</span>' : '<span>配置已同步</span>') +
      '<span class="ai-spacer"></span>' +
      (dirty ? '<button class="btn ghost sm" data-act="cfg-revert">撤销</button>' +
        '<button class="btn primary sm" data-act="cfg-save">保存配置</button>' : '') +
      '</div></section>';
  }

  /* ───────────── ② 档案 ───────────── */

  function profileHtml() {
    var m = byId(sel);
    if (!m) {
      return '<section class="panel"><div class="ai-panel-head"><h2>选中模型</h2></div>' +
        '<div class="ai-empty">从左侧选一个模型，看它的接入参数。</div></section>';
    }
    var t = tests[m.id];
    var testTxt = t
      ? (t.busy ? "测试中…" : (t.ok
        ? fmtClock(t.at) + ' · ' + t.latency + 'ms · JSON ' + (t.json ? "正常" : "不支持")
        : fmtClock(t.at) + ' · 不通：' + (t.err || "未知错误")))
      : "未测";
    var state = [];
    if (m.is_default) state.push("默认");
    if (m.in_fallbacks) state.push("回退第 " + ((S.fallbacks || []).indexOf(m.id) + 1));
    if (Number(m.cooling_ms) > 0) state.push("冷却中 " + Math.round(Number(m.cooling_ms) / 1000) + "s");
    if (m.local) state.push("本地");
    if (!m.has_key) state.push("缺 key");

    var warn = "";
    if (!m.has_key) warn = '<p class="ai-warnline">这个条目没有 apiKey，不会被选中；补一把 key 或删除它。</p>';
    else if (Number(m.cooling_ms) > 0) warn = '<p class="ai-warnline">连续失败后进入冷却，暂时不参与挑选。</p>';

    function cap(k) {
      return '<span class="ai-tag' + (m[k] ? " on" : "") + '">' + (m[k] ? "✓ " : "— ") + capName(k) + '</span>';
    }
    function capName(k) {
      return k === "supportsImages" ? "图片" : (k === "supportsReasoning" ? "推理" : "工具");
    }

    return '<section class="panel" id="ai-profile">' +
      '<div class="ai-prof-head">' +
      '<div class="ai-prof-title"><h2>' + esc(m.name || m.id) + '</h2><code>' + esc(m.id) + '</code></div>' +
      '<div class="ai-prof-act">' +
      '<button class="btn ghost sm" data-act="test" data-id="' + esc(m.id) + '"' + (t && t.busy ? " disabled" : "") + '>' +
      (t && t.busy ? "测试中…" : "测试连通") + '</button>' +
      '<button class="btn ghost sm" data-act="edit" data-id="' + esc(m.id) + '">编辑</button>' +
      (m.is_default ? '' : '<button class="btn ghost sm" data-act="def" data-id="' + esc(m.id) + '">设为默认</button>') +
      '<button class="btn danger sm" data-act="del" data-id="' + esc(m.id) + '">删除</button>' +
      '</div></div>' + warn +
      '<dl class="ai-kv">' +
      '<dt>厂商</dt><dd>' + esc(m.vendor || "—") + '</dd>' +
      '<dt>接口</dt><dd class="mono">' + esc(m.url) + '</dd>' +
      '<dt>风格</dt><dd>' + esc(m.api_style || "自动识别") + '</dd>' +
      '<dt>上限</dt><dd>入 ' + tok(m.maxInputTokens) + ' · 出 ' + tok(m.maxOutputTokens) + '</dd>' +
      '<dt>Key</dt><dd class="mono">' + (m.has_key ? esc(m.key_masked) : "—") +
      (keyAge(m.key_updated_at)
        ? ' <span class="ai-tag' + (keyAge(m.key_updated_at).warn ? " warn" : "") + '">' + esc(keyAge(m.key_updated_at).text) + '</span>'
        : '') + '</dd>' +
      '<dt>能力</dt><dd>' + cap("supportsImages") + cap("supportsReasoning") + cap("supportsToolCall") + '</dd>' +
      '<dt>状态</dt><dd>' + (state.length ? state.map(function (s) {
        return '<span class="ai-tag on">' + esc(s) + '</span>';
      }).join("") : '<span class="muted">未启用</span>') + '</dd>' +
      '<dt>最近测试</dt><dd>' + esc(testTxt) + '</dd>' +
      '</dl></section>';
  }

  /* ───────────── ③ 用途绑定 ───────────── */

  function purposesSupported() {
    return !!(S && S.purposes_supported);
  }

  function purposeHtml() {
    if (!purposesSupported()) {
      return '<section class="panel"><div class="ai-panel-head"><h2>按用途绑定</h2>' +
        '<span class="muted">后端升级后可用</span></div>' +
        '<p class="muted" style="margin:0">当前后端还没有 purposes 配置：所有用途都跟随默认与回退链。</p></section>';
    }
    var cur = draft.purposes || {};
    var rows = PURPOSES.map(function (p) {
      var key = p[0], label = p[1];
      var want = cur[key] || "";
      var needImg = key === "ps_product_scan";
      var opts = ['<option value="">继承默认</option>'].concat(models().filter(function (m) {
        if (needImg && !m.supportsImages) return false;
        return m.has_key;
      }).map(function (m) {
        return '<option value="' + esc(m.id) + '"' + (want === m.id ? " selected" : "") + '>' +
          esc(m.name || m.id) + '</option>';
      })).join("");
      return '<div class="ai-purpose-row"><label>' + esc(label) + '</label>' +
        '<select data-act="purpose" data-key="' + esc(key) + '">' + opts + '</select></div>';
    }).join("");
    return '<section class="panel" id="ai-purpose-panel">' +
      '<div class="ai-panel-head"><h2>按用途绑定</h2>' +
      '<span class="muted">未绑定的用途跟随默认与回退链；图片识别只能绑带图片能力的模型</span></div>' +
      rows +
      (purposeDirty()
        ? '<div class="ai-savebar"><span class="ai-dirty">● 用途绑定有未保存改动</span>' +
          '<span class="ai-spacer"></span>' +
          '<button class="btn ghost sm" data-act="purpose-revert">撤销</button>' +
          '<button class="btn primary sm" data-act="cfg-save">保存绑定</button></div>'
        : '') +
      '</section>';
  }

  /* ───────────── ④ 用量 ───────────── */

  function barRow(label, n, max, suffix) {
    var w = max > 0 ? Math.max(2, Math.round(100 * n / max)) : 0;
    return '<div class="ai-barrow"><span title="' + esc(label) + '">' + esc(label) + '</span>' +
      '<div class="ai-bar"><i style="width:' + w + '%"></i></div><b>' + n + (suffix || '') + '</b></div>';
  }

  function budgetHtml() {
    if (!CALLS || !CALLS.budget) return "";
    var b = CALLS.budget;
    var cl = Number(b.calls_limit || 0), tl = Number(b.tokens_limit || 0);
    var cu = Number(b.calls || 0), tu = Number(b.tokens || 0);
    if (!cl && !tl) return "";
    var rows = "";
    if (cl) rows += barRow("调用次数", cu, cl, " / " + cl);
    if (tl) rows += barRow("token", tu, tl, " / " + num(tl));
    return '<div class="ai-budget"><h3 style="font-size:12px;color:var(--sub);margin:0 0 8px;font-weight:600">' +
      '本轮预算（单次修复运行，不是日预算）</h3>' + rows + '</div>';
  }

  function usageHtml() {
    if (!CALLS) {
      return '<section class="panel"><div class="ai-panel-head"><h2>调用与用量</h2></div>' +
        '<p class="muted" style="margin:0">读不到调用记录（/api/ai/calls）。</p></section>';
    }
    var st = usageStats(usageWin);
    var winBtns = [["24h", "近 24h"], ["7d", "近 7 天"]].map(function (w) {
      return '<button class="ai-chip' + (usageWin === w[0] ? " is-on" : "") + '" data-act="win" data-v="' + w[0] + '">' + w[1] + '</button>';
    }).join("");

    if (!st || !st.n) {
      return '<section class="panel"><div class="ai-panel-head"><h2>调用与用量</h2>' +
        '<div style="display:flex;gap:5px">' + winBtns + '</div></div>' +
        '<p class="muted" style="margin:0">这个时间窗里还没有调用。</p></section>';
    }

    var maxP = st.byPurpose.length ? st.byPurpose[0][1] : 0;
    var maxM = st.byModel.length ? st.byModel[0][1] : 0;
    var pRows = st.byPurpose.map(function (x) {
      return barRow(P_LABEL[x[0]] || x[0], x[1], maxP);
    }).join("");
    var mTop = st.byModel.slice(0, 6);
    var rest = st.byModel.slice(6).reduce(function (a, x) { return a + x[1]; }, 0);
    var mRows = mTop.map(function (x) { return barRow(x[0], x[1], maxM); }).join("");
    if (rest) mRows += barRow("其他", rest, maxM);

    var recent = st.rows.slice(0, 10).map(function (r) {
      return '<tr class="' + (r.ok ? "" : "fail") + '">' +
        '<td class="mono">' + esc(fmtDay(r.ts) + " " + fmtClock(r.ts)) + '</td>' +
        '<td>' + esc(r.model || "?") + '</td>' +
        '<td>' + esc(P_LABEL[r.purpose] || r.purpose || "—") + '</td>' +
        '<td class="mono">' + (Number(r.latency_ms || 0) >= 1000
          ? (Number(r.latency_ms) / 1000).toFixed(1) + "s" : Number(r.latency_ms || 0) + "ms") + '</td>' +
        '<td class="' + (r.ok ? "ok" : "bad") + '">' + (r.ok ? "成功" : "失败") + '</td></tr>';
    }).join("");

    return '<section class="panel" id="ai-usage">' +
      '<div class="ai-panel-head"><h2>调用与用量</h2>' +
      '<span class="muted" style="display:flex;gap:5px">' + winBtns + '</span></div>' +
      '<div class="ai-metrics">' +
      '<div class="ai-metric"><em>调用</em><b>' + st.n + '</b><small>次</small></div>' +
      '<div class="ai-metric"><em>成功率</em><b>' + st.rate + '</b><small>%</small></div>' +
      '<div class="ai-metric"><em>平均耗时</em><b>' + (st.avg >= 1000 ? (st.avg / 1000).toFixed(1) + "s" : st.avg + "ms") + '</b>' +
      '<small>p95 ' + (st.p95 >= 1000 ? (st.p95 / 1000).toFixed(1) + "s" : st.p95 + "ms") + '</small></div>' +
      '<div class="ai-metric"><em>token</em><b>' + tok(st.tin) + '/' + tok(st.tout) + '</b><small>入/出</small></div>' +
      '</div>' +
      '<div class="ai-dist">' +
      '<div><h3>按用途</h3>' + pRows + '</div>' +
      '<div><h3>按模型</h3>' + mRows + '</div>' +
      '</div>' +
      '<h3 style="font-size:12px;color:var(--sub);margin:14px 0 2px;font-weight:600">最近调用</h3>' +
      '<table class="ai-calls"><thead><tr><th>时间</th><th>模型</th><th>用途</th><th>耗时</th><th>结果</th></tr></thead>' +
      '<tbody>' + recent + '</tbody></table>' +
      '<p class="ai-hint" style="margin-top:6px">基于最近 ' + (CALLS.calls ? CALLS.calls.length : 0) + ' 次调用（接口上限 200 条）。</p>' +
      budgetHtml() + '</section>';
  }

  /* ───────────── ⑤ 文件与备份 ───────────── */

  function filesHtml() {
    var backups = (S.backups || []);
    var bk = backups.length
      ? '<ul class="ai-bk-list">' + backups.map(function (b) {
        return '<li><span class="grow">' + esc(b.name) + '</span>' +
          '<span>' + esc(fmtTs(b.ts)) + '</span><span>' + Math.max(1, Math.round(Number(b.bytes || 0) / 1024)) + ' KB</span>' +
          (S.restore_supported
            ? '<button class="btn ghost sm" data-act="restore" data-name="' + esc(b.name) + '">回滚</button>'
            : '') + '</li>';
      }).join("") + '</ul>'
      : '<p class="muted" style="margin:6px 0 0">还没有备份（第一次保存/删除时会自动生成）。</p>';
    return '<section class="panel" id="ai-files">' +
      '<div class="ai-panel-head"><h2>文件与备份</h2>' +
      '<span class="muted">每次写入前自动备份，保留最近 20 份</span></div>' +
      '<p class="ai-path">唯一真相源 <code>' + esc(S.path || "") + '</code>' +
      '<button class="btn ghost sm" data-act="copy-path">复制</button></p>' +
      (S.env_override
        ? '<p class="ai-warnline">检测到环境变量 WB_AI_KEY：它插入的「环境变量模型」优先于本清单，删改这里的条目不会影响它。</p>'
        : '') + bk + '</section>';
  }

  /* ───────────── 抽屉 / 确认框 ───────────── */

  function drawerHtml() {
    if (!drawer) return "";
    var m = drawer.mode === "edit" ? byId(drawer.id) : null;
    var isNew = drawer.mode === "new";
    function v(k, d) { return esc(m && m[k] != null ? m[k] : (d || "")); }
    var styleOpts = ["", "openai", "anthropic", "gemini"].map(function (s) {
      var lab = s || "自动识别";
      var selected = m ? (m.api_style || "") === s : s === "";
      return '<option value="' + s + '"' + (selected ? " selected" : "") + '>' + lab + '</option>';
    }).join("");

    return '<div class="ai-mask" data-act="drawer-close"></div>' +
      '<aside class="ai-drawer" role="dialog" aria-modal="true" aria-label="' + (isNew ? "新增模型" : "编辑模型") + '">' +
      '<div class="ai-drawer-head"><h2>' + (isNew ? "新增模型" : "编辑模型") + '</h2>' +
      '<button class="ai-iconbtn" data-act="drawer-close" title="关闭">×</button></div>' +
      '<div class="ai-drawer-body">' +
      '<div class="ai-form-grid">' +
      '<label class="ai-field"><span>模型 id（调用名，唯一）</span>' +
      '<input type="text" id="ai-f-id" value="' + v("id") + '"' + (isNew ? "" : " readonly") + ' placeholder="例: deepseek-flash"></label>' +
      '<label class="ai-field"><span>显示名</span>' +
      '<input type="text" id="ai-f-name" value="' + v("name") + '" placeholder="例: DS ·DeepSeek-V4.1-Flash"></label>' +
      '<label class="ai-field"><span>厂商</span>' +
      '<input type="text" id="ai-f-vendor" value="' + v("vendor") + '" placeholder="deepseek / tokenmao"></label>' +
      '<label class="ai-field"><span>接口地址 url</span>' +
      '<input type="text" id="ai-f-url" value="' + v("url") + '" placeholder="https://api.deepseek.com/v1"></label>' +
      '<label class="ai-field"><span>apiKey' + (isNew ? "" : "（留空 = 不改）") + '</span>' +
      '<input type="password" id="ai-f-key" value="" autocomplete="new-password" placeholder="' +
      (isNew ? "sk-..." : esc(m ? m.key_masked : "")) + '"></label>' +
      '<div class="ai-form-2">' +
      '<label class="ai-field"><span>接口风格</span><select id="ai-f-style">' + styleOpts + '</select></label>' +
      '<label class="ai-field"><span>输入上限 tokens</span>' +
      '<input type="number" min="1" id="ai-f-maxin" value="' + v("maxInputTokens") + '" placeholder="1000000"></label>' +
      '</div>' +
      '<label class="ai-field"><span>输出上限 tokens</span>' +
      '<input type="number" min="1" id="ai-f-maxout" value="' + v("maxOutputTokens") + '" placeholder="65536"></label>' +
      '<div class="ai-checks">' +
      '<label><input type="checkbox" id="ai-f-img"' + (m && m.supportsImages ? " checked" : "") + '> 支持图片</label>' +
      '<label><input type="checkbox" id="ai-f-reason"' + (m && m.supportsReasoning ? " checked" : "") + '> 支持推理</label>' +
      '<label><input type="checkbox" id="ai-f-tool"' + (m && m.supportsToolCall ? " checked" : "") + '> 支持工具调用</label>' +
      '</div>' +
      '<p class="ai-hint">「支持图片」影响提示词优化里的产品图识别；改动即时生效，不需要重启。</p>' +
      (drawer.err ? '<p class="ai-err">' + esc(drawer.err) + '</p>' : '') +
      '</div></div>' +
      '<div class="ai-drawer-foot">' +
      '<button class="btn ghost" data-act="drawer-close">取消</button>' +
      '<button class="btn primary" data-act="drawer-save"' + (drawer.busy ? " disabled" : "") + '>' +
      (drawer.busy ? "保存中…" : "保存") + '</button>' +
      '</div></aside>';
  }

  function confirmHtml() {
    if (!confirmBox) return "";
    return '<div class="ai-mask" data-act="confirm-cancel"></div>' +
      '<div class="ai-confirm" role="alertdialog" aria-modal="true">' +
      '<h3>' + esc(confirmBox.title) + '</h3>' +
      (confirmBox.body || []).map(function (line) {
        return '<p>' + esc(line) + '</p>';
      }).join("") +
      '<div class="ai-confirm-act">' +
      '<button class="btn ghost" data-act="confirm-cancel">取消</button>' +
      '<button class="btn ' + (confirmBox.danger ? "danger" : "primary") + '" data-act="confirm-ok">' +
      esc(confirmBox.okText || "确定") + '</button></div></div>';
  }

  /* ───────────── 渲染 ───────────── */

  function bodyHtml() {
    if (loadErr) {
      var hint = /^404/.test(loadErr)
        ? "后端还是旧代码（/api/ai/models/admin 未加载）：重启工作台后再刷新本页。"
        : "检查服务是否在运行、models.json 是否可读。";
      return '<section class="panel"><p class="muted">读不到模型清单：' + esc(loadErr) + '</p>' +
        '<p class="muted">' + esc(hint) + '</p>' +
        '<button class="btn" data-act="refresh">重试</button></section>';
    }
    if (!S) {
      return '<section class="panel"><div class="ai-skel" style="width:40%;margin-bottom:12px"></div>' +
        '<div class="ai-skel" style="width:70%;margin-bottom:8px"></div>' +
        '<div class="ai-skel" style="width:55%"></div></section>';
    }
    return '<div class="ai-wrap">' + indexHtml() +
      '<div class="ai-main">' + configHtml() + profileHtml() + purposeHtml() + usageHtml() + filesHtml() +
      '</div></div>';
  }

  function render() {
    var h = host();
    if (!h) return;
    var list = document.getElementById("ai-index-list");
    if (list) listTop = list.scrollTop;
    h.innerHTML = headHtml() + bodyHtml() + drawerHtml() + confirmHtml();
    var nl = document.getElementById("ai-index-list");
    if (nl) nl.scrollTop = listTop;
  }

  /* ───────────── 数据 ───────────── */

  async function load() {
    loadErr = "";
    try {
      var res = await Promise.all([
        api("/api/ai/models/admin"),
        api("/api/ai/calls?limit=200").catch(function () { return null; })
      ]);
      S = res[0];
      CALLS = res[1];
      syncDraft();
      draft.purposes = JSON.parse(JSON.stringify((S && S.purposes) || {}));
      var exists = models().some(function (m) { return m.id === sel; });
      if (!exists) sel = (S.default && byId(S.default)) ? S.default : (models()[0] ? models()[0].id : "");
      render();
      refreshCooldownTicker();
    } catch (e) {
      loadErr = String((e && e.message) || e || "");
      S = null;
      render();
    }
  }

  var ticker = null;
  function refreshCooldownTicker() {
    if (ticker) { clearInterval(ticker); ticker = null; }
    var hasCooling = models().some(function (m) { return Number(m.cooling_ms) > 0; });
    if (!hasCooling) return;
    ticker = setInterval(function () {
      var left = false;
      models().forEach(function (m) {
        if (Number(m.cooling_ms) > 0) {
          m.cooling_ms = Math.max(0, Number(m.cooling_ms) - 1000);
          if (m.cooling_ms > 0) left = true;
        }
      });
      render();
      if (!left) { clearInterval(ticker); ticker = null; }
    }, 1000);
  }

  async function postJson(url, body, timeoutMs) {
    var ms = timeoutMs || 20000;
    var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
    try {
      var r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: ctl ? ctl.signal : undefined
      });
      var text = await r.text();
      if (!r.ok) throw new Error(r.status + " " + text.slice(0, 200));
      return text ? JSON.parse(text) : {};
    } catch (e) {
      if (e && e.name === "AbortError") throw new Error("请求超时（" + Math.round(ms / 1000) + " 秒）");
      throw e;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /* ───────────── 动作 ───────────── */

  async function saveCfg() {
    var body = { model: draft.model || "", fallbacks: (draft.fallbacks || []).slice(0, 5), enabled: !!draft.enabled };
    if (draft.purposes && S.purposes_supported) body.purposes = draft.purposes;
    try {
      await postJson("/api/ai/config", body);
      toast("AI 配置已保存");
      await load();
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
  }

  function fbMove(id, dir) {
    var arr = draft.fallbacks || [];
    var i = arr.indexOf(id);
    if (i < 0) return;
    var j = i + dir;
    if (j < 0 || j >= arr.length) return;
    arr.splice(i, 1);
    arr.splice(j, 0, id);
    render();
  }

  async function testModel(id) {
    if (!id || (tests[id] && tests[id].busy)) return;
    tests[id] = { busy: true, at: Date.now() / 1000 };
    render();
    try {
      var r = await postJson("/api/ai/test", { model: id }, 45000);
      tests[id] = {
        busy: false, ok: !!(r && r.ok), latency: (r && r.latency_ms) || 0,
        json: !!(r && r.json_ok), at: Date.now() / 1000, err: (r && r.error) || ""
      };
      toast((r && r.ok ? "连通 ✓ " : "不通 ✗ ") + nameOf(id) +
        (r && r.ok ? " · " + r.latency_ms + "ms" : "：" + ((r && r.error) || "未知错误")),
        r && r.ok ? undefined : "error");
    } catch (e) {
      tests[id] = { busy: false, ok: false, latency: 0, json: false, at: Date.now() / 1000, err: e.message };
      toast("测试失败：" + e.message, "error");
    }
    render();
  }

  async function testAll() {
    if (testingAll) return;
    var list = models().filter(function (m) { return m.has_key; });
    if (!list.length) return toast("没有可测试的模型（都缺 key）", "error");
    testingAll = true;
    var ok = 0, bad = 0;
    for (var i = 0; i < list.length; i++) {
      var id = list[i].id;
      tests[id] = { busy: true, at: Date.now() / 1000 };
      render();
      try {
        var r = await postJson("/api/ai/test", { model: id }, 45000);
        if (r && r.ok) { ok++; } else { bad++; }
        tests[id] = {
          busy: false, ok: !!(r && r.ok), latency: (r && r.latency_ms) || 0,
          json: !!(r && r.json_ok), at: Date.now() / 1000, err: (r && r.error) || ""
        };
      } catch (e) {
        bad++;
        tests[id] = { busy: false, ok: false, latency: 0, json: false, at: Date.now() / 1000, err: e.message };
      }
      render();
    }
    testingAll = false;
    toast("全测完成：" + ok + " 通 / " + bad + " 不通", bad ? "error" : undefined);
  }

  async function saveDrawer() {
    if (!drawer || drawer.busy) return;
    function val(id) { var e = document.getElementById(id); return e ? String(e.value || "") : ""; }
    function chk(id) { var e = document.getElementById(id); return !!(e && e.checked); }
    var body = {
      id: val("ai-f-id").trim(),
      name: val("ai-f-name").trim(),
      vendor: val("ai-f-vendor").trim(),
      url: val("ai-f-url").trim(),
      apiKey: val("ai-f-key").trim(),
      api_style: val("ai-f-style"),
      supportsImages: chk("ai-f-img"),
      supportsReasoning: chk("ai-f-reason"),
      supportsToolCall: chk("ai-f-tool")
    };
    var mi = val("ai-f-maxin").trim(), mo = val("ai-f-maxout").trim();
    if (mi) body.maxInputTokens = Number(mi);
    if (mo) body.maxOutputTokens = Number(mo);
    if (!body.id) { drawer.err = "模型 id 必填"; render(); return; }
    if (!body.url) { drawer.err = "接口地址 url 必填"; render(); return; }
    drawer.busy = true; drawer.err = "";
    render();
    try {
      var r = await postJson("/api/ai/models", body, 30000);
      toast(r.created ? "已新增模型：" + r.model.name : "已保存：" + r.model.name);
      drawer = null;
      sel = r.model.id;
      await load();
    } catch (e) {
      drawer.busy = false;
      drawer.err = e.message;
      render();
    }
  }

  function askDelete(id) {
    var m = byId(id);
    if (!m) return;
    var bk = (S.backups && S.backups[0]) ? S.backups[0].name : "（写入前会自动生成一份）";
    confirmBox = {
      kind: "delete", id: id, danger: true, okText: "删除「" + (m.name || id) + "」",
      title: "删除模型？",
      body: [
        "模型：" + (m.name || id) + "（" + id + "）",
        "只从 models.json 移除，不会吊销服务商那边的 key。",
        m.is_default ? "它是当前默认模型，删除后会切回「自动挑选」，回退链里的引用一并清掉。" : "回退链里对它的引用会被一并清掉。",
        "写入前会自动备份：" + bk
      ]
    };
    render();
  }

  async function doDelete() {
    var id = confirmBox.id;
    confirmBox = null;
    render();
    try {
      var r = await api("/api/ai/models/" + encodeURIComponent(id), { method: "DELETE" });
      toast("已删除 " + nameOf(id) + (r && !r.default ? "，默认模型已改为自动挑选" : ""));
      if (sel === id) sel = "";
      await load();
    } catch (e) {
      toast("删除失败：" + e.message, "error");
    }
  }

  async function askRestore(name) {
    confirmBox = {
      kind: "restore", name: name, danger: false, okText: "回滚到这份",
      title: "回滚 models.json？",
      body: [
        "将用备份 " + name + " 覆盖当前 models.json。",
        "回滚前会先把当前版本再备份一份，所以这一步可逆。",
        "正在跑的 AI 任务不受影响，但下一次调用会用回滚后的清单。"
      ]
    };
    render();
  }

  async function doRestore(name) {
    confirmBox = null;
    render();
    try {
      var r = await postJson("/api/ai/models/restore", { name: name }, 30000);
      toast("已回滚：" + (r && r.restored ? r.restored : name) + "，当前 " + ((r && r.count) || 0) + " 条");
      await load();
    } catch (e) {
      toast("回滚失败：" + e.message, "error");
    }
  }

  function askDisable() {
    confirmBox = {
      kind: "disable", danger: true, okText: "停用 AI",
      title: "停用 AI？",
      body: [
        "停用后这些功能会降级：一键修复的诊断与属性归因、提示词优化、产品图识别。",
        "已生成的结果不受影响；随时可以在这里重新启用。"
      ]
    };
    render();
  }

  /* ───────────── 事件 ───────────── */

  function onClick(e) {
    var b = e.target && e.target.closest ? e.target.closest("[data-act]") : null;
    if (!b) return;
    var act = b.getAttribute("data-act");
    var id = b.getAttribute("data-id") || "";

    if (act === "refresh") return load();
    if (act === "new") { drawer = { mode: "new", busy: false, err: "" }; return render(); }
    if (act === "edit") { drawer = { mode: "edit", id: id, busy: false, err: "" }; return render(); }
    if (act === "drawer-close") { drawer = null; return render(); }
    if (act === "drawer-save") return saveDrawer();
    if (act === "pick") { sel = id; return render(); }
    if (act === "filter") { filter = b.getAttribute("data-v"); return renderIndex(); }
    if (act === "win") { usageWin = b.getAttribute("data-v"); return render(); }
    if (act === "test") return testModel(id);
    if (act === "testall") return testAll();
    if (act === "purpose-revert") { draft.purposes = JSON.parse(JSON.stringify(S.purposes || {})); return render(); }
    if (act === "def") {
      draft.model = id;
      return saveCfg();
    }
    if (act === "del") return askDelete(id);

    if (act === "fb-add") {
      var s = document.getElementById("ai-fb-pick");
      if (!s) return;
      var v = s.value;
      if (!v) return;
      if ((draft.fallbacks || []).indexOf(v) >= 0) return;
      if ((draft.fallbacks || []).length >= 5) return toast("回退链最多 5 个", "error");
      draft.fallbacks.push(v);
      return render();
    }
    if (act === "fb-del") {
      draft.fallbacks = (draft.fallbacks || []).filter(function (x) { return x !== id; });
      return render();
    }
    if (act === "fb-up") return fbMove(id, -1);
    if (act === "fb-down") return fbMove(id, 1);
    if (act === "cfg-save") return saveCfg();
    if (act === "cfg-revert") { syncDraft(); return render(); }

    if (act === "copy-path") {
      var p = (S && S.path) || "";
      if (navigator.clipboard && p) {
        navigator.clipboard.writeText(p).then(function () { toast("路径已复制"); },
          function () { toast(p); });
      } else { toast(p); }
      return;
    }
    if (act === "restore") return askRestore(b.getAttribute("data-name"));
    if (act === "confirm-cancel") { confirmBox = null; return render(); }
    if (act === "confirm-ok") {
      var k = confirmBox && confirmBox.kind;
      if (k === "delete") return doDelete();
      if (k === "restore") return doRestore(confirmBox.name);
      if (k === "disable") {
        confirmBox = null;
        draft.enabled = false;
        render();
        return saveCfg();
      }
    }
    if (act === "toggle-enabled") return; // change 事件里处理
  }

  function onChange(e) {
    var t = e.target;
    if (!t) return;
    if (t.id === "ai-default") { draft.model = t.value; return render(); }
    if (t.id === "ai-enabled") {
      if (!t.checked) { t.checked = true; return askDisable(); }
      draft.enabled = true;
      render();
      return saveCfg();
    }
    if (t.getAttribute && t.getAttribute("data-act") === "purpose") {
      draft.purposes = draft.purposes || {};
      draft.purposes[t.getAttribute("data-key")] = t.value;
      return render();
    }
  }

  function onInput(e) {
    if (e.target && e.target.id === "ai-q") { q = e.target.value; return renderIndex(); }
  }

  /* 回退链拖拽排序 */
  var dragId = "";
  function onDragStart(e) {
    var li = e.target && e.target.closest ? e.target.closest(".ai-fb-item") : null;
    if (!li) return;
    dragId = li.getAttribute("data-id");
    li.classList.add("is-drag");
    if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", dragId); } catch (x) {} }
  }
  function onDragOver(e) {
    var li = e.target && e.target.closest ? e.target.closest(".ai-fb-item") : null;
    if (!li || !dragId) return;
    e.preventDefault();
    li.classList.add("is-over");
  }
  function onDragLeave(e) {
    var li = e.target && e.target.closest ? e.target.closest(".ai-fb-item") : null;
    if (li) li.classList.remove("is-over");
  }
  function onDrop(e) {
    var li = e.target && e.target.closest ? e.target.closest(".ai-fb-item") : null;
    if (!li || !dragId) return;
    e.preventDefault();
    var target = li.getAttribute("data-id");
    var arr = draft.fallbacks || [];
    var from = arr.indexOf(dragId), to = arr.indexOf(target);
    if (from >= 0 && to >= 0 && from !== to) {
      arr.splice(from, 1);
      arr.splice(to, 0, dragId);
    }
    dragId = "";
    render();
  }
  function onDragEnd() {
    dragId = "";
    var els = document.querySelectorAll("#view-aiconf .ai-fb-item");
    for (var i = 0; i < els.length; i++) els[i].classList.remove("is-drag", "is-over");
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (confirmBox) { confirmBox = null; render(); return; }
    if (drawer) { drawer = null; render(); }
  });

  /* ───────────── 对外 ───────────── */

  window.AIA = { load: load, mount: load };

  var h = host();
  if (h) {
    h.addEventListener("click", onClick);
    h.addEventListener("change", onChange);
    h.addEventListener("input", onInput);
    h.addEventListener("dragstart", onDragStart);
    h.addEventListener("dragover", onDragOver);
    h.addEventListener("dragleave", onDragLeave);
    h.addEventListener("drop", onDrop);
    h.addEventListener("dragend", onDragEnd);
  }
  (function autoload() {
    var v = document.getElementById("view-aiconf");
    if (v && v.classList.contains("active")) load();
  })();
})();
