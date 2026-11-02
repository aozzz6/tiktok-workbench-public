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
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

let sheetMode = 'normal';   // normal | empty | error
const HEADER = ['记录日期', '对标账号', '视频发布时间', '视频标题', '视频链接/ID'];
const ROWS = [
  ['2026-10-17', '@baliee_official', '2026-10-16 08:00', 'Erste Zeile', 'https://t/v/1'],
  ['2026-10-17', '@chloe.shop_de', '2026-10-16 09:00', 'Zweite Zeile', 'https://t/v/2'],
  ['2026-10-17', '@chloe.shop_de', '2026-10-16 10:00', 'Dritte Zeile', 'https://t/v/3'],
];

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://127.0.0.1:8787/' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;
w.fetch = (u) => {
  u = String(u);
  if (u.includes('/api/ledger/resolve')) return ok({ sheet: '对标记录-baliee_official', file: 'tiktok_对标.xlsx' });
  if (u.includes('/api/ledger/sheet')) {
    if (sheetMode === 'error') return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve('{"detail":"台账被 Excel 占用"}') });
    if (sheetMode === 'empty') return ok({ header: HEADER, rows: [], total: 0, file: 'tiktok_对标.xlsx', hidden_cols: [], checks: [] });
    return ok({ header: HEADER, rows: ROWS, total: ROWS.length, file: 'tiktok_对标.xlsx', hidden_cols: [], checks: [] });
  }
  if (u.startsWith('/api/config')) return ok({ accounts: ['baliee_official'], local_steps: [], agent_steps: [], step_labels: {}, steps: [] });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  if (u === '/api/accounts') return ok({ total: 1, shown: 1,
    counts: { all: 1, active: 1, inactive: 0, video: 1, image: 0, artifacts: 1 },
    accounts: [{ account: 'baliee_official', nickname: '', region: 'de', lang: 'de', tags: [],
      active: true, media_type: 'video', scrape_items: 1, audio_count: 1, transcript_count: 1,
      ledger_rows: 3, severity: 'ok', landing: 100, group_id: 'g1', group_name: 'G' }] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

(async () => {
  const d = w.document;
  await new Promise((r) => setTimeout(r, 30));

  await w.loadAccounts(true);
  check('账号入口：行内「表格」按钮存在', () => {
    const btn = [...d.querySelectorAll('#acct-list .btn-link')].find((b) => b.textContent === '表格');
    assert(btn, '缺少「表格」入口');
    assert(/openAccountSheet/.test(btn.getAttribute('onclick')), btn.getAttribute('onclick'));
  });

  await w.openAccountSheet('baliee_official');
  await new Promise((r) => setTimeout(r, 80));

  check('进入表格：切到 #view-sheet 并带上 hash 参数', () => {
    assert(d.querySelector('#view-sheet').classList.contains('active'), '视图未激活');
    assert(/^#sheet\?/.test(w.location.hash), 'hash 未写入：' + w.location.hash);
    assert(/sheet=/.test(w.location.hash) && /file=/.test(w.location.hash), w.location.hash);
    assert(/baliee_official/.test(d.querySelector('#sheet-title').textContent), d.querySelector('#sheet-title').textContent);
  });

  check('数据加载：表头 + 只读标识 + 上下文过滤后 1 行', () => {
    const ths = d.querySelectorAll('#sheet-host thead th');
    assert(ths.length === HEADER.length, '表头列数 ' + ths.length);
    assert(d.querySelectorAll('#sheet-host tbody tr').length === 1, '上下文过滤后应只剩 1 行');
    assert(/共 3 行/.test(d.querySelector('#sheet-meta').textContent), d.querySelector('#sheet-meta').textContent);
    assert(/只读/.test(d.querySelector('#sheet-meta').textContent), '缺只读标识');
  });

  check('上下文定位：按 @账号 过滤后只剩该账号的行', () => {
    const inp = d.querySelector('#sheet-host .sheet-toolbar input');
    assert(inp.value === '@baliee_official', '搜索框未带上下文：' + inp.value);
    const rows = [...d.querySelectorAll('#sheet-host tbody tr')];
    assert(rows.length === 1 && /baliee_official/.test(rows[0].textContent), '过滤未生效');
  });

  check('上下文未命中：过滤后 0 行（调用方会退回全表）', () => {
    w.filterSheet('@nobody_here');
    assert(w._sheetVisibleCount() === 0, '应判定为 0 行');
    w.filterSheet('');
  });

  sheetMode = 'empty';
  await w.openAccountSheet('ghost_account');
  await new Promise((r) => setTimeout(r, 60));

  check('空数据态：提示"还没有数据"', () => {
    assert(d.querySelector('#sheet-host .sheet-empty'), '缺空态块');
    assert(/还没有数据/.test(d.querySelector('#sheet-host').textContent), '空态文案缺失');
  });

  sheetMode = 'error';
  await w.renderSheetView({ file: 'tiktok_对标.xlsx', sheet: '对标记录-x' });
  await new Promise((r) => setTimeout(r, 60));

  check('异常态：可读错误 + 重试按钮', () => {
    const host = d.querySelector('#sheet-host');
    assert(/表格加载失败/.test(host.textContent), host.textContent.slice(0, 80));
    assert(/台账被 Excel 占用/.test(host.textContent), '未展示后端 detail');
    assert(host.querySelector('button'), '缺重试按钮');
  });

  sheetMode = 'normal';
  w.sheetReturnView = 'accounts';
  w.showReturnView('accounts');
  check('返回路径：回到来源视图并恢复导航高亮', () => {
    assert(d.querySelector('#view-accounts').classList.contains('active'), '未回到账号管理');
    const nav = d.querySelector('.nav-item[data-view="accounts"]');
    assert(nav.classList.contains('active'), '导航未高亮');
  });

  check('深链解析：#sheet?sheet=..&file=..&q=..', () => {
    const p = w.parseSheetHash('#sheet?file=a.xlsx&sheet=%E5%AF%B9%E6%A0%87%E8%AE%B0%E5%BD%95-x&account=abc&q=%40abc');
    assert(p.sheet === '对标记录-x', p.sheet);
    assert(p.file === 'a.xlsx' && p.account === 'abc' && p.q === '@abc', JSON.stringify(p));
    assert(w.parseSheetHash('#bench') === null, '非 sheet hash 应返回 null');
  });

  check('弹窗「进入表格」：有 sheet 上下文才显示', () => {
    w.openModal('台账 · x', 'sub', { file: 'a.xlsx', sheet: '对标记录-x' });
    const btn = d.querySelector('#modal-sheet');
    assert(btn.style.display !== 'none', '应显示');
    assert(typeof btn.onclick === 'function', '缺点击行为');
    w.openModal('账号详情 · x', 'sub', null);
    assert(d.querySelector('#modal-sheet').style.display === 'none', '无上下文应隐藏');
  });

  sheetMode = 'normal';
  await w.loadSheetInto('#modal-content', '对标记录-x', 200, 'tiktok_对标.xlsx');

  check('表格工具栏：右侧出现「进入表格」按钮（红框位置）', () => {
    const btn = d.querySelector('#modal-content .sheet-toolbar .sheet-open');
    assert(btn, '工具栏内缺按钮');
    assert(/进入表格/.test(btn.textContent), btn.textContent);
    const tools = d.querySelector('#modal-content .sheet-tools');
    assert(tools && tools.querySelector('.sheet-file'), '文件标签应与按钮同组靠右');
  });

  await w.openSheetViewFromSheet();
  await new Promise((r) => setTimeout(r, 80));

  check('点工具栏按钮：关闭弹窗 → 进入表格视图，且视图内不再显示该按钮', () => {
    assert(d.querySelector('#modal').classList.contains('hidden'), '弹窗未关闭');
    assert(d.querySelector('#view-sheet').classList.contains('active'), '未切换到表格视图');
    assert(/sheet=/.test(w.location.hash), 'hash 未更新');
    assert(!d.querySelector('#sheet-host .sheet-open'), '视图内不应再显示「进入表格」');
  });

  console.log(fails ? `${fails} FAILED` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
