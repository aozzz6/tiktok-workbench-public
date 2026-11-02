/* tablekit.test.js — 表格增强层（列宽 / 排序 / 分页 / 展开 / 对比 / 图表联动 / 空态）
   验证的是「表现层接管后，业务口径与交互契约仍然成立」。 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const tkJs = fs.readFileSync('static/tablekit.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');

const dom = new JSDOM(html, {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  url: 'http://127.0.0.1:8787/',
});
const w = dom.window;
const d = w.document;
w.EventSource = class { close() {} };
w.URL.createObjectURL = () => 'blob:x';
w.URL.revokeObjectURL = () => {};
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
w.eval(tkJs);
w.eval(appJs.replace(/\nboot\(\);?/g, '').replace(/^setInterval\(.*\);$/gm, ''));

let fails = 0;
let total = 0;
function check(name, fn) {
  total++;
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
// jsdom 里 SVGElement 没有 .click()，统一用事件派发
function fire(el) {
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
}

/* ── 测试数据：30 条视频 / 3 账号，覆盖分档与来源 ── */
const VIDEOS = [];
for (let i = 0; i < 30; i++) {
  VIDEOS.push({
    vid: 'v' + i,
    account: 'acc' + (i % 3),
    title: '收纳盒 ' + i,
    desc: '#fyp #tag' + i,
    plays: (i + 1) * 2000,
    likes: i + 1, comments: i, shares: i % 3, collects: i % 4,
    engagement: 1.2,
    publish_time: `2026-10-${String(1 + (i % 20)).padStart(2, '0')} 10:00:00`,
    url: `https://www.tiktok.com/@acc${i % 3}/video/v${i}`,
    products: i % 4 ? [{ product_id: 'p' + i, title: '商品 ' + i }] : [],
    source: i % 5 === 0 ? 'ledger' : 'scrape',
  });
}
const ACCOUNTS = [
  { account: 'acc0', videos: 10, total_plays: 100000, avg_plays: 10000, avg_engagement: 2.1,
    product_coverage: 75, top_tags: ['#fyp'], top_hooks: ['wait'], lang: 'de' },
  { account: 'acc1', videos: 12, total_plays: 240000, avg_plays: 20000, avg_engagement: 3.4,
    product_coverage: 50, top_tags: ['#tiktok'], top_hooks: ['look'], lang: 'en' },
  { account: 'acc2', videos: 8, total_plays: 60000, avg_plays: 7500, avg_engagement: 1.1,
    product_coverage: 25, top_tags: [], top_hooks: [] },
];

function render() {
  w.renderBench({
    ts: Date.now() / 1000,
    kpis: { accounts: 3, videos: VIDEOS.length, total_plays: 400000, avg_engagement: 2.2,
            with_products: 22, product_coverage: 73, ledger_only: 6 },
    accounts: ACCOUNTS,
    account_options: ACCOUNTS.map((a) => ({ account: a.account })),
    videos: VIDEOS,
    patterns: { play_buckets: [], top_tags: [], hook_words: [], products_top: [], top_videos: [] },
    takeaways: [],
    });
}
const rows = () => [...d.querySelectorAll('#bench-videos tbody tr:not(.tk-exp)')];
const ths = () => [...d.querySelectorAll('#bench-videos thead th')];

render();

/* ── 1. 列宽 ── */
check('列宽：12 个表头各挂一个拖拽手柄', () => {
  assert(ths().length === 12, '表头数 ' + ths().length);
  const grips = d.querySelectorAll('#bench-videos thead .tk-grip');
  assert(grips.length === 12, '手柄数 ' + grips.length);
});

check('列宽：拖动后写入 px 宽度（table-layout 已切 fixed）', () => {
  const th = ths()[2];
  const grip = th.querySelector('.tk-grip');
  const down = new w.Event('pointerdown', { bubbles: true });
  down.clientX = 200;
  grip.dispatchEvent(down);
  const move = new w.Event('pointermove', { bubbles: true });
  move.clientX = 320;
  w.dispatchEvent(move);
  w.dispatchEvent(new w.Event('pointerup', { bubbles: true }));
  assert(/px$/.test(th.style.width), '未写入宽度：' + th.style.width);
  assert(parseInt(th.style.width, 10) >= 64, '宽度低于最小宽度：' + th.style.width);
  assert(d.querySelector('#bench-videos').classList.contains('tk-resizable'), '未切 fixed 布局');
});

