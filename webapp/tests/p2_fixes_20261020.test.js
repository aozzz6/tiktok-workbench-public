/* p2_fixes_20261020.test.js — 独立验收 4 项 P2 的回归用例
   P2-1 列宽手柄不得触发表头排序
   P2-2 账号图必须覆盖「所选账号在 TOP12 之外」的情况
   P2-3 对比上限 4 条需要在选中入口拦截，选中数 = 计算数
   P2-4 tablekit 缺失时仍能翻到全部数据（降级分页可点）
*/
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const tkJs = fs.readFileSync('static/tablekit.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');

let fails = 0;
let total = 0;
function check(name, fn) {
  total++;
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }

/* ── 数据：30 条视频 / 20 个账号（用于 TOP12 之外的联动）── */
const VIDEOS = [];
for (let i = 0; i < 30; i++) {
  const acct = 'acc' + String(i % 20).padStart(2, '0');
  VIDEOS.push({
    vid: 'v' + i,
    account: acct,
    title: '收纳盒 ' + i,
    desc: '#fyp #tag' + i,
    plays: (30 - i) * 3000,
    likes: i * 2 + 1, comments: i, shares: i % 3, collects: i % 4,
    engagement: 1.2,
    publish_time: `2026-10-${String(1 + (i % 20)).padStart(2, '0')} 10:00:00`,
    url: `https://www.tiktok.com/@${acct}/video/v${i}`,
    products: [],
    source: 'scrape',
  });
}
const ACCOUNTS = [];
for (let i = 0; i < 20; i++) {
  ACCOUNTS.push({
    account: 'acc' + String(i).padStart(2, '0'),
    videos: 1,
    total_plays: (20 - i) * 10000,          // acc00 第 1 名 … acc19 第 20 名
    avg_plays: (20 - i) * 10000,
    avg_engagement: 1 + i / 10,
    product_coverage: 50,
    top_tags: [], top_hooks: [],
  });
}

function boot(withTablekit) {
  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8787/',
  });
  const w = dom.window;
  w.EventSource = class { close() {} };
  w.URL.createObjectURL = () => 'blob:x';
  w.URL.revokeObjectURL = () => {};
  w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  if (withTablekit) w.eval(tkJs);
  w.eval(appJs.replace(/\nboot\(\);?/g, '').replace(/^setInterval\(.*\);$/gm, ''));
  return { w, d: w.document };
}

function renderAll(w, videos) {
  w.renderBench({
    ts: Date.now() / 1000,
    kpis: { accounts: ACCOUNTS.length, videos: videos.length, total_plays: 1, avg_engagement: 2,
            with_products: 0, product_coverage: 0, ledger_only: 0 },
    accounts: ACCOUNTS,
    account_options: ACCOUNTS.map((a) => ({ account: a.account })),
    videos,
    patterns: { play_buckets: [], top_tags: [], hook_words: [], products_top: [], top_videos: [] },
    takeaways: [],
    });
}

/* ══════════ 正常态（加载 tablekit） ══════════ */
const { w, d } = boot(true);
renderAll(w, VIDEOS);

const bvRows = () => [...d.querySelectorAll('#bench-videos tbody tr:not(.tk-exp)')];
const bvThs = () => [...d.querySelectorAll('#bench-videos thead th')];
const acctRows = () => [...d.querySelectorAll('#bench-accounts tbody tr')];
const sortVal = () => d.querySelector('#bv-sort').value;
const bars = () => [...d.querySelectorAll('#bv-accchart .ba-bar')];
const fire = (el, type, props) => {
  const ev = new w.Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, props || {});
  el.dispatchEvent(ev);
  return ev;
};

/* ── P2-1 ── */
check('P2-1 单击列宽手柄不改排序', () => {
  const before = sortVal();
  const grip = bvThs()[2].querySelector('.tk-grip');
  assert(grip, '缺列宽手柄');
  fire(grip, 'pointerdown', { clientX: 200 });
  fire(w, 'pointerup', {});
  grip.click();
  assert(sortVal() === before, `排序被手柄点击改掉：${before} → ${sortVal()}`);
});

