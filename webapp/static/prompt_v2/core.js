/* ============================================================
   core.js · 原子件（window.PO）+ 九个功能单元（window.U）
   九个单元 = 九个 U 调用，各自带 data-unit，验收按它断言：
     source · product · mode · analysis · versions · selfcheck · history · export · status
   文案、数字全部来自 window.PODATA（真实生成结果），架构只决定「怎么摆」，不改内容
   ============================================================ */
window.PO = (function () {
  /* 线上版：数据是活的 —— 每次读属性都取 window.PODATA 的当前值，
     这样「状态 → PODATA → 重渲染」不需要重建模块闭包 */
  const D = new Proxy({}, { get: (_, k) => (window.PODATA || {})[k] });

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf = (n, d) => Number(n || 0).toLocaleString('zh-CN', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  const cut = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : (s || ''));
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

  const statusLabel = { pass: '通过', needs_revision: '需修改', manual: '需人工确认' };
  const statusCls = { pass: 'ok', needs_revision: 'bad', manual: 'warn' };

  const exportAll = () => {
    const f = D.form || {}, m = D.meta || {}, r = D.run || {}, p = D.plan || {};
    const vs = D.versions || [];
    if (!vs.length) return '';
    const head = '# 提示词优化 · ' + f.product + '\n' +
      '平台：' + (m.platform || '') + '\n生成：' + (r.at || '') + ' · ' + (m.model || '') + '\n' +
      '输入：' + (p.text || '') + ' ' + (f.ratio || '') + ' · 口播 ' + (f.language || '') + '\n';
    return head + vs.map((v) =>
      '\n## ' + v.label + ' · ' + v.title + '\n差异：' + v.difference +
      '\n\n' + v.clips.map((c, i) => '### 第 ' + (i + 1) + ' 段（' + c.duration + ' 秒）\n\n' + c.prompt).join('\n\n')
    ).join('\n') + '\n\n---\n' + (D.notes || []).map((n) => '- ' + n).join('\n');
  };

  /* 六个版本的可复制文本（抽屉与复制按钮共用）；结果变了要重建 */
  const texts = {};
  function rebuildTexts() {
    Object.keys(texts).forEach((k) => { delete texts[k]; });
    (D.versions || []).forEach((v) => {
      (v.clips || []).forEach((c, i) => { texts[v.id + 'c' + i] = { t: c.prompt, name: v.title + ' · 第 ' + (i + 1) + ' 段' }; });
    });
    texts.all = { t: exportAll(), name: '六版提示词全文' };
    return texts;
  }

  const btnCopy = (key, label, cls) =>
    '<button class="btn ' + (cls || 'sm') + '" data-copy="' + key + '">' + (label || '复制') + '</button>';

  const chip = (text, cls, tip) =>
    '<span class="chip ' + (cls || '') + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + text + '</span>';

  const statusChip = (s) => chip(statusLabel[s] || s, statusCls[s] || '');

  const sevDot = (s) => '<i class="dot ' + (statusCls[s] || '') + '" style="background:currentColor"></i>';

  const axis = (k, v) => '<div class="u-axis"><span>' + k + '</span><b>' + esc(v) + '</b></div>';

  const warnIcon = () => '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M8 1.6 15 14H1z" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 6v3.4M8 11.4v.6" stroke="currentColor" stroke-width="1.3"/></svg>';

  /* ── 抽屉内容 ─────────────────────────────────────────── */
  function versionDetail(v) {
    const chk = D.selfCheck.versions.find((x) => x.label === v.label) || {};
    return '<div class="dr-kv"><span>类型</span><b>' + v.label + '</b>' +
      '<span>主题</span><b>' + esc(v.theme) + '</b>' +
      '<span>人物</span><b>' + esc(v.character) + '</b>' +
      '<span>场景</span><b>' + esc(v.scene) + '</b>' +
      '<span>运镜</span><b>' + esc(v.camera) + '</b>' +
      '<span>音频</span><b>' + esc(v.sound || '—') + '</b>' +
      '<span>自检</span><b>' + (statusLabel[chk.status] || '—') + '</b></div>' +
      '<div><h3>与其他版本的具体差异</h3><p>' + esc(v.difference) + '</p></div>' +
      '<div><h3>四轴</h3><div class="u-axes">' + axis('开头', v.hook) + axis('场景', v.scene) + axis('叙事', v.story) + axis('风格', v.style) + '</div></div>' +
      (v.dialogue.length ? '<div><h3>口播（' + D.form.language + '，' + v.dialogue.length + ' 句 / ' + v.words + ' 词）</h3>' +
        v.dialogue.map((d) => '<p class="qt">' + esc(d) + '</p>').join('') + '</div>' : '') +
      v.clips.map((c, i) => '<div><h3>第 ' + (i + 1) + ' 段 · ' + c.duration + ' 秒 · ' + c.shots.length + ' 镜' +
        btnCopy(v.id + 'c' + i, '复制', 'ghost sm') + '</h3>' +
        '<div class="dr-kv" style="margin:6px 0 8px">' + c.shots.map((s) =>
          '<span>' + s.start + '–' + s.end + ' 秒</span><b>' + esc(cut(s.action, 46)) + '</b>').join('') + '</div>' +
        '<pre class="pre sm">' + esc(c.prompt) + '</pre></div>').join('') +
      (v.warnings.length ? '<div><h3>生成模型提示</h3><ul class="bul bad">' + v.warnings.map((w) => '<li>' + esc(w) + '</li>').join('') + '</ul></div>' : '');
  }

  function benchDetail(b) {
    const dash = (v, unit) => (v === null || v === undefined || v === '' || v === 0 ? '—' : v + (unit || ''));
    return '<div class="dr-kv"><span>报告 ID</span><b class="mono">' + esc(b.id || '—') + '</b>' +
      '<span>原片时长</span><b>' + dash(b.duration, ' 秒') + '</b>' +
      '<span>分镜</span><b>' + dash(b.shots, ' 段') + '</b>' +
      '<span>抽帧</span><b>' + dash(b.frames, ' 帧') + '</b>' +
      '<span>参考图</span><b>' + dash(b.refs, ' 张') + '</b>' +
      '<span>模式</span><b>' + esc(b.mode || '—') + '</b>' +
      '<span>模型</span><b>' + esc(b.model || '—') + '</b>' +
      '<span>生成于</span><b>' + esc(b.at || '—') + '</b>' +
      '<span>音轨</span><b>' + esc(b.audio || '—') + '</b></div>' +
      '<div><h3>反推出的提示词（' + (b.source || '').length + ' 字）</h3><pre class="pre sm">' + esc(b.source || '') + '</pre></div>' +
      (b.unc ? '<div><h3>报告里标注的不确定项（' + b.unc + ' 条）</h3><p class="muted">不确定项已进入「分析」单元的问题清单，这里不重复展开。</p></div>' : '');
  }

  function analysisDetail() {
    const a = D.analysis;
    const li = (x) => '<li>' + esc(x) + '</li>';
    return '<div><h3>原片分析</h3><p>' + esc(a.summary) + '</p></div>' +
      '<div class="dr-kv"><span>原片时长</span><b>' + (a.duration == null ? '未标注' : a.duration + ' 秒') + '</b>' +
      '<span>原片比例</span><b>' + (a.ratio || '未标注') + '</b>' +
      '<span>原口播语言</span><b>' + (a.language || '原声无台词，未识别') + '</b>' +
      '<span>分析模型</span><b>' + esc(a.model || '') + '</b></div>' +
      '<div><h3>可保留（' + a.strengths.length + '）</h3><ul class="bul ok">' + a.strengths.map(li).join('') + '</ul></div>' +
      '<div><h3>问题（' + a.issues.length + '）</h3><ul class="bul bad">' + a.issues.map(li).join('') + '</ul></div>' +
      '<div><h3>改进（' + a.changes.length + '）</h3><ul class="bul">' + a.changes.map(li).join('') + '</ul></div>' +
      '<div><h3>需要你补充（' + a.missing.length + '）</h3><ul class="bul">' + a.missing.map(li).join('') + '</ul></div>';
  }

  function selfcheckDetail() {
    const s = D.selfCheck;
    return '<div class="dr-kv"><span>总判定</span><b>' + (statusLabel[s.status] || s.status) + '</b>' +
      '<span>检查时间</span><b>' + esc(s.checkedAt.replace('T', ' ')) + '</b>' +
      '<span>方式</span><b>本地规则，不新增模型调用</b>' +
      '<span>规则门</span><b>' + statusLabel[s.ruleGate.status] + '</b></div>' +
      '<div><h3>规则门</h3><p>' + esc(s.ruleGate.reasons.join('；')) + '</p></div>' +
      s.versions.map((v) => '<div><h3>' + v.label + ' · ' + esc(v.title) + ' ' + statusChip(v.status) + '</h3>' +
        v.items.map((it) => '<p class="tiny" style="margin:2px 0"><b>' + esc(it.title) + '</b> ' + statusChip(it.status) +
          ' <span class="muted">' + esc(cut(it.reasons.join('；'), 90)) + '</span></p>').join('') + '</div>').join('') +
      '<div><h3>口径说明</h3><ul class="bul">' + s.limitations.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul></div>';
  }

  function detail(kind, key) {
    if (kind === 'ver') {
      const v = D.versions.find((x) => x.id === key);
      if (v) {
        const bare = String(v.title).replace(/^(优化版|B[1-3]|C[1-2])\s*[：:]\s*/, '');
        return { t: v.label + ' · ' + bare, s: v.kind + ' · ' + v.clips.length + ' 段 · ' + v.chars + ' 字', b: versionDetail(v) };
      }
    }
    if (kind === 'bench') {
      const b = D.benchmarks.find((x) => x.id === key);
      if (b) return { t: b.name, s: b.id + ' · ' + b.duration + ' 秒 · ' + b.shots + ' 分镜', b: benchDetail(b) };
    }
    if (kind === 'analysis') return { t: '原片分析与替换影响', s: '分析模型 ' + (D.analysis.model || ''), b: analysisDetail() };
    if (kind === 'selfcheck') return { t: '六版自检报告', s: D.selfCheck.method + ' · ' + statusLabel[D.selfCheck.status], b: selfcheckDetail() };
    if (kind === 'plan') {
      return { t: '时长计划', s: D.plan.text, b: '<div class="dr-kv"><span>目标总时长</span><b>' + D.plan.total + ' 秒</b>' +
        '<span>入口单段上限</span><b>' + D.plan.clipLimit + ' 秒</b>' +
        '<span>策略</span><b>' + (D.plan.strategy === 'split' ? '拆段生成' : '压缩成一条') + '</b>' +
        '<span>段数</span><b>' + D.plan.count + ' 段</b>' +
        '<span>每段</span><b>' + D.plan.segments.join(' / ') + ' 秒</b></div>' +
        '<p class="muted tiny">拆段时每段独立构图，人物、产品与光线保持连贯；不加速念台词。</p>' };
    }
    if (kind === 'scan') {
      return { t: '用产品图辅助填写', s: '当前 0 张', b: '<p>' + esc(D.scan.hint) + '</p>' +
        '<p class="muted tiny">识别只会补全可见字段（品类 / 材质 / 颜色 / 场景 / 卖点），材质与功效仍以你确认的事实为准；不会写入未经确认的触感与性能。</p>' };
    }
    if (kind === 'history') {
      return { t: '历史结果', s: D.history.length + ' 次', b: D.history.map((h) =>
        '<div><h3>' + esc(h.title) + ' ' + statusChip(h.status) + '</h3><p class="tiny muted">' + h.at + ' · ' + esc(h.product) +
        ' · ' + h.duration + ' 秒 · ' + esc(h.language) + ' · ' + h.versions + ' 版</p><p class="tiny">' +
        h.texts.map(esc).join(' / ') + '</p></div>').join('') };
    }
    return { t: '详情', s: '', b: '<p class="muted">未登记的明细。</p>' };
  }

  return { D, esc, nf, cut, pct, chip, statusChip, statusLabel, statusCls, btnCopy, texts, rebuildTexts, detail, sevDot, axis, warnIcon };
})();

