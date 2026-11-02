/* ============================================================
   run2.js · 把「一键运行」换成 S237（任务看板 · P18 奶咖）
   原则：行为 1:1 —— 旧控件（id / onclick / app.js 逻辑）一个都不重写，
         只把它们搬进 S237 的骨架里；新增的只有「看板 / 今日运行 /
         分组 / 环境」这几块纯展示面板（数据来自 /api/runs、/api/groups）。
   改哪里：index.html 只加 1 行 link + 1 行 script；
          app.js 一行不动；其它视图完全不受影响。
   ============================================================ */
(function () {
  'use strict';

  const VIEW_ID = 'view-run';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const hhmm = (sec) => { const d = new Date(Number(sec) * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()); };
  const dayOf = (sec) => { const d = new Date(Number(sec) * 1000); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); };
  const dur = (s) => {
    const n = Math.round(Number(s) || 0);
    if (!n) return '—';
    if (n < 60) return n + 's';
    if (n < 3600) return Math.floor(n / 60) + 'm' + pad(n % 60) + 's';
    return Math.floor(n / 3600) + 'h' + pad(Math.floor((n % 3600) / 60)) + 'm';
  };
  const KIND = { pipeline: '流水线', daily: '日更', ledger: '台账', script: '脚本', repair: '修复' };
  /* 步骤短名（只影响显示；chip 的 textContent 保持原样，getSteps 才不会坏） */
  const SHORT = {
    s0_check: '校验', s1_download: '下载', s2_scrape: '抓取', s3_4_sheet: '建表',
    s5_transcribe: '转写', s5b_products: '商品', s6_translate: '翻译', s7_backfill: '回填',
    s8_gmv: 'GMV', s9_report: '报表'
  };
  const RUNLBL = { running: '运行中', done: '成功', failed: '失败', stopped: '已终止', interrupted: '已中断' };
  const TONE = { running: 'live', done: 'ok', failed: 'bad', stopped: 'warn', interrupted: 'warn' };

  const DATA = { runs: [], groups: [], cfg: null, loading: false, at: 0 };

  async function jget(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(url + ' → ' + r.status);
    return r.json();
  }

  /* ── 挂载 ───────────────────────────────────────────────── */
  function mount() {
    const view = document.getElementById(VIEW_ID);
    if (!view || view.dataset.rv2 === '1') return false;
    try {
      build(view);
      view.dataset.rv2 = '1';
      load();
      return true;
    } catch (e) {
      console.error('[run2] 挂载失败，保留旧界面：', e);
      return false;
    }
  }

  function build(view) {
    const head = $(':scope > .page-head', view);
    const panels = $$(':scope > .panel', view);
    const cfg = panels[0], prog = panels[1], logP = panels[2];

    /* 1. 骨架（S237 / R4） */
    const shell = document.createElement('div');
    shell.className = 'rv2';
    shell.innerHTML =
      '<header class="r4-top">' +
        '<div class="r4-lead"><h1>任务看板</h1><p id="rv2-sub">正在读取今日运行…</p></div>' +
        '<div class="rv2-acts" id="rv2-acts"></div>' +
      '</header>' +
      '<section class="r4-new">' +
        '<header class="r4-nh"><b>＋ 新建任务</b><span>账号 → 范围 → 步骤 → 启动</span>' +
          '<div class="rv2-right" id="rv2-nh-right"></div></header>' +
        '<div class="r4-nb">' +
          '<div class="a-pick" id="rv2-pick"></div>' +
          '<div class="a-range" id="rv2-range"></div>' +
          '<div class="a-lang" id="rv2-lang"></div>' +
          '<div class="a-steps" id="rv2-steps"></div>' +
          '<div class="a-opts" id="rv2-opts"></div>' +
          '<div class="a-go" id="rv2-go"></div>' +
        '</div>' +
      '</section>' +
      '<div class="r4-strip">' +
        '<div class="rv2-prog" id="rv2-prog"></div>' +
        '<div class="rv2-queue" id="rv2-queue"></div>' +
      '</div>' +
      '<div class="r4-board" id="rv2-board">' +
        colHtml('wait', '等待', 0) + colHtml('live', '运行中', 0) + colHtml('ok', '成功', 0) + colHtml('bad', '失败', 0) +
      '</div>' +
      '<div class="r4-bottom">' +
        '<section class="rv2-box" id="rv2-logbox"></section>' +
        '<section class="rv2-box" id="rv2-failbox"></section>' +
      '</div>' +
      '<div class="r4-foot">' +
        '<section class="rv2-box" id="rv2-hist"></section>' +
        '<section class="rv2-box" id="rv2-groups"></section>' +
        '<section class="rv2-box" id="rv2-diag"></section>' +
      '</div>' +
      '<section class="rv2-box" id="rv2-addacc"></section>';
    view.appendChild(shell);
    const addacc = $('#rv2-addacc', shell);

    /* 2. 头部：把「翻译回填」按钮与待译角标搬进动作区 */
    if (head) {
      const acts = $('#rv2-acts', shell);
      const badge = $('#trans-pending-badge', head);
      const refill = $('button[onclick*="backfillTranslation"]', head);
      if (badge) acts.appendChild(badge);
      const refresh = document.createElement('button');
      refresh.className = 'btn ghost sm';
      refresh.textContent = '刷新';
      refresh.onclick = () => load(true);
      acts.appendChild(refresh);
      if (refill) acts.appendChild(refill);
      head.remove();
    }

    /* 3. 配置面板：按控件归属把旧 label / 按钮搬进网格 */
    if (cfg) {
      const pick = $('#rv2-pick', shell), range = $('#rv2-range', shell), lang = $('#rv2-lang', shell);
      const steps = $('#rv2-steps', shell), opts = $('#rv2-opts', shell), go = $('#rv2-go', shell);

      $$(':scope > .form-row', cfg).forEach((row) => {
        if (row.querySelector('#run-account')) {
          $$(':scope > label', row).forEach((l) => {
            if (l.querySelector('#run-account')) pick.appendChild(l);
            else if (l.querySelector('#run-from') || l.querySelector('#run-to')) range.appendChild(l);
            else if (l.querySelector('#run-lang') || l.querySelector('#run-backend')) lang.appendChild(l);
          });
          return;
        }
        if (row.querySelector('#run-multi')) {
          pick.appendChild(row.querySelector('.field') || row.firstElementChild || row);
          return;
        }
        if (row.querySelector('#run-steps')) {
          steps.appendChild(row.querySelector('label.grow') || row);
          return;
        }
        if (row.querySelector('#run-no-resume')) {
          const box = document.createElement('div');
          box.className = 'rv2-checks';
          $$('.chk', row).forEach((c) => box.appendChild(c));
          steps.appendChild(box);
          return;
        }
        if (row.querySelector('button[onclick*="submitRun"]')) {
          go.appendChild(row.querySelector('button[onclick*="submitRun"]'));
          const tip = document.createElement('p');
          tip.className = 'rv2-tip';
          tip.style.cssText = 'font-size:10.5px;color:var(--muted);margin:0;line-height:1.5';
          tip.textContent = '串行执行，共用一条日志；失败不中断后续账号';
          go.appendChild(tip);
          return;
        }
        if (row.querySelector('button[onclick*="presetAll"]') || row.querySelector('button[onclick*="presetCore"]')) {
          $$('button', row).forEach((b) => opts.appendChild(b));
          return;
        }
        if (row.classList.contains('run-add-row')) {
          addacc.appendChild(row);
          return;
        }
        // 其余未归类的行留在原位（防御：以后旧页加字段也不会丢）
        steps.appendChild(row);
      });
      [$('#run-add-form', cfg), ...Array.from(cfg.children)].forEach((el) => {
        if (!el) return;
        if (el.id === 'run-add-form' || el.classList.contains('run-add-row')) addacc.appendChild(el);
        else if (!el.querySelector('#run-add-form')) steps.appendChild(el);
      });
      cfg.remove();
    }

    /* 4. 进度面板：人话进度搬进运行条；失败分析搬进右下 */
    const progh = document.createElement('div');
    progh.className = 'rv2-prog-h';
    progh.innerHTML = '<b>运行进度</b><span id="rv2-prog-note">空闲</span>';
    const progBox = $('#rv2-prog', shell);
    progBox.appendChild(progh);
    if (prog) {
      const rp = $('#run-progress', prog);
      const note = $('#run-progress-note', prog);
      const fa = $('#fail-analysis', prog);
      if (note) { $('#rv2-prog-note').replaceWith(note); note.id = 'run-progress-note'; }
      if (rp) progBox.appendChild(rp);
      if (fa) {
        const fb = $('#rv2-failbox', shell);
        fb.innerHTML = '<header class="rv2-box-h"><b>失败分析</b><span class="rv2-right" id="rv2-failright"></span></header>' +
          '<div class="rv2-box-b" id="rv2-failbody"></div>';
        $('#rv2-failbody', fb).appendChild(fa);
      }
      prog.remove();
    }

    /* 5. 日志面板：真身 pre#log 搬进来，状态徽标 + 终止/清屏搬进头 */
    const lb = $('#rv2-logbox', shell);
    lb.innerHTML = '<header class="rv2-box-h"><b>实时日志</b><span class="rv2-right" id="rv2-logright"></span></header>' +
      '<div id="rv2-logbody"></div>';
    if (logP) {
      const logEl = $('#log', logP);
      const heads = $('.panel-head > div', logP);
      if (heads) $('#rv2-logright', lb).appendChild(heads);
      if (logEl) $('#rv2-logbody', lb).appendChild(logEl);
      logP.remove();
    }

    /* 6. 页脚三块 + 新增账号标题 */
    const hist = $('#rv2-hist', shell), groups = $('#rv2-groups', shell), diag = $('#rv2-diag', shell);
    hist.innerHTML = '<header class="rv2-box-h"><b>今日运行</b><span class="rv2-right" id="rv2-hist-n"></span></header><div class="rv2-box-b" id="rv2-hist-b"></div>';
    groups.innerHTML = '<header class="rv2-box-h"><b>分组</b><span class="rv2-right">点一下填充账号</span></header><div class="rv2-box-b" id="rv2-groups-b"></div>';
    diag.innerHTML = '<header class="rv2-box-h"><b>环境前置</b><span class="rv2-right" id="rv2-diag-n"></span></header><div class="rv2-box-b" id="rv2-diag-b"></div>';
    addacc.insertAdjacentHTML('afterbegin', '<header class="rv2-box-h"><b>新增对标账号</b><span class="rv2-right">注册后即可在本页选用</span></header><div class="rv2-box-b" id="rv2-add-b"></div>');
    const addBody = $('#rv2-add-b', shell);
    $$(':scope > *', addacc).forEach((el) => { if (!el.classList.contains('rv2-box-h') && el.id !== 'rv2-add-b') addBody.appendChild(el); });

    /* 7. 抽屉 + 事件 */
    buildDrawer(shell);
    bind(shell);
    watchStatus();
    watchSteps();
  }

  function colHtml(kind, label, n) {
    return '<section class="r4-col" data-col="' + kind + '">' +
      '<header class="r4-coh"><span class="r4-dot ' + (kind === 'live' ? 'live' : kind) + '"></span><h2>' + label + '</h2><b>' + n + '</b></header>' +
      '<div class="r4-colb" data-colb="' + kind + '"></div></section>';
  }

  /* ── 抽屉 ───────────────────────────────────────────────── */
  let drawer = null, scrim = null;
  function buildDrawer(shell) {
    scrim = document.createElement('div');
    scrim.className = 'rv2-scrim';
    drawer = document.createElement('aside');
    drawer.className = 'rv2-drawer';
    drawer.setAttribute('role', 'dialog');
    scrim.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
    document.body.append(scrim, drawer);
  }
  function closeDrawer() { if (drawer) { drawer.classList.remove('on'); scrim.classList.remove('on'); } }
  function openRun(run) {
    if (!run || !drawer) return;
    drawer.innerHTML =
      '<header class="rv2-drawer-h"><div><div class="rv2-eyebrow">RUN</div><h2>' + esc(run.title || run.id) + '</h2>' +
      '<span style="font-size:11px;color:var(--muted)">' + esc(run.id) + '</span></div>' +
      '<button class="btn ghost sm rv2-x">关闭</button></header>' +
      '<div class="rv2-drawer-b">' +
        '<div class="rv2-kv">' +
          '<span>状态</span><b>' + esc(RUNLBL[run.status] || run.status) + '</b>' +
          '<span>类型</span><b>' + esc(KIND[run.kind] || run.kind || '—') + '</b>' +
          '<span>账号</span><b>' + esc(run.account || '—') + '</b>' +
          '<span>开始</span><b>' + esc(run.at || '—') + '</b>' +
          '<span>耗时</span><b>' + dur(run.duration) + '</b>' +
          '<span>退出码</span><b>' + (run.exit == null ? '—' : run.exit) + '</b>' +
        '</div>' +
        (run.cmd ? '<h3 style="font-size:12px;margin:14px 0 6px">命令</h3><pre>' + esc(run.cmd) + '</pre>' : '') +
      '</div>';
    drawer.classList.add('on');
    scrim.classList.add('on');
    $('.rv2-x', drawer).onclick = closeDrawer;
  }

  /* ── 事件 ───────────────────────────────────────────────── */
  function bind(shell) {
    shell.addEventListener('click', (e) => {
      const g = e.target.closest('.rv2-gt');
      if (g) { fillGroup(g.dataset.g); return; }
      const card = e.target.closest('.r4-card');
      if (card && card.dataset.run) {
        const run = DATA.runs.find((r) => r.id === card.dataset.run);
        if (run) openRun(run);
        return;
      }
      const row = e.target.closest('.rv2-tb tbody tr');
      if (row && row.dataset.run) {
        const run = DATA.runs.find((r) => r.id === row.dataset.run);
        if (run) openRun(run);
      }
    });
  }

  /* 按组填充：直接勾选旧的多选清单，再调旧页自己的计数函数 */
  function fillGroup(name) {
    const list = $('#run-multi-list');
    if (!list || !name) return;
    const names = new Set(DATA.groups.find((x) => x.name === name)?.members || []);
    $$('input', list).forEach((c) => { c.checked = names.has(c.value); });
    try { if (window.updateMultiCount) window.updateMultiCount(); } catch (e) { /* 旧函数签名变了也不影响勾选 */ }
    syncQueue();
    render();
  }

  /* ── 步骤 chips：显示短名，textContent 不动 ──────────────────
     getSteps() / presetCore() 都是 chip.textContent.split(' ')[0] 取步骤键，
     所以这里一个字都不改文本，只用 CSS 把原文藏起来、用 ::after 画短名。 */
  function decorateSteps() {
    $$('#run-steps .chip').forEach((c) => {
      if (c.dataset.rv2 === '1') return;
      const raw = (c.textContent || '').trim();
      const key = raw.split(/\s+/)[0];
      const label = raw.slice(key.length).trim();
      if (!/^s\d/.test(key)) return;                 // 非步骤 chip（如空文本）
      if (c.classList.contains('tb-only')) {          // 「翻译回填」独立开关：只把 s6 前缀藏掉，文本仍不计入 --steps
        c.dataset.short = label || '翻译回填';
        c.title = raw + '（独立开关，不计入 --steps）';
        c.classList.add('rv2-step');
        c.dataset.rv2 = '1';
        return;
      }
      c.dataset.short = SHORT[key] || label.replace(/[（(].*$/, '').slice(0, 2) || key;
      c.title = key + (label ? ' · ' + label : '');
      c.classList.add('rv2-step');
      c.dataset.rv2 = '1';
    });
  }

  function watchSteps() {
    const box = document.getElementById('run-steps');
    if (!box || !window.MutationObserver) return;
    let tick = 0;
    new MutationObserver(() => {
      clearTimeout(tick);
      tick = setTimeout(decorateSteps, 60);
    }).observe(box, { childList: true });
    decorateSteps();
  }

  /* ── 数据 ───────────────────────────────────────────────── */
  async function load(manual) {
    if (DATA.loading) return;
    DATA.loading = true;
    try {
      const [runs, groups, cfg] = await Promise.all([
        jget('/api/runs?limit=24'),
        jget('/api/groups').catch(() => null),
        jget('/api/config').catch(() => null)
      ]);
      DATA.runs = (runs.runs || []).map((r) => ({
        id: r.id, kind: r.kind, title: r.title, account: r.account || '', cmd: r.cmd || '',
        at: r.started_at ? hhmm(r.started_at) : '—',
        day: r.started_at ? dayOf(r.started_at) : '',
        status: r.status === 'interrupted' ? 'interrupted' : (r.status || 'done'),
        duration: r.duration_s || 0, exit: r.exit_code
      }));
      DATA.groups = ((groups && groups.groups) || []).map((g) => ({
        name: g.name, count: g.count || (g.members || []).length,
        members: (g.members || []).map((m) => (typeof m === 'string' ? m : m.account)).filter(Boolean)
      }));
      DATA.cfg = cfg;
      DATA.at = Date.now();
      render();
      if (manual) toastLike('已刷新 · ' + hhmm(Math.round(Date.now() / 1000)));
    } catch (e) {
      console.warn('[run2] 数据加载失败', e);
      const sub = $('#rv2-sub');
      if (sub) sub.textContent = '接口不可用：' + e.message;
    } finally {
      DATA.loading = false;
    }
  }

  const today = () => dayOf(Math.round(Date.now() / 1000));
  const todayRuns = () => DATA.runs.filter((r) => r.day === today());

  function selectedAccounts() {
    try {
      if (typeof window.getSelectedAccounts === 'function') return window.getSelectedAccounts();
    } catch (e) { /* ignore */ }
    const one = $('#run-account');
    return one && one.value ? [one.value] : [];
  }

  function render() {
    decorateSteps();
    const tr = todayRuns();
    const running = DATA.runs.filter((r) => r.status === 'running');
    const done = tr.filter((r) => r.status === 'done');
    const failed = tr.filter((r) => r.status === 'failed' || r.status === 'interrupted');
    const sel = selectedAccounts();
    const waiting = sel.filter((a) => !running.some((r) => (r.account || '').split(',').includes(a)));

    /* 抬头 */
    const sub = $('#rv2-sub');
    if (sub) {
      sub.innerHTML = running.length
        ? '正在跑 <b>' + esc(running[0].account || running[0].title) + '</b> · 今日 <b>' + tr.length + '</b> 次 · 失败 <b>' + failed.length + '</b> 次'
        : '当前空闲 · 今日 <b>' + tr.length + '</b> 次 · 成功 <b>' + done.length + '</b> · 失败 <b>' + failed.length + '</b>';
    }

    /* 四列看板 */
    setCol('wait', waiting.slice(0, 8).map((a) => ({
      cls: '', dot: '', name: a, right: '', text: '已勾选，未开始', run: ''
    })), sel.length);
    setCol('live', running.map((r) => ({
      cls: 'live', dot: 'live', name: r.account || r.title, right: r.at,
      text: (KIND[r.kind] || '任务') + ' · ' + (r.duration ? dur(r.duration) : '运行中'), run: r.id, indet: true
    })), running.length);
    setCol('ok', done.slice(0, 6).map((r) => ({
      cls: 'done', dot: 'ok', name: r.title, right: r.at, text: (KIND[r.kind] || '') + ' · ' + dur(r.duration), run: r.id
    })), done.length);
    setCol('bad', failed.slice(0, 6).map((r) => ({
      cls: 'fail', dot: 'bad', name: r.title, right: r.at,
      text: (KIND[r.kind] || '') + ' · ' + dur(r.duration) + (r.exit ? ' · exit ' + r.exit : ''), run: r.id
    })), failed.length);

    /* 空闲时进度条占位（旧代码开始跑会重写 innerHTML） */
    const rp = $('#run-progress');
    if (rp && !rp.children.length && !running.length) {
      rp.innerHTML = '<span style="font-size:11px;color:var(--muted)">空闲 · 勾选账号后点「开始运行」</span>';
    }

    /* 队列 chips + 进度说明 */
    syncQueue(sel, running);

    /* 失败分析空态 */
    const fb = $('#rv2-failbody');
    const fa = $('#fail-analysis');
    if (fb && fa) {
      const empty = fa.classList.contains('hidden') || !fa.innerHTML.trim();
      fb.classList.toggle('is-empty', empty);
      let ph = fb.querySelector('.rv2-fa-empty');
      if (empty && !ph) {
        ph = document.createElement('p');
        ph.className = 'rv2-fa-empty';
        ph.textContent = '本次没有失败账号；有失败时这里会出现原因与重跑入口。';
        fb.appendChild(ph);
      } else if (!empty && ph) ph.remove();
    }

    /* 今日运行表 */
    const hb = $('#rv2-hist-b');
    if (hb) {
      const rows = tr.slice(0, 8);
      hb.innerHTML = rows.length
        ? '<table class="rv2-tb"><thead><tr><th>时间</th><th>任务</th><th>类型</th><th class="r">耗时</th><th class="r">结果</th></tr></thead><tbody>' +
          rows.map((r) => '<tr data-run="' + esc(r.id) + '"><td>' + esc(r.at) + '</td>' +
            '<td class="n">' + esc(r.title) + '</td><td>' + esc(KIND[r.kind] || r.kind) + '</td>' +
            '<td class="r">' + dur(r.duration) + '</td>' +
            '<td class="r"><span class="rv2-chip ' + (TONE[r.status] || '') + '">' + esc(RUNLBL[r.status] || r.status) + '</span></td></tr>').join('') +
          '</tbody></table>'
        : '<p class="r4-empty">今天还没有运行记录</p>';
      const hn = $('#rv2-hist-n');
      if (hn) hn.textContent = tr.length + ' 次';
    }

    /* 分组 */
    const gb = $('#rv2-groups-b');
    if (gb) {
      gb.innerHTML = DATA.groups.length
        ? '<div class="rv2-groups">' + DATA.groups.map((g) =>
          '<button class="rv2-gt" data-g="' + esc(g.name) + '"><b>' + esc(g.name) + '</b><span>' + g.count + ' 个账号</span></button>').join('') + '</div>'
        : '<p class="r4-empty">没有分组数据</p>';
    }

    /* 环境前置 */
    const db = $('#rv2-diag-b'), dn = $('#rv2-diag-n');
    const dg = (DATA.cfg && DATA.cfg.diagnostics) || null;
    if (db) {
      if (dg) {
        const items = [
          ['pipeline 目录', dg.pipeline_exists, dg.pipeline_dir],
          ['python 虚拟环境', dg.python_exists, dg.python],
          ['accounts.json', dg.accounts_exists, ((DATA.cfg && DATA.cfg.accounts) || []).length + ' 个启用'],
          ['输出目录', dg.output_exists, dg.output_exists ? '就绪' : '缺失'],
          ['代理', !!dg.proxy, dg.proxy],
          ['台账文件', !!dg.ledger, (dg.ledger || '').split(/[\\/]/).pop()]
        ];
        db.innerHTML = '<div class="rv2-diag">' + items.map(([n, ok, v]) =>
          '<div class="rv2-dl"><i class="' + (ok ? '' : 'bad') + '"></i><span>' + esc(n) + '</span><b>' + esc(v || '—') + '</b></div>').join('') + '</div>';
        if (dn) dn.textContent = items.filter((x) => x[1]).length + '/' + items.length + ' 就绪';
      } else {
        db.innerHTML = '<p class="r4-empty">没有诊断数据</p>';
      }
    }
  }

  function setCol(kind, cards, n) {
    const col = $('[data-col="' + kind + '"]');
    if (!col) return;
    $('.r4-coh b', col).textContent = n;
    const box = $('[data-colb="' + kind + '"]', col);
    box.innerHTML = cards.length
      ? cards.map((c) =>
        '<article class="r4-card ' + c.cls + '"' + (c.run ? ' data-run="' + esc(c.run) + '"' : '') + ' title="' + esc(c.name) + '">' +
          '<div class="r4-ch"><span class="r4-dot ' + (c.dot || '') + '"></span><b>' + esc(c.name) + '</b><span>' + esc(c.right || '') + '</span></div>' +
          '<p>' + esc(c.text || '') + '</p>' + (c.indet ? '<span class="r4-bar"><b></b></span>' : '') +
        '</article>').join('')
      : '<p class="r4-empty">' + (kind === 'wait' ? '没有勾选账号' : kind === 'live' ? '没有在跑的任务' : '暂无') + '</p>';
  }

  function syncQueue(sel, running) {
    sel = sel || selectedAccounts();
    running = running || DATA.runs.filter((r) => r.status === 'running');
    const box = $('#rv2-queue');
    if (!box) return;
    const runningAcc = new Set();
    running.forEach((r) => String(r.account || '').split(',').forEach((a) => a && runningAcc.add(a)));
    box.innerHTML = sel.slice(0, 14).map((a) =>
      '<span class="rv2-chip ' + (runningAcc.has(a) ? 'live' : '') + '">' + (runningAcc.has(a) ? '● ' : '') + esc(a) + '</span>').join('') ||
      '<span class="rv2-chip">未选账号</span>';
    const note = $('#run-progress-note');
    if (note) note.textContent = running.length ? '运行中 · ' + dur(running[0].duration) : '空闲';
  }

  /* 运行状态徽标变了（旧页在写），顺手把看板/进度也刷一下 */
  function watchStatus() {
    const badge = $('#log-status');
    if (!badge || !window.MutationObserver) return;
    let last = badge.textContent;
    new MutationObserver(() => {
      const now = badge.textContent;
      if (now === last) return;
      last = now;
      syncQueue();
      setTimeout(() => load(), 800);
    }).observe(badge, { childList: true, characterData: true, subtree: true });
  }

  /* 复用页面自带的 toast（如果有） */
  function toastLike(msg) {
    try {
      if (typeof window.toast === 'function') return window.toast(msg);
    } catch (e) { /* ignore */ }
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('on');
    setTimeout(() => el.classList.remove('on'), 1800);
  }

  /* 勾选变化 → 队列 chips 跟着动（旧页的 change 事件在 document 上） */
  let tick = 0;
  document.addEventListener('change', (e) => {
    if (!e.target.closest || !e.target.closest('#run-multi-list')) return;
    clearTimeout(tick);
    tick = setTimeout(() => { syncQueue(); render(); }, 120);
  });

  /* 点「开始运行 / 终止」后，尽快刷一次看板 */
  ['submitRun', 'stopCurrent', 'setAllAccounts', 'setAccountsByGroup', 'presetAll', 'presetCore'].forEach((fn) => {
    const orig = window[fn];
    if (typeof orig !== 'function') return;
    window[fn] = function () {
      const out = orig.apply(this, arguments);
      setTimeout(() => { syncQueue(); load(); }, 900);
      return out;
    };
  });

  /* ── 启动 ───────────────────────────────────────────────── */
  function boot() {
    if (!mount()) return;
    setInterval(() => {
      const view = document.getElementById(VIEW_ID);
      if (view && view.classList.contains('active')) load();
    }, 12000);
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(boot, 300);
  } else {
    document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 300));
  }
  window.__RUN2__ = { mount, load, openRun };
})();
