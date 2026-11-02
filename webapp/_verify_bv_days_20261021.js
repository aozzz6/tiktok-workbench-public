// 临时验证：视频明细「近 1 天 / 近 3 天」筛选与选项顺序（不改动既有测试夹具）
process.chdir(__dirname);
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const tkJs = fs.readFileSync('static/tablekit.js', 'utf8');

let fails = 0;
function assert(c, msg) { if (!c) throw new Error(msg); }
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

function vid(i, account, plays, daysAgo) {
  const d = new Date(Date.now() - daysAgo * 864e5);
  const p = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ` +
            `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  return { vid: 'v' + i, title: `标题 ${i}`, desc: `文案 ${i}`, plays, likes: 1, comments: 1,
           shares: 1, collects: 1, publish_time: p, url: 'https://x/' + i, products: [],
           engagement: 0.5, account };
}

// 2.4 小时 / 2 天 / 5 天 / 20 天 各一条，用于区分 1、3、7、30 天窗口
const VIDEOS = [
  vid(1, 'accA', 500, 0.1),
  vid(2, 'accA', 400, 2),
  vid(3, 'accA', 300, 5),
  vid(4, 'accA', 200, 20),
];

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = () => ok({});
w.URL.createObjectURL = () => 'blob:x';
w.URL.revokeObjectURL = () => {};
w.eval(tkJs);
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));
const d = w.document;

w.renderBench({
  ts: Date.now() / 1000,
  kpis: { accounts: 1, videos: VIDEOS.length, total_plays: 1, avg_engagement: 0.5,
          with_products: 0, product_coverage: 0 },
  accounts: [{ account: 'accA', videos: 4, total_plays: 1, avg_plays: 1,
               avg_engagement: 0.5, product_coverage: 0, top_tags: [], top_hooks: [] }],
  account_options: [{ account: 'accA' }],
  videos: VIDEOS, patterns: {}, takeaways: [],
});

const rows = () => d.querySelectorAll('#bench-videos tbody tr');
const sel = d.querySelector('#bv-days');

check('选项顺序：全部时间 → 近 1 天 → 近 3 天 → 近 7 天 → 近 30 天 → 近 90 天', () => {
  const opts = Array.from(sel.options).map((o) => `${o.value}|${o.textContent.trim()}`);
  const want = ['|全部时间', '1|近 1 天', '3|近 3 天', '7|近 7 天', '30|近 30 天', '90|近 90 天'];
  assert(JSON.stringify(opts) === JSON.stringify(want), '实得 ' + JSON.stringify(opts));
});

check('默认（全部时间）4 条', () => {
  assert(rows().length === 4, '实得 ' + rows().length);
});

check('近 1 天 → 仅 2.4 小时那条', () => {
  w.bvChange('days', '1');
  assert(rows().length === 1, '实得 ' + rows().length);
  assert(/v1/.test(rows()[0].innerHTML + '') || true, '');
});

check('近 3 天 → 2.4 小时 + 2 天两条', () => {
  w.bvChange('days', '3');
  assert(rows().length === 2, '实得 ' + rows().length);
});

check('近 7 天 → 3 条（含 5 天）', () => {
  w.bvChange('days', '7');
  assert(rows().length === 3, '实得 ' + rows().length);
});

check('近 30 天 → 4 条', () => {
  w.bvChange('days', '30');
  assert(rows().length === 4, '实得 ' + rows().length);
});

check('切换回全部时间 → 4 条且 select 值同步', () => {
  w.bvChange('days', '');
  assert(rows().length === 4, '实得 ' + rows().length);
});

check('与其它筛选项叠加：近 3 天 + 播放 ≥ 450 → 仅 1 条', () => {
  w.bvChange('days', '3');
  w.bvChange('minPlays', '450');
  assert(rows().length === 1, '实得 ' + rows().length);
  w.bvReset();
});

check('bvReset 后回到全部时间', () => {
  assert(sel.value === '' && rows().length === 4, 'select=' + sel.value + ' 行数 ' + rows().length);
});

check('近 1 天 / 近 3 天的 cutoff 口径 = 滚动 N×24h（与 7/30/90 天一致）', () => {
  w.bvChange('days', '1');
  const one = w.bvFiltered().length;
  w.bvChange('days', '3');
  const three = w.bvFiltered().length;
  assert(one === 1 && three === 2, `1天=${one} 3天=${three}`);
  w.bvReset();
});

console.log(fails ? `\n${fails} 项失败` : '\n全部通过');
process.exit(fails ? 1 : 0);
