/* bench_empty_data.test.js — T-11 回归（2026-11-05 全功能检查）
 *
 * 现象：对标分析页在没有任何视频数据时，webapp/static/bench2/derive.js 用
 *       `new Date('' + 'T00:00:00')` 得到 Invalid Date，`toISOString()` 抛
 *       RangeError: Invalid time value，页面显示「读取 /api/benchmark 失败：Invalid time value」。
 *
 * 本用例只测纯函数 derive()（不连后端、不需要真机），加载真正的 derive.js：
 *   1) 空载荷（accounts: []、videos: []）不抛异常，返回结构仍齐全
 *      （KPI / 账号行 / 趋势桶 / 7 日切点用到的字段）。
 *   2) 有账号配置但 0 视频（抓取为空）同样不抛异常，账号行 recent7 = 0。
 *   3) 有数据时 7 日切点口径不变：只统计 lastDay 前 7 天内的视频（含边界），
 *      更早的视频不计入 recent7。
 *
 * 运行：cd webapp && node tests\bench_empty_data.test.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
const w = dom.window;
// 与 static/index.html 一致：derive.js 挂 window.BENCH_DERIVE
w.eval(read('static/bench2/derive.js'));
const derive = w.BENCH_DERIVE;

let pass = 0;
let fail = 0;
const check = (name, fn) => {
  try { fn(); pass++; console.log('  PASS ' + name); }
  catch (e) { fail++; console.log('  FAIL ' + name + ' -> ' + (e && e.message)); }
};
const assert = (c, m) => { if (!c) throw new Error(m || 'assertion failed'); };

const clone = (x) => JSON.parse(JSON.stringify(x));

/* 与 2026-11-05 实测 /api/benchmark 一致的空载荷字段形状 */
const makeEmptyPayload = () => ({
  ts: 1793868123.7268693,
  kpis: {
    accounts: 0, videos: 0, total_plays: 0, total_likes: 0, avg_engagement: 0,
    with_products: 0, product_coverage: 0, top_accounts_by_plays: [],
    ledger_rows: 0, ledger_only: 0
  },
  accounts: [],
  account_options: [],
  videos: [],
  patterns: {
    play_buckets: [
      { label: '0-1k', count: 0, pct: 0 },
      { label: '1k-10k', count: 0, pct: 0 },
      { label: '10k-100k', count: 0, pct: 0 },
      { label: '100k+', count: 0, pct: 0 }
    ],
    top_tags: [], hook_words: [], top_videos: [], products_top: [],
    n_videos_in_sample: 0
  },
  takeaways: [],
  data_sources: { scrape_dir: '', products_dir: '', ledger_rows: 0, ledger_only: 0, gmv_status: '' }
});

const emptyPayload = makeEmptyPayload();

console.log('== 用例 1：空载荷（accounts: []、videos: []）不抛异常，返回结构齐全 ==');
check('derive(空) 不抛 RangeError: Invalid time value', () => {
  assert(derive, 'window.BENCH_DERIVE 未加载');
  derive(clone(emptyPayload));
});
check('KPI 原样带出（kpis 对象仍在）', () => {
  const D = derive(clone(emptyPayload));
  assert(D && D.kpis && typeof D.kpis === 'object', 'kpis 丢失');
  assert(D.kpis.videos === 0, 'kpis.videos=' + D.kpis.videos);
  assert(D.kpis.accounts === 0, 'kpis.accounts=' + D.kpis.accounts);
});
check('账号行是空数组（accounts 仍在）', () => {
  const D = derive(clone(emptyPayload));
  assert(Array.isArray(D.accounts), 'accounts 不是数组');
  assert(D.accounts.length === 0, 'accounts.length=' + D.accounts.length);
});
check('趋势桶是空数组（series 仍在）', () => {
  const D = derive(clone(emptyPayload));
  assert(Array.isArray(D.series), 'series 不是数组');
  assert(D.series.length === 0, 'series.length=' + D.series.length);
});
check('7 日切点相关字段齐备：meta.from/to/days + 视频列表', () => {
  const D = derive(clone(emptyPayload));
  assert(D.meta && typeof D.meta === 'object', 'meta 丢失');
  assert(D.meta.from === '', 'meta.from=' + JSON.stringify(D.meta.from));
  assert(D.meta.to === '', 'meta.to=' + JSON.stringify(D.meta.to));
  assert(D.meta.days === 0, 'meta.days=' + D.meta.days);
  assert(D.meta.nVideos === 0, 'meta.nVideos=' + D.meta.nVideos);
  assert(D.meta.nAccounts === 0, 'meta.nAccounts=' + D.meta.nAccounts);
  assert(Array.isArray(D.videos), 'videos 不是数组');
});
check('其余渲染依赖字段都在：baselines / patterns / takeaways', () => {
  const D = derive(clone(emptyPayload));
  assert(D.baselines && typeof D.baselines === 'object', 'baselines 丢失');
  assert(D.patterns && typeof D.patterns === 'object', 'patterns 丢失');
  assert(Array.isArray(D.patterns.products_top), 'patterns.products_top 不是数组');
  assert(Array.isArray(D.takeaways), 'takeaways 不是数组');
});
check('asOf 是合法的时间串', () => {
  const D = derive(clone(emptyPayload));
  assert(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(D.meta.asOf), 'asOf=' + JSON.stringify(D.meta.asOf));
});

