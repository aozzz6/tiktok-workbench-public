/* overview_ui.test.js — 总览 KPI 重构的前端表现（jsdom，不发真实请求）
   覆盖（对应 总览看板诊断_20261024.html 的 P0/P1）：
     · 六张卡：今日批次 / 执行 / 待处理 / 待翻译 / 账号 / 新视频（顺序 + 名称）
     · 「今日执行」= 执行总数（不是成功数），副标题拆成功/失败/中断
     · 启用口径按 enabled：停用账号既不算「账号」也不算「待处理」
     · 批次 partial → 显示「部分完成」；headline 出现数据新鲜度
     · 点卡展开下钻、再点收起；待处理下钻会去取 repair 计划算「可自动修」
     · 运行中 → 第二卡切成「正在运行」并带流动条
     · 轮询失败 → 更新时间变红 + 出现过期警示条（原来是静默假绿）
     · aria-live 摘要只播报一句
   运行：node tests/overview_ui.test.js（NODE_PATH 指向 workspace node_modules）
*/
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');

let fails = 0, total = 0;
function check(name, fn) {
  total++;
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const ACC = (o) => Object.assign({
  account: 'accA', media_type: 'video', enabled: true, severity: 'ok', gaps: [],
  audio_count: 10, transcript_count: 10, scrape_items: 10, products_count: 1,
  ledger_covered: 10, ledger_rows: 10, new_today: 0, active: true,
}, o);

const NOW = 1792830000;
const RUN_DAY = {
  date: '2026-10-24', total: 16, success: 8, failed: 5, stopped: 3, running: 0,
  accounts_covered: 2, accounts_total: 3, new_videos: 7,
  series: [
    { date: '10-19', success: 4, failed: 0 }, { date: '10-20', success: 6, failed: 1 },
    { date: '10-21', success: 3, failed: 2 }, { date: '10-22', success: 9, failed: 0 },
    { date: '10-23', success: 5, failed: 1 }, { date: '10-24', success: 8, failed: 8 },
  ],
  failed_runs: [
    { id: 'r1', account: 'accA', title: '流水线 · accA', kind: 'pipeline',
      started_at: NOW - 600, exit_code: 1, duration_s: 178.5, status: 'failed' },
    { id: 'r2', account: 'accB', title: '流水线 · accB', kind: 'pipeline',
      started_at: NOW - 900, exit_code: null, duration_s: null, status: 'interrupted' },
  ],
};

const BASE = {
  ts: NOW, server_now: NOW,
  accounts: [
    ACC({ account: 'accA', new_today: 5 }),
    ACC({ account: 'accB', new_today: 2, severity: 'warn', ledger_covered: 8,
          gaps: [{ level: 'warn', label: '入账', msg: '抓取 10 条中有 2 条未入账' }] }),
    ACC({ account: 'accC' }),
    ACC({ account: 'off1', enabled: false, severity: 'warn', new_today: 3,
          gaps: [{ level: 'warn', label: '入账', msg: '停用账号的遗留缺口' }] }),
  ],
  runs: [], active_runs: [], diagnostics: { ledger: 'D:/1/对标账号/tiktok_对标.xlsx' },
  pending_translation: { total: 9, by_account: [{ account: 'accA', count: 9 }] },
  day: RUN_DAY,
  batch: { date: '2026-10-24', state: 'done', partial: true, level: 'WARN',
           total: 3, counts: { ok: 2, failed: 1 }, duration_s: 750, ended_at: NOW - 120,
           round_id: '20261024T152129_230_36928', failed_stages: [], alerts: [] },
  freshness: { as_of: NOW - 3600, as_of_text: '10-24 15:28', stale_hours: 24,
               stale_count: 2, stale_accounts: [] },
  scrape_health: { items: 19, errors: 1, ratio: 0.95, level: 'ok', proxy: '127.0.0.1:7897' },
};

let payload = BASE;
let failNext = false;

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = (u) => {
  u = String(u);
  if (failNext && u.startsWith('/api/overview')) return Promise.reject(new Error('Failed to fetch'));
  if (u.startsWith('/api/overview')) return ok(payload);
  if (u.startsWith('/api/repair/scan')) return ok({ items: [
    { id: 'rp01', repairable: true }, { id: 'rp02', repairable: true },
    { id: 'rp03', repairable: false }], counts: {} });
  if (u.startsWith('/api/pipeline/status')) return ok({ takeover: [], alerts: [], receipt: {} });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  if (u.startsWith('/api/config')) return ok({ accounts: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));
const d = w.document;
const txt = (sel) => { const n = d.querySelector(sel); return n ? n.textContent : ''; };
const cards = () => Array.from(d.querySelectorAll('#ov-kpi .kpi'));

(async () => {
  await w.loadOverview(true);

  check('六张卡：批次 / 执行 / 待处理 / 待翻译 / 账号 / 新视频（按此顺序）', () => {
    const labels = cards().map((c) => c.querySelector('.kpi-l').textContent.replace(/[▾▴→]/g, '').trim());
    assert(labels.join('|') === '今日批次|今日执行|待处理|待翻译|账号|今日新视频', labels.join('|'));
    assert(cards().every((c) => c.tagName === 'BUTTON'), '卡片必须是 button（可点 + 键盘可达）');
  });

  check('「今日执行」= 执行总数 16（旧实现错写成成功数 8）', () => {
    const v = cards()[1].querySelector('.kpi-v').textContent;
    assert(v.startsWith('16'), 'value=' + v);
    assert(/次/.test(v), '单位应是「次」不是「条」：' + v);
    const cap = cards()[1].querySelector('.kpi-cap').textContent;
    assert(/成功 8/.test(cap) && /失败 5/.test(cap) && /中断 3/.test(cap), cap);
    // 环比做成角标（原来塞进 caption 会挤成两行）
    const tr = cards()[1].querySelector('.kpi-trend');
    assert(tr && tr.textContent === '+3', '环比角标=' + (tr && tr.textContent));
  });

  check('停用账号不计入「账号」与「待处理」', () => {
    const accCap = cards()[4].querySelector('.kpi-cap').textContent;
    assert(/停用 1/.test(accCap), accCap);
    assert(cards()[4].querySelector('.kpi-v').textContent.startsWith('3'), '账号应为 3 个启用');
    assert(cards()[4].dataset.nav === 'accounts', '账号卡应直接跳转账号管理，而不是下钻');
    // off1 是停用账号，它的 warn 不该进待处理
    assert(cards()[2].querySelector('.kpi-v').textContent.startsWith('1'),
      '待处理应为 1（只有 accB），实际 ' + cards()[2].querySelector('.kpi-v').textContent);
  });

  check('批次 partial → 「部分完成」，副标题带账号数与耗时', () => {
    const c = cards()[0];
    assert(/部分完成/.test(c.querySelector('.kpi-v').textContent), c.querySelector('.kpi-v').textContent);
    assert(/2\/3 账号/.test(c.querySelector('.kpi-cap').textContent), c.querySelector('.kpi-cap').textContent);
    assert(/12m30s/.test(c.querySelector('.kpi-cap').textContent), c.querySelector('.kpi-cap').textContent);
  });

  check('headline 只讲结论 + 新鲜度，不再复述四个数字', () => {
    const f = txt('#ov-finding');
    assert(/数据到 10-24 15:28/.test(f), f);
    assert(/2 个账号超 24h/.test(f), f);
    assert(!/执行成功/.test(f), '不该再念 KPI 数字：' + f);
  });

  check('7 日 sparkline 渲染 6 根柱（末根含失败）', () => {
    const bars = d.querySelectorAll('#ov-kpi .kpi-spark i');
    assert(bars.length === 6, 'bars=' + bars.length);
    assert(bars[5].className === 'has-fail', '今日有失败应标红：' + bars[5].className);
  });

  check('点「执行」卡展开下钻，列出失败/中断记录；再点收起', () => {
    const card = d.querySelector('#ov-kpi .kpi[data-ov="run"]');
    card.click();
    const box = d.querySelector('#ov-detail');
    assert(box.hidden === false, '下钻应展开');
    assert(/accA/.test(txt('#ov-detail')) && /exit=1/.test(txt('#ov-detail')), txt('#ov-detail'));
    assert(card.getAttribute('aria-expanded') === 'true', 'aria-expanded 应同步');
    card.click();
    assert(box.hidden === true, '再点应收起');
  });

  d.querySelector('#ov-kpi .kpi[data-ov="todo"]').click();
  await new Promise((r) => setTimeout(r, 200));
  check('待处理下钻：显示可自动修复 2 项（3 项里 1 项不可修）', () => {
    const t = txt('#ov-scan');
    assert(/可自动修复 2 项/.test(t), t);
    assert(/需人工\/暂不可修 1 项/.test(t), t);
  });

  check('aria-live 摘要只一句，含执行总数与待译', () => {
    const sr = txt('#ov-sr');
    assert(/今日执行 16 次/.test(sr) && /待翻译 9 条/.test(sr), sr);
  });

  // ── 运行中：第二卡切成「正在运行」+ 流动条 ──
  payload = { ...BASE, day: { ...RUN_DAY, running: 2 },
              active_runs: [{ id: 'a', account: 'accA' }, { id: 'b', account: 'accB' }] };
  await w.loadOverview(true);
  check('运行中：第二卡变「正在运行 2 个」并带流动高光条', () => {
    const c = cards()[1];
    assert(/正在运行/.test(c.querySelector('.kpi-l').textContent), c.textContent);
    assert(c.querySelector('.kpi-v').textContent.startsWith('2'), c.querySelector('.kpi-v').textContent);
    assert(c.querySelector('.kpi-live'), '缺运行中动画条');
    assert(/正在跑 2 个任务/.test(txt('#ov-finding')), txt('#ov-finding'));
  });

  // ── 轮询失败：不再假绿 ──
  failNext = true;
  await w.loadOverview(false);
  failNext = false;
  check('刷新失败：更新时间变红并出现过期警示条', () => {
    const el = d.querySelector('#ov-updated');
    assert(el.classList.contains('stale'), 'class=' + el.className);
    assert(/连接中断/.test(el.textContent), el.textContent);
    const bar = d.querySelector('#ov-alert');
    assert(bar.hidden === false, '警示条应显示');
    assert(/数据可能已过期/.test(bar.textContent), bar.textContent);
  });

  payload = BASE;
  await w.loadOverview(true);
  check('恢复：警示条隐藏、更新时间不再是「连接中断」', () => {
    assert(d.querySelector('#ov-alert').hidden === true, '警示条应收起');
    assert(!/连接中断/.test(txt('#ov-updated')), txt('#ov-updated'));
  });

  // ── 版本不匹配：静态资源已换新、后端未重启 → 必须明确提示，而不是显示一堆 0 ──
  payload = { accounts: [], runs: [], diagnostics: {}, pending_translation: { total: 0 } };
  await w.loadOverview(true);
  check('旧后端（无 day/batch）→ 提示重启，而不是静默显示 0', () => {
    const bar = d.querySelector('#ov-alert');
    assert(bar.hidden === false, '应显示提示条');
    assert(/服务端版本较旧/.test(bar.textContent), bar.textContent);
    assert(/重启工作台/.test(bar.textContent), bar.textContent);
  });

  // ── 待译快照过期（2026-10-24）：队列龄期 > 30min 必须变红并写明，不能继续承诺「15min 内回填」──
  payload = { ...BASE, pending_translation: { total: 9, age_s: 4000, ok: true,
    by_account: [{ account: 'accA', count: 9 }], truncated: true, by_account_total: 11 } };
  await w.loadOverview(true);
  check('待译队列过期 → 卡片标红 + 写「N 分钟未刷新」，不再假承诺 15min', () => {
    const c = cards()[3];
    assert(/待翻译/.test(c.textContent), c.textContent);
    const cap = c.querySelector('.kpi-cap').textContent;
    assert(/66 分钟未刷新/.test(cap) || /67 分钟未刷新/.test(cap), cap);
    assert(!/巡检 15min 内回填/.test(cap), '过期时不能继续承诺 15min：' + cap);
    assert(c.classList.contains('bad'), '过期卡片应标红：' + c.className);
    const diag = d.querySelector('#diag-bar').textContent;
    assert(/待翻译 9 条/.test(diag) && /分钟未刷新/.test(diag), diag);
  });

  check('待译下钻：显示快照龄期 + 截断提示（不静默丢账号）', () => {
    w.ovToggleDetail('trans');
    const box = txt('#ov-detail');
    assert(/待翻译分布/.test(box), box);
    assert(/队列快照/.test(box) && /分钟前/.test(box), box);
    assert(/共 11 个账号，仅列前 1 个/.test(box), box);
  });

  payload = BASE;
  await w.loadOverview(true);

  for (const count of [0, 9]) {
    payload = { ...BASE, pending_translation: { total: count, ok: false } };
    await w.loadOverview(true);
    check(`队列不可核验（旧值 ${count}）：卡片与诊断条均报错`, () => {
      assert(cards()[3].classList.contains('bad'), '必须标红');
      assert(/数字不可信/.test(cards()[3].textContent), cards()[3].textContent);
      assert(/数字不可信/.test(txt('#diag-bar')), txt('#diag-bar'));
    });
  }

  console.log(`\n通过 ${total - fails} / ${total}`);
  process.exit(fails ? 1 : 0);
})();
