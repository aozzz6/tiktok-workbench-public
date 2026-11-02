/* ============================================================
   runtime.js · 提示词优化 · 中控台（T106）线上运行时
   状态 → PODATA → 渲染；所有数字来自接口，不造数据
   接口：/api/prompt-optimizer/{profile,analyze,generate,self-check}
        /api/ps/product-scan、/api/reverse/list、/tk_reverse/<id>/report.json
   ============================================================ */
(function () {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* 容器：独立页用 #stage；主壳里用 #pv2 / #pv2-stage（抽屉、toast 都挂在这个根里） */
  const ROOT = document.getElementById('pv2') || document.body;
  const stage = document.getElementById('pv2-stage') || document.getElementById('stage');

  const LS_DRAFT = 'wb.prompt.optimizer.v1';
  const LS_HIST = 'wb.prompt.optimizer.history.v1';
  const LS_ARCH = 'wb.prompt.optimizer.archive.v1';

  const DEFAULTS = { source: '', product: '', facts: '', refs: '', language: '', duration: '30', clipLimit: '15', strategy: 'split', ratio: '9:16', mode: 'text', model: '' };

  const state = {
    form: { ...DEFAULTS },
    benchmark: null,
    benchmarks: [],
    analysis: null,
    result: null,
    history: [],
    images: [],            /* 产品图缩略图 {name, thumb}；只存小图，原图只在识别那一次用 */
    scan: null,
    factRows: [],          /* 事实条（facts.js 的行模型；state.form.facts 始终是它的序列化结果） */
    forceGen: false,       /* 「仍然生成」跳过阻断项 */
    ui: { factsOpen: false, paramsOpen: false, checkOpen: false },   /* C1 折叠态的展开状态 */
    histQuery: '', histSel: [],
    busy: '',
    error: '',
    lastRun: null,
    modelName: ''
  };

  /* ── 本地草稿（与旧页共用同一个 key） ─────────────────── */
  function loadDraft() {
    try {
      const d = JSON.parse(localStorage.getItem(LS_DRAFT) || 'null');
      if (d && d.form) state.form = { ...DEFAULTS, ...d.form };
      if (d && d.benchmark) state.benchmark = d.benchmark;
      if (d && d.analysis) state.analysis = d.analysis;
      if (d && d.result) state.result = d.result;
      /* 产品图缩略图随草稿走：刷新后还看得见用的是哪张图 */
      if (d && Array.isArray(d.images)) state.images = d.images.filter((x) => x && x.thumb).slice(0, 3);
    } catch (e) { /* 草稿坏了就用默认 */ }
    try { state.history = JSON.parse(localStorage.getItem(LS_HIST) || '[]').slice(0, 5); } catch (e) { state.history = []; }
    try { state.benchmarks = JSON.parse(localStorage.getItem(LS_ARCH) || '[]').slice(0, 20); } catch (e) { state.benchmarks = []; }
    /* ★ 2026-10-30：草稿里的 benchmark 与归档列表是两份 JSON 解析出来的不同对象，
       同 id 时要指回列表里的那一个 —— 否则补全（enrichArchive）只改列表、不改当前卡。 */
    if (state.benchmark) {
      const same = state.benchmarks.find((b) => b.id === state.benchmark.id);
      if (same) state.benchmark = same;
      else state.benchmarks.unshift(state.benchmark);
    }
    /* 旧草稿的 facts 是自由文本：进内存就拆成事实条，用户下次打开就是结构化视图 */
    state.factRows = window.FACTS ? window.FACTS.parse(state.form.facts) : [];
  }

  function saveDraft() {
    try {
      localStorage.setItem(LS_DRAFT, JSON.stringify({
        form: state.form, benchmark: state.benchmark, analysis: state.analysis, result: state.result,
        images: state.images
      }));
    } catch (e) { /* 超出配额就放弃保存，不打断流程 */ }
  }

  /* ── 接口 ────────────────────────────────────────────── */
  let base = null;
  async function endpoint() {
    if (base !== null) return base;
    for (const b of ['', 'http://127.0.0.1:8791']) {
      try {
        const r = await fetch(b + '/api/prompt-optimizer/profile', { signal: AbortSignal.timeout(3000) });
        if (!r.ok) continue;
        const d = await r.json();
        if (d && d.platform) { base = b; return b; }
      } catch (e) { /* 试下一个 */ }
    }
    base = '';
    return '';
  }

  async function post(path, body, timeoutMs) {
    const b = await endpoint();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 280000);
    try {
      const r = await fetch(b + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: ctrl.signal
      });
      const data = await r.json().catch(() => ({ detail: '服务返回 ' + r.status + '，请检查服务是否已更新' }));
      if (!r.ok) throw Error(data.detail || ('请求失败（' + r.status + '）'));
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw Error('等待超时，任务可能仍在后台跑；稍后用「历史」恢复');
      throw e;
    } finally { clearTimeout(timer); }
  }

  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!r.ok) throw Error('读取失败（' + r.status + '）');
    return r.json();
  }

  /* ── 时长计划（与后端 inputs() 同口径） ──────────────── */
  function plan() {
    const f = state.form;
    const duration = Number(f.duration) || 0;
    const clip = Number(f.clipLimit) || 0;
    const total = f.strategy === 'split' ? duration : Math.min(duration, clip);
    const count = clip ? Math.max(1, Math.ceil(total / clip)) : 0;
    const ticks = Math.round(total * 10);
    const segments = count ? Array.from({ length: count }, (_, i) => (Math.floor(ticks / count) + (i < ticks % count ? 1 : 0)) / 10) : [];
    let error = '';
    if (!duration || duration < 4 || duration > 60) error = '总时长须在 4–60 秒';
    else if (!clip || clip < 4 || clip > 15) error = '单段上限须在 4–15 秒';
    else if (count > 4) error = '一次最多生成 4 段，请缩短总时长或提高单段上限';
    else if (total / count < 4) error = '单段不足 4 秒，请调整总时长或单段上限';
    return { total, clipLimit: clip, count, segments, error, text: error || (duration + ' 秒 → ' + count + ' 段 × ' + segments[0] + ' 秒') };
  }

  function fingerprint() { return JSON.stringify(state.form); }
  function stale() { return !!state.result && state.result.fingerprint !== fingerprint(); }

  /* ── 校验（与后端一致，提前拦） ───────────────────────── */
  function check(generating) {
    const f = state.form;
    if (!f.source.trim()) throw Error('请先粘贴或导入对标提示词');
    if (f.source.length > 24000) throw Error('对标提示词最多 24000 字');
    if (!f.product.trim()) throw Error('请填写你的产品名称');
    if (f.product.length > 150) throw Error('产品名称最多 150 字');
    if (!f.facts.trim()) throw Error('请填写已确认的产品信息');
    if (f.facts.length > 5000) throw Error('产品信息最多 5000 字');
    if (f.mode !== 'text' && !f.refs.trim()) {
      // 2026-10-29：这条以前只说「需要说明」，不说在哪填；而输入框在「②」里、
      // 默认又在折叠线以下，于是每次都只看到红条、找不到改的地方。现在直接
      // 展开并滚动到该字段、聚焦，并在文案里写清两条出路。
      const box = $('[data-refs]');
      if (box) {
        box.hidden = false;
        try { box.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
        catch (e) { box.scrollIntoView(); }
        box.classList.add('is-bad');
        const ta = box.querySelector('textarea');
        if (ta) ta.focus();
        setTimeout(() => box.classList.remove('is-bad'), 2400);
      }
      throw Error('图生/多素材模式需要说明会上传哪些参考素材：在「② 换成我的产品 → 参考素材说明」填一句即可，或把「生成模式」切回「文生」');
    }
    if (!generating) return;
    if (!f.language.trim()) throw Error('请选择口播语言');
    const p = plan();
    if (p.error) throw Error(p.error);
  }

  function requestBody() {
    const f = state.form;
    return {
      source: f.source, product: f.product, facts: f.facts, refs: f.refs,
      language: f.language, mode: f.mode, model: f.model,
      duration: Number(f.duration), clip_limit: Number(f.clipLimit),
      strategy: f.strategy, ratio: f.ratio
    };
  }

  /* ── 数据适配：state → window.PODATA ─────────────────── */
  const KIND = ['优化版', 'B1', 'B2', 'B3', 'C1', 'C2'];
  const stripTitle = (s) => String(s || '').replace(/^(优化版|B[1-3]|C[1-2])\s*[：:]\s*/, '');

  const emptyAnalysis = () => ({ summary: '', language: '', duration: null, ratio: '', replacement: '', strengths: [], issues: [], changes: [], missing: [], model: '' });
  const emptySelf = () => ({ status: 'manual', checkedAt: '', method: 'local_rules', ruleGate: { status: 'manual', reasons: ['生成后自动检查'] }, versions: [], comparisons: [], summary: { versions: 0, rule_passed: 0, rule_manual: 0, needs_revision: 0, manual: 0, pairs: 0 }, limitations: [] });

  function mapVersions(r) {
    if (!r || !Array.isArray(r.versions)) return [];
    return r.versions.map((v, i) => {
      /* 历史上的旧条目可能是残缺的（旧版本存的、或存到一半）：这里兜住，别让整页渲染崩掉 */
      const clips = (Array.isArray(v.clips) ? v.clips : []).map((c) => ({ ...c, shots: Array.isArray(c.shots) ? c.shots : [] }));
      const dialogue = clips.flatMap((c) => c.shots.map((s) => s.dialogue).filter(Boolean));
      const shots = clips.reduce((n, c) => n + c.shots.length, 0);
      const chars = clips.reduce((n, c) => n + (c.prompt || '').length, 0);
      return {
        id: v.id || 'v' + i, kind: v.kind, label: KIND[i] || v.kind,
        title: stripTitle(v.title), title_full: v.title,
        difference: v.difference, theme: v.theme, character: v.character, look: v.look, camera: v.camera,
        sound: v.sound, hook: v.hook, scene: v.scene, story: v.story, style: v.style,
        warnings: v.warnings || [], clips, dialogue, shots, chars,
        words: dialogue.join(' ').split(/\s+/).filter(Boolean).length
      };
    });
  }

  function mapSelf(sc) {
    if (!sc || !Array.isArray(sc.versions)) return emptySelf();
    return {
      status: sc.status, checkedAt: sc.checked_at || sc.checkedAt || '', method: sc.method || 'local_rules',
      ruleGate: sc.rule_gate || sc.ruleGate || { status: 'manual', reasons: [] },
      versions: (sc.versions || []).map((r) => ({ ...r, title: stripTitle(r.title) })),
      comparisons: sc.comparisons || [], summary: sc.summary || emptySelf().summary,
      limitations: sc.limitations || []
    };
  }

  /* ── 事实条 + 生成前检查（2026-10-30 A+C） ─────────────
     口径：state.form.facts 永远是事实条的序列化文本，后端不用改；
     事实条只是同一份数据的可编辑视图。 */
  function factAnalysis() {
    const F = window.FACTS;
    if (!F) return { rows: [], counts: { total: 0, ok: 0, dup: 0, conflict: 0, weak: 0, empty: 0 } };
    return F.analyze(state.factRows || []);
  }

  function metaText(c) {
    const parts = [c.total + ' 条'];
    if (c.dup) parts.push(c.dup + ' 重复');
    if (c.conflict) parts.push(c.conflict + ' 冲突');
    if (c.weak) parts.push(c.weak + ' 需核验');
    return parts.join(' · ');
  }

  /* 改事实条 → 立刻写回 state.form.facts（后端只看这个字符串） */
  function writeFacts(rows) {
    state.factRows = rows;
    if (window.FACTS) state.form.facts = window.FACTS.serialize(rows);
    saveDraft();
  }

  function modeName(m) { return m === 'image' ? '图生' : m === 'reference' ? '多素材参考' : '文生'; }

  /* 生成前检查：ok / warn / bad；bad 即阻断项 */
  function checklist() {
    const f = state.form;
    const c = factAnalysis().counts;
    const p = plan();
    const items = [];
    const src = (f.source || '').trim();
    const b = state.benchmark || {};

    if (src) {
      const bits = [];
      if (b.account) bits.push('@' + b.account);
      if (b.shots) bits.push(b.shots + ' 分镜');
      if (b.duration) bits.push(b.duration + ' 秒');
      items.push({ k: '对标结构', v: (bits.length ? bits.join(' · ') + ' · ' : '') + src.length + ' 字', st: 'ok' });
    } else {
      items.push({ k: '对标结构', v: '还没有对标：从反推报告导入，或粘到 ①', st: 'bad', act: 'goto-source', actText: '去 ①' });
    }

    items.push(f.product.trim()
      ? { k: '我的产品', v: f.product.trim(), st: 'ok' }
      : { k: '我的产品', v: '还没填产品名称', st: 'bad' });

    if (!c.total) {
      items.push({ k: '已确认事实', v: '一条都没有，模型会凭空编产品', st: 'bad', act: 'facts-open', actText: '去填' });
    } else if (c.conflict) {
      items.push({ k: '已确认事实', v: c.total + ' 条 · ' + c.conflict + ' 处冲突', st: 'bad', act: 'facts-tidy', actText: '整理' });
    } else if (c.dup || c.weak) {
      items.push({
        k: '已确认事实', st: 'warn', act: 'facts-tidy', actText: '整理',
        v: c.total + ' 条' + (c.dup ? ' · ' + c.dup + ' 重复' : '') + (c.weak ? ' · ' + c.weak + ' 需核验' : '')
      });
    } else {
      items.push({ k: '已确认事实', v: c.total + ' 条 · 无重复无冲突', st: 'ok' });
    }

    if (f.mode === 'text') {
      items.push({ k: '参考素材', v: '文生模式不需要，切到图生/多素材才要写', st: 'ok' });
    } else if (!(f.refs || '').trim()) {
      items.push({ k: '参考素材', v: '选了' + modeName(f.mode) + '，但没说会上传哪些素材', st: 'bad', act: 'refs-fill', actText: '补全' });
    } else {
      items.push({ k: '参考素材', v: modeName(f.mode) + ' · ' + f.refs.trim().length + ' 字', st: 'ok' });
    }

    items.push(p.error
      ? { k: '时长计划', v: p.error, st: 'bad' }
      : { k: '时长计划', v: p.text + '（单段上限 ' + p.clipLimit + ' 秒）', st: 'ok' });

    return { items, blockers: items.filter((i) => i.st === 'bad').length };
  }

  /* 打字时就地刷新计数与检查条，不重绘整页（否则输入框掉焦点） */
  function refreshFactsUI() {
    if (!stage) return;
    const fa = factAnalysis();
    $$('[data-facts-meta]').forEach((el) => {
      el.textContent = factSummaryText(fa.counts);
      const row = el.closest('.u-sum');
      if (row) {
        row.classList.toggle('bad', !!fa.counts.conflict);
        row.classList.toggle('warn', !fa.counts.conflict && !!(fa.counts.dup || fa.counts.weak));
      }
    });
    patchCheckArea();
  }

  /* 检查条整块重画（条 + 展开的明细）。★ 九个单元在 window.U，window.PO 是原子件 */
  function patchCheckArea() {
    const box = $('[data-checkbox]');
    const U = window.U;
    if (!box || !U || !U.checkArea) return;
    adapt();
    const host = box.closest('[data-chk-host]');
    const ck = window.PODATA.check || { items: [] };
    box.outerHTML = U.checkArea(state.ui);
    /* 没问题时连外面那层一起收起，② 底部不留空白条 */
    if (host) host.hidden = !(ck.items || []).some((i) => i.st === 'bad' || i.st === 'warn');
  }

  /* 单条细节的状态标记就地同步：打字时只改这一行，不重绘整页（重绘会顶掉光标） */
  const FACT_ST = { ok: ['ok', '✓'], dup: ['warn', '重复'], conflict: ['bad', '冲突'], weak: ['warn', '需核验'], empty: ['bad', '空'] };

  function syncFactBadge(input, id) {
    const F = window.FACTS;
    const li = input.closest('.u-fact');
    if (!F || !li) return;
    const cur = F.analyze(state.factRows || []).rows.filter((r) => r.id === id)[0];
    if (!cur) return;
    li.className = 'u-fact is-' + cur.st;
    const st = li.querySelector('.u-fact-st');
    const hit = FACT_ST[cur.st] || ['', ''];
    if (st) { st.className = 'u-fact-st ' + hit[0]; st.textContent = hit[1]; }
  }

  function factSummaryText(c) {
    const flags = [];
    if (c.conflict) flags.push(c.conflict + ' 冲突');
    if (c.dup) flags.push(c.dup + ' 重复');
    if (c.weak) flags.push(c.weak + ' 需核验');
    let txt = c.total ? c.total + ' 条' : '还没有';
    if (flags.length) txt += ' · ' + flags.join(' · ');
    else if (c.total) txt += ' · 都可确认';
    return txt;
  }

  /* ── 生成进度条（按时间估算；界面明写「预计」，不假装真实进度） ── */
  const GEN_EST = 90;          /* 秒：60–120 秒取中值 */
  let genTimer = null;

  /* 前段走快、后段走慢：一次 60–120 秒的任务，匀速条前一分钟几乎不动，看着像卡死 */
  const GEN_CURVE = [[0, 4], [6, 16], [15, 32], [30, 50], [50, 66], [75, 78], [95, 86], [150, 93]];

  function genPctAt(sec) {
    const c = GEN_CURVE;
    if (sec <= c[0][0]) return c[0][1];
    for (let i = 1; i < c.length; i++) {
      if (sec <= c[i][0]) {
        const [t0, p0] = c[i - 1], [t1, p1] = c[i];
        return p0 + ((sec - t0) / (t1 - t0)) * (p1 - p0);
      }
    }
    return c[c.length - 1][1];
  }

  function genPaint() {
    const p = state.progress;
    if (!p) return;
    const sec = Math.round((Date.now() - p.t0) / 1000);
    $$('[data-gen-bar]').forEach((el) => { el.style.width = p.pct.toFixed(1) + '%'; });
    $$('[data-gen-pct]').forEach((el) => { el.textContent = Math.round(p.pct) + '%'; });
    $$('[data-gen-phase]').forEach((el) => { el.textContent = p.phase; });
    $$('[data-gen-elapsed]').forEach((el) => { el.textContent = '已用 ' + sec + ' 秒'; });
  }

  function genTick() {
    const p = state.progress;
    if (!p) return;
    const sec = (Date.now() - p.t0) / 1000;
    p.elapsed = sec;
    p.pct = genPctAt(sec);   /* 曲线估算；最后一格停在 93%，等真结果回来才给 100% */
    p.phase = sec < 6 ? '正在读对标结构' : sec < 15 ? '正在拆段与配比' : sec < 75 ? '正在写六个版本' : '正在做结构自检';
    genPaint();
  }

  function genStart() {
    genStop();
    state.progress = { t0: Date.now(), pct: 2, phase: '准备中', elapsed: 0 };
    genTimer = setInterval(genTick, 500);
  }

  function genStop(done) {
    if (genTimer) { clearInterval(genTimer); genTimer = null; }
    if (!state.progress) return;
    if (done) {
      state.progress.pct = 100;
      state.progress.phase = '完成';
      genPaint();
      setTimeout(() => { state.progress = null; }, 900);
    } else {
      state.progress = null;
    }
  }

  /* 按当前模式补一句素材说明 */
  function fillRefs() {
    const f = state.form;
    const b = state.benchmark || {};
    const parts = [];
    if (f.mode === 'image') parts.push('首图作为起始画面，锁定产品外观与颜色');
    else parts.push('产品正面图与细节图锁定外观、材质与颜色');
    parts.push('对标' + (b.id ? ' ' + b.id : '视频') + ' 只参考构图、运镜与节奏，不复制其中的竞品和口播');
    f.refs = parts.join('；') + '。';
    saveDraft();
    render();
    toast('已按「' + modeName(f.mode) + '」补全参考素材说明');
  }

  /* 事实条动作：增 / 删 / 选一条 / 换类目 / 整理 / 去重 / 只留可确认 */
  function factsAction(a, fid) {
    const F = window.FACTS;
    if (!F) return;
    const rows = (state.factRows || []).slice();
    const row = fid ? rows.filter((r) => r.id === fid)[0] : null;

    if (a === 'facts-add') {
      const nr = { id: F.nid(), key: '卖点', value: '' };
      writeFacts(rows.concat([nr]));
      render();
      const input = $('.u-fact[data-fid="' + nr.id + '"] .u-fact-v');
      if (input) input.focus();
      return;
    }
    if (a === 'facts-del') { writeFacts(rows.filter((r) => r.id !== fid)); render(); return; }
    if (a === 'facts-pick') {
      if (!row) return;
      writeFacts(rows.filter((r) => r.id === fid || r.key !== row.key));
      render();
      toast('已保留「' + row.value + '」，同类其它值已删');
      return;
    }
    if (a === 'facts-key') {
      if (!row) return;
      const i = F.KEYS.indexOf(row.key);
      row.key = F.KEYS[(i + 1) % F.KEYS.length];
      writeFacts(rows);
      render();
      return;
    }
    if (a === 'facts-tidy') {
      const before = rows.length;
      writeFacts(F.tidy(rows));
      render();
      toast('已整理：' + before + ' → ' + state.factRows.length + ' 条');
      return;
    }
    if (a === 'facts-dedupe') {
      const before = rows.length;
      writeFacts(F.dedupe(rows));
      render();
      toast('已去重：' + before + ' → ' + state.factRows.length + ' 条');
      return;
    }
    if (a === 'facts-keep-ok') {
      const keep = F.analyze(rows).rows.filter((r) => r.st === 'ok');
      writeFacts(keep.map((r) => ({ id: r.id, key: r.key, value: r.value, src: r.src })));
      render();
      toast('已只留可确认的 ' + keep.length + ' 条');
      return;
    }
  }

  function adapt() {
    const f = state.form;
    const p = plan();
    const r = state.result;
    const versions = mapVersions(r);
    const selfCheck = r && r.self_check ? mapSelf(r.self_check) : emptySelf();
    const ana = state.analysis || emptyAnalysis();
    const at = state.lastRun ? state.lastRun.at : '—';
    const fa = factAnalysis();
    const ck = checklist();
    window.PODATA = {
      meta: {
        asOf: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
        platform: (r && r.platform) || 'TikTok Symphony / Dreamina Seedance 2.0',
        model: state.modelName || (r && r.model) || '工作台默认',
        api: '/api/prompt-optimizer', input: ''
      },
      run: {
        at, costSec: state.lastRun ? state.lastRun.costSec : 0, status: r ? 'done' : 'idle',
        stale: stale(), gate: (selfCheck.ruleGate && selfCheck.ruleGate.reasons && selfCheck.ruleGate.reasons[0]) || ''
      },
      plan: { total: p.total, clipLimit: p.clipLimit, strategy: f.strategy, ratio: f.ratio, count: p.count, segments: p.segments, text: p.text },
      form: { ...f, duration: String(f.duration), clipLimit: String(f.clipLimit), model: f.model || '工作台默认' },
      benchmark: state.benchmark || { id: '', name: '尚未选择对标', duration: 0, shots: 0, frames: 0, refs: 0, mode: '', model: '', at: '', audio: '', source: '', unc: 0 },
      benchmarks: state.benchmarks,
      analysis: ana,
      next: null,
      images: state.images,
      factRows: fa.rows,
      factCounts: fa.counts,
      check: ck,
      progress: state.progress || null,
      scan: state.scan || { state: 'empty', hint: '拖入产品图后自动识别品类、材质、颜色、场景与可核验卖点' },
      versions, selfCheck,
      history: state.history.map((h) => ({
        at: h.at, title: h.title, product: h.product, language: h.language,
        duration: h.duration, strategy: h.strategy, versions: h.versions, status: h.status, texts: h.texts || [],
        id: h.id || '', ts: h.ts || 0, mode: h.mode || '', factsCount: h.factsCount || 0,
        form: h.form || null,
        same: sameInput(h)
      })),
      hist: { query: state.histQuery || '', sel: state.histSel || [], limit: HIST_LIMIT },
      notes: (r && r.notes) || [],
      counts: {
        versions: versions.length,
        clips: versions.reduce((n, v) => n + v.clips.length, 0),
        shots: versions.reduce((n, v) => n + v.shots, 0),
        dialogue: versions.reduce((n, v) => n + v.dialogue.length, 0),
        chars: versions.reduce((n, v) => n + v.chars, 0)
      },
      ui: { ...state.ui, busy: !!state.busy, error: state.error }
    };
    window.PO.rebuildTexts();
  }

  /* ── 渲染 ────────────────────────────────────────────── */
  function render() {
    if (!stage) return;
    adapt();
    stage.innerHTML = window.ARCH.T1.render();
    bindFields();
    filterModels();
    decorateHead();
    autosize();
    if (state.histQuery) filterHistory(state.histQuery);   /* 重绘后保持搜索过滤 */
  }

  /* 长文本域按内容长高，别把字裁掉（上限各留一屏内的高度） */
  function autosize() {
    [['source', 210], ['facts', 240]].forEach(([k, max]) => {
      const el = $('[data-field="' + k + '"]');
      if (!el || !el.offsetParent) return;    /* 折叠在 <details> 里的先不量 */
      el.style.height = 'auto';
      el.style.height = Math.min(max, el.scrollHeight + 2) + 'px';
    });
  }

  /* 页头右侧补一个「旧版」入口（不改九单元） */
  function decorateHead() {
    const actions = stage.querySelector('.hd-actions');
    if (!actions || actions.querySelector('[data-act="old"]')) return;
    const a = document.createElement('a');
    a.className = 'btn sm ghost';
    a.href = '?old=1#prompt';
    a.dataset.act = 'old';
    a.textContent = '旧版';
    a.title = '回到旧版三段式界面（?old=1）';
    actions.appendChild(a);
  }

  /* 只更新与输入联动的几处，避免整页重绘丢焦点 */
  function syncDerived() {
    const p = plan();
    $$('[data-plan-text]').forEach((el) => { el.textContent = p.text; el.classList.toggle('is-bad', !!p.error); });
    $$('[data-plan-segs]').forEach((el) => {
      el.innerHTML = p.segments.map((s, i) => '<i style="flex:' + s + '">' + (i + 1) + ' · ' + s + 's</i>').join('');
    });
    $$('[data-facts-count]').forEach((el) => { el.textContent = state.form.facts.length; });
    $$('[data-src-count]').forEach((el) => { el.textContent = state.form.source.length; });
    $$('[data-sta-msg]').forEach((el) => {
      el.textContent = stale() ? '输入已修改，下面保留的是上次结果' : (state.result ? '输入未改动 · 结果是最新一次生成' : '尚未生成');
      el.classList.toggle('is-warn', stale());
    });
    $$('[data-refs]').forEach((el) => { el.hidden = state.form.mode === 'text'; });
    /* 生成按钮：阻断项没清零就置灰（检查单里会说明原因，「仍然生成」可跳过） */
    const ck = checklist();
    $$('[data-gen-btn]').forEach((b) => { b.disabled = !!state.busy || !!p.error || (ck.blockers > 0 && !state.forceGen); });
    /* 检查条要跟着产品名/对标/模式一起变，但不重绘整页（会掉焦点） */
    patchCheckArea();
    $$('[data-gen-gate]').forEach((el) => { el.textContent = '先修 ' + ck.blockers + ' 项'; });
    $$('[data-gen-gate], [data-gen-force]').forEach((el) => {
      const wrap = el.closest('.u-mode-gate') || el;
      wrap.hidden = !ck.blockers;
    });
    autosize();
    saveDraft();
  }

  function bindFields() {
    $$('[data-field]').forEach((el) => {
      if (el.dataset.bound) return;
      el.dataset.bound = '1';
      const key = el.dataset.field;
      el.addEventListener('input', () => {
        state.form[key] = el.value;
        if (key === 'facts') {
          /* 原文框：即时重解析成事实条，但先不重绘列表（会把光标顶掉） */
          state.factRows = window.FACTS ? window.FACTS.parse(el.value) : [];
          refreshFactsUI();
          saveDraft();
          return;
        }
        syncDerived();
      });
      el.addEventListener('change', () => {
        state.form[key] = el.value;
        if (key === 'mode') { render(); return; }
        if (key === 'facts') { render(); return; }
        syncDerived();
      });
    });
  }

  async function filterModels() {
    const sel = $('[data-field="model"]');
    if (!sel || sel.dataset.full) return;
    try {
      const d = await getJSON('/api/ai/models');
      const list = (d.models || []).slice(0, 30);
      if (!list.length) return;
      sel.dataset.full = '1';
      sel.innerHTML = '<option value="">工作台默认</option>' + list.map((m) =>
        '<option value="' + esc(m.id) + '"' + (m.id === state.form.model ? ' selected' : '') + '>' + esc(m.name || m.id) + '</option>').join('');
    } catch (e) { /* 没有模型列表就用默认 */ }
  }

  /* ── Toast / 抽屉 / 复制 ─────────────────────────────── */
  let toastEl, toastTimer;
  function toast(msg, bad) {
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'toast'; ROOT.appendChild(toastEl); }
    toastEl.textContent = msg;
    toastEl.classList.toggle('bad', !!bad);
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('on'), 2400);
  }

  const scrim = document.createElement('div'); scrim.className = 'scrim';
  const drawer = document.createElement('aside'); drawer.className = 'drawer';
  drawer.setAttribute('role', 'dialog'); drawer.setAttribute('aria-modal', 'true');
  ROOT.append(scrim, drawer);
  const closeDrawer = () => { drawer.classList.remove('on'); scrim.classList.remove('on'); };
  scrim.addEventListener('click', closeDrawer);

  function openDrawer(kind, key, html, title, sub) {
    if (html) {
      drawer.innerHTML = '<header class="drawer-h"><div class="grow"><div class="eb">DETAIL</div><h2>' + esc(title) + '</h2>' +
        '<span class="tiny muted">' + esc(sub || '') + '</span></div><button class="btn ghost sm" data-close>关闭</button></header>' +
        '<div class="drawer-b">' + html + '</div>';
    } else {
      const d = window.PO.detail(kind, key);
      if (!d) return;
      drawer.innerHTML = '<header class="drawer-h"><div class="grow"><div class="eb">DETAIL</div><h2>' + esc(d.t) + '</h2>' +
        '<span class="tiny muted">' + esc(d.s) + '</span></div><button class="btn ghost sm" data-close>关闭</button></header>' +
        '<div class="drawer-b">' + d.b + '</div>';
    }
    drawer.classList.add('on'); scrim.classList.add('on');
    drawer.querySelector('[data-close]').onclick = closeDrawer;
  }

  function copyText(str) {
    const done = () => toast('已复制 ' + str.length + ' 字');
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = str; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败，请手动选择', true); }
      ta.remove();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(str).then(done, fallback);
    else fallback();
  }

  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type: type || 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }

  /* ── 动作 ────────────────────────────────────────────── */
  async function runAnalyze() {
    if (state.busy) return;
    try { check(false); } catch (e) { toast(e.message, true); return; }
    state.busy = 'analyze'; state.error = '';
    render();
    toast('正在分析对标提示词…');
    try {
      const body = requestBody(); delete body.duration; delete body.clip_limit; delete body.strategy; delete body.ratio;
      const d = await post('/api/prompt-optimizer/analyze', body, 120000);
      state.analysis = d.analysis;
      if (d.model) state.modelName = d.model;
      const hinted = (d.analysis.language || '').trim();
      if (hinted && !state.form.language) { state.form.language = hinted; toast('已按分析结果填入口播语言：' + hinted); }
      saveDraft();
    } catch (e) { state.error = e.message; toast(e.message, true); }
    state.busy = ''; render();
  }

  async function runGenerate(attempt) {
    attempt = attempt || 0;
    if (state.busy) return;
    /* 生成前检查：阻断项没清零就别浪费一次模型调用（「仍然生成」可跳过） */
    if (!state.forceGen) {
      const ck = checklist();
      if (ck.blockers) {
        const names = ck.items.filter((i) => i.st === 'bad').map((i) => i.k).join('、');
        toast('还有 ' + ck.blockers + ' 项要修：' + names + '（在「② 换成我的产品」下面）', true);
        const box = $('[data-check]');
        if (box) {
          try { box.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { box.scrollIntoView(); }
          box.classList.add('is-bad-flash');
          setTimeout(() => box.classList.remove('is-bad-flash'), 2000);
        }
        return;
      }
    }
    try { check(true); } catch (e) { toast(e.message, true); return; }
    state.busy = 'generate'; state.error = '';
    genStart();
    render();
    const t0 = Date.now();
    toast(attempt ? '第一遍没通过结构检查，自动再试一次…' : '已提交，正在生成六版（约 1–2 分钟）…');
    try {
      const d = await post('/api/prompt-optimizer/generate', requestBody(), 280000);
      state.result = { ...d, fingerprint: fingerprint(), at: new Date().toLocaleString('zh-CN', { hour12: false }) };
      state.lastRun = { at: new Date().toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5), costSec: Math.round((Date.now() - t0) / 1000) };
      if (d.model) state.modelName = d.model;
      pushHistory();
      saveDraft();
      toast('六版已生成 · 用时 ' + state.lastRun.costSec + ' 秒');
    } catch (e) {
      state.error = e.message;
      genStop(false);
      state.busy = ''; render();
      /* 模型偶发没通过结构/差异检查：自动再跑一次，仍失败才交给用户 */
      if (attempt < 1 && /结构|差异|未完成|解析/.test(e.message)) { return void runGenerate(attempt + 1); }
      toast(e.message, true);
      return;
    }
    genStop(true);
    /* 让进度条先走满 100% 再切回按钮，否则「完成」这一帧看不见 */
    setTimeout(() => { state.busy = ''; render(); }, 700);
  }

  /* ── 历史库（H1 输入快照 + H3 分组/搜索/多选，2026-10-30） ── */
  const HIST_LIMIT = 20;

  function snapshotForm() {
    const f = state.form;
    return {
      product: f.product, facts: f.facts, refs: f.refs, language: f.language,
      duration: f.duration, clipLimit: f.clipLimit, ratio: f.ratio,
      mode: f.mode, model: f.model, strategy: f.strategy,
      benchmarkId: (state.benchmark && state.benchmark.id) || '',
      benchmarkName: (state.benchmark && state.benchmark.name) || ''
    };
  }

  /* 这条历史的输入跟现在填的是不是同一份（行上标 =/≠ 当前输入） */
  function sameInput(h) {
    const f = h && h.form;
    if (!f) return null;
    const cur = state.form;
    return String(f.product || '') === String(cur.product || '') &&
      String(f.facts || '') === String(cur.facts || '') &&
      String(f.duration || '') === String(cur.duration || '') &&
      String(f.language || '') === String(cur.language || '');
  }

  function pushHistory() {
    const r = state.result;
    if (!r) return;
    const v0 = (r.versions || [])[0] || {};
    const now = new Date();
    const pad = (n) => (n < 10 ? '0' : '') + n;
    const entry = {
      id: 'h' + now.getTime().toString(36) + Math.random().toString(36).slice(2, 6),
      ts: now.getTime(),
      at: pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes()),
      title: stripTitle(v0.title) || '未命名',
      product: state.form.product, language: state.form.language,
      duration: Number(state.form.duration), strategy: state.form.strategy,
      mode: state.form.mode, factsCount: (state.factRows || []).length,
      versions: (r.versions || []).length,
      status: (r.self_check && r.self_check.status) || 'manual',
      texts: (r.versions || []).map((v) => stripTitle(v.title)).slice(0, 6),
      form: snapshotForm(),
      analysis: state.analysis,
      result: { ...r, fingerprint: r.fingerprint }
    };
    state.history = [entry, ...state.history].slice(0, HIST_LIMIT);
    state.histSel = [];
    saveHistory();
  }

  /* 写本地；配额不够就从最旧的开始丢，并明确告知，不静默丢数据 */
  function saveHistory() {
    for (let n = state.history.length; n > 0; n--) {
      try {
        localStorage.setItem(LS_HIST, JSON.stringify(state.history.slice(0, n)));
        if (n < state.history.length) toast('本地存储快满了，只留下最近 ' + n + ' 条', true);
        return true;
      } catch (e) {
        if (n === 1) { toast('本地存储已满，这条历史没能保存', true); return false; }
      }
    }
    return true;
  }

  /* withInput=true 时连输入一起回填（H1 的核心：避免拿当前输入去重生成） */
  function restoreHistory(i, withInput) {
    const h = state.history[i];
    if (!h || !h.result) { toast('这条历史没有完整结果', true); return; }
    if (!Array.isArray(h.result.versions) || !h.result.versions.length) {
      toast('这条历史的六版结果是残缺的（旧版本存的），只保留输入', true);
      if (!withInput) return;
    }
    if (withInput && h.form) {
      const { benchmarkId, benchmarkName, ...formPart } = h.form;   // eslint-disable-line no-unused-vars
      state.form = { ...state.form, ...formPart };
      state.factRows = window.FACTS ? window.FACTS.parse(state.form.facts) : [];
      if (benchmarkId) {
        const b = state.benchmarks.filter((x) => x.id === benchmarkId)[0];
        if (b) state.benchmark = b;
      }
      saveDraft();
    }
    state.result = h.result;
    state.analysis = h.analysis || null;
    state.lastRun = { at: h.at, costSec: 0 };
    render();
    toast(withInput ? '已载入 ' + h.at + ' 的输入与结果' : '已恢复 ' + h.at + ' 的六版结果');
  }

  function historyMarkdown(entries) {
    return entries.map((h) => {
      const vs = (h.result && h.result.versions) || [];
      return '## ' + h.at + ' · ' + h.title + '\n' + (h.form && h.form.product ? '产品：' + h.form.product + '\n' : '') +
        vs.map((v) => '### ' + v.title + '\n' + (v.clips || []).map((c, i) =>
          '第 ' + (i + 1) + ' 段（' + c.duration + ' 秒）\n' + c.prompt).join('\n')).join('\n\n');
    }).join('\n\n---\n\n');
  }

  function historyAction(a, el) {
    const i = el && el.dataset.his != null ? Number(el.dataset.his) : -1;
    const id = el && el.dataset.hisId;
    if (a === 'his-view') return restoreHistory(i, false);
    if (a === 'his-load') return restoreHistory(i, true);
    if (a === 'his-sel') {
      const has = state.histSel.indexOf(id) >= 0;
      state.histSel = has ? state.histSel.filter((x) => x !== id) : state.histSel.concat([id]);
      return render();
    }
    if (a === 'his-export-sel') {
      const picked = state.history.filter((h) => state.histSel.indexOf(h.id) >= 0);
      if (!picked.length) { toast('先勾选要导出的历史', true); return; }
      download('历史提示词_' + picked.length + '条.md', historyMarkdown(picked));
      toast('已导出 ' + picked.length + ' 条历史');
      return;
    }
    if (a === 'his-del-sel') {
      const picked = state.histSel.slice();
      if (!picked.length) { toast('先勾选要删除的历史', true); return; }
      state.history = state.history.filter((h) => picked.indexOf(h.id) < 0);
      state.histSel = [];
      saveHistory(); render();
      toast('已删除 ' + picked.length + ' 条历史');
      return;
    }
    if (a === 'his-clear') {
      if (!state.history.length) return;
      if (!window.confirm('清空全部历史（含六版提示词）？此操作不可恢复。')) return;
      state.history = []; state.histSel = []; state.histQuery = '';
      saveHistory(); render();
      toast('历史已清空');
    }
  }

  /* 搜索：就地隐藏行，不重绘（保住输入框焦点） */
  function filterHistory(q) {
    state.histQuery = q || '';
    const key = String(q || '').trim().toLowerCase();
    const box = $('[data-unit="history"]');
    if (!box) return;
    $$('[data-his-row]', box).forEach((row) => {
      row.hidden = !!key && String(row.dataset.s || '').indexOf(key) < 0;
    });
    $$('[data-his-day]', box).forEach((day) => {
      let any = false, n = day.nextElementSibling;
      while (n && n.hasAttribute('data-his-row')) {
        if (!n.hidden) { any = true; break; }
        n = n.nextElementSibling;
      }
      day.hidden = !any;
    });
  }

  function archiveSource() {
    try {
      const text = state.form.source.trim();
      if (!text) throw Error('先把对标提示词粘进来');
      if (text.length > 24000) throw Error('最多 24000 字');
      const name = (text.split('\n').find((l) => l.trim()) || '对标提示词').replace(/^#+\s*/, '').slice(0, 28);
      const id = 'a' + Math.abs(hash(text)).toString(36).slice(0, 8);
      if (state.benchmarks.some((b) => b.id === id)) { toast('这份已经归档过了'); return; }
      const item = { id, name, duration: 0, shots: 0, frames: 0, refs: 0, mode: '粘贴', model: '', at: new Date().toLocaleString('zh-CN', { hour12: false }), audio: '', source: text, unc: 0 };
      state.benchmarks = [item, ...state.benchmarks].slice(0, 20);
      try { localStorage.setItem(LS_ARCH, JSON.stringify(state.benchmarks)); } catch (e) { /* 忽略 */ }
      state.benchmark = item;
      render();
      toast('已归档：' + name);
    } catch (e) { toast(e.message, true); }
  }

  function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; } return h; }

  async function importReport() {
    try {
      if (typeof window.PromptReportBridge === 'undefined') throw Error('报告桥未加载，请刷新页面');
      const items = await window.PromptReportBridge.list();
      if (!items.length) { toast('还没有已完成的原片还原报告', true); return; }
      const rows = items.slice(0, 20).map((i) =>
        '<button class="btn block" style="justify-content:space-between" data-import="' + esc(i.video_id) + '">' +
        '<span>' + esc(i.title || i.video_id) + '</span><span class="tiny muted">' + esc(i.video_id) + '</span></button>').join('');
      openDrawer('report', '', '<p class="tiny muted">导入后只作为创意参考：不锁定竞品外观、不沿用原口播。</p>' + rows, '从反推报告导入', items.length + ' 份可用');
    } catch (e) { toast(e.message, true); }
  }

  async function useReport(id) {
    try {
      toast('正在读取报告…');
      const b = await window.PromptReportBridge.load(id);
      state.benchmark = {
        id: b.id,
        /* 标题用「@账号 · N 分镜」，报告号降级成副标识——旧版拿编号当标题，没人看得懂 */
        name: (b.account ? '@' + b.account : '报告 ' + (b.reportNo || b.id.slice(-6))) + ' · ' + b.shots + ' 分镜',
        account: b.account || '', nickname: b.nickname || '',
        desc: b.desc || '', thumb: b.thumb || '', reportNo: b.reportNo || b.id.slice(-6),
        duration: b.duration, shots: b.shots, frames: 0,
        refs: (b.references || []).length, mode: '原片还原', model: '', at: b.at || '',
        audio: b.voice && b.voice.state === 'spoken' ? '原声 · ' + b.voice.language : '原声（未分离）',
        source: b.source, unc: 0, reportUrl: b.reportUrl
      };
      state.form.source = b.source;
      // 2026-10-29：报告桥已经把「参考素材说明」算好了（b.refs），旧版三段式会一并
      // 填进去，v2 重写时漏了这一步：用户切到图生/多素材就必然撞上「需要说明」。
      if (b.refs) state.form.refs = b.refs;
      if (b.duration && b.duration >= 4 && b.duration <= 60) state.form.duration = String(Math.round(b.duration));
      if (b.language) state.form.language = b.language;
      state.benchmarks = [state.benchmark, ...state.benchmarks.filter((x) => x.id !== b.id)].slice(0, 20);
      try { localStorage.setItem(LS_ARCH, JSON.stringify(state.benchmarks)); } catch (e) { /* 忽略 */ }
      closeDrawer();
      render();
      toast('已导入 ' + b.id + '（' + b.duration + ' 秒）');
    } catch (e) { toast(e.message, true); }
  }

  /* ── 素材库抽屉（2026-10-30）：把"另外几条"从主界面收进抽屉，带搜索 ── */
  function openLibrary() {
    const list = state.benchmarks || [];
    if (!list.length) { toast('素材库还是空的，先从反推报告导入一份', true); return; }
    const rows = list.map((x) => {
      const on = !!(state.benchmark && state.benchmark.id === x.id);
      const thumb = x.thumb || (/^\d{6,}$/.test(String(x.id)) ? '/tk_reverse/' + x.id + '/frames/f_001.jpg' : '');
      const meta = [
        x.account ? '@' + x.account : '', x.duration ? x.duration + ' 秒' : '',
        x.shots ? x.shots + ' 分镜' : '', x.refs ? x.refs + ' 参考图' : '', x.mode || ''
      ].filter(Boolean).join(' · ');
      const kw = ((x.name || '') + ' ' + (x.desc || '') + ' ' + meta + ' ' + x.id).toLowerCase();
      return '<button class="u-lib-row' + (on ? ' on' : '') + '" data-bench="' + esc(x.id) + '" data-kw="' + esc(kw) + '">' +
        '<span class="u-lib-cv">' + (thumb ? '<img src="' + esc(thumb) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>' +
        '<span class="u-lib-bd"><b>' + esc(x.account ? '@' + x.account : x.name) + '</b>' +
          '<span class="tiny muted">' + esc(meta || '粘贴归档') + '</span></span>' +
        '<span class="u-lib-go">' + (on ? '当前' : '用这条') + '</span></button>';
    }).join('');
    openDrawer('library', '', '<div class="u-lib">' +
      '<input class="inp u-lib-q" placeholder="搜账号 / 编号 / 关键词" aria-label="搜索素材">' +
      '<div class="u-lib-list">' + rows + '</div>' +
      '<p class="tiny muted">素材只作创意参考：不锁定竞品外观、不沿用原口播。</p></div>',
      '素材库', list.length + ' 份 · 点一条即可切换');
    const q = drawer.querySelector('.u-lib-q');
    if (q) q.oninput = () => {
      const k = q.value.trim().toLowerCase();
      drawer.querySelectorAll('.u-lib-row').forEach((r) => {
        r.hidden = !!k && !String(r.dataset.kw || '').includes(k);
      });
    };
  }

  /* 粘贴正文默认折叠：老用户要粘贴时再展开，并顺手把高度撑开 */
  function togglePaste() {
    const box = stage && stage.querySelector('.u-src-paste');
    if (!box) return;
    box.hidden = !box.hidden;
    if (!box.hidden) {
      autosize();
      const ta = box.querySelector('textarea');
      if (ta) ta.focus();
    }
  }

  /* 旧档案补全：2026-10-30 之前归档的记录只有 name/source，没有账号与缩略图。
     开机拉一次 /api/reverse/list，把能补的字段补上，老记录也能显示成人话标题。 */
  let archiveEnriched = false;
  async function enrichArchive() {
    if (!window.PromptReportBridge || !(state.benchmarks || []).length) return;
    try {
      const items = await window.PromptReportBridge.list();
      const map = new Map(items.map((i) => [String(i.video_id), i]));
      let changed = false;
      state.benchmarks.forEach((b) => {
        const it = map.get(String(b.id));
        if (!it) return;
        const author = (it.meta && it.meta.author) || '';
        if (!b.account && author) { b.account = author; changed = true; }
        if (!b.desc && it.meta && it.meta.desc) {
          /* 简介常常全是 hashtag，剥掉后不足 8 字就不当摘要（留给界面兜底文案） */
          const clean = String(it.meta.desc).replace(/#[^\s#]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 88);
          if (clean.length >= 8) { b.desc = clean; changed = true; }
        }
        if (!b.thumb && it.frames) { b.thumb = '/tk_reverse/' + b.id + '/frames/f_001.jpg'; changed = true; }
        if (!b.reportNo) { b.reportNo = String(b.id).slice(-6); changed = true; }
      });
      if (changed) {
        try { localStorage.setItem(LS_ARCH, JSON.stringify(state.benchmarks)); } catch (e) { /* 配额满了就算了 */ }
        render();
      }
    } catch (e) { /* 列表拉不到就不补，界面照常 */ }
  }

  /* 产品图识别 */
  function pickImages() {
    const input = $('#pv2-file');
    if (!input) { toast('页面缺少文件输入框，请刷新', true); return; }
    input.value = '';
    input.click();
  }

  function bindFileInput() {
    const input = $('#pv2-file');
    if (!input || input.dataset.bound) return;
    input.dataset.bound = '1';
    input.addEventListener('change', () => {
      const files = [...input.files].slice(0, 3);
      input.value = '';
      if (files.length) scanImages(files);
    });
  }

  /* 上传产品图：识别结果直接记进「细节」，不用再点一次确认（2026-10-30 简化） */
  async function scanImages(files) {
    if (!files.length) return;
    try {
      const imgs = [];
      for (const f of files) {
        if (f.size > 6 * 1048576) throw Error('单张图不能超过 6MB');
        imgs.push(await shrink(f));
      }
      toast('正在识别产品图…');
      const d = await post('/api/ps/product-scan', { images: imgs.map((i) => ({ name: i.name, mime: i.mime, data: i.data })), hint: state.form.product }, 120000);
      if (!d.ok) throw Error(d.reason || '识别失败');
      const p = d.product || {};
      const F = window.FACTS;
      /* 模型常把「未知 / 无法确认 / N/A」当值返回：这类不记，免得细节里全是废话 */
      const CLEAN = /^(未知|无|不确定|无法确认|看不出|不清楚|n\/?a|none|null|[-—]{1,2})$/i;
      const use = (v) => {
        const s = String(v == null ? '' : v).trim();
        return (s && !CLEAN.test(s)) ? s : '';
      };
      if (use(p.category) && !state.form.product.trim()) state.form.product = use(p.category);
      const lines = [];
      if (use(p.material)) lines.push('可见材质：' + use(p.material));
      const colors = (p.colors || []).map(use).filter(Boolean);
      if (colors.length) lines.push('颜色：' + colors.join('、'));
      if (use(p.scene)) lines.push('可见场景：' + use(p.scene));
      if (use(p.audience)) lines.push('人群：' + use(p.audience));
      (p.selling || []).map(use).filter(Boolean).forEach((s) => lines.push('可见卖点：' + s));
      const fresh = F ? F.parse(lines.join('\n')) : [];
      fresh.forEach((r) => { r.src = '图'; });
      /* 合并而不是无限追加：同义/重复的行当场并掉，免得越扫越乱 */
      const before = (state.factRows || []).length;
      const next = F ? F.dedupe((state.factRows || []).concat(fresh)) : (state.factRows || []).concat(fresh);
      writeFacts(next);
      /* 缩略图常驻（最多 3 张），刷新后还看得见用的是哪张 */
      state.images = (state.images || []).concat(imgs.map((i) => ({ name: i.name, thumb: i.thumb }))).slice(-3);
      state.ui.factsOpen = true;
      saveDraft();
      render();
      const c = factAnalysis().counts;
      const added = Math.max(0, next.length - before);
      toast(c.total
        ? '已记下 ' + c.total + ' 条细节' + (added ? '（新增 ' + added + ' 条）' : '') + '，点每条可改'
        : '这张图没认出可用细节，可以手动「＋ 加一条」', !c.total);
    } catch (e) { toast(e.message, true); }
  }

  function shrink(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const im = new Image();
      im.onload = () => {
        const scale = Math.min(1, 1200 / Math.max(im.width, im.height));
        const c = document.createElement('canvas');
        c.width = Math.max(1, Math.round(im.width * scale));
        c.height = Math.max(1, Math.round(im.height * scale));
        c.getContext('2d').drawImage(im, 0, 0, c.width, c.height);
        /* 再画一张 132px 的小图：草稿只存这张，原图只在这次识别请求里用一次 */
        const ts = Math.min(1, 132 / Math.max(im.width, im.height));
        const tc = document.createElement('canvas');
        tc.width = Math.max(1, Math.round(im.width * ts));
        tc.height = Math.max(1, Math.round(im.height * ts));
        tc.getContext('2d').drawImage(im, 0, 0, tc.width, tc.height);
        URL.revokeObjectURL(url);
        resolve({
          name: file.name, mime: 'image/jpeg',
          data: c.toDataURL('image/jpeg', 0.85).split(',')[1],
          thumb: tc.toDataURL('image/jpeg', 0.72)
        });
      };
      im.onerror = () => { URL.revokeObjectURL(url); reject(Error('这张图片读不出来')); };
      im.src = url;
    });
  }

  /* ── 事件委托 ────────────────────────────────────────── */
  ROOT.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) { closeDrawer(); return; }

    const cp = e.target.closest('[data-copy]');
    if (cp) {
      const t = window.PO.texts[cp.dataset.copy];
      if (t && t.t) copyText(t.t); else toast('这项没有可复制的文本', true);
      return;
    }

    const drill = e.target.closest('[data-drill]');
    if (drill) { const [k, v] = String(drill.dataset.drill).split(':'); openDrawer(k, v); return; }

    const dr = e.target.closest('[data-drawer]');
    if (dr) { const [k, v] = String(dr.dataset.drawer).split(':'); openDrawer(k, v); return; }

    const imp = e.target.closest('[data-import]');
    if (imp) { useReport(imp.dataset.import); return; }

    const bench = e.target.closest('[data-bench]');
    if (bench) {
      const b = state.benchmarks.find((x) => x.id === bench.dataset.bench);
      if (b) {
        state.benchmark = b;
        if (b.source) state.form.source = b.source;
        if (b.duration) state.form.duration = String(Math.round(b.duration));
        saveDraft(); closeDrawer(); render();
        toast('已切换对标：' + (b.account ? '@' + b.account : b.name));
      }
      return;
    }

    /* 步骤条跳转（① → ② → ⑤ 都在同一页） */
    const goto = e.target.closest('[data-goto]');
    if (goto) {
      const unit = stage && stage.querySelector('[data-unit="' + goto.dataset.goto + '"]');
      if (unit) unit.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }

    const seg = e.target.closest('[data-seg]');
    if (seg) {
      const box = seg.closest('.seg');
      if (box) box.querySelectorAll('.sg').forEach((b) => b.classList.toggle('on', b === seg));
      const k = seg.dataset.seg;
      if (k === 'split' || k === 'compress') state.form.strategy = k;
      if (k === 'r916') state.form.ratio = '9:16';
      if (k === 'r169') state.form.ratio = '16:9';
      if (k === 'r11') state.form.ratio = '1:1';
      saveDraft(); syncDerived();
      return;
    }

    /* 老标记（只有 data-his、没有 data-act）仍然可用；新按钮走 data-act 分支 */
    const his = e.target.closest('[data-his]');
    if (his && !his.dataset.act) { restoreHistory(Number(his.dataset.his), false); return; }

    const act = e.target.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a.indexOf('his-') === 0) return void historyAction(a, act);
      /* 检查条上的「去填」：展开细节，没有行就顺手给一行空行并聚焦 */
      if (a === 'facts-open') {
        state.ui.factsOpen = true;
        if (!(state.factRows || []).length && window.FACTS) {
          writeFacts([{ id: window.FACTS.nid(), key: '卖点', value: '' }]);
        }
        render();
        const first = $('.u-fact-v');
        if (first) first.focus();
        return;
      }
      if (a.indexOf('facts-') === 0) return void factsAction(a, act.dataset.fid);
      if (a === 'toggle-facts') { state.ui.factsOpen = !state.ui.factsOpen; render(); return; }
      if (a === 'toggle-params') { state.ui.paramsOpen = !state.ui.paramsOpen; render(); return; }
      if (a === 'toggle-check') { state.ui.checkOpen = !state.ui.checkOpen; patchCheckArea(); return; }
      if (a === 'refs-fill') return void fillRefs();
      if (a === 'goto-source') {
        const u = $('[data-unit="source"]');
        if (u) { try { u.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (err) { u.scrollIntoView(); } }
        return;
      }
      if (a === 'generate-force') {
        state.forceGen = true;
        runGenerate().finally(() => { state.forceGen = false; });
        return;
      }
      if (a === 'analyze') return void runAnalyze();
      if (a === 'generate') return void runGenerate();
      if (a === 'archive') return void archiveSource();
      if (a === 'import-report') return void importReport();
      if (a === 'library') return void openLibrary();
      if (a === 'toggle-paste') return void togglePaste();
      if (a === 'scan') return void pickImages();
      if (a === 'img-del') {
        const k = Number(act.dataset.idx);
        state.images = (state.images || []).filter((_, i) => i !== k);
        saveDraft(); render();
        toast('已移除这张图');
        return;
      }
      if (a === 'img-view') {
        const im = (state.images || [])[Number(act.dataset.idx)];
        if (!im) return;
        openDrawer('photo', '', '<img class="u-ph-big" src="' + esc(im.thumb || '') + '" alt="">',
          im.name || '产品图', '这张图只用来识别细节，不会传给生成接口');
        return;
      }
      if (a === 'export' || a === 'export-all') {
        const t = window.PO.texts.all;
        if (!t || !t.t) { toast('还没有可导出的结果', true); return; }
        download('提示词优化_六个版本.md', t.t);
        toast('已导出 提示词优化_六个版本.md');
        return;
      }
      if (a === 'backup') {
        download('提示词优化_草稿备份.json', JSON.stringify({ form: state.form, benchmark: state.benchmark, analysis: state.analysis, result: state.result }, null, 1), 'application/json');
        toast('草稿已备份');
        return;
      }
      if (a === 'refresh') { render(); toast('已刷新'); return; }
      if (a === 'clear-error') { state.error = ''; render(); return; }
      if (a === 'full') { ROOT.classList.toggle('pv2-wide'); return; }
      return;
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runGenerate(); }
  });

  /* 光斑跟随：把 --mx/--my 写到鼠标下的面板/卡片（rAF 节流；触屏与「减少动效」时关掉） */
  const canHover = window.matchMedia('(hover: hover)').matches &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (canHover) {
    let raf = 0, pending = null;
    ROOT.addEventListener('pointermove', (e) => {
      const el = e.target.closest('.mod, .u-ver, .u-src, .u-prod, .u-mode, .u-ana, .u-chk, .u-his, .u-exp');
      if (!el) return;
      pending = { el, x: e.clientX, y: e.clientY };
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!pending) return;
        const { el: node, x, y } = pending;
        const r = node.getBoundingClientRect();
        node.style.setProperty('--mx', ((x - r.left) / r.width * 100).toFixed(1) + '%');
        node.style.setProperty('--my', ((y - r.top) / r.height * 100).toFixed(1) + '%');
      });
    }, { passive: true });
  }

  /* ── 启动：主壳里按需挂载，独立页打开即挂载 ─────────── */
  let mounted = false;
  function mount() {
    if (!stage || window.__PROMPT_OLD__) return;
    if (!mounted) {
      mounted = true;
      loadDraft();
      bindFileInput();
      /* 事实条逐字编辑：只更新内存与检查单，不重绘列表 */
      ROOT.addEventListener('input', (e) => {
        const q = e.target.closest ? e.target.closest('[data-hist-q]') : null;
        if (q) { filterHistory(q.value); return; }
        const v = e.target.closest ? e.target.closest('.u-fact-v') : null;
        if (!v) return;
        const row = (state.factRows || []).filter((r) => r.id === v.dataset.fid)[0];
        if (!row) return;
        row.value = v.value;
        if (window.FACTS) state.form.facts = window.FACTS.serialize(state.factRows);
        saveDraft();
        refreshFactsUI();
        syncFactBadge(v, row.id);
      });
      /* 只有首次挂载才做入场动画，之后重绘不再整页闪 */
      ROOT.classList.add('pv2-enter');
      setTimeout(() => ROOT.classList.remove('pv2-enter'), 700);
    }
    render();
    if (!archiveEnriched) { archiveEnriched = true; enrichArchive(); }
  }

  window.PV2 = { state, render, mount, runAnalyze, runGenerate, plan, planOf: plan };

  if (!window.__PROMPT_OLD__ && !window.__PV2_DEFER__) mount();
})();
