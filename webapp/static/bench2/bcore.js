/* ============================================================
   bcore.js · 对标分析组件小库（window.BS）
   纯字符串拼 HTML / SVG，不碰 DOM；10 个架构共用
   图表全部内联 SVG / 纯 CSS，零外部请求
   ============================================================ */
window.BS = (function () {
  /* ── 格式化 ───────────────────────────────────────────── */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const int = (v) => Math.round(Number(v) || 0).toLocaleString('en-US');

  const nf = (v, d = 1) => {
    const n = Number(v) || 0;
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(d) + ' 亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(d) + ' 万';
    return int(n);
  };

  const num = (v, fmt) => {
    if (fmt === 'wan') return (Number(v) / 1e4).toFixed(1);
    if (fmt === 'pct1') return Number(v).toFixed(1);
    if (fmt === 'pct2') return Number(v).toFixed(2);
    return int(v);
  };

  const pct = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d) + '%');
  const ratio = (v, d = 1) => (Number(v) || 0).toFixed(d);

  /* 数值 → 千分位 + 万位缩写并存，表格里用 int，卡面用 nf */
  const dateMD = (s) => String(s || '').slice(5, 10);
  const timeHM = (s) => String(s || '').slice(11, 16);

  const sev = (x) => (x === 'bad' || x === 'error' ? 'bad' : x === 'warn' ? 'warn' : 'ok');

  /* ── SVG 基础 ─────────────────────────────────────────── */
  const svg = (inner, o = {}) => '<svg class="' + (o.cls || 'ch') + '" viewBox="' + (o.w || 100) + ' ' + (o.h || 100) +
    '" preserveAspectRatio="none" role="img" aria-label="' + esc(o.label || 'chart') + '">' + inner + '</svg>';

  /* 面积/折线：坐标用 0–100 的百分比空间 + non-scaling-stroke，任意容器不变形 */
  function area(pts, o = {}) {
    const vals = pts.map((p) => Number(p.v) || 0);
    const max = Math.max(1, ...vals);
    const min = Math.min(...vals) * .6;
    const x = (i) => (pts.length < 2 ? 0 : (i / (pts.length - 1)) * 100);
    const y = (v) => 100 - ((v - min) / (max - min || 1)) * 100;
    const line = pts.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(2) + ' ' + y(p.v).toFixed(2)).join(' ');
    const gid = 'bg' + Math.random().toString(36).slice(2, 8);
    const grid = [0, 25, 50, 75, 100].map((t) =>
      '<line x1="0" x2="100" y1="' + t + '" y2="' + t + '" class="gl" vector-effect="non-scaling-stroke"/>').join('');
    const hits = pts.map((p, i) => {
      const bw = 100 / Math.max(1, pts.length);
      return '<rect x="' + (x(i) - bw / 2).toFixed(2) + '" y="0" width="' + bw.toFixed(2) + '" height="100" fill="transparent" ' +
        (p.tip ? 'data-tip="' + esc(p.tip) + '"' : '') + '/>';
    }).join('');
    return '<svg class="ch ch-area" viewBox="0 0 100 100" preserveAspectRatio="none">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="var(--accent)" stop-opacity=".32"/>' +
      '<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>' +
      grid +
      '<path d="' + line + ' L100 100 L0 100 Z" fill="url(#' + gid + ')" stroke="none"/>' +
      '<path d="' + line + '" class="ln" vector-effect="non-scaling-stroke"/>' +
      hits + '</svg>';
  }

  /* 柱状：发布量按天 */
  function bars(pts, o = {}) {
    const max = Math.max(1, ...pts.map((p) => Number(p.v) || 0));
    const bw = 100 / Math.max(1, pts.length);
    const inner = pts.map((p, i) => {
      const hh = (Number(p.v) || 0) / max * 100;
      return '<rect x="' + (i * bw + bw * .2).toFixed(2) + '" y="' + (100 - hh).toFixed(2) + '" width="' + (bw * .6).toFixed(2) +
        '" height="' + hh.toFixed(2) + '" class="b1" ' + (p.tip ? 'data-tip="' + esc(p.tip) + '"' : '') + '/>';
    }).join('');
    return '<svg class="ch ch-bars" viewBox="0 0 100 100" preserveAspectRatio="none">' + inner + '</svg>';
  }

  /* 迷你柱（KPI 里的 sparkline） */
  function spark(pts, o = {}) {
    const max = Math.max(1, ...pts.map((p) => Number(p.v) || 0));
    return '<span class="b-sp">' + pts.map((p) =>
      '<i style="height:' + Math.max(2, Math.round((p.v / max) * (o.h || 16))) + 'px"></i>').join('') + '</span>';
  }

  /* 横向条（账号维度对比），纯 HTML，文字不拉伸 */
  function hbars(items, o = {}) {
    const max = Math.max(1, ...items.map((x) => Number(x.v) || 0));
    return '<div class="b-hbars">' + items.map((x) =>
      '<div class="b-hb' + (x.on ? ' on' : '') + '"' + (x.acc ? ' data-acc="' + esc(x.acc) + '"' : '') + ' data-tip="' + esc(x.tip || x.label + ' ' + x.txt) + '">' +
      (x.rank != null ? '<span class="b-hb-r">' + x.rank + '</span>' : '') +
      '<span class="b-hb-n">' + esc(x.label) + '</span>' +
      '<span class="b-hb-b"><i style="width:' + ((Number(x.v) || 0) / max * 100).toFixed(1) + '%"></i></span>' +
      '<b class="b-hb-v tnum">' + esc(x.txt) + '</b></div>').join('') + '</div>';
  }

  /* 环 */
  function ring(v, o = {}) {
    const size = o.size || 92, sw = o.sw || 7, r = (size - sw) / 2, c = 2 * Math.PI * r;
    const p = Math.max(0, Math.min(100, Number(v) || 0));
    const off = c * (1 - p / 100);
    return '<svg class="ch ch-ring" viewBox="0 0 ' + size + ' ' + size + '" style="width:' + size + 'px;height:' + size + 'px">' +
      '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" class="rk"/>' +
      '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" class="rv" stroke-dasharray="' + c.toFixed(1) +
      '" stroke-dashoffset="' + off.toFixed(1) + '" transform="rotate(-90 ' + size / 2 + ' ' + size / 2 + ')"/>' +
      (o.center || '') + '</svg>';
  }

  /* 散点（A10 象限）：x 用对数轴，避免长尾挤成一坨；固定 viewBox 等比缩放 */
  function scatter(pts, o = {}) {
    const w = o.w || 660, h = o.h || 380, pl = 62, pr = 20, pt = 26, pb = 46;
    const xs = pts.map((p) => Math.max(1, p.x)), ys = pts.map((p) => p.y);
    const xmin = Math.max(1, Math.min(...xs) * .8), xmax = Math.max(...xs) * 1.12;
    const ymax = Math.max(0.1, ...ys) * 1.14;
    const lx = (v) => (Math.log(Math.max(v, 1)) - Math.log(xmin)) / (Math.log(xmax) - Math.log(xmin));
    const px = (v) => pl + lx(v) * (w - pl - pr);
    const py = (v) => h - pb - (v / ymax) * (h - pt - pb);
    const mx = px(o.mx || 0), my = py(o.my || 0);

    const xt = [200, 500, 1000, 2000, 5000, 10000, 20000].filter((v) => v >= xmin && v <= xmax);
    const xgrid = xt.map((v) =>
      '<line x1="' + px(v).toFixed(1) + '" x2="' + px(v).toFixed(1) + '" y1="' + pt + '" y2="' + (h - pb) + '" class="sc-grid"/>' +
      '<text x="' + px(v).toFixed(1) + '" y="' + (h - pb + 13) + '" class="sc-t" text-anchor="middle">' +
      (v >= 1000 ? (v / 1000) + 'k' : v) + '</text>').join('');
    const yt = (() => {
      const out = [];
      for (let v = 0; v <= ymax + 0.001; v += 0.5) out.push(v);
      return out.map((v) =>
        '<line x1="' + pl + '" x2="' + (w - pr) + '" y1="' + py(v).toFixed(1) + '" y2="' + py(v).toFixed(1) + '" class="sc-grid"/>' +
        '<text x="' + (pl - 7) + '" y="' + (py(v) + 3).toFixed(1) + '" class="sc-t" text-anchor="end">' + v.toFixed(1) + '</text>').join('');
    })();

    return '<svg class="ch ch-scatter" viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto">' +
      '<rect x="' + pl + '" y="' + pt + '" width="' + (w - pl - pr) + '" height="' + (h - pt - pb) + '" class="sc-bg"/>' +
      xgrid + yt +
      '<line x1="' + mx.toFixed(1) + '" x2="' + mx.toFixed(1) + '" y1="' + pt + '" y2="' + (h - pb) + '" class="sc-mid"/>' +
      '<line x1="' + pl + '" x2="' + (w - pr) + '" y1="' + my.toFixed(1) + '" y2="' + my.toFixed(1) + '" class="sc-mid"/>' +
      pts.map((p) => {
        const cx = px(p.x), cy = py(p.y);
        const right = cx > w - 96;
        const low = cy < pt + 14;
        return '<g class="sc-g" data-acc="' + esc(p.acc) + '" data-tip="' + esc(p.tip) + '">' +
          '<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + (p.r || 5) + '" class="sc-d ' + (p.tone || '') + '"/>' +
          (p.name ? '<text x="' + (right ? cx - 7 : cx + 7).toFixed(1) + '" y="' + (low ? cy + 13 : cy - 6).toFixed(1) +
            '" class="sc-l" text-anchor="' + (right ? 'end' : 'start') + '">' + esc(p.name) + '</text>' : '') +
          '</g>';
      }).join('') +
      '<text x="' + (w - pr) + '" y="' + (h - 6) + '" class="sc-t" text-anchor="end">均播（对数）</text>' +
      '<text x="' + pl + '" y="' + (pt - 10) + '" class="sc-t">互动率 %</text>' +
      '</svg>';
  }

  /* ── HTML 原子件 ─────────────────────────────────────── */
  const kpi = (o) => {
    const tone = o.tone ? ' is-' + o.tone : '';
    return '<button type="button" class="k' + tone + '" data-drill="' + esc(o.drill || '') + '" title="' + esc((o.label || '') + ' · 查看明细') + '">' +
      '<div class="k-l">' + esc(o.label) + '<span class="arw" aria-hidden="true">' + (o.arrow || '⌄') + '</span></div>' +
      '<div class="k-v">' + (o.count != null
        ? '<b data-count="' + esc(o.count) + '" data-fmt="' + esc(o.fmt || 'int') + '">0</b>'
        : esc(o.value)) +
      (o.unit ? '<span class="u">' + esc(o.unit) + '</span>' : '') + '</div>' +
      '<div class="k-c">' + esc(o.cap || '') + '</div>' +
      (o.spark ? spark(o.spark) : '') + '</button>';
  };

  const mod = (o) => '<section class="mod ' + (o.cls || '') + '"' + (o.key ? ' data-unit="' + esc(o.key) + '"' : '') + '>' +
    '<header class="mod-h"><h2>' + (o.title || '') + '</h2>' + (o.sub ? '<span class="hint">' + o.sub + '</span>' : '') +
    (o.actions ? '<div class="mod-a">' + o.actions + '</div>' : '') + '</header>' +
    '<div class="mod-b">' + o.body + '</div>' + (o.foot ? '<footer class="mod-f">' + o.foot + '</footer>' : '') + '</section>';

  const tag = (t, n) => '<span class="b-tag">#' + esc(t) + (n != null ? '<i>' + int(n) + '</i>' : '') + '</span>';

  const delta = (v, base) => {
    const d = base ? ((v - base) / base) * 100 : 0;
    const tone = d >= 0 ? 'up' : 'down';
    return '<span class="b-dl ' + tone + '">' + (d >= 0 ? '+' : '') + d.toFixed(0) + '%</span>';
  };

  return {
    esc, int, nf, num, pct, ratio, dateMD, timeHM, sev,
    area, bars, spark, hbars, ring, scatter, svg,
    kpi, mod, tag, delta
  };
})();
