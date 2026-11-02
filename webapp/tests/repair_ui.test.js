/* repair_ui.test.js — 一键修复的前端表现（jsdom，不发真实请求）
   覆盖：
     · 空闲态不占位
     · 运行态：百分比 / 已完成·总数 / 当前处理项 / 进度条宽度
     · 完成态：成功·失败·跳过汇总 + 失败原因可见 + 「重试失败项」
     · 点「一键修复」发起 POST /api/repair/start（不带 ids = 修全部）
     · 「重试失败项」重新扫描后按 kind+account 映射新 id，只提交失败项
     · 计划预览：可修项为 0 时「开始修复」禁用、跳过原因可见
   运行：node tests/repair_ui.test.js（NODE_PATH 指向 workspace node_modules）
*/
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');

let fails = 0;
let total = 0;
function check(name, fn) {
  total++;
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const ITEM = (o) => Object.assign(
  { id: 'rp01', kind: 'collect', kind_label: '采集重跑', account: 'accA',
    title: '采集重跑（全链）', reason: '日更失败（exit=1）', repairable: true,
    skip_reason: '', status: 'pending', detail: '', duration_s: null }, o);

const IDLE = { status: 'idle', total: 0, done: 0, percent: 0,
               counts: { ok: 0, failed: 0, skipped: 0 }, items: [] };

// 多步骤阶段表（与 repair.REPAIR_FLOW 同名同权重）
const FLOW = [['s1_download', '下载音频', 5], ['s2_scrape', '抓取详情', 50],
              ['s5_transcribe', '转写', 30], ['s5b_products', '商品提取', 10],
              ['ledger', '台账入账', 5]];
const FLOW_STEPS = (states) =>
  FLOW.map(([key, label, weight], i) => ({ key, label, weight, state: states[i] }));

const RUNNING = {
  id: 's1', status: 'running', started_at: 1790000000, ended_at: null,
  duration_s: 42, total: 3, done: 1, percent: 33,
  counts: { ok: 1, failed: 0, skipped: 0 },
  current: { id: 'rp02', kind: 'ledger', kind_label: '台账同步', account: 'accB',
             title: '台账补入账', step: '台账同步（write + 终验）' },
  // 2026-10-24：子任务真实阶段（collect 一项要 7 分钟，只显示 0/3 会让人以为卡死）
  live: { run_id: 'r1', pipeline_run_id: 'p1', status: 'running', elapsed_s: 241.2,
          stage: '抓取详情', line: '[15:25:41] s2_scrape | ok=True cost=241.2s',
          pct: 5, current_key: 's2', steps: FLOW_STEPS(['done', 'running', 'pending', 'pending', 'pending']) },
  items: [
    ITEM({ status: 'ok', detail: '采集重跑（抓取→转写→商品→入账） 完成', duration_s: 20 }),
    ITEM({ id: 'rp02', kind: 'ledger', kind_label: '台账同步', account: 'accB',
           title: '台账补入账', status: 'pending' }),
    ITEM({ id: 'rp03', kind: 'translation', kind_label: '翻译回填', account: '',
           title: '翻译回填 2 条', repairable: false, skip_reason: '本地服务无 LLM', status: 'pending' }),
  ],
};

const DONE = {
  id: 's1', status: 'done', started_at: 1790000000, ended_at: 1790000060,
  duration_s: 60, total: 3, done: 3, percent: 100,
  counts: { ok: 1, failed: 1, skipped: 1 },
  current: null,
  items: [
    ITEM({ status: 'ok', detail: '采集重跑（抓取→转写→商品→入账） 完成', duration_s: 20 }),
    ITEM({ id: 'rp02', kind: 'ledger', kind_label: '台账同步', account: 'accB',
           title: '台账补入账', status: 'failed',
           detail: '台账同步（write + 终验）失败（exit=1）：列「商品链接」缺失', duration_s: 35 }),
    ITEM({ id: 'rp03', kind: 'translation', kind_label: '翻译回填', account: '',
           title: '翻译回填 2 条', repairable: false, status: 'skipped',
           skip_reason: '本地服务无 LLM：译文由巡检 automation 回填' }),
  ],
  summary: {
    ok: 1, failed: 1, skipped: 1, total: 3,
    failed_items: [{ id: 'rp02', kind: 'ledger', kind_label: '台账同步', account: 'accB',
                     title: '台账补入账', reason: '台账同步失败（exit=1）：列「商品链接」缺失' }],
  },
};

const SCAN = {
  as_of: 1790000000, total: 2, running: false,
  counts: { total: 2, repairable: 1, skipped: 1 },
  items: [
    ITEM({ repairable: true }),
    ITEM({ id: 'rp02', kind: 'translation', kind_label: '翻译回填', account: '',
           title: '翻译回填 2 条', repairable: false,
           skip_reason: '本地服务无 LLM：译文由巡检 automation 回填' }),
  ],
};
const SCAN_NONE = { ...SCAN, counts: { total: 2, repairable: 0, skipped: 2 },
                    items: SCAN.items.map((x) => ({ ...x, repairable: false, skip_reason: '需人工处理' })) };

const calls = [];
let rpStatus = IDLE;
let scanPayload = SCAN;

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = (u, opts) => {
  u = String(u);
  const method = (opts && opts.method) || 'GET';
  calls.push({ u, method, body: opts && opts.body });
  if (u.startsWith('/api/repair/start')) return ok(RUNNING);
  if (u.startsWith('/api/repair/status')) return ok(rpStatus);
  if (u.startsWith('/api/repair/stop')) return ok({ ...RUNNING, status: 'stopped' });
  if (u.startsWith('/api/repair/scan')) return ok(scanPayload);
  if (u.startsWith('/api/overview')) return ok({ accounts: [], runs: [], diagnostics: {},
                                                 pending_translation: { total: 0 } });
  if (u.startsWith('/api/pipeline/status')) return ok({ takeover: [], alerts: [], receipt: { stale: false } });
  if (u.startsWith('/api/config')) return ok({ accounts: [], local_steps: [], agent_steps: [],
                                               step_labels: {}, steps: [] });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

const d = w.document;
const box = d.querySelector('#rp-box');
const txt = (sel) => { const n = d.querySelector(sel); return n ? n.textContent : ''; };

(async () => {
  check('入口存在：待接管面板里有「一键修复」按钮', () => {
    const b = d.querySelector('#tk-repair');
    assert(b, '缺 #tk-repair');
    assert(/一键修复/.test(b.textContent), b.textContent);
    assert(/自动检测并修复/.test(b.getAttribute('title') || ''), '缺 title 说明');
    assert(b.closest('#takeover-panel'), '按钮应在待接管面板内');
  });

  check('空闲态：进度区隐藏且不占位', () => {
    w.renderRepair(IDLE);
    assert(box.hidden === true, '应隐藏');
    assert(box.innerHTML === '', '应清空');
  });

  // ── 运行态 ────────────────────────────────────────────────────
  w.renderRepair(RUNNING);
  check('运行态：可见、百分比与已完成/总数正确', () => {
    assert(box.hidden === false, '应可见');
    const nums = txt('#rp-box .rp-nums');
    assert(/已完成/.test(nums), nums);
    assert(/\b1\b/.test(nums) && /\b3\b/.test(nums), '缺已完成/总数: ' + nums);
    assert(/33%/.test(nums), '缺百分比: ' + nums);
    assert(/正在修复/.test(txt('#rp-box .rp-head')), '缺运行标题');
  });

  check('运行态：进度条宽度 = 百分比、有 progressbar 语义', () => {
    const t = d.querySelector('#rp-box .rp-track');
    assert(t, '缺进度条');
    assert(t.getAttribute('role') === 'progressbar', '缺 role');
    assert(t.getAttribute('aria-valuenow') === '33', 'aria-valuenow=' + t.getAttribute('aria-valuenow'));
    const i = t.querySelector('i');
    assert(/width:\s*33%/.test(i.getAttribute('style')), i.getAttribute('style'));
  });

  check('运行态：显示当前处理项 + 账号 + 阶段', () => {
    const cur = txt('#rp-box .rp-cur');
    assert(/当前处理/.test(cur), cur);
    assert(/台账补入账/.test(cur), '缺当前项标题: ' + cur);
    assert(/accB/.test(cur), '缺账号');
    assert(/台账同步（write \+ 终验）/.test(cur), '缺阶段: ' + cur);
  });

  check('运行态：出现「中止」按钮', () => {
    const btns = Array.from(d.querySelectorAll('#rp-box .rp-acts button')).map((b) => b.textContent);
    assert(btns.some((t) => /中止/.test(t)), btns.join(','));
  });

  // ── 完成态 ────────────────────────────────────────────────────
  w.renderRepair(DONE);
  check('完成态：成功/失败/跳过三类数量汇总', () => {
    const sum = txt('#rp-box .rp-sum');
    assert(/成功 1/.test(sum), sum);
    assert(/失败 1/.test(sum), sum);
    assert(/跳过 1/.test(sum), sum);
    assert(/修复完成/.test(txt('#rp-box .rp-head')), '缺完成标题');
    assert(/100%/.test(txt('#rp-box .rp-nums')), txt('#rp-box .rp-nums'));
  });

  check('完成态：失败项列出原因（可判断是否重试）', () => {
    const failed = d.querySelector('#rp-box .rp-item.is-failed');
    assert(failed, '缺失败项行');
    assert(/商品链接/.test(failed.textContent), '缺失败原因: ' + failed.textContent);
    assert(/台账同步/.test(failed.textContent), '缺类型标签');
  });

  check('完成态：给出「重试失败项（1）」与「关闭」', () => {
    const btns = Array.from(d.querySelectorAll('#rp-box .rp-acts button')).map((b) => b.textContent);
    assert(btns.some((t) => /重试失败项（1）/.test(t)), btns.join(','));
    assert(btns.some((t) => /关闭/.test(t)), btns.join(','));
  });

  check('完成态：跳过项折叠组可展开且带原因', () => {
    const grps = Array.from(d.querySelectorAll('#rp-box .rp-grp summary')).map((s) => s.textContent);
    assert(grps.some((t) => /跳过（1）/.test(t)), grps.join('|'));
    assert(grps.some((t) => /成功（1）/.test(t)), grps.join('|'));
    assert(/LLM/.test(txt('#rp-box')), '跳过原因未渲染');
  });

  check('关闭：非运行态可收起进度区', () => {
    w.repairReset();
    assert(box.hidden === true, '应隐藏');
    assert(box.innerHTML === '', '应清空');
  });

  // ── 启动 ──────────────────────────────────────────────────────
  const before = calls.length;
  await w.repairStart();
  check('点击一键修复：POST /api/repair/start（不带 ids = 全部可修项）', () => {
    const post = calls.slice(before).find((c) => c.u.startsWith('/api/repair/start'));
    assert(post && post.method === 'POST', '未发起修复请求');
    assert(JSON.parse(post.body).ids === undefined, '全量不应带 ids: ' + post.body);
    assert(box.hidden === false, '进度区应立刻可见');
    assert(/正在修复/.test(txt('#rp-box .rp-head')), '应进入运行态');
  });

  // ── 后端旧版本（404）→ 给出可执行的恢复指引 ───────────────────
  const realFetch = w.fetch;
  w.fetch = (u, opts) => {
    u = String(u);
    if (u.startsWith('/api/repair/')) {
      return Promise.resolve({ ok: false, status: 404, text: () => Promise.resolve('{"detail":"Not Found"}') });
    }
    return realFetch(u, opts);
  };
  await w.repairStart();
  check('后端旧版本 404：提示重启工作台，不再盲调 scan', () => {
    const t = d.querySelector('#toast').textContent;
    assert(/重启工作台/.test(t), t);
    assert(!/\{"detail":"Not Found"\}/.test(t), '不该把原始 404 报文丢给用户: ' + t);
  });
  w.fetch = realFetch;

  // ── 进度轮询 → 完成刷新 ───────────────────────────────────────
  rpStatus = DONE;
  const beforeOv = calls.length;
  await w.pollRepair();
  check('轮询到终态：渲染汇总并自动刷新总览', () => {
    assert(/修复完成/.test(txt('#rp-box .rp-head')), txt('#rp-box .rp-head'));
    const refreshed = calls.slice(beforeOv).some((c) => c.u.startsWith('/api/overview'));
    assert(refreshed, '完成后应刷新总览');
  });

  // ── 重试失败项 ────────────────────────────────────────────────
  scanPayload = { ...SCAN, items: [ITEM({ id: 'rp09', kind: 'ledger', kind_label: '台账同步',
                                          account: 'accB', title: '台账补入账', repairable: true })] };
  const before2 = calls.length;
  await w.repairRetryFailed();
  check('重试失败项：先重扫，再按 kind+account 映射新 id 提交', () => {
    const seq = calls.slice(before2);
    const scan = seq.find((c) => c.u.startsWith('/api/repair/scan'));
    const post = seq.find((c) => c.u.startsWith('/api/repair/start'));
    assert(scan, '应重新扫描计划');
    assert(post, '应提交修复请求');
    const ids = JSON.parse(post.body).ids;
    assert(Array.isArray(ids) && ids.length === 1 && ids[0] === 'rp09',
      '应只提交映射后的失败项 id: ' + post.body);
  });

  // ── 计划预览 ──────────────────────────────────────────────────
  w.repairReset();
  w.renderRepair(SCAN);
  check('计划预览：显示可修/需人工数量与「开始修复」', () => {
    assert(box.hidden === false, '应可见');
    assert(/检测结果/.test(txt('#rp-box .rp-head')), '缺预览标题');
    assert(/可自动修复 1 项/.test(txt('#rp-box .rp-head')), txt('#rp-box .rp-head'));
    const go = d.querySelector('#rp-go');
    assert(go, '缺「开始修复」');
    assert(go.disabled === false, '有可修项时不应禁用');
  });

  w.renderRepair(SCAN_NONE);
  check('计划预览：可修项为 0 时按钮禁用且跳过原因可见', () => {
    const go = d.querySelector('#rp-go');
    assert(go && go.disabled === true, '应禁用');
    assert(/可自动修复 0 项/.test(txt('#rp-box .rp-head')), txt('#rp-box .rp-head'));
    assert(/需人工处理/.test(txt('#rp-box')), '缺跳过原因');
  });

  // ── 刷新后接回运行中会话 ──────────────────────────────────────
  rpStatus = RUNNING;
  w.repairReset();
  await w.restoreRepair();
  check('刷新页面：接回运行中会话并显示进度', () => {
    assert(box.hidden === false, '应显示进度');
    assert(/正在修复/.test(txt('#rp-box .rp-head')), txt('#rp-box .rp-head'));
    // 运行中要能看出「在跑」而不是「卡死」：显示子任务真实阶段与已跑时长
    assert(/正在 抓取详情/.test(txt('#rp-box')), txt('#rp-box .rp-cur'));
    assert(/4m01s/.test(txt('#rp-box')), txt('#rp-box .rp-cur'));
  });
  check('多步骤阶段条：五项按顺序 + 名称 + 占比 + 三态', () => {
    const flow = box.querySelector('.rp-flow');
    assert(flow, '缺阶段条');
    const lis = Array.from(flow.querySelectorAll('.rp-flow-i'));
    assert(lis.length === 5, '步骤数=' + lis.length);
    const names = lis.map((li) => li.querySelector('.rp-flow-name').textContent);
    assert(names.join('|') === '下载音频|抓取详情|转写|商品提取|台账入账', names.join('|'));
    const ws = lis.map((li) => li.querySelector('.rp-flow-w').textContent);
    assert(ws.join('|') === '5%|50%|30%|10%|5%', ws.join('|'));
    assert(lis[0].className.includes('is-done'), lis[0].className);
    assert(lis[1].className.includes('is-running'), lis[1].className);
    assert(lis[2].className.includes('is-pending'), lis[2].className);
    assert(/已完成/.test(lis[0].textContent) && /进行中/.test(lis[1].textContent) &&
           /未开始/.test(lis[2].textContent), lis[1].textContent);
    // 整体占比只累计「已完成」的权重，避免来回跳
    assert(flow.style.getPropertyValue('--rp-pct').trim() === '5%',
           '占比填充=' + flow.style.getPropertyValue('--rp-pct'));
    assert(lis[1].getAttribute('aria-current') === 'step', '进行中应标 aria-current');
  });

  // 推进一格：必须复用同一批 DOM 节点（否则 width 过渡与脉冲动画每 1.5s 从头开始）
  const flowNode = box.querySelector('.rp-flow');
  const barNode = box.querySelector('.rp-track');
  const firstLi = flowNode.querySelector('.rp-flow-i');
  rpStatus = { ...RUNNING, live: { ...RUNNING.live, pct: 55,
    steps: FLOW_STEPS(['done', 'done', 'running', 'pending', 'pending']) } };
  w.renderRepair(rpStatus);
  check('阶段推进：复用同一 DOM 节点，过渡动画不重置', () => {
    assert(box.querySelector('.rp-flow') === flowNode, '阶段条被重建了');
    assert(box.querySelector('.rp-flow-i') === firstLi, '步骤节点被重建了');
    assert(box.querySelector('.rp-track') === barNode, '主进度条被重建了');
    const lis = flowNode.querySelectorAll('.rp-flow-i');
    assert(lis[1].className.includes('is-done'), lis[1].className);
    assert(lis[2].className.includes('is-running'), lis[2].className);
    assert(flowNode.style.getPropertyValue('--rp-pct').trim() === '55%',
           '占比没推进：' + flowNode.style.getPropertyValue('--rp-pct'));
  });

  // 失败收口：停在哪一步一眼可见，后续仍是「未开始」
  rpStatus = { ...DONE, status: 'done', live: { ...RUNNING.live, pct: 5, final: 'failed',
    steps: FLOW_STEPS(['done', 'failed', 'pending', 'pending', 'pending']) } };
  w.renderRepair(rpStatus);
  check('失败收口：停住的步骤标红并写「失败」，后续保持未开始', () => {
    const lis = box.querySelectorAll('.rp-flow-i');
    assert(lis[1].className.includes('is-failed'), lis[1].className);
    assert(/失败/.test(lis[1].textContent), lis[1].textContent);
    assert(lis[2].className.includes('is-pending'), lis[2].className);
    assert(/已中止|失败/.test(txt('#rp-box .rp-head')), txt('#rp-box .rp-head'));
  });

  // 中止收口
  rpStatus = { ...DONE, status: 'stopped', live: { ...RUNNING.live, pct: 5, final: 'stopped',
    steps: FLOW_STEPS(['done', 'stopped', 'pending', 'pending', 'pending']) } };
  w.renderRepair(rpStatus);
  check('中止收口：当前步骤标「已中止」，阶段条仍保留', () => {
    const lis = box.querySelectorAll('.rp-flow-i');
    assert(lis[1].className.includes('is-stopped'), lis[1].className);
    assert(/已中止/.test(lis[1].textContent), lis[1].textContent);
    assert(box.querySelector('.rp-flow'), '中止后阶段条不该消失');
  });

  rpStatus = RUNNING;
  w._rpStopPoll();

  dom.window.close();
  console.log(`\n通过 ${total - fails} / ${total}`);
  process.exit(fails ? 1 : 0);
})();
