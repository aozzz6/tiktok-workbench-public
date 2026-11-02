/* ============================================================
   accounts_s236.js · 账号管理 · S236（A29 看板 / P18 奶咖）
   只接管 #view-accounts 这一屏的渲染；数据层复用 app.js：
     · 数据：app.js 的 loadAccounts() → acctCache（15s 缓存）
     · 写入：api() / toggleAccountActive() / deleteAccount() / refreshAccountsEverywhere()
     · 台账：openAccountSheet()
   对外只暴露 window.ACC236 = { render, loading, error }
   ============================================================ */
(function () {
  'use strict';

  const ROOT_SEL = '.acc236';
  const REGION = {
    '': '未标注', us: '美国', uk: '英国', eu: '欧洲', de: '德国', fr: '法国',
    es: '西语区', it: '意区', cn: '中国大陆', 'cn-hk': '中国香港', 'cn-tw': '中国台湾',
    jp: '日本', kr: '韩国'
  };
  const LANGS = ['en', 'de', 'fr', 'es', 'it', 'zh', 'jp', 'kr'];

  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const int = (n) => Number(n || 0).toLocaleString('zh-CN');
  const sevOf = (a) => (a.severity === 'error' ? 'bad' : a.severity === 'warn' ? 'warn' : 'ok');
  const sevLabel = (s) => (s === 'error' ? '异常' : s === 'warn' ? '缺口' : '正常');
  const regionLabel = (c) => REGION[c || ''] || c || '未标注';
  const typeChip = (a) => (a.media_type === 'image' ? '<span class="chip img">图文</span>' : '<span class="chip vid">视频</span>');
  const activeChip = (a) => (a.active ? '<span class="chip ok">启用</span>' : '<span class="chip off">停用</span>');
  const tagsHtml = (a) => (a.tags || []).slice(0, 2).map((t) => '<span class="tiny muted">' + esc(t) + '</span>').join(' ');

  const st = {
    q: '', status: '', type: '', group: '',
    sort: { k: '', d: 1 },
    sel: new Set(),
    focus: null,
    form: { mode: 'create', account: null },
    busy: false,
    loading: false,
    err: null
  };
  const D = { items: [], counts: {}, groups: [], at: null };

  const say = (msg) => { try { if (typeof toast === 'function') toast(msg); } catch (e) { /* 老版本无 toast */ } };

  /* ── 数据形状 ─────────────────────────────────────────── */
  function absorb(d) {
    if (!d) return;
    D.items = (d.accounts || []).map((a) => Object.assign({}, a, {
      regionLabel: regionLabel(a.region),
      has_artifacts: !!a.has_artifacts,
      landing: Number(a.landing) || 0
    }));
    D.counts = d.counts || {};
    D.groups = groupBy(D.items);
    D.at = new Date();
    st.err = null;
    if (st.focus && !D.items.some((a) => a.account === st.focus)) st.focus = null;
  }

  function groupBy(items) {
    const map = new Map();
    items.forEach((a) => {
      const key = a.group_name || '未分组';
      if (!map.has(key)) map.set(key, { name: key, accounts: 0, active: 0, inactive: 0, video: 0, image: 0, sum: 0 });
      const g = map.get(key);
      g.accounts++;
      if (a.active) g.active++; else g.inactive++;
      if (a.media_type === 'image') g.image++; else g.video++;
      g.sum += a.landing;
    });
    return [...map.values()]
      .map((g) => Object.assign({}, g, { landing: g.accounts ? Math.round(g.sum / g.accounts) : 0 }))
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

  /* ── 片段 ─────────────────────────────────────────────── */
  function head() {
    return '<header class="acc236-head">' +
      '<div class="acc236-title"><h1>账号管理</h1>' +
      '<p class="acc236-sub">账号即 accounts.json 的一条记录；写操作仅限本机（127.0.0.1）' +
      (D.at ? ' · 数据更新于 ' + D.at.toLocaleTimeString('zh-CN', { hour12: false }) : '') + '</p></div>' +
      '<div class="acc236-acts">' +
      '<button class="btn" data-acc236="refresh">刷新</button>' +
      '<button class="btn" data-acc236="focus-import">⤓ 批量导入</button>' +
      '<button class="btn pri" data-acc236="create">＋ 新增账号</button></div></header>';
  }

  function finding() {
    const c = D.counts;
    return '<section class="u-find">' +
      '<div class="eyebrow">看板 · ' + (D.at ? D.at.toLocaleString('zh-CN', { hour12: false }) : '') + '</div>' +
      '<h1><b>' + (c.active || 0) + '</b> 个账号在跑，<b>' + (c.image || 0) + '</b> 个走图文链路</h1>' +
      '<p class="u-find-sub">' + (c.inactive || 0) + ' 个停用（账页保留） · ' + (c.video || 0) + ' 个视频 · ' +
      (c.artifacts || 0) + '/' + (c.all || 0) + ' 有产物 · 删除一律被拦</p></section>';
  }

  function kpis() {
    const c = D.counts;
    const list = [
      { k: 'all', l: '账号总数', v: c.all, u: '个', cap: 'accounts.json 记录数' },
      { k: 'active', l: '启用中', v: c.active, u: '个', cap: '参与流水线 · ' + (c.inactive || 0) + ' 个停用' },
      { k: 'image', l: '图文账号', v: c.image, u: '个', cap: '跳过音频与转写' },
      { k: 'artifacts', l: '有产物', v: c.artifacts, u: '个', cap: '有台账/音频 → 不可直接删' }
    ];
    return '<div class="u-kpis">' + list.map((x) =>
      '<button type="button" class="k' + (x.k === 'artifacts' && c.artifacts === c.all ? ' is-warn' : '') + '" data-acc236-drill="' + x.k + '">' +
      '<div class="k-l">' + x.l + '<span class="arw">⌄</span></div>' +
      '<div class="k-v"><b class="tnum">' + int(x.v) + '</b><span class="u">' + x.u + '</span></div>' +
      '<div class="k-c">' + esc(x.cap) + '</div></button>').join('') + '</div>';
  }

  function filters() {
    const opts = D.groups.map((g) => '<option value="' + esc(g.name) + '"' + (st.group === g.name ? ' selected' : '') + '>' +
      esc(g.name) + '（' + g.accounts + '）</option>').join('');
    return '<section class="u-filters">' +
      '<div class="fs-q"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>' +
      '<input type="search" data-acc236="q" value="' + esc(st.q) + '" placeholder="搜索账号 / 昵称 / 标签 / 分组" aria-label="搜索账号"></div>' +
      '<label class="fs"><span class="fs-l">状态</span><select data-acc236-f="status">' +
      '<option value="">全部状态</option><option value="active"' + (st.status === 'active' ? ' selected' : '') + '>启用中</option>' +
      '<option value="inactive"' + (st.status === 'inactive' ? ' selected' : '') + '>已停用</option></select></label>' +
      '<label class="fs"><span class="fs-l">类型</span><select data-acc236-f="type">' +
      '<option value="">全部类型</option><option value="video"' + (st.type === 'video' ? ' selected' : '') + '>视频账号</option>' +
      '<option value="image"' + (st.type === 'image' ? ' selected' : '') + '>图文账号</option></select></label>' +
      '<label class="fs"><span class="fs-l">分组</span><select data-acc236-f="group"><option value="">全部分组</option>' + opts + '</select></label>' +
      '<span class="fs-hint" data-acc236="hint">匹配 ' + D.items.filter(match).length + ' / ' + D.items.length + '</span>' +
      '<button class="btn ghost" data-acc236="clear">清空</button></section>';
  }

  function card(a) {
    const gaps = (a.gaps || []).filter((g) => g.level !== 'info');
    return '<div class="a29-card' + (a.active ? '' : ' off') + '" data-account="' + esc(a.account) + '" data-acc236-drill="acct:' + esc(a.account) + '" tabindex="0" role="button">' +
      '<div class="a29-c-h"><span class="dot ' + sevOf(a) + '"></span><b>' + esc(a.account) + '</b>' + typeChip(a) + '</div>' +
      '<div class="a29-c-g">' + esc(a.group_name || '未分组') + ' · ' + esc(a.regionLabel) + ' · ' + esc(a.lang) + '</div>' +
      '<div class="a29-c-n"><span>抓取 <b class="tnum">' + int(a.scrape_items) + '</b></span>' +
      '<span>台账 <b class="tnum">' + int(a.ledger_rows) + '</b></span>' +
      '<span>落地 <b class="tnum">' + a.landing + '%</b></span></div>' +
      '<div class="a29-c-f"><span class="a29-ops">' +
      '<button class="bl" data-acc236="sheet" data-account="' + esc(a.account) + '">表格</button>' +
      '<button class="bl" data-acc236="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="bl" data-acc236="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="bl danger" data-acc236="del" data-account="' + esc(a.account) + '">删除</button></span>' +
      (gaps.length ? '<span class="chip warn">' + esc(gaps[0].label) + '</span>'
        : (a.severity !== 'ok' ? '<span class="chip ' + sevOf(a) + '">' + sevLabel(a.severity) + '</span>' : '')) +
      '</div></div>';
  }

  function board() {
    const low = (a) => a.active && a.landing < 90;
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

  function tableRows() {
    const list = sorted(D.items.filter(match));
    if (!list.length) return '<div class="acc236-empty"><b>没有匹配的账号</b>换个关键词，或点「清空」重置筛选</div>';
    return list.map((a) => '<tr data-account="' + esc(a.account) + '" data-acc236-drill="acct:' + esc(a.account) + '"' + (a.active ? '' : ' class="off"') + '>' +
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
      '<i class="tnum">' + a.landing + '%</i></td>' +
      '<td class="c-ops"><span class="rowops">' +
      '<button class="bl" data-acc236="sheet" data-account="' + esc(a.account) + '">表格</button>' +
      '<button class="bl" data-acc236="detail" data-account="' + esc(a.account) + '">详情</button>' +
      '<button class="bl" data-acc236="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="bl" data-acc236="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="bl danger" data-acc236="del" data-account="' + esc(a.account) + '">删除</button></span></td></tr>').join('');
  }

  function tableSection() {
    const list = sorted(D.items.filter(match));
    return '<section class="mod a29-table">' +
      '<header class="mod-h"><h2>核对视图</h2><span class="hint">11 列全字段 · 点表头排序 · 表内滚动</span>' +
      '<div class="mod-a"><button class="btn ghost" data-acc236="clear">清空筛选</button></div></header>' +
      '<div class="mod-b"><div class="acc236-scroll"><table class="acct-tbl"><thead><tr>' +
      COLS.map((c) => '<th class="' + (c.r ? 'r ' : '') + (c.k === 'ops' ? 'c-ops' : '') + '"' + (c.k !== 'ops' ? ' data-acc236-sort="' + c.k + '"' : '') + '>' +
        c.t + (c.k !== 'ops' ? '<i class="srt">' + (st.sort.k === c.k ? (st.sort.d > 0 ? '↑' : '↓') : '↕') + '</i>' : '') + '</th>').join('') +
      '</tr></thead><tbody>' + tableRows() + '</tbody></table></div>' +
      '<div class="tbl-foot"><span>' + list.length + ' / ' + D.items.length + ' 行</span>' +
      '<span class="muted">本页 ' + list.length + ' 行 · 其中 ' + list.filter((a) => a.has_artifacts).length + ' 个有产物（删除被拦）</span></div></div></section>';
  }

  function detailSection() {
    const a = D.items.find((x) => x.account === st.focus) || null;
    if (!a) {
      return '<section class="u-detail"><div class="acc236-empty"><b>当前未选中账号</b>点左侧卡片或表格行，选一个账号看详情</div></section>';
    }
    const nums = [['抓取', a.scrape_items], ['音频', a.audio_count], ['转写', a.transcript_count],
      ['台账', a.ledger_rows], ['商品', a.products_count], ['落地', a.landing + '%']];
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
      '<button class="btn pri" data-acc236="edit" data-account="' + esc(a.account) + '">编辑</button>' +
      '<button class="btn" data-acc236="toggle" data-account="' + esc(a.account) + '">' + (a.active ? '停用' : '启用') + '</button>' +
      '<button class="btn ghost" data-acc236="detail" data-account="' + esc(a.account) + '">看缺口与产物</button>' +
      '<button class="btn ghost" data-acc236="del" data-account="' + esc(a.account) + '">删除</button></div></section>';
  }

  function formSection() {
    /* 注意：这里不修改 st.form —— 缓存命中时会用旧数据先渲染一次，
       若在这里把 st.form 降级成 create，会把刚保存的「编辑态」冲掉 */
    const want = st.form.mode === 'edit' && !!st.form.account;
    const a = want ? D.items.find((x) => x.account === st.form.account) : null;
    const cur = a || null;
    const val = (k, d) => (cur ? cur[k] : d);
    return '<section class="mod" data-acc236-form><header class="mod-h"><h2>' + (cur ? '编辑 · ' + esc(cur.account) : '新增账号') + '</h2>' +
      '<span class="hint">主键不可改 · 8 个可编辑字段</span></header>' +
      '<div class="mod-b"><div class="u-form">' +
      '<div class="uf-grid">' +
      '<label class="ff ro"><span class="ff-l">账号<i class="req">*</i></span>' +
      '<input type="text" name="account" value="' + esc(cur ? cur.account : '') + '"' + (cur ? ' readonly' : '') + ' placeholder="小写字母/数字/下划线"></label>' +
      '<label class="ff"><span class="ff-l">昵称</span><input type="text" name="nickname" value="' + esc(val('nickname', '')) + '"></label>' +
      '<label class="ff"><span class="ff-l">地区</span><select name="region">' +
      Object.keys(REGION).map((c) => '<option value="' + c + '"' + (String(val('region', '')).toLowerCase() === c ? ' selected' : '') + '>' + (c || '未标注') + '</option>').join('') +
      '</select></label>' +
      '<label class="ff"><span class="ff-l">语言</span><select name="lang">' +
      LANGS.map((l) => '<option value="' + l + '"' + (val('lang', 'en') === l ? ' selected' : '') + '>' + l + '</option>').join('') +
      '</select></label>' +
      '<label class="ff"><span class="ff-l">类型</span><span class="seg" role="group">' +
      '<button type="button" class="sg' + (val('media_type', 'video') !== 'image' ? ' on' : '') + '" data-acc236-pick="media_type" data-v="video">视频</button>' +
      '<button type="button" class="sg' + (val('media_type', 'video') === 'image' ? ' on' : '') + '" data-acc236-pick="media_type" data-v="image">图文</button></span>' +
      '<input type="hidden" name="media_type" value="' + esc(val('media_type', 'video')) + '"></label>' +
      '<label class="ff"><span class="ff-l">标签</span><input type="text" name="tags" value="' + esc(((cur && cur.tags) || []).join(',')) + '" placeholder="逗号分隔"></label>' +
      '<label class="ff"><span class="ff-l">备注</span><input type="text" name="note" value="' + esc(val('note', '')) + '"></label>' +
      '<label class="ff"><span class="ff-l">启用</span><span class="sw"><input type="checkbox" name="active"' + ((cur ? !!cur.active : true) ? ' checked' : '') + '><i></i><span>' + ((cur ? !!cur.active : true) ? '启用' : '停用') + '</span></span></label>' +
      '</div>' +
      '<div class="uf-acts"><button class="btn pri" data-acc236="save" data-mode="' + (cur ? 'edit' : 'create') + '"' + (st.busy ? ' disabled' : '') + '>' +
      (cur ? '保存修改' : '创建账号') + '</button>' +
      '<button class="btn ghost" data-acc236="cancel">' + (cur ? '退出编辑' : '重置') + '</button>' +
      '<span class="tiny muted">写入 accounts.json 后立即刷新清单</span></div>' +
      '</div></div></section>';
  }

  function importSection() {
    return '<section class="mod"><header class="mod-h"><h2>批量导入</h2><span class="hint">粘贴 → 校验 → 写入</span></header>' +
      '<div class="mod-b"><div class="u-imp">' +
      '<ol class="imp-steps"><li class="on">粘贴清单</li><li>校验重复</li><li>写入 accounts.json</li></ol>' +
      '<textarea class="imp-ta" data-acc236="paste" spellcheck="false" placeholder="每行一个：账号,昵称,地区,语言&#10;例如：newaccount,小王,de,de"></textarea>' +
      '<div class="imp-hint">每行 <code>账号,昵称,地区,语言</code> · 空行忽略 · 主键重复会拦下</div>' +
      '<div class="imp-res" data-acc236="import-res"><span class="chip">0 行</span><span class="tiny muted">粘贴后点「校验」</span></div>' +
      '<div class="imp-acts"><button class="btn" data-acc236="validate">校验</button>' +
      '<button class="btn pri" data-acc236="import"' + (st.busy ? ' disabled' : '') + '>导入</button></div>' +
      '</div></div></section>';
  }

  function opsSection() {
    const total = D.items.filter(match).length;
    return '<section class="mod"><header class="mod-h"><h2>批量操作</h2><span class="hint">本机写接口 · 127.0.0.1</span>' +
      '<div class="mod-a"><span class="tiny muted" data-acc236="sel">已选 ' + st.sel.size + ' / ' + total + '</span></div></header>' +
      '<div class="mod-b"><div class="u-ops">' +
      '<label class="op-all"><input type="checkbox" data-acc236="selall"> 全选当前筛选结果（' + total + ' 个）</label>' +
      '<div class="op-grp"><span class="op-l">类型</span>' +
      '<button class="btn" data-acc236="bulk-type" data-type="image">改为图文</button>' +
      '<button class="btn" data-acc236="bulk-type" data-type="video">改为视频</button></div>' +
      '<div class="op-grp"><span class="op-l">状态</span>' +
      '<button class="btn" data-acc236="bulk-active" data-active="0">批量停用</button>' +
      '<button class="btn" data-acc236="bulk-active" data-active="1">批量启用</button></div>' +
      '<div class="op-grp"><span class="op-l">清理</span>' +
      '<button class="btn ghost" data-acc236="bulk-del">删除空壳</button>' +
      '<span class="tiny muted">' + (D.counts.artifacts || 0) + '/' + (D.counts.all || 0) + ' 有产物 → 删除会被 400 拦下，改用停用</span></div>' +
      '</div><footer class="mod-f">未勾选时，批量操作作用于「当前筛选结果」全部账号。</footer></div></section>';
  }

  function typeSection() {
    const c = D.counts;
    return '<section class="mod"><header class="mod-h"><h2>类型与启停</h2><span class="hint">视频 / 图文 · 启用 / 停用</span></header>' +
      '<div class="mod-b"><div class="u-type">' +
      '<div class="tp-row"><span class="tp-l">类型</span>' +
      '<span class="seg"><button class="sg' + (st.type === 'video' ? ' on' : '') + '" data-acc236="filter-type" data-v="video">视频 ' + (c.video || 0) + '</button>' +
      '<button class="sg' + (st.type === 'image' ? ' on' : '') + '" data-acc236="filter-type" data-v="image">图文 ' + (c.image || 0) + '</button></span>' +
      '<span class="tp-n tiny muted">图文账号跳过音频下载与转写，走 enumerate_image.py</span></div>' +
      '<div class="tp-row"><span class="tp-l">状态</span>' +
      '<span class="seg"><button class="sg' + (st.status === 'active' ? ' on' : '') + '" data-acc236="filter-status" data-v="active">启用 ' + (c.active || 0) + '</button>' +
      '<button class="sg' + (st.status === 'inactive' ? ' on' : '') + '" data-acc236="filter-status" data-v="inactive">停用 ' + (c.inactive || 0) + '</button></span>' +
      '<span class="tp-n tiny muted">停用后被 load_accounts / daily_update 直接过滤</span></div>' +
      '<div class="tp-protect"><span class="dot warn"></span><b>' + (c.artifacts || 0) + ' / ' + (c.all || 0) + ' 有产物</b>' +
      '<span class="tiny muted">删除接口只放行无音频 / 无抓取 / 无台账的空壳；其余一律 400</span></div>' +
      '</div></div></section>';
  }

  function groupsSection() {
    const G = D.groups;
    return '<section class="mod"><header class="mod-h"><h2>分组归属</h2><span class="hint">' + G.length + ' 个分组 · 点行筛选</span></header>' +
      '<div class="mod-b"><div class="u-gbars">' + G.map((g) =>
        '<button class="gb" data-acc236="filter-group" data-group="' + esc(g.name) + '">' +
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
      '<p class="dg-note">空态：筛选无结果时提示「没有匹配的账号」；读取失败时给重试按钮；删除有产物的账号会返回 400 并说明原因。</p>' +
      '</div></div></section>';
  }

  /* ── 入场动效 ────────────────────────────────────────
     只在「整屏渲染」时播放；筛选/排序等局部刷新不再重播
     系统开「减少动态效果」时直接显示，不做位移 */
  let revealIO = null;
  const REDUCE = '(prefers-reduced-motion: reduce)';
  const RV_SEL = '.acc236-head, .u-find, .u-kpis .k, .u-filters, .a29-col, .a29-table, .acc236-dock > *, .acc236-dock2 > *';

  function reveal(animate) {
    const root = $(ROOT_SEL);
    if (!root) return;
    const els = Array.from(root.querySelectorAll(RV_SEL));
    const calm = !animate || (window.matchMedia && window.matchMedia(REDUCE).matches);
    if (revealIO) { revealIO.disconnect(); revealIO = null; }
    if (calm) {
      els.forEach((el) => { el.removeAttribute('data-rv'); el.classList.add('in'); el.style.removeProperty('--d'); });
      return;
    }
    els.forEach((el, i) => {
      el.setAttribute('data-rv', '');
      el.classList.remove('in');
      el.style.setProperty('--d', Math.min(300, i * 32) + 'ms');
    });
    if (!('IntersectionObserver' in window)) {
      els.forEach((el) => el.classList.add('in'));
      return;
    }
    revealIO = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        en.target.classList.add('in');
        revealIO.unobserve(en.target);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.01 });
    els.forEach((el) => revealIO.observe(el));
  }

  /* ── 整屏渲染 ─────────────────────────────────────────── */
  function body() {
    return head() + finding() + kpis() + filters() + board() + tableSection() +
      '<div class="acc236-dock">' + detailSection() + formSection() + importSection() + opsSection() + '</div>' +
      '<div class="acc236-dock2">' + typeSection() + groupsSection() + diagSection() + '</div>';
  }

  let firstPaint = true;

  function render(payload) {
    if (payload) absorb(payload);
    const root = $(ROOT_SEL);
    if (!root) return;
    if (st.err) {
      root.innerHTML = head() + '<div class="acc236-empty"><b>账号清单读取失败</b>' + esc(st.err) +
        '<div><button class="btn pri" data-acc236="refresh">重试</button></div></div>';
      return;
    }
    if (!D.items.length && st.loading) {
      root.innerHTML = head() + '<div class="acc236-empty"><b>正在读取账号清单…</b>GET /api/accounts</div>';
      return;
    }
    const animate = firstPaint || st.animateNext;
    firstPaint = false;
    st.animateNext = false;
    root.innerHTML = body();
    reveal(animate);
  }

  /* 从别的视图切回账号管理时，重新播放入场（写操作后的刷新不播） */
  (function watchView() {
    const sec = document.getElementById('view-accounts');
    if (!sec || !('MutationObserver' in window)) return;
    new MutationObserver(() => {
      if (sec.classList.contains('active')) st.animateNext = true;
    }).observe(sec, { attributes: true, attributeFilter: ['class'] });
  })();

  /* 局部刷新：保留输入焦点 */
  function refresh() {
    const root = $(ROOT_SEL);
    if (!root) return;
    const set = (sel, html) => { const el = $(sel, root); if (el) el.outerHTML = html; };
    set('.a29-board', board());
    set('.a29-table', tableSection());
    set('.acc236-dock > .u-detail', detailSection());
    set('[data-acc236-form]', formSection());
    const hint = $('[data-acc236="hint"]', root);
    if (hint) hint.textContent = '匹配 ' + D.items.filter(match).length + ' / ' + D.items.length;
    const sel = $('[data-acc236="sel"]', root);
    if (sel) sel.textContent = '已选 ' + st.sel.size + ' / ' + D.items.filter(match).length;
    const gs = $('[data-acc236-f="group"]', root); if (gs) gs.value = st.group;
    const ss = $('[data-acc236-f="status"]', root); if (ss) ss.value = st.status;
    const ts = $('[data-acc236-f="type"]', root); if (ts) ts.value = st.type;
    reveal(false);   /* 局部刷新不重播入场 */
  }

  /* ── 抽屉 ─────────────────────────────────────────────── */
  function hosts() {
    const root = $(ROOT_SEL);
    if (!root) return {};
    let scrim = $('.acc236-scrim', root), dr = $('.acc236-drawer', root);
    if (!scrim) { scrim = document.createElement('div'); scrim.className = 'scrim acc236-scrim'; root.appendChild(scrim); }
    if (!dr) { dr = document.createElement('aside'); dr.className = 'drawer acc236-drawer'; dr.setAttribute('role', 'dialog'); root.appendChild(dr); }
    return { scrim: scrim, dr: dr };
  }
  function closeDrawer() {
    const root = $(ROOT_SEL); if (!root) return;
    const s = $('.acc236-scrim', root), d = $('.acc236-drawer', root);
    if (s) s.classList.remove('on');
    if (d) d.classList.remove('on');
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

  function openDrawer(title, sub, bodyHtml) {
    const h = hosts();
    if (!h.dr) return;
    h.dr.innerHTML = '<header class="drawer-h"><div><div class="eyebrow">DETAIL</div><h2>' + title + '</h2>' +
      '<span class="tiny muted">' + sub + '</span></div><button class="btn ghost x" data-acc236="close">关闭</button></header>' +
      '<div class="drawer-b">' + bodyHtml + '</div>';
    h.scrim.classList.add('on');
    h.dr.classList.add('on');
  }

  async function openDetail(name) {
    openDrawer(esc(name), '读取 /api/accounts/' + esc(name) + ' …', '<div class="acc236-empty">加载中…</div>');
    try {
      const d = await api('/api/accounts/' + encodeURIComponent(name));
      const kv = [
        ['昵称', d.nickname ? esc(d.nickname) : '未填'],
        ['地区 / 语言', regionLabel(d.region) + ' / ' + esc(d.lang || '—')],
        ['类型', d.media_type === 'image' ? '图文账号' : '视频账号'],
        ['配置状态', d.active === false ? '已停用' : '启用中'],
        ['标签', (d.tags || []).length ? d.tags.map((t) => esc(t)).join('、') : '无'],
        ['备注', d.note ? esc(d.note) : '—'],
        ['抓取 / 音频 / 转写', int(d.scrape_items) + ' / ' + int(d.audio_count) + ' / ' + int(d.transcript_count)],
        ['台账行 / 已覆盖', int(d.ledger_rows) + ' / ' + (d.ledger_covered == null ? '—' : d.ledger_covered)],
        ['商品锚点', int(d.products_count) + ' 条视频'],
        ['落地率', int(d.landing) + '%'],
        ['最近成功', d.last_success_date ? esc(d.last_success_date) : '未采集']
      ];
      const gaps = (d.gaps || []).length
        ? d.gaps.map((g) => '<div class="gap ' + esc(g.level || 'info') + '"><b>' + esc(g.label || '缺口') + '</b> · ' + esc(g.msg || '') + '</div>').join('')
        : '<div class="gap info">没有检出缺口</div>';
      openDrawer(esc(name), esc(d.group_name || '未分组'),
        '<div class="dr-kv">' + kv.map(([k, v]) => '<span>' + k + '</span><b>' + v + '</b>').join('') + '</div>' +
        '<h3 class="dr-h">缺口</h3>' + gaps +
        '<div class="dr-acts">' +
        '<button class="btn pri" data-acc236="edit" data-account="' + esc(name) + '">编辑</button>' +
        '<button class="btn" data-acc236="toggle" data-account="' + esc(name) + '">' + (d.active === false ? '启用' : '停用') + '</button>' +
        '<button class="btn ghost" data-acc236="sheet" data-account="' + esc(name) + '">产物明细</button>' +
        '<button class="btn ghost" data-acc236="open-sheet" data-account="' + esc(name) + '">打开台账 sheet</button></div>');
    } catch (e) {
      openDrawer(esc(name), '读取失败', '<div class="acc236-empty"><b>详情读取失败</b>' + esc(apiErrMsg(e)) + '</div>');
    }
  }

  function openSheet(name) {
    const a = D.items.find((x) => x.account === name);
    if (!a) return;
    const rows = [
      ['抓取条目', int(a.scrape_items), a.scrape_errors ? '失败 ' + a.scrape_errors : '无失败'],
      ['音频', int(a.audio_count), a.audio_dir || '—'],
      ['转写', int(a.transcript_count), '—'],
      ['台账行', int(a.ledger_rows), '对标记录-' + esc(a.account)],
      ['商品挂车', int(a.products_count), '探测覆盖 ' + int(a.products_probed) + ' 条'],
      ['最近成功', a.last_success_date ? esc(a.last_success_date) : '未回写', ''],
      ['产物标记', a.has_artifacts ? '有产物' : '空壳', a.has_artifacts ? 'DELETE 会被 400 拦下，改用停用' : '可以删除']
    ];
    openDrawer(esc(name) + ' · 产物', '统计口径与 /api/overview 同源',
      '<table class="tb"><thead><tr><th>产物</th><th class="r">数量</th><th>备注</th></tr></thead><tbody>' +
      rows.map(([k, v, note]) => '<tr><td>' + k + '</td><td class="r tnum">' + v + '</td><td class="tiny muted">' + esc(note) + '</td></tr>').join('') +
      '</tbody></table>' +
      '<div class="dr-acts"><button class="btn pri" data-acc236="open-sheet" data-account="' + esc(name) + '">打开台账 sheet</button>' +
      '<button class="btn ghost" data-acc236="detail" data-account="' + esc(name) + '">看缺口</button></div>');
  }

  /* ── 写操作 ───────────────────────────────────────────── */
  async function saveForm(mode) {
    if (st.busy) return;
    const box = $('[data-acc236-form]');
    if (!box) return;
    const v = {};
    box.querySelectorAll('[name]').forEach((el) => { v[el.name] = el.type === 'checkbox' ? el.checked : el.value; });
    const name = String(v.account || '').trim().toLowerCase();
    if (!name) return say('账号不能为空');
    if (!/^[a-z0-9_.]+$/.test(name)) return say('账号只能用小写字母 / 数字 / 下划线 / 点');
    if (name.length > 31) return say('账号最长 31 个字符');
    const payload = {
      nickname: v.nickname || '', region: v.region || '', lang: v.lang || '',
      tags: String(v.tags || '').split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      note: v.note || '', active: !!v.active
    };
    st.busy = true;
    try {
      if (mode === 'edit') {
        payload.media_type = v.media_type || 'video';
        await api('/api/accounts/' + encodeURIComponent(name), {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        say('已保存 ' + name);
        st.focus = name;
        st.form = { mode: 'edit', account: name };
      } else {
        payload.account = name;
        payload.media_type = v.media_type || 'video';
        await api('/api/accounts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        say('已新增 ' + name);
        st.focus = name;
        st.form = { mode: 'edit', account: name };
      }
      await refreshAccountsEverywhere();
    } catch (e) {
      say('保存失败：' + apiErrMsg(e));
    } finally {
      st.busy = false;
    }
  }

  function parsePaste() {
    const ta = $('[data-acc236="paste"]');
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
    const box = $('[data-acc236="import-res"]');
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
    if (!r.fresh.length) return say('没有可新增的行（' + r.dup.length + ' 行主键重复）');
    st.busy = true;
    let ok = 0; const errs = [];
    for (const row of r.fresh) {
      try {
        await api('/api/accounts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ account: row.name, nickname: row.nickname, region: row.region, lang: row.lang, media_type: 'video', active: true })
        });
        ok++;
      } catch (e) { errs.push(row.name + '：' + apiErrMsg(e)); }
    }
    st.busy = false;
    const ta = $('[data-acc236="paste"]'); if (ta) ta.value = '';
    say('导入 ' + ok + ' 个' + (errs.length ? ' · 失败 ' + errs.length + '（' + errs[0] + '）' : ''));
    await refreshAccountsEverywhere();
  }

  async function bulkType(mediaType) {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    if (!tgt.length) return say('没有可操作的账号');
    try {
      const r = await api('/api/accounts/media-type', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accounts: tgt.map((a) => a.account), media_type: mediaType })
      });
      const parts = [];
      ['saved', 'same', 'not_found', 'invalid'].forEach((k) => { if (r && r[k]) parts.push(k + ' ' + r[k]); });
      say('改为' + (mediaType === 'image' ? '图文' : '视频') + '：' + (parts.join(' / ') || '完成'));
      await refreshAccountsEverywhere();
    } catch (e) { say('操作失败：' + apiErrMsg(e)); }
  }

  async function bulkActive(on) {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    if (!tgt.length) return say('没有可操作的账号');
    if (!window.confirm((on ? '启用' : '停用') + ' ' + tgt.length + ' 个账号？')) return;
    st.busy = true;
    let ok = 0; const errs = [];
    for (const a of tgt) {
      try {
        await api('/api/accounts/' + encodeURIComponent(a.account), {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: on })
        });
        ok++;
      } catch (e) { errs.push(a.account); }
    }
    st.busy = false;
    say((on ? '启用 ' : '停用 ') + ok + ' 个' + (errs.length ? ' · 失败 ' + errs.length : ''));
    await refreshAccountsEverywhere();
  }

  async function bulkDel() {
    const tgt = st.sel.size ? D.items.filter((a) => st.sel.has(a.account)) : D.items.filter(match);
    const free = tgt.filter((a) => !a.has_artifacts);
    if (!free.length) return say('选中范围内没有空壳账号（' + tgt.length + ' 个都有产物）');
    if (!window.confirm('删除 ' + free.length + ' 个空壳账号？有产物的 ' + (tgt.length - free.length) + ' 个会被跳过（接口 400）。')) return;
    st.busy = true;
    let ok = 0;
    for (const a of free) {
      try {
        await api('/api/accounts/' + encodeURIComponent(a.account), { method: 'DELETE' });
        ok++;
      } catch (e) { /* 跳过 */ }
    }
    st.busy = false;
    say('删除空壳 ' + ok + ' 个 · 跳过 ' + (tgt.length - free.length) + ' 个有产物');
    await refreshAccountsEverywhere();
  }

  /* ── 事件 ─────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    if (!e.target.closest(ROOT_SEL)) return;
    const opEl = e.target.closest('[data-acc236]');
    const op = opEl && opEl.dataset.acc236;

    const th = e.target.closest('th[data-acc236-sort]');
    if (th) {
      const k = th.dataset.acc236Sort;
      st.sort = { k: k, d: st.sort.k === k ? -st.sort.d : (['account', 'group_name', 'regionLabel', 'media_type'].includes(k) ? 1 : -1) };
      refresh();
      return;
    }

    if (op === 'refresh') { loadAccounts(true); return; }
    if (op === 'close') { closeDrawer(); return; }
    if (op === 'clear') {
      st.q = ''; st.status = ''; st.type = ''; st.group = '';
      if (opEl.closest('.u-filters') || opEl.closest('.a29-table')) {
        const qi = $('[data-acc236="q"]'); if (qi) qi.value = '';
      }
      render();
      return;
    }
    if (op === 'create') {
      st.form = { mode: 'create', account: null };
      const box = $('[data-acc236-form]'); if (box) box.outerHTML = formSection();
      return;
    }
    if (op === 'cancel') {
      st.form = { mode: 'create', account: null };
      const box = $('[data-acc236-form]'); if (box) box.outerHTML = formSection();
      return;
    }
    if (op === 'save') { saveForm(opEl.dataset.mode || st.form.mode); return; }
    if (op === 'edit') {
      st.form = { mode: 'edit', account: opEl.dataset.account };
      st.focus = opEl.dataset.account;
      closeDrawer();
      refresh();
      say('正在编辑 ' + opEl.dataset.account);
      return;
    }
    if (op === 'toggle') {
      const a = D.items.find((x) => x.account === opEl.dataset.account);
      if (a && typeof toggleAccountActive === 'function') toggleAccountActive(a.account, !a.active);
      return;
    }
    if (op === 'del') {
      if (typeof deleteAccount === 'function') deleteAccount(opEl.dataset.account);
      return;
    }
    if (op === 'sheet') { openSheet(opEl.dataset.account); return; }
    if (op === 'open-sheet') {
      closeDrawer();
      if (typeof openAccountSheet === 'function') openAccountSheet(opEl.dataset.account);
      return;
    }
    if (op === 'detail') { openDetail(opEl.dataset.account); return; }
    if (op === 'focus-import') {
      const ta = $('[data-acc236="paste"]');
      if (ta) { ta.scrollIntoView({ block: 'center', behavior: 'smooth' }); ta.focus(); }
      return;
    }
    if (op === 'validate') { const r = renderImportRes(); say('校验完成：可新增 ' + r.fresh.length + '，重复 ' + r.dup.length); return; }
    if (op === 'import') { runImport(); return; }
    if (op === 'selall') {
      const cb = $('[data-acc236="selall"]');
      if (cb && cb.checked) D.items.filter(match).forEach((a) => st.sel.add(a.account));
      else st.sel.clear();
      const s = $('[data-acc236="sel"]');
      if (s) s.textContent = '已选 ' + st.sel.size + ' / ' + D.items.filter(match).length;
      return;
    }
    if (op === 'bulk-type') { bulkType(opEl.dataset.type); return; }
    if (op === 'bulk-active') { bulkActive(opEl.dataset.active === '1'); return; }
    if (op === 'bulk-del') { bulkDel(); return; }
    if (op === 'filter-group') {
      st.group = st.group === opEl.dataset.group ? '' : opEl.dataset.group;
      refresh();
      const gs = $('[data-acc236-f="group"]'); if (gs) gs.value = st.group;
      return;
    }
    if (op === 'filter-type') {
      st.type = st.type === opEl.dataset.v ? '' : opEl.dataset.v;
      render();
      return;
    }
    if (op === 'filter-status') {
      st.status = st.status === opEl.dataset.v ? '' : opEl.dataset.v;
      render();
      return;
    }
    if (opEl && opEl.dataset.acc236Pick) {
      const key = opEl.dataset.acc236Pick;
      opEl.parentElement.querySelectorAll('.sg').forEach((b) => b.classList.toggle('on', b === opEl));
      const hidden = $('[data-acc236-form] input[name="' + key + '"]');
      if (hidden) hidden.value = opEl.dataset.v;
      return;
    }

    const drill = e.target.closest('[data-acc236-drill]');
    if (drill) {
      const key = drill.dataset.acc236Drill;
      if (key.indexOf('acct:') === 0) {
        const name = key.slice(5);
        st.focus = name;
        refresh();
        openDetail(name);
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
    if (e.target.matches('[data-acc236="q"]')) {
      st.q = e.target.value.trim().toLowerCase();
      refresh();
    }
    if (e.target.matches('[data-acc236="paste"]')) renderImportRes();
  });

  document.addEventListener('change', (e) => {
    const f = e.target.dataset && e.target.dataset.acc236F;
    if (f) {
      if (f === 'status') st.status = e.target.value;
      if (f === 'type') st.type = e.target.value;
      if (f === 'group') st.group = e.target.value;
      refresh();
    }
    if (e.target.matches('[name="active"]') && e.target.closest(ROOT_SEL)) {
      const sp = e.target.parentElement && e.target.parentElement.querySelector('span');
      if (sp) sp.textContent = e.target.checked ? '启用' : '停用';
    }
  });

  window.ACC236 = {
    render: function (payload) { st.loading = false; render(payload); },
    loading: function () { st.loading = true; render(); },
    error: function (msg) { st.loading = false; st.err = String(msg || ''); render(); },
    __state: st
  };
})();
