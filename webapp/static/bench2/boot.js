/* ============================================================
   boot.js · 对标分析新入口（bench2）· 由 100 方案画廊基座复制
   差异只有一处：无参数时默认 S096 形态（A10 象限 / P18 奶咖 / airy / bold）
   ============================================================ */
(function () {
  const S = window.BS, U = window.BU, D = window.BENCHDATA;
  const root = document.documentElement;
  const q = new URLSearchParams(location.search);
  const mini = q.get('mini') === '1';

  /* ── 方案解析 ─────────────────────────────────────────── */
  let scheme = null;
  if (q.get('s') && window.SCHEMES) scheme = window.SCHEMES.list.find((x) => x.id === q.get('s'));
  if (!scheme) {
    scheme = {
      id: 'bench2', name: '象限落点',
      arch: q.get('a') || 'A10', pal: q.get('p') || 'P18',
      density: q.get('d') || 'airy', motion: q.get('m') || 'bold',
      fx: q.get('fx') || 'spotlight'
    };
  }
  root.dataset.pal = scheme.pal;
  root.dataset.density = scheme.density;
  root.dataset.motion = scheme.motion;
  root.dataset.arch = scheme.arch;
  if (mini) root.classList.add('mini');
  document.title = scheme.name + ' · 对标分析 100 方案';

  const stage = document.getElementById('stage');
  const arch = window.ARCH[scheme.arch] || window.ARCH.A1;
  stage.innerHTML = arch.render(D, scheme);

  /* ── Tooltip ──────────────────────────────────────────── */
  const tt = document.createElement('div');
  tt.id = 'tt';
  document.body.appendChild(tt);
  document.addEventListener('mouseover', (e) => {
    const t = e.target.closest('[data-tip]');
    if (!t) return;
    tt.textContent = t.dataset.tip;
    const r = t.getBoundingClientRect();
    tt.style.left = (r.left + r.width / 2) + 'px';
    tt.style.top = r.top + 'px';
    tt.classList.add('on');
  });
  document.addEventListener('mouseout', (e) => {
    if (e.target.closest('[data-tip]')) tt.classList.remove('on');
  });

  /* ── 抽屉 ─────────────────────────────────────────────── */
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  const drawer = document.createElement('aside');
  drawer.className = 'drawer';
  drawer.setAttribute('role', 'dialog');
  drawer.setAttribute('aria-modal', 'true');
  document.body.append(scrim, drawer);

  const closeDrawer = () => { drawer.classList.remove('on'); scrim.classList.remove('on'); };
  scrim.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

  function openDrawer(key) {
    const d = U.detail(key, D);
    if (!d) return;
    drawer.innerHTML = '<header class="drawer-h"><div><div class="eyebrow">DETAIL</div><h2>' + S.esc(d.t) + '</h2>' +
      '<span class="tiny muted">' + S.esc(d.s) + '</span></div>' +
      '<button class="btn ghost x" data-close>关闭</button></header><div class="drawer-b">' + d.b + '</div>';
    drawer.classList.add('on');
    scrim.classList.add('on');
    drawer.querySelector('[data-close]').onclick = closeDrawer;
  }

  /* ── 排序 ─────────────────────────────────────────────── */
  function sortTable(th) {
    const tbl = th.closest('table');
    const key = th.dataset.sort;
    const dir = tbl.dataset.key === key && tbl.dataset.dir !== 'desc' ? 'desc' : 'asc';
    tbl.dataset.key = key;
    tbl.dataset.dir = dir;
    const numKey = ['rank', 'videos', 'total_plays', 'avg_plays', 'avg_engagement', 'product_coverage'].includes(key);
    const rows = D.accounts.slice().sort((a, b) => {
      const x = a[key], y = b[key];
      const v = numKey ? (Number(x) || 0) - (Number(y) || 0) : String(x).localeCompare(String(y));
      return dir === 'asc' ? v : -v;
    });
    tbl.querySelector('tbody').innerHTML = rows.map((a) => U.accRow(a, tbl.dataset.variant)).join('');
    tbl.querySelectorAll('th').forEach((h) => h.classList.toggle('sorted', h === th));
    th.title = '点表头排序 · 当前' + (dir === 'asc' ? '升序' : '降序');
  }

  /* ── 趋势周期 ─────────────────────────────────────────── */
  function switchPeriod(btn) {
    const mod = btn.closest('[data-unit="trend"]') || btn.closest('.mod');
    if (!mod) return;
    mod.querySelectorAll('[data-period]').forEach((b) => b.classList.toggle('on', b === btn));
    const n = Number(btn.dataset.period) || 0;
    if (typeof window.__PERIOD__ === 'function' && window.__PERIOD__(n, mod)) return;
    const wrap = mod.querySelector('[data-chart="trend"]');
    if (!wrap) return;
    wrap.innerHTML = U.trendInner(D, n, wrap.dataset.kind || 'area', Number(wrap.dataset.h) || 150);
  }

  /* ── CSV / 导出 ───────────────────────────────────────── */
  function download(name, text) {
    const blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  }

  function q1(s) { return '"' + String(s == null ? '' : s).replace(/"/g, '""') + '"'; }

  function csvAccounts() {
    const head = ['名次', '账号', '语言', '视频数', '总播放', '平均播放', '互动率%', '挂车率%', '最高单条', '最近发布'];
    const rows = D.accounts.map((a) => [a.rank, a.account, a.lang, a.videos, a.total_plays, a.avg_plays,
      a.avg_engagement, a.product_coverage, a.maxPlays, a.lastPublish].map(q1).join(','));
    download('bench_accounts_' + D.meta.asOf.replace(/[-: ]/g, '') + '.csv', [head.join(','), ...rows].join('\n'));
    toast('已导出 47 个账号');
  }

  function csvVideos() {
    const head = ['账号', '标题', '发布', '播放', '点赞', '评论', '分享', '收藏', '互动率%', '商品数', '来源', '链接'];
    const rows = D.videos.slice().sort((a, b) => b.plays - a.plays).map((v) => [v.account, v.title, v.fullDay,
      v.plays, v.likes, v.comments, v.shares, v.collects, v.engagement,
      (v.products || []).length, v.source, v.url].map(q1).join(','));
    download('bench_videos_' + D.meta.asOf.replace(/[-: ]/g, '') + '.csv', [head.join(','), ...rows].join('\n'));
    toast('已导出 ' + S.int(D.videos.length) + ' 条视频');
  }

  function exportBrief() {
    const k = D.kpis;
    const lines = [
      '对标分析 · 结论文本 · 快照 ' + D.meta.asOf,
      '账号 ' + k.accounts + ' 个 · 样本视频 ' + k.videos + ' 条 · 总播放 ' + S.int(k.total_plays) + ' · 总点赞 ' + S.int(k.total_likes),
      '平均互动率 ' + k.avg_engagement + '% · 挂车率 ' + k.product_coverage + '%（' + k.with_products + ' 条）',
      '',
      '播放前 3 账号：' + D.accounts.slice(0, 3).map((a) => a.account + '（' + S.nf(a.total_plays) + '）').join('、'),
      '播放分桶：' + D.patterns.play_buckets.map((b) => b.label + ' ' + b.pct + '%').join(' / '),
      '高频标签：' + D.patterns.top_tags.slice(0, 5).map((t) => '#' + t.tag).join(' '),
      '',
      '可借鉴点：'
    ].concat(D.takeaways.map((t, i) => (i + 1) + '. ' + t.text));
    const text = lines.join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast('结论已复制到剪贴板'), () => toast('复制失败，已打开控制台'));
    } else toast('当前环境不支持复制');
    console.log(text);
  }

  /* ── Toast ────────────────────────────────────────────── */
  let toastEl;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'toast';
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(() => toastEl.classList.remove('on'), 1900);
  }

  function pulse() { stage.classList.remove('flash'); void stage.offsetWidth; stage.classList.add('flash'); }

  /* ── 事件委托 ─────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const period = e.target.closest('[data-period]');
    if (period) { switchPeriod(period); return; }

    const th = e.target.closest('th[data-sort]');
    if (th) { sortTable(th); return; }

    const drill = e.target.closest('[data-drill]');
    if (drill) { openDrawer(drill.dataset.drill); return; }

    const acc = e.target.closest('[data-acc]');
    if (acc) { openDrawer('acc:' + acc.dataset.acc); return; }

    const vid = e.target.closest('[data-vid]');
    if (vid) { openDrawer('vid:' + vid.dataset.vid); return; }

    const prod = e.target.closest('[data-prod]');
    if (prod) { openDrawer('prod:' + prod.dataset.prod); return; }

    const act = e.target.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a === 'refresh') {
        toast('正在重新读取 /api/benchmark …');
        setTimeout(() => location.reload(), 260);
      }
      else if (a === 'export') exportBrief();
      else if (a === 'csvacc') csvAccounts();
      else if (a === 'csvvid') csvVideos();
      return;
    }

    const nav = e.target.closest('[data-nav]');
    if (nav) { toast('跳转 → ' + nav.dataset.nav); return; }
  });

  /* ── 数字滚动 ─────────────────────────────────────────── */
  const fmtNum = (n, fmt) => {
    if (fmt === 'wan') return (n / 1e4).toFixed(1);
    if (fmt === 'pct1') return n.toFixed(1);
    if (fmt === 'pct2') return n.toFixed(2);
    return Math.round(n).toLocaleString('en-US');
  };

  function countUp(el) {
    const target = Number(el.dataset.count) || 0;
    const fmt = el.dataset.fmt || 'int';
    if (mini || matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = fmtNum(target, fmt); return; }
    const t0 = performance.now(), dur = 720;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      const e2 = 1 - Math.pow(1 - p, 3);
      el.textContent = fmtNum(target * e2, fmt);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ── 入场 ─────────────────────────────────────────────── */
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      en.target.classList.add('in');
      en.target.querySelectorAll('[data-count]').forEach(countUp);
      io.unobserve(en.target);
    });
  }, { rootMargin: '0px 0px -6% 0px', threshold: 0.02 });

  document.querySelectorAll('[data-unit], .k, .b-fig, .b-take-c, .b-sec, .b-tile').forEach((el, i) => {
    el.setAttribute('data-rv', '');
    el.style.setProperty('--d', Math.min(320, i * 24) + 'ms');
    io.observe(el);
  });
  document.querySelectorAll('[data-count]').forEach((el) => {
    if (!el.closest('[data-rv]')) countUp(el);
  });

  /* ── 时钟（大屏） ─────────────────────────────────────── */
  const clock = document.querySelector('[data-clock]');
  if (clock) {
    const tick = () => { clock.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false }); };
    tick(); setInterval(tick, 1000);
  }

  /* ── 效果层 ───────────────────────────────────────────── */
  const light = (window.SCHEMES && window.SCHEMES.LIGHT) || ['P18', 'P21'];
  let fx = ((scheme.fx || '') + ',' + (q.get('fx') || '')).split(',').map((s) => s.trim()).filter(Boolean);
  fx = [...new Set(fx)];
  if (light.includes(scheme.pal)) fx = fx.filter((f) => f !== 'aurora');
  fx = fx.slice(0, 3);
  if (fx.length && window.OVFX) {
    try { window.OVFX.init(fx); } catch (err) { console.warn('fx failed', err); }
  }

  window.__BENCH_READY__ = true;
  window.__BENCH_SCHEME__ = scheme;
})();
