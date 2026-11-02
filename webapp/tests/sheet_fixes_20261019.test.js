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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const H = ['记录日期', '对标账号', '播放量'];
const row = (tag) => [`2026-10-17`, `@${tag}`, `${tag.length}`];
const sheetOf = (tag, n, total) => ok({
  header: H, rows: Array.from({ length: n }, (_, i) => [`d${i}`, `@${tag}${i}`, String(i)]),
  total: total === undefined ? n : total, file: `${tag}.xlsx`, hidden_cols: [], checks: [],
});

let sheetCtl = null;   // (url) => Promise<response>
const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://127.0.0.1:8787/' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;
w.fetch = (u) => {
  u = String(u);
  if (u.includes('/api/ledger/sheet')) {
    return sheetCtl ? sheetCtl(u) : sheetOf('x', 0, 0);
  }
  if (u.includes('/api/ledger/resolve')) {
    const m = u.match(/account=([^&]+)/);
    const acc = decodeURIComponent(m ? m[1] : '');
    return ok({ account: acc, sheet: 'shared', file: 'image.xlsx', candidates: [] });
  }
  if (u === '/api/accounts') {
    return ok({ total: 0, shown: 0, counts: { all: 0, active: 0, inactive: 0, video: 0, image: 0, artifacts: 0 }, accounts: [] });
  }
  if (u.startsWith('/api/config')) return ok({ accounts: [], local_steps: [], agent_steps: [], step_labels: {}, steps: [] });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

function deferred() {
  let res, rej;
  const p = new Promise((a, b) => { res = a; rej = b; });
  return { p, res, rej };
}

(async () => {
  const d = w.document;

  /* ───────── P1-1 请求乱序 ───────── */
  {
    const A = deferred(), B = deferred();
    sheetCtl = (u) => (u.includes('sheet=A') ? A.p : B.p);
    const pA = w.renderSheetView({ file: 'a.xlsx', sheet: 'A' });
    const pB = w.renderSheetView({ file: 'b.xlsx', sheet: 'B' });
    B.res({ ok: true, json: () => Promise.resolve({ header: H, rows: [row('B1'), row('B2')], total: 2, file: 'b.xlsx', hidden_cols: [], checks: [] }) });
    await pB;
    A.res({ ok: true, json: () => Promise.resolve({ header: H, rows: [row('A1')], total: 1, file: 'a.xlsx', hidden_cols: [], checks: [] }) });
    await pA;

    check('P1-1 乱序成功：B 先回 A 后回，标题与数据仍是 B', () => {
      assert(/B/.test(d.querySelector('#sheet-title').textContent), '标题被 A 覆盖：' + d.querySelector('#sheet-title').textContent);
      const txt = d.querySelector('#sheet-host tbody').textContent;
      assert(txt.includes('B1') && !txt.includes('A1'), '数据被 A 覆盖：' + txt.slice(0, 60));
      assert(/显示 2 \/ 2 行/.test(d.querySelector('#sheet-host .sheet-count').textContent),
        '状态被 A 覆盖：' + d.querySelector('#sheet-host .sheet-count').textContent);
    });
  }

  {
    const C = deferred(), D = deferred();
    sheetCtl = (u) => (u.includes('sheet=C') ? C.p : D.p);
    const pC = w.renderSheetView({ file: 'c.xlsx', sheet: 'C' });
    const pD = w.renderSheetView({ file: 'd.xlsx', sheet: 'D' });
    D.res({ ok: true, json: () => Promise.resolve({ header: H, rows: [row('D1')], total: 1, file: 'd.xlsx', hidden_cols: [], checks: [] }) });
    await pD;
    C.res({ ok: false, status: 500, text: () => Promise.resolve('{"detail":"旧请求失败"}') });
    await pC;

    check('P1-1 乱序失败：旧请求的 500 不覆盖新表', () => {
      const host = d.querySelector('#sheet-host');
      assert(!/表格加载失败/.test(host.textContent), '旧失败覆盖了新表：' + host.textContent.slice(0, 60));
      assert(/D/.test(d.querySelector('#sheet-title').textContent), '标题不是 D');
      assert(/D1/.test(host.textContent), '新表数据丢失');
    });
  }

  /* ───────── P1-1 加载中返回来源页 ───────── */
  {
    const E = deferred();
    sheetCtl = () => E.p;
    const pE = w.renderSheetView({ file: 'e.xlsx', sheet: 'E' });
    d.querySelector('.nav-item[data-view="overview"]').click();
    await sleep(30);
    E.res({ ok: true, json: () => Promise.resolve({ header: H, rows: [row('E1'), row('E2')], total: 2, file: 'e.xlsx', hidden_cols: [], checks: [] }) });
    await pE;

    check('P1-1 加载中返回来源页：迟到响应不写表格 DOM', () => {
      assert(d.querySelector('#view-overview').classList.contains('active'), '未回到总览');
      const host = d.querySelector('#sheet-host');
      assert(!/E1/.test(host.textContent), '迟到响应仍渲染了表格：' + host.textContent.slice(0, 50));
      assert(!String(w.location.hash || '').startsWith('#sheet'), '返回后 hash 未清：' + w.location.hash);
    });
  }

  /* ───────── P2-1 搜索/排序后仍可加载更多 ───────── */  {
    let calls = [];
    sheetCtl = (u) => {
      calls.push(u);
      const off = Number((u.match(/offset=(\d+)/) || [])[1] || 0);
      const limit = Number((u.match(/limit=(\d+)/) || [])[1] || 200);
      const n = off === 0 ? limit : Math.max(0, 325 - off);
      const rows = Array.from({ length: n }, (_, i) => [`d${off + i}`, `@acc${off + i}`,
        String((off + i) % 7)]);
      return ok({ header: H, rows, total: 325, file: 'big.xlsx', hidden_cols: [], checks: [] });
    };

    await w.renderSheetView({ file: 'big.xlsx', sheet: 'BIG' });
    const moreBox = () => d.querySelector('#sheet-host .sheet-more');
    const moreBtn = () => d.querySelector('#sheet-host .sheet-more button');

    check('P2-1 默认：325 行表出现「加载更多」', () => {
      assert(moreBtn(), '缺加载按钮');
      assert(/200 \/ 325/.test(moreBtn().textContent), moreBtn().textContent);
    });

    w.sortSheet(2);
    check('P2-1 排序后：按钮仍在，提示保留', () => {
      assert(moreBtn(), '排序后加载按钮消失');
      assert(/仅作用于已加载的 200 \/ 325/.test(moreBox().textContent), moreBox().textContent);
    });

    w.filterSheet('acc1');
    check('P2-1 搜索后：按钮仍在', () => {
      assert(moreBtn(), '搜索后加载按钮消失');
    });

    await w.loadMoreSheet();
    check('P2-1 加载更多：追加到 325 行，筛选/排序仍生效', () => {
      const cnt = d.querySelector('#sheet-host .sheet-count').textContent;
      assert(/\/ 325 行/.test(cnt), '总数未更新：' + cnt);
      assert(!moreBtn(), '已加载完仍显示按钮');
      const visible = w._sheetVisibleCount();
      assert(visible > 0 && visible < 325, '筛选未重新应用，visible=' + visible);
      assert(/仅作用于已加载的 325 \/ 325/.test(moreBox().textContent), moreBox().textContent);
    });
    w.filterSheet('');
  }

  /* ───────── P2-2 弹窗 → 全屏保留账号上下文 ───────── */
  {
    sheetCtl = () => ok({ header: H, rows: [['d1', '@example_account_051', '1'], ['d2', '@example_account_051', '2'], row('other')], total: 3, file: 'image.xlsx', hidden_cols: [], checks: [] });
    await w.openAccountLedger('example_account_051');
    const btn = d.querySelector('#modal-sheet');

    check('P2-2 弹窗打开时带上 account/q 上下文', () => {
      assert(btn && btn.style.display !== 'none', '弹窗缺「进入表格」');
      assert(typeof btn.onclick === 'function', '缺点击行为');
    });

    btn.onclick();
    await sleep(80);

    check('P2-2 页头入口 → hash 与副标题保留 @example_account_051', () => {
      const h = w.location.hash;
      assert(/account=example_account_051/.test(decodeURIComponent(h)), 'hash 缺 account：' + h);
      assert(/q=%40example_account_051/.test(h) || /q=@example_account_051/.test(decodeURIComponent(h)), 'hash 缺 q：' + h);
      assert(/@example_account_051/.test(d.querySelector('#sheet-sub').textContent), '副标题缺账号：' + d.querySelector('#sheet-sub').textContent);
      assert(d.querySelector('#sheet-host .sheet-toolbar input').value === '@example_account_051', '搜索框未预填');
    });

    // 工具栏入口：用同一上下文再走一遍
    await w.loadSheetInto('#modal-content', 'shared', 200, 'image.xlsx', { account: 'example_account_051', q: '@example_account_051' });
    await w.openSheetViewFromSheet();
    await sleep(80);

    check('P2-2 工具栏入口 → 同样保留 account/q', () => {
      const h = decodeURIComponent(w.location.hash);
      assert(/account=example_account_051/.test(h) && /q=@example_account_051/.test(h), 'hash 丢上下文：' + h);
      assert(d.querySelector('#sheet-host .sheet-toolbar input').value === '@example_account_051', '搜索框未预填');
    });
  }

  /* ───────── P2-3 离开表格清 hash ───────── */
  {
    check('P2-3 进入表格后 hash 可解析', () => {
      assert(w.parseSheetHash(w.location.hash), '前置失败：当前不在 sheet hash');
    });
    d.querySelector('.nav-item[data-view="accounts"]').click();
    await sleep(30);

    check('P2-3 点导航离开 → hash 清空，刷新不会回到旧表', () => {
      assert(!String(w.location.hash || '').startsWith('#sheet'), 'hash 未清：' + w.location.hash);
      assert(w.parseSheetHash(w.location.hash) === null, '仍能解析出 sheet 路由');
      assert(d.querySelector('#view-accounts').classList.contains('active'), '未切到账号管理');
    });

    check('P2-3 showReturnView 同样清 hash', () => {
      w.location.hash = '#sheet?sheet=shared&file=image.xlsx';
      w.showReturnView('overview');
      assert(!String(w.location.hash || '').startsWith('#sheet'), 'showReturnView 未清 hash：' + w.location.hash);
    });
  }

  /* ───────── P2-4 JSON / CSV 导入解析 ───────── */
  {
    check('P2-4 JSON 对象：解析 account 与保留字段', () => {
      const r = w.parseAccountLines('{"accounts":[{"account":"new_a","region":"de"}]}');
      assert(r.length === 1 && r[0].account === 'new_a', JSON.stringify(r));
      assert(r[0].region === 'de', 'region 未保留：' + JSON.stringify(r[0]));
    });

    check('P2-4 JSON 数组（字符串/对象混排）+ 去重', () => {
      const r = w.parseAccountLines('["@new_a", {"handle":"new_b","media_type":"image","tags":"de,eu"}, {"account":"new_a"}]');
      assert(r.length === 2, JSON.stringify(r));
      assert(r[0].account === 'new_a' && r[1].account === 'new_b', JSON.stringify(r));
      assert(r[1].media_type === 'image', 'media_type 未保留');
      assert(Array.isArray(r[1].tags) && r[1].tags.length === 2, 'tags 未拆分：' + JSON.stringify(r[1].tags));
    });

    check('P2-4 CSV 表头：不把 account 当账号，按列映射', () => {
      const r = w.parseAccountLines('account,region\nnew_a,de\n\nnew_a,de\nnew_c,fr');
      assert(r.length === 2, JSON.stringify(r));
      assert(!r.some((x) => x.account === 'account'), '表头被当成账号');
      assert(r[0].account === 'new_a' && r[0].region === 'de', JSON.stringify(r[0]));
      assert(r[1].account === 'new_c' && r[1].region === 'fr', JSON.stringify(r[1]));
    });

    check('P2-4 中文表头 CSV', () => {
      const r = w.parseAccountLines('账号,地区\nnew_d,de');
      assert(r.length === 1 && r[0].account === 'new_d' && r[0].region === 'de', JSON.stringify(r));
    });

    check('P2-4 纯文本仍然可用（@handle / 主页链接 / 账号名）', () => {
      const r = w.parseAccountLines('@gutpickde\nhttps://www.tiktok.com/@example_account_047\nexample_account_048');
      assert(r.map((x) => x.account).join(',') === 'gutpickde,example_account_047,example_account_048', JSON.stringify(r));
    });

    check('P2-4 非法 JSON 退回按行解析，不抛错', () => {
      const r = w.parseAccountLines('{not json}\n@ok_name');
      assert(r.length >= 1 && r.some((x) => x.account === 'ok_name'), JSON.stringify(r));
    });
  }

  console.log(fails ? `${fails} FAILED` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