console.log('== 用例 2：有账号配置但 0 视频，同样不抛异常，账号行 recent7 = 0 ==');
const accOnly = makeEmptyPayload();
accOnly.accounts.push({
  account: 'alpha', lang: 'EN', videos: 0, total_plays: 0, total_likes: 0,
  avg_plays: 0, avg_engagement: 0, product_coverage: 0,
  top_tags: [], top_hooks: [], has_scrape: false
});
check('derive(有账号、无视频) 不抛异常', () => {
  assert(derive, 'window.BENCH_DERIVE 未加载');
  derive(clone(accOnly));
});
check('账号行仍产出 1 行，recent7 = 0，rank = 1', () => {
  const D = derive(clone(accOnly));
  assert(D.accounts.length === 1, 'accounts.length=' + D.accounts.length);
  assert(D.accounts[0].account === 'alpha', 'account=' + D.accounts[0].account);
  assert(D.accounts[0].recent7 === 0, 'recent7=' + D.accounts[0].recent7);
  assert(D.accounts[0].rank === 1, 'rank=' + D.accounts[0].rank);
  assert(D.meta.nAccounts === 1, 'meta.nAccounts=' + D.meta.nAccounts);
});

console.log('== 用例 3：有数据时 7 日切点口径不变（只算 lastDay 前 7 天）==');
const withData = makeEmptyPayload();
withData.videos = [
  { vid: 'v1', account: 'alpha', title: 'old', plays: 10, likes: 1, publish_time: '2026-10-20T08:00:00' },
  { vid: 'v2', account: 'alpha', title: 'old2', plays: 20, likes: 2, publish_time: '2026-10-22T08:00:00' },
  { vid: 'v3', account: 'alpha', title: 'in1', plays: 30, likes: 3, publish_time: '2026-10-27T08:00:00' },
  { vid: 'v4', account: 'alpha', title: 'in2', plays: 40, likes: 4, publish_time: '2026-11-01T08:00:00' }
];
withData.accounts.push({
  account: 'alpha', lang: 'EN', videos: 4, total_plays: 100, total_likes: 10,
  avg_plays: 25, avg_engagement: 10, product_coverage: 0,
  top_tags: [], top_hooks: [], has_scrape: true
});
check('有数据时 derive 正常，series 按天聚合 4 个桶', () => {
  const D = derive(clone(withData));
  assert(D.series.length === 4, 'series.length=' + D.series.length);
  assert(D.series[0].full === '2026-10-20', 'series[0].full=' + D.series[0].full);
  assert(D.series[3].full === '2026-11-01', 'series[3].full=' + D.series[3].full);
});
check('meta.from/to 仍是最早 / 最晚一天', () => {
  const D = derive(clone(withData));
  assert(D.meta.from === '2026-10-20', 'meta.from=' + D.meta.from);
  assert(D.meta.to === '2026-11-01', 'meta.to=' + D.meta.to);
  assert(D.meta.days === 4, 'meta.days=' + D.meta.days);
});
check('7 日窗口：10-27 与 11-01 计入，10-20 / 10-22 不计入（recent7 = 2）', () => {
  const D = derive(clone(withData));
  assert(D.accounts[0].recent7 === 2, 'recent7=' + D.accounts[0].recent7);
});
check('有数据时返回结构与空数据一致（KPI / 账号行 / 趋势桶齐全）', () => {
  const D = derive(clone(withData));
  assert(D.kpis && D.kpis.videos === 0, 'kpis 未原样带出');
  assert(Array.isArray(D.accounts) && D.accounts.length === 1, 'accounts 异常');
  assert(Array.isArray(D.series) && D.series.length === 4, 'series 异常');
  assert(typeof D.baselines.medianPlays === 'number', 'baselines.medianPlays 不是数字');
});

console.log('== 用例 4：bench.js 渲染单元在空数据下不抛异常（T-11 追加 2026-11-05）==');
// 与 static/index.html 一致：bcore.js（window.BS）→ bench.js（window.BU）
w.eval(read('static/bench2/bcore.js'));
w.eval(read('static/bench2/bench.js'));
const BU = w.BU;
check('BU.trend(空数据) 不抛 TypeError，仍产出 trend 区块', () => {
  const D = derive(clone(emptyPayload));
  const html = BU.trend(D, { n: 0 });
  assert(typeof html === 'string' && html.length > 0, 'trend 未返回 HTML');
  assert(html.indexOf('data-unit="trend"') >= 0, '缺少 data-unit="trend"');
});
check('BU.topvideos(空数据) 不抛 TypeError，仍产出 topvideos 区块', () => {
  const D = derive(clone(emptyPayload));
  const html = BU.topvideos(D, {});
  assert(typeof html === 'string' && html.length > 0, 'topvideos 未返回 HTML');
  assert(html.indexOf('data-unit="topvideos"') >= 0, '缺少 data-unit="topvideos"');
});
check('BU.trend / BU.topvideos 在有数据时也正常（口径不变）', () => {
  const D = derive(clone(withData));
  const t = BU.trend(D, { n: 0 });
  const v = BU.topvideos(D, {});
  assert(t.indexOf('data-unit="trend"') >= 0, 'trend 区块缺失');
  assert(v.indexOf('data-unit="topvideos"') >= 0, 'topvideos 区块缺失');
  assert(t.indexOf('2026-10-20') >= 0 && t.indexOf('2026-11-01') >= 0, 'trend 未使用真实日期范围');
});

dom.window.close();
console.log('\n通过 ' + pass + ' / ' + (pass + fail));
process.exit(fail ? 1 : 0);
