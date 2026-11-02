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

const calls = [];
const STATUS = {
  as_of: 1790000000, window_days: 3, failed: 2, stale: false,
  counts: { ok: 27, warn: 2, failed: 2 },
  summary: { date: '2026-10-19', level: 'ERROR', total: 31, duration_s: 3600, counts: { ok: 27, warn: 2, failed: 2 } },
  takeover: [
    { account: 'accA', reason: '日更失败（exit=1）', kind: 'daily', attempts: 3,
      at: 1789999000, duration_s: 120, payload: '' },
    { account: 'accB', reason: '台账同步失败（exit=1）', kind: 'ledger', attempts: 1,
      at: 1789998000, duration_s: 45, payload: '' },
  ],
};

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = (u, opts) => {
  u = String(u);
  const method = (opts && opts.method) || 'GET';
  calls.push({ u, method, body: opts && opts.body });
  if (u.startsWith('/api/pipeline/status')) return ok(w.__status);
  if (u.startsWith('/api/pipeline/retry')) {
    return ok({ ok: true, accounts: (JSON.parse(opts.body).accounts || []), run: { id: 'r1' } });
  }
  if (u.startsWith('/api/config')) {
    return ok({ accounts: [], accounts_detail: [], local_steps: [], agent_steps: [],
                step_labels: {}, steps: [] });
  }
  if (u.includes('/api/groups')) return ok({ groups: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));
w.__status = STATUS;

(async () => {
  const d = w.document;
  const panel = d.querySelector('#takeover-panel');
  const badge = d.querySelector('#tk-nav-badge');

  // 有失败：渲染
  w.__status = STATUS;
  await w.loadPipelineStatus(true);

  check('面板与角标：2 个失败时可见', () => {
    assert(panel, '缺 #takeover-panel');
    assert(!panel.classList.contains('hidden'), '有失败时面板应可见');
    assert(badge.textContent === '2', '角标应为 2，实得 ' + badge.textContent);
    assert(!badge.classList.contains('hidden'), '角标应可见');
    assert(d.querySelector('#tk-count').textContent === '2', '计数徽标不对');
  });

  check('列表：逐账号渲染原因/重试次数/时间', () => {
    const items = d.querySelectorAll('#tk-list .tk-item');
    assert(items.length === 2, '条目数 ' + items.length);
    assert(/accA/.test(items[0].textContent), '缺账号名');
    assert(/日更失败/.test(items[0].textContent), '缺失败原因');
    assert(/已重试 3 次/.test(items[0].textContent), '缺重试次数');
    assert(/台账同步失败/.test(items[1].textContent), '第二条原因不对');
  });

  check('副标题：最近一轮日期 + 告警级别', () => {
    const t = d.querySelector('#tk-sub').textContent;
    assert(/2026-10-19/.test(t), '缺日期: ' + t);
    assert(/异常/.test(t), '应把 ERROR 译为「异常」: ' + t);
  });

  // 单账号重跑
  const before = calls.length;
  await w.retryTakeover(['accA']);
  check('单账号重跑：POST accounts 只含该账号', () => {
    const post = calls.slice(before).find((c) => c.u.startsWith('/api/pipeline/retry'));
    assert(post && post.method === 'POST', '未发起重跑请求');
    const body = JSON.parse(post.body);
    assert(Array.isArray(body.accounts) && body.accounts.length === 1 &&
           body.accounts[0] === 'accA', '请求体不对: ' + post.body);
    assert(/重跑/.test(d.querySelector('#toast').textContent), '缺提交提示');
  });

  // 全部重跑（不传账号）
  const before2 = calls.length;
  await w.retryTakeover();
  check('重跑全部：不带 accounts 字段（后端取队列全集）', () => {
    const post = calls.slice(before2).find((c) => c.u.startsWith('/api/pipeline/retry'));
    assert(post, '未发起重跑请求');
    const body = JSON.parse(post.body);
    assert(!('accounts' in body), '全量重跑不应传 accounts: ' + post.body);
  });

  // 无失败：整块隐藏
  w.__status = { ...STATUS, failed: 0, takeover: [], summary: null, stale: true };
  await w.loadPipelineStatus(true);
  check('无失败：面板与角标全隐藏、列表清空', () => {
    assert(panel.classList.contains('hidden'), '无失败时面板应隐藏');
    assert(badge.classList.contains('hidden'), '无失败时角标应隐藏');
    assert(d.querySelectorAll('#tk-list .tk-item').length === 0, '列表未清空');
  });

  // 无失败但回执不可信（旧格式缺阶段/缺轮次）：面板要露面，副标题写明「回执存疑」
  // 2026-10-21 验收 P1-D：假绿的形态恰恰是「没有待接管 + level=INFO」，不能因此藏起来。
  w.__status = {
    ...STATUS, failed: 0, takeover: [], alerts: [], stale: true,
    receipt: { state: 'done', level: 'INFO', legacy: true, confidence: 'low',
               stale: true, rounds: 0, round_id: null,
               stale_reasons: ['回执缺阶段结论（旧格式，可信度不足：无法确认商品/台账阶段）',
                               '回执缺口轮标识（无法判断是哪一轮写的）'] },
    summary: { date: '2026-10-19', level: 'INFO', total: 2, failed_stages: [] },
  };
  await w.loadPipelineStatus(true);
  check('无账号但回执存疑：面板可见、副标题含「回执存疑」、列表为空', () => {
    assert(!panel.classList.contains('hidden'), '回执存疑时面板应可见');
    const t = d.querySelector('#tk-sub').textContent;
    assert(/回执存疑/.test(t), '副标题应含「回执存疑」: ' + t);
    assert(/缺阶段结论/.test(t), '副标题应带具体原因: ' + t);
    assert(d.querySelectorAll('#tk-list .tk-item').length === 0, '不应残留旧条目');
    assert(d.querySelector('#tk-count').style.display === 'none',
      '队列为空时不应显示红色 0');
  });

  // 无失败时点重跑 → 只提示，不发请求
  const before3 = calls.length;
  await w.retryTakeover();
  check('无失败时重跑：只提示不发请求', () => {
    assert(!calls.slice(before3).some((c) => c.u.startsWith('/api/pipeline/retry')),
      '不应发重跑请求');
    assert(/没有待接管/.test(d.querySelector('#toast').textContent), '缺提示文案');
  });

  // 接口异常不炸总览
  w.fetch = (u) => (String(u).startsWith('/api/pipeline/status')
    ? Promise.resolve({ ok: false, text: () => Promise.resolve('boom') })
    : ok({}));
  const r = await w.loadPipelineStatus(true);
  check('状态接口失败：返回 null，不抛出', () => {
    assert(r === null, '应静默返回 null');
  });

  dom.window.close();
  console.log(`\n通过 ${9 - fails} / 9`);
  process.exit(fails ? 1 : 0);
})();