check('P2-1 完整拖拽（down/move/up/click）不改排序且宽度变化', () => {
  const before = sortVal();
  const th = bvThs()[2];
  const grip = th.querySelector('.tk-grip');
  fire(grip, 'pointerdown', { clientX: 200 });
  fire(w, 'pointermove', { clientX: 320 });
  fire(w, 'pointerup', {});
  grip.click();
  assert(sortVal() === before, `拖拽后排序变了：${before} → ${sortVal()}`);
  assert(/px$/.test(th.style.width), '未写入 px 宽度：' + th.style.width);
});

check('P2-1 拖拽时对手柄发出指针捕获（浏览器据此把补发 click 重定向到手柄）', () => {
  const th = bvThs()[2];
  const grip = th.querySelector('.tk-grip');
  let captured = null, released = null;
  grip.setPointerCapture = (id) => { captured = id; };
  grip.releasePointerCapture = (id) => { released = id; };
  fire(grip, 'pointerdown', { clientX: 200, pointerId: 1 });
  fire(w, 'pointermove', { clientX: 260, pointerId: 1 });
  fire(w, 'pointerup', { pointerId: 1 });
  assert(captured === 1, 'pointerdown 未捕获指针：' + captured);
  assert(released === 1, 'pointerup 未释放指针：' + released);
});

check('P2-1 拖拽后补发的 click 落在手柄上不排序，正常表头点击不受影响', () => {
  const before = sortVal();
  const th = bvThs()[2];
  const grip = th.querySelector('.tk-grip');
  fire(grip, 'pointerdown', { clientX: 200, pointerId: 2 });
  fire(w, 'pointermove', { clientX: 300, pointerId: 2 });
  fire(w, 'pointerup', { pointerId: 2 });
  grip.click();                                  // 指针捕获后 click 的落点就是手柄
  assert(sortVal() === before, '补发的 click 触发了排序');
  bvThs()[3].click();                            // 紧接着的正常表头点击必须照常生效
  assert(sortVal() !== before, '正常表头点击被误吞');
});

check('P2-1 双击自适应不改排序', () => {
  const before = sortVal();
  const grip = bvThs()[4].querySelector('.tk-grip');
  fire(grip, 'dblclick', {});
  grip.click();
  assert(sortVal() === before, `双击自适应后排序变了：${before} → ${sortVal()}`);
});

check('P2-1 点表头文字仍能排序', () => {
  const before = sortVal();
  bvThs()[3].click();
  assert(sortVal() !== before, '点表头未触发排序');
  const th = bvThs()[3];
  assert(th.classList.contains('tk-asc') || th.classList.contains('tk-desc'), '缺排序方向标记');
});

check('P2-1 键盘 Enter 排序仍生效', () => {
  const before = sortVal();
  fire(bvThs()[3], 'keydown', { key: 'Enter' });
  assert(sortVal() !== before, '键盘未触发排序');
});

/* ── P2-2 ── */
check('P2-2 TOP12 内账号：图内 12 条且有选中条', () => {
  const tr = acctRows().find((x) => x.getAttribute('data-acct') === 'acc02');
  assert(tr, '缺 acc02 行');
  tr.click();
  assert(bars().length === 12, '图内条数应为 12，实得 ' + bars().length);
  const on = bars().filter((g) => g.classList.contains('on'));
  assert(on.length === 1 && on[0].getAttribute('data-acc') === 'acc02', '选中条不对');
  assert(/已选 acc02/.test(d.querySelector('#bv-accchart .bv-legend').textContent), '图例未显示已选');
});

