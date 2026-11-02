/* ba_fold_20261020.test.js — 方案 A「账号对比表 TOP12 折叠」回归
   · 默认折叠：20 账号只显 TOP12 + 1 行「其余 N 个账号」
   · 展开：显全部 + 尾部「收起」行
   · 选中 12 名外账号：折叠态下自动并入为 pinned 行（标全量名次）
   · 排序说明补「默认 TOP 12，其余 N 个已折叠」
   · 3 账号（≤TOP12）不折叠
*/
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const tkJs = fs.readFileSync('static/tablekit.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/style.css', 'utf8');
const cssNoWs = css.replace(/\s+/g, '');

let fails = 0, total = 0;
function check(n, f) {
  total++;
  try { f(); console.log('PASS', n); }
  catch (e) { fails++; console.log('FAIL', n, '->', e.message); }
}
function assert(c, m) { if (!c) throw new Error(m); }

function boot() {
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8787/' });
  const w = dom.window;
  w.EventSource = class { close() {} };
  w.URL.createObjectURL = () => 'blob:x';
  w.URL.revokeObjectURL = () => {};
  w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  w.eval(tkJs);
  w.eval(appJs.replace(/\nboot\(\);?/g, '').replace(/^setInterval\(.*\);$/gm, ''));
  return { w, d: w.document };
}

// 20 账号，acc00..acc19，total_plays 严格降序（acc00 最高）
const ACCOUNTS = [];
for (let i = 0; i < 20; i++) {
  ACCOUNTS.push({
    account: 'acc' + String(i).padStart(2, '0'),
    videos: 1,
    total_plays: (20 - i) * 10000,   // acc00=200000 … acc19=10000
    avg_plays: (20 - i) * 10000,
    avg_engagement: 1 + i / 10,
    product_coverage: 50,
    top_tags: [], top_hooks: [],
  });
}

const { w, d } = boot();
w.renderBenchAccounts(ACCOUNTS);
const tbody = () => d.querySelector('#bench-accounts').tBodies[0];
const acctRows = () => [...tbody().querySelectorAll('tr[data-acct]')];
const foldRow = () => tbody().querySelector('tr[data-ba-fold]');
const foldBtn = () => foldRow() && foldRow().querySelector('.ba-fold-btn');
const note = () => d.querySelector('#ba-sort-note').textContent;

check('F1 默认折叠：只显 TOP12 + 1 折叠行', () => {
  assert(acctRows().length === 12, '默认应 12 行账号，实得 ' + acctRows().length);
  assert(foldRow(), '缺折叠行');
  assert(/其余 8 个账号/.test(foldBtn().textContent), '折叠文案=' + foldBtn().textContent);
  assert(/合计播放/.test(foldBtn().textContent), '折叠行缺合计播放');
  assert(/条视频/.test(foldBtn().textContent), '折叠行缺视频数');
});

check('F1b 折叠态排序说明标注 TOP12', () => {
  assert(/默认 TOP 12/.test(note()), 'note=' + note());
  assert(/其余 8 个已折叠/.test(note()), 'note 未标折叠数：' + note());
});

check('F2 展开：显全部 20 + 尾部收起行', () => {
  w.baSetExpanded(true);
  assert(acctRows().length === 20, '展开后应 20 行，实得 ' + acctRows().length);
  const fr = foldRow();
  assert(fr, '展开态应保留收起行');
  assert(fr.classList.contains('on'), '收起行未标 on');
  assert(/已展开全部 20/.test(fr.querySelector('.ba-fold-btn').textContent), '收起文案=' + fr.querySelector('.ba-fold-btn').textContent);
  assert(/已展开全部 20 个账号/.test(note()), 'note=' + note());
});

check('F3 点折叠行按钮 = 展开，再点 = 收起', () => {
  // jsdom 不执行 inline onclick，直接调按钮背后的 baToggleExpand()
  w.baSetExpanded(false);
  assert(acctRows().length === 12, '收起未生效');
  assert(foldBtn() && /其余 8 个账号/.test(foldBtn().textContent), '折叠按钮未就位');
  w.baToggleExpand();                 // 展开
  assert(acctRows().length === 20, '点击未展开');
  assert(foldRow().classList.contains('on'), '收起行应为 on');
  w.baToggleExpand();                // 再收起
  assert(acctRows().length === 12, '再次点击未收起');
});

check('F4 折叠态选中 12 名外账号：并入为 pinned 行 + 标名次', () => {
  w.baSetExpanded(false);
  // 先展开选中 acc14（全量第 15 名），再收起 → pinned 行应保留
  w.baSetExpanded(true);
  acctRows().find((x) => x.getAttribute('data-acct') === 'acc14').click();   // baActive=acc14
  w.baSetExpanded(false);
  const pinned = acctRows().find((x) => x.getAttribute('data-acct') === 'acc14');
  assert(pinned, 'pinned 行 acc14 应存在');
  assert(pinned.classList.contains('ba-pin'), 'pinned 行未标类');
  assert(/全量第 15 名/.test(pinned.children[0].textContent), '名次标记缺失：' + pinned.children[0].textContent);
  assert(acctRows().length === 13, '折叠态 = TOP12 + 1 pinned，实得 ' + acctRows().length);
  assert(/其余 7 个账号/.test(foldBtn().textContent), 'pinned 后隐藏数应 7：' + foldBtn().textContent);
  // 取消选择 → pinned 消失
  pinned.click();
  assert(!acctRows().find((x) => x.getAttribute('data-acct') === 'acc14'), '取消后 pinned 未消失');
  assert(acctRows().length === 12, '取消后应回 12 行');
});

check('F5 折叠态点折叠行不当选行（不误触发高亮）', () => {
  w.baSetExpanded(false);
  const before = d.querySelectorAll('#bench-accounts tbody tr.tk-sel').length;
  foldRow().click();                 // 点的是整行，非按钮文字
  assert(d.querySelectorAll('#bench-accounts tbody tr.tk-sel').length === before, '点折叠行改了高亮');
});

check('F6 CSS：折叠行 + pinned 样式已接入', () => {
  assert(cssNoWs.includes('ba-fold-cell{'), '缺 .ba-fold-cell');
  assert(cssNoWs.includes('ba-fold-btn{'), '缺 .ba-fold-btn');
  assert(cssNoWs.includes('tr.ba-pin'), '缺 pinned 行样式');
  assert(cssNoWs.includes('ba-pin-rank'), '缺名次样式');
});

check('F7 ≤TOP12 账号不折叠', () => {
  w.renderBenchAccounts(ACCOUNTS.slice(0, 5));   // 5 账号
  assert(acctRows().length === 5, '5 账号应全显');
  assert(!foldRow(), '≤12 不应出现折叠行');
  assert(!/折叠/.test(note()), '≤12 不应有折叠说明：' + note());
  w.renderBenchAccounts(ACCOUNTS);
});

console.log(`\n${total - fails}/${total} passed`);
process.exit(fails ? 1 : 0);
