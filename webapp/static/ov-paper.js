/* ============================================================
   ov-paper.js · 只改「总览看板」这一屏：按 S041 重排成 左目录 + 右详情
   ------------------------------------------------------------
   · 保留现有全部功能块（待接管 / 流水线覆盖 / 分组看板 / 流水线状态表 / 最近运行）
     —— 只是把节点搬进面板，id 原样不动，app.js 的渲染与按钮照旧生效。
   · 新增两块：趋势折线、账号健康环（数据来自 /api/overview 的 day.series 与 accounts）。
   · 只作用于 #view-overview，其他视图一行不碰。
   ============================================================ */
(function () {
  var view = document.getElementById('view-overview');
  if (!view) return;
  var RM = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* 数字滚动：只在 0 → 目标值 之间过渡，保留原有单位/趋势标记节点 */
  function countUp(node, to, dur) {
    if (RM || !node || !isFinite(to)) { if (node) node.nodeValue = String(to); return; }
    var t0 = performance.now(), d = dur || 520;
    function step(t) {
      var k = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - k, 3);
      node.nodeValue = String(Math.round(to * e));
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  var elKpi = document.getElementById('ov-kpi');
  var elTk = document.getElementById('takeover-panel');
  var elStage = view.querySelector('.stage-panel');
  var elGrp = document.getElementById('grp-body') ? document.getElementById('grp-body').closest('section') : null;
  var elPipe = document.getElementById('pipe-panel');
  /* 2026-10-31 修复 P2：面板被搬进「账号」pane，但它的折叠标题 h2#pipe-fold
     留在原处 → 总览底部多出一个空标题「▾ 流水线状态」，点了没反应。 */
  var elPipeHead = document.getElementById('pipe-fold');
  var elIdle = document.getElementById('idle-wrap');
  var elOps = null;
  var sections = view.querySelectorAll('section.panel');
  for (var i = 0; i < sections.length; i++) {
    if (sections[i].querySelector('#recent-runs')) { elOps = sections[i]; break; }
  }
  if (!elKpi || !elTk) return;              // 结构不符就不动，避免把页面弄坏

  /* ── 外壳 ── */
  var md = document.createElement('div');
  md.className = 'ov-md';

  var rail = document.createElement('aside');
  rail.className = 'ov-rail';
  rail.innerHTML =
    '<div class="ov-rail-h"><b>总览目录</b><span id="ov-rail-sub">正在读取…</span></div>' +
    '<div class="ov-nav" id="ov-nav">' +
      '<button data-pane="sum" class="on">概览<span class="ov-nav-n" id="ov-n-sum"></span></button>' +
      '<button data-pane="acc">账号<span class="ov-nav-n" id="ov-n-acc"></span></button>' +
      '<button data-pane="run">运行<span class="ov-nav-n" id="ov-n-run"></span></button>' +
      '<button data-pane="trans">待译<span class="ov-nav-n" id="ov-n-trans"></span></button>' +
      '<button data-pane="diag">诊断<span class="ov-nav-n" id="ov-n-diag"></span></button>' +
    '</div>' +
    '<div class="ov-acc-h">账号速览 <span id="ov-acc-hint">按落地率</span></div>' +
    '<div class="ov-acc-list" id="ov-acc-list"></div>';

  var panes = document.createElement('div');
  panes.className = 'ov-panes';
  function pane(name, title, sub) {
    var s = document.createElement('section');
    s.className = 'ov-pane' + (name === 'sum' ? ' on' : '');
    s.dataset.pane = name;
    if (title) {
      var h = document.createElement('div');
      h.className = 'ov-pane-h';
      h.innerHTML = '<h2>' + title + '</h2>' + (sub ? '<span>' + sub + '</span>' : '');
      s.appendChild(h);
    }
    panes.appendChild(s);
    return s;
  }
  var pSum = pane('sum');
  var pAcc = pane('acc', '账号', '分组 → 账号 → 四阶段覆盖');
  var pRun = pane('run');   // 2026-10-31：标题由搬家进来的面板自带（「运行记录」）
  var pTrans = pane('trans', '待翻译队列', '按账号分布 · 队列龄期');
  var pDiag = pane('diag', '环境诊断', '本地检查项与抓取通道');

  /* 新增两块容器（趋势 + 健康环），先插进概览面板，数据回填 */
  var row2 = document.createElement('div');
  row2.className = 'ov-row2';
  row2.innerHTML =
    '<section class="ov-card ov-trend">' +
      '<div class="ov-card-h"><h2 id="ov-trend-title">每日执行 · 近 7 天</h2><span id="ov-trend-sub"></span></div>' +
      '<div id="ov-trend-body"></div>' +
      '<div class="ov-trend-foot"><span><i class="dot ok"></i>成功</span><span><i class="dot bad"></i>失败</span>' +
        '<span id="ov-trend-foot" style="margin-left:auto"></span></div>' +
    '</section>' +
    '<section class="ov-card">' +
      '<div class="ov-card-h"><h2>账号健康</h2><span id="ov-ring-sub"></span></div>' +
      '<div class="ov-ring-wrap">' +
        '<div class="ov-ring" id="ov-ring"></div>' +
        '<div class="ov-legend" id="ov-legend"></div>' +
      '</div>' +
      '<div class="ov-ring-note" id="ov-ring-note"></div>' +
    '</section>';

  /* ── 搬家 ── */
  pSum.appendChild(elKpi);
  pSum.appendChild(row2);
  pSum.appendChild(elTk);
  if (elStage) pSum.appendChild(elStage);
  if (elGrp) pAcc.appendChild(elGrp);
  if (elPipe) pAcc.appendChild(elPipe);
  if (elPipeHead) elPipeHead.style.display = 'none';   /* 标题随面板走，别留空壳 */
  if (elIdle) pAcc.appendChild(elIdle);
  if (elOps) pRun.appendChild(elOps);

  md.appendChild(rail);
  md.appendChild(panes);
  var anchor = document.getElementById('ov-alert') || document.getElementById('diag-bar');
  if (anchor && anchor.parentNode === view) view.insertBefore(md, anchor.nextSibling);
  else view.appendChild(md);

  /* ── 面板切换 ── */
  var nav = rail.querySelector('.ov-nav');
  function show(name) {
    [].forEach.call(nav.children, function (b) { b.classList.toggle('on', b.dataset.pane === name); });
    [].forEach.call(panes.children, function (p) { p.classList.toggle('on', p.dataset.pane === name); });
  }
  nav.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-pane]');
    if (b) show(b.dataset.pane);
  });

  /* ── 数据回填 ── */
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function sevName(s) { return s === 'error' ? 'bad' : s === 'warn' ? 'warn' : 'ok'; }

  function drawTrend(series) {
    var n = series.length;
    if (!n) { document.getElementById('ov-trend-body').innerHTML = '<div class="ov-empty">暂无趋势数据</div>'; return; }
    var W = 720, H = 190, pad = 22, bottom = 22;
    var vals = series.map(function (d) { return d.success || 0; });
    var max = Math.max.apply(null, vals) * 1.08 || 1;
    var min = Math.min.apply(null, vals) * 0.92;
    var iw = W - pad * 2, ih = H - pad - bottom;
    var X = function (i) { return pad + (i / Math.max(1, n - 1)) * iw; };
    var Y = function (v) { return pad + ih - ((v - min) / (max - min || 1)) * ih; };
    var pts = series.map(function (d, i) { return [X(i), Y(d.success || 0)]; });
    var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    var grid = [0, .25, .5, .75, 1].map(function (t) { var y = pad + ih * t; return '<line class="gl" x1="' + pad + '" x2="' + (W - pad) + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '"/>'; }).join('');
    var step = Math.ceil(n / 7);
    var ticks = series.map(function (d, i) {
      var anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
      return (i % step === 0 || i === n - 1) ? '<text class="ax" x="' + X(i).toFixed(1) + '" y="' + (H - 4) + '" text-anchor="' + anchor + '">' + esc(d.date) + '</text>' : '';
    }).join('');
    var dots = pts.map(function (p, i) {
      return '<circle class="pt" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="2.4">' +
        '<title>' + esc(series[i].date + ' · 成功 ' + series[i].success + (series[i].failed ? ' / 失败 ' + series[i].failed : '')) + '</title></circle>';
    }).join('');
    var areaD = line + ' L' + X(n - 1).toFixed(1) + ' ' + (pad + ih) + ' L' + pad + ' ' + (pad + ih) + ' Z';
    var body = document.getElementById('ov-trend-body');
    body.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="每日执行趋势">' +
      grid + '<path class="ar" d="' + areaD + '" fill="rgba(138,90,59,.10)" opacity="0"/>' +
      '<path class="ln" d="' + line + '"/>' + dots + ticks + '</svg>';
    var lnEl = body.querySelector('.ln'), arEl = body.querySelector('.ar');
    if (!RM && lnEl) {
      try {
        var L = lnEl.getTotalLength();
        lnEl.style.strokeDasharray = L;
        lnEl.style.strokeDashoffset = L;
        requestAnimationFrame(function () { requestAnimationFrame(function () { lnEl.style.strokeDashoffset = 0; }); });
      } catch (e) {}
    }
    if (!RM && arEl) requestAnimationFrame(function () { arEl.style.opacity = '1'; });
    var sum = vals.reduce(function (a, b) { return a + b; }, 0);
    var mean = Math.round(sum / n);
    var peakIdx = vals.indexOf(Math.max.apply(null, vals));
    document.getElementById('ov-trend-title').textContent = '每日执行 · 近 ' + n + ' 天';
    document.getElementById('ov-trend-sub').textContent = '成功 ' + (series[n - 1].success || 0) + ' · 失败 ' + (series[n - 1].failed || 0);
    document.getElementById('ov-trend-foot').textContent =
      '峰值 ' + vals[peakIdx] + '（' + series[peakIdx].date + '）· 均值 ' + mean + ' / 日';
  }

  function drawRing(accs) {
    var ok = accs.filter(function (a) { return a.severity === 'ok'; }).length;
    var warn = accs.filter(function (a) { return a.severity === 'warn'; }).length;
    var bad = accs.filter(function (a) { return a.severity === 'error'; }).length;
    var total = accs.length || 1;
    var okPct = Math.round(ok / total * 100);
    var landing = Math.round(accs.reduce(function (s, a) { return s + (a.landing || 0); }, 0) / total);
    var R = 46, C = 2 * Math.PI * R;
    var ringEl = document.getElementById('ov-ring');
    ringEl.innerHTML =
      '<svg width="120" height="120" viewBox="0 0 120 120" role="img" aria-label="账号健康 ' + okPct + '%">' +
        '<circle class="rk" cx="60" cy="60" r="' + R + '"/>' +
        '<circle class="rv" cx="60" cy="60" r="' + R + '" stroke-dasharray="0 ' + C.toFixed(1) + '" transform="rotate(-90 60 60)"/>' +
        '<text class="rk-v" x="60" y="58" text-anchor="middle">0</text>' +
        '<text class="rk-l" x="60" y="74" text-anchor="middle">正常 / ' + total + '</text>' +
      '</svg>';
    var arcEl = ringEl.querySelector('.rv'), rvTxt = ringEl.querySelector('.rk-v');
    if (!RM && arcEl) requestAnimationFrame(function () {
      requestAnimationFrame(function () { arcEl.setAttribute('stroke-dasharray', (C * okPct / 100).toFixed(1) + ' ' + C.toFixed(1)); });
    });
    if (rvTxt && rvTxt.firstChild) countUp(rvTxt.firstChild, ok, 620);
    document.getElementById('ov-legend').innerHTML =
      '<div><span class="dot ok"></span>正常<b>' + ok + '</b></div>' +
      '<div><span class="dot warn"></span>缺口<b>' + warn + '</b></div>' +
      '<div><span class="dot bad"></span>异常<b>' + bad + '</b></div>';
    document.getElementById('ov-ring-sub').textContent = okPct + '% 健康';
    document.getElementById('ov-ring-note').textContent = '平均落地 ' + landing + '% · 目标 90%';
  }

  function fill(d) {
    var accs = (d.accounts || []).filter(function (a) { return a.enabled !== false; });
    var ok = accs.filter(function (a) { return a.severity === 'ok'; }).length;
    var warn = accs.filter(function (a) { return a.severity === 'warn'; }).length;
    var err = accs.filter(function (a) { return a.severity === 'error'; }).length;
    var day = d.day || {}, tr = d.pending_translation || {}, dg = d.diagnostics || {};
    var runs = d.runs || [];

    document.getElementById('ov-rail-sub').textContent =
      (day.total || 0) + ' 次执行 · ' + (day.success || 0) + ' 成功 · ' + (err + warn) + ' 个待处理';
    document.getElementById('ov-n-sum').textContent = (day.total || 0) + ' 次';
    document.getElementById('ov-n-acc').textContent = accs.length + ' 个';
    document.getElementById('ov-n-run').textContent = runs.length + ' 条';
    document.getElementById('ov-n-trans').textContent = (tr.total || 0) + ' 条';

    var checks = [
      ['pipeline 目录', dg.pipeline_exists], ['解释器 .venv', dg.python_exists],
      ['accounts.json', dg.accounts_exists], ['产物目录', dg.output_exists],
      ['爆品 Cookie', dg.cookie_exists], ['WorkBuddy', dg.workbuddy_exists]
    ];
    document.getElementById('ov-n-diag').textContent =
      checks.filter(function (c) { return c[1]; }).length + '/' + checks.length;

    /* 左栏账号速览 */
    document.getElementById('ov-acc-hint').textContent = accs.length + ' 个 · 按落地率';
    document.getElementById('ov-acc-list').innerHTML = accs.slice()
      .sort(function (a, b) { return (a.landing || 0) - (b.landing || 0); })
      .map(function (a) {
        var v = Math.round(a.landing || 0);
        return '<div class="ov-acc"><span class="ov-acc-n">' + esc(a.account) + '</span>' +
          '<span class="ov-acc-bar"><i class="' + sevName(a.severity) + '" data-w="' + v + '" style="width:0"></i></span>' +
          '<span class="ov-acc-v">' + v + '%</span></div>';
      }).join('');

    /* 进度条从 0 长到目标值（先渲染 0 宽，再在下一帧给真实宽度，交给 CSS 过渡） */
    if (!RM) requestAnimationFrame(function () {
      var bars = document.querySelectorAll('#ov-acc-list .ov-acc-bar i');
      [].forEach.call(bars, function (b) { b.style.width = (b.dataset.w || 0) + '%'; });
    });

    drawTrend(day.series || []);
    drawRing(accs);

    /* 待译 */
    var items = (tr.by_account || []).map(function (x) {
      return '<div class="ov-acc"><span class="ov-acc-n">' + esc(x.account) + '</span><span class="ov-acc-v">' + (x.count || 0) + '</span></div>';
    }).join('');
    pTrans.innerHTML =
      '<div class="ov-kv"><span>待译条数</span><b>' + (tr.total || 0) + ' 条</b>' +
      '<span>核对状态</span><b>' + (tr.ok === false ? '有异常' : '正常') + '</b>' +
      '<span>回填规则</span><b>巡检 15 分钟内回填；超过 2 小时算异常</b></div>' +
      (items ? '<div style="margin-top:12px">' + items + '</div>'
             : '<div class="ov-empty">当前队列为空 —— 没有等待回填的译文。</div>');

    /* 诊断 */
    pDiag.innerHTML =
      '<div class="ov-checks">' + checks.map(function (c) {
        return '<div class="ov-check"><span class="dot ' + (c[1] ? 'ok' : 'bad') + '"></span>' + c[0] +
          '<span class="muted">' + (c[1] ? '就绪' : '缺失') + '</span></div>';
      }).join('') + '</div>' +
      '<div class="ov-kv" style="margin-top:14px">' +
        '<span>台账文件</span><b>' + esc(String(dg.ledger || '—').split(/[\\/]/).pop()) + '</b>' +
        '<span>抓取通道</span><b>' + (d.scrape_health
          ? (d.scrape_health.items + ' / ' + (d.scrape_health.items + d.scrape_health.errors) +
             ' · ' + Math.round((d.scrape_health.ratio || 0) * 100) + '%') : '—') + '</b>' +
        '<span>代理</span><b>' + esc((d.scrape_health && d.scrape_health.proxy) || dg.proxy || '—') + '</b>' +
        '<span>数据时间</span><b>' + esc((d.freshness && d.freshness.as_of_text) || '—') + '</b>' +
      '</div>';
  }

  fetch('/api/overview', { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d) fill(d); })
    .catch(function () {});

  /* KPI 数字滚动：只在首次真实渲染时跑一次，避免 15 秒轮询反复跳动 */
  (function () {
    var kpi = document.getElementById('ov-kpi');
    if (!kpi || RM) return;
    var done = false;
    var io = new MutationObserver(function () {
      if (done) return;
      if (kpi.querySelector('.is-skel')) return;
      var vals = kpi.querySelectorAll('.kpi-v');
      if (!vals.length) return;
      done = true;
      io.disconnect();
      [].forEach.call(vals, function (v) {
        var t = v.firstChild;
        if (!t || t.nodeType !== 3) return;
        var m = /^\s*(\d+)/.exec(t.nodeValue || '');
        if (!m) return;
        var to = parseInt(m[1], 10);
        if (!to) return;
        t.nodeValue = '0';
        countUp(t, to, 560);
      });
    });
    io.observe(kpi, { childList: true, subtree: true });
  })();

  /* 概览点账号速览 → 切到账号面板 */
  document.getElementById('ov-acc-list').addEventListener('click', function () { show('acc'); });
})();
