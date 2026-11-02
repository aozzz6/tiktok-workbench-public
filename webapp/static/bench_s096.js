/* ============================================================
   bench_s096.js · 对标分析屏（S096 · A10 象限落点 / P18 奶咖 / airy / bold）
   2026-10-28：整屏替换 #view-bench 的内容，其它视图不动。
   保留的主力交互：账号切换、视频搜索/时间/播放量/挂车/来源/排序、分页、
                  CSV 导出、导出结论、抽屉下钻、7/14/全部 周期、表头排序。
   隔离方式：Shadow DOM —— 只在这里引入 S096 的 CSS，不污染全站样式。
   ============================================================ */
(function () {
  'use strict';

  const HOST_ID = 'bench-s096';
  const CSS_FILES = ['base.css', 'pal-extra.css', 'ui.css', 'bench.css', 'arch-e.css'];
  const CSS_BASE = '/static/bench2/';

  let host = null, shadow = null, booted = false, cssReady = false;

  const st = {
    acct: 'all',
    group: null,          // { gid, accounts: [] }
    period: 'all',        // '7' | '14' | 'all'
    q: '', days: '', plays: 0, prod: '', src: '', sort: 'plays_desc',
    page: 1, size: 20,
    accSort: 'total_plays', accDir: 'desc',
    D: null, loading: false, error: null
  };

  /* ── 小工具 ───────────────────────────────────────────── */
  const S = () => window.BS;
  const U = () => window.BU;
  const $ = (sel) => shadow && shadow.querySelector(sel);
  const $$ = (sel) => (shadow ? Array.from(shadow.querySelectorAll(sel)) : []);
  const esc = (s) => window.BS.esc(s);

  function selOpt(v, cur) { return '<option value="' + esc(v) + '"' + (String(cur) === String(v) ? ' selected' : '') + '>'; }

  /* ── 骨架 ─────────────────────────────────────────────── */
  function skeletonHTML() {
    return '' +
      '<div class="pg a10">' +
        '<header class="b-head" id="b96-head">' +
          '<div class="b-brand"><span class="b-mark">TT</span><div><b>对标分析</b><small id="b96-asof">BENCH</small></div></div>' +
          '<div class="b-scope">' +
            '<select class="b96-sel" id="b96-acc" aria-label="账号范围"><option value="all">全部账号</option></select>' +
            '<span class="chip acc" id="b96-scope">全站</span>' +
            '<button class="btn ghost sm" id="b96-scope-all" data-act="scope-all" hidden>看全站</button>' +
          '</div>' +
          '<div class="b-acts">' +
            '<span class="stamp"><i class="dot live"></i><span id="b96-updated">—</span></span>' +
            '<span class="stamp muted" id="b96-daystext">—</span>' +
            '<button class="btn ghost" data-act="refresh">刷新</button>' +
            '<button class="btn ghost" data-act="export">导出结论</button>' +
          '</div>' +
        '</header>' +
        '<div id="b96-kpi"></div>' +
        '<div class="a10-hero"><div id="b96-hero"></div><aside class="a10-leg" id="b96-leg"></aside></div>' +
        '<div class="a10-row"><div id="b96-trend"></div><div id="b96-pat"></div></div>' +
        '<div class="a10-row2">' +
          '<section class="mod b-accmod" data-unit="acctable">' +
            '<header class="mod-h"><h2>账号对比</h2><span class="hint" id="b96-asub">—</span>' +
              '<div class="mod-a"><button class="btn ghost sm" data-act="csvacc">导出 CSV</button></div></header>' +
            '<div class="mod-b"><div class="b-tablewrap b96-scroll"><table class="tb b-tb b-tb-acc" data-sort="total_plays">' +
              '<thead><tr>' +
                '<th class="c" data-sort="rank">名次</th><th data-sort="account">账号</th><th class="r" data-sort="videos">视频</th>' +
                '<th class="r" data-sort="total_plays">总播放</th><th class="r" data-sort="avg_plays">均播</th>' +
                '<th class="r" data-sort="avg_engagement">互动率</th><th class="r" data-sort="product_coverage">挂车率</th>' +
                '<th class="r" data-sort="lastPublish">最近发布</th>' +
              '</tr></thead><tbody id="b96-abody"></tbody></table></div></div>' +
            '<footer class="mod-f"><span id="b96-afoot">—</span><span class="muted">点表头排序 · 点行开详情</span></footer>' +
          '</section>' +
          '<section class="mod b-vidmod" data-unit="videos">' +
            '<header class="mod-h"><h2>视频明细</h2><span class="hint" id="b96-vsub">—</span>' +
              '<div class="mod-a"><button class="btn ghost sm" data-act="csvvid">导出 CSV</button></div></header>' +
            '<div class="mod-b">' +
              '<div class="b96-tools">' +
                '<input class="b96-inp" id="b96-q" type="search" placeholder="搜索标题 / 文案 / 账号" autocomplete="off">' +
                '<select class="b96-sel-sm" id="b96-days"><option value="">全部时间</option><option value="1">近 1 天</option>' +
                  '<option value="3">近 3 天</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="90">近 90 天</option></select>' +
                '<select class="b96-sel-sm" id="b96-plays"><option value="0">全部播放量</option><option value="1000">≥ 1 千</option>' +
                  '<option value="10000">≥ 1 万</option><option value="100000">≥ 10 万</option><option value="500000">≥ 50 万</option></select>' +
                '<select class="b96-sel-sm" id="b96-prod"><option value="">挂车：全部</option><option value="yes">仅带小黄车</option>' +
                  '<option value="no">仅未挂车</option></select>' +
                '<select class="b96-sel-sm" id="b96-src"><option value="">来源：全部</option><option value="scrape">仅抓取快照</option>' +
                  '<option value="ledger">仅台账补录</option></select>' +
                '<select class="b96-sel-sm" id="b96-sort"><option value="plays_desc">播放 高→低</option>' +
                  '<option value="plays_asc">播放 低→高</option><option value="likes_desc">点赞 高→低</option>' +
                  '<option value="engagement_desc">互动率 高→低</option><option value="time_desc">发布 新→旧</option>' +
                  '<option value="time_asc">发布 旧→新</option></select>' +
                '<button class="btn ghost sm" data-act="vreset">重置</button>' +
                '<span class="b96-count" id="b96-vcount">—</span>' +
              '</div>' +
              '<div class="b-tablewrap"><table class="tb b-tb b-tb-vid">' +
                '<thead><tr><th>账号</th><th>标题</th><th class="r">发布</th><th class="r">播放</th>' +
                '<th class="r">点赞</th><th class="r">互动率</th><th class="c">商品</th></tr></thead>' +
                '<tbody id="b96-vbody"></tbody></table></div>' +
              '<div class="b96-pager" id="b96-vpager"></div>' +
            '</div>' +
            '<footer class="mod-f"><span id="b96-vfoot">—</span><span class="muted">抓取快照按发布时间归集</span></footer>' +
          '</section>' +
        '</div>' +
        '<div class="a10-row3"><div id="b96-prodwrap"></div><div id="b96-topv"></div></div>' +
        '<div id="b96-take"></div>' +
        '<div id="b96-foot"></div>' +
      '</div>' +
      '<div class="b96-load" id="b96-load"><div class="b96-load-in"><i></i><p id="b96-load-tip">正在读取 /api/benchmark …</p>' +
        '<button class="btn" data-act="retry" hidden>重试</button></div></div>' +
      '<div class="scrim" id="b96-scrim"></div>' +
      '<aside class="drawer" id="b96-drawer" role="dialog" aria-modal="true"></aside>' +
      '<div class="toast" id="b96-toast"></div>' +
      '<div id="tt"></div>';
  }

  const EXTRA_CSS = `
    :host { display: block; margin: -26px -32px; background: #f7f1e6; min-height: 100vh; }
    .bs96 {
      --f-sans: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif;
      --f-mono: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
      --f-num: "Inter", "SF Pro Display", "Segoe UI", system-ui, sans-serif;
      --r-l: 22px; --ease: cubic-bezier(.22,.61,.36,1);
      --fast: .18s; --mid: .38s; --slow: .62s;
      --shadow-1: 0 1px 2px rgba(0,0,0,.06), 0 8px 24px -18px rgba(0,0,0,.35);
      --shadow-2: 0 2px 6px rgba(0,0,0,.08), 0 24px 60px -30px rgba(0,0,0,.5);
      --maxw: 1440px;
      position: relative; min-height: 100vh;
      font: 400 var(--fs)/1.5 var(--f-sans);
      background: var(--bg); color: var(--ink);
    }
    .bs96::before {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background:
        radial-gradient(1200px 600px at 12% -10%, color-mix(in oklab, var(--accent) 9%, transparent), transparent 60%),
        radial-gradient(900px 500px at 100% 0%, color-mix(in oklab, var(--accent2) 8%, transparent), transparent 60%);
    }
    .bs96 .pg { position: relative; z-index: 1; padding-top: 20px; }
    .bs96 .b-head { flex-wrap: wrap; row-gap: 8px; }
    .bs96 .a10-row2 { grid-template-columns: minmax(0, 1fr); }
    .bs96 .a10-row2 .b-vidmod { order: -1; }
    .bs96 .b-tb-vid th:nth-child(1), .bs96 .b-tb-vid td:nth-child(1) { width: 118px; }
    .bs96 .b-tb-vid th:nth-child(3), .bs96 .b-tb-vid td:nth-child(3) { width: 56px; }
    .bs96 .b-tb-vid th:nth-child(4), .bs96 .b-tb-vid td:nth-child(4) { width: 78px; }
    .bs96 .b-tb-vid th:nth-child(5), .bs96 .b-tb-vid td:nth-child(5) { width: 64px; }
    .bs96 .b-tb-vid th:nth-child(6), .bs96 .b-tb-vid td:nth-child(6) { width: 60px; }
    .bs96 .b-tb-vid th:nth-child(7), .bs96 .b-tb-vid td:nth-child(7) { width: 58px; }

    .b96-sel, .b96-sel-sm, .b96-inp {
      font: inherit; color: var(--ink); background: var(--surface);
      border: 1px solid var(--line2); border-radius: 8px; padding: 5px 8px;
      outline: none; max-width: 220px;
    }
    .b96-sel { font-size: 11.5px; font-weight: 600; min-width: 132px; }
    .b96-sel-sm { font-size: 10.5px; padding: 4px 6px; }
    .b96-inp { font-size: 11px; min-width: 168px; padding: 5px 8px; }
    .b96-inp::placeholder { color: var(--muted); }
    .b96-sel:focus, .b96-sel-sm:focus, .b96-inp:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in oklab, var(--accent) 16%, transparent); }

    .b96-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
    .b96-count { margin-left: auto; font-size: 10px; color: var(--muted); white-space: nowrap; }
    .b96-scroll { max-height: 460px; overflow: auto; }
    .bs96 .b-tb-acc th:nth-child(1), .bs96 .b-tb-acc td:nth-child(1) { width: 44px; }
    .bs96 .b-tb-acc th:nth-child(3), .bs96 .b-tb-acc td:nth-child(3) { width: 44px; }
    .bs96 .b-tb-acc th:nth-child(4), .bs96 .b-tb-acc td:nth-child(4) { width: 76px; }
    .bs96 .b-tb-acc th:nth-child(6), .bs96 .b-tb-acc td:nth-child(6) { width: 58px; }
    .bs96 .b-tb-acc th:nth-child(7), .bs96 .b-tb-acc td:nth-child(7) { width: 56px; }
    .bs96 .b-tb-acc { min-width: 0; }
    .b96-scroll thead th { position: sticky; top: 0; z-index: 2; background: var(--surface); }
    .b96-pager { display: flex; align-items: center; gap: 8px; margin-top: 9px; font-size: 10.5px; color: var(--muted); }
    .b96-pager .btn { padding: 3px 8px; font-size: 10.5px; }
    .b96-pager .pg-n { margin-left: auto; font-variant-numeric: tabular-nums; }

    .b96-load {
      position: absolute; inset: 0; z-index: 60; display: grid; place-items: center;
      background: color-mix(in oklab, var(--bg) 88%, transparent); backdrop-filter: blur(2px);
    }
    .b96-load[hidden] { display: none; }
    .b96-load-in { text-align: center; }
    .b96-load-in i {
      display: block; width: 24px; height: 24px; margin: 0 auto 12px; border-radius: 50%;
      border: 2px solid var(--line2); border-top-color: var(--accent); animation: b96spin .8s linear infinite;
    }
    @keyframes b96spin { to { transform: rotate(360deg); } }
    .b96-load p { font-size: 12px; color: var(--muted); margin: 0; }
    .b96-load.err i { animation: none; border-color: var(--bad); }
    .b96-load .btn { margin-top: 12px; }
    .bs96 [hidden] { display: none !important; }
    .bs96 .drawer-b .tb { table-layout: fixed; width: 100%; }
    .bs96 .drawer-b .tb .b-td-title { white-space: normal; overflow-wrap: anywhere; }
    .bs96 .drawer-b .tb .r { width: 62px; }
    .bs96 .drawer, .bs96 .scrim, .bs96 .toast, .bs96 #tt { z-index: 90; }
    .bs96 .drawer { z-index: 91; }
    .bs96 .tbody tr { cursor: pointer; }

    /* ── 动效层（2026-10-28 加强版）────────────────────────
       统一 0.16–0.26s；不改布局，只动颜色/描边/阴影/位移 2px */
    .bs96 .b-strip-i { transition: background .18s var(--ease), color .18s; }
    .bs96 .b-strip-i:hover { background: color-mix(in oklab, var(--accent) 7%, transparent); }
    .bs96 .b-strip-i:hover b { color: var(--accent); }

    .bs96 .a10-q { transition: border-color .18s, box-shadow .22s, transform .22s var(--ease), background .18s; }
    .bs96 .a10-q:hover {
      border-color: color-mix(in oklab, var(--accent) 38%, var(--line2));
      transform: translateY(-2px);
      box-shadow: 0 10px 26px -22px rgba(36,27,19,.55);
    }
    .bs96 .a10-ql .b-tag { transition: background .16s, border-color .16s, color .16s; }
    .bs96 .a10-ql .b-tag:hover {
      background: color-mix(in oklab, var(--accent) 13%, transparent);
      border-color: color-mix(in oklab, var(--accent) 42%, var(--line));
      color: var(--ink);
    }

    .bs96 .b-prod, .bs96 .b-topv { transition: background .16s, padding-left .16s; }
    .bs96 .b-prod:hover, .bs96 .b-topv:hover { padding-left: 3px; }

    .bs96 .mod { transition: border-color .18s, box-shadow .24s; }
    .bs96 .mod:hover {
      border-color: color-mix(in oklab, var(--accent) 26%, var(--line2));
      box-shadow: 0 2px 6px rgba(36,27,19,.04), 0 18px 40px -34px rgba(36,27,19,.45);
    }

    .bs96 .b-tb th[data-sort] { transition: color .15s, background .15s; }
    .bs96 .b-tb th[data-sort]:hover { color: var(--ink); background: color-mix(in oklab, var(--accent) 6%, transparent); }

    .bs96 .b96-sel, .bs96 .b96-sel-sm, .bs96 .b96-inp { transition: border-color .16s, box-shadow .16s; }
    .bs96 .b96-sel:hover, .bs96 .b96-sel-sm:hover, .bs96 .b96-inp:hover { border-color: color-mix(in oklab, var(--accent) 40%, var(--line2)); }

    .bs96 .sc-d { transform-box: fill-box; transform-origin: center; transition: opacity .16s, transform .16s var(--ease); }
    .bs96 .sc-g:hover .sc-d { transform: scale(1.32); }

    .bs96 .b96-pager .btn { transition: background .16s, border-color .16s, transform .1s; }
    .bs96 .b96-pager .btn:active { transform: translateY(1px); }

    @keyframes b96fade { from { opacity: .3; transform: translateY(3px); } to { opacity: 1; transform: none; } }
    .bs96 .b96-fade { animation: b96fade .22s var(--ease); }
    @media (max-width: 900px) { .b96-inp { min-width: 120px; } }
  `;

  /* ── 初始化（只做一次） ───────────────────────────────── */
  async function ensure() {
    if (booted) return true;
    host = document.getElementById(HOST_ID);
    if (!host) return false;
    if (!host.shadowRoot) {
      shadow = host.attachShadow({ mode: 'open' });
      const texts = await Promise.all(CSS_FILES.map((f) =>
        fetch(CSS_BASE + f, { cache: 'no-store' }).then((r) => (r.ok ? r.text() : '')).catch(() => '')
      ));
      texts.forEach((t) => {
        const el = document.createElement('style');
        el.textContent = t;
        shadow.appendChild(el);
      });
      const extra = document.createElement('style');
      extra.textContent = EXTRA_CSS;
      shadow.appendChild(extra);

      const root = document.createElement('div');
      root.className = 'bs96';
      root.dataset.pal = 'P18';
      root.dataset.density = 'airy';
      root.dataset.motion = 'bold';
      root.innerHTML = skeletonHTML();
      shadow.appendChild(root);
      bind();
      observeReveal();
    } else {
      shadow = host.shadowRoot;
    }
    booted = true;
    return true;
  }

  /* ── 事件 ─────────────────────────────────────────────── */
  let qTimer = 0;
  function bind() {
    shadow.addEventListener('click', (e) => {
      const t = e.target;
      const act = t.closest('[data-act]');
      if (act) { doAction(act.dataset.act); return; }

      const period = t.closest('[data-period]');
      if (period) { st.period = period.dataset.period; renderTrend(); return; }

      const th = t.closest('th[data-sort]');
      if (th) { sortAccounts(th.dataset.sort); return; }

      const page = t.closest('[data-page]');
      if (page) { changePage(Number(page.dataset.page)); return; }

      const td = t.closest('[data-scope-all]');
      if (td) { clearGroup(); return; }

      const drill = t.closest('[data-drill]');
      if (drill) { openDrawer(drill.dataset.drill); return; }
      const acc = t.closest('[data-acc]');
      if (acc) { openDrawer('acc:' + acc.dataset.acc); return; }
      const vid = t.closest('[data-vid]');
      if (vid) { openDrawer('vid:' + vid.dataset.vid); return; }
      const prod = t.closest('[data-prod]');
      if (prod) { openDrawer('prod:' + prod.dataset.prod); return; }

      if (t.closest('[data-close]') || t.id === 'b96-scrim') { closeDrawer(); return; }
    });

    shadow.addEventListener('input', (e) => {
      if (e.target.id === 'b96-q') {
        const v = e.target.value;
        clearTimeout(qTimer);
        qTimer = setTimeout(() => { st.q = v; st.page = 1; renderVideos(); }, 240);
      }
    });

    shadow.addEventListener('change', (e) => {
      const id = e.target.id;
      if (id === 'b96-acc') { pickAccount(e.target.value); return; }
      if (id === 'b96-days') st.days = e.target.value;
      else if (id === 'b96-plays') st.plays = Number(e.target.value) || 0;
      else if (id === 'b96-prod') st.prod = e.target.value;
      else if (id === 'b96-src') st.src = e.target.value;
      else if (id === 'b96-sort') st.sort = e.target.value;
      else return;
      st.page = 1;
      renderVideos();
    });

    shadow.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDrawer();
    });
    // Shadow DOM 里的元素通常拿不到焦点，Esc 会落到 document 上：补一层全局监听
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDrawer();
    });

    shadow.addEventListener('mouseover', (e) => {
      const t = e.target.closest('[data-tip]');
      const tt = $('#tt');
      if (!t || !tt) return;
      tt.textContent = t.dataset.tip;
      const r = t.getBoundingClientRect();
      tt.style.left = (r.left + r.width / 2) + 'px';
      tt.style.top = r.top + 'px';
      tt.classList.add('on');
    });
    shadow.addEventListener('mouseout', (e) => {
      if (e.target.closest('[data-tip]')) { const tt = $('#tt'); if (tt) tt.classList.remove('on'); }
    });
  }

  function observeReveal() {
    if (!('IntersectionObserver' in window)) return;
    const io = new IntersectionObserver((rows) => {
      rows.forEach((en) => {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      });
    }, { root: null, rootMargin: '0px 0px -6% 0px', threshold: 0.02 });
    $$('[data-unit], .bs96 .b-strip, .bs96 .b-head').forEach((el, i) => {
      el.setAttribute('data-rv', '');
      el.style.setProperty('--d', Math.min(300, i * 22) + 'ms');
      io.observe(el);
    });
  }

  /* ── 数据 ─────────────────────────────────────────────── */
  function apiUrl() {
    if (st.group && st.group.accounts && st.group.accounts.length) {
      return '/api/benchmark?accounts=' + encodeURIComponent(st.group.accounts.join(','));
    }
    if (st.acct && st.acct !== 'all') return '/api/benchmark?account=' + encodeURIComponent(st.acct);
    return '/api/benchmark';
  }

  async function fetchData() {
    const r = await fetch(apiUrl(), { cache: 'no-store', credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    if (!window.BENCH_DERIVE) throw new Error('derive.js 未加载');
    return window.BENCH_DERIVE(j);
  }

  async function load(force) {
    const ok = await ensure();
    if (!ok) return;
    if (st.loading) return;
    if (!force && st.D && $('#b96-drawer') && $('#b96-drawer').classList.contains('on')) return;

    st.loading = true;
    if (!st.D) showLoading('正在读取 /api/benchmark …');
    try {
      const D = await fetchData();
      st.D = D;
      st.error = null;
      fillAccountOptions(D);
      renderAll();
      hideLoading();
    } catch (err) {
      console.error('[bench-s096] load failed', err);
      st.error = err;
      showLoading('读取 /api/benchmark 失败：' + (err && err.message ? err.message : err), true);
    } finally {
      st.loading = false;
    }
  }

  function showLoading(msg, isErr) {
    const box = $('#b96-load');
    if (!box) return;
    box.hidden = false;
    box.classList.toggle('err', !!isErr);
    const tip = $('#b96-load-tip');
    if (tip) tip.textContent = msg;
    const btn = box.querySelector('[data-act="retry"]');
    if (btn) btn.hidden = !isErr;
  }
  function hideLoading() {
    const box = $('#b96-load');
    if (box) box.hidden = true;
  }

  function fillAccountOptions(D) {
    const sel = $('#b96-acc');
    if (!sel) return;
    const opts = (D.meta.accountOptions || []).slice().sort((a, b) => (b.videos || 0) - (a.videos || 0));
    const cur = st.acct;
    sel.innerHTML = '<option value="all">全部账号</option>' + opts.map((o) =>
      '<option value="' + esc(o.account) + '">' + esc(o.account) + ' · ' + (o.videos || 0) + ' 条</option>').join('');
    sel.value = opts.some((o) => o.account === cur) ? cur : 'all';
    if (sel.value === 'all') st.acct = 'all';
  }

  function pickAccount(v) {
    st.acct = v || 'all';
    st.group = null;
    st.page = 1;
    load(true);
  }

  function setGroup(accounts, gid) {
    const list = (accounts || []).filter(Boolean);
    if (!list.length) return;
    st.group = { gid: gid || '', accounts: list };
    st.acct = 'all';
    st.page = 1;
    load(true);
  }

  function clearGroup() {
    st.group = null;
    st.page = 1;
    load(true);
  }

  /* ── 渲染 ─────────────────────────────────────────────── */
  function renderAll() {
    const D = st.D;
    if (!D) return;
    const asof = $('#b96-asof');
    if (asof) asof.textContent = 'BENCH · ' + D.meta.asOf;
    const up = $('#b96-updated');
    if (up) up.textContent = '快照 ' + D.meta.asOf.slice(11);
    const days = $('#b96-daystext');
    if (days) days.textContent = D.meta.days + ' 天 · 只读';

    const scope = $('#b96-scope');
    if (scope) {
      scope.textContent = st.group
        ? '组内 · ' + st.group.accounts.length + ' 个账号'
        : (st.acct === 'all' ? '全站 · ' + D.kpis.accounts + ' 个账号' : '单账号 · ' + st.acct);
    }
    const back = $('#b96-scope-all');
    if (back) back.hidden = !st.group;

    renderKpi();
    renderHero();
    renderTrend();
    renderPatterns();
    renderAccounts();
    renderVideos();
    renderProducts();
    renderTopVideos();
    renderTakeaways();
    renderFoot();
    revealSoon();
  }

  let revealTimer = 0;
  function revealSoon() {
    clearTimeout(revealTimer);
    revealTimer = setTimeout(() => {
      $$('[data-rv]').forEach((el) => el.classList.add('in'));
    }, 900);
  }

  function renderKpi() {
    $('#b96-kpi').innerHTML = U().kpis(st.D, { variant: 'strip' });
  }

  function quadCounts() {
    const D = st.D;
    const mx = D.baselines.medianAvgPlays;
    const my = D.baselines.medianEngagement;
    const q = { hh: [], hl: [], lh: [], ll: [] };
    D.accounts.forEach((a) => {
      const highX = a.avg_plays >= mx, highY = a.avg_engagement >= my;
      (highX ? (highY ? q.hh : q.hl) : (highY ? q.lh : q.ll)).push(a);
    });
    return { q, mx, my };
  }

  function renderHero() {
    const D = st.D;
    const { q, mx, my } = quadCounts();
    $('#b96-hero').innerHTML = U().accbars(D, {
      variant: 'scatter',
      title: '账号落点 · 均播 × 互动率',
      sub: st.acct === 'all' && !st.group
        ? '点 = 一个账号 · 点大小 = 总播放'
        : '当前范围 ' + D.accounts.length + ' 个账号'
    });

    const quad = [
      { arr: q.hh, t: '标杆', d: '高播放 · 高互动', tone: 'ok' },
      { arr: q.hl, t: '流量', d: '高播放 · 低互动', tone: '' },
      { arr: q.lh, t: '潜力', d: '低播放 · 高互动', tone: '' },
      { arr: q.ll, t: '沉寂', d: '低播放 · 低互动', tone: 'bad' }
    ];
    $('#b96-leg').innerHTML = quad.map((x) => {
      const arr = x.arr.slice().sort((a, b) => b.total_plays - a.total_plays);
      return '<div class="a10-q"><div class="a10-qh"><i class="dot ' + (x.tone || '') + '"></i><b>' + x.t + '</b>' +
        '<span class="tiny muted">' + x.d + '</span><b class="a10-qn tnum">' + arr.length + '</b></div>' +
        '<div class="a10-ql">' + arr.slice(0, 3).map((a) =>
          '<span class="b-tag" data-acc="' + esc(a.account) + '">' + esc(a.account) + '<i>' + S().nf(a.total_plays) + '</i></span>').join('') +
        (arr.length > 3 ? '<span class="tiny muted">…共 ' + arr.length + ' 个</span>' : '') + '</div></div>';
    }).join('') + '<p class="tiny muted a10-note">虚线 = 当前范围中位数：均播 ' + S().int(mx) + ' · 互动 ' + S().pct(my, 2) + '</p>';
  }

  function periodN() {
    if (st.period === '7') return 7;
    if (st.period === '14') return 14;
    return 0;
  }

  function renderTrend() {
    const D = st.D;
    const n = periodN();
    $('#b96-trend').innerHTML = U().trend(D, {
      n: n || D.series.length,
      kind: 'bars',
      title: '每日发布与播放',
      sub: D.meta.from + ' → ' + D.meta.to,
      cls: 'b96-trend'
    });
    // 让分段按钮反映当前选择
    $$('#b96-trend [data-period]').forEach((b) => {
      const k = b.dataset.period;
      b.classList.toggle('on', (k === '0' && !n) || (k !== '0' && Number(k) === n));
    });
  }

  function renderPatterns() {
    $('#b96-pat').innerHTML = U().patterns(st.D, { title: '爆款规律', sub: '播放分桶 · 标签 · 开场钩子' });
  }

  function renderProducts() {
    const D = st.D;
    const list = (D.patterns.products_top || []);
    if (!list.length) {
      $('#b96-prodwrap').innerHTML = window.BS.mod({
        key: 'products', title: '被挂车最多的商品', sub: '当前快照没有挂车数据',
        body: '<p class="dr-p" style="margin:2px 0 0">接口返回的商品挂载为 0：样本视频里没有带小黄车的记录，这里不补占位数据。' +
          '抓取恢复后刷新即可看到商品榜。</p>'
      });
      return;
    }
    $('#b96-prodwrap').innerHTML = U().products(D, { n: 3 });
  }
  function renderTopVideos() {
    $('#b96-topv').innerHTML = U().topvideos(st.D, { n: 3 });
  }
  function renderTakeaways() {
    $('#b96-take').innerHTML = U().takeaways(st.D, { variant: 'list' });
  }
  function renderFoot() {
    $('#b96-foot').innerHTML = U().foot(st.D);
  }

  /* ── 账号表 ───────────────────────────────────────────── */
  const ACC_KEYS = ['rank', 'account', 'videos', 'total_plays', 'avg_plays', 'avg_engagement', 'product_coverage', 'lastPublish'];

  function sortAccounts(key) {
    if (!ACC_KEYS.includes(key)) return;
    if (st.accSort === key) st.accDir = st.accDir === 'desc' ? 'asc' : 'desc';
    else { st.accSort = key; st.accDir = 'desc'; }
    renderAccounts();
  }

  function renderAccounts() {
    const D = st.D;
    const num = ['rank', 'videos', 'total_plays', 'avg_plays', 'avg_engagement', 'product_coverage'];
    const rows = D.accounts.slice().sort((a, b) => {
      const x = a[st.accSort], y = b[st.accSort];
      const v = num.includes(st.accSort) ? (Number(x) || 0) - (Number(y) || 0) : String(x).localeCompare(String(y));
      return st.accDir === 'asc' ? v : -v;
    });

    const body = $('#b96-abody');
    if (body) body.innerHTML = rows.map((a) => U().accRow(a, 'table')).join('');
    fadeIn(body);

    $$('#b96-acctable th, .b-tb-acc th').forEach((th) => {
      const k = th.dataset.sort;
      if (!k) return;
      th.classList.toggle('sorted', k === st.accSort);
      th.title = '点表头排序' + (k === st.accSort ? ' · 当前' + (st.accDir === 'asc' ? '升序' : '降序') : '');
      if (k === st.accSort) th.dataset.dir = st.accDir;
      else delete th.dataset.dir;
    });

    const sub = $('#b96-asub');
    if (sub) sub.textContent = D.accounts.length + ' 个账号 · 可排序 · 可滚动';
    const foot = $('#b96-afoot');
    if (foot) foot.textContent = '合计 ' + S().int(D.kpis.total_plays) + ' 播放 · ' + D.accounts.length + ' 个账号';
  }

  /* ── 视频明细 ─────────────────────────────────────────── */
  function videoList() {
    const D = st.D;
    const q = st.q.trim().toLowerCase();
    let rows = D.videos.slice();

    if (q) {
      rows = rows.filter((v) =>
        ((v.title || '') + ' ' + (v.desc || '') + ' ' + v.account).toLowerCase().includes(q));
    }
    if (st.days) {
      const cut = Date.now() - Number(st.days) * 864e5;
      rows = rows.filter((v) => {
        const t = Date.parse(String(v.publish_time).replace(' ', 'T'));
        return Number.isFinite(t) && t >= cut;
      });
    }
    if (st.plays) rows = rows.filter((v) => (v.plays || 0) >= Number(st.plays));
    if (st.prod === 'yes') rows = rows.filter((v) => v.products && v.products.length);
    if (st.prod === 'no') rows = rows.filter((v) => !(v.products && v.products.length));
    if (st.src) rows = rows.filter((v) => v.source === st.src);

    const cmp = {
      plays_desc: (a, b) => (b.plays || 0) - (a.plays || 0),
      plays_asc: (a, b) => (a.plays || 0) - (b.plays || 0),
      likes_desc: (a, b) => (b.likes || 0) - (a.likes || 0),
      engagement_desc: (a, b) => (b.engagement || 0) - (a.engagement || 0),
      time_desc: (a, b) => (a.publish_time < b.publish_time ? 1 : -1),
      time_asc: (a, b) => (a.publish_time < b.publish_time ? -1 : 1)
    }[st.sort] || ((a, b) => (b.plays || 0) - (a.plays || 0));

    return rows.sort(cmp);
  }

  function renderVideos() {
    const D = st.D;
    const rows = videoList();
    const total = rows.length;
    const pages = Math.max(1, Math.ceil(total / st.size));
    if (st.page > pages) st.page = pages;
    if (st.page < 1) st.page = 1;
    const start = (st.page - 1) * st.size;
    const slice = rows.slice(start, start + st.size);

    const body = $('#b96-vbody');
    if (body) body.innerHTML = slice.map((v) => U().videoRow(v)).join('');
    fadeIn(body);

    const cnt = $('#b96-vcount');
    if (cnt) cnt.textContent = '共 ' + S().int(total) + ' 条';

    const sub = $('#b96-vsub');
    if (sub) sub.textContent = '筛选后 ' + S().int(total) + ' / ' + S().int(D.videos.length) + ' 条';

    const foot = $('#b96-vfoot');
    if (foot) {
      foot.textContent = total
        ? (start + 1) + '–' + Math.min(start + st.size, total) + ' / ' + S().int(total) + ' · 第 ' + st.page + ' / ' + pages + ' 页'
        : '没有符合条件的视频';
    }

    const pager = $('#b96-vpager');
    if (pager) {
      pager.innerHTML = pages <= 1 ? '' :
        '<button class="btn ghost" data-page="' + (st.page - 1) + '"' + (st.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
        '<button class="btn ghost" data-page="' + (st.page + 1) + '"' + (st.page >= pages ? ' disabled' : '') + '>下一页</button>' +
        '<span class="pg-n">第 ' + st.page + ' / ' + pages + ' 页 · 每页 ' + st.size + ' 条</span>';
    }
  }

  function fadeIn(el) {
    if (!el) return;
    el.classList.remove('b96-fade');
    void el.offsetWidth;
    el.classList.add('b96-fade');
  }

  function changePage(p) {
    if (!Number.isFinite(p)) return;
    st.page = p;
    renderVideos();
  }

  /* ── 抽屉 ─────────────────────────────────────────────── */
  function openDrawer(key) {
    const d = U().detail(key, st.D);
    if (!d) return;
    const drawer = $('#b96-drawer');
    const scrim = $('#b96-scrim');
    drawer.innerHTML = '<header class="drawer-h"><div><div class="eyebrow">DETAIL</div><h2>' + esc(d.t) + '</h2>' +
      '<span class="tiny muted">' + esc(d.s) + '</span></div>' +
      '<button class="btn ghost x" data-close>关闭</button></header><div class="drawer-b">' + d.b + '</div>';
    drawer.classList.add('on');
    scrim.classList.add('on');
  }

  function closeDrawer() {
    const drawer = $('#b96-drawer');
    const scrim = $('#b96-scrim');
    if (drawer) drawer.classList.remove('on');
    if (scrim) scrim.classList.remove('on');
  }

  /* ── Toast / 动作 ─────────────────────────────────────── */
  let toastTimer = 0;
  function toast(msg) {
    const el = $('#b96-toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('on'), 1900);
  }

  function doAction(a) {
    if (a === 'refresh') { toast('正在重新读取 /api/benchmark …'); load(true); return; }
    if (a === 'retry') { load(true); return; }
    if (a === 'export') { exportBrief(); return; }
    if (a === 'csvacc') { csvAccounts(); return; }
    if (a === 'csvvid') { csvVideos(); return; }
    if (a === 'vreset') {
      st.q = ''; st.days = ''; st.plays = 0; st.prod = ''; st.src = ''; st.sort = 'plays_desc'; st.page = 1;
      const q = $('#b96-q'); if (q) q.value = '';
      ['b96-days', 'b96-prod', 'b96-src', 'b96-sort'].forEach((id) => { const el = $('#' + id); if (el) el.value = ''; });
      const pl = $('#b96-plays'); if (pl) pl.value = '0';
      const so = $('#b96-sort'); if (so) so.value = 'plays_desc';
      renderVideos();
      return;
    }
    if (a === 'scope-all') { clearGroup(); return; }
  }

  /* ── 导出 ─────────────────────────────────────────────── */
  function q1(s) { return '"' + String(s == null ? '' : s).replace(/"/g, '""') + '"'; }

  function download(name, text) {
    const blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
  }

  function csvAccounts() {
    const D = st.D;
    const head = ['名次', '账号', '语言', '视频数', '总播放', '平均播放', '互动率%', '挂车率%', '最高单条', '最近发布'];
    const rows = D.accounts.map((a) => [a.rank, a.account, a.lang, a.videos, a.total_plays, a.avg_plays,
      a.avg_engagement, a.product_coverage, a.maxPlays, a.lastPublish].map(q1).join(','));
    download('bench_accounts_' + D.meta.asOf.replace(/[-: ]/g, '') + '.csv', [head.join(','), ...rows].join('\n'));
    toast('已导出 ' + D.accounts.length + ' 个账号');
  }

  function csvVideos() {
    const D = st.D;
    const rows = videoList();
    const head = ['账号', '标题', '发布', '播放', '点赞', '评论', '分享', '收藏', '互动率%', '商品数', '来源', '链接'];
    const body = rows.map((v) => [v.account, v.title, v.fullDay, v.plays, v.likes, v.comments, v.shares,
      v.collects, v.engagement, (v.products || []).length, v.source, v.url].map(q1).join(','));
    download('bench_videos_' + D.meta.asOf.replace(/[-: ]/g, '') + '.csv', [head.join(','), ...body].join('\n'));
    toast('已导出 ' + rows.length + ' 条视频');
  }

  function exportBrief() {
    const D = st.D;
    const k = D.kpis;
    const lines = [
      '对标分析 · 结论文本 · 快照 ' + D.meta.asOf + (st.group ? ' · 组内 ' + st.group.accounts.length + ' 账号' : (st.acct !== 'all' ? ' · 账号 ' + st.acct : '')),
      '账号 ' + k.accounts + ' 个 · 样本视频 ' + k.videos + ' 条 · 总播放 ' + S().int(k.total_plays) + ' · 总点赞 ' + S().int(k.total_likes),
      '平均互动率 ' + k.avg_engagement + '% · 挂车率 ' + k.product_coverage + '%（' + k.with_products + ' 条）',
      '',
      '播放前 3 账号：' + D.accounts.slice(0, 3).map((a) => a.account + '（' + S().nf(a.total_plays) + '）').join('、'),
      '播放分桶：' + D.patterns.play_buckets.map((b) => b.label + ' ' + b.pct + '%').join(' / '),
      '高频标签：' + D.patterns.top_tags.slice(0, 5).map((t) => '#' + t.tag).join(' '),
      '',
      '可借鉴点：'
    ].concat(D.takeaways.map((t, i) => (i + 1) + '. ' + t.text));
    const text = lines.join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => toast('结论已复制到剪贴板'),
        () => { console.log(text); toast('复制失败，已输出到控制台'); }
      );
    } else {
      console.log(text);
      toast('当前环境不支持复制，已输出到控制台');
    }
  }

  /* ── 对外接口（app.js 调） ────────────────────────────── */
  window.BS96 = {
    load: load,
    refresh: () => load(true),
    enter: () => load(false),
    setGroup: setGroup,
    clearGroup: clearGroup,
    get active() { return booted; },
    get scope() { return st.group ? 'group' : st.acct; }
  };

  /* 页面进入 bench 时如果元素已在，先热一次（app.js 也会调 load） */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (document.getElementById(HOST_ID)) load(false); });
  } else if (document.getElementById(HOST_ID)) {
    load(false);
  }
})();
