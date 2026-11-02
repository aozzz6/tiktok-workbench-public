const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
let fails = 0;
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }

const ACCOUNTS = [
  { account: 'baliee_official', nickname: 'Baliee', region: 'de', lang: 'de', tags: ['德国AI对标'],
    active: true, media_type: 'video', audio_count: 29, transcript_count: 29, scrape_items: 29,
    ledger_rows: 29, products_count: 29, severity: 'ok', landing: 100, has_artifacts: true,
    group_id: 'g1', group_name: '钟如妹账号' },
  { account: 'chloe.shop_de', nickname: '', region: 'de', lang: 'de', tags: [],
    active: false, media_type: 'video', audio_count: 10, transcript_count: 4, scrape_items: 10,
    ledger_rows: 8, products_count: 0, severity: 'error', landing: 62, has_artifacts: true,
    group_id: 'g1', group_name: '钟如妹账号' },
  { account: 'newhouse_de', nickname: '新房', region: 'de', lang: 'de', tags: ['家居'],
    active: true, media_type: 'image', audio_count: 0, transcript_count: 0, scrape_items: 0,
    ledger_rows: 0, products_count: 0, severity: 'ok', landing: 0, has_artifacts: false,
    group_id: '', group_name: '未分组' },
];

const calls = [];
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;
w.fetch = (u, opts) => {
  u = String(u);
  const method = (opts && opts.method) || 'GET';
  calls.push({ u, method, body: opts && opts.body });
  if (u === '/api/accounts' && method === 'POST') return ok({ ok: true, account: { account: 'x' } });
  if (u === '/api/accounts') {
    return ok({ total: ACCOUNTS.length, shown: ACCOUNTS.length,
      counts: { all: 3, active: 2, inactive: 1, video: 2, image: 1, artifacts: 2 }, accounts: ACCOUNTS });
  }
  if (/^\/api\/accounts\/[^?]+$/.test(u)) {
    if (method === 'PATCH' || method === 'DELETE') return ok({ ok: true });
    return ok({ ...ACCOUNTS[0], editable_fields: [], field_schema: {}, gaps: [] });
  }
  if (u.startsWith('/api/config')) return ok({ accounts: ACCOUNTS.map((a) => a.account), accounts_detail: ACCOUNTS, local_steps: [], agent_steps: [], step_labels: {}, steps: [] });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

(async () => {
  await w.loadAccounts(true);
  const d = w.document;

  check('导航与视图存在（账号管理入口）', () => {
    assert(d.querySelector('.nav-item[data-view="accounts"]'), '缺导航项');
    assert(d.querySelector('#view-accounts'), '缺视图容器');
  });

  check('列表渲染：三条账号 + KPI 四项 + 分组列', () => {
    const rows = d.querySelectorAll('#acct-list tbody tr');
    assert(rows.length === 3, '行数 ' + rows.length);
    assert(d.querySelectorAll('#acct-kpis .kpi').length === 4, 'KPI 数量');
    assert(/钟如妹账号/.test(rows[0].textContent), '分组列缺失');
    assert(/图文/.test(d.querySelector('#acct-list').textContent), '类型列缺图文');
  });

  check('搜索过滤（昵称/标签也命中）', () => {
    const q = d.querySelector('#acct-q');
    q.value = '家居';
    w.renderAccountTable();
    assert(d.querySelectorAll('#acct-list tbody tr').length === 1, '应按标签命中 1 条');
    q.value = '';
    w.renderAccountTable();
    assert(d.querySelectorAll('#acct-list tbody tr').length === 3, '清空后应恢复 3 条');
  });

  check('状态筛选：只看已停用', () => {
    d.querySelector('#acct-status').value = 'inactive';
    w.renderAccountTable();
    const rows = d.querySelectorAll('#acct-list tbody tr');
    assert(rows.length === 1 && /chloe\.shop_de/.test(rows[0].textContent), '停用筛选错');
    d.querySelector('#acct-status').value = '';
    w.renderAccountTable();
  });

  check('新增表单：非法账号名被前端拦截，不发请求', async () => {
    const before = calls.filter((c) => c.u === '/api/accounts' && c.method === 'POST').length;
    w.openAccountForm('create');
    d.querySelector('#af-account').value = 'Bad Name!';
    w.saveAccountForm();
    await new Promise((r) => setTimeout(r, 10));
    const after = calls.filter((c) => c.u === '/api/accounts' && c.method === 'POST').length;
    assert(before === after, '非法名不应发 POST');
    assert(/只能用小写/.test(d.querySelector('#af-account-hint').textContent), '缺字段级错误提示');
  });

  check('新增表单：合法输入 → POST 载荷含 media_type / tags 数组 / active', async () => {
    w.openAccountForm('create');
    d.querySelector('#af-account').value = 'brand_new_de';
    d.querySelector('#af-nickname').value = '新号';
    d.querySelector('#af-region').value = 'us';
    w.acctFormSyncLang();
    d.querySelector('#af-tags').value = 'a, b';
    d.querySelector('#af-type').value = 'image';
    d.querySelector('#af-active').value = '0';
    await w.saveAccountForm();
    const req = calls.filter((c) => c.u === '/api/accounts' && c.method === 'POST').pop();
    assert(req, '没有发出 POST');
    const body = JSON.parse(req.body);
    assert(body.account === 'brand_new_de', 'account 错');
    assert(body.media_type === 'image', 'media_type 未传');
    assert(Array.isArray(body.tags) && body.tags.join(',') === 'a,b', 'tags 未拆分');
    assert(body.active === false, 'active 未传');
    assert(body.lang === 'en', '地区应联动语言，实际 ' + body.lang);
  });

  check('编辑表单：PATCH 不含主键，含 media_type', async () => {
    w.openAccountForm('edit', 'baliee_official');
    d.querySelector('#af-nickname').value = 'Baliee 2';
    await w.saveAccountForm();
    const req = calls.filter((c) => c.u === '/api/accounts/baliee_official' && c.method === 'PATCH').pop();
    assert(req, '没有发出 PATCH');
    const body = JSON.parse(req.body);
    assert(!('account' in body), 'PATCH 不应带主键');
    assert(body.nickname === 'Baliee 2', '昵称未提交');
    assert(body.media_type === 'video', 'media_type 未提交');
  });

  check('删除保护：有产物的账号在前端就被拦住，不发 DELETE', async () => {
    const before = calls.filter((c) => c.method === 'DELETE').length;
    await w.deleteAccount('baliee_official');
    const after = calls.filter((c) => c.method === 'DELETE').length;
    assert(before === after, '有产物账号不应发 DELETE');
    assert(/停用/.test(d.querySelector('#toast').textContent), '应提示改用停用');
  });

  check('删除空壳：发 DELETE 并刷新', async () => {
    await w.deleteAccount('newhouse_de');
    assert(calls.some((c) => c.u === '/api/accounts/newhouse_de' && c.method === 'DELETE'), '未发 DELETE');
  });

  check('停用/启用：PATCH {active:false}', async () => {
    await w.toggleAccountActive('baliee_official', false);
    const req = calls.filter((c) => c.u === '/api/accounts/baliee_official' && c.method === 'PATCH').pop();
    assert(JSON.parse(req.body).active === false, 'active 载荷错');
  });

  check('详情弹窗：展示缺失缺口与操作按钮', async () => {
    await w.openAccountDetail('baliee_official');
    const box = d.querySelector('#modal-content');
    assert(box.querySelector('.acct-kv'), '缺 kv 区域');
    assert(box.querySelector('.acct-detail-actions'), '缺操作区');
    assert(/打开台账/.test(box.textContent), '缺台账入口');
  });

  console.log(fails ? `${fails} FAILED` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
