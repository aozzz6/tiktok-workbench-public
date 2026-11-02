const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const tkJs = fs.readFileSync('static/tablekit.js', 'utf8');

let fails = 0;
function check(name, fn) {
  try { w.bvReset(); fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

function vid(i, account, plays, daysAgo, products = []) {
  const d = new Date(Date.now() - daysAgo * 864e5);
  const p = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 10:00:00`;
  return { vid: 'v' + i, title: `标题 ${i} 收纳盒`, desc: `文案 ${i} #tag`, plays,
           likes: i * 3, comments: 1, shares: 2, collects: 4, publish_time: p,
           url: `https://www.tiktok.com/@${account}/video/${i}`, products,
           engagement: 0.5, account };
}

const VIDEOS = [];
for (let i = 1; i <= 8; i++) VIDEOS.push(vid(i, 'accA', i * 1000, i * 10, i % 2 ? [{ product_id: 'p1' }] : []));
for (let i = 9; i <= 12; i++) VIDEOS.push(vid(i, 'accB', i * 5000, 1, []));

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = () => ok({});
w.URL.createObjectURL = () => 'blob:x';
w.URL.revokeObjectURL = () => {};
w.eval(tkJs);        // 表格增强层（分页条 / 排序 / 列宽 / 图表）—— 必须早于 app.js
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

const d = w.document;
w.renderBench({
  ts: Date.now() / 1000,
  kpis: { accounts: 2, videos: VIDEOS.length, total_plays: 1, avg_engagement: 0.5,
          with_products: 4, product_coverage: 33.3 },
  accounts: [{ account: 'accA', videos: 8, total_plays: 1, avg_plays: 1,
               avg_engagement: 0.5, product_coverage: 50, top_tags: [], top_hooks: [] }],
  account_options: [{ account: 'accA' }, { account: 'accB' }],
  videos: VIDEOS,
  patterns: {},
  takeaways: [],
});

const rows = () => d.querySelectorAll('#bench-videos tbody tr');

check('默认渲染：全部 12 条、按播放降序', () => {
  assert(rows().length === 12, '行数 ' + rows().length);
  const first = rows()[0].textContent;
  assert(/accB/.test(first), '首行应是播放最高的 accB，实得 ' + first.slice(0, 30));
  assert(/12 \/ 12/.test(d.querySelector('#bv-count').textContent), '计数文案不对');
});

check('筛选：播放 ≥ 1 万', () => {
  w.bvChange('minPlays', '10000');
  assert(rows().length === 4, 'accB 四条均 ≥1 万，实得 ' + rows().length);
  w.bvReset();
  assert(rows().length === 12, '重置后应回到 12 条');
});

check('筛选：仅带小黄车', () => {
  w.bvChange('prod', 'yes');
  assert(rows().length === 4, '应剩 4 条，实得 ' + rows().length);
  w.bvChange('prod', 'no');
  assert(rows().length === 8, '未挂车应 8 条，实得 ' + rows().length);
  w.bvReset();
});

check('筛选：近 7 天', () => {
  w.bvChange('days', '7');
  const n = rows().length;
  assert(n === 4, '近 7 天应只剩 accB 4 条，实得 ' + n);
  w.bvReset();
});

check('时间筛选：选项顺序 全部时间 → 近 1 天 → 近 3 天 → 近 7 天 → 近 30 天 → 近 90 天', () => {
  const opts = Array.from(d.querySelectorAll('#bv-days option'))
    .map((o) => `${o.value}|${o.textContent.trim()}`);
  const want = ['|全部时间', '1|近 1 天', '3|近 3 天', '7|近 7 天', '30|近 30 天', '90|近 90 天'];
  assert(JSON.stringify(opts) === JSON.stringify(want), '实得 ' + JSON.stringify(opts));
});

check('筛选：近 3 天（新增，位置在全部时间之后、近 7 天之前）', () => {
  w.bvChange('days', '3');
  const n = rows().length;
  // accA 最近一条为 10 天前，近 3 天只应剩 accB 的 4 条
  assert(n === 4, '近 3 天应只剩 accB 4 条，实得 ' + n);
  w.bvChange('days', '');
  assert(rows().length === 12, '切回全部时间应回 12 条');
});

check('搜索：标题命中 3 条 + 大小写无关', () => {
  w.bvChange('q', '标题 1');
  // 标题 1、10、11、12 共 4 条
  assert(rows().length === 4, '应命中 4 条，实得 ' + rows().length);
  w.bvChange('q', '收纳盒');
  assert(rows().length === 12, '关键词全命中应 12 条');
  w.bvReset();
});

check('排序：播放升序 / 发布时间新→旧', () => {
  w.bvChange('sort', 'plays_asc');
  assert(/accA/.test(rows()[0].textContent), '升序首行应是 accA');
  w.bvChange('sort', 'time_desc');
  const t0 = rows()[0].textContent;
  assert(/ accB|accB/.test(t0), '最新应是 accB，实得 ' + t0.slice(0, 20));
  w.bvReset();
});

check('分页：>50 条出现页码分页，翻页换批且不重复', () => {
  const many = [];
  for (let i = 1; i <= 120; i++) many.push(vid(1000 + i, 'accC', i, 1));
  w.renderBench({ kpis: {}, accounts: [], account_options: [], videos: many,
                  patterns: {}, takeaways: [] });
  assert(rows().length === 50, '首批应 50 行，实得 ' + rows().length);
  const pager = () => d.querySelector('#bv-pager .tk-pager');
  assert(pager(), '缺分页条');
  assert(/共 120 条/.test(pager().textContent), '分页条总数不对：' + pager().textContent);
  // 用行上的 data-vid 比对（账号列同账号时文本相同，不能当唯一标识）
  const firstOf = () => [...rows()].map((tr) => tr.getAttribute('data-vid')).join(',');
  const page1 = firstOf();
  const btn2 = [...pager().querySelectorAll('[data-pg]')].find((b) => b.getAttribute('data-pg') === '2');
  assert(btn2, '缺第 2 页按钮');
  btn2.click();
  assert(rows().length === 50, '第 2 页应 50 行，实得 ' + rows().length);
  assert(/第 2 \/ 3 页/.test(pager().textContent), '页码未更新：' + pager().textContent);
  assert(firstOf() !== page1, '第 2 页与第 1 页内容相同（分页未生效）');
  // 每页条数：25/页 → 行数跟着变
  const selSize = d.querySelector('#bv-pager select');
  selSize.value = '25';
  selSize.dispatchEvent(new w.Event('change'));
  assert(rows().length === 25, '切 25/页应 25 行，实得 ' + rows().length);
  assert(/共 120 条/.test(pager().textContent), '换页大小后总数丢失');
});

check('空结果：给放宽筛选提示，不崩', () => {
  w.bvChange('q', '不存在的关键词zzz');
  const t = d.querySelector('#bench-videos').textContent;
  assert(/没有符合条件/.test(t), '缺空态文案');
  assert(/无匹配视频/.test(d.querySelector('#bv-summary').textContent), '摘要未置空态');
  w.bvReset();
});

check('CSV 导出：触发下载并提示条数', () => {
  let clicked = 0;
  const origClick = w.HTMLAnchorElement.prototype.click;
  w.HTMLAnchorElement.prototype.click = function () { clicked++; };
  w.bvExportCsv();
  w.HTMLAnchorElement.prototype.click = origClick;
  assert(clicked === 1, '未触发下载点击');
  assert(/已导出/.test(d.querySelector('#toast').textContent), '缺导出提示');
});

check('表格：标题为可点外链（新窗口 + noopener）', () => {
  const a = d.querySelector('#bench-videos tbody td a');
  assert(a, '缺链接');
  assert(a.target === '_blank', 'target 应为 _blank');
  assert(/noopener/.test(a.getAttribute('rel') || ''), '缺 rel=noopener');
});

dom.window.close();
console.log(`\n通过 ${12 - fails} / 12`);
process.exit(fails ? 1 : 0);
