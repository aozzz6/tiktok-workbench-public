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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const H = ['记录日期', '对标账号'];
const DATA = { header: H, rows: [['d1', '@a1'], ['d2', '@a2']], total: 2, file: 'a.xlsx', hidden_cols: [], checks: [] };

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://127.0.0.1:8787/' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;

// 记录滚动行为（jsdom 不实现布局与滚动）
let scrollCalls = [];
let fakeScrollY = 0;
w.scrollTo = (x, y) => { scrollCalls.push({ x, y }); };
w.Element.prototype.scrollIntoView = function () { this.dataset.scrolled = '1'; };
Object.defineProperty(w, 'scrollY', { get: () => fakeScrollY, configurable: true });

w.fetch = (u) => {
  u = String(u);
  if (u.includes('/api/ledger/sheet')) return Promise.resolve({ ok: true, json: () => Promise.resolve(DATA) });
  if (u.includes('/api/ledger/resolve')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ sheet: 'shared', file: 'a.xlsx' }) });
  if (u === '/api/accounts') return Promise.resolve({ ok: true, json: () => Promise.resolve({ total: 0, shown: 0, counts: { all: 0, active: 0, inactive: 0, video: 0, image: 0, artifacts: 0 }, accounts: [] }) });
  if (u.startsWith('/api/config')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ accounts: [], local_steps: [], agent_steps: [], step_labels: {}, steps: [] }) });
  if (u.includes('/api/groups')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ groups: [] }) });
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);$/mg, ''));

(async () => {
  const d = w.document;
  const last = () => scrollCalls[scrollCalls.length - 1];

  // 模拟页面布局：#view-sheet 距文档顶部 600px
  d.querySelector('#view-sheet').getBoundingClientRect = () => ({
    top: 600, left: 0, right: 0, bottom: 900, width: 1200, height: 300, x: 0, y: 600,
  });

  /* ── 1. 全屏视图：进入后滚到视图顶部 ── */
  fakeScrollY = 0; scrollCalls = [];
  w.openSheetView({ file: 'a.xlsx', sheet: 'A' });
  await sleep(120);

  check('全屏：进入表格后滚到 #view-sheet 顶部（600 - 10 = 590）', () => {
    assert(d.querySelector('#view-sheet').classList.contains('active'), '视图未激活');
    assert(scrollCalls.length >= 1, '未调用 scrollTo');
    assert(last().y === 590, '滚动位置不对：' + JSON.stringify(scrollCalls));
  });

  /* ── 2. 加载完成后再对齐一次（内容高度变化） ── */
  {
    const before = scrollCalls.length;
    await w.renderSheetView({ file: 'a.xlsx', sheet: 'A' });
    check('全屏：数据渲染完成后再次对齐', () => {
      assert(scrollCalls.length > before, '完成态没有再次定位');
      assert(last().y === 590, '完成态滚动位置不对：' + last().y);
    });
  }

  /* ── 3. 弹窗：打开新表时弹窗内容回到顶部 ── */
  {
    w.openModal('台账 · A', 'sub', { file: 'a.xlsx', sheet: 'A' });
    const body = d.querySelector('#modal .modal-body');
    body.scrollTop = 400;
    await w.loadSheetInto('#modal-content', 'A', 200, 'a.xlsx');
    check('弹窗：加载表格后 modal-body 滚回顶部', () => {
      assert(body.scrollTop === 0, '弹窗未回到顶部：' + body.scrollTop);
    });
  }

  /* ── 4. 台账预览（内联）：已随「报表与台账」页一起下线（2026-10-24 D1-A）──
     原用例针对 #ledger-view（报表页右侧台账预览），页面删除后该容器不存在。
     台账仍有两个入口：全屏 #sheet-host（用例 1/2）与弹窗 #modal-content（用例 3），
     两者都已覆盖，这里不再重复。 */

  /* ── 5. 返回：恢复进入前的滚动位置 ── */
  {
    w.showReturnView('overview');
    await sleep(30);
    fakeScrollY = 750; scrollCalls = [];
    w.openSheetView({ file: 'a.xlsx', sheet: 'A' });
    await sleep(120);
    fakeScrollY = 0;            // 进入后页面已在表格顶部
    w.sheetViewBack();
    await sleep(220);
    check('返回：回到进入表格前的位置（750）', () => {
      assert(last().y === 750, '未恢复原位置：' + JSON.stringify(scrollCalls.slice(-4)));
      assert(!String(w.location.hash || '').startsWith('#sheet'), 'hash 未清：' + w.location.hash);
    });
  }

  /* ── 6. 浅层：深链进入（页面加载）同样定位 ── */
  {
    fakeScrollY = 0; scrollCalls = [];
    w.location.hash = '#sheet?sheet=A&file=a.xlsx';
    await sleep(200);
    check('深链：页面加载进入指定表格并定位', () => {
      assert(d.querySelector('#view-sheet').classList.contains('active'), '深链未激活表格视图');
      assert(scrollCalls.some((c) => c.y === 590), '深链未定位：' + JSON.stringify(scrollCalls));
    });
  }

  console.log(fails ? `${fails} FAILED` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