window.U = (function () {
  const { D, esc, nf, cut, pct, chip, statusChip, statusLabel, btnCopy, texts, warnIcon } = window.PO;
  const A = new Proxy({}, { get: (_, k) => ((window.PODATA || {}).analysis || {})[k] });
  const S = new Proxy({}, { get: (_, k) => ((window.PODATA || {}).selfCheck || {})[k] });

  /* 页头：架构可自换结构，但标题文案统一 */
  function head(o) {
    o = o || {};
    return '<header class="hd' + (o.cls ? ' ' + o.cls : '') + '">' +
      '<div class="grow">' +
        (o.eyebrow !== false ? '<div class="eb">' + esc(o.eyebrow || '提示词优化 · 本地工作台') + '</div>' : '') +
        '<h1>提示词优化</h1>' +
        '<div class="sub">分析对标提示词，换成你的产品，生成优化版与五份不同创意</div>' +
      '</div>' +
      '<div class="hd-actions">' +
        '<span class="tiny muted">更新于 ' + D.meta.asOf + '</span>' +
        '<button class="btn" data-act="refresh">刷新</button>' +
        '<button class="btn" data-drawer="scan">产品图</button>' +
      '</div>' +
    '</header>';
  }

  /* ① 对标提示词 */
  function source(o) {
    o = o || {};
    const b = D.benchmark || { id: '', name: '未选择对标', duration: 0, shots: 0, frames: 0, refs: 0, audio: '', mode: '', source: '' };
    const v = o.variant || 'card';
    const list = (D.benchmarks || []).map((x) =>
      '<button class="u-bench' + (x.id === b.id ? ' on' : '') + '" data-bench="' + esc(x.id) + '">' +
        '<b class="ell">' + esc(x.name) + '</b>' +
        '<span class="tiny muted">' + x.duration + ' 秒 · ' + x.shots + ' 分镜 · ' + x.refs + ' 参考图</span>' +
      '</button>').join('');
    if (o.editable) {
      /* ★ 2026-10-30 改版（对标区）：三步条 + 一张主卡 + 素材库抽屉。
         旧版把「报告 941280 · 5 分镜」这样的内部编号当标题、三条平铺、没有图，
         第一次用的人不知道点哪条、也不知道「分镜」是什么。现在：
           · 默认只显示当前选中的那条（首帧 + 账号 + 一句人话摘要 + 三个数字）
           · 其余收进「素材库」抽屉（带搜索）
           · 粘贴正文折叠到「粘贴提示词」，不再占据主视线 */
      const b = D.benchmark || {};
      const list = D.benchmarks || [];
      const form = D.form || {};
      const has = !!b.id;
      const versions = D.versions || [];
      const thumb = has ? (b.thumb || (/^\d{6,}$/.test(String(b.id)) ? '/tk_reverse/' + b.id + '/frames/f_001.jpg' : '')) : '';
      const who = has ? (b.account ? '@' + b.account : b.name) : '';
      const kv = has ? [
        b.duration ? b.duration + ' 秒' : '',
        b.shots ? b.shots + ' 分镜' : '',
        b.refs ? b.refs + ' 参考图' : '',
        b.mode || ''
      ].filter(Boolean) : [];
      const stepNow = versions.length ? 3 : (has ? 2 : 1);
      const stepBar = '<div class="u-steps">' + [['1', '选对标', 'source'], ['2', '换成我的产品', 'product'], ['3', '生成六版', 'versions']].map((s, i) => {
        const n = i + 1;
        const cls = n === stepNow ? ' on' : (n < stepNow ? ' done' : '');
        return '<button type="button" class="u-st' + cls + '" data-goto="' + s[2] + '"><i>' + s[0] + '</i>' + esc(s[1]) + '</button>' +
          (i < 2 ? '<span class="u-st-ln"></span>' : '');
      }).join('') + '</div>';
      const hero = has
        ? '<div class="u-hero">' +
            '<div class="u-hero-cv">' + (thumb ? '<img src="' + esc(thumb) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</div>' +
            '<div class="u-hero-bd">' +
              '<div class="u-hero-t">' + esc(who) + (b.reportNo ? '<span class="u-hero-no">报告 ' + esc(b.reportNo) + '</span>' : '') + '</div>' +
              '<p class="u-hero-d">' + esc(b.desc || ((b.duration ? b.duration + ' 秒' : '这条') + '片子拆成 ' + (b.shots || '?') + ' 个镜头；点「看报告」看逐段拆解。')) + '</p>' +
              '<div class="u-hero-kv">' + kv.map((k) => '<span>' + esc(k) + '</span>').join('') + '</div>' +
              '<div class="u-hero-act">' +
                '<button class="btn sm pri" data-drawer="bench:' + esc(b.id) + '">看报告</button>' +
                '<button class="btn sm" data-act="library">换一条</button>' +
              '</div>' +
            '</div>' +
          '</div>'
        : '<div class="u-hero u-hero-empty">' +
            '<b>还没有对标素材</b>' +
            '<p>去「提示词反推」贴一条 TikTok 链接，几分钟出一份报告；也可以从已有报告导入。</p>' +
            '<div class="u-hero-act">' +
              '<button class="btn sm pri" data-act="import-report">从反推报告导入</button>' +
              '<a class="btn sm ghost" href="#reverse">去提示词反推 →</a>' +
            '</div>' +
          '</div>';
      return '<section class="u-src u-src-card u-src-hero" data-unit="source">' +
        stepBar +
        '<div class="mod-h"><h2>① 选一条对标</h2>' +
          '<span class="hint">别人跑通的片子，换成你的产品</span>' +
          '<span class="grow"></span>' +
          (list.length ? '<button class="btn sm ghost" data-act="library">素材库 · ' + list.length + '</button>' : '') +
        '</div>' +
        '<div class="mod-b u-src-live">' + hero +
          '<div class="u-src-tools">' +
            (has ? '<button class="btn sm" data-act="import-report">从反推报告导入</button>' : '') +
            '<button class="btn sm ghost" data-act="toggle-paste">' + (form.source ? '编辑提示词正文' : '粘贴提示词') + '</button>' +
            '<span class="grow"></span>' +
            '<button class="btn sm ghost" data-act="export-all">导出全部 .md</button>' +
          '</div>' +
          '<div class="u-src-paste" hidden>' +
            '<label class="lab" for="f-source">提示词正文（<span data-src-count>' + (form.source || '').length + '</span> / 24000 字）</label>' +
            '<textarea id="f-source" class="ta u-src-ta" data-field="source" rows="6" maxlength="24000" ' +
              'placeholder="粘贴提示词反推结果（markdown 也可以）">' + esc(form.source) + '</textarea>' +
            '<div class="u-src-btns"><button class="btn sm" data-act="archive">解析并归档</button></div>' +
          '</div>' +
          '<p class="u-src-tip">「分镜」= 把片子切成几段，每段一句话写清画面和动作。点「看报告」能逐段对照。</p>' +
        '</div>' +
      '</section>';
    }
    const metas = [
      ['原片时长', b.duration + ' 秒'], ['分镜', b.shots + ' 段'], ['抽帧', b.frames + ' 帧'],
      ['参考图', b.refs + ' 张'], ['音轨', b.audio], ['模式', b.mode]
    ];
    if (v === 'bar') {
      return '<section class="u-src u-src-bar" data-unit="source">' +
        '<div class="u-src-lead">' + chip('① 对标反推提示词', 'acc') +
          '<b class="ell">' + esc(b.name) + '</b>' +
          '<span class="tiny muted nowrap">' + b.duration + ' 秒 · ' + b.shots + ' 分镜 · ' + b.refs + ' 参考图</span></div>' +
        '<div class="u-src-act">' +
          '<button class="btn sm ghost" data-bench="' + b.id + '">' + D.benchmarks.length + ' 份归档</button>' +
          '<button class="btn sm ghost" data-drawer="bench:' + b.id + '">报告</button>' +
        '</div>' +
      '</section>';
    }
    return '<section class="u-src u-src-' + v + '" data-unit="source">' +
      '<div class="mod-h"><h2>① 对标反推提示词</h2>' +
        '<span class="hint">粘贴 .md / 上传 .zip / 从反推报告导入 · 单份 ≤ 512KB</span>' +
        '<span class="grow"></span>' + chip('已归档 ' + D.benchmarks.length + ' 份', 'solid') + '</div>' +
      '<div class="mod-b u-src-grid">' +
        '<div class="u-src-pick">' +
          '<div class="u-benchlist">' + list + '</div>' +
          '<div class="u-src-btns">' +
            '<button class="btn sm" data-act="parse">解析并归档</button>' +
            '<button class="btn sm" data-drawer="bench:' + b.id + '">看报告</button>' +
          '</div>' +
        '</div>' +
        '<div class="u-src-meta">' + metas.map((m) => '<div class="u-mini"><span>' + m[0] + '</span><b>' + esc(m[1]) + '</b></div>').join('') + '</div>' +
        '<div class="u-src-quote"><div class="lab">反推出的提示词 · ' + b.source.length + ' 字</div>' +
          '<p class="qt">' + esc(cut(b.source, o.short ? 110 : 210)) + '</p></div>' +
      '</div>' +
    '</section>';
  }

  /* ── ② 摘要行（2026-10-30 简化：产品图 / 细节 / 设置，各一行） ── */
  function photosRow() {
    const imgs = D.images || [];
    const thumbs = imgs.map((im, i) =>
      '<span class="u-ph">' +
        '<button class="u-ph-im" data-act="img-view" data-idx="' + i + '" title="看大图">' +
          '<img src="' + esc(im.thumb || '') + '" alt="">' +
        '</button>' +
        '<button class="u-ph-x" data-act="img-del" data-idx="' + i + '" title="移除这张图">×</button>' +
      '</span>').join('');
    return '<div class="u-sum u-sum-photo">' +
      '<span class="u-sum-k">产品图</span>' +
      (imgs.length
        ? '<span class="u-phs">' + thumbs + '</span>' +
          '<span class="u-sum-v" data-img-meta>' + imgs.length + ' 张 · 已识别</span>'
        : '<span class="u-sum-v muted">还没有 · 传一张，自动记下图里看得到的细节</span>') +
      '<span class="grow"></span>' +
      '<button class="u-sum-btn" data-act="scan">' + (imgs.length ? '再加' : '上传') + '</button>' +
    '</div>';
  }

  function paramsSummary(f) {
    const ui = D.ui || {};
    const modeName = { text: '文生', image: '图生', reference: '多素材' }[f.mode] || '文生';
    const txt = [f.language || '未选语言', f.duration + ' 秒', '单段 ' + f.clipLimit + ' 秒', f.ratio, modeName].join(' · ');
    return '<div class="u-sum' + (f.language ? '' : ' warn') + '">' +
      '<span class="u-sum-k">设置</span>' +
      '<span class="u-sum-v">' + esc(txt) + '</span>' +
      '<span class="grow"></span>' +
      '<button class="u-sum-btn" data-act="toggle-params">' + (ui.paramsOpen ? '收起' : '改') + '</button>' +
    '</div>';
  }

  function factsRow() {
    const c = D.factCounts || { total: 0, ok: 0, dup: 0, conflict: 0, weak: 0 };
    const ui = D.ui || {};
    const flags = [];
    if (c.conflict) flags.push(c.conflict + ' 冲突');
    if (c.dup) flags.push(c.dup + ' 重复');
    if (c.weak) flags.push(c.weak + ' 需核验');
    let txt = c.total ? c.total + ' 条' : '还没有';
    let tone = '';
    if (flags.length) { txt += ' · ' + flags.join(' · '); tone = c.conflict ? ' bad' : ' warn'; }
    else if (c.total) txt += ' · 都可确认';
    return '<div class="u-sum' + tone + '">' +
      '<span class="u-sum-k">细节</span>' +
      '<span class="u-sum-v" data-facts-meta>' + esc(txt) + '</span>' +
      '<span class="grow"></span>' +
      '<button class="u-sum-btn" data-act="toggle-facts">' + (ui.factsOpen ? '收起' : (c.total ? '改' : '填')) + '</button>' +
    '</div>';
  }

  /* 检查：默认一条状态条，展开才是 5 行明细 */
  function checkArea(ui) {
    const ck = D.check || { items: [], blockers: 0 };
    const items = ck.items || [];
    const bad = items.filter((i) => i.st === 'bad');
    const warn = items.filter((i) => i.st === 'warn');
    /* 2026-10-30 简化：没问题就整条收起，不占位（以前永远挂一条"可以生成"） */
    if (!bad.length && !warn.length) return '<div class="u-ckbox" data-checkbox hidden></div>';
    /* 优先显示「本面板能一键修」的那条：细节缺了就「去填」，有冲突就「整理」 */
    const fixable = (i) => i.act === 'facts-tidy' || i.act === 'facts-open';
    const first = bad.find(fixable) || bad.find((i) => i.act) || bad[0] ||
      warn.find(fixable) || warn.find((i) => i.act) || warn[0] || null;
    const tone = bad.length ? 'bad' : (warn.length ? 'warn' : 'ok');
    const head = bad.length ? bad.length + ' 项要修' : (warn.length ? warn.length + ' 项可优化' : '可以生成');
    const act = first && first.act
      ? '<button class="u-ckbar-act" data-act="' + esc(first.act) + '">' + esc(first.actText) + '</button>'
      : '';
    return '<div class="u-ckbox" data-checkbox>' +
      '<div class="u-ckbar is-' + tone + '">' +
        '<i class="u-ckbar-dot"></i>' +
        '<b class="u-ckbar-h">' + esc(head) + '</b>' +
        (first
          ? '<span class="u-ckbar-v">' + esc(first.k) + '：' + esc(first.v) + '</span>'
          : '<span class="u-ckbar-v">没有阻断项</span>') +
        act +
        '<span class="grow"></span>' +
        '<button class="u-ckbar-more" data-act="toggle-check">' + (ui.checkOpen ? '收起' : '展开') + '</button>' +
      '</div>' +
      (ui.checkOpen ? checkBlock({ head: false }) : '') +
    '</div>';
  }

  /* ── ② 产品细节：一行一条，就地改/删；2026-10-30 去掉重复表头、去重/只留/原文三件套 ── */
  function factsBlock() {
    const rows = D.factRows || [];
    const stText = { ok: '✓', dup: '重复', conflict: '冲突', weak: '需核验', empty: '空' };
    const stCls = { ok: 'ok', dup: 'warn', conflict: 'bad', weak: 'warn', empty: 'bad' };
    const list = rows.length
      ? rows.map((r) => '<li class="u-fact is-' + r.st + '" data-fid="' + esc(r.id) + '">' +
          '<button class="u-fact-k" data-act="facts-key" data-fid="' + esc(r.id) + '" title="换一个类目">' + esc(r.key) + '</button>' +
          '<input class="u-fact-v" data-fid="' + esc(r.id) + '" value="' + esc(r.value) + '" aria-label="细节内容" placeholder="写图里看得到的">' +
          (r.src ? '<span class="u-fact-src">' + esc(r.src) + '</span>' : '') +
          '<span class="u-fact-st ' + (stCls[r.st] || '') + '">' + (stText[r.st] || '') + '</span>' +
          (r.st === 'conflict'
            ? '<button class="u-fact-pick" data-act="facts-pick" data-fid="' + esc(r.id) + '" title="留这条，删掉同类其它值">用这条</button>'
            : '') +
          '<button class="u-fact-x" data-act="facts-del" data-fid="' + esc(r.id) + '" title="删掉这条">×</button>' +
          (r.st === 'weak' ? '<div class="u-fact-tip">功效类说法，模型无法从画面确认；改成看得到的表述，或删掉</div>' : '') +
        '</li>').join('')
      : '<li class="u-fact-empty">还没有：传张产品图自动识别，或点下面「＋ 加一条」。</li>';

    return '<div class="u-facts" data-facts>' +
      '<ul class="u-facts-list" data-facts-list>' + list + '</ul>' +
      '<div class="u-facts-tools"><button class="btn sm ghost" data-act="facts-add">＋ 加一条</button></div>' +
    '</div>';
  }

  /* ── ② 生成前检查（runtime 算结果，这里只画） ── */
  function checkBlock(o) {
    o = o || {};
    const ck = D.check || { items: [], blockers: 0 };
    const items = ck.items || [];
    const ic = { ok: '✓', warn: '!', bad: '✕' };
    const head = '<div class="u-chk2-h">生成前检查' +
        (ck.blockers
          ? '<span class="u-chk2-badge">' + ck.blockers + ' 项要修</span>'
          : '<span class="u-chk2-badge ok">可以生成</span>') +
        '<span class="grow"></span><span class="tiny muted">实时</span></div>';
    return '<div class="u-chk2' + (ck.blockers ? ' has-bad' : '') + '" data-check>' +
      (o.head === false ? '' : head) +
      items.map((it) => '<div class="u-chk2-row is-' + esc(it.st) + '">' +
        '<span class="u-chk2-ic ' + esc(it.st) + '">' + (ic[it.st] || '·') + '</span>' +
        '<span class="u-chk2-b"><i>' + esc(it.k) + '</i>' + esc(it.v) + '</span>' +
        (it.act ? '<button class="btn sm' + (it.st === 'bad' ? '' : ' ghost') + '" data-act="' + esc(it.act) + '">' + esc(it.actText) + '</button>' : '') +
      '</div>').join('') +
    '</div>';
  }

  /* ② 换成我的产品 */
  function product(o) {
    o = o || {};
    const f = D.form, v = o.variant || 'form';
    const facts = f.facts;
    if (v === 'chips') {
      return '<section class="u-prod u-prod-chips" data-unit="product">' +
        '<div class="u-prod-lead"><span class="lab">② 换成我的产品</span><b class="ell">' + esc(f.product) + '</b></div>' +
        '<div class="u-chiprow">' + [
          ['口播', f.language], ['时长', f.duration + ' 秒'], ['单段', f.clipLimit + ' 秒'], ['模式', '文生'], ['比例', f.ratio]
        ].map((x) => chip(x[0] + ' ' + x[1], 'solid')).join('') + '</div>' +
        '<span class="tiny muted nowrap">已确认事实 ' + facts.length + ' 字 · 产品图 0 张</span>' +
      '</section>';
    }
    if (v === 'table') {
      return '<section class="u-prod u-prod-table" data-unit="product">' +
        '<div class="mod-h"><h2>② 换成我的产品</h2><span class="hint">只改产品与事实，其余沿用对标</span></div>' +
        '<div class="mod-b"><table class="tbl"><tbody>' +
          '<tr><th>产品名称</th><td><b>' + esc(f.product) + '</b></td><th>口播语言</th><td>' + esc(f.language) + '</td></tr>' +
          '<tr><th>已确认事实</th><td colspan="3">' + esc(facts) + '</td></tr>' +
          '<tr><th>总时长</th><td>' + f.duration + ' 秒</td><th>单段上限</th><td>' + f.clipLimit + ' 秒</td></tr>' +
          '<tr><th>模式</th><td>文生（纯文本定义人物与产品）</td><th>比例</th><td>' + f.ratio + '</td></tr>' +
        '</tbody></table></div></section>';
    }
    if (o.editable) {
      const lang = ['德语', '英语', '法语', '西班牙语', '意大利语', '葡萄牙语', '中文', '日语', '韩语', '印尼语', '泰语', '越南语', '阿拉伯语'];
      const langList = lang.indexOf(f.language) >= 0 ? lang : (f.language ? [f.language].concat(lang) : lang);
      const durs = [8, 10, 12, 15, 20, 25, 30, 40, 50, 60];
      const clips = [4, 5, 6, 8, 10, 12, 15];
      const sel = (name, val, list, suffix) => '<select class="sel" data-field="' + name + '">' +
        list.map((x) => '<option value="' + x + '"' + (String(x) === String(val) ? ' selected' : '') + '>' + x + (suffix || '') + '</option>').join('') +
        '</select>';
      const ui = D.ui || {};
      /* 检查条只在真有问题时出现；没问题时整个 host 隐藏，不占高度 */
      const issues = ((D.check && D.check.items) || []).some((i) => i.st === 'bad' || i.st === 'warn');
      return '<section class="u-prod u-prod-form" data-unit="product">' +
        '<div class="mod-h"><h2>② 换成我的产品</h2><span class="hint">传张产品图，细节自动记下来</span></div>' +
        '<div class="mod-b u-form">' +
          '<div class="field" style="grid-column:span 2"><label for="f-product">产品名称</label>' +
            '<input id="f-product" class="inp" data-field="product" maxlength="150" value="' + esc(f.product) + '" placeholder="例：米白翻领针织上衣"></div>' +
          '<div style="grid-column:span 2">' + photosRow() + '</div>' +
          '<div style="grid-column:span 2">' + factsRow() + '</div>' +
          (ui.factsOpen
            ? '<div class="field" style="grid-column:span 2">' + factsBlock() + '</div>'
            : '') +
          '<div style="grid-column:span 2">' + paramsSummary(f) + '</div>' +
          (ui.paramsOpen
            ? '<div class="field"><label for="f-language">口播语言</label><select id="f-language" class="sel" data-field="language">' +
                '<option value=""' + (f.language ? '' : ' selected') + '>未选择</option>' +
                langList.map((x) => '<option value="' + esc(x) + '"' + (x === f.language ? ' selected' : '') + '>' + esc(x) + '</option>').join('') +
              '</select></div>' +
              '<div class="field"><label for="f-duration">目标总时长</label>' + sel('duration', f.duration, durs, ' 秒') + '</div>' +
              '<div class="field"><label for="f-clip">单段上限</label>' + sel('clipLimit', f.clipLimit, clips, ' 秒') + '</div>' +
              '<div class="field"><label for="f-ratio">画面比例</label>' + sel('ratio', f.ratio, ['9:16', '16:9', '1:1']) + '</div>' +
              '<div class="field"><label for="f-mode">生成模式</label>' +
                '<select id="f-mode" class="sel" data-field="mode">' +
                  ['text:文生（纯文字）', 'image:图生（上传起始图）', 'reference:多素材参考'].map((x) => {
                    const [k, label] = x.split(':');
                    return '<option value="' + k + '"' + (f.mode === k ? ' selected' : '') + '>' + label + '</option>';
                  }).join('') +
                '</select></div>' +
              '<div class="field"><label for="f-model">改写模型</label>' +
                '<select id="f-model" class="sel" data-field="model"><option value="">工作台默认</option></select></div>' +
              '<div class="field" style="grid-column:span 2"' + (f.mode === 'text' ? ' hidden' : '') + ' data-refs><label for="f-refs">参考素材说明</label>' +
                '<textarea id="f-refs" class="ta" data-field="refs" rows="2" maxlength="3000" placeholder="例：产品正面图锁定外观；对标视频只参考运镜">' + esc(f.refs) + '</textarea></div>'
            : '') +
        '</div>' +
        '<div class="mod-b u-chk-wrap" data-chk-host' + (issues ? '' : ' hidden') + ' style="padding-top:0">' + checkArea(ui) + '</div>' +
      '</section>';
    }
    return '<section class="u-prod u-prod-form" data-unit="product">' +
      '<div class="mod-h"><h2>② 换成我的产品</h2><span class="hint">只改产品与事实，其余沿用对标结构与口播语言</span>' +
        '<span class="grow"></span><button class="btn sm ghost" data-drawer="scan">用产品图辅助填写</button></div>' +
      '<div class="mod-b u-form">' +
        '<div class="field" style="grid-column:span 2"><label>产品名称</label><div class="inp u-inp-static">' + esc(f.product) + '</div></div>' +
        '<div class="field" style="grid-column:span 2"><label>已确认事实（' + facts.length + ' 字）</label><div class="ta u-inp-static" style="height:auto">' + esc(facts) + '</div></div>' +
        '<div class="field"><label>口播语言</label><div class="sel u-inp-static">' + esc(f.language) + '</div></div>' +
        '<div class="field"><label>目标总时长</label><div class="sel u-inp-static">' + f.duration + ' 秒</div></div>' +
        '<div class="field"><label>单段上限</label><div class="sel u-inp-static">' + f.clipLimit + ' 秒</div></div>' +
        '<div class="field"><label>画面比例</label><div class="sel u-inp-static">' + f.ratio + '</div></div>' +
        '<div class="field"><label>生成模式</label><div class="sel u-inp-static">文生（纯文字）</div></div>' +
        '<div class="field"><label>改写模型</label><div class="sel u-inp-static">' + esc(f.model) + '</div></div>' +
        '<div class="field" style="grid-column:span 2"><label>参考素材说明</label><div class="inp u-inp-static muted">未填写（文生模式不需要）</div></div>' +
        '<div class="field" style="grid-column:span 2"><label>产品图（0 张）</label>' +
          '<button class="drop" data-drawer="scan">拖入产品图，或点这里选择 · 识别品类 / 材质 / 颜色 / 场景 / 卖点</button></div>' +
      '</div></section>';
  }

  /* ③ 生成中的进度条：服务端没有中途推送，只能按时间估算，界面上明写「预计」 */
  function genProgress() {
    const g = D.progress || { pct: 0, phase: '准备中', elapsed: 0 };
    const pct = Math.max(0, Math.min(100, Number(g.pct) || 0));
    return '<div class="u-gbar">' +
      '<div class="u-gbar-t"><span data-gen-phase>' + esc(g.phase || '准备中') + '</span>' +
        '<b data-gen-pct>' + Math.round(pct) + '%</b></div>' +
      '<div class="u-gbar-track"><i data-gen-bar style="width:' + pct.toFixed(1) + '%"></i></div>' +
      '<div class="u-gbar-meta"><span data-gen-elapsed>已用 ' + Math.round(g.elapsed || 0) + ' 秒</span>' +
        '<span>·</span><span>预计 60–120 秒（按时间估算，不是真实进度）</span></div>' +
    '</div>' +
    '<button class="btn pri block" data-gen-btn disabled>生成中… 可切到别的视图</button>';
  }

  /* ③ 确认生成方式 */
  function mode(o) {
    o = o || {};
    const p = D.plan, v = o.variant || 'plan';
    const seg = (k, label, on) => '<button class="sg' + (on ? ' on' : '') + '" data-seg="' + k + '">' + label + '</button>';
    if (v === 'chips') {
      return '<section class="u-mode u-mode-chips" data-unit="mode">' +
        '<span class="lab">③ 生成方式</span>' +
        '<div class="seg">' + seg('split', '拆段生成', p.strategy === 'split') + seg('compress', '压缩成一条', p.strategy !== 'split') + '</div>' +
        chip(p.text.replace(/^\d+ 秒 → /, '') + ' · ' + D.form.ratio, 'solid') +
        '<button class="btn pri" data-act="generate">生成六版</button>' +
      '</section>';
    }
    if (v === 'summary') {
      return '<section class="u-mode u-mode-sum" data-unit="mode">' +
        '<div><div class="eb">③ 确认生成方式</div>' +
        '<div class="u-mode-line"><b>' + p.text + '</b><span class="muted tiny">' + D.form.ratio + ' · ' + esc(D.form.language) + ' · 一次最多 4 段</span></div></div>' +
        '<button class="btn pri" data-act="generate">开始生成</button>' +
      '</section>';
    }
    return '<section class="u-mode u-mode-plan" data-unit="mode">' +
      '<div class="mod-h"><h2>③ 确认生成方式</h2><span class="hint">时长与入口上限决定段数，不加速念台词</span></div>' +
      '<div class="mod-b u-mode-grid">' +
        '<div class="u-plan"><div class="eb">时长计划</div><b class="u-plan-big" data-plan-text>' + p.text + '</b>' +
          '<div class="u-plan-segs" data-plan-segs>' + p.segments.map((s, i) => '<i style="flex:' + s + '">' + (i + 1) + ' · ' + s + 's</i>').join('') + '</div></div>' +
        '<div class="u-mode-opts">' +
          '<div class="lab">分段策略</div>' +
          '<div class="seg">' + seg('split', '拆段生成', p.strategy === 'split') + seg('compress', '压缩成一条', p.strategy !== 'split') + '</div>' +
          (o.ratio === false ? '' :
            '<div class="lab" style="margin-top:10px">画面比例</div>' +
            '<div class="seg">' + seg('r916', '9:16', true) + seg('r169', '16:9', false) + seg('r11', '1:1', false) + '</div>') +
        '</div>' +
        '<div class="u-mode-go">' +
          (o.busy
            ? genProgress()
            : '<button class="btn pri block" data-gen-btn data-act="generate"' + ((D.check && D.check.blockers) ? ' disabled' : '') + '>' + (o.regenerate ? '重新生成六版' : '生成优化版 + 五个新版本') + '</button>') +
          '<div class="u-mode-gate"' + ((D.check && D.check.blockers) ? '' : ' hidden') + '>' +
            '<span class="u-gate-txt" data-gen-gate>先修 ' + ((D.check && D.check.blockers) || 0) + ' 项</span>' +
            '<button class="btn sm ghost" data-act="generate-force" data-gen-force>仍然生成</button>' +
          '</div>' +
          '<span class="tiny muted">' + (D.run && D.run.costSec ? '上次 ' + D.run.costSec + ' 秒' : '预计 60–120 秒') +
            ' · ' + p.count + ' 段 × ' + p.segments[0] + ' 秒 · ' + esc((D.meta || {}).model || '工作台默认') + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="u-mode-foot tiny muted">' +
        '<span>一场只跑一个任务</span><span>·</span><span>生成中可离开页面</span><span>·</span>' +
        '<span>结果自动留在本地，不写台账</span>' +
      '</div>' +
    '</section>';
  }

  /* ④ 分析 */
  function analysis(o) {
    o = o || {};
    const v = o.variant || 'panel';
    const groups = [
      ['保留点', A.strengths, 'ok'], ['问题', A.issues, 'bad'], ['改进', A.changes, ''], ['待补', A.missing, '']
    ];
    const head = '<div class="mod-h"><h2>④ 原片分析</h2>' +
      '<span class="hint">' + (A.duration == null ? '原片时长未标注' : '原片 ' + A.duration + ' 秒') + ' · ' +
      (A.ratio || '比例未标注') + ' · 口播 ' + (A.language || '未识别（原声无台词）') + '</span>' +
      '<span class="grow"></span><button class="btn sm ghost" data-drawer="analysis">看完整分析</button></div>';
    if (v === 'bullets') {
      return '<section class="u-ana u-ana-bul" data-unit="analysis">' + head +
        '<div class="mod-b"><p class="u-ana-sum">' + esc(A.summary) + '</p>' +
        '<div class="u-ana-4">' + groups.map((g) =>
          '<div class="u-ana-grp' + (g[2] ? ' ' + g[2] : '') + '"><div class="u-ana-t">' + g[0] + ' <b>' + g[1].length + '</b></div>' +
          '<ul class="bul ' + g[2] + '">' + g[1].slice(0, o.max || 3).map((x) => '<li>' + esc(cut(x, o.cut || 30)) + '</li>').join('') + '</ul></div>'
        ).join('') + '</div></div></section>';
    }
    if (v === 'table') {
      return '<section class="u-ana u-ana-tbl" data-unit="analysis">' + head +
        '<div class="mod-b"><table class="tbl"><thead><tr><th>类别</th><th>内容</th></tr></thead><tbody>' +
        groups.map((g) => '<tr><th>' + g[0] + ' · ' + g[1].length + '</th><td>' + esc(cut(g[1].join('；'), 92)) + '</td></tr>').join('') +
        '</tbody></table><p class="tiny muted" style="margin-top:8px">替换影响：' + esc(cut(A.replacement, 96)) + '</p></div></section>';
    }
    return '<section class="u-ana u-ana-panel" data-unit="analysis">' + head +
      '<div class="mod-b u-ana-body">' +
        '<div class="u-ana-left"><p class="u-ana-sum">' + esc(A.summary) + '</p>' +
          '<div class="u-ana-repl"><span class="lab">替换产品后必须改变</span><p>' + esc(cut(A.replacement, o.rep || 120)) + '</p></div></div>' +
        '<div class="u-ana-right">' + groups.map((g) =>
          '<div class="u-ana-grp' + (g[2] ? ' ' + g[2] : '') + '"><div class="u-ana-t">' + g[0] + ' <b>' + g[1].length + '</b></div>' +
          '<ul class="bul ' + g[2] + '">' + g[1].slice(0, o.max || 3).map((x) => '<li>' + esc(cut(x, o.cut || 26)) + '</li>').join('') + '</ul></div>'
        ).join('') + '</div>' +
      '</div></section>';
  }

  /* 版本卡（六版的核心内容，各变体共用） */
  function verBody(v, o) {
    o = o || {};
    const clips = v.clips.map((c, i) =>
      '<details class="u-more"><summary>第 ' + (i + 1) + ' 段 · ' + c.duration + ' 秒 · ' + c.shots.length + ' 镜 · ' + c.prompt.length + ' 字</summary>' +
      '<pre class="pre sm">' + esc(c.prompt) + '</pre></details>').join('');
    const dl = v.dialogue.length
      ? '<div class="u-ver-dl">' + v.dialogue.slice(0, o.dl || 2).map((d) => '<p class="qt">' + esc(cut(d, o.dlCut || 64)) + '</p>').join('') + '</div>'
      : '<p class="tiny muted">此版无口播（对标原声无台词）</p>';
    return '<div class="u-ver-h">' +
        '<span class="u-ver-kind">' + v.label + '</span>' +
        '<b class="u-ver-t" data-tip="' + esc(v.title) + '">' + esc(v.title) + '</b>' +
        '<span class="grow"></span>' +
        (v.warnings.length ? '<span class="u-ver-warn" data-tip="' + esc(v.warnings[0]) + '">' + warnIcon() + v.warnings.length + '</span>' : '') +
        '<button class="btn sm ghost" data-drawer="ver:' + v.id + '">展开</button>' +
      '</div>' +
      '<div class="u-ver-diff">' + esc(cut(v.difference, o.diff || 40)) + '</div>' +
      '<div class="u-axes">' + window.PO.axis('开头', v.hook) + window.PO.axis('场景', v.scene) + window.PO.axis('叙事', v.story) + window.PO.axis('风格', v.style) + '</div>' +
      (o.dl === 0 ? '' : dl) +
      (o.prompts === false ? '' : clips) +
      '<div class="u-ver-f">' +
        '<span class="tiny muted">' + v.shots + ' 镜 · ' + v.chars + ' 字' + (v.dialogue.length ? ' · ' + v.words + ' 词口播' : '') + '</span>' +
        '<span class="grow"></span>' + btnCopy(v.id + 'c0', '复制第 1 段', 'ghost sm') +
      '</div>';
  }

  function versions(o) {
    o = o || {};
    const v = o.variant || 'grid';
    const cards = D.versions.map((x) =>
      '<article class="u-ver u-ver-' + x.label.toLowerCase() + '" data-ver="' + x.id + '" data-drill="ver:' + x.id + '">' + verBody(x, o) + '</article>').join('');
    if (v === 'rows') {
      return '<section class="u-vers u-vers-rows" data-unit="versions">' +
        '<div class="mod-h"><h2>⑤ 六个版本</h2><span class="hint">优化版保留对标结构，B 类换开头/场景/叙事，C 类重新构思</span>' +
        '<span class="grow"></span>' + btnCopy('all', '复制全部', 'sm') + '</div>' +
        '<div class="mod-b u-rows">' + cards + '</div></section>';
    }
    if (v === 'list') {
      return '<section class="u-vers u-vers-list" data-unit="versions">' +
        '<div class="mod-h"><h2>⑤ 六个版本</h2><span class="hint">' + D.counts.shots + ' 镜 · ' + D.counts.clips + ' 段 · ' + nf(D.counts.chars) + ' 字提示词</span>' +
        '<span class="grow"></span>' + btnCopy('all', '复制全部', 'sm') + '</div>' +
        '<div class="mod-b u-list">' + cards + '</div></section>';
    }
    return '<section class="u-vers u-vers-grid" data-unit="versions">' +
      '<div class="mod-h"><h2>⑤ 六个版本</h2><span class="hint">优化版 + 3 个 B 类 + 2 个 C 类 · 每版独立可生成</span>' +
      '<span class="grow"></span>' + btnCopy('all', '复制全部', 'sm') + '</div>' +
      '<div class="mod-b u-grid6">' + cards + '</div></section>';
  }

  /* ⑥ 六版自检 */
  const CHK_COLS = [['structure', '结构'], ['timeline', '时间轴'], ['speech', '口播'], ['framing', '取景'], ['claims', '事实'], ['template', '模板'], ['cross_version', '跨版']];
  function selfcheck(o) {
    o = o || {};
    const v = o.variant || 'matrix';
    const sum = S.summary;
    const rows = S.versions.map((r) => {
      const cells = CHK_COLS.map((c) => {
        const it = r.items.find((x) => x.code === c[0]);
        const st = it ? it.status : 'pass';
        return '<td class="u-chkcell"><i class="u-chkc ' + window.PO.statusCls[st] + '" data-tip="' +
          esc(c[1] + '：' + statusLabel[st] + (it ? ' — ' + cut(it.reasons.join('；'), 60) : '')) + '"></i></td>';
      }).join('');
      return '<tr data-chk="' + r.index + '"><th><b>' + r.label + '</b> <span class="ell">' + esc(cut(r.title, 12)) + '</span></th>' +
        cells + '<td class="r">' + statusChip(r.status) + '</td></tr>';
    }).join('');
    const head = '<div class="mod-h"><h2>⑥ 六版自检</h2>' +
      '<span class="hint">' + S.method + ' · 不新增模型调用 · ' + cut(S.ruleGate.reasons[0], 26) + '</span>' +
      '<span class="grow"></span>' + statusChip(S.status) + '<button class="btn sm ghost" data-drawer="selfcheck">逐项原因</button></div>';
    if (v === 'bars') {
      return '<section class="u-chk u-chk-bars" data-unit="selfcheck">' + head +
        '<div class="mod-b"><div class="u-chk-sum">' +
          [['规则通过', sum.rule_passed], ['需修改', sum.needs_revision], ['需人工', sum.manual], ['对比对', sum.pairs]].map((x) =>
            '<div class="u-chk-k"><b class="num">' + x[1] + '</b><span>' + x[0] + '</span></div>').join('') +
        '</div>' +
        '<div class="u-chk-list">' + S.versions.map((r) => {
          const bad = r.items.filter((i) => i.status === 'needs_revision').length;
          return '<div class="u-chk-row"><b>' + r.label + '</b><span class="ell">' + esc(cut(r.title, 16)) + '</span>' +
            '<div class="u-chk-bar"><i style="width:' + (bad ? pct(bad, r.items.length) : 4) + '%"></i></div>' +
            statusChip(r.status) + '</div>';
        }).join('') + '</div></div></section>';
    }
    if (v === 'list') {
      return '<section class="u-chk u-chk-listv" data-unit="selfcheck">' + head +
        '<div class="mod-b"><ul class="bul">' + S.versions.map((r) =>
          '<li><b>' + r.label + '</b> ' + statusChip(r.status) + ' <span class="muted">' + esc(cut(r.title, 18)) + ' · ' +
          r.items.filter((i) => i.status === 'pass').length + '/' + r.items.length + ' 项规则通过</span></li>').join('') + '</ul></div></section>';
    }
    return '<section class="u-chk u-chk-matrix" data-unit="selfcheck">' + head +
      '<div class="mod-b"><table class="tbl u-matrix"><thead><tr><th>版本</th><th>' + CHK_COLS.map((c) => c[1]).join('</th><th>') + '</th><th class="r">结果</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>' +
      '<div class="u-chk-foot tiny muted"><span>6 版 × 7 项规则 · 每格可点看原因</span><span>需人工：口播语言、产品事实、成片效果</span></div></div></section>';
  }

  /* ⑦ 历史结果 */
  function history(o) {
    o = o || {};
    const list = D.history || [];
    const hist = D.hist || { query: '', sel: [], limit: 20 };
    const sel = hist.sel || [];

    if ((o.variant || '') === 'chips') {
      return '<section class="u-his u-his-chips" data-unit="history"><span class="lab">历史结果</span>' +
        list.map((h, i) => '<button class="u-his-item" data-his="' + i + '"><b class="num">' + esc(h.at) + '</b>' +
          '<span class="ell">' + esc(h.title) + '</span></button>').join('') + '</section>';
    }

    /* 按天分组：今天 / 昨天 / MM-DD */
    const groups = [];
    list.forEach((h, i) => {
      const g = h.ts ? (function (ts) {
        const d = new Date(ts), now = new Date();
        const k = (x) => x.getFullYear() + '-' + (x.getMonth() + 1) + '-' + x.getDate();
        const y = new Date(now.getTime() - 86400000);
        if (k(d) === k(now)) return '今天 · ' + (d.getMonth() + 1) + '-' + d.getDate();
        if (k(d) === k(y)) return '昨天 · ' + (d.getMonth() + 1) + '-' + d.getDate();
        return (d.getMonth() + 1) + '-' + d.getDate();
      })(h.ts) : '更早';
      const last = groups[groups.length - 1];
      if (last && last.label === g) last.items.push([h, i]);
      else groups.push({ label: g, items: [[h, i]] });
    });

    const row = ([h, i]) => {
      const f = h.form;
      const chips = [];
      if (f) {
        if (f.product) chips.push('<span class="u-his-chip">产品 ' + esc(cut(f.product, 12)) + '</span>');
        chips.push('<span class="u-his-chip">事实 ' + (h.factsCount || 0) + ' 条</span>');
        const modeName = { text: '文生', image: '图生', reference: '多素材' }[h.mode || f.mode] || '';
        if (modeName) chips.push('<span class="u-his-chip">' + modeName + '</span>');
        chips.push(h.same
          ? '<span class="u-his-same ok">= 当前输入</span>'
          : '<span class="u-his-same">≠ 当前输入</span>');
      }
      const search = [h.at, h.title, h.product, (f && f.product) || ''].join(' ').toLowerCase();
      return '<div class="u-his-row' + (sel.indexOf(h.id) >= 0 ? ' is-sel' : '') + '" data-his-row ' +
          'data-s="' + esc(search) + '" data-his-id="' + esc(h.id || '') + '">' +
        '<button class="u-his-cb' + (sel.indexOf(h.id) >= 0 ? ' on' : '') + '" data-act="his-sel" ' +
          'data-his-id="' + esc(h.id || '') + '" aria-label="选中"></button>' +
        '<div class="u-his-main">' +
          '<div class="u-his-l1">' +
            '<button class="u-his-title" data-act="his-view" data-his="' + i + '" title="只看这次的结果">' + esc(h.title) + '</button>' +
            '<span class="u-his-meta">' + esc(h.at) + ' · ' + h.duration + ' 秒 · ' + esc(h.language) + ' · ' + h.versions + ' 版</span>' +
          '</div>' +
          (chips.length ? '<div class="u-his-l2">' + chips.join('') + '</div>' : '') +
        '</div>' +
        '<div class="u-his-acts">' +
          (f ? '<button class="btn sm pri" data-act="his-load" data-his="' + i + '">载入输入</button>' : '') +
          '<button class="btn sm ghost" data-act="his-view" data-his="' + i + '">看结果</button>' +
        '</div>' +
      '</div>';
    };

    return '<section class="u-his u-his-lib" data-unit="history">' +
      '<div class="mod-h"><h2>⑦ 历史库</h2>' +
        '<span class="hint">' + list.length + ' / ' + (hist.limit || 20) + ' 条 · 本地存储</span>' +
        '<span class="grow"></span>' +
        '<input class="inp u-his-q" data-hist-q placeholder="搜产品 / 标题" value="' + esc(hist.query || '') + '">' +
        '<button class="btn sm ghost" data-act="his-clear">清空</button></div>' +
      '<div class="mod-b u-his-col">' +
        (list.length
          ? groups.map((g) => '<div class="u-his-day" data-his-day>' + esc(g.label) + '</div>' +
              g.items.map(row).join('')).join('')
          : '<p class="u-his-empty">还没有历史。生成一次六版后会自动留在这里。</p>') +
        '<div class="u-his-bulk"' + (sel.length ? '' : ' hidden') + '>' +
          '<span data-his-selcount>已选 ' + sel.length + ' 条</span>' +
          '<button class="btn sm" data-act="his-export-sel">导出 .md</button>' +
          '<button class="btn sm ghost" data-act="his-del-sel">删除</button>' +
          '<span class="grow"></span><span class="tiny muted">上限 ' + (hist.limit || 20) + ' 条，超出时丢最旧的</span>' +
        '</div>' +
      '</div></section>';
  }

  /* ⑧ 导出 */
  function exportBar(o) {
    o = o || {};
    const v = o.variant || 'row';
    const btns = '<button class="btn sm" data-act="export">导出六版 .md</button>' +
      btnCopy('all', '复制全部', 'sm') +
      '<button class="btn sm ghost" data-act="backup">备份草稿</button>';
    if (v === 'inline') return '<div class="u-exp u-exp-inline" data-unit="export">' + btns + '</div>';
    if (v === 'menu') {
      return '<div class="u-exp u-exp-menu" data-unit="export"><span class="lab">导出</span>' + btns + '</div>';
    }
    return '<section class="u-exp u-exp-row" data-unit="export">' +
      '<div class="mod-h"><h2>⑧ 导出</h2><span class="hint">导出为 .md，粘贴到平台即可；备份含输入与六版结果</span>' +
      '<span class="grow"></span></div>' +
      '<div class="mod-b u-exp-btns">' + btns + '</div></section>';
  }

  /* ⑨ 状态 */
  function status(o) {
    o = o || {};
    const v = o.variant || 'strip';
    const run = D.run || {};
    const has = run.status === 'done';
    const meta = [
      ['模型', (D.meta || {}).model || '工作台默认'],
      ['生成', has ? run.at : '尚未生成'],
      ['耗时', run.costSec ? run.costSec + ' 秒' : '—'],
      ['输入', run.stale ? '已修改' : '未改动']
    ];
    if (v === 'badge') {
      return '<div class="u-sta u-sta-badge" data-unit="status">' + chip(has ? '已生成 · ' + run.at : '尚未生成', has ? 'acc' : 'solid', run.stale ? '输入已改动，结果需重跑' : '') +
        chip((D.meta || {}).model || '工作台默认', 'solid') + (has ? statusChip(D.selfCheck.status) : '') + '</div>';
    }
    return '<div class="u-sta u-sta-' + v + '" data-unit="status">' +
      '<span class="u-sta-dot' + (has ? '' : ' idle') + '" aria-hidden="true"></span>' +
      meta.map((m) => '<span class="u-sta-i"><em>' + m[0] + '</em><b>' + esc(m[1]) + '</b></span>').join('') +
      '<span class="grow"></span>' +
      '<span class="u-sta-msg" data-sta-msg>' + (has ? '输入未改动 · 结果是最新一次生成' : '填好左边三步后点生成') + '</span>' +
      (has ? statusChip(D.selfCheck.status) : '') +
    '</div>';
  }

  return { head, source, product, mode, analysis, versions, selfcheck, history, exportBar, status, verBody, factsBlock, checkBlock, checkArea };
})();
