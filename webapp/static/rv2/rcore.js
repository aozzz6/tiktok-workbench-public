/* ============================================================
   rcore.js · 提示词反推 11 个功能单元（U.*）
   每块都带 data-unit，验收脚本据此断言「功能没丢」
   数据只读 window.RVDATA；不发明字段
   ============================================================ */
(function () {
  const S = window.OV;
  const D = () => window.RVDATA;

  const esc = S.esc;
  const mmss = (sec) => {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m ? m + ' 分 ' + String(s).padStart(2, '0') + ' 秒' : s + ' 秒';
  };
  const num = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const modelName = (id) => {
    const m = D().models.find((x) => x.id === id);
    return m ? m.name : (id || '未记录');
  };
  const focusTask = (D0, o) => {
    const id = (o && o.task) || window.RV_FOCUS || D0.focusId;
    // 2026-11-04（D1）：任务列表为空时明确返回 null，不返回 undefined 让调用方去 .id/.account 崩掉。
    return D0.tasks.find((t) => t.id === id) || D0.tasks[0] || null;
  };
  const reportOf = (D0, t) => (t && D0.reports[t.id]) || null;
  const stageTone = (s) => s === 'done' ? 'ok' : s === 'failed' ? 'bad' : s === 'ready' ? 'warn' : 'acc';

  /* ── 顶栏：状态角标 + 动作 ────────────────────────────── */
  function stamps(D0) {
    D0 = D0 || D();
    return '<div class="u-stamps">' +
      '<span class="chip"><i class="dot ok"></i>本地 8789</span>' +
      '<span class="chip">快照 ' + esc(D0.meta.asOf.slice(5)) + '</span>' +
      '<span class="chip acc">' + D0.models.length + ' 个模型</span>' +
      '<span class="chip">上传上限 ' + D0.diag.maxMB + 'MB · ' + D0.diag.maxSec + 's 内</span></div>';
  }
  function actions() {
    return '<div class="u-acts">' +
      '<button class="btn ghost" data-act="refresh">刷新</button>' +
      '<button class="btn" data-act="bundle">素材包</button>' +
      '<button class="btn pri" data-act="create">① 生成素材</button></div>';
  }

  /* ── 1 提交区 ─────────────────────────────────────────── */
  function compose(D0, o = {}) {
    D0 = D0 || D();
    const v = o.variant || 'panel';
    const models = D0.models.map((m) =>
      '<option value="' + esc(m.id) + '"' + (m.id === D0.defaultModel ? ' selected' : '') + '>' + esc(m.name) + '</option>').join('');
    const form =
      '<label class="u-field grow"><span>视频链接</span>' +
      '<input type="text" value="' + esc(o.value || 'https://www.tiktok.com/@' + D0.tasks[0].account + '/video/' + D0.tasks[0].id) +
      '" spellcheck="false" aria-label="视频链接"></label>' +
      '<label class="u-field"><span>反推模式</span><select aria-label="反推模式"><option>原片还原 · 保留原声</option><option>广告拆解 · 旁白改写</option></select></label>' +
      '<label class="u-field"><span>分析模型</span><select aria-label="分析模型">' + models + '</select></label>' +
      '<div class="u-field-acts"><button class="btn pri" data-act="create">① 生成素材</button>' +
      '<button class="btn ghost" data-act="import">导入本地视频</button></div>';
    const note = '<p class="u-note">链接或本地视频（≤ ' + D0.diag.maxMB + 'MB）先出素材，再点「② AI 反推」；原片还原按实际镜头拆，导出多张参考图并保留原音轨，当前支持 ' +
      D0.diag.maxSec + ' 秒以内。</p>';

    if (v === 'inline') {
      return '<section class="u-compose is-inline" data-unit="compose">' +
        '<div class="u-compose-row">' + form + '</div>' + note + '</section>';
    }
    if (v === 'terminal') {
      return '<section class="u-compose is-terminal" data-unit="compose">' +
        '<div class="u-compose-cmd"><b>rv</b> --mode restore --model ' + esc(D0.defaultModel) + ' ' + esc(D0.tasks[0].id) + '</div>' +
        '<div class="u-compose-row">' + form + '</div>' + note + '</section>';
    }
    if (v === 'strip') {
      return '<section class="u-compose is-strip" data-unit="compose">' +
        '<div class="u-compose-lead">新建反推</div><div class="u-compose-row">' + form + '</div>' + note + '</section>';
    }
    return '<section class="u-compose" data-unit="compose">' +
      '<header class="u-compose-h"><h2>新建反推</h2><span class="tiny muted">第一步不花 token，只出素材</span></header>' +
      '<div class="u-compose-row">' + form + '</div>' + note + '</section>';
  }

  /* ── 2 今日结论 ───────────────────────────────────────── */
  function finding(D0, o = {}) {
    D0 = D0 || D();
    const c = D0.counts;
    const head = o.head || ('累计 <b>' + c.total + '</b> 个反推任务，<b>' + c.done + '</b> 个出了报告，<b class="bad">' + c.failed +
      '</b> 个卡在下载，<b class="warn">' + c.ready + '</b> 个素材就绪还没反推');
    const sub = [
      '平均 ' + mmss(D0.avgSec),
      '参考图 ' + D0.refsTotal + ' 张',
      '默认模型 ' + esc(modelName(D0.defaultModel)),
      c.busy ? '<b class="live">' + c.busy + ' 个在跑</b>' : '当前无任务在跑'
    ].join(' · ');
    return '<section class="u-find ' + (o.cls || '') + '" data-unit="finding">' +
      (o.eyebrow === false ? '' : '<div class="eyebrow">' + esc(o.eyebrow || D0.meta.asOf + ' · ' + D0.tasks.length + ' 条记录') + '</div>') +
      '<h1>' + head + '</h1><p class="u-find-sub">' + sub + '</p></section>';
  }

  /* ── 3 KPI ────────────────────────────────────────────── */
  function kpiData(D0) {
    const c = D0.counts;
    const first = D0.tasks[D0.tasks.length - 1];
    return [
      // 2026-11-04（D1）：空任务列表时 first 为 undefined，直接读 .createdDay 会抛 TypeError，
      // 让 reverse_v2.js 的 renderAll 整体失败（反推页只剩壳）。空态退化为「暂无任务」。
      { key: 'all', label: '反推任务', value: c.total, unit: '个', cap: (first ? first.createdDay + ' 起累计' : '暂无任务'), tone: '', count: c.total },
      { key: 'done', label: '出报告', value: c.done, unit: '份', cap: '多段 ' + D0.tasks.filter((t) => t.shots > 1).length + ' · 单段 ' + D0.tasks.filter((t) => t.shots === 1).length, tone: 'live', count: c.done },
      { key: 'failed', label: '失败', value: c.failed, unit: '个', cap: '全部卡在下载原片', tone: 'bad', count: c.failed },
      { key: 'ready', label: '待反推', value: c.ready, unit: '个', cap: '素材已就绪', tone: 'warn', count: c.ready },
      { key: 'avg', label: '平均耗时', value: (D0.avgSec / 60).toFixed(1), unit: '分', cap: '按最后一次连续过程', tone: '' },
      { key: 'refs', label: '参考图', value: D0.refsTotal, unit: '张', cap: '至多 57 张 / 任务', tone: '', count: D0.refsTotal }
    ];
  }
  function kpis(D0, o = {}) {
    D0 = D0 || D();
    const list = o.six === false ? kpiData(D0).slice(0, 4) : kpiData(D0);
    const v = o.variant || 'cards';
    if (v === 'rows') {
      return '<div class="u-krows" data-unit="kpi">' + list.map((k) =>
        '<button class="kr" data-drill="' + k.key + '"><span class="kr-l">' + esc(k.label) + '</span>' +
        '<span class="kr-v tnum">' + k.value + (k.unit ? '<i>' + esc(k.unit) + '</i>' : '') + '</span>' +
        '<span class="kr-c">' + esc(k.cap) + '</span><span class="kr-a">→</span></button>').join('') + '</div>';
    }
    if (v === 'figures') {
      return '<div class="u-figs" data-unit="kpi">' + list.map((k) =>
        '<div class="fig" data-drill="' + k.key + '"><div class="fig-l">' + esc(k.label) + '</div>' +
        '<div class="fig-v">' + k.value + (k.unit ? '<i>' + esc(k.unit) + '</i>' : '') + '</div>' +
        '<div class="fig-c">' + esc(k.cap) + '</div></div>').join('') + '</div>';
    }
    if (v === 'gauges') {
      const g = [
        { k: 'done', l: '出报告率', v: Math.round(D0.counts.done / D0.counts.total * 100), c: D0.counts.done + '/' + D0.counts.total },
        { k: 'ready', l: '素材转化', v: Math.round((D0.counts.done + D0.counts.ready) / D0.counts.total * 100), c: D0.counts.done + '+' + D0.counts.ready + '/' + D0.counts.total },
        { k: 'avg', l: '耗时合规', v: Math.max(0, Math.min(100, Math.round(100 - (D0.avgSec / 60 - 5) * 8))), c: mmss(D0.avgSec) }
      ];
      return '<div class="u-gauges" data-unit="kpi">' + g.map((x) =>
        '<div class="gau" data-drill="' + x.k + '">' + S.gauge(x.v, { w: 168, h: 90 }) +
        '<div class="gau-v">' + x.v + '<i>%</i></div><div class="gau-l">' + esc(x.l) + '</div><div class="gau-c">' + esc(x.c) + '</div></div>').join('') + '</div>';
    }
    if (v === 'mega') {
      return '<div class="u-mega" data-unit="kpi">' + list.slice(0, 4).map((k) =>
        '<div class="mega" data-drill="' + k.key + '"><div class="mega-l">' + esc(k.label) + '</div>' +
        '<div class="mega-v tnum">' + k.value + (k.unit ? '<i>' + esc(k.unit) + '</i>' : '') + '</div>' +
        '<div class="mega-c">' + esc(k.cap) + '</div></div>').join('') + '</div>';
    }
    return '<div class="u-kpis" data-unit="kpi">' + list.map((k) =>
      S.kpi({ ...k, arrow: '⌄' })).join('') + '</div>';
  }

  /* ── 4 阶段漏斗 ───────────────────────────────────────── */
  function queue(D0, o = {}) {
    D0 = D0 || D();
    const v = o.variant || 'flow';
    const rows = D0.stages;
    const max = Math.max(...rows.map((r) => r.n), 1);
    if (v === 'bars') {
      return S.mod({
        key: 'queue', title: o.title || '阶段漏斗', sub: '累计通过 · 并发上限 ' + D0.concurrency,
        body: rows.map((r) => '<div class="u-stage-row"><span class="u-stage-l">' + esc(r.label) + '</span>' +
          '<span class="u-stage-b"><i class="' + (r.key === 'done' ? 'ok' : r.key === 'ready' ? 'warn' : '') + '" style="width:' + Math.round(r.n / max * 100) + '%"></i></span>' +
          '<b class="u-stage-n tnum">' + r.n + '</b></div>').join('')
      });
    }
    if (v === 'columns') {
      return '<section class="u-flow is-columns" data-unit="queue">' + rows.map((r, i) =>
        '<div class="u-flow-col' + (r.key === 'done' ? ' ok' : r.key === 'ready' ? ' warn' : '') + '">' +
        '<div class="u-flow-n tnum">' + r.n + '</div><div class="u-flow-l">' + esc(r.label) + '</div>' +
        (i < rows.length - 1 ? '<span class="u-flow-arw" aria-hidden="true">→</span>' : '') + '</div>').join('') +
        '<div class="u-flow-foot">当前在跑 <b>' + D0.counts.busy + '</b> · 并发上限 ' + D0.concurrency + ' · 排队 0</div></section>';
    }
    return '<section class="u-flow" data-unit="queue">' +
      '<div class="u-flow-head"><h2>' + esc(o.title || '阶段漏斗') + '</h2><span class="tiny muted">累计通过 · 并发上限 ' + D0.concurrency + '</span></div>' +
      '<div class="u-flow-track">' + rows.map((r, i) =>
        '<div class="u-flow-cell' + (r.key === 'done' ? ' is-ok' : r.stuck ? ' is-warn' : '') + '">' +
        '<b class="tnum">' + r.n + '</b><span>' + esc(r.label) + '</span>' +
        (r.stuck ? '<em class="tnum">卡住 ' + r.stuck + '</em>' : '') +
        (i < rows.length - 1 ? '<i class="u-flow-arw" aria-hidden="true"></i>' : '') + '</div>').join('') + '</div>' +
      '<p class="u-flow-note">下载 <b>' + rows[0].n + '</b> → 抽帧 <b>' + rows[1].n + '</b> → 读帧 <b>' + rows[3].n + '</b> → 报告 <b>' + rows[5].n +
      '</b>；失败 2 个停在第 1 段（原片取不到），素材就绪 1 个等第二次点击。</p></section>';
  }

  /* ── 5 任务流 ─────────────────────────────────────────── */
  function taskRow(t, o = {}) {
    const tone = stageTone(t.stage);
    const acts = [];
    if (t.hasReport) acts.push('<button class="btn ghost" data-act="report" data-task="' + t.id + '">报告</button>');
    if (t.bundle) acts.push('<button class="btn ghost" data-act="bundle" data-task="' + t.id + '">素材包</button>');
    if (t.hasReport) acts.push('<button class="btn ghost" data-act="handoff" data-task="' + t.id + '">用这个优化</button>');
    if (t.stage === 'failed' || t.stage === 'ready') acts.push('<button class="btn" data-act="retry" data-task="' + t.id + '">重跑</button>');
    return '<article class="u-task' + (o.focus ? ' is-focus' : '') + '" data-task="' + t.id + '">' +
      '<header class="u-task-h"><span class="chip ' + tone + '">' + esc(t.stageLabel) + '</span>' +
      (t.modeLabel ? '<span class="chip">' + esc(t.modeLabel) + '</span>' : '') +
      (t.origin === 'local' ? '<span class="chip">本地视频</span>' : '') +
      '<span class="u-task-id mono">' + esc(t.id) + '</span>' +
      '<span class="u-task-at tiny muted">' + esc(t.createdDay.slice(5) + ' ' + t.createdHM) + '</span></header>' +
      '<div class="u-task-t">@' + esc(t.account) + (t.nickname ? ' · ' + esc(t.nickname) : '') + '</div>' +
      '<p class="u-task-d">' + esc(t.desc || '（简介为空）') + '</p>' +
      '<div class="u-task-kv">' +
      '<span><i>时长</i><b class="tnum">' + t.duration.toFixed(1) + 's</b></span>' +
      '<span><i>分镜</i><b class="tnum">' + t.shots + '</b></span>' +
      '<span><i>参考图</i><b class="tnum">' + t.refs + '</b></span>' +
      '<span><i>抽帧</i><b class="tnum">' + t.frames + '/' + t.aiFrames + '</b></span>' +
      '<span><i>耗时</i><b class="tnum">' + mmss(t.elapsedSec) + '</b></span></div>' +
      (t.error ? '<p class="u-task-err">' + esc(t.error) + '</p>' : '') +
      '<div class="u-task-bar"><i class="' + tone + '" style="width:' + Math.min(100, t.progress) + '%"></i></div>' +
      '<footer class="u-task-acts">' + acts.join('') + '</footer></article>';
  }

  function tasks(D0, o = {}) {
    D0 = D0 || D();
    const v = o.variant || 'cards';
    const list = o.all ? D0.tasks : D0.tasks.slice(0, o.n || 6);
    const focusId = focusTask(D0, o).id;
    if (v === 'table') {
      return S.mod({
        key: 'tasks', title: o.title || '反推任务', sub: list.length + ' / ' + D0.tasks.length + ' 条',
        actions: '<a class="btn ghost" href="#reverse" data-nav="reverse">全部 →</a>',
        body: '<table class="tb"><thead><tr><th>任务</th><th>账号</th><th class="c">模式</th><th class="r">分镜</th><th class="r">参考图</th><th class="r">耗时</th><th class="r">状态</th></tr></thead><tbody>' +
          list.map((t) => '<tr data-task="' + t.id + '"><td class="mono tiny">' + esc(t.id) + '</td><td>' + esc(t.account) + '</td>' +
            '<td class="c tiny">' + esc(t.modeLabel || '—') + '</td><td class="r tnum">' + t.shots + '</td><td class="r tnum">' + t.refs +
            '</td><td class="r tnum">' + mmss(t.elapsedSec) + '</td><td class="r"><span class="chip ' + stageTone(t.stage) + '">' + esc(t.stageLabel) + '</span></td></tr>').join('') +
          '</tbody></table>',
        foot: '点一行看报告；失败的重跑会复用已抽的帧'
      });
    }
    if (v === 'list') {
      return '<section class="u-tasks is-list" data-unit="tasks"><header class="u-tasks-h"><h2>' + esc(o.title || '任务流') +
        '</h2><span class="tiny muted">' + list.length + ' 条</span></header>' +
        list.map((t) => '<button class="u-tl" data-task="' + t.id + '">' +
          '<span class="chip ' + stageTone(t.stage) + '">' + esc(t.stageLabel) + '</span>' +
          '<span class="u-tl-id mono">' + esc(t.id) + '</span>' +
          '<span class="u-tl-a">@' + esc(t.account) + '</span>' +
          '<span class="u-tl-n tnum">分镜 ' + t.shots + '</span>' +
          '<span class="u-tl-n tnum">图 ' + t.refs + '</span>' +
          '<span class="u-tl-t muted tnum">' + esc(t.createdHM) + '</span></button>').join('') + '</section>';
    }
    if (v === 'board') {
      const cols = [['done', '已完成'], ['ready', '素材就绪'], ['failed', '失败']];
      return '<section class="u-board" data-unit="tasks">' + cols.map(([k, label]) => {
        const rows = D0.tasks.filter((t) => t.stage === k);
        return '<div class="u-board-col"><div class="u-board-h"><b>' + label + '</b><span class="tnum">' + rows.length + '</span></div>' +
          rows.map((t) => '<article class="u-board-card" data-task="' + t.id + '"><div class="u-board-id mono">' + esc(t.id) + '</div>' +
            '<div class="u-board-a">@' + esc(t.account) + '</div>' +
            '<div class="u-board-m tiny muted">分镜 ' + t.shots + ' · 图 ' + t.refs + ' · ' + mmss(t.elapsedSec) + '</div>' +
            '<div class="u-task-bar"><i class="' + stageTone(t.stage) + '" style="width:' + t.progress + '%"></i></div></article>').join('') +
          '</div>';
      }).join('') + '</section>';
    }
    return '<section class="u-tasks" data-unit="tasks"><header class="u-tasks-h"><h2>' + esc(o.title || '任务流') +
      '</h2><span class="tiny muted">最近 ' + list.length + ' 条 · 点卡片切换右侧明细</span></header>' +
      '<div class="u-task-grid">' + list.map((t) => taskRow(t, { focus: t.id === focusId })).join('') + '</div></section>';
  }

  /* ── 6 分镜表 ─────────────────────────────────────────── */
  function shots(D0, o = {}) {
    D0 = D0 || D();
    const t = focusTask(D0, o), R = reportOf(D0, t);
    const v = o.variant || 'table';
    if (!R) {
      return S.mod({ key: 'shots', title: '分镜', sub: '该任务还没有报告',
        body: '<p class="u-empty">' + esc(t.id) + ' 停在「' + esc(t.stageLabel) + '」，没有分镜数据。换个任务看。</p>' });
    }
    const rows = R.shots;
    if (v === 'timeline') {
      return S.mod({
        key: 'shots', title: o.title || '分镜时间轴', sub: rows.length + ' 段 · ' + R.sourceDuration.toFixed(2) + ' 秒',
        body: '<div class="u-tl-shot">' + rows.map((s) => {
          const w = Math.max(4, (s.t1 - s.t0) / R.sourceDuration * 100);
          return '<div class="u-tl-shot-row"><span class="u-tl-t tnum">' + s.t0.toFixed(2) + '–' + s.t1.toFixed(2) + 's</span>' +
            '<span class="u-tl-bar"><i style="margin-left:' + (s.t0 / R.sourceDuration * 100).toFixed(1) + '%;width:' + w.toFixed(1) + '%"></i></span>' +
            '<span class="u-tl-name">' + esc(s.name) + '</span></div>';
        }).join('') + '</div><p class="u-note">切点来自场景检测 + 抽帧复核；无硬切时按动作变化合并。</p>'
      });
    }
    if (v === 'cards') {
      return S.mod({
        key: 'shots', title: o.title || '分镜 · ' + rows.length + ' 段', sub: esc(t.id),
        body: '<div class="u-shot-cards">' + rows.map((s) =>
          '<article class="u-shot-card"><header><b class="tnum">' + String(s.idx).padStart(2, '0') + '</b>' +
          '<span class="tiny muted tnum">' + s.t0.toFixed(2) + '–' + s.t1.toFixed(2) + 's</span></header>' +
          '<h3>' + esc(s.name) + '</h3><p>' + esc(s.action) + '</p>' +
          '<footer class="tiny muted">运镜：' + esc(s.camera) + '</footer></article>').join('') + '</div>'
      });
    }
    return S.mod({
      key: 'shots', title: o.title || '分镜表', sub: rows.length + ' 段 · ' + R.sourceDuration.toFixed(2) + ' 秒 · ' + esc(t.modeLabel || ''),
      body: '<table class="tb u-shot-tb"><thead><tr><th class="c">#</th><th class="r">时间</th><th>镜头</th><th>动作与运镜</th></tr></thead><tbody>' +
        rows.map((s) => '<tr><td class="c tnum">' + s.idx + '</td><td class="r tnum tiny">' + s.t0.toFixed(2) + '–' + s.t1.toFixed(2) + 's</td>' +
          '<td><b>' + esc(s.name) + '</b><div class="tiny muted">运镜：' + esc(s.camera) + '</div></td>' +
          '<td class="tiny">' + esc(s.action) + '</td></tr>').join('') + '</tbody></table>'
    });
  }

  /* ── 7 参考图 / 素材包 ────────────────────────────────── */
  function refs(D0, o = {}) {
    D0 = D0 || D();
    const t = focusTask(D0, o), R = reportOf(D0, t);
    const v = o.variant || 'wall';
    const n = R ? R.gallery : t.refs;
    const items = (R ? R.galleryTail : []).map((g) =>
      '<span class="u-ref" data-tip="' + esc(g.id + ' · ' + g.t + 's') + '"><b class="mono">' + esc(g.id) + '</b><i>' + Number(g.t).toFixed(2) + 's</i></span>').join('');
    const body =
      '<div class="u-ref-' + (v === 'strip' ? 'strip' : v === 'grid' ? 'grid' : 'wall') + '">' + items + '</div>' +
      '<div class="u-ref-kv"><span><i>参考图</i><b class="tnum">' + num(n) + ' 张</b></span>' +
      '<span><i>送模型</i><b class="tnum">' + t.aiFrames + ' 帧</b></span>' +
      '<span><i>素材包</i><b>' + (t.bundle ? 'bundle.zip' : '未生成') + '</b></span>' +
      '<span><i>原声</i><b>' + (R && R.audio.available ? '已保留 · ' + esc(R.audio.codec).toUpperCase() : '无音轨') + '</b></span></div>' +
      (R ? '<p class="u-note">' + esc(R.audio.note) + '</p>' : '');
    if (v === 'panel') {
      return S.mod({ key: 'refs', title: o.title || '参考图与素材包', sub: t.id, body });
    }
    return '<section class="u-refs" data-unit="refs"><header class="u-refs-h"><h2>' + esc(o.title || '参考图与素材包') +
      '</h2><span class="tiny muted">' + esc(t.id) + '</span></header>' + body + '</section>';
  }

  /* ── 8 还原提示词 ─────────────────────────────────────── */
  function prompts(D0, o = {}) {
    D0 = D0 || D();
    const t = focusTask(D0, o), R = reportOf(D0, t);
    const v = o.variant || 'blocks';
    if (!R || !R.prompts.length) {
      return S.mod({ key: 'prompts', title: '还原提示词', sub: '无', body: '<p class="u-empty">该任务没有提示词。素材就绪后点「② AI 反推」才会生成。</p>' });
    }
    const list = o.one ? R.prompts.slice(0, 1) : R.prompts;
    const shown = o.n ? list.slice(0, o.n) : list;
    if (v === 'list') {
      return S.mod({
        key: 'prompts', title: o.title || '还原提示词', sub: '共 ' + list.length + ' 段 · 可直接喂 Seedance 2.0',
        actions: '<button class="btn ghost" data-act="prompts-all">全部 ' + list.length + ' 段</button>',
        body: shown.map((p) => '<div class="u-pr-row"><span class="u-pr-i tnum">' + String(p.idx).padStart(2, '0') + '</span>' +
          '<p>' + esc(p.zh) + '</p><button class="btn ghost" data-act="copy" data-idx="' + p.idx + '">复制</button></div>').join('') +
          (shown.length < list.length ? '<p class="u-note">还有 ' + (list.length - shown.length) + ' 段，点右上「全部」进抽屉看完整提示词。</p>' : '')
      });
    }
    if (v === 'single') {
      const p = list[0];
      return S.mod({
        key: 'prompts', title: o.title || '提示词 · 第 1 段', sub: '共 ' + R.prompts.length + ' 段',
        actions: '<button class="btn ghost" data-act="copy" data-idx="1">复制中文</button>',
        body: '<p class="u-pr-main">' + esc(p.zh) + '</p>' +
          '<p class="u-pr-en">' + esc(p.en) + '</p>' +
          '<p class="u-note">' + esc(p.notes) + '</p>'
      });
    }
    return S.mod({
      key: 'prompts', title: o.title || '还原提示词', sub: list.length + ' 段 · 中文 / 英文 / 多图版',
      body: '<div class="u-pr-blocks">' + list.map((p) =>
        '<article class="u-pr-block"><header><b class="tnum">' + String(p.idx).padStart(2, '0') + '</b>' +
        '<span class="tiny muted">图生视频 · 静音生成</span>' +
        '<button class="btn ghost" data-act="copy" data-idx="' + p.idx + '">复制</button></header>' +
        '<p class="u-pr-zh">' + esc(p.zh) + '</p>' +
        '<p class="u-pr-en">' + esc(p.en) + '</p>' +
        (p.multi ? '<p class="u-pr-multi"><i>多图版</i>' + esc(p.multi) + '</p>' : '') + '</article>').join('') + '</div>'
    });
  }

  /* ── 9 原片口播 ───────────────────────────────────────── */
  function voice(D0, o = {}) {
    D0 = D0 || D();
    const t = focusTask(D0, o);
    const langs = ['德语', '英语', '法语', '西班牙语', '意大利语', '中文', '日语'];
    const state = t.voiceState || 'unknown';
    const label = state === 'spoken' ? '已确认 · ' + esc(t.voiceLang) : state === 'none' ? '已确认 · 无口播' : '尚未确认';
    const opts = langs.map((l) => '<option' + (t.voiceLang === l ? ' selected' : '') + '>' + l + '</option>').join('');
    const body = '<div class="u-voice-row"><span class="chip ' + (state === 'unknown' ? 'warn' : 'ok') + '">' + label + '</span>' +
      '<span class="tiny muted">简介语言猜测：' + esc(t.lang ? t.lang.toUpperCase() : '未知') + '</span></div>' +
      '<div class="u-voice-acts"><label class="u-field"><span>口播语言</span><select aria-label="口播语言" data-voice>' +
      '<option value="unknown">尚未确认</option><option value="none">无口播（仅音乐 / 环境声）</option>' + opts + '</select></label>' +
      '<button class="btn" data-act="save-voice">保存口播信息</button></div>' +
      '<p class="u-note">这里确认的是原视频的口播语言，保存后「提示词优化」自动带入；简介语言只是提示，不代表音频识别。</p>';
    if (o.variant === 'inline') {
      return '<section class="u-voice is-inline" data-unit="voice">' + body + '</section>';
    }
    return S.mod({ key: 'voice', title: o.title || '原片口播', sub: '用户确认 · 写入该视频目录', body });
  }

  /* ── 10 趋势 ──────────────────────────────────────────── */
  function trend(D0, o = {}) {
    D0 = D0 || D();
    const kind = o.kind || 'area';
    const all = D0.day.series;
    const win = o.win || 7;
    const series = all.slice(-win);
    const get = (d) => d.tasks;
    const chartOpt = { h: o.h || 180, w: o.w || 760, get, label: '任务' };
    const svg = kind === 'bars' ? S.bars(series, { ...chartOpt, h: o.h || 180 })
      : kind === 'steps' ? S.steps(series, { ...chartOpt, h: o.h || 170 })
        : S.area(series, chartOpt);
    const active = all.filter((s) => s.tasks).length;
    return S.mod({
      key: 'trend', title: o.title || '每天的反推任务', sub: '近 30 天有 ' + active + ' 天记录',
      cls: o.cls || '',
      actions: '<div class="seg" role="group" aria-label="时间范围"><button class="sg on" data-period="7">7 天</button><button class="sg" data-period="30">30 天</button></div>',
      body: '<div class="u-chart" data-chart="trend">' + svg + '</div>' +
        '<div class="u-chart-foot"><span><i class="lg lg-a"></i>任务数</span><span><i class="lg lg-b"></i>失败</span>' +
        '<span class="muted">峰值 4（10-26）</span><span class="muted">共 ' + D0.counts.total + ' 个</span></div>',
      foot: o.foot
    });
  }

  /* ── 11 环境与模型 ────────────────────────────────────── */
  function diag(D0, o = {}) {
    D0 = D0 || D();
    const v = o.variant || 'strip';
    const d = D0.diag;
    const items = [
      ['ffmpeg', d.ffmpeg ? '就绪' : '缺失', d.ffmpeg],
      ['Python', d.python ? '就绪' : '缺失', d.python],
      ['模型通道', d.models + ' 个可选', d.models > 0],
      ['代理', d.proxy, true],
      ['上传', '≤ ' + d.maxMB + 'MB', true]
    ];
    if (v === 'rows') {
      return S.mod({ key: 'diag', title: o.title || '环境与模型', sub: '5 项', body: items.map(([k, s, ok]) =>
        '<div class="u-dg-row"><span>' + esc(k) + '</span><b class="' + (ok ? 'ok' : 'bad') + '">' + esc(s) + '</b></div>').join('') });
    }
    if (v === 'panel') {
      return S.mod({
        key: 'diag', title: o.title || '环境与模型', sub: '本地 · 无外链',
        body: items.map(([k, s, ok]) => '<div class="u-dg-row"><span>' + esc(k) + '</span><b class="' + (ok ? 'ok' : 'bad') + '">' + esc(s) + '</b></div>').join('') +
          '<p class="u-note">' + esc(d.note) + '</p>'
      });
    }
    return '<section class="u-diag" data-unit="diag">' + items.map(([k, s, ok]) =>
      '<span class="u-dg-i ' + (ok ? 'ok' : 'bad') + '"><i class="dot ' + (ok ? 'ok' : 'bad') + '"></i>' + esc(k) + ' <b>' + esc(s) + '</b></span>').join('') +
      '<span class="u-dg-i muted">' + esc(d.note) + '</span></section>';
  }

  window.RVU = window.U = {
    compose, finding, kpis, kpiData, queue, tasks, taskRow, shots, refs, prompts, voice, trend, diag,
    stamps, actions, mmss, num, modelName, stageTone
  };
})();


