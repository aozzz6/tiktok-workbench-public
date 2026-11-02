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

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));
const d = w.document;

function day(offset) {
  const x = new Date(Date.now() - offset * 864e5);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')} 10:00:00`;
}
function v(i, account, plays, offset, extra = {}) {
  return { vid: 'v' + i, title: 't' + i, desc: 'd' + i, plays, likes: 1, comments: 0,
           shares: 0, collects: 0, publish_time: day(offset), url: 'https://x/' + i,
           products: [], engagement: 0.2, account, source: 'scrape', ...extra };
}

const VIDEOS = [
  v(1, 'accA', 1000, 0), v(2, 'accA', 3000, 0), v(3, 'accA', 500, 2),
  v(4, 'accB', 9000, 1), v(5, 'accB', 200, 5),
  v(6, 'accA', 42, 3, { source: 'ledger', has_audio: true }),
];
const ACCOUNTS = [
  { account: 'accB', videos: 2, total_plays: 9200, avg_plays: 4600, avg_engagement: 0.2,
    product_coverage: 0, with_products: 0, top_tags: [], top_hooks: [] },
  { account: 'accA', videos: 4, total_plays: 4542, avg_plays: 1135, avg_engagement: 0.2,
    product_coverage: 0, with_products: 0, top_tags: [], top_hooks: [] },
];

function mount() {
  w.renderBench({ kpis: { accounts: 2, videos: VIDEOS.length, total_plays: 1 },
                  accounts: ACCOUNTS, account_options: [], videos: VIDEOS,
                  patterns: {}, takeaways: [] });
}
mount();

check('趋势图：渲染 SVG，柱子数 = 近 30 天桶数', () => {
  const svg = d.querySelector('#bv-trend svg');
  assert(svg, '缺趋势 SVG');
  const rects = d.querySelectorAll('#bv-trend svg rect');
  assert(rects.length === 30, '应 30 个日桶，实得 ' + rects.length);
  assert(d.querySelectorAll('#bv-trend polyline').length === 1, '缺播放折线');
  assert(/发布条数/.test(d.querySelector('#bv-trend .bv-legend').textContent), '缺图例');
});

check('趋势图：切到近 7 天只剩 7 个桶', () => {
  const sel = d.querySelector('#bv-trend-range');
  sel.value = '7';
  w.renderBenchTrend();
  assert(d.querySelectorAll('#bv-trend svg rect').length === 7, '桶数不对');
});

check('趋势图：全部 = 从最早到今天的连续天数', () => {
  const sel = d.querySelector('#bv-trend-range');
  sel.value = '0';
  w.renderBenchTrend();
  // 数据跨度 0..5 天 → 6 天
  assert(d.querySelectorAll('#bv-trend svg rect').length === 6,
    '桶数 ' + d.querySelectorAll('#bv-trend svg rect').length);
  sel.value = '30';
  w.renderBenchTrend();
});

check('趋势图：无发布时间数据时给占位，不崩', () => {
  w.renderBench({ kpis: {}, accounts: [], account_options: [],
                  videos: [{ vid: 'x', title: 't', plays: 1, publish_time: '', account: 'a' }],
                  patterns: {}, takeaways: [] });
  assert(/暂无带发布时间/.test(d.querySelector('#bv-trend').textContent), '缺空态');
  mount();
});

check('账号图：按总播放降序，条形数 = 账号数', () => {
  const t = d.querySelectorAll('#bv-accchart svg text');
  assert(t.length >= 2, '缺账号标签');
  assert(/accB/.test(t[0].textContent), '首行应为播放最高的 accB，实得 ' + t[0].textContent);
  assert(d.querySelectorAll('#bv-accchart svg rect').length === 2, '条形数不对');
  // 2026-10-20 P2-7：数字口径统一为「<1 万千分位 / 万 / 亿」，9200 现在渲染成 9,200
  assert(/9,?200|9\.2千/.test(d.querySelector('#bv-accchart').textContent), '缺数值标签');
});

check('账号图：切换指标到「视频数」后数值口径变化', () => {
  const sel = d.querySelector('#bv-metric');
  sel.value = 'videos';
  w.renderBenchAccChart();
  assert(/按视频数排序/.test(d.querySelector('#bv-accchart .bv-legend').textContent), '图例未换');
  sel.value = 'product_coverage';
  w.renderBenchAccChart();
  assert(/%/.test(d.querySelector('#bv-accchart').textContent), '百分比指标缺 % 号');
  sel.value = 'total_plays';
  w.renderBenchAccChart();
});

check('来源列：抓取 / 台账两种标记都出现', () => {
  const txt = d.querySelector('#bench-videos').textContent;
  assert(/抓取/.test(txt), '缺抓取标记');
  assert(/台账/.test(txt), '缺台账标记');
  assert(d.querySelector('#bench-videos .src-ledger'), '台账行缺专属样式');
});

check('来源筛选：仅台账只剩 1 条', () => {
  w.bvChange('src', 'ledger');
  const rows = d.querySelectorAll('#bench-videos tbody tr');
  assert(rows.length === 1, '应剩 1 条，实得 ' + rows.length);
  assert(/台账/.test(rows[0].textContent), '残留非台账行');
  w.bvChange('src', 'scrape');
  assert(d.querySelectorAll('#bench-videos tbody tr').length === 5, '抓取行应为 5');
  w.bvReset();
  assert(d.querySelectorAll('#bench-videos tbody tr').length === 6, '重置后应 6 条');
});

check('重置：来源筛选回「全部来源」', () => {
  w.bvChange('src', 'ledger');
  w.bvReset();
  assert(d.querySelector('#bv-src').value === '', '下拉未复位');
  assert(d.querySelectorAll('#bench-videos tbody tr').length === 6, '行数未复位');
});

dom.window.close();
console.log(`\n通过 ${10 - fails} / 10`);
process.exit(fails ? 1 : 0);
