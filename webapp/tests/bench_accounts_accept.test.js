const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/style.css', 'utf8');
const tkCss = fs.readFileSync('static/tablekit.css', 'utf8');
const cssNoWs = css.replace(/\s+/g, '');
let fails = 0;
const assert = (c, m) => { if (!c) throw new Error(m); };
function check(n, f) {
  try { f(); console.log('PASS ' + n); }
  catch (e) { fails++; console.log('FAIL ' + n + ' -> ' + e.message); }
}

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
w.setInterval = () => 0;
w.eval(fs.readFileSync('static/tablekit.js', 'utf8'));
w.eval(appJs.replace(/\nboot\(\);/g, '\n'));

const ACCOUNTS = [
  { account: 'baliee_official', lang: 'en', videos: 12, total_plays: 1090000, avg_plays: 22000,
    avg_engagement: 4.35, product_coverage: 62, top_tags: ['alltagmitos', 'kueche', 'organizer'],
    top_hooks: ['sofort', 'endlich', 'warte'] },
  { account: 'chloe.shop_de', lang: 'de', videos: 5, total_plays: 7541, avg_plays: 1508,
    avg_engagement: null, product_coverage: null, top_tags: [], top_hooks: ['jetzt'] },
  { account: 'lucas_style', lang: 'en', videos: 30, total_plays: 250000000, avg_plays: 8333333,
    avg_engagement: 7.1, product_coverage: 100, top_tags: ['fashion'], top_hooks: [] },
];

const d = w.document;
w.renderBenchAccounts(ACCOUNTS);
const table = d.querySelector('#bench-accounts');

check('B1 表头只有 1 个排序箭头 + 排序说明', () => {
  const heads = [...table.querySelectorAll('thead th')];
  const marked = heads.filter(h => h.classList.contains('tk-asc') || h.classList.contains('tk-desc'));
  assert(marked.length === 1, '排序态表头数=' + marked.length);
  assert(marked[0].textContent.includes('总播放'), '排序列=' + marked[0].textContent);
  assert(marked[0].classList.contains('tk-desc'), '默认应降序');
  const note = d.querySelector('#ba-sort-note');
  assert(/按 总播放 降序/.test(note.textContent), 'note=' + note.textContent);
});

check('B2 8 列语义宽度 + 数字列右对齐', () => {
  const heads = [...table.querySelectorAll('thead th')];
  assert(heads.length === 8, '列数=' + heads.length);
  // 2026-10-22 R3（独立验收）：列宽不再是写死 px（1280 会溢出），
  // 改为「语义权重 → 百分比」铺满容器；权重仍保留在 data-w 上供核对。
  const want = [202, 70, 100, 92, 100, 104, 202, 202];
  heads.forEach((h, i) => {
    const wv = parseInt(h.dataset.w, 10);
    assert(wv === want[i], '第' + i + '列语义权重 ' + wv + ' != ' + want[i]);
    assert(/%$/.test(h.style.width), '第' + i + '列不是百分比宽度：' + h.style.width);
  });
  const pct = heads.reduce((s, h) => s + parseFloat(h.style.width), 0);
  assert(Math.abs(pct - 100) < 0.05, '列宽百分比合计 ' + pct + ' != 100');
  const rts = heads.filter(h => h.classList.contains('rt')).length;
  assert(rts === 5, '右对齐列数=' + rts);
  assert(/tabular-nums/.test(css), '缺 tabular-nums');
});

check('B3 标签列 2 芯片 + N，完整不截断', () => {
  const first = [...table.tBodies[0].rows].find(r => r.dataset.acct === 'baliee_official');
  const chips = [...first.querySelectorAll('td.ba-tags')[0].querySelectorAll('.mini-tag')];
  assert(chips.length === 3, '芯片数=' + chips.length);
  assert(chips[0].textContent === 'alltagmitos', '首个芯片被截断: ' + chips[0].textContent);
  assert(chips[0].textContent !== chips[1].textContent, '两个芯片相同');
  assert(chips[2].textContent === '+1', '溢出标=' + chips[2].textContent);
  assert(/alltagmitos.*organizer/.test(chips[2].getAttribute('title')), '+N 缺全量 title');
  assert(/white-space:normal/.test(css), '标签列未允许换行');
});

