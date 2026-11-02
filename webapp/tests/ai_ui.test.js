/* ai_ui.test.js — AI 层的前端表现（jsdom，不发真实请求）
   覆盖：
     · 模型清单填充下拉；无可用模型时「AI 诊断」开关禁用并取消勾选
     · 「测试」按钮：成功/失败都有可读提示
     · 点一键修复：勾了 AI 就带 use_ai=true + 选中 model，不勾则不带
     · 进度区 AI 行：诊断中 / 判定统计（模型·项数·调用·token）/ 降级原因
     · 明细行 AI 标签：动作+置信度、低置信度弱化、失败归因
   运行：node tests/ai_ui.test.js（NODE_PATH 指向 workspace node_modules）
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
    title: '采集重跑（全链）', reason: '失败', repairable: true, skip_reason: '',
    status: 'ok', detail: '', duration_s: 20, ai: {} }, o);

const MODELS = {
  available: true, enabled: true, default: 'gpt-4o-mini',
  models: [
    { id: 'gpt-4o-mini', name: 'GPT-4o mini', cooldown_s: 0 },
    { id: 'claude-x', name: 'Claude X', cooldown_s: 42 },
  ],
  actions: [],
};
const MODELS_NONE = { available: false, enabled: true, default: '', models: [] };

const AI_RUN = {
  status: 'running', total: 2, done: 1, percent: 50, duration_s: 30,
  counts: { ok: 1, failed: 0, skipped: 0 },
  current: { title: '台账补入账', account: 'accB', step: '台账同步（write + 终验）' },
  items: [
    ITEM({ ai: { action: 'sync_ledger', action_label: '台账同步', confidence: 0.88,
                 applied: true, reason: '数据已在磁盘', target_kind: 'ledger' } }),
    ITEM({ id: 'rp02', kind: 'ledger', kind_label: '台账同步', account: 'accB',
           status: 'pending', title: '台账补入账' }),
  ],
  ai: { enabled: true, phase: 'execute', model: 'gpt-4o-mini', degraded: false,
        error: '', judged: 2, attributed: 0, requests: 1, calls: 1, tokens: 520, truthful: true },
};

const AI_DIAG = {
  status: 'running', total: 1, done: 0, percent: 0, duration_s: 3,
  counts: { ok: 0, failed: 0, skipped: 0 }, current: null,
  items: [ITEM({ status: 'pending' })],
  ai: { enabled: true, phase: 'diagnose', model: 'gpt-4o-mini', degraded: false,
        error: '', judged: 0, attributed: 0, calls: 0, tokens: 0, truthful: true },
};

const AI_DEGRADED = {
  status: 'done', total: 2, done: 2, percent: 100, duration_s: 60,
  counts: { ok: 1, failed: 1, skipped: 0 }, current: null,
  items: [
    ITEM({ ai: { action: 'manual', action_label: '转人工', confidence: 0.7,
                 applied: true, needs_human: true, reason: '需人工核对表头' } }),
    ITEM({ id: 'rp02', status: 'failed', kind: 'collect', account: 'accB',
           title: '采集重跑（全链）', detail: '采集失败（exit=1）',
           ai: { action: 'rerun_collect', action_label: '采集重跑', confidence: 0.9,
                 applied: true,
                 attribution: { category: 'gate', cause: '门禁未过', retry_worth: false,
                                suggestion: '先看 gate_report.json' } } }),
  ],
  summary: { ok: 1, failed: 1, skipped: 0, total: 2,
             failed_items: [{ id: 'rp02', kind: 'collect', account: 'accB',
                              title: '采集重跑（全链）', reason: '采集失败（exit=1）' }] },
  ai: { enabled: true, phase: 'done', model: 'gpt-4o-mini', degraded: true,
        error: '模型未返回合法 JSON', judged: 2, attributed: 1, requests: 2, calls: 2, tokens: 900,
        truthful: false },
};

const calls = [];
let modelsPayload = MODELS;
let probePayload = { ok: true, model: 'gpt-4o-mini', latency_ms: 123, json_ok: true };
let startPayload = AI_RUN;

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = (u, opts) => {
  u = String(u);
  calls.push({ u, method: (opts && opts.method) || 'GET', body: opts && opts.body });
  if (u.startsWith('/api/ai/models')) return ok(modelsPayload);
  if (u.startsWith('/api/ai/test')) return ok(probePayload);
  if (u.startsWith('/api/repair/start')) return ok(startPayload);
  if (u.startsWith('/api/repair/status')) return ok({ status: 'idle', items: [] });
  if (u.startsWith('/api/repair/scan')) return ok({ items: [], counts: {} });
  if (u.startsWith('/api/overview')) return ok({ accounts: [], runs: [], diagnostics: {},
                                                 pending_translation: { total: 0 } });
  if (u.startsWith('/api/pipeline/status')) return ok({ takeover: [], alerts: [], receipt: {} });
  if (u.startsWith('/api/config')) return ok({ accounts: [], local_steps: [], agent_steps: [],
                                               step_labels: {}, steps: [] });
  if (u.includes('/api/groups')) return ok({ groups: [] });
  return ok({});
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));

const d = w.document;
const txt = (sel) => { const n = d.querySelector(sel); return n ? n.textContent : ''; };
const box = d.querySelector('#rp-box');

(async () => {
  check('入口：AI 开关 + 模型下拉 + 测试按钮都在待接管面板内', () => {
    const cb = d.querySelector('#tk-ai-use');
    const sel = d.querySelector('#tk-ai-model');
    const btn = d.querySelector('#tk-ai-test');
    assert(cb && sel && btn, '缺 AI 控件');
    assert(cb.closest('#takeover-panel'), '开关应在面板内');
    assert(/AI 诊断/.test(d.querySelector('#tk-ai-wrap').textContent), '缺标签文案');
    assert(cb.disabled === true, '未加载模型前应先禁用（默认保守）');
  });

  await w.loadAiModels();
  check('模型清单：填充下拉、默认选中配置模型、冷却中的标出来', () => {
    const sel = d.querySelector('#tk-ai-model');
    const opts = Array.from(sel.options).map((o) => o.textContent);
    assert(opts[0] === '自动选模型', opts.join('|'));
    assert(opts.some((t) => /GPT-4o mini/.test(t)), opts.join('|'));
    assert(opts.some((t) => /冷却中/.test(t)), '冷却状态没标: ' + opts.join('|'));
    assert(sel.value === 'gpt-4o-mini', '默认值=' + sel.value);
    assert(d.querySelector('#tk-ai-use').disabled === false, '有模型时应可用');
    // 2026-10-24：默认开启 AI 诊断（此前默认不勾，用户以为没接入模型）
    assert(d.querySelector('#tk-ai-use').checked === true, '有模型时应默认勾选 AI 诊断');
    assert(w.aiSelection().use_ai === true, 'aiSelection 应直接给出 use_ai=true');
  });

  await w.aiTest();
  check('测试连接成功：toast 带模型与毫秒', () => {
    const t = d.querySelector('#toast').textContent;
    assert(/连接正常/.test(t), t);
    assert(/gpt-4o-mini/.test(t) && /123ms/.test(t), t);
  });

  probePayload = { ok: false, model: '', error: 'HTTP 401', latency_ms: 40 };
  await w.aiTest();
  check('测试连接失败：toast 带错误，不静默', () => {
    const t = d.querySelector('#toast').textContent;
    assert(/连接失败/.test(t) && /401/.test(t), t);
  });

  check('诊断中：进度区显示「AI 诊断中」与模型名', () => {
    w.renderRepair(AI_DIAG);
    const t = txt('#rp-box .rp-ai-line');
    assert(/AI 诊断中/.test(t), t);
    assert(/gpt-4o-mini/.test(t), t);
  });

  check('统计行：模型 / 判定项数 / 物理请求数 / token', () => {
    w.renderRepair(AI_RUN);
    const t = txt('#rp-box .rp-ai-line');
    assert(/gpt-4o-mini/.test(t), t);
    assert(/判定 2 项/.test(t), t);
    assert(/模型请求 1 次/.test(t), t);
    assert(/520 tokens/.test(t), t);
  });

  check('明细行：AI 建议动作 + 置信度可见（title 带理由）', () => {
    const tag = d.querySelector('#rp-box .rp-ai');
    assert(tag, '缺 AI 标签');
    assert(/台账同步/.test(tag.textContent), tag.textContent);
    assert(/0\.88/.test(tag.textContent), tag.textContent);
    assert(/数据已在磁盘/.test(tag.getAttribute('title') || ''), 'title 缺理由');
  });

  check('降级：明显标出「已降级为规则模式」及原因', () => {
    w.renderRepair(AI_DEGRADED);
    const t = txt('#rp-box .rp-ai-line');
    assert(/已降级为规则模式/.test(t), t);
    assert(/JSON/.test(t), t);
  });

  check('转人工项：AI 标签带 is-warn，原因在 title', () => {
    const tags = Array.from(d.querySelectorAll('#rp-box .rp-ai'));
    const manual = tags.find((x) => /转人工/.test(x.textContent));
    assert(manual, '缺转人工标签');
    assert(manual.classList.contains('is-warn'), manual.className);
    assert(/需人工核对表头/.test(manual.getAttribute('title') || ''), manual.getAttribute('title'));
  });

  check('失败归因：明细出现「归因 gate」标签', () => {
    const tags = Array.from(d.querySelectorAll('#rp-box .rp-ai'));
    const attr = tags.find((x) => /归因 gate/.test(x.textContent));
    assert(attr, tags.map((x) => x.textContent).join('|'));
    assert(/门禁未过/.test(attr.getAttribute('title') || ''), attr.getAttribute('title'));
  });

  const before = calls.length;
  d.querySelector('#tk-ai-use').checked = false;
  w.repairReset();
  await w.repairStart();
  let post = calls.slice(before).find((c) => c.u.startsWith('/api/repair/start'));
  check('不勾 AI：POST 体不带 use_ai/model（纯规则模式）', () => {
    assert(post, '未发起修复');
    const b = JSON.parse(post.body);
    assert(b.use_ai === undefined && b.model === undefined, post.body);
  });

  const before2 = calls.length;
  d.querySelector('#tk-ai-use').checked = true;
  d.querySelector('#tk-ai-model').value = 'claude-x';
  w.repairReset();
  await w.repairStart();
  const post2 = calls.slice(before2).find((c) => c.u.startsWith('/api/repair/start'));
  check('勾 AI：POST 体带 use_ai=true 与选中模型', () => {
    assert(post2, '未发起修复');
    const b = JSON.parse(post2.body);
    assert(b.use_ai === true, post2.body);
    assert(b.model === 'claude-x', post2.body);
  });

  check('启动提示：勾 AI 时说明「先让 AI 复核动作」', () => {
    const t = d.querySelector('#toast').textContent;
    assert(/AI 复核动作/.test(t), t);
  });

  // 无模型：开关禁用
  modelsPayload = MODELS_NONE;
  await w.loadAiModels();
  check('无可用模型：开关禁用 + 自动取消勾选（不让用户以为 AI 生效）', () => {
    const cb = d.querySelector('#tk-ai-use');
    assert(cb.disabled === true, '应禁用');
    assert(cb.checked === false, '应取消勾选');
    assert(/没有可用的 AI 模型/.test(cb.getAttribute('title') || ''), cb.getAttribute('title'));
  });

  w._rpStopPoll();
  dom.window.close();
  console.log(`\n通过 ${total - fails} / ${total}`);
  process.exit(fails ? 1 : 0);
})();
