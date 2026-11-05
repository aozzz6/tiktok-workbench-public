/* ============================================================
   bench.js · 10 个功能单元（window.BU）
   所有布局共用；每块外层带 data-unit，验收脚本按它断言
   ============================================================ */
(function () {
  const S = window.BS;

  const D0 = () => window.BENCHDATA;

  /* ── 6 个 KPI 的数据（全部来自 /api/benchmark） ─────────── */
  function kpiData(D) {
    const k = D.kpis, b = D.baselines;
    const likeRate = k.total_plays ? (k.total_likes / k.total_plays) * 100 : 0;
    const perVideo = k.videos ? k.total_plays / k.videos : 0;
    return [
      { key: 'acc', label: '监控账号', count: k.accounts, fmt: 'int', unit: '个',
        cap: D.accounts.length + ' 个有抓取快照', drill: 'acc' },
      { key: 'videos', label: '样本视频', count: k.videos, fmt: 'int', unit: '条',
        cap: '明细 ' + S.int(D.videos.length) + ' 条 · 台账补录 ' + k.ledger_only, drill: 'videos' },
      { key: 'plays', label: '总播放', count: k.total_plays, fmt: 'wan', unit: '万',
        cap: '均 ' + S.nf(perVideo) + ' / 条', drill: 'plays' },
      { key: 'likes', label: '总点赞', count: k.total_likes, fmt: 'int', unit: '',
        cap: '赞播比 ' + S.pct(likeRate, 2), drill: 'likes' },
      { key: 'eng', label: '平均互动率', count: k.avg_engagement, fmt: 'pct2', unit: '%',
        cap: '中位 ' + S.pct(b.medianEngagement, 2) + ' · 按播放加权', drill: 'eng' },
      { key: 'cov', label: '挂车率', count: k.product_coverage, fmt: 'pct1', unit: '%',
        cap: S.int(k.with_products) + ' 条带小黄车', drill: 'cov' }
    ];
  }

  /* ── 顶栏 ─────────────────────────────────────────────── */
  function stamps(D) {
    return '<span class="stamp"><i class="dot live"></i>快照 ' + S.esc(S.timeHM(D.meta.asOf)) + '</span>' +
      '<span class="stamp muted">' + D.meta.days + ' 天 · 只读</span>';
  }

  function actions() {
    return '<button class="btn ghost" data-act="refresh" title="重新读取 /api/benchmark">刷新</button>' +
      '<button class="btn ghost" data-act="export" title="导出结论文本">导出结论</button>';
  }

  function head(D, o = {}) {
    return '<header class="b-head ' + (o.cls || '') + '">' +
      '<div class="b-brand"><span class="b-mark">TT</span><div><b>对标分析</b>' +
      '<small>BENCH · ' + S.esc(D.meta.asOf) + '</small></div></div>' +
      '<div class="b-scope"><span class="chip acc">全站 · ' + D.kpis.accounts + ' 个账号</span>' +
      '<span class="b-scope-t tiny muted">' + S.int(D.videos.length) + ' 条视频 · ' + D.meta.days + ' 天</span></div>' +
      '<div class="b-acts">' + stamps(D) + actions() + '</div></header>';
  }

  /* ── KPI ─────────────────────────────────────────────── */
  function kpis(D, o = {}) {
    const list = kpiData(D);
    const v = o.variant || 'cards';
    if (v === 'cards') {
      return '<div class="b-kpis" data-unit="kpi">' + list.map((k) => S.kpi(k)).join('') + '</div>';
    }
    if (v === 'figures') {
      return '<div class="b-figs" data-unit="kpi">' + list.map((k) =>
        '<div class="b-fig" data-drill="' + k.key + '"><span class="b-fig-l">' + S.esc(k.label) + '</span>' +
        '<span class="b-fig-v tnum">' + S.num(k.count, k.fmt) + (k.unit ? '<i>' + k.unit + '</i>' : '') + '</span>' +
        '<span class="b-fig-c">' + S.esc(k.cap) + '</span></div>').join('') + '</div>';
    }
    if (v === 'mega') {
      return '<div class="b-mega" data-unit="kpi">' + list.map((k) =>
        '<div class="mega" data-drill="' + k.key + '"><div class="mega-l">' + S.esc(k.label) + '</div>' +
        '<div class="mega-v tnum">' + (k.count != null ? '<b data-count="' + k.count + '" data-fmt="' + k.fmt + '">0</b>' : S.esc(k.value)) +
        (k.unit ? '<i>' + k.unit + '</i>' : '') + '</div>' +
        '<div class="mega-c">' + S.esc(k.cap) + '</div></div>').join('') + '</div>';
    }
    if (v === 'rows') {
      return '<div class="b-krows" data-unit="kpi">' + list.map((k) =>
        '<button class="b-kr" data-drill="' + k.key + '"><span class="b-kr-l">' + S.esc(k.label) + '</span>' +
        '<b class="b-kr-v tnum">' + S.num(k.count, k.fmt) + (k.unit ? '<i>' + k.unit + '</i>' : '') + '</b>' +
        '<span class="b-kr-c">' + S.esc(k.cap) + '</span><span class="b-kr-a">›</span></button>').join('') + '</div>';
    }
    /* strip：一行读完，A4 报告 / A7 终端 用 */
    return '<div class="b-strip" data-unit="kpi">' + list.map((k, i) =>
      '<div class="b-strip-i" data-drill="' + k.key + '"><span>' + S.esc(k.label) + '</span><b class="tnum">' +
      S.num(k.count, k.fmt) + (k.unit ? '<i>' + k.unit + '</i>' : '') + '</b></div>' +
      (i < list.length - 1 ? '<i class="b-strip-s"></i>' : '')).join('') + '</div>';
  }

  /* ── 趋势 ─────────────────────────────────────────────── */
  function seriesSlice(D, n) {
    const s = D.series;
    return n && n > 0 ? s.slice(-n) : s.slice();
  }

  function trendInner(D, n, kind, h) {
    const s = seriesSlice(D, n);
    const pts = s.map((d) => ({ v: d.plays, tip: d.full + ' · 播放 ' + S.int(d.plays) + ' · 发布 ' + d.videos + ' 条' }));
    const vps = s.map((d) => ({ v: d.videos, tip: d.full + ' · 发布 ' + d.videos + ' 条' }));
    const main = kind === 'bars' ? S.bars(pts) : S.area(pts);
    const sub = kind === 'bars' ? S.area(vps) : S.bars(vps);
    const step = Math.max(1, Math.ceil(s.length / 6));
    const labels = s.map((d, i) => (i % step === 0 || i === s.length - 1) ? '<span>' + d.date + '</span>' : '<span></span>').join('');
    return '<div class="b-chart-in"><div class="b-chart" style="height:' + (h || 150) + 'px">' + main + '</div>' +
      '<div class="b-x">' + labels + '</div>' +
      '<div class="b-chart b-chart-sm">' + sub + '</div></div>';
  }

  function trend(D, o = {}) {
    const n = o.n || D.series.length;
    const kind = o.kind || 'area';
    const s = seriesSlice(D, n);
    // 空数据：没有任何带发布时间的视频时没有可画的趋势，
    // 不能去读 s[0].date / peak.date（undefined.date 会抛 TypeError）。
    if (!s.length) {
      return S.mod({
        key: 'trend',
        title: o.title || '每日发布与播放',
        sub: '暂无发布数据',
        cls: 'b-trend ' + (o.cls || ''),
        body: '<p class="muted b-empty">暂无发布数据（还没有抓取到带发布时间的视频）</p>',
        foot: o.foot
      });
    }
    const peak = s.reduce((m, d) => (d.plays > m.plays ? d : m), s[0]);
    const total = s.reduce((a, d) => a + d.videos, 0);
    return S.mod({
      key: 'trend',
      title: o.title || '每日发布与播放',
      sub: o.sub || (s[0].date + ' → ' + s[s.length - 1].date + ' · ' + s.length + ' 天'),
      cls: 'b-trend ' + (o.cls || ''),
      actions: '<div class="seg" role="group" aria-label="时间范围">' +
        '<button class="sg" data-period="7">7 天</button>' +
        '<button class="sg' + (s.length === 14 ? ' on' : '') + '" data-period="14">14 天</button>' +
        '<button class="sg' + (s.length > 14 ? ' on' : '') + '" data-period="0">全部</button></div>',
      body: '<div data-chart="trend" data-kind="' + kind + '" data-h="' + (o.h || 150) + '">' + trendInner(D, n, kind, o.h) + '</div>' +
        '<div class="b-legend"><span><i class="b-lg"></i>' + (kind === 'bars' ? '播放（柱，累计值）' : '播放（面积，累计值）') + '</span>' +
        '<span><i class="b-lg b-lg-b"></i>' + (kind === 'bars' ? '发布条数（面积）' : '发布条数（柱）') + '</span>' +
        '<span class="muted">峰值 ' + peak.date + ' · ' + S.nf(peak.plays) + '</span>' +
        '<span class="muted">发布合计 ' + S.int(total) + ' 条</span></div>',
      foot: o.foot
    });
  }

  /* ── 账号维度对比 ─────────────────────────────────────── */
  /* 单账号近 n 天的播放序列（按发布时间归集） */
  function accDaily(D, name, n) {
    const days = D.series.slice(-n).map((d) => d.full);
    const map = new Map(days.map((d) => [d, 0]));
    D.videos.forEach((v) => {
      if (v.account === name && map.has(v.fullDay)) map.set(v.fullDay, map.get(v.fullDay) + v.plays);
    });
    return D.series.slice(-n).map((d) => ({ date: d.date, plays: map.get(d.full) || 0 }));
  }

  function accbars(D, o = {}) {
    const n = o.n || 10;
    const v = o.variant || 'multi';
    const byPlays = D.accounts.slice(0, n);

    if (v === 'matrix') {
      const rows = D.accounts.slice(0, o.rows || 20);
      const metrics = [
        { k: 'total_plays', t: '总播放', max: Math.max(...rows.map((a) => a.total_plays)) },
        { k: 'avg_plays', t: '均播', max: Math.max(...rows.map((a) => a.avg_plays)) },
        { k: 'avg_engagement', t: '互动', max: Math.max(...rows.map((a) => a.avg_engagement)) },
        { k: 'product_coverage', t: '挂车', max: Math.max(1, ...rows.map((a) => a.product_coverage)) },
        { k: 'recent7', t: '近 7 天', max: Math.max(1, ...rows.map((a) => a.recent7)) }
      ];
      const cell = (a, m) => {
        const r = Math.max(0, Math.min(1, (a[m.k] || 0) / m.max));
        const pc = Math.round(r * 74);
        const val = m.k === 'avg_engagement' ? S.pct(a[m.k], 2) : m.k === 'product_coverage' ? S.pct(a[m.k], 1) : S.int(a[m.k]);
        return '<i class="b-mx-c" style="background:color-mix(in oklab, var(--accent) ' + pc + '%, var(--line))" data-tip="' +
          S.esc(a.account + ' · ' + m.t + ' ' + val) + '"></i>';
      };
      return S.mod({
        key: 'accbars', title: o.title || '账号 × 指标 热力方格',
        sub: o.sub || (rows.length + ' 个账号 · 越浓越强'),
        cls: 'b-matrix ' + (o.cls || ''),
        body: '<div class="b-mx"><div class="b-mx-h"><span>#</span><span>账号</span>' +
          metrics.map((m) => '<span>' + m.t + '</span>').join('') + '<span>占比</span></div>' +
          rows.map((a) => '<div class="b-mx-r" data-acc="' + S.esc(a.account) + '">' +
            '<span class="b-mx-rank">' + a.rank + '</span>' +
            '<span class="b-mx-name">' + S.esc(a.account) + '</span>' +
            metrics.map((m) => cell(a, m)).join('') +
            '<b class="b-mx-sh tnum">' + S.pct(a.share) + '</b></div>').join('') + '</div>',
        foot: '行点开账号详情 · 方格浓度 = 该列内的相对强度'
      });
    }

    if (v === 'cards') {
      const rows = D.accounts.slice(0, o.n || 12);
      return S.mod({
        key: 'accbars', title: o.title || '账号卡墙',
        sub: o.sub || ('播放前 ' + rows.length + ' 个 · 各带 8 天走势'),
        cls: 'b-cards ' + (o.cls || ''),
        body: '<div class="b-wall">' + rows.map((a) =>
          '<button class="b-card' + (a.rank <= 3 ? ' top' : '') + '" data-acc="' + S.esc(a.account) + '">' +
          '<div class="b-card-h"><span class="b-card-r">' + a.rank + '</span><b>' + S.esc(a.account) + '</b>' +
          '<span class="tiny muted">' + a.lang.toUpperCase() + '</span></div>' +
          '<div class="b-card-v"><b class="tnum">' + S.nf(a.total_plays) + '</b>' +
          '<span class="tiny muted">播放 · 占比 ' + S.pct(a.share) + '</span></div>' +
          S.spark(accDaily(D, a.account, 8).map((d) => ({ v: d.plays })), { h: 22 }) +
          '<div class="b-card-f"><span>均播 ' + S.int(a.avg_plays) + '</span><span>互动 ' + S.pct(a.avg_engagement, 2) + '</span>' +
          '<span>挂车 ' + S.pct(a.product_coverage, 0) + '</span></div></button>').join('') + '</div>',
        foot: '点卡片开账号详情 · 条高 = 当天发布视频的播放合计'
      });
    }

    if (v === 'scatter') {
      const pts = D.accounts.map((a) => ({
        acc: a.account, x: a.avg_plays, y: a.avg_engagement,
        r: 3 + Math.sqrt(a.total_plays) / 240,
        tone: a.product_coverage >= 50 ? 'ok' : '',
        big: a.rank === 1, name: a.rank === 1 ? a.account : '',
        tip: a.account + ' · 均播 ' + S.int(a.avg_plays) + ' · 互动 ' + S.pct(a.avg_engagement, 2) +
          ' · 总播放 ' + S.nf(a.total_plays)
      }));
      return S.mod({
        key: 'accbars', title: o.title || '播放 × 互动 象限',
        sub: o.sub || '每个点一个账号 · 点大小 = 总播放',
        cls: 'b-qi ' + (o.cls || ''),
        body: S.scatter(pts, { mx: D.baselines.medianAvgPlays, my: D.baselines.medianEngagement, quad: true }),
        foot: '虚线 = 全站中位数（均播 ' + S.int(D.baselines.medianAvgPlays) + ' · 互动 ' + S.pct(D.baselines.medianEngagement, 2) + '）· 绿点 = 挂车率 ≥ 50%'
      });
    }

    if (v === 'ladder') {
      const items = byPlays.map((a) => ({
        label: a.account, acc: a.account, rank: a.rank, v: a.total_plays,
        txt: S.nf(a.total_plays),
        tip: '第 ' + a.rank + ' 名 · ' + S.int(a.total_plays) + ' 播放 · 占比 ' + S.pct(a.share) + ' · ' + a.videos + ' 条'
      }));
      return S.mod({
        key: 'accbars', title: o.title || '名次阶梯', sub: o.sub || ('总播放前 ' + n + ' / ' + D.accounts.length + ' 个账号'),
        cls: 'b-ladder ' + (o.cls || ''), body: S.hbars(items),
        foot: '中位 ' + S.nf(D.baselines.medianPlays) + ' · 均值 ' + S.nf(D.baselines.meanPlays)
      });
    }

    if (v === 'single') {
      const metric = o.metric || 'total_plays';
      const sorted = D.accounts.slice().sort((a, b) => b[metric] - a[metric]).slice(0, n);
      const isPct = metric === 'avg_engagement' || metric === 'product_coverage';
      const items = sorted.map((a) => ({
        label: a.account, acc: a.account, v: a[metric],
        txt: isPct ? S.pct(a[metric], 2) : S.int(a[metric]),
        tip: a.account + ' · ' + S.int(a.videos) + ' 条 · ' + S.pct(a.product_coverage)
      }));
      return S.mod({
        key: 'accbars', title: o.title || '账号维度对比', sub: o.sub || 'Top ' + n + ' · 按总播放',
        cls: o.cls || '', body: S.hbars(items)
      });
    }

    const groups = [
      { k: 'total_plays', t: '总播放', f: (x) => S.nf(x.total_plays) },
      { k: 'avg_plays', t: '平均播放', f: (x) => S.int(x.avg_plays) },
      { k: 'product_coverage', t: '挂车率', f: (x) => S.pct(x.product_coverage) }
    ];
    const body = '<div class="b-multi">' + groups.map((g) => {
      const rows = D.accounts.slice().sort((a, b) => b[g.k] - a[g.k]).slice(0, o.rows || 6);
      const max = Math.max(1, ...rows.map((a) => a[g.k]));
      return '<div class="b-mg"><div class="b-mg-h">' + g.t + '<span class="muted">Top ' + rows.length + '</span></div>' +
        rows.map((a) => '<div class="b-mg-r" data-acc="' + S.esc(a.account) + '" data-tip="' + S.esc(a.account + ' · ' + g.t + ' ' + g.f(a)) + '">' +
          '<span class="b-mg-n">' + S.esc(a.account) + '</span>' +
          '<span class="b-mg-b"><i style="width:' + (a[g.k] / max * 100).toFixed(1) + '%"></i></span>' +
          '<b class="tnum">' + g.f(a) + '</b></div>').join('') + '</div>';
    }).join('') + '</div>';

    return S.mod({
      key: 'accbars', title: o.title || '账号维度对比', sub: o.sub || '播放 / 均播 / 挂车率 · 各取前 6',
      cls: 'b-accbars ' + (o.cls || ''), body: body,
      foot: '中位：总播放 ' + S.nf(D.baselines.medianPlays) + ' · 均播 ' + S.int(D.baselines.medianAvgPlays) + ' · 挂车 ' + S.pct(D.baselines.medianCoverage)
    });
  }

  /* ── 账号对比表（可排序） ──────────────────────────────── */
  function accRow(a, variant) {
    const tone = a.product_coverage >= 50 ? 'acc' : '';
    if (variant === 'terminal') {
      return '<tr data-acc="' + S.esc(a.account) + '">' +
        '<td class="b-td-rank">' + String(a.rank).padStart(2, '0') + '</td>' +
        '<td class="b-td-acc">' + S.esc(a.account) + '</td>' +
        '<td class="r tnum">' + a.videos + '</td>' +
        '<td class="r tnum">' + S.int(a.total_plays) + '</td>' +
        '<td class="r tnum">' + S.int(a.avg_plays) + '</td>' +
        '<td class="r tnum">' + S.pct(a.avg_engagement, 2) + '</td>' +
        '<td class="r tnum">' + S.pct(a.product_coverage, 1) + '</td>' +
        '<td class="r muted tnum">' + a.lastPublish.slice(5, 16) + '</td></tr>';
    }
    return '<tr data-acc="' + S.esc(a.account) + '">' +
      '<td class="b-td-rank"><b>' + a.rank + '</b></td>' +
      '<td class="b-td-acc"><span class="dot ' + (a.has_scrape ? 'ok' : 'warn') + '"></span> <b>' + S.esc(a.account) + '</b>' +
      '<span class="tiny muted"> ' + a.lang.toUpperCase() + '</span></td>' +
      '<td class="r tnum">' + a.videos + '</td>' +
      '<td class="r tnum">' + S.int(a.total_plays) + '</td>' +
      '<td class="r tnum">' + S.int(a.avg_plays) + '</td>' +
      '<td class="r tnum">' + S.pct(a.avg_engagement, 2) + '</td>' +
      '<td class="r tnum"><span class="chip ' + tone + '">' + S.pct(a.product_coverage, 1) + '</span></td>' +
      '<td class="r muted tnum">' + a.lastPublish.slice(5, 16) + '</td></tr>';
  }

  const ACC_COLS = [
    ['rank', '名次', 'c'], ['account', '账号', ''], ['videos', '视频', 'r'],
    ['total_plays', '总播放', 'r'], ['avg_plays', '均播放', 'r'],
    ['avg_engagement', '互动率', 'r'], ['product_coverage', '挂车率', 'r'], ['lastPublish', '最近发布', 'r']
  ];

  function accTable(D, o = {}) {
    const n = o.n || 12;
    const variant = o.variant || 'table';
    const sort = o.sort || 'total_plays';
    const rows = D.accounts.slice().sort((a, b) => (b[sort] || 0) - (a[sort] || 0)).slice(0, n);
    const head = ACC_COLS.map(([k, label, cls]) =>
      '<th class="' + cls + (k === sort ? ' sorted' : '') + '" data-sort="' + k + '" title="点表头排序">' + label + '</th>').join('');
    return S.mod({
      key: 'acctable',
      title: o.title || '账号对比',
      sub: o.sub || (n + ' / ' + D.accounts.length + ' 个账号 · 点表头排序 · 点行开详情'),
      cls: 'b-accmod ' + (o.cls || ''),
      actions: '<button class="btn ghost sm" data-act="csvacc">导出 CSV</button>',
      body: '<div class="b-tablewrap"><table class="tb b-tb b-tb-acc" data-variant="' + variant + '" data-sort="' + sort + '">' +
        '<thead><tr>' + head + '</tr></thead><tbody>' +
        rows.map((a) => accRow(a, variant)).join('') + '</tbody></table></div>',
      foot: '<span>合计 ' + S.int(D.accounts.reduce((s, a) => s + a.total_plays, 0)) + ' 播放 · 47 个账号</span>' +
        '<span class="muted">按「' + (ACC_COLS.find((c) => c[0] === sort) || [, '名次'])[1] + '」降序</span>'
    });
  }

  /* ── 视频明细 ─────────────────────────────────────────── */
  function videoRow(v) {
    return '<tr data-vid="' + S.esc(v.vid) + '">' +
      '<td class="b-td-acc tiny">' + S.esc(v.account) + '</td>' +
      '<td class="b-td-title" title="' + S.esc(v.title) + '">' + S.esc(v.title || '（无标题）') + '</td>' +
      '<td class="r muted tnum tiny">' + v.day + '</td>' +
      '<td class="r tnum"><b>' + S.int(v.plays) + '</b></td>' +
      '<td class="r tnum">' + S.int(v.likes) + '</td>' +
      '<td class="r tnum">' + S.pct(v.engagement, 2) + '</td>' +
      '<td class="c">' + (v.products && v.products.length
        ? '<span class="chip acc" data-tip="' + S.esc(v.products[0].title) + '">已挂车</span>'
        : '<span class="tiny muted">—</span>') + '</td></tr>';
  }

  function videos(D, o = {}) {
    const n = o.n || 8;
    const rows = D.videos.slice().sort((a, b) => b.plays - a.plays).slice(0, n);
    return S.mod({
      key: 'videos',
      title: o.title || '视频明细',
      sub: o.sub || ('播放最高 ' + n + ' 条 · 共 ' + S.int(D.videos.length) + ' 条'),
      cls: 'b-vidmod ' + (o.cls || ''),
      actions: '<button class="btn ghost sm" data-act="csvvid">导出 CSV</button>',
      body: '<div class="b-filters"><span class="b-fl">全部时间</span><span class="b-fl">全部播放量</span>' +
        '<span class="b-fl">全部来源</span><span class="b-fl">按播放降序</span>' +
        '<span class="b-fl-hint muted">筛选条件已应用 · 共 ' + S.int(D.videos.length) + ' 条</span></div>' +
        '<div class="b-tablewrap"><table class="tb b-tb b-tb-vid"><thead><tr>' +
        '<th>账号</th><th>标题</th><th class="r">发布</th><th class="r">播放</th><th class="r">点赞</th><th class="r">互动率</th><th class="c">商品</th>' +
        '</tr></thead><tbody>' + rows.map(videoRow).join('') + '</tbody></table></div>',
      foot: '<span>1–' + n + ' / ' + S.int(D.videos.length) + ' · 第 1 页</span>' +
        '<span class="muted">抓取快照 ' + D.meta.from + ' → ' + D.meta.to + '</span>'
    });
  }

  /* ── 爆款规律 ─────────────────────────────────────────── */
  function patterns(D, o = {}) {
    const p = D.patterns;
    const buckets = p.play_buckets;
    const maxB = Math.max(1, ...buckets.map((b) => b.count));
    const bucketRows = buckets.map((b) =>
      '<div class="b-bk" data-tip="' + S.esc(b.label + '：' + b.count + ' 条 · ' + b.pct + '%') + '">' +
      '<span class="b-bk-l">' + S.esc(b.label) + '</span>' +
      '<span class="b-bk-b"><i style="width:' + (b.count / maxB * 100).toFixed(1) + '%"></i></span>' +
      '<b class="tnum">' + S.int(b.count) + '</b><span class="b-bk-p muted tnum">' + b.pct + '%</span></div>').join('');

    const tags = p.top_tags.slice(0, o.tags || 10).map((t) => S.tag(t.tag, t.count)).join('');
    const hooks = p.hook_words.slice(0, o.hooks || 10).map((h) => '<span class="b-hook">' + S.esc(h) + '</span>').join('');

    return S.mod({
      key: 'patterns',
      title: o.title || '爆款规律',
      sub: o.sub || ('播放分桶 · 标签 · 开场钩子 · 样本 ' + S.int(p.n_videos_in_sample)),
      cls: 'b-patmod ' + (o.cls || ''),
      body: '<div class="b-pat">' +
        '<div class="b-pat-c"><div class="b-pat-h">播放量分布<span class="muted">长尾</span></div>' + bucketRows + '</div>' +
        '<div class="b-pat-c"><div class="b-pat-h">高频话题标签<span class="muted">Top ' + Math.min(10, p.top_tags.length) + '</span></div>' +
        '<div class="b-tagcloud">' + tags + '</div></div>' +
        '<div class="b-pat-c"><div class="b-pat-h">文案开场钩子<span class="muted">前 3 个词</span></div>' +
        '<div class="b-hooks">' + hooks + '</div></div>' +
        '</div>',
      foot: '分桶按当前累计播放量 · 标签/钩子取标题与文案词频'
    });
  }

  /* ── 商品 TOP ─────────────────────────────────────────── */
  function products(D, o = {}) {
    const n = o.n || 5;
    const list = D.patterns.products_top.slice(0, n);
    const rows = list.map((p, i) =>
      '<div class="b-prod" data-prod="' + S.esc(p.product_id) + '">' +
      '<span class="b-prod-n">' + (i + 1) + '</span>' +
      '<div class="b-prod-b"><b title="' + S.esc(p.title) + '">' + S.esc(p.title) + '</b>' +
      '<span class="tiny muted">' + p.vids + ' 条视频 · ' + p.accounts.length + ' 个账号 · ' + S.int(p.total_plays) + ' 播放</span></div>' +
      '<a class="b-prod-a" href="' + S.esc(p.fastmoss_url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="FastMoss 商品页">↗</a></div>').join('');
    return S.mod({
      key: 'products', title: o.title || '被挂车最多的商品', sub: o.sub || ('Top ' + list.length + ' · 按挂载视频数'),
      cls: 'b-prodmod ' + (o.cls || ''), body: rows,
      foot: '商品名来自抓取快照 · 出单/GMV 未接入（FastMoss 积分耗尽）'
    });
  }

  /* ── 播放 TOP 视频 ────────────────────────────────────── */
  function topvideos(D, o = {}) {
    const n = o.n || 5;
    const list = D.videos.slice().sort((a, b) => b.plays - a.plays).slice(0, n);
    // 空列表时 list[0] 是 undefined，不能再读 list[0].plays。
    const rows = list.length ? list.map((v, i) =>
      '<div class="b-topv" data-vid="' + S.esc(v.vid) + '">' +
      '<span class="b-topv-n">' + (i + 1) + '</span>' +
      '<div class="b-topv-b"><b title="' + S.esc(v.title) + '">' + S.esc(v.title) + '</b>' +
      '<span class="tiny muted">' + S.esc(v.account) + ' · ' + v.day + ' · 互动 ' + S.pct(v.engagement, 2) + '</span></div>' +
      '<b class="b-topv-v tnum">' + S.nf(v.plays) + '</b></div>').join('')
      : '<p class="muted b-empty">暂无视频数据</p>';
    return S.mod({
      key: 'topvideos', title: o.title || '播放 TOP 视频',
      sub: o.sub || ('Top ' + list.length + (list.length ? ' · 单条最高 ' + S.nf(list[0].plays) : ' · 暂无数据')),
      cls: 'b-topvmod ' + (o.cls || ''), body: rows,
      foot: '播放为抓取时累计值'
    });
  }

  /* ── 可借鉴点 ─────────────────────────────────────────── */
  function takeaways(D, o = {}) {
    const v = o.variant || 'cards';
    const list = o.skip ? D.takeaways.slice(o.skip) : D.takeaways;
    if (v === 'list') {
      return S.mod({
        key: 'takeaways', title: o.title || '可借鉴点', sub: o.sub || '由播放 / 挂车 / 标签统计自动推导',
        cls: 'b-takemod ' + (o.cls || ''),
        body: '<ol class="b-take-list">' + list.map((t) =>
          '<li><span class="b-take-tx">' + S.esc(t.text) + '</span>' +
          '<span class="b-take-meta">' + S.esc(t.basis) + ' · ' + S.esc(t.confidence) + '</span></li>').join('') + '</ol>',
        foot: '结论由脚本按数据推导，非人工判断'
      });
    }
    return '<section class="b-take" data-unit="takeaways">' +
      '<div class="b-take-h"><h2>可借鉴点</h2><span class="hint">' + S.esc(o.sub || '自动推导 · 每条带依据') + '</span></div>' +
      '<div class="b-take-grid">' + list.map((t, i) =>
        '<article class="b-take-c"><span class="b-take-i">' + String(i + (o.skip || 0) + 1).padStart(2, '0') + '</span>' +
        '<p>' + S.esc(t.text) + '</p>' +
        '<footer><span>' + S.esc(t.basis) + '</span><span class="chip">' + S.esc(t.confidence) + '</span></footer></article>').join('') +
      '</div></section>';
  }

  /* ── 口径脚注 ─────────────────────────────────────────── */
  function foot(D, o = {}) {
    return '<footer class="b-foot ' + (o.cls || '') + '" data-unit="foot">' +
      '<div class="b-foot-l"><b>口径</b> 数据源 <code>' + S.esc(D.meta.source) + '</code> · 快照 ' + S.esc(D.meta.asOf) +
      ' · 覆盖 ' + S.esc(D.meta.from) + ' → ' + S.esc(D.meta.to) + '（' + D.meta.days + ' 天）</div>' +
      '<div class="b-foot-r">播放/点赞为抓取时累计值，按发布时间归集到当日；挂车率 = 带小黄车视频 / 样本视频；' +
      '出单与 GMV 未接入（' + S.esc(D.meta.gmvStatus) + '）；台账 ' + S.int(D.meta.ledgerRows) + ' 行，其中 ' +
      D.meta.ledgerOnly + ' 行仅台账无抓取。</div></footer>';
  }

  /* ── 抽屉内容 ─────────────────────────────────────────── */
  function rowsTable(head, body) {
    return '<table class="tb"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
  }

  function detail(key, D) {
    if (!key) return null;

    if (key.indexOf('acc:') === 0) {
      const name = key.slice(4);
      const a = D.accounts.find((x) => x.account === name);
      if (!a) return null;
      const vs = D.videos.filter((v) => v.account === name).sort((x, y) => y.plays - x.plays).slice(0, 5);
      const latest = D.videos.filter((v) => v.account === name)
        .sort((x, y) => (x.publish_time < y.publish_time ? 1 : -1))[0];
      return {
        t: a.account, s: a.lang.toUpperCase() + ' · 名次 ' + a.rank + ' / ' + D.accounts.length + ' · 占比 ' + S.pct(a.share),
        b: '<div class="dr-kv"><span>视频</span><b>' + a.videos + ' 条</b>' +
          '<span>总播放</span><b>' + S.int(a.total_plays) + '</b>' +
          '<span>平均播放</span><b>' + S.int(a.avg_plays) + '</b>' +
          '<span>互动率</span><b>' + S.pct(a.avg_engagement, 2) + '</b>' +
          '<span>挂车率</span><b>' + S.pct(a.product_coverage, 1) + '</b>' +
          '<span>最高单条</span><b>' + S.int(a.maxPlays) + '</b>' +
          '<span>近 7 天</span><b>' + a.recent7 + ' 条</b>' +
          '<span>最近发布</span><b>' + S.esc(a.lastPublish) + '</b></div>' +
          '<h3 class="dr-h">高频标签</h3><div class="b-tagcloud">' + (a.top_tags || []).slice(0, 8).map((t) => S.tag(t)).join('') + '</div>' +
          '<h3 class="dr-h">播放最高 5 条</h3>' +
          rowsTable('<th>标题</th><th class="r">播放</th><th class="r">互动</th>',
            vs.map((v) => '<tr><td class="b-td-title" title="' + S.esc(v.title) + '">' + S.esc(v.title) + '</td>' +
              '<td class="r tnum">' + S.int(v.plays) + '</td><td class="r tnum">' + S.pct(v.engagement, 2) + '</td></tr>').join('')) +
          '<p class="dr-p"><a class="btn ghost" href="' + S.esc(latest.url) + '" target="_blank" rel="noopener">打开最新视频 ↗</a></p>'
      };
    }

    if (key.indexOf('vid:') === 0) {
      const v = D.videos.find((x) => x.vid === key.slice(4));
      if (!v) return null;
      return {
        t: v.title ? v.title.slice(0, 46) : '（无标题）', s: v.account + ' · ' + v.fullDay + ' ' + v.publish_time.slice(11, 16),
        b: '<div class="dr-kv"><span>播放</span><b>' + S.int(v.plays) + '</b>' +
          '<span>点赞</span><b>' + S.int(v.likes) + '</b>' +
          '<span>评论</span><b>' + S.int(v.comments) + '</b>' +
          '<span>分享</span><b>' + S.int(v.shares) + '</b>' +
          '<span>收藏</span><b>' + S.int(v.collects) + '</b>' +
          '<span>互动率</span><b>' + S.pct(v.engagement, 2) + '</b>' +
          '<span>来源</span><b>' + (v.source === 'ledger' ? '台账补录' : '抓取快照') + '</b>' +
          '<span>商品</span><b>' + (v.products.length ? v.products.length + ' 个' : '未挂车') + '</b></div>' +
          '<h3 class="dr-h">文案</h3><p class="dr-p">' + S.esc(v.desc || v.title) + '</p>' +
          (v.products.length ? '<h3 class="dr-h">挂载商品</h3>' + v.products.map((p) =>
            '<p class="dr-p">' + S.esc(p.title) + '</p>').join('') : '') +
          '<p class="dr-p"><a class="btn ghost" href="' + S.esc(v.url) + '" target="_blank" rel="noopener">开原视频 ↗</a></p>'
      };
    }

    if (key.indexOf('prod:') === 0) {
      const p = D.patterns.products_top.find((x) => x.product_id === key.slice(5));
      if (!p) return null;
      return {
        t: p.title.slice(0, 40), s: p.vids + ' 条视频 · ' + p.accounts.length + ' 个账号',
        b: '<div class="dr-kv"><span>总播放</span><b>' + S.int(p.total_plays) + '</b>' +
          '<span>挂载视频</span><b>' + p.vids + ' 条</b>' +
          '<span>涉及账号</span><b>' + p.accounts.length + ' 个</b>' +
          '<span>商品 ID</span><b>' + S.esc(p.product_id) + '</b></div>' +
          '<h3 class="dr-h">标题</h3><p class="dr-p">' + S.esc(p.title) + '</p>' +
          '<h3 class="dr-h">挂载视频</h3>' +
          rowsTable('<th>账号</th><th>标题</th><th class="r">播放</th>',
            p.videos.map((v) => '<tr><td class="tiny">' + S.esc(v.account) + '</td>' +
              '<td class="b-td-title" title="' + S.esc(v.title) + '">' + S.esc(v.title) + '</td>' +
              '<td class="r tnum">' + S.int(v.plays) + '</td></tr>').join('')) +
          '<p class="dr-p"><a class="btn ghost" href="' + S.esc(p.fastmoss_url) + '" target="_blank" rel="noopener">FastMoss 商品页 ↗</a></p>'
      };
    }

    const k = kpiData(D).find((x) => x.key === key);
    if (k) {
      const top = D.accounts.slice(0, 12);
      return {
        t: k.label, s: S.num(k.count, k.fmt) + (k.unit || '') + ' · ' + k.cap,
        b: rowsTable('<th>账号</th><th class="r">视频</th><th class="r">总播放</th><th class="r">占比</th>',
          top.map((a) => '<tr><td>' + S.esc(a.account) + '</td><td class="r tnum">' + a.videos +
            '</td><td class="r tnum">' + S.int(a.total_plays) + '</td><td class="r tnum">' + S.pct(a.share) + '</td></tr>').join('')) +
          '<p class="dr-p muted">' + S.esc(D.meta.from) + ' → ' + S.esc(D.meta.to) + ' · 共 ' + D.accounts.length + ' 个账号</p>'
      };
    }
    return null;
  }

  window.BU = {
    head, stamps, actions, kpis, kpiData, trend, trendInner, seriesSlice,
    accbars, acctable: accTable, accRow, videos, videoRow, patterns, products, topvideos, takeaways, foot, detail
  };
})();

