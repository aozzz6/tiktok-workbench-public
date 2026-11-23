/* ============================================================
   对标分析 · 视频工作流与账号分析共用同一份 benchmark 数据。
   ============================================================ */
(function () {
  'use strict';

  const HOST_ID = 'bench-s096';
  const CSS_FILES = ['base.css', 'pal-extra.css', 'ui.css', 'bench.css', 'arch-e.css'];
  const CSS_BASE = '/static/bench2/';

  let host = null, shadow = null, booted = false, initializing = null;
  let pendingUrl = '', requestId = 0, returnPosition = null;
  let transcriptRequest = 0;

  const QUADRANTS = { hh: '标杆', hl: '流量', lh: '潜力', ll: '沉寂' };
  const SORTS = {
    plays: { field: 'plays', label: '累计播放' }, delta: { field: 'delta', label: '日增' },
    likes: { field: 'likes', label: '点赞' }, engagement: { field: 'engagement', label: '互动率' },
    time: { field: 'publish_time', label: '发布时间' }
  };
  const handedOff = new Set();

  const st = {
    acct: 'all',
    group: null,          // { gid, accounts: [] }
    period: 'all',        // '7' | '14' | 'all'
    video: '', q: '', days: '', plays: 0, prod: '', src: '', quad: '', rise: false,
    sort: 'plays_desc',
    page: 1, size: 20,
    accSort: 'total_plays', accDir: 'desc',
    D: null
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
        '<nav class="b96-tabs" aria-label="对标分析视图">' +
          '<button type="button" data-tab="videos" aria-controls="b96-videos" aria-pressed="true">视频明细</button>' +
          '<button type="button" data-tab="insights" aria-controls="b96-insights" aria-pressed="false">账号与规律</button>' +
        '</nav>' +
        '<p id="b96-refresh-error" class="b96-refresh-error" role="status" hidden></p>' +
        '<section class="mod b-vidmod" id="b96-videos" data-unit="videos">' +
            '<div class="mod-b">' +
              '<div class="b96-tools">' +
                '<input class="b96-inp" id="b96-q" type="search" aria-label="搜索标题、文案或账号" placeholder="搜索标题 / 文案 / 账号" autocomplete="off">' +
                '<select class="b96-sel-sm" id="b96-days" aria-label="发布时间"><option value="">全部时间</option><option value="1">近 1 天</option>' +
                  '<option value="3">近 3 天</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="90">近 90 天</option></select>' +
                '<select class="b96-sel-sm" id="b96-plays" aria-label="累计播放量"><option value="0">全部播放量</option><option value="1000">≥ 1 千</option>' +
                  '<option value="10000">≥ 1 万</option><option value="100000">≥ 10 万</option><option value="500000">≥ 50 万</option></select>' +
                '<select class="b96-sel-sm" id="b96-prod" aria-label="挂车"><option value="">挂车：全部</option><option value="yes">仅带小黄车</option>' +
                  '<option value="no">仅未挂车</option></select>' +
                '<select class="b96-sel-sm" id="b96-src" aria-label="来源"><option value="">来源：全部</option><option value="scrape">仅抓取快照</option>' +
                  '<option value="ledger">仅台账补录</option></select>' +
                '<select class="b96-sel-sm" id="b96-quad" aria-label="账号象限"><option value="">象限：全部</option>' +
                  Object.entries(QUADRANTS).map(([key, label]) => '<option value="' + key + '">' + label + '</option>').join('') + '</select>' +
                '<button class="btn ghost sm" data-act="rising" aria-pressed="false" title="今日快照播放量高于昨日">正在起量</button>' +
                '<select class="b96-sel-sm" id="b96-sort" aria-label="视频排序">' +
                  Object.entries(SORTS).map(([key, { label }]) => '<option value="' + key + '_desc">' + label + ' ↓</option>' +
                    '<option value="' + key + '_asc">' + label + ' ↑</option>').join('') + '</select>' +
                '<button class="btn ghost sm" data-act="vreset">重置</button>' +
                '<div class="b96-list-actions"><span id="b96-vsub" class="muted tiny" aria-live="polite"></span>' +
                  '<button class="btn ghost sm" data-act="csvvid">导出 CSV</button></div>' +
              '</div>' +
              '<div id="b96-chips" class="b96-chips" hidden></div>' +
              '<div class="b96-video-grid" id="b96-video-grid">' +
                '<div class="b96-list">' +
                  '<div class="b-tablewrap b96-video-scroll" id="b96-vscroll"><table class="tb b-tb b-tb-vid" aria-label="对标视频">' +
                    '<thead><tr><th scope="col">账号</th><th scope="col">标题</th>' +
                      ['time', 'plays', 'delta', 'likes', 'engagement'].map((key) =>
                        '<th scope="col" class="r" data-column="' + key + '"><button type="button" data-vsort="' + key + '">' + SORTS[key].label + '</button></th>').join('') +
                      '<th scope="col" class="c" data-column="product">商品</th><th scope="col" class="c" data-column="transcript">口播</th>' +
                    '</tr></thead><tbody id="b96-vbody"></tbody></table></div>' +
                  '<div class="b96-empty" id="b96-empty" hidden></div>' +
                '</div>' +
                '<aside class="b96-video-detail" id="b96-video-detail" aria-label="视频详情" hidden></aside>' +
              '</div>' +
              '<div class="b96-pager" id="b96-vpager"></div>' +
            '</div>' +
            '<footer class="mod-f"><span id="b96-vfoot">—</span></footer>' +
        '</section>' +
        '<section id="b96-insights" aria-label="账号与规律" hidden>' +
          '<div class="a10-hero"><div id="b96-hero"></div><aside class="a10-leg" id="b96-leg"></aside></div>' +
          '<div class="a10-row"><div id="b96-trend"></div><div id="b96-pat"></div></div>' +
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
          '<div class="a10-row3"><div id="b96-prodwrap"></div><div id="b96-topv"></div></div>' +
          '<div id="b96-take"></div><div id="b96-foot"></div>' +
        '</section>' +
      '</div>' +
      '<div class="b96-load" id="b96-load"><div class="b96-load-in"><i></i><p id="b96-load-tip" role="status">正在读取对标数据…</p>' +
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
    .bs96 .b96-tabs { display: flex; gap: 15px; border-bottom: 1px solid var(--line2); margin: 12px 0; }
    .bs96 .b96-tabs button { font: inherit; font-weight: 650; color: var(--muted); padding: 8px 3px;
      border: 0; border-bottom: 2px solid transparent; background: transparent; cursor: pointer; }
    .bs96 .b96-tabs button[aria-pressed="true"] { color: var(--accent); border-bottom-color: var(--accent); }
    .bs96 .b-accmod { margin-bottom: var(--gap); }
    .bs96 .b-vidmod { margin-top: 0; }
    .bs96 .b-vidmod > .mod-b { padding: 12px 14px; }
    .b96-list-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; }
    .b96-video-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; align-items: start;
      --video-height: clamp(280px, calc(100dvh - 290px), 680px); }
    .b96-video-grid.is-open { grid-template-columns: minmax(0, 1fr) minmax(320px, 44%); }
    .b96-list { min-width: 0; }
    .bs96 .b96-video-scroll { max-height: var(--video-height); overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
    .bs96 .b96-video-scroll .b-tb-vid { min-width: 690px; width: 100%; }
    .bs96 .b96-video-scroll th { position: sticky; top: 0; z-index: 2; background: var(--surface2); }
    .bs96 .b96-video-scroll tbody td { height: 32px; padding: 5px 8px; }
    .bs96 .b96-video-scroll th:nth-child(1), .bs96 .b96-video-scroll td:nth-child(1) { width: 115px; }
    .bs96 .b96-video-scroll [data-column="time"] { width: 82px; }
    .bs96 .b96-video-scroll [data-column="plays"] { width: 86px; }
    .bs96 .b96-video-scroll [data-column="delta"] { width: 86px; }
    .bs96 .b96-video-scroll [data-column="likes"], .bs96 .b96-video-scroll [data-column="engagement"] { width: 66px; }
    .bs96 .b96-video-scroll [data-column="product"] { width: 74px; }
    .bs96 .b96-video-scroll [data-column="transcript"] { width: 64px; }
    .bs96 .b96-video-scroll td[data-column] { white-space: nowrap; text-overflow: ellipsis; }
    .bs96 .b96-video-scroll td[data-column="product"] { text-overflow: clip; padding-inline: 4px; }
    .bs96 .b96-video-scroll th button, .b96-row-title, .b96-quad-button {
      border: 0; padding: 0; background: none; color: inherit; cursor: pointer; font: inherit;
    }
    .b96-row-title { display: inline-block; text-align: left; max-width: 100%; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }
    .b96-row-title:focus-visible, .bs96 button:focus-visible, .bs96 summary:focus-visible, .bs96 a:focus-visible {
      outline: 2px solid var(--accent); outline-offset: 2px;
    }
    .bs96 th[aria-sort] button { color: var(--accent); font-weight: 700; }
    .bs96 th[aria-sort="ascending"] button::after { content: " ↑"; }
    .bs96 th[aria-sort="descending"] button::after { content: " ↓"; }
    .bs96 tr.is-selected { background: color-mix(in oklab, var(--accent) 12%, var(--surface)); }
    .b96-handoff { display: inline-block; margin-inline-start: 6px; color: var(--accent); font-size: 10px; }
    .b96-positive { color: var(--ok); }
    .bs96 .b96-video-grid.is-open .b-tb-vid { min-width: 320px; }
    .bs96 .b96-video-grid.is-open th:first-child, .bs96 .b96-video-grid.is-open td:first-child { width: 94px; }
    .bs96 .b96-video-grid.is-open :is(th, td)[data-column]:not([data-column="plays"]):not([data-column="delta"]):not(.is-sorted) { display: none; }
    .bs96 .b96-video-detail { display: flex; flex-direction: column; min-width: 0; max-height: var(--video-height);
      border: 1px solid var(--line2); border-radius: 8px; background: var(--surface); }
    .b96-detail-head { padding: 12px 14px 10px; border-bottom: 1px solid var(--line); }
    .b96-detail-top, .b96-detail-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
    .b96-detail-head h3 { font-size: 15px; margin: 7px 0 0; overflow-wrap: anywhere; }
    .b96-detail-head h3:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
    .b96-detail-body { padding: 4px 14px 12px; overflow: auto; }
    .b96-detail-body h4 { margin: 12px 0 4px; font-size: 12px; }
    .b96-detail-body p { margin: 4px 0; overflow-wrap: anywhere; }
    .b96-detail-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px;
      margin: 4px 0 8px; padding-bottom: 9px; border-bottom: 1px solid var(--line); }
    .b96-detail-metrics div { min-width: 0; }
    .b96-detail-metrics dt { color: var(--muted); font-size: 10px; }
    .b96-detail-metrics dd { margin: 0; font-weight: 650; overflow-wrap: anywhere; }
    .b96-detail-footer { padding: 9px 14px; border-top: 1px solid var(--line); }
    .b96-detail-footer [data-act="handoff"] { margin-left: auto; }
    .b96-detail-text { white-space: pre-wrap; }
    .b96-detail-text summary { white-space: normal; cursor: pointer; color: var(--accent); }
    .b96-detail-text summary + p { margin-top: 6px; }
    .b96-empty { text-align: center; padding: 38px 12px; color: var(--muted); }
    .b96-empty p { margin: 0 0 12px; }
    .b96-refresh-error { margin: 0 0 10px; padding: 7px 10px; border: 1px solid var(--line2); background: var(--surface); border-radius: 8px; }
    .b96-chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin: 0 0 8px; }
    .b96-chips button { cursor: pointer; }
    .bs96 [data-act="rising"][aria-pressed="true"] { color: var(--accent); border-color: var(--accent); }
    .b96-quad-button { font-weight: 650; text-align: left; }

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
    @media (max-width: 1050px) {
      .b96-video-grid.is-open { grid-template-columns: minmax(0, 1fr); }
      .b96-video-grid.is-open .b96-video-scroll { max-height: min(42vh, 360px); }
    }
    @media (max-width: 680px) {
      :host { margin: -12px; }
      .bs96 .b-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .bs96 .b-strip-s { display: none; }
      .bs96 .b-acts { flex-wrap: wrap; justify-content: flex-start; }
      .b96-inp { min-width: 120px; }
      .bs96 .b96-video-grid.is-open .b-tb-vid { min-width: 0; }
      .bs96 .b96-video-grid.is-open [data-column="delta"]:not(.is-sorted) { display: none; }
      .bs96 .b96-video-grid.is-open:has(th.is-sorted:not([data-column="plays"])) [data-column="plays"] { display: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      .bs96 *, .bs96 *::before, .bs96 *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
    }
  `;

  /* ── 初始化（只做一次） ───────────────────────────────── */
  function ensure() {
    if (!initializing) initializing = mount();
    return initializing;
  }

  async function mount() {
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

      const tab = t.closest('[data-tab]');
      if (tab) { selectTab(tab.dataset.tab); return; }
      const quad = t.closest('[data-quad]');
      if (quad) { selectTab('videos'); setFilter('quad', quad.dataset.quad); return; }
      const filter = t.closest('[data-clear-filter]');
      if (filter) { setFilter(filter.dataset.clearFilter, filter.dataset.clearFilter === 'plays' ? 0 : ''); return; }
      const videoSort = t.closest('[data-vsort]');
      if (videoSort) {
        const key = videoSort.dataset.vsort;
        setFilter('sort', key + (st.sort === key + '_desc' ? '_asc' : '_desc'));
        return;
      }

      const row = t.closest('[data-video-key]');
      if (row) { openVideo(row.dataset.videoKey, t.closest('button') === shadow.activeElement); return; }

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
        qTimer = setTimeout(() => setFilter('q', v), 180);
      }
    });

    shadow.addEventListener('change', (e) => {
      const id = e.target.id;
      if (id === 'b96-acc') { pickAccount(e.target.value); return; }
      const key = id.replace(/^b96-/, '');
      if (['days', 'plays', 'prod', 'src', 'quad', 'sort'].includes(key)) {
        setFilter(key, key === 'plays' ? Number(e.target.value) : e.target.value);
      }
    });

    shadow.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && st.video) { closeVideo(true); e.stopPropagation(); return; }
      const key = e.key.toLowerCase();
      if (!st.video || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey ||
          e.target.matches('input, select, textarea, [contenteditable]')) return;
      if (key === 'j' || key === 'k') {
        e.preventDefault();
        stepVideo(key === 'j' ? 1 : -1, true);
      }
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
  function apiUrl(refresh) {
    const params = new URLSearchParams({ workbench: 'true' });
    if (st.group && st.group.accounts && st.group.accounts.length) {
      params.set('accounts', st.group.accounts.join(','));
    } else if (st.acct !== 'all') {
      params.set('account', st.acct);
    }
    if (refresh) params.set('refresh', 'true');
    return '/api/benchmark?' + params.toString();
  }

  async function fetchData(url) {
    const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    if (!window.BENCH_DERIVE) throw new Error('derive.js 未加载');
    return window.BENCH_DERIVE(j);
  }

  async function load(force, refresh = false) {
    const ok = await ensure();
    if (!ok) return;
    if (!force && st.D && (st.video || $('#b96-drawer').classList.contains('on'))) return;

    const url = apiUrl(refresh);
    if (pendingUrl === url) return;
    const request = ++requestId;
    pendingUrl = url;
    const notice = $('#b96-refresh-error');
    const refreshButton = $('[data-act="refresh"]');
    refreshButton.disabled = true;
    refreshButton.textContent = '刷新中…';
    notice.hidden = true;

    if (!st.D) showLoading('正在读取对标数据…');
    try {
      const D = await fetchData(url);
      if (request !== requestId) return;
      st.D = D;
      fillAccountOptions(D);
      renderAll();
      if (st.video) renderVideoDetail(currentVideo());
      hideLoading();
    } catch (err) {
      if (request !== requestId) return;
      if (st.D) {
        notice.textContent = '读取失败，仍显示 ' + st.D.meta.asOf + ' 的快照。可点击刷新重试。';
        notice.hidden = false;
      } else {
        showLoading('对标数据读取失败：' + (err.message || '连接异常'), true);
      }
    } finally {
      if (request === requestId) {
        pendingUrl = '';
        refreshButton.disabled = false;
        refreshButton.textContent = '刷新';
      }
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
    changeScope();
  }

  function setGroup(accounts, gid) {
    const list = (accounts || []).filter(Boolean);
    if (!list.length) return;
    st.group = { gid: gid || '', accounts: list };
    st.acct = 'all';
    changeScope();
  }

  function clearGroup() {
    st.group = null;
    changeScope();
  }

  function changeScope() {
    closeVideo();
    st.page = 1;
    const list = $('#b96-vscroll');
    if (list) list.scrollTop = 0;
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
      { key: 'hh', d: '高播放 · 高互动', tone: 'ok' },
      { key: 'hl', d: '高播放 · 低互动', tone: '' },
      { key: 'lh', d: '低播放 · 高互动', tone: '' },
      { key: 'll', d: '低播放 · 低互动', tone: 'bad' }
    ];
    $('#b96-leg').innerHTML = quad.map((x) => {
      const arr = q[x.key].slice().sort((a, b) => b.total_plays - a.total_plays);
      return '<div class="a10-q"><div class="a10-qh"><i class="dot ' + (x.tone || '') + '"></i>' +
        '<button type="button" class="b96-quad-button" data-quad="' + x.key + '" aria-label="查看' + QUADRANTS[x.key] + '账号的视频">' + QUADRANTS[x.key] + '</button>' +
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
  function selectTab(tab) {
    if (tab !== 'videos') closeVideo();
    $('#b96-videos').hidden = tab !== 'videos';
    $('#b96-insights').hidden = tab !== 'insights';
    $$('[data-tab]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.tab === tab)));
  }

  function setFilter(key, value) {
    if (key === 'q') clearTimeout(qTimer);
    st[key] = value;
    st.page = 1;
    $('#b96-vscroll').scrollTop = 0;
    if (st.D) renderVideos();
  }

  function filterChips() {
    const inputs = { q: '搜索 ' + st.q.trim(), days: '近 ' + st.days + ' 天', plays: '播放 ≥ ' + S().nf(st.plays),
      prod: st.prod === 'yes' ? '已挂车' : '未挂车', src: st.src === 'scrape' ? '抓取快照' : '台账补录', quad: QUADRANTS[st.quad] };
    const box = $('#b96-chips');
    const chips = [];
    for (const [key, label] of Object.entries(inputs)) {
      const input = $('#b96-' + key);
      if (input.value !== String(st[key]) && !(key === 'q' && input === shadow.activeElement)) input.value = st[key];
      if (st[key] && (key !== 'q' || st.q.trim())) {
        chips.push('<button type="button" class="chip acc" data-clear-filter="' + key + '" aria-label="清除' + esc(label) + '">' + esc(label) + ' ×</button>');
      }
    }
    const riseButton = $('[data-act="rising"]');
    riseButton.setAttribute('aria-pressed', String(st.rise));
    if (st.rise) {
      const excluded = st.D.videos.filter((v) => v.delta == null && v.mediaType !== 'image').length;
      chips.push('<span class="muted tiny">日增大于 0' + (excluded ? ' · 当前账号范围 ' + S().int(excluded) + ' 条缺少可比较快照' : '') + '</span>');
    }
    box.innerHTML = chips.join('');
    box.hidden = !chips.length;
  }

  function videoKey(v) {
    return JSON.stringify([v.account, v.vid || v.url || [v.title, v.publish_time]]);
  }

  function metric(v, key) {
    if (v.missingMetrics.includes(key) || v[key] == null || v[key] === '') return null;
    return v[key];
  }

  function metricText(v, key) {
    const value = metric(v, key);
    if (key === 'delta') return value == null ? (v.growthStatus === 'baseline' ? '待建立基线' : '暂无增量') :
      (value > 0 ? '+' : '') + S().int(value);
    if (value == null) return '—';
    return key === 'engagement' ? S().pct(value, 2) : S().int(value);
  }

  function hasProduct(v) { return !!(v.products.length || v.productLink); }

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
    if (st.plays) rows = rows.filter((v) => metric(v, 'plays') >= st.plays);
    if (st.prod) rows = rows.filter((v) => hasProduct(v) === (st.prod === 'yes'));
    if (st.src) rows = rows.filter((v) => v.source === st.src);
    if (st.quad) {
      const names = new Set(quadCounts().q[st.quad].map((a) => a.account));
      rows = rows.filter((v) => names.has(v.account));
    }
    if (st.rise) rows = rows.filter((v) => v.delta > 0);

    const [key, dir] = st.sort.split('_');
    const field = SORTS[key].field;
    return rows.sort((a, b) => {
      const x = metric(a, field), y = metric(b, field);
      if (x == null || y == null) return Number(x == null) - Number(y == null);
      const order = x === y ? 0 : x < y ? -1 : 1;
      return dir === 'asc' ? order : -order;
    });
  }

  function videoRow(v, sorted) {
    const title = v.title || '（无标题）';
    const key = videoKey(v);
    const completed = handedOff.has(key) && window.RV2?.hasReport?.(v.vid);
    const handoff = handedOff.has(key) ? (completed ? '已反推' : '已带入') : '';
    const selected = key === st.video;
    const cell = (column, value, classes = 'r tnum') => '<td data-column="' + column + '" class="' + classes +
      (sorted === column ? ' is-sorted' : '') + '">' + value + '</td>';
    const numbers = ['plays', 'delta', 'likes', 'engagement'].map((key) => cell(key, esc(metricText(v, key)),
      'r tnum' + (metric(v, key) == null ? ' muted' : key === 'delta' && v.delta > 0 ? ' b96-positive' : ''))).join('');
    const transcript = { available: '可查看', missing: '暂无', not_applicable: '图文' }[v.transcriptStatus] || '—';
    return '<tr data-video-key="' + esc(key) + '" class="' + (selected ? 'is-selected' : '') + '">' +
      '<td class="b-td-acc" title="' + esc(v.account) + '">' + esc(v.account) + '</td>' +
      '<td class="b-td-title"><button type="button" class="b96-row-title" aria-controls="b96-video-detail" aria-expanded="' + selected +
      '" title="' + esc(title) + '">' + esc(title) + '</button>' + (handoff ? '<span class="b96-handoff">' + handoff + '</span>' : '') + '</td>' +
      cell('time', esc(v.day || '—'), 'r muted tnum') + numbers +
      cell('product', hasProduct(v) ? '<span class="chip acc">已挂车</span>' : '—', 'c') +
      cell('transcript', transcript, v.transcriptStatus === 'available' ? 'c' : 'c muted') + '</tr>';
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
    const [sorted, dir] = st.sort.split('_');
    $('#b96-sort').value = st.sort;
    $$('[data-vsort]').forEach((button) => {
      const th = button.parentElement;
      const active = sorted === button.dataset.vsort;
      th.classList.toggle('is-sorted', active);
      if (active) th.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
    filterChips();
    if (st.video && !slice.some((v) => videoKey(v) === st.video)) closeVideo();

    const body = $('#b96-vbody');
    body.innerHTML = slice.map((v) => videoRow(v, sorted)).join('');

    const empty = $('#b96-empty');
    empty.hidden = !!total;
    $('#b96-vscroll').hidden = !total;
    if (!total) {
      empty.innerHTML = D.videos.length
        ? '<p>没有符合当前条件的视频</p><button type="button" class="btn ghost" data-act="vreset">清除筛选</button>'
        : '<p>当前账号范围还没有采集到视频</p><button type="button" class="btn ghost" data-act="run">去一键运行</button>';
    }

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
    if (!Number.isFinite(p) || !st.D) return;
    st.page = p;
    $('#b96-vscroll').scrollTop = 0;
    renderVideos();
  }

  /* ── 抽屉 ─────────────────────────────────────────────── */
  function currentVideo() {
    return st.D?.videos.find((v) => videoKey(v) === st.video);
  }

  function updateSelection() {
    $$('[data-video-key]').forEach((row) => {
      const selected = row.dataset.videoKey === st.video;
      row.classList.toggle('is-selected', selected);
      row.querySelector('.b96-row-title').setAttribute('aria-expanded', String(selected));
    });
  }

  function videoLinks(v) {
    let original = '', reverse = '';
    try {
      const url = new URL(v.url);
      if (/^https?:$/.test(url.protocol) && /(^|\.)tiktok\.com$/i.test(url.hostname)) {
        original = url.href;
        if (/\/video\/\d{6,}(?:\/|$)/.test(url.pathname) || /^(vm|vt)\.tiktok\.com$/i.test(url.hostname)) reverse = original;
      }
    } catch (_) { /* 台账可能只有 ID */ }
    if (!reverse && v.mediaType !== 'image' && !/\/photo\//.test(original) && /^\d{15,25}$/.test(v.vid)) reverse = String(v.vid);
    return { original, reverse };
  }

  function expandableText(text) {
    const clean = text.trim();
    if (clean.length <= 200 && clean.split('\n').length <= 4) return '<p class="b96-detail-text">' + esc(clean) + '</p>';
    return '<p class="b96-detail-text b96-text-preview">' + esc(clean.split('\n').slice(0, 4).join('\n').slice(0, 200)) + '…</p>' +
      '<details class="b96-detail-text"><summary>展开全文</summary><p>' + esc(clean) + '</p></details>';
  }

  async function renderTranscript(v, box) {
    const request = ++transcriptRequest;
    if (v.transcriptStatus === 'not_applicable') { box.textContent = '图文内容无需口播转写'; return; }
    if (!v.transcriptStatus) { box.textContent = '转写接口尚未加载，请重启工作台服务后刷新。'; return; }
    if (v.transcriptStatus !== 'available') { box.textContent = '暂无本地转写'; return; }
    box.textContent = '正在读取本地转写…';
    try {
      const url = '/api/benchmark/' + encodeURIComponent(v.vid) + '/transcript?account=' + encodeURIComponent(v.account);
      const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (request !== transcriptRequest) return;
      if (data.status === 'available' && data.text) {
        box.classList.remove('muted');
        box.innerHTML = expandableText(data.text) +
          (data.truncated ? '<p class="muted">转写过长，已截断显示。</p>' : '') +
          '<button type="button" class="btn ghost sm" data-act="copy-transcript">复制口播</button>';
        box.dataset.transcript = data.text;
        return;
      }
      if (data.status === 'missing') { box.textContent = '暂无本地转写'; return; }
      throw new Error('unavailable');
    } catch (_) {
      if (request === transcriptRequest) {
        box.innerHTML = '暂时无法读取本地转写 <button type="button" class="btn ghost sm" data-act="retry-transcript">重试</button>';
      }
    }
  }

  function renderVideoDetail(v, focus = false) {
    if (!v) return;
    const detail = $('#b96-video-detail');
    const { original, reverse } = videoLinks(v);
    const visibleKeys = $$('[data-video-key]').map((row) => row.dataset.videoKey);
    const index = visibleKeys.indexOf(st.video);
    const metrics = [['累计播放', 'plays'], ['日增', 'delta'], ['互动率', 'engagement'],
      ['点赞', 'likes'], ['评论', 'comments'], ['分享', 'shares'], ['收藏', 'collects']].map(([label, key]) =>
      '<div><dt>' + label + '</dt><dd>' + esc(metricText(v, key)) + '</dd></div>').join('');
    detail.innerHTML = '<header class="b96-detail-head">' +
      '<div class="b96-detail-top"><span class="muted">' + esc(v.account) + ' · ' + esc(v.publish_time || '发布时间未知') + '</span>' +
      '<div><button type="button" class="btn ghost sm" data-act="video-prev" aria-label="上一条视频（K）"' + (index <= 0 ? ' disabled' : '') + '>↑</button> ' +
      '<button type="button" class="btn ghost sm" data-act="video-next" aria-label="下一条视频（J）"' + (index === visibleKeys.length - 1 ? ' disabled' : '') + '>↓</button> ' +
      '<button type="button" class="btn ghost sm" data-act="video-close">关闭</button></div></div>' +
      '<h3 tabindex="-1">' + esc(v.title || '（无标题）') + '</h3></header>' +
      '<div class="b96-detail-body"><dl class="b96-detail-metrics">' + metrics + '</dl>' +
      '<h4>口播</h4><div id="b96-transcript" class="muted" role="status"></div>' +
      '<h4>文案</h4>' + (v.desc.trim() ? expandableText(v.desc) +
        '<button type="button" class="btn ghost sm" data-act="copy-caption">复制文案</button>' : '<p class="muted">暂无文案</p>') +
      '<h4>挂载商品</h4>' + (v.products.length ? v.products.map((p) => '<p>' + esc(p.title || '商品名称未采集') + '</p>').join('') :
        '<p class="muted">' + (v.productLink ? '已记录商品链接，暂无商品名称' : '暂无商品记录') + '</p>') + '</div>' +
      '<footer class="b96-detail-footer">' + (original ? '<a class="btn ghost sm" href="' + esc(original) +
        '" target="_blank" rel="noopener noreferrer">打开原帖 ↗</a>' : '') +
      (reverse ? '<button type="button" class="btn pri" data-act="handoff">反推这条 →</button>' :
        '<span class="muted">暂无可用于反推的视频链接</span>') + '</footer>';
    detail.hidden = false;
    $('#b96-video-grid').classList.add('is-open');
    renderTranscript(v, $('#b96-transcript'));
    if (focus) detail.querySelector('h3').focus({ preventScroll: true });
  }

  function openVideo(key, focus = false) {
    const video = st.D?.videos.find((v) => videoKey(v) === key);
    if (!video) return;
    st.video = key;
    updateSelection();
    renderVideoDetail(video, focus);
    if (matchMedia('(max-width: 1050px)').matches) $('#b96-video-detail').scrollIntoView({ block: 'nearest' });
  }

  function stepVideo(direction, focus = false) {
    const rows = $$('[data-video-key]');
    const index = rows.findIndex((row) => row.dataset.videoKey === st.video);
    const next = rows[index + direction];
    if (index < 0 || !next) return;
    openVideo(next.dataset.videoKey, focus);
    const list = $('#b96-vscroll');
    const rect = next.getBoundingClientRect(), frame = list.getBoundingClientRect();
    const header = list.querySelector('thead').getBoundingClientRect().height;
    if (rect.top < frame.top + header) list.scrollTop += rect.top - frame.top - header;
    else if (rect.bottom > frame.bottom) list.scrollTop += rect.bottom - frame.bottom;
  }

  function closeVideo(restoreFocus = false) {
    if (!st.video) return;
    const selected = $('[data-video-key].is-selected');
    st.video = '';
    ++transcriptRequest;
    $('#b96-video-detail').hidden = true;
    $('#b96-video-grid').classList.remove('is-open');
    updateSelection();
    if (restoreFocus) selected?.querySelector('button').focus({ preventScroll: true });
  }

  function handoffVideo() {
    const video = currentVideo();
    const url = video && videoLinks(video).reverse;
    if (!url || !window.openBenchmarkReverse) return;
    const list = $('#b96-vscroll');
    returnPosition = { top: list.scrollTop, left: list.scrollLeft, windowY: window.scrollY, key: st.video };
    handedOff.add(st.video);
    closeVideo();
    window.openBenchmarkReverse(url);
  }

  function enter() {
    if (returnPosition && st.D) {
      const position = returnPosition;
      returnPosition = null;
      renderVideos();
      requestAnimationFrame(() => {
        if (!host.closest('.view.active')) return;
        const list = $('#b96-vscroll');
        list.scrollTo(position.left, position.top);
        window.scrollTo(0, position.windowY);
        const previous = $$('[data-video-key]').find((row) => row.dataset.videoKey === position.key);
        previous?.querySelector('button').focus({ preventScroll: true });
      });
    }
    return load(false);
  }

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
    if (a === 'refresh') { load(true, true); return; }
    if (a === 'retry') { load(true); return; }
    if (a === 'video-close') { closeVideo(true); return; }
    if (a === 'video-prev' || a === 'video-next') { stepVideo(a === 'video-next' ? 1 : -1, true); return; }
    if (a === 'handoff') { handoffVideo(); return; }
    if (a === 'retry-transcript') { const video = currentVideo(); if (video) renderTranscript(video, $('#b96-transcript')); return; }
    if (a === 'copy-transcript' || a === 'copy-caption') {
      const text = a === 'copy-transcript' ? $('#b96-transcript')?.dataset.transcript : currentVideo()?.desc;
      if (!text || !navigator.clipboard) { toast('剪贴板暂不可用'); return; }
      navigator.clipboard.writeText(text).then(() => toast('已复制'), () => toast('无法访问剪贴板'));
      return;
    }
    if (a === 'run') { document.querySelector('.nav-item[data-view="run"]')?.click(); return; }
    if (a === 'rising') {
      st.rise = !st.rise;
      setFilter('sort', st.rise ? 'delta_desc' : st.sort);
      return;
    }
    if (a === 'scope-all') { clearGroup(); return; }
    if (!st.D) return;
    if (a === 'export') { exportBrief(); return; }
    if (a === 'csvacc') { csvAccounts(); return; }
    if (a === 'csvvid') { csvVideos(); return; }
    if (a === 'vreset') {
      Object.assign(st, { days: '', plays: 0, prod: '', src: '', quad: '', rise: false, sort: 'plays_desc' });
      $('#b96-q').value = '';
      setFilter('q', '');
      return;
    }
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
    const head = ['账号', '标题', '发布', '播放', '点赞', '评论', '分享', '收藏', '互动率%', '商品数', '来源', '链接', '日增', '日增状态'];
    const body = rows.map((v) => [v.account, v.title, v.fullDay, metric(v, 'plays'), metric(v, 'likes'),
      metric(v, 'comments'), metric(v, 'shares'), metric(v, 'collects'), metric(v, 'engagement'),
      v.products.length, v.source, v.url, v.delta, v.delta == null ? metricText(v, 'delta') : ''].map(q1).join(','));
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
    refresh: () => load(true, true),
    enter: enter,
    setGroup: setGroup,
    clearGroup: clearGroup,
    get active() { return booted; },
    get scope() { return st.group ? 'group' : st.acct; }
  };

})();