/* ── 2. 排序 ── */
check('排序：点「播放」表头 → 切升序并重排（首行变最低播放）', () => {
  render();                              // 复位到 plays_desc
  const playOf = (tr) => (VIDEOS.find((v) => v.vid === tr.getAttribute('data-vid')) || {}).plays;
  const before = playOf(rows()[0]);
  ths()[2].click();
  const seq = rows().map(playOf);
  assert(before !== seq[0], '行序未变化（仍以最高播放开头）');
  assert(ths()[2].classList.contains('tk-asc'), '缺升序箭头');
  assert(seq.every((p, i) => i === 0 || seq[i - 1] <= p), '升序结果不对：' + seq.join(','));
});

check('排序：再点一次切回降序', () => {
  ths()[2].click();
  assert(ths()[2].classList.contains('tk-desc'), '缺降序箭头');
});

/* ── 3. 分页 ── */
check('分页：30 条 = 1 页（50/页），页码信息正确', () => {
  render();
  const p = d.querySelector('#bv-pager .tk-pager');
  assert(p, '缺分页条');
  assert(/共 30 条/.test(p.textContent), p.textContent);
  assert(/第 1 \/ 1 页/.test(p.textContent), p.textContent);
  assert(rows().length === 30, '行数 ' + rows().length);
});

/* ── 4. 行展开 ── */
check('展开：点 ▸ 插入详情行，含视频 ID / 原始链接 / 商品', () => {
  const tr = rows()[0];
  const vid = tr.getAttribute('data-vid');
  tr.querySelector('.tk-exp-btn').click();
  const exp = d.querySelector('#bench-videos tbody tr.tk-exp');
  assert(exp, '缺展开行');
  assert(/视频 ID/.test(exp.textContent), '缺视频 ID');
  assert(exp.textContent.includes(vid), '详情未对应本条视频');
  assert(/原始链接/.test(exp.textContent), '缺原始链接');
  assert(exp.previousElementSibling === tr, '展开行未紧跟原行');
});

check('展开：再点一次收起（不留残行）', () => {
  rows()[0].querySelector('.tk-exp-btn').click();
  assert(!d.querySelector('#bench-videos tbody tr.tk-exp'), '未收起');
});

/* ── 5. 多选对比 ── */
check('对比：选中两行 → 出现指标对比条，最高/最低标色', () => {
  const rs = rows();
  rs[0].click();
  rs[1].click();
  const cmp = d.querySelector('#bv-cmp .tk-cmp');
  assert(cmp, '缺对比条');
  assert(/对比/.test(cmp.textContent), '缺标题');
  assert(cmp.querySelectorAll('td.hi').length >= 1, '缺最高值标色');
  assert(rs[0].classList.contains('tk-sel') && rs[1].classList.contains('tk-sel'), '行未高亮');
});

check('对比：清除选择后对比条消失、行取消高亮', () => {
  d.querySelector('#bv-cmp [data-tk-cmp-clear]').click();
  render();
  assert(!d.querySelector('#bv-cmp .tk-cmp'), '对比条未清除');
  assert(!d.querySelector('#bench-videos tbody tr.tk-sel'), '行仍高亮');
});

/* ── 6. 图表 ── */
check('图表：分档清单 + 环形 + 折线都渲染', () => {
  render();
  const bands = d.querySelectorAll('#bv-viz-band .tk-band');
  assert(bands.length === 4, '分档行数应为 4，实得 ' + bands.length);
  assert(d.querySelector('#bv-viz-band .tk-band .tk-band-n'), '分档缺数值列');
  assert(d.querySelector('#bv-viz-acct svg.tk-donut'), '缺环形图');
  assert(d.querySelector('#bv-viz-day svg.tk-line'), '缺折线图');
  assert(d.querySelector('#bv-viz-day svg .tk-ln-grid'), '折线缺网格参照线');
});

check('联动：点「1万-10万」柱 → 表格只剩该档（26 条）', () => {
  const bars = d.querySelectorAll('#bv-viz-band .tk-band');
  fire(bars[2]);                        // 1万-10万：plays>=1e4 → i+1>=5 → 26 条
  assert(/26 \/ 30/.test(d.querySelector('#bv-count').textContent),
    '计数未跟着筛：' + d.querySelector('#bv-count').textContent);
  assert(rows().length === 26, '行数 ' + rows().length);
  assert(/图筛/.test(d.querySelector('#bv-summary').textContent), '摘要未标注图筛');
});

check('联动：再点同一柱取消筛选，恢复 30 条', () => {
  fire(d.querySelectorAll('#bv-viz-band .tk-band')[2]);
  assert(rows().length === 30, '行数 ' + rows().length);
});

