/* ============================================================
   acc-v2.js · 账号管理 · 看板版（S236 落地）
   数据：GET /api/accounts（真实）  ·  详情：GET /api/accounts/{name}
   写：POST /api/accounts、PATCH/DELETE /api/accounts/{name}、
       POST /api/accounts/media-type（全部走 127.0.0.1 本机写接口）
   ============================================================ */
(function () {
  'use strict';

  const API = '/api/accounts';
  const REGION = {
    '': '未标注', us: '美国', uk: '英国', eu: '欧洲', de: '德国', fr: '法国',
    es: '西语区', it: '意区', cn: '中国大陆', 'cn-hk': '中国香港', 'cn-tw': '中国台湾',
    jp: '日本', kr: '韩国'
  };
  const LANGS = ['en', 'de', 'fr', 'es', 'it', 'zh', 'jp', 'kr'];

  const $ = (s, r) => (r || document).querySelector(s);
  const stage = document.getElementById('stage');

  const st = {
    q: '', status: '', type: '', group: '',
    sort: { k: '', d: 1 },
    sel: new Set(),
    focus: null,
    form: { mode: 'create', account: null },
    busy: false
  };
  const D = { items: [], counts: {}, groups: [], at: null, error: null, loading: true };

  /* ── 小工具 ───────────────────────────────────────────── */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const int = (n) => Number(n || 0).toLocaleString('zh-CN');
  const sevOf = (a) => (a.severity === 'error' ? 'bad' : a.severity === 'warn' ? 'warn' : 'ok');
  const sevLabel = (s) => (s === 'error' ? '异常' : s === 'warn' ? '缺口' : '正常');
  const regionLabel = (c) => REGION[c || ''] || c || '未标注';
  const typeChip = (a) => (a.media_type === 'image'
    ? '<span class="chip img">图文</span>' : '<span class="chip vid">视频</span>');
  const activeChip = (a) => (a.active
    ? '<span class="chip ok">启用</span>' : '<span class="chip off">停用</span>');
  const tagsHtml = (a) => (a.tags || []).slice(0, 2).map((t) => '<span class="tag-mini">' + esc(t) + '</span>').join('');

  function toast(msg, tone) {
    let el = $('#toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
    el.textContent = msg;
    el.classList.toggle('bad', tone === 'bad');
    el.classList.add('on');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('on'), 2600);
  }

  async function api(path, opt) {
    const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opt || {}));
    let body = null;
    try { body = await res.json(); } catch (e) { /* 空响应 */ }
    if (!res.ok) {
      const detail = body && (body.detail || body.error || body.message);
      throw new Error(typeof detail === 'string' ? detail : ('HTTP ' + res.status));
    }
    return body;
  }

  /* ── 读取 ─────────────────────────────────────────────── */
  async function load(keepScroll) {
    const y = window.scrollY;
    D.loading = true; D.error = null;
    if (!D.items.length) mount();
    try {
      const d = await api(API);
      D.items = (d.accounts || []).map((a) => Object.assign({}, a, {
        regionLabel: regionLabel(a.region),
        has_artifacts: !!a.has_artifacts
      }));
      D.counts = d.counts || {};
      D.groups = computeGroups(D.items);
      D.at = new Date();
      D.loading = false;
      D.error = null;
    } catch (e) {
      D.loading = false;
      D.error = e.message || String(e);
    }
    if (st.focus && !D.items.some((a) => a.account === st.focus)) st.focus = null;
    mount();
    if (keepScroll) window.scrollTo(0, y);
  }

  function computeGroups(items) {
    const map = new Map();
    items.forEach((a) => {
      const key = a.group_name || '未分组';
      if (!map.has(key)) map.set(key, { name: key, accounts: 0, active: 0, inactive: 0, video: 0, image: 0, landingSum: 0 });
      const g = map.get(key);
      g.accounts++;
      if (a.active) g.active++; else g.inactive++;
      if (a.media_type === 'image') g.image++; else g.video++;
      g.landingSum += Number(a.landing) || 0;
    });
    return [...map.values()]
      .map((g) => Object.assign({}, g, { landing: g.accounts ? Math.round(g.landingSum / g.accounts) : 0 }))
      .sort((a, b) => b.accounts - a.accounts);
  }

  function match(a) {
    if (st.q) {
      const hay = [a.account, a.nickname, (a.tags || []).join(' '), a.group_name].join(' ').toLowerCase();
      if (!hay.includes(st.q)) return false;
    }
    if (st.status === 'active' && !a.active) return false;
    if (st.status === 'inactive' && a.active) return false;
    if (st.type && a.media_type !== st.type) return false;
    if (st.group && (a.group_name || '未分组') !== st.group) return false;
    return true;
  }

  function sorted(items) {
    if (!st.sort.k) return items;
    const k = st.sort.k, d = st.sort.d;
    return items.slice().sort((x, y) => {
      if (['account', 'group_name', 'regionLabel', 'media_type'].includes(k)) {
        return d * String(x[k] || '').localeCompare(String(y[k] || ''), 'zh');
      }
      if (k === 'active') return d * ((x.active ? 1 : 0) - (y.active ? 1 : 0));
      return d * ((Number(x[k]) || 0) - (Number(y[k]) || 0));
    });
  }

  /* ── 局部渲染 ─────────────────────────────────────────── */
  function kpis() {
    const c = D.counts;
    const list = [
      { k: 'all', l: '账号总数', v: c.all, u: '个', cap: 'accounts.json 记录数' },
      { k: 'active', l: '启用中', v: c.active, u: '个', cap: '参与流水线 · ' + (c.inactive || 0) + ' 个停用' },
      { k: 'image', l: '图文账号', v: c.image, u: '个', cap: '跳过音频与转写' },
      { k: 'artifacts', l: '有产物', v: c.artifacts, u: '个', cap: '有台账/音频 → 不可直接删' }
    ];
    return '<div class="u-kpis">' + list.map((x) =>
      '<button type="button" class="k' + (x.k === 'artifacts' && c.artifacts === c.all ? ' is-warn' : '') + '" data-drill="' + x.k + '">' +
      '<div class="k-l">' + x.l + '<span class="arw">⌄</span></div>' +
      '<div class="k-v"><b class="tnum">' + int(x.v) + '</b><span class="u">' + x.u + '</span></div>' +
      '<div class="k-c">' + esc(x.cap) + '</div></button>').join('') + '</div>';
  }

  function filters() {
    const opts = D.groups.map((g) => '<option value="' + esc(g.name) + '"' + (st.group === g.name ? ' selected' : '') + '>' +
      esc(g.name) + '（' + g.accounts + '）</option>').join('');
    const matched = D.items.filter(match).length;
    return '<section class="u-filters">' +
      '<div class="fs-q"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>' +
      '<input type="search" data-slot="q" value="' + esc(st.q) + '" placeholder="搜索账号 / 昵称 / 标签 / 分组" aria-label="搜索账号"></div>' +
      '<label class="fs"><span class="fs-l">状态</span><select data-f="status">' +
      '<option value="">全部状态</option><option value="active"' + (st.status === 'active' ? ' selected' : '') + '>启用中</option>' +
      '<option value="inactive"' + (st.status === 'inactive' ? ' selected' : '') + '>已停用</option></select></label>' +
      '<label class="fs"><span class="fs-l">类型</span><select data-f="type">' +
      '<option value="">全部类型</option><option value="video"' + (st.type === 'video' ? ' selected' : '') + '>视频账号</option>' +
      '<option value="image"' + (st.type === 'image' ? ' selected' : '') + '>图文账号</option></select></label>' +
      '<label class="fs"><span class="fs-l">分组</span><select data-f="group"><option value="">全部分组</option>' + opts + '</select></label>' +
      '<span class="fs-hint" data-slot="hint">匹配 ' + matched + ' / ' + D.items.length + '</span>' +
      '<button class="btn ghost" data-op="clear">清空</button></section>';
  }

  function card(a) {
    const gaps = (a.gaps || []).filter((g) => g.level !== 'info');
    return '<div class="a29-card' + (a.active ? '' : ' off') + '" data-account="' + esc(a.account) + '" data-drill="acct:' + esc(a.account) + '" tabindex="0" role="button">' +
      '<div class="a29-c-h"><span class="dot ' + sevOf(a) + '"></span><b>' + esc(a.account) + '</b>' + typeChip(a) + '</div>' +
      '<div class="a29-c-g">' + esc(a.group_name || '未分组') + ' · ' + esc(a.regionLabel) + ' · ' + esc(a.lang) + '</div>' +
      '<div class="a29-c-n"><span>抓取 <b class="tnum">' + int(a.scrape_items) + '</b></span>' +
      '<span>台账 <b class="tnum">' + int(a.ledger_rows) + '</b></span>' +
      '<span>落地 <b class="tnum">' + (Number(a.landing) || 0) + '%</b></span></div>' +
      '<div class="a29-c-f"><span class="a29-ops">' +
      '<button class="bl" data-op="sheet" data-account="' + esc(a.account) + '">表格</button>' +
      '<button class="bl" data-op="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="bl" data-op="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="bl danger" data-op="del" data-account="' + esc(a.account) + '">删除</button></span>' +
      (gaps.length ? '<span class="chip warn">' + esc(gaps[0].label) + '</span>'
        : (a.severity !== 'ok' ? '<span class="chip ' + sevOf(a) + '">' + sevLabel(a.severity) + '</span>' : '')) +
      '</div></div>';
  }

  function board() {
    const low = (a) => a.active && (Number(a.landing) || 0) < 90;
    const cols = [
      { k: 'video', t: '视频 · 启用', ids: D.items.filter((a) => a.active && !low(a) && a.media_type !== 'image') },
      { k: 'image', t: '图文 · 启用', ids: D.items.filter((a) => a.active && !low(a) && a.media_type === 'image') },
      { k: 'warn', t: '低落地 · <90%', ids: D.items.filter(low) },
      { k: 'off', t: '停用', ids: D.items.filter((a) => !a.active) }
    ];
    return '<div class="a29-board">' + cols.map((c) => {
      const shown = c.ids.filter(match);
      return '<section class="a29-col ' + c.k + '">' +
        '<header class="a29-col-h"><b>' + c.t + '</b><span class="tnum">' + shown.length + '</span>' +
        '<span class="tiny muted">' + (c.k === 'warn' ? '按落地升序' : '列内下滑') + '</span></header>' +
        '<div class="a29-col-b">' + (shown.length ? shown.map(card).join('') : '<div class="a29-more tiny muted">没有匹配的账号</div>') + '</div>' +
        '</section>';
    }).join('') + '</div>';
  }

  const COLS = [
    { k: 'account', t: '账号' }, { k: 'regionLabel', t: '地区 · 语言' }, { k: 'media_type', t: '类型' },
    { k: 'group_name', t: '分组' }, { k: 'active', t: '状态' }, { k: 'scrape_items', t: '抓取', r: 1 },
    { k: 'audio_count', t: '音频', r: 1 }, { k: 'transcript_count', t: '转写', r: 1 },
    { k: 'ledger_rows', t: '台账', r: 1 }, { k: 'landing', t: '健康' }, { k: 'ops', t: '操作' }
  ];

  function rows() {
    const list = sorted(D.items.filter(match));
    if (!list.length) {
      return '<div class="a29-state"><b>没有匹配的账号</b>换个关键词，或点「清空」重置筛选条件</div>';
    }
    return list.map((a) => '<tr data-account="' + esc(a.account) + '" data-drill="acct:' + esc(a.account) + '"' + (a.active ? '' : ' class="off"') + '>' +
      '<td class="c-acc"><div class="an"><span class="dot ' + sevOf(a) + '"></span><b>' + esc(a.account) + '</b></div>' +
      '<div class="as">' + (a.nickname ? esc(a.nickname) : '<span class="muted">无昵称</span>') + tagsHtml(a) + '</div></td>' +
      '<td class="c-region nowrap"><span class="mut">' + esc(a.regionLabel) + '</span> <i class="lg-t">' + esc(a.lang) + '</i></td>' +
      '<td>' + typeChip(a) + '</td>' +
      '<td class="nowrap c-group">' + esc(a.group_name || '未分组') + '</td>' +
      '<td>' + activeChip(a) + '</td>' +
      '<td class="r tnum">' + int(a.scrape_items) + '</td>' +
      '<td class="r tnum">' + int(a.audio_count) + '</td>' +
      '<td class="r tnum">' + int(a.transcript_count) + '</td>' +
      '<td class="r tnum">' + int(a.ledger_rows) + '</td>' +
      '<td class="c-health nowrap"><span class="chip ' + sevOf(a) + '">' + sevLabel(a.severity) + '</span>' +
      '<i class="tnum">' + (Number(a.landing) || 0) + '%</i></td>' +
      '<td class="c-ops"><span class="rowops">' +
      '<button class="bl" data-op="sheet" data-account="' + esc(a.account) + '">表格</button>' +
      '<button class="bl" data-op="detail" data-account="' + esc(a.account) + '">详情</button>' +
      '<button class="bl" data-op="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="bl" data-op="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="bl danger" data-op="del" data-account="' + esc(a.account) + '">删除</button></span></td></tr>').join('');
  }

  function tableSection() {
    const list = sorted(D.items.filter(match));
    return '<section class="mod a29-table">' +
      '<header class="mod-h"><h2>核对视图</h2><span class="hint">11 列全字段 · 点表头排序 · 表内滚动</span>' +
      '<div class="mod-a"><button class="btn ghost" data-op="clear">清空筛选</button></div></header>' +
      '<div class="mod-b"><div class="a29-scroll"><table class="acct-tbl"><thead><tr>' +
      COLS.map((c) => '<th class="' + (c.r ? 'r ' : '') + (c.k === 'ops' ? 'c-ops' : '') + '"' + (c.k !== 'ops' ? ' data-sort="' + c.k + '"' : '') + '>' +
        c.t + (c.k !== 'ops' ? '<i class="srt">' + (st.sort.k === c.k ? (st.sort.d > 0 ? '↑' : '↓') : '↕') + '</i>' : '') + '</th>').join('') +
      '</tr></thead><tbody data-slot="tbody">' + rows() + '</tbody></table></div>' +
      '<div class="tbl-foot"><span data-slot="hint2">' + list.length + ' / ' + D.items.length + ' 行</span>' +
      '<span class="muted">本页 ' + list.length + ' 行 · 其中 ' + list.filter((a) => a.has_artifacts).length + ' 个有产物（删除被拦）</span></div></div></section>';
  }

  function detailSection() {
    const a = D.items.find((x) => x.account === st.focus) || null;
    if (!a) {
      return '<section class="u-detail"><div class="a29-state"><b>当前未选中账号</b>点左侧卡片或表格行，选一个账号看详情</div></section>';
    }
    const nums = [['抓取', a.scrape_items], ['音频', a.audio_count], ['转写', a.transcript_count],
      ['台账', a.ledger_rows], ['商品', a.products_count], ['落地', (Number(a.landing) || 0) + '%']];
    const kv = [
      ['账号', esc(a.account)],
      ['昵称', a.nickname ? esc(a.nickname) : '<span class="muted">未填</span>'],
      ['地区 / 语言', esc(a.regionLabel) + ' / ' + esc(a.lang)],
      ['类型', a.media_type === 'image' ? '图文账号' : '视频账号'],
      ['分组', esc(a.group_name || '未分组')],
      ['状态', a.active ? '启用中' : '已停用'],
      ['最近成功', a.last_success_date ? esc(a.last_success_date) : '<span class="muted">未回写</span>']
    ];
    return '<section class="u-detail">' +
      '<div class="u-detail-h"><span class="av ' + sevOf(a) + '">' + esc((a.nickname || a.account).slice(0, 1).toUpperCase()) + '</span>' +
      '<div class="u-detail-n"><b>' + esc(a.account) + '</b>' +
      '<span class="tiny muted">' + esc(a.group_name || '未分组') + ' · ' + esc(a.regionLabel) + ' · ' + esc(a.lang) + '</span></div>' +
      '<span class="chip ' + sevOf(a) + '">' + sevLabel(a.severity) + '</span></div>' +
      '<div class="u-detail-nums">' + nums.map(([k, v]) =>
        '<div class="dn"><span>' + k + '</span><b class="tnum">' + v + '</b></div>').join('') + '</div>' +
      '<dl class="u-detail-kv">' + kv.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('') + '</dl>' +
      '<div class="u-detail-acts">' +
      '<button class="btn pri" data-op="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="btn" data-op="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="btn ghost" data-op="detail" data-account="' + esc(a.account) + '">看缺口与产物</button>' +
      '<button class="btn ghost" data-op="del" data-account="' + esc(a.account) + '">删除</button></div></section>';
  }

  function formSection() {
    const edit = st.form.mode === 'edit';
    const a = edit ? D.items.find((x) => x.account === st.form.account) : null;
    if (edit && !a) { st.form = { mode: 'create', account: null }; return formSection(); }
    const val = (k, d) => (a ? a[k] : d);
    return '<section class="mod" data-unit="form"><header class="mod-h"><h2>' + (edit ? '编辑 · ' + esc(a.account) : '新增账号') + '</h2>' +
      '<span class="hint">主键不可改 · 8 个可编辑字段</span></header>' +
      '<div class="mod-b"><div class="u-form">' +
      '<div class="uf-grid">' +
      '<label class="ff ro"><span class="ff-l">账号<i class="req">*</i></span>' +
      '<input type="text" name="account" value="' + esc(edit ? a.account : '') + '"' + (edit ? ' readonly' : '') + ' placeholder="小写字母/数字/下划线"></label>' +
      '<label class="ff"><span class="ff-l">昵称</span><input type="text" name="nickname" value="' + esc(val('nickname', '')) + '"></label>' +
      '<label class="ff"><span class="ff-l">地区</span><select name="region">' +
      Object.keys(REGION).map((c) => '<option value="' + c + '"' + (String(val('region', '')).toLowerCase() === c ? ' selected' : '') + '>' + (c || '未标注') + '</option>').join('') +
      '</select></label>' +
      '<label class="ff"><span class="ff-l">语言</span><select name="lang">' +
      LANGS.map((l) => '<option value="' + l + '"' + (val('lang', 'en') === l ? ' selected' : '') + '>' + l + '</option>').join('') +
      '</select></label>' +
      '<label class="ff"><span class="ff-l">类型</span><span class="seg" role="group">' +
      '<button type="button" class="sg' + (val('media_type', 'video') !== 'image' ? ' on' : '') + '" data-pick="media_type" data-v="video">视频</button>' +
      '<button type="button" class="sg' + (val('media_type', 'video') === 'image' ? ' on' : '') + '" data-pick="media_type" data-v="image">图文</button></span>' +
      '<input type="hidden" name="media_type" value="' + esc(val('media_type', 'video')) + '"></label>' +
      '<label class="ff"><span class="ff-l">标签</span><input type="text" name="tags" value="' + esc((a && a.tags || []).join(',')) + '" placeholder="逗号分隔"></label>' +
      '<label class="ff"><span class="ff-l">备注</span><input type="text" name="note" value="' + esc(val('note', '')) + '"></label>' +
      '<label class="ff"><span class="ff-l">启用</span><span class="sw"><input type="checkbox" name="active"' + ((a ? !!a.active : true) ? ' checked' : '') + '><i></i><span>' + ((a ? !!a.active : true) ? '启用' : '停用') + '</span></span></label>' +
      '</div>' +
      '<div class="uf-acts"><button class="btn pri" data-op="save" data-mode="' + (edit ? 'edit' : 'create') + '"' + (st.busy ? ' disabled' : '') + '>' +
      (edit ? '保存修改' : '创建账号') + '</button>' +
      '<button class="btn ghost" data-op="cancel">' + (edit ? '退出编辑' : '重置') + '</button>' +
      '<span class="tiny muted" data-slot="form-msg">写入 accounts.json 后立即刷新清单</span></div>' +
      '</div></div></section>';
  }

  function importSection() {
    return '<section class="mod"><header class="mod-h"><h2>批量导入</h2><span class="hint">粘贴 → 校验 → 写入</span></header>' +
      '<div class="mod-b"><div class="u-imp">' +
      '<ol class="imp-steps"><li class="on">粘贴清单</li><li>校验重复</li><li>写入 accounts.json</li></ol>' +
      '<textarea class="imp-ta" data-slot="paste" spellcheck="false" placeholder="每行一个：账号,昵称,地区,语言&#10;例如：newaccount,小王,de,de"></textarea>' +
      '<div class="imp-hint">每行 <code>账号,昵称,地区,语言</code> · 空行忽略 · 主键重复会拦下</div>' +
      '<div class="imp-res" data-slot="import-res"><span class="chip">0 行</span><span class="tiny muted">粘贴后点「校验」</span></div>' +
      '<div class="imp-acts"><button class="btn" data-op="validate">校验</button>' +
      '<button class="btn pri" data-op="import"' + (st.busy ? ' disabled' : '') + '>导入</button></div>' +
      '</div></div></section>';
  }

  function opsSection() {
    const total = D.items.filter(match).length;
    return '<section class="mod"><header class="mod-h"><h2>批量操作</h2><span class="hint">本机写接口 · 127.0.0.1</span>' +
      '<div class="mod-a"><span class="tiny muted" data-slot="sel">已选 ' + st.sel.size + ' / ' + total + '</span></div></header>' +
      '<div class="mod-b"><div class="u-ops">' +
      '<label class="op-all"><input type="checkbox" data-op="selall" data-count="' + total + '"> 全选当前筛选结果（' + total + ' 个）</label>' +
      '<div class="op-grp"><span class="op-l">类型</span>' +
      '<button class="btn" data-op="bulk-type" data-type="image">改为图文</button>' +
      '<button class="btn" data-op="bulk-type" data-type="video">改为视频</button></div>' +
      '<div class="op-grp"><span class="op-l">状态</span>' +
      '<button class="btn" data-op="bulk-active" data-active="0">批量停用</button>' +
      '<button class="btn" data-op="bulk-active" data-active="1">批量启用</button></div>' +
      '<div class="op-grp"><span class="op-l">清理</span>' +
      '<button class="btn ghost" data-op="bulk-del">删除空壳</button>' +
      '<span class="tiny muted">' + D.counts.artifacts + '/' + D.counts.all + ' 有产物 → 删除会被 400 拦下，改用停用</span></div>' +
      '</div><footer class="mod-f muted">未勾选时，批量操作作用于「当前筛选结果」全部账号。</footer></div></section>';
  }

  function typeSection() {
    const c = D.counts;
    return '<section class="mod"><header class="mod-h"><h2>类型与启停</h2><span class="hint">视频 / 图文 · 启用 / 停用</span></header>' +
      '<div class="mod-b"><div class="u-type">' +
      '<div class="tp-row"><span class="tp-l">类型</span>' +
      '<span class="seg"><button class="sg" data-op="filter-type" data-v="video">视频 ' + (c.video || 0) + '</button>' +
      '<button class="sg" data-op="filter-type" data-v="image">图文 ' + (c.image || 0) + '</button></span>' +
      '<span class="tp-n tiny muted">图文账号跳过音频下载与转写，走 enumerate_image.py</span></div>' +
      '<div class="tp-row"><span class="tp-l">状态</span>' +
      '<span class="seg"><button class="sg" data-op="filter-status" data-v="active">启用 ' + (c.active || 0) + '</button>' +
      '<button class="sg" data-op="filter-status" data-v="inactive">停用 ' + (c.inactive || 0) + '</button></span>' +
      '<span class="tp-n tiny muted">停用后被 load_accounts / daily_update 直接过滤</span></div>' +
      '<div class="tp-protect"><span class="dot warn"></span><b>' + (c.artifacts || 0) + ' / ' + (c.all || 0) + ' 有产物</b>' +
      '<span class="tiny muted">删除接口只放行无音频 / 无抓取 / 无台账的空壳；其余一律 400</span></div>' +
      '</div></div></section>';
  }

  function groupsSection() {
    const G = D.groups;
    return '<section class="mod"><header class="mod-h"><h2>分组归属</h2><span class="hint">' + G.length + ' 个分组 · 点行筛选</span></header>' +
      '<div class="mod-b"><div class="u-gbars">' + G.map((g) =>
        '<button class="gb" data-op="filter-group" data-group="' + esc(g.name) + '">' +
        '<span class="gb-n">' + esc(g.name) + '</span>' +
        '<span class="gb-b"><i style="width:' + Math.round(g.accounts / Math.max(1, G[0].accounts) * 100) + '%"></i></span>' +
        '<b class="tnum">' + g.accounts + '</b>' +
        '<span class="tiny muted">' + g.video + '/' + g.image + '</span>' +
        '<span class="gb-s">' + (g.inactive ? '<span class="dot"></span>' + g.inactive : '') + '</span></button>').join('') + '</div>' +
      '<footer class="mod-f">视频/图文分列 · 灰点 = 停用</footer></div></section>';
  }

  function diagSection() {
    const sum = (k) => D.items.reduce((s, a) => s + (Number(a[k]) || 0), 0);
    return '<section class="mod"><header class="mod-h"><h2>数据口径</h2><span class="hint">实时 · /api/accounts</span></header>' +
      '<div class="mod-b"><div class="u-diag2">' +
      '<dl class="dg-kv"><dt>配置来源</dt><dd><code>accounts.json</code> · ' + D.items.length + ' 条</dd>' +
      '<dt>产物统计</dt><dd>GET /api/accounts · ' + (D.at ? D.at.toLocaleTimeString('zh-CN', { hour12: false }) : '—') + '</dd>' +
      '<dt>台账</dt><dd>tiktok_对标.xlsx</dd>' +
      '<dt>写接口</dt><dd>仅 127.0.0.1 可写</dd></dl>' +
      '<div class="dg-sums">' + [['抓取', sum('scrape_items')], ['音频', sum('audio_count')], ['转写', sum('transcript_count')], ['台账', sum('ledger_rows')], ['商品', sum('products_count')]].map(([k, v]) =>
        '<div class="ds"><span>' + k + '</span><b class="tnum">' + int(v) + '</b></div>').join('') + '</div>' +
      '<p class="dg-note">空态：筛选无结果时提示「没有匹配的账号」；读取失败时给重试按钮；删除有产物的账号会返回 400 并在提示条里说明原因。</p>' +
      '</div></div></section>';
  }

  function statusBar() {
    const at = D.at ? D.at.toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    return '<div class="a29-status"><span class="dot ' + (D.error ? 'bad' : 'ok') + '"></span>' +
      '<span>' + (D.error ? '读取失败：' + esc(D.error) : '更新于 ' + at + ' · 共 ' + D.items.length + ' 个账号 · 数据来自 /api/accounts') + '</span>' +
      '<span class="grow"></span>' +
      (D.error ? '<button class="btn ghost" data-op="refresh">重试</button>' : '') +
      '</div>';
  }

  function mount() {
    if (D.loading && !D.items.length) {
      stage.innerHTML = '<div class="pg"><div class="a29-state"><b>正在读取账号清单…</b>/api/accounts</div></div>';
      return;
    }
    if (D.error && !D.items.length) {
      stage.innerHTML = '<div class="pg"><div class="a29-state"><b>账号清单读取失败</b>' + esc(D.error) +
        '<div><button class="btn pri" data-op="refresh">重试</button></div></div></div>';
      return;
    }
    stage.innerHTML = '<div class="pg a29">' +
      '<header class="topbar a29-top">' +
      '<div class="brand"><span class="logo">看</span><div><b>账号看板</b><small>ACCOUNTS · BOARD</small></div></div>' +
      '<a class="backlink" href="/">← 回主工作台</a>' +
      '<div class="tb-stamps"><span class="stamp"><i class="dot ok"></i>本机写接口</span></div>' +
      '<div class="tb-acts">' +
      '<button class="btn ghost" data-op="refresh">刷新</button>' +
      '<button class="btn ghost" data-op="focus-import">⤓ 批量导入</button>' +
      '<button class="btn pri" data-op="create">＋ 新增账号</button></div>' +
      '</header>' +
      statusBar() +
      '<div class="a29-head">' +
      '<div>' + finding() + '</div>' + kpis() + '</div>' +
      filters() + board() + tableSection() +
      '<div class="a29-low">' + detailSection() + formSection() + importSection() + opsSection() + '</div>' +
      '<div class="a29-low2">' + typeSection() + groupsSection() + diagSection() + '</div>' +
      '</div>';
    // 筛选/排序后只换两块，避免整页重绘打断输入
  }

  function finding() {
    const c = D.counts;
    return '<section class="u-find a29-find">' +
      '<div class="eyebrow">账号管理 · ' + (D.at ? D.at.toLocaleString('zh-CN', { hour12: false }) : '') + '</div>' +
      '<h1><b>' + (c.active || 0) + '</b> 个账号在跑，<b>' + (c.image || 0) + '</b> 个走图文链路</h1>' +
      '<p class="u-find-sub">' + (c.inactive || 0) + ' 个停用（账页保留） · ' + (c.video || 0) + ' 个视频 · ' +
      (c.artifacts || 0) + '/' + (c.all || 0) + ' 有产物 · 删除一律被拦</p></section>';
  }

  /* 局部刷新：只重画看板 / 表格 / 详情 / 计数，输入框与下拉保持原样，不打断打字 */
  function refreshLists() {
    const b = $('.a29-board'); if (b) b.outerHTML = board();
    const t = $('.a29-table'); if (t) t.outerHTML = tableSection();
    const d = $('.u-detail'); if (d) d.outerHTML = detailSection();
    const hint = $('[data-slot="hint"]');
    if (hint) hint.textContent = '匹配 ' + D.items.filter(match).length + ' / ' + D.items.length;
    const sel = $('[data-slot="sel"]');
    if (sel) sel.textContent = '已选 ' + st.sel.size + ' / ' + D.items.filter(match).length;
    const gs = $('[data-f="group"]'); if (gs) gs.value = st.group;
    const ss = $('[data-f="status"]'); if (ss) ss.value = st.status;
    const ts = $('[data-f="type"]'); if (ts) ts.value = st.type;
    document.querySelectorAll('[data-op="filter-type"]').forEach((el) => el.classList.toggle('on', el.dataset.v === st.type));
    document.querySelectorAll('[data-op="filter-status"]').forEach((el) => el.classList.toggle('on', el.dataset.v === st.status));
  }

  /* ── 抽屉 ─────────────────────────────────────────────── */
  function drawer() {
    let scrim = $('#scrim'), dr = $('#drawer');
    if (!scrim) {
      scrim = document.createElement('div'); scrim.id = 'scrim'; scrim.className = 'scrim';
      scrim.addEventListener('click', closeDrawer);
      document.body.appendChild(scrim);
    }
    if (!dr) {
      dr = document.createElement('aside'); dr.id = 'drawer'; dr.className = 'drawer';
      dr.setAttribute('role', 'dialog'); dr.setAttribute('aria-modal', 'true');
      document.body.appendChild(dr);
    }
    return { scrim, dr };
  }
  function closeDrawer() {
    const s = $('#scrim'), d = $('#drawer');
    if (s) s.classList.remove('on');
    if (d) d.classList.remove('on');
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

  function openDrawer(title, sub, body) {
    const { scrim, dr } = drawer();
    dr.innerHTML = '<header class="drawer-h"><div><div class="eyebrow">DETAIL</div><h2>' + title + '</h2>' +
      '<span class="tiny muted">' + sub + '</span></div><button class="btn ghost x" data-close>关闭</button></header>' +
      '<div class="drawer-b">' + body + '</div>';
    scrim.classList.add('on'); dr.classList.add('on');
    dr.querySelector('[data-close]').onclick = closeDrawer;
  }

  async function openAccountDetail(name) {
    openDrawer(esc(name), '读取 /api/accounts/' + esc(name) + ' …', '<div class="a29-state">加载中…</div>');
    try {
      const d = await api(API + '/' + encodeURIComponent(name));
      const kv = [
        ['昵称', d.nickname ? esc(d.nickname) : '未填'],
        ['地区 / 语言', regionLabel(d.region) + ' / ' + esc(d.lang || '—')],
        ['类型', d.media_type === 'image' ? '图文账号' : '视频账号'],
        ['配置状态', d.active === false ? '已停用' : '启用中'],
        ['标签', (d.tags || []).length ? d.tags.map((t) => '<span class="tag-mini">' + esc(t) + '</span>').join(' ') : '无'],
        ['备注', d.note ? esc(d.note) : '—'],
        ['抓取 / 音频 / 转写', int(d.scrape_items) + ' / ' + int(d.audio_count) + ' / ' + int(d.transcript_count)],
        ['台账行 / 已覆盖', int(d.ledger_rows) + ' / ' + (d.ledger_covered == null ? '—' : d.ledger_covered)],
        ['商品锚点', int(d.products_count) + ' 条视频'],
        ['落地率', (Number(d.landing) || 0) + '%'],
        ['最近成功', d.last_success_date ? esc(d.last_success_date) : '未采集'],
        ['可编辑字段', (d.editable_fields || []).join(' / ') || '—']
      ];
      const gaps = (d.gaps || []).length
        ? d.gaps.map((g) => '<div class="gap ' + esc(g.level || 'info') + '"><b>' + esc(g.label || '缺口') + '</b> · ' + esc(g.msg || '') + '</div>').join('')
        : '<div class="gap info">没有检出缺口</div>';
      openDrawer(esc(name), esc(d.group_name || '未分组'), 
        '<div class="dr-kv">' + kv.map(([k, v]) => '<span>' + k + '</span><b>' + v + '</b>').join('') + '</div>' +
        '<h3 class="dr-h">缺口</h3>' + gaps +
        '<div class="dr-acts">' +
        '<button class="btn pri" data-op="edit" data-account="' + esc(name) + '">编辑</button>' +
        '<button class="btn" data-op="toggle" data-account="' + esc(name) + '">' + (d.active === false ? '启用' : '停用') + '</button>' +
        '<button class="btn ghost" data-op="del" data-account="' + esc(name) + '">删除</button></div>');
    } catch (e) {
      openDrawer(esc(name), '读取失败', '<div class="a29-state"><b>详情读取失败</b>' + esc(e.message) + '</div>');
    }
  }

  function openSheet(name) {
    const a = D.items.find((x) => x.account === name);
    if (!a) return;
    const rowsHtml = [
      ['抓取条目', int(a.scrape_items), a.scrape_errors ? '失败 ' + a.scrape_errors : '无失败'],
      ['音频', int(a.audio_count), a.audio_dir || '—'],
      ['转写', int(a.transcript_count), '—'],
      ['台账行', int(a.ledger_rows), '对标记录-' + esc(a.account)],
      ['商品挂车', int(a.products_count), '探测覆盖 ' + int(a.products_probed) + ' 条'],
      ['最近成功', a.last_success_date ? esc(a.last_success_date) : '未回写', ''],
      ['产物标记', a.has_artifacts ? '有产物' : '空壳', a.has_artifacts ? 'DELETE 会被 400 拦下，改用停用' : '可以删除']
    ];
    openDrawer(esc(name) + ' · 产物', '统计口径：/api/overview 同源',
      '<table class="tb"><thead><tr><th>产物</th><th class="r">数量</th><th>备注</th></tr></thead><tbody>' +
      rowsHtml.map(([k, v, note]) => '<tr><td>' + k + '</td><td class="r tnum">' + v + '</td><td class="tiny muted">' + esc(note) + '</td></tr>').join('') +
      '</tbody></table>');
  }

  /* ── 写操作 ───────────────────────────────────────────── */
  async function saveForm(mode) {
    if (st.busy) return;
    const box = $('[data-unit="form"]');
    if (!box) return;
    const v = {};
    box.querySelectorAll('[name]').forEach((el) => {
      v[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    });
    const name = String(v.account || '').trim().toLowerCase();
    if (!name) return toast('账号不能为空', 'bad');
    if (!/^[a-z0-9_.]+$/.test(name)) return toast('账号只能用小写字母 / 数字 / 下划线 / 点', 'bad');
    if (name.length > 31) return toast('账号最长 31 个字符', 'bad');
    const tags = String(v.tags || '').split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const payload = {
      nickname: v.nickname || '', region: v.region || '', lang: v.lang || '',
      tags: tags, note: v.note || '', active: !!v.active
    };
    st.busy = true;
    try {
      if (mode === 'edit') {
        payload.media_type = v.media_type || 'video';
        await api(API + '/' + encodeURIComponent(name), { method: 'PATCH', body: JSON.stringify(payload) });
        toast('已保存 ' + name);
        st.focus = name;
        st.form = { mode: 'edit', account: name };
      } else {
        payload.account = name;
        payload.media_type = v.media_type || 'video';
        await api(API, { method: 'POST', body: JSON.stringify(payload) });
        toast('已新增 ' + name);
        st.focus = name;
        st.form = { mode: 'edit', account: name };
      }
      await load(true);
    } catch (e) {
      toast('保存失败：' + e.message, 'bad');
    } finally {
      st.busy = false;
      const btn = $('[data-op="save"]');
      if (btn) btn.disabled = false;
    }
  }

  async function toggleActive(name) {
    const a = D.items.find((x) => x.account === name);
    if (!a) return;
    try {
      await api(API + '/' + encodeURIComponent(name), { method: 'PATCH', body: JSON.stringify({ active: !a.active }) });
      toast(name + (a.active ? ' 已停用' : ' 已启用'));
      await load(true);
    } catch (e) {
      toast('操作失败：' + e.message, 'bad');
    }
  }

  async function delAccount(name) {
    const a = D.items.find((x) => x.account === name);
    if (!a) return;
    if (a.has_artifacts) {
      toast(name + ' 有音频/台账/抓取产物 → 只能停用（接口会返回 400）', 'bad');
      return;
    }
    if (!window.confirm('删除账号「' + name + '」？\n\n只删 accounts.json 里的这一条配置，不删文件、不删台账。')) return;
    try {
      await api(API + '/' + encodeURIComponent(name), { method: 'DELETE' });
      toast('已删除 ' + name);
      if (st.focus === name) st.focus = null;
      if (st.form.account === name) st.form = { mode: 'create', account: null };
      await load(true);
    } catch (e) {
      toast('删除失败：' + e.message, 'bad');
    }
  }

  function parsePaste() {
    const ta = $('[data-slot="paste"]');
    if (!ta) return { lines: 0, fresh: [], dup: [] };
    const raws = ta.value.split('\n').map((s) => s.trim()).filter(Boolean);
    const fresh = [], dup = [];
    raws.forEach((ln) => {
      const cols = ln.split(/[,，\t]/).map((s) => s.trim());
      const name = (cols[0] || '').toLowerCase();
      if (!name) return;
      const row = { name: name, nickname: cols[1] || '', region: cols[2] || '', lang: cols[3] || '' };
      if (D.items.some((a) => a.account === name)) dup.push(row); else fresh.push(row);
    });
    return { lines: raws.length, fresh: fresh, dup: dup };
  }

  function renderImportRes() {
    const r = parsePaste();
    const box = $('[data-slot="import-res"]');
    if (box) {
      box.innerHTML = '<span class="chip">' + r.lines + ' 行</span>' +
        '<span class="chip ' + (r.dup.length ? 'warn' : '') + '">已存在 ' + r.dup.length + '</span>' +
        '<span class="chip ' + (r.fresh.length ? 'ok' : '') + '">可新增 ' + r.fresh.length + '</span>' +
        (r.fresh.length ? '<span class="tiny muted">点「导入」写入</span>' : '<span class="tiny muted">没有可新增的行</span>');
    }
    return r;
  }

  async function runImport() {
    const r = parsePaste();
    if (!r.fresh.length) return toast('没有可新增的行（' + r.dup.length + ' 行主键重复）', 'bad');
    st.busy = true;
    let ok = 0; const errs = [];
    for (const row of r.fresh) {
      try {
        await api(API, { method: 'POST', body: JSON.stringify({
          account: row.name, nickname: row.nickname, region: row.region, lang: row.lang, media_type: 'video', active: true
        }) });
        ok++;
      } catch (e) {
        errs.push(row.name + '：' + e.message);
      }
    }
    st.busy = false;
    const ta = $('[data-slot="paste"]'); if (ta) ta.value = '';
    toast('导入 ' + ok + ' 个' + (errs.length ? ' · 失败 ' + errs.length + '（' + errs[0] + '）' : ''), errs.length ? 'bad' : '');
    await load(true);
  }

  async function bulkType(mediaType) {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    if (!tgt.length) return toast('没有可操作的账号', 'bad');
    try {
      const r = await api(API + '/media-type', { method: 'POST', body: JSON.stringify({
        accounts: tgt.map((a) => a.account), media_type: mediaType
      }) });
      toast('改为' + (mediaType === 'image' ? '图文' : '视频') + '：' + JSON.stringify(r).slice(0, 120));
      await load(true);
    } catch (e) { toast('操作失败：' + e.message, 'bad'); }
  }

  async function bulkActive(on) {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    if (!tgt.length) return toast('没有可操作的账号', 'bad');
    if (!window.confirm((on ? '启用' : '停用') + ' ' + tgt.length + ' 个账号？')) return;
    st.busy = true;
    let ok = 0; const errs = [];
    for (const a of tgt) {
      try { await api(API + '/' + encodeURIComponent(a.account), { method: 'PATCH', body: JSON.stringify({ active: on }) }); ok++; }
      catch (e) { errs.push(a.account); }
    }
    st.busy = false;
    toast((on ? '启用 ' : '停用 ') + ok + ' 个' + (errs.length ? ' · 失败 ' + errs.length : ''), errs.length ? 'bad' : '');
    await load(true);
  }

  async function bulkDel() {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    const free = tgt.filter((a) => !a.has_artifacts);
    if (!free.length) return toast('选中范围内没有空壳账号（' + tgt.length + ' 个都有产物）', 'bad');
    if (!window.confirm('删除 ' + free.length + ' 个空壳账号？有产物的 ' + (tgt.length - free.length) + ' 个会被跳过（接口 400）。')) return;
    st.busy = true;
    let ok = 0;
    for (const a of free) {
      try { await api(API + '/' + encodeURIComponent(a.account), { method: 'DELETE' }); ok++; } catch (e) { /* 跳过 */ }
    }
    st.busy = false;
    toast('删除空壳 ' + ok + ' 个 · 跳过 ' + (tgt.length - free.length) + ' 个有产物');
    await load(true);
  }

  /* ── 事件 ─────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const opEl = e.target.closest('[data-op]');
    const op = opEl && opEl.dataset.op;

    const th = e.target.closest('th[data-sort]');
    if (th) {
      const k = th.dataset.sort;
      st.sort = { k: k, d: st.sort.k === k ? -st.sort.d : (['account', 'group_name', 'regionLabel', 'media_type'].includes(k) ? 1 : -1) };
      refreshLists();
      return;
    }

    if (op === 'refresh') { load(true); return; }
    if (op === 'clear') {
      st.q = ''; st.status = ''; st.type = ''; st.group = '';
      mount();
      return;
    }
    if (op === 'create') {
      st.form = { mode: 'create', account: null };
      const box = $('[data-unit="form"]'); if (box) box.outerHTML = formSection();
      toast('表单已切到「新增账号」');
      return;
    }
    if (op === 'cancel') {
      st.form = { mode: 'create', account: null };
      const box = $('[data-unit="form"]'); if (box) box.outerHTML = formSection();
      return;
    }
    if (op === 'save') { saveForm(opEl.dataset.mode || st.form.mode); return; }
    if (op === 'edit') {
      st.form = { mode: 'edit', account: opEl.dataset.account };
      st.focus = opEl.dataset.account;
      closeDrawer();
      refreshLists();
      const box = $('[data-unit="form"]'); if (box) box.outerHTML = formSection();
      toast('正在编辑 ' + opEl.dataset.account);
      return;
    }
    if (op === 'toggle') { toggleActive(opEl.dataset.account); return; }
    if (op === 'del') { delAccount(opEl.dataset.account); return; }
    if (op === 'sheet') { openSheet(opEl.dataset.account); return; }
    if (op === 'detail') { closeDrawer(); openAccountDetail(opEl.dataset.account); return; }
    if (op === 'focus-import') {
      const ta = $('[data-slot="paste"]');
      if (ta) { ta.scrollIntoView({ block: 'center', behavior: 'smooth' }); ta.focus(); }
      return;
    }
    if (op === 'validate') { const r = renderImportRes(); toast('校验完成：可新增 ' + r.fresh.length + '，重复 ' + r.dup.length); return; }
    if (op === 'import') { runImport(); return; }
    if (op === 'selall') {
      const cb = $('[data-op="selall"]');
      if (cb && cb.checked) D.items.filter(match).forEach((a) => st.sel.add(a.account));
      else st.sel.clear();
      const s = $('[data-slot="sel"]');
      if (s) s.textContent = '已选 ' + st.sel.size + ' / ' + D.items.filter(match).length;
      return;
    }
    if (op === 'bulk-type') { bulkType(opEl.dataset.type); return; }
    if (op === 'bulk-active') { bulkActive(opEl.dataset.active === '1'); return; }
    if (op === 'bulk-del') { bulkDel(); return; }
    if (op === 'filter-group') {
      st.group = st.group === opEl.dataset.group ? '' : opEl.dataset.group;
      refreshLists();
      return;
    }
    if (op === 'filter-type') {
      st.type = st.type === opEl.dataset.v ? '' : opEl.dataset.v;
      refreshLists();
      return;
    }
    if (op === 'filter-status') {
      st.status = st.status === opEl.dataset.v ? '' : opEl.dataset.v;
      refreshLists();
      return;
    }
    if (opEl && opEl.dataset.pick) {
      const key = opEl.dataset.pick;
      opEl.parentElement.querySelectorAll('.sg').forEach((b) => b.classList.toggle('on', b === opEl));
      const hidden = $('[data-unit="form"] input[name="' + key + '"]');
      if (hidden) hidden.value = opEl.dataset.v;
      return;
    }

    const drillEl = e.target.closest('[data-drill]');
    if (drillEl) {
      const key = drillEl.dataset.drill;
      if (key && key.indexOf('acct:') === 0) {
        const name = key.slice(5);
        st.focus = name;
        refreshLists();
        openAccountDetail(name);
        return;
      }
      const map = { all: '全部账号', active: '启用中的账号', image: '图文账号', artifacts: '有产物的账号' };
      if (map[key]) {
        const list = key === 'all' ? D.items
          : key === 'active' ? D.items.filter((a) => a.active)
            : key === 'image' ? D.items.filter((a) => a.media_type === 'image')
              : D.items.filter((a) => a.has_artifacts);
        openDrawer(map[key], list.length + ' 个',
          '<table class="tb"><thead><tr><th>账号</th><th>分组</th><th>类型</th><th class="r">抓取</th><th class="r">台账</th></tr></thead><tbody>' +
          list.map((a) => '<tr><td>' + esc(a.account) + '</td><td class="tiny">' + esc(a.group_name || '未分组') + '</td>' +
            '<td>' + (a.media_type === 'image' ? '图文' : '视频') + '</td><td class="r tnum">' + int(a.scrape_items) + '</td>' +
            '<td class="r tnum">' + int(a.ledger_rows) + '</td></tr>').join('') + '</tbody></table>');
        return;
      }
    }
  });

  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-slot="q"]')) {
      st.q = e.target.value.trim().toLowerCase();
      refreshLists();
    }
    if (e.target.matches('[data-slot="paste"]')) renderImportRes();
  });

  document.addEventListener('change', (e) => {
    const f = e.target.dataset && e.target.dataset.f;
    if (f) {
      if (f === 'status') st.status = e.target.value;
      if (f === 'type') st.type = e.target.value;
      if (f === 'group') st.group = e.target.value;
      refreshLists();
    }
    if (e.target.matches('[name="active"]')) {
      const sp = e.target.parentElement && e.target.parentElement.querySelector('span');
      if (sp) sp.textContent = e.target.checked ? '启用' : '停用';
    }
  });

  /* 首屏 */
  mount();
  load();
})();
