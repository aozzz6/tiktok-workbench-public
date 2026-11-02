/* bench_chart_zero.test.js — R2 回归（2026-10-22 独立验收）
   验收报告 R2 的原始缺陷：0% 账号的 SVG 条宽仍是 6.0（`Math.max(6, …)` 无条件下限），
   且 `r[key] || 0` 把 null 混成 0。本套件断言「真实渲染出来的条」：
     · 0        → 不出 <rect>（值标签仍显示 0%）
     · null/缺字段 → 不出 <rect>，值标签显示「未知」
     · 微小正值  → 出 <rect>，宽度正好是可见性下限（6.0）
     · 普通正值  → 出 <rect>，宽度与数值成比例
   不做「SVG 存在即通过」这种弱断言。 */
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

const acct = (name, cov, plays) => ({
  account: name, product_coverage: cov, avg_engagement: cov,
  total_plays: plays, avg_plays: plays, videos: 4, top_tags: [], top_hooks: [],
});
const ACCOUNTS = [
  acct('top', 80, 8000),
  acct('mid', 40, 4000),
  acct('tiny', 0.4, 40),
  acct('zero', 0, 0),
  acct('unknown', null, null),
  (() => { const a = acct('missing', 0, 100); delete a.product_coverage; delete a.avg_engagement; return a; })(),
];

function renderWith(metric, accounts) {
  w.renderBench({ kpis: {}, accounts, account_options: [], videos: [], patterns: {}, takeaways: [] });
  d.querySelector('#bv-metric').value = metric;
  w.renderBenchAccChart();
}
renderWith('product_coverage', ACCOUNTS);

const bar = (name) => d.querySelector(`#bv-accchart g.ba-bar[data-acc="${name}"] rect`);
const barW = (name) => parseFloat(bar(name).getAttribute('width'));
const barText = (name) => {
  const g = d.querySelector(`#bv-accchart g.ba-bar[data-acc="${name}"]`);
  const texts = [...g.querySelectorAll('text')];
  return texts[texts.length - 1].textContent;
};
const geoms = () => [...d.querySelectorAll('#bv-accchart g.ba-bar')].map(g => g.getAttribute('data-acc'));

check('R2-1 0% 不出条形（0 不是「6px 的小条」）', () => {
  assert(!bar('zero'), '0% 仍画出了 <rect>');
  assert(barText('zero') === '0%', '0% 值标签=' + barText('zero'));
});

check('R2-2 null / 字段缺失 → 不出条形且标「未知」', () => {
  assert(!bar('unknown'), 'null 仍画出了 <rect>');
  assert(!bar('missing'), '缺字段仍画出了 <rect>');
  assert(barText('unknown') === '未知', 'null 值标签=' + barText('unknown'));
  assert(barText('missing') === '未知', '缺字段值标签=' + barText('missing'));
});

check('R2-3 微小正值拿到可见性下限 6.0', () => {
  assert(bar('tiny'), '微小正值没有条形');
  assert(barW('tiny') === 6, '微小正值条宽=' + barW('tiny'));
});

check('R2-4 普通正值按比例（80 : 40 = 2 : 1）', () => {
  const a = barW('top'), b = barW('mid');
  assert(a > 6 && b > 6, '普通正值条宽异常 top=' + a + ' mid=' + b);
  assert(Math.abs(a / b - 2) < 0.02, '80/40 条宽比=' + (a / b));
});

check('R2-5 条形总数 = 3（只有 top/mid/tiny 有有效正值）', () => {
  assert(d.querySelectorAll('#bv-accchart svg rect').length === 3,
    'rect 数=' + d.querySelectorAll('#bv-accchart svg rect').length);
  assert(d.querySelectorAll('#bv-accchart rect[width="0"]').length === 0, '存在 0 宽 rect');
});

check('R2-6 排序：未知值沉底，不参与大小比较', () => {
  const names = geoms();
  assert(names[0] === 'top', '首位应是 top，实得 ' + names[0]);
  assert(names.indexOf('zero') > names.indexOf('tiny'), '0 应排在微小正值之后');
  assert(names.slice(-2).sort().join() === 'missing,unknown', '未知值未沉底：' + names.join('>'));
});

check('R2-7 图例说明「0 与未知不画条 · 正值最小 6px」', () => {
  const t = d.querySelector('#bv-accchart .bv-legend').textContent;
  assert(/0 与未知不画条/.test(t), '图例缺零值说明：' + t);
  assert(/正值最小 6px/.test(t), '图例缺可见性下限说明：' + t);
});

check('R2-8 全 0/全未知时给出说明，而不是一堆 6px 条', () => {
  renderWith('product_coverage', [
    acct('a', 0, 0), acct('b', 0, 0), acct('c', null, null),
  ]);
  assert(d.querySelectorAll('#bv-accchart svg rect').length === 0, '全零仍画条');
  assert(/全部为 0 或未知/.test(d.querySelector('#bv-accchart').textContent), '缺全零说明');
});

check('R2-9 total_plays 的 null 同样不算 0', () => {
  renderWith('total_plays', [acct('p', 10, 1000), acct('q', 10, null)]);
  assert(bar('p'), '正值缺条');
  assert(!bar('q'), 'null 播放量画出了条');
  assert(barText('q') === '未知', 'null 播放量标签=' + barText('q'));
});

dom.window.close();
console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
process.exit(fails ? 1 : 0);