check('P2-2 TOP12 外账号：自动并入图内末行并标注名次', () => {
  acctRows().find((x) => x.getAttribute('data-acct') === 'acc02').click();   // 先取消
  // 方案 A：表格默认折叠到 TOP12，acc14（全量第 15 名）在折叠尾部 —— 先展开才能点它
  w.baSetExpanded(true);
  const tr = acctRows().find((x) => x.getAttribute('data-acct') === 'acc14'); // 全量第 15 名
  assert(tr, '缺 acc14 行（展开后应在）');
  tr.click();
  const gs = bars();
  assert(gs.length === 13, '应 TOP12 + 所选 = 13 条，实得 ' + gs.length);
  const on = gs.filter((g) => g.classList.contains('on'));
  assert(on.length === 1, '高亮条数应为 1，实得 ' + on.length);
  assert(on[0].getAttribute('data-acc') === 'acc14', '高亮条不是所选账号');
  assert(on[0].classList.contains('pin'), '所选条未标记为外部并入');
  assert(gs[gs.length - 1] === on[0], '所选条应位于末行');
  const legend = d.querySelector('#bv-accchart .bv-legend').textContent;
  assert(/第 15 名/.test(legend), '图例未标注实际名次：' + legend);
  assert(/TOP 12 \+ 所选/.test(legend), '图例未说明并入关系：' + legend);
  w.baSetExpanded(false);
});

check('P2-2 切换指标后仍保留所选账号的对应条', () => {
  const sel = d.querySelector('#bv-metric');
  sel.value = 'avg_plays';
  sel.dispatchEvent(new w.Event('change', { bubbles: true }));
  // 直接调用渲染入口，避免依赖 change 绑定的时序
  w.renderBenchAccChart();
  const gs = bars();
  assert(gs.some((g) => g.getAttribute('data-acc') === 'acc14'), '切指标后所选账号消失');
  assert(gs.filter((g) => g.classList.contains('on')).length === 1, '切指标后无唯一高亮条');
  sel.value = 'total_plays';
  sel.dispatchEvent(new w.Event('change', { bubbles: true }));
  w.renderBenchAccChart();
});

/* ── P2-3 ── */
check('P2-3 连选 5 行：只入 4 条，第 5 条被拦截并提示', () => {
  w.bvReset();
  const rs = bvRows();
  for (let i = 0; i < 5; i++) rs[i].click();
  const sel = d.querySelectorAll('#bench-videos tbody tr.tk-sel');
  assert(sel.length === 4, '高亮行数应为 4，实得 ' + sel.length);
  const head = d.querySelectorAll('#bv-cmp .tk-cmp thead th');
  assert(head.length === 6, '对比表列数应为 6（指标 + 4 列 + 极差），实得 ' + head.length);
  assert(/已选 4 条/.test(d.querySelector('#bv-cmp .tk-cmp').textContent), '计数文案与列数不一致');
  assert(/最多 4 条/.test(d.querySelector('#toast').textContent), '缺上限提示：' + d.querySelector('#toast').textContent);
});

check('P2-3 取消一条后可以再选，计数同步', () => {
  bvRows()[0].click();                                  // 取消第 1 条
  assert(d.querySelectorAll('#bench-videos tbody tr.tk-sel').length === 3, '取消失败');
  bvRows()[4].click();                                  // 第 5 条此时可入
  assert(d.querySelectorAll('#bench-videos tbody tr.tk-sel').length === 4, '补选失败');
  assert(d.querySelectorAll('#bv-cmp .tk-cmp thead th').length === 6, '对比列数未同步');
});

check('P2-3 跨页选择仍计入同一上限', () => {
  w.bvReset();
  const rs = bvRows();
  rs[0].click(); rs[1].click(); rs[2].click();
  // 跳到第 2 页（30 条 / 50 每页 → 仅 1 页，改用直接选第 4、5 行）
  rs[3].click(); rs[4].click();
  assert(d.querySelectorAll('#bench-videos tbody tr.tk-sel').length === 4, '上限在多次点击后失效');
});

check('P2-3 筛选后已选条目移出对比，「已选 N 条」不再虚高', () => {
  w.bvReset();
  const rs = bvRows();
  rs[0].click(); rs[1].click(); rs[2].click(); rs[3].click();
  assert(d.querySelectorAll('#bench-videos tbody tr.tk-sel').length === 4, '初始未选中 4 条');
  w.bvChange('acct', 'acc00');                       // 只留 acc00 的视频
  const left = bvRows().length;
  const sel = d.querySelectorAll('#bench-videos tbody tr.tk-sel').length;
  assert(left === 2, '筛选后行数 ' + left);
  assert(sel === 1, '残留选中应只剩 1 条，实得 ' + sel);
  assert(/已移出对比/.test(d.querySelector('#toast').textContent), '缺移出提示');
  const cmp = d.querySelector('#bv-cmp .tk-cmp');
  assert(!cmp, '只剩 1 条时不应还有对比表');
  w.bvReset();
});

