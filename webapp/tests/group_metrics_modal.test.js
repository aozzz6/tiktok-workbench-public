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
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const METRICS = {
  group: { id: 'g1', name: '钟如妹账号', count: 3 },
  accounts: [
    { account: 'baliee_official', videos: 29, total_plays: 53000, avg_plays: 1827, viral: 2,
      recent7d: 6, last_pub: '2026-10-18 09:12:00', stale_days: 1, product_coverage: 62.5, tags: ['今日更新'] },
    { account: 'chloe.shop_de', videos: 10, total_plays: 7700, avg_plays: 770, viral: 0,
      recent7d: 0, last_pub: '2026-09-28 10:00:00', stale_days: 21, product_coverage: 30, tags: ['无爆款', '21天未更新'] },
    { account: 'lucas.style', videos: 5, total_plays: 88000, avg_plays: 17600, viral: 4,
      recent7d: 3, last_pub: '2026-10-19 08:00:00', stale_days: 0, product_coverage: 100, tags: ['今日更新'] },
  ],
  low_value: ['chloe.shop_de'],
  stale: ['chloe.shop_de'],
  empty: false,
};

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;
w.fetch = (u) => {
  u = String(u);
  if (u.includes('/metrics')) return ok(METRICS);
  if (u.includes('/api/groups')) return ok({
    groups: [
      { id: 'default', name: '默认分组', builtin: true, members: [{ account: 'lonely_acc' }] },
      { id: 'g1', name: '钟如妹账号', members: [{ account: 'baliee_official' }, { account: 'chloe.shop_de' }, { account: 'lucas.style' }] },
    ],
  });
  if (u.includes('/api/overview')) return ok({
    accounts: [
      { account: 'baliee_official', media_type: 'video', audio_count: 29, transcript_count: 29, severity: 'ok', scrape_items: 29, last_success_date: '2026-10-18', active: false },
      { account: 'chloe.shop_de', media_type: 'video', audio_count: 10, transcript_count: 4, severity: 'error', scrape_items: 10, last_success_date: '2026-09-28', active: false },
      { account: 'lucas.style', media_type: 'video', audio_count: 5, transcript_count: 5, severity: 'ok', scrape_items: 5, last_success_date: '2026-10-19', active: true },
      { account: 'lonely_acc', media_type: 'video', audio_count: 1, transcript_count: 0, severity: 'ok', scrape_items: 1, active: false },
    ],
    pending_translation: { total: 0 }, active_runs: [], runs: [],
  });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*15000\);/mg, ''));

(async () => {
  await w.loadOverview(false);
  await new Promise((r) => setTimeout(r, 50));
  await w.toggleGroupMetrics('g1');
  await new Promise((r) => setTimeout(r, 30));
  const d = w.document;
  const box = d.querySelector('#modal-content');

  check('组内指标渲染在全宽弹窗，不再塞进泳道列内', () => {
    assert(!d.querySelector('#modal').classList.contains('hidden'), 'modal 未打开');
    assert(!d.querySelector('.lane .grp-metrics'), '泳道内仍有内嵌指标块');
    assert(/钟如妹账号/.test(d.querySelector('#modal-title').textContent), '标题缺组名');
    assert(box.querySelector('.gm-tbl-wrap'), '缺表格容器');
  });

  check('组级 KPI 四项 + 健康摘要只给计数（不堆账号名）', () => {
    const kpis = box.querySelectorAll('.gm-kpis .kpi');
    assert(kpis.length === 4, 'KPI 数量 ' + kpis.length);
    const hl = box.querySelector('.gm-healthline').textContent;
    assert(/低价值 1/.test(hl), hl);
    assert(!/chloe\.shop_de/.test(hl), '健康摘要不应列出全部账号名');
    assert(box.querySelector('.gm-healthline .gm-h[title*="chloe.shop_de"]'), '账号名单应在 title 里');
  });

  check('表格 9 列齐全，账号名完整显示（不截断成 8 字）', () => {
    const ths = [...box.querySelectorAll('thead th')];
    assert(ths.length === 9, '表头列数 ' + ths.length);
    assert(ths[0].textContent.trim().startsWith('账号'), ths[0].textContent);
    const names = [...box.querySelectorAll('.gm-row td.acc')].map((td) => td.textContent);
    assert(names.includes('baliee_official') && names.includes('chloe.shop_de'), names.join(','));
  });

  check('默认按总播放降序，组均行在首行且不可点', () => {
    const first = box.querySelector('.gm-row td.acc').textContent;
    assert(first === 'lucas.style', '首行应为播放最高的 lucas.style，实际 ' + first);
    const avg = box.querySelector('.gm-avg');
    assert(avg && /组均/.test(avg.textContent), '缺组均行');
    assert(!avg.getAttribute('onclick'), '组均行不应可点');
  });

  check('点表头可按其它列排序（视频先降序 29/10/5，再点升序 5/10/29）', () => {
    w.gmSortBy('videos');
    const desc = [...box.querySelectorAll('.gm-row')].map((tr) => Number(tr.children[1].textContent));
    assert(desc.join(',') === '29,10,5', '数字列首次点击应降序，实际 ' + desc.join(','));
    w.gmSortBy('videos');
    const asc = [...box.querySelectorAll('.gm-row')].map((tr) => Number(tr.children[1].textContent));
    assert(asc.join(',') === '5,10,29', '再点应升序，实际 ' + asc.join(','));
  });

  check('点行打开账号台账（行自带 onclick）', () => {
    const tr = box.querySelector('.gm-row');
    assert(/openAccountLedger/.test(tr.getAttribute('onclick')), tr.getAttribute('onclick'));
  });

  console.log(fails ? `${fails} FAILED` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
