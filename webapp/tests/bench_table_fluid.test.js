/* bench_table_fluid.test.js — R3 回归（2026-10-22 独立验收）
   验收报告 R3：1280 桌面下账号表固定 1080px，滚动容器只有 908px，必然横向溢出。
   本套件锁三条策略：
     1. 首次进入 = 流体（表宽 100% + 百分比列宽），不写死 px、不设 min-width；
     2. 手工拖列宽 = 冻结成 px，此时横向滚动是保留行为（不是 bug）；
     3. 重置列宽 = 清掉 localStorage 记忆 + 回到流体。
   真实容器宽度/scrollWidth 的判定在浏览器量测（verify_ui_r2r3_20261022.py）里做。 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');

let fails = 0;
const assert = (c, m) => { if (!c) throw new Error(m); };
function check(n, f) {
  try { f(); console.log('PASS ' + n); }
  catch (e) { fails++; console.log('FAIL ' + n + ' -> ' + e.message); }
}

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
const d = w.document;
w.EventSource = class { close() {} };
w.setInterval = () => 0;
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
w.eval(fs.readFileSync('static/tablekit.js', 'utf8'));
w.eval(appJs.replace(/\nboot\(\);/g, '\n'));

const ACCOUNTS = [
  { account: 'a1', videos: 3, total_plays: 9000, avg_plays: 3000, avg_engagement: 3.1,
    product_coverage: 60, top_tags: ['x'], top_hooks: ['y'], lang: 'en' },
  { account: 'a2', videos: 2, total_plays: 500, avg_plays: 250, avg_engagement: 3.3,
    product_coverage: 50, top_tags: ['z'], top_hooks: [], lang: 'de' },
];
const table = () => d.querySelector('#bench-accounts');
const ths = () => [...table().querySelectorAll('thead th')];
const KEY = 'tk:bench-accounts';

// 走 renderBench（而不是直接 renderBenchAccounts），让 benchData 有值 —— 与真实加载路径一致
w.renderBench({ kpis: {}, accounts: ACCOUNTS.slice(), account_options: [], videos: [], patterns: {}, takeaways: [] });

check('R3-1 首次进入：流体模式、表宽 100%、无 min-width', () => {
  const t = table();
  assert(t.dataset.tkFluid === '1', '未进流体模式');
  assert(t.classList.contains('tk-fluid'), '缺 tk-fluid 类');
  assert(t.style.width === '100%', '表宽=' + t.style.width);
  // 不写 inline min-width：窄视口下要让 CSS 的 @media(min-width:900px) 兜底生效
  assert(t.style.minWidth === '', '不应内联 min-width：' + t.style.minWidth);
  assert(!t.style.tableLayout || t.style.tableLayout === 'fixed', 'table-layout=' + t.style.tableLayout);
});

check('R3-2 首次进入：8 列百分比宽度，合计 100%', () => {
  const ps = ths().map(h => parseFloat(h.style.width));
  assert(ps.every(v => v > 0), '存在非百分比列宽：' + ths().map(h => h.style.width).join(','));
  assert(Math.abs(ps.reduce((a, b) => a + b, 0) - 100) < 0.05, '合计=' + ps.reduce((a, b) => a + b, 0));
});

check('R3-3 拖列宽 → 冻结 px 模式（允许滚动），不再铺满', () => {
  const g = ths()[0].querySelector('.tk-grip');
  assert(g, '缺列宽手柄');
  const down = new w.Event('pointerdown', { bubbles: true });
  down.clientX = 100;
  g.dispatchEvent(down);
  w.dispatchEvent(new w.Event('pointerup', { bubbles: true }));
  assert(table().dataset.tkFluid !== '1', '拖动后仍是流体模式');
  assert(/px$/.test(table().style.width), '拖动后表宽未切 px：' + table().style.width);
});

check('R3-4 有列宽记忆时：按记忆的 px 渲染，不覆盖成百分比', () => {
  w.localStorage.setItem(KEY, JSON.stringify([180, 60, 90, 80, 84, 92, 190, 190]));
  w.renderBenchAccounts(ACCOUNTS);
  const ws = ths().map(h => parseInt(h.style.width, 10));
  assert(ws[0] === 180 && ws[7] === 190, '未按记忆列宽渲染：' + JSON.stringify(ws));
  assert(table().dataset.tkFluid !== '1', '有记忆时不应回到流体');
});

check('R3-5 重置列宽：清记忆 + 回流体 + 百分比恢复', () => {
  w.baResetColWidths();
  assert(w.localStorage.getItem(KEY) === null, '记忆未清除');
  assert(table().dataset.tkFluid === '1', '重置后未回流体');
  const ps = ths().map(h => parseFloat(h.style.width));
  assert(ps.every(v => v > 0) && Math.abs(ps.reduce((a, b) => a + b, 0) - 100) < 0.05,
    '重置后列宽不是百分比：' + ths().map(h => h.style.width).join(','));
});

check('R3-6 页面有「重置列宽」入口且绑定到 baResetColWidths', () => {
  const btn = d.querySelector('#ba-reset-w');
  assert(btn, '缺 #ba-reset-w');
  assert(/baResetColWidths\(\)/.test(btn.getAttribute('onclick') || ''), 'onclick=' + btn.getAttribute('onclick'));
  assert(typeof w.TK.resetWidths === 'function', 'TK.resetWidths 未导出');
});

check('R3-7 静态资源版本号已升级（避免浏览器吃旧 app.js）', () => {
  // 2026-10-26：新增「提示词优化」板块时同步升级了 app.js 版本号，断言跟随当前发布版本
  assert(/app\.js\?v=20261026A/.test(html), 'app.js 版本号未升级');
  assert(/style\.v3\.css\?v=20261022B/.test(html), 'style.v3.css 版本号未升级');
});

dom.window.close();
console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
process.exit(fails ? 1 : 0);