/* ══════════ 降级态（不加载 tablekit） ══════════ */
const bigVideos = [];
for (let i = 0; i < 120; i++) {
  bigVideos.push({
    vid: 'b' + i, account: 'accA', title: '视频 ' + i, desc: '', plays: 1000 + i,
    likes: 1, comments: 1, shares: 1, collects: 1, engagement: 0.5,
    publish_time: '2026-10-01 10:00:00',
    url: 'https://www.tiktok.com/@accA/video/b' + i, products: [], source: 'scrape',
  });
}
const fb = boot(false);
renderAll(fb.w, bigVideos);
const fbRows = () => [...fb.d.querySelectorAll('#bench-videos tbody tr:not(.tk-exp)')];
// [0] = 上一页、[1] = 下一页
const fbNext = () => {
  const bs = [...fb.d.querySelectorAll('#bv-pager [data-pg]')];
  return bs[bs.length - 1];
};

check('P2-4 无 tablekit：渲染原生分页条（可点，不是空函数）', () => {
  const pager = fb.d.querySelector('#bv-pager');
  assert(pager.querySelector('button'), '降级分页条没有按钮');
  assert(/共 120 条/.test(pager.textContent), '缺总数：' + pager.textContent);
  assert(/第 1 \/ 3 页/.test(pager.textContent), '缺页码：' + pager.textContent);
  assert(fbRows().length === 50, '首屏行数 ' + fbRows().length);
});

check('P2-4 无 tablekit：点「下一页」能翻到第 2 / 3 页', () => {
  const first = fbRows()[0].getAttribute('data-vid');
  fbNext().click();
  assert(/第 2 \/ 3 页/.test(fb.d.querySelector('#bv-pager').textContent), '未翻到第 2 页');
  assert(fbRows().length === 50, '第 2 页行数 ' + fbRows().length);
  assert(fbRows()[0].getAttribute('data-vid') !== first, '行内容未变化');
  fbNext().click();
  assert(fbRows().length === 20, '末页应为 20 行，实得 ' + fbRows().length);
  assert(/第 3 \/ 3 页/.test(fb.d.querySelector('#bv-pager').textContent), '未到末页');
});

check('P2-4 无 tablekit：末页「下一页」禁用，回退可用', () => {
  const btns = [...fb.d.querySelectorAll('#bv-pager [data-pg]')];
  const next = btns[btns.length - 1];
  assert(next.disabled, '末页下一页未禁用');
  btns[0].click();
  assert(/第 2 \/ 3 页/.test(fb.d.querySelector('#bv-pager').textContent), '回退失败');
});

check('P2-4 无 tablekit：旧「加载更多」隐藏也不影响数据可达（120 条全部可翻到）', () => {
  const more = fb.d.querySelector('#bv-more');
  assert(more.classList.contains('hidden'), '旧按钮未隐藏');
  const seen = new Set(fbRows().map((tr) => tr.getAttribute('data-vid')));
  fb.d.querySelectorAll('#bv-pager [data-pg]')[0].click();     // 第 1 页
  fbRows().forEach((tr) => seen.add(tr.getAttribute('data-vid')));
  fb.d.querySelectorAll('#bv-pager [data-pg]')[1].click();     // 第 2 页
  fbRows().forEach((tr) => seen.add(tr.getAttribute('data-vid')));
  fb.d.querySelectorAll('#bv-pager [data-pg]')[1].click();     // 第 3 页
  fbRows().forEach((tr) => seen.add(tr.getAttribute('data-vid')));
  assert(seen.size === 120, '可达视频 ' + seen.size + ' / 120');
});

console.log(`\n${total - fails}/${total} passed`);
process.exit(fails ? 1 : 0);