check('联动：点账号环 → 只留该账号；点环形图例同样生效', () => {
  const legend = d.querySelector('#bv-viz-acct .tk-dn-legend [data-k="acc1"]');
  assert(legend, '缺账号图例');
  legend.click();
  const left = rows().every((tr) => tr.cells[0].textContent.includes('acc1'));
  assert(left, '未按账号过滤');
  d.querySelector('#bv-viz-acct .tk-dn-legend [data-k="acc1"]').click();
  assert(rows().length === 30, '取消账号筛选后未恢复');
});

/* ── 7. 空状态 ── */
check('空态：无命中 → 统一空态块 + 重置入口', () => {
  w.bvChange('q', '不存在的关键词zzz');
  const emp = d.querySelector('#bench-videos .tk-empty');
  assert(emp, '缺空态块');
  assert(/没有符合条件/.test(emp.textContent), '空态文案');
  assert(emp.querySelector('[onclick="bvReset()"]'), '缺重置按钮');
  assert(/无匹配视频/.test(d.querySelector('#bv-summary').textContent), '摘要未置空态');
  w.bvReset();
});

/* ── 8. 账号对比表 ── */
check('账号表：8 个列宽手柄 + 点表头排序生效', () => {
  render();
  const at = d.querySelector('#bench-accounts');
  assert(at.querySelectorAll('thead .tk-grip').length === 8,
    '手柄数 ' + at.querySelectorAll('thead .tk-grip').length);
  const first = () => at.querySelector('tbody tr').getAttribute('data-acct');
  const before = first();
  at.querySelectorAll('thead th')[2].click();     // 总播放（默认 desc），点一下切 asc
  const after = first();
  assert(before !== after, '账号表未重排：' + before + ' → ' + after);
  assert(at.querySelectorAll('thead th.tk-asc, thead th.tk-desc').length === 1, '排序箭头数量不对');
});

check('账号表：点行高亮并与账号图联动', () => {
  const at = d.querySelector('#bench-accounts');
  const tr = at.querySelector('tbody tr');
  const acc = tr.getAttribute('data-acct');
  tr.click();
  assert(tr.classList.contains('tk-sel'), '行未高亮');
  const bar = d.querySelector(`#bv-accchart .ba-bar[data-acc="${acc}"]`);
  if (bar) {
    assert(/已选/.test(d.querySelector('#bv-accchart .bv-legend').textContent), '图例未标注已选');
  }
  tr.click();
});

/* ── 9. 台账表（异步） ── */
const SHEET = {
  header: ['日期', '@账号', '播放'],
  // 故意乱序：验证排序真的重排，而不是「碰巧原样」
  rows: [['2026-10-17', '@acc2', '9800'], ['2026-10-18', '@acc1', '1200'], ['2026-10-16', '@acc3', '5400']],
  total: 3, file: 'demo.xlsx', hidden_cols: [], checks: [],
};

(async function sheetChecks() {
  w.fetch = (u) => {
    if (String(u).includes('/api/ledger/sheet')) return Promise.resolve({ ok: true, json: () => Promise.resolve(SHEET) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  await w.loadSheetInto('#sheet-host', 'demo', 50);

  check('台账：表头可排序 + 数字列右对齐', () => {
    const t = d.querySelector('#sheet-host table');
    assert(t, '缺台账表');
    assert(t.querySelectorAll('thead th.tk-sortable').length === 3, '表头未标记可排序');
    assert(t.querySelectorAll('thead .tk-grip').length === 3, '缺列宽手柄');
    const numCell = [...t.querySelectorAll('tbody td')].find((td) => td.textContent === '1200');
    assert(numCell && numCell.classList.contains('num'), '数字列未右对齐');
  });

  check('台账：点表头排序 → aria-sort 变化，行序随之改变', () => {
    const t = d.querySelector('#sheet-host table');
    const before = [...t.querySelectorAll('tbody tr')].map((r) => r.cells[2].textContent).join(',');
    t.querySelectorAll('thead th')[2].click();
    const after = [...t.querySelectorAll('tbody tr')].map((r) => r.cells[2].textContent).join(',');
    assert(before !== after, '排序未生效：' + before + ' → ' + after);
    assert(['ascending', 'descending'].includes(t.querySelectorAll('thead th')[2].getAttribute('aria-sort')),
      'aria-sort 未更新');
  });

  check('台账：搜索无命中 → 空态提示 + 清空入口', () => {
    w.filterSheet('zzz-not-here');
    const emp = d.querySelector('#sheet-host .tk-empty');
    assert(emp, '缺空态');
    assert(/没有匹配的行/.test(emp.textContent), '空态文案');
    w.filterSheet('');
    assert(d.querySelectorAll('#sheet-host tbody tr').length === 3, '清空后未恢复');
  });

  dom.window.close();
  console.log(`\n通过 ${total - fails} / ${total}`);
  process.exit(fails ? 1 : 0);
})();
