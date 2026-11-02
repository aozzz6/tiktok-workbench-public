/* ============================================================
   ovlib.js · 图表 + 组件小库（无依赖，纯字符串拼 SVG / HTML）
   所有函数都不碰 DOM，方便 15 套布局自由组合
   ============================================================ */
window.OV = (function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf = (v, d = 1) => {
    const n = Number(v) || 0;
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(d) + ' 亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(d) + ' 万';
    return String(Math.round(n * 10) / 10);
  };
  const int = (v) => (Number(v) || 0).toLocaleString('zh-CN');
  const pct = (v) => (v == null ? '—' : Math.round(v) + '%');
  const sev = (s) => (s === 'error' ? 'bad' : s === 'warn' ? 'warn' : 'ok');
  const sevLabel = (s) => (s === 'error' ? '异常' : s === 'warn' ? '缺口' : '正常');
  const dur = (s) => {
    const n = Number(s) || 0;
    if (!n) return '—';
    if (n < 60) return n + 's';
    return Math.floor(n / 60) + 'm' + String(Math.round(n % 60)).padStart(2, '0') + 's';
  };

  /* ── 折线 / 面积 ───────────────────────────────────────── */
  function path(pts) { return pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' '); }

  function area(series, o = {}) {
    const w = o.w || 760, h = o.h || 190, pad = o.pad == null ? 8 : o.pad, bottom = o.bottom == null ? 22 : o.bottom;
    const val = o.get || ((d) => d.success), valL = o.label || '值';
    const vals = series.map(val);
    const max = Math.max(...vals) * 1.06 || 1, min = Math.min(...vals) * 0.9;
    const iw = w - pad * 2, ih = h - pad - bottom;
    const x = (i) => pad + (i / Math.max(1, series.length - 1)) * iw;
    const y = (v) => pad + ih - ((v - min) / (max - min || 1)) * ih;
    const pts = series.map((d, i) => [x(i), y(val(d))]);
    const line = path(pts);
    const gid = 'g' + Math.random().toString(36).slice(2, 8);
    const grid = [0, .25, .5, .75, 1].map((t) => pad + ih * t).map((yy) =>
      '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + yy.toFixed(1) + '" y2="' + yy.toFixed(1) + '" class="gl"/>').join('');
    const ticks = series.map((d, i) => (i % Math.ceil(series.length / 6) === 0 || i === series.length - 1)
      ? '<text x="' + Math.min(w - pad, Math.max(pad, x(i))).toFixed(1) + '" y="' + (h - 4) + '" class="ax" text-anchor="' +
        (i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle') + '">' + esc(String(d.date).slice(5)) + '</text>' : '').join('');
    const hits = series.map((d, i) => {
      const bw = iw / series.length;
      return '<rect x="' + (x(i) - bw / 2).toFixed(1) + '" y="0" width="' + bw.toFixed(1) + '" height="' + ih + '" fill="transparent" data-tip="' +
        esc(d.date + ' · ' + valL + ' ' + val(d) + (d.failed ? ' / 失败 ' + d.failed : '')) + '"/>' +
        '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(val(d)).toFixed(1) + '" r="2.4" class="pt"/>';
    }).join('');
    return '<svg class="ch ch-area" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="var(--accent)" stop-opacity=".34"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>' +
      grid +
      '<path d="' + line + ' L' + x(series.length - 1).toFixed(1) + ' ' + (pad + ih) + ' L' + pad + ' ' + (pad + ih) + ' Z" fill="url(#' + gid + ')"/>' +
      '<path d="' + line + '" class="ln"/>' + hits + ticks + '</svg>';
  }

  function bars(series, o = {}) {
    const w = o.w || 760, h = o.h || 190, pad = 8, bottom = 22;
    const val = o.get || ((d) => d.success);
    const max = Math.max(...series.map(val)) * 1.05 || 1;
    const iw = w - pad * 2, ih = h - pad - bottom;
    const bw = iw / series.length;
    const out = series.map((d, i) => {
      const x = pad + i * bw;
      const hh = (val(d) / max) * ih;
      const fh = (d.failed / max) * ih;
      return '<g data-tip="' + esc(d.date + ' · ' + (o.label || '值') + ' ' + val(d) + (d.failed ? ' / 失败 ' + d.failed : '')) + '">' +
        '<rect x="' + (x + bw * .18).toFixed(1) + '" y="' + (pad + ih - hh).toFixed(1) + '" width="' + (bw * .64).toFixed(1) + '" height="' + hh.toFixed(1) + '" rx="1.5" class="b1"/>' +
        (fh > 0 ? '<rect x="' + (x + bw * .18).toFixed(1) + '" y="' + (pad + ih - hh).toFixed(1) + '" width="' + (bw * .64).toFixed(1) + '" height="' + Math.min(fh, hh).toFixed(1) + '" rx="1.5" class="b2"/>' : '') +
        '</g>';
    }).join('');
    const ticks = series.map((d, i) => (i % Math.ceil(series.length / 7) === 0
      ? '<text x="' + Math.min(w - pad, Math.max(pad, pad + i * bw + bw / 2)).toFixed(1) + '" y="' + (h - 4) + '" class="ax" text-anchor="' +
        (i === 0 ? 'start' : 'middle') + '">' + esc(String(d.date).slice(5)) + '</text>' : '')).join('');
    return '<svg class="ch ch-bars" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' + out + ticks + '</svg>';
  }

  function steps(series, o = {}) {
    const w = o.w || 760, h = o.h || 150, pad = 8, bottom = 18;
    const val = o.get || ((d) => d.success);
    const max = Math.max(...series.map(val)) * 1.05 || 1, min = 0;
    const iw = w - pad * 2, ih = h - pad - bottom;
    let d = '';
    series.forEach((s, i) => {
      const x = pad + (i / Math.max(1, series.length - 1)) * iw;
      const y = pad + ih - ((val(s) - min) / max) * ih;
      d += (i === 0 ? 'M' + x + ' ' + y : 'H' + x + ' V' + y.toFixed(1));
    });
    return '<svg class="ch ch-steps" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><path d="' + d + '" class="ln"/></svg>';
  }

  function spark(series, o = {}) {
    const pts = series.slice(-(o.n || 14));
    const val = o.get || ((d) => d.success);
    const max = Math.max(1, ...pts.map(val));
    return '<div class="k-sp" aria-hidden="true">' + pts.map((d) =>
      '<i style="height:' + Math.max(2, Math.round((val(d) / max) * 15)) + 'px"' + (d.failed ? ' class="has-fail"' : '') + '></i>').join('') + '</div>';
  }

  /* ── 环 / 仪表 ─────────────────────────────────────────── */
  function ring(value, o = {}) {
    const size = o.size || 92, sw = o.sw || 7, r = (size - sw) / 2, c = 2 * Math.PI * r;
    const p = Math.max(0, Math.min(100, Number(value) || 0));
    const off = c * (1 - p / 100);
    const tone = o.tone || '';
    return '<svg class="ch ch-ring ' + tone + '" viewBox="0 0 ' + size + ' ' + size + '" style="width:' + size + 'px;height:' + size + 'px">' +
      '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" class="rk"/>' +
      '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" class="rv" stroke-dasharray="' + c.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1) + '" transform="rotate(-90 ' + size / 2 + ' ' + size / 2 + ')"/>' +
      (o.center || '') + '</svg>';
  }

  function gauge(value, o = {}) {
    const w = o.w || 150, h = o.h || 84, sw = o.sw || 9;
    const r = (w - sw) / 2 - 2, cx = w / 2, cy = h - 6;
    const p = Math.max(0, Math.min(100, Number(value) || 0));
    const len = Math.PI * r;
    const off = len * (1 - p / 100);
    return '<svg class="ch ch-gauge" viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto">' +
      '<path d="M' + (cx - r) + ' ' + cy + ' A' + r + ' ' + r + ' 0 0 1 ' + (cx + r) + ' ' + cy + '" class="gk"/>' +
      '<path d="M' + (cx - r) + ' ' + cy + ' A' + r + ' ' + r + ' 0 0 1 ' + (cx + r) + ' ' + cy + '" class="gv" stroke-dasharray="' + len.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1) + '"/>' +
      (o.center || '') + '</svg>';
  }

  /* ── 热力矩阵 ─────────────────────────────────────────── */
  function heat(rows, cols, get, o = {}) {
    const cw = o.cw || 16, ch = o.ch || 16, gap = o.gap == null ? 3 : o.gap;
    const w = cols.length * (cw + gap), h = rows.length * (ch + gap);
    const cells = [];
    rows.forEach((row, ri) => cols.forEach((col, ci) => {
      const v = Math.max(0, Math.min(100, get(row, col)));
      const tone = v >= 90 ? 'h4' : v >= 70 ? 'h3' : v >= 45 ? 'h2' : v > 0 ? 'h1' : 'h0';
      cells.push('<rect x="' + (ci * (cw + gap)) + '" y="' + (ri * (ch + gap)) + '" width="' + cw + '" height="' + ch + '" rx="2" class="' + tone +
        '" data-tip="' + esc(row.account + ' · ' + col.key + ' ' + Math.round(v) + '%') + '"/>');
    }));
    return '<svg class="ch ch-heat" viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto">' + cells.join('') + '</svg>';
  }

  function bullet(v, target, o = {}) {
    const w = o.w || 120, h = 6;
    const p = Math.max(0, Math.min(100, v));
    const tp = Math.max(0, Math.min(100, target || 90));
    return '<svg class="ch ch-bullet" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" style="width:100%;height:' + h + 'px">' +
      '<rect width="' + w + '" height="' + h + '" rx="2" class="bk"/>' +
      '<rect width="' + (w * p / 100).toFixed(1) + '" height="' + h + '" rx="2" class="bv"/>' +
      '<rect x="' + (w * tp / 100).toFixed(1) + '" width="1.5" height="' + h + '" class="bt"/></svg>';
  }

  function stack(parts, o = {}) {
    const total = parts.reduce((s, p) => s + p.v, 0) || 1;
    return '<div class="sk">' + parts.map((p) =>
      '<i class="' + (p.tone || '') + '" style="width:' + (p.v / total * 100).toFixed(2) + '%" data-tip="' + esc(p.k + ' ' + p.v) + '"></i>').join('') + '</div>';
  }

  /* ── HTML 组件 ───────────────────────────────────────── */
  const kpi = (o) => {
    const tone = o.tone ? ' is-' + o.tone : '';
    return '<button type="button" class="k' + tone + '" data-drill="' + esc(o.key || '') + '" title="' + esc((o.label || '') + ' · 查看明细') + '">' +
      '<div class="k-l">' + esc(o.label) + '<span class="arw" aria-hidden="true">' + (o.arrow || '⌄') + '</span></div>' +
      '<div class="k-v">' + (o.count != null ? '<b data-count="' + o.count + '">0</b>' : esc(o.value)) +
      (o.unit ? '<span class="u">' + esc(o.unit) + '</span>' : '') +
      (o.trend ? '<span class="k-tr ' + (o.trend[0] === '-' ? 'down' : '') + '">' + esc(o.trend) + '</span>' : '') + '</div>' +
      '<div class="k-c">' + esc(o.cap || '') + '</div>' +
      (o.spark ? spark(o.spark) : '') +
      (o.live ? '<div class="k-live" aria-hidden="true"></div>' : '') +
      '</button>';
  };

  const mod = (o) => '<section class="mod ' + (o.cls || '') + '"' + (o.key ? ' data-unit="' + esc(o.key) + '"' : '') + '>' +
    '<header class="mod-h"><h2>' + (o.title || '') + '</h2>' + (o.sub ? '<span class="hint">' + o.sub + '</span>' : '') +
    (o.actions ? '<div class="mod-a">' + o.actions + '</div>' : '') + '</header>' +
    '<div class="mod-b">' + o.body + '</div>' + (o.foot ? '<footer class="mod-f">' + o.foot + '</footer>' : '') + '</section>';

  const runRow = (r) => '<div class="rt" data-tip="' + esc(r.id + ' · ' + (r.account || '全局') + ' · exit ' + r.exit) + '">' +
    '<span class="dot ' + (r.status === 'failed' ? 'bad' : r.status === 'running' ? 'live' : 'ok') + '"></span>' +
    '<span class="t">' + esc(r.at) + '</span>' +
    '<span class="n">' + esc(r.title) + '</span>' +
    '<span class="d">' + esc(dur(r.duration)) + '</span></div>';

  const groupTile = (g) => '<div class="gt" data-tip="' + esc(g.name + ' · ' + g.accounts + ' 个账号 · 落地 ' + g.landing + '%') + '">' +
    '<div class="gt-h"><b>' + esc(g.name) + '</b><span class="tiny muted">' + g.accounts + ' 账号</span></div>' +
    '<div class="bar ' + (g.err ? 'bad' : g.warn ? 'warn' : 'ok') + '"><i style="width:' + g.landing + '%"></i></div>' +
    '<div class="gt-f"><span class="tiny muted">落地 ' + g.landing + '%</span>' +
    '<span class="tiny">' + (g.ok ? '<span class="dot ok"></span> ' + g.ok : '') + (g.warn ? ' <span class="dot warn"></span> ' + g.warn : '') + (g.err ? ' <span class="dot bad"></span> ' + g.err : '') + '</span></div></div>';

  const accRow = (a, opts = {}) => '<tr data-acc="' + esc(a.account) + '">' +
    '<td><span class="dot ' + sev(a.severity) + '"></span> <b>' + esc(a.account) + '</b>' + (opts.site ? ' <span class="tiny muted">' + esc(a.site) + '</span>' : '') + '</td>' +
    '<td class="c tiny muted">' + esc(a.lang.toUpperCase()) + '</td>' +
    '<td class="r tnum">' + a.videos + '</td>' +
    '<td class="r tnum">' + a.landing + '%</td>' +
    (opts.stages ? ['scrape', 'transcribe', 'ledger', 'products'].map((k) =>
      '<td class="c"><span class="mini ' + (a.stages[k] >= 90 ? 'ok' : a.stages[k] >= 60 ? 'warn' : 'bad') + '">' + a.stages[k] + '</span></td>').join('') : '') +
    '<td class="r"><span class="chip ' + sev(a.severity) + '">' + sevLabel(a.severity) + '</span></td></tr>';

  const stageBar = (a) => '<div class="stg">' + ['scrape', 'transcribe', 'ledger', 'products'].map((k) => {
    const v = a.stages[k], tone = v >= 90 ? 'ok' : v >= 60 ? 'warn' : 'bad';
    return '<i class="' + tone + '" data-tip="' + esc(a.account + ' · ' + k + ' ' + v + '%') + '"><b style="width:' + v + '%"></b></i>';
  }).join('') + '</div>';

  const transList = (D) => D.trans.byAccount.map((x) =>
    '<div class="tr-row"><span>' + esc(x.account) + '</span><b class="tnum">' + x.count + '</b></div>').join('');

  const diagLine = (D) => '<div class="dg">' +
    '<span class="dg-i ok"><i class="dot ok"></i>环境 ' + D.diag.envOk + '/' + D.diag.envTotal + ' 就绪</span>' +
    '<span class="dg-i">台账 <b>' + esc(D.diag.ledger) + '</b></span>' +
    '<span class="dg-i ' + (D.diag.channel.pct >= 90 ? 'ok' : 'warn') + '">抓取通道 ' + D.diag.channel.items + '/' + (D.diag.channel.items + D.diag.channel.errors) +
    '（' + D.diag.channel.pct + '%）</span>' +
    '<span class="dg-i muted">' + esc(D.diag.channel.proxy) + '</span>' +
    '<span class="dg-i warn">待译 ' + D.trans.total + ' 条 · 快照 ' + D.trans.ageMin + ' 分钟前</span></div>';

  return { esc, nf, int, pct, sev, sevLabel, dur, area, bars, steps, spark, ring, gauge, heat, bullet, stack, kpi, mod, runRow, groupTile, accRow, stageBar, transList, diagLine };
})();

/* 别名：避免和其它模块的 window.OV 抢名字 */
window.RVOV = window.OV;