check('B4 首次进入铺满容器；窄视口/手工拉宽才横向滚动', () => {
  // 2026-10-22 R3：旧断言锁的是 `.bench-tbl{min-width:1080px}` —— 正是 1280 溢出的原因。
  // 现在默认流体（100% + 百分比列宽），只有 @media(max-width:1180px) 给 900px 兜底。
  const v3 = fs.readFileSync('static/style.v3.css', 'utf8').replace(/\s+/g, '');
  assert(/\.bench-tbl\{min-width:0;width:100%\}/.test(v3), '缺流体覆盖规则');
  assert(/@media\(max-width:1279px\)\{\.bench-tbl\{min-width:900px\}\}/.test(v3), '缺窄视口兜底');
  assert(table.dataset.tkFluid === '1', '首次进入未进流体模式');
  assert(table.style.width === '100%', '表宽不是 100%：' + table.style.width);
});

check('B5 表头吸顶 + 去掉 460 死高', () => {
  assert(/\.bench-tbltheadth\{position:sticky;top:0/.test(cssNoWs), '表头未吸顶');
  assert(/max-height:calc\(100vh-260px\)/.test(cssNoWs), '未改 calc 高度');
  assert(!/\.bench-table\{max-height:460px\}/.test(cssNoWs), '460px 死高仍在');
  assert(/scrollbar-gutter:stable/.test(css), '缺 scrollbar-gutter');
});

check('B6 数字口径统一 + 原始值 title', () => {
  assert(w.baNum(250000000) === '2.5亿', '亿: ' + w.baNum(250000000));
  assert(w.baNum(1090000) === '109.0万', '万: ' + w.baNum(1090000));
  assert(w.baNum(7541) === '7,541', '千分位: ' + w.baNum(7541));
  const tr = [...table.tBodies[0].rows].find(r => r.dataset.acct === 'chloe.shop_de');
  const tds = [...tr.children];
  assert(tds[2].textContent.trim() === '7,541', '总播放显示=' + tds[2].textContent);
  assert(tds[2].getAttribute('title') === '7,541', '原始值 title 缺失');
  assert(/—/.test(tds[4].textContent), '空互动率应显示 —，实际 ' + tds[4].textContent);
});

check('B7 挂车率 条+定宽数字', () => {
  assert(/\.ba-cov-bar\{display:inline-block;width:44px/.test(cssNoWs), '条宽未定');
  assert(/\.ba-cov-v\{display:inline-block;min-width:40px;text-align:right/.test(cssNoWs), '数字未定宽右对齐');
  const tr = [...table.tBodies[0].rows].find(r => r.dataset.acct === 'baliee_official');
  const cov = tr.querySelector('.ba-cov');
  assert(cov.querySelector('.ba-cov-bar b').style.width === '62%', '条宽未按 62%');
  assert(cov.querySelector('.ba-cov-v').textContent === '62%', '数字不对');
});

check('B8 单击行 = 高亮 + 联动条形图', () => {
  const tr = [...table.tBodies[0].rows].find(r => r.dataset.acct === 'lucas_style');
  let drawn = 0;
  w.renderBenchAccChart = () => { drawn++; };
  tr.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  assert(tr.classList.contains('tk-sel'), '行未高亮');
  assert(drawn === 1, '未触发条形图重绘');
});

check('B9 双击行 -> openAccountSheet', () => {
  const tr = [...table.tBodies[0].rows].find(r => r.dataset.acct === 'lucas_style');
  const opened = [];
  w.openAccountSheet = (a) => opened.push(a);
  tr.dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true }));
  assert(opened[0] === 'lucas_style', '未调用: ' + JSON.stringify(opened));
});

check('B10 行 tabindex + 回车打开 + 聚焦描边', () => {
  const trs = [...table.tBodies[0].rows];
  assert(trs.every(t => t.getAttribute('tabindex') === '0'), '存在不可聚焦行');
  assert(/tbodytr:focus-visible\{outline:2pxsolidvar\(--pri\)/.test(cssNoWs), '缺 focus-visible 描边');
  const opened = [];
  w.openAccountSheet = (a) => opened.push(a);
  trs[0].dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  assert(opened.length === 1, '回车未打开台账');
});

check('表头操作提示行', () => {
  const hint = d.querySelector('.ba-hint');
  assert(hint, '缺 .ba-hint');
  assert(/单击行联动/.test(hint.textContent) && /双击行打开/.test(hint.textContent), hint.textContent);
});

check('排序占位符默认透明', () => {
  const flat = tkCss.replace(/\s+/g, '');
  assert(/content:"↕"/.test(flat), '缺箭头占位符');
  assert(/content:"↕"[^}]*opacity:0/.test(flat), '占位符未默认透明');
});

console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
process.exit(fails ? 1 : 0);
