/* tests/prompt_bench_hero.test.js — 提示词优化 · 对标区改版（2026-10-30）
   断言 ① 单元的三态：有对标（主卡）/ 无对标（空态引导）/ 已出六版（步骤条走到第 3 步）。
   运行：NODE_PATH=<jsdom 所在 node_modules> node tests/prompt_bench_hero.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const CORE = fs.readFileSync(path.join(ROOT, 'static/prompt_v2/core.js'), 'utf8');

let ok = 0, fail = 0;
function check(name, fn) {
  try { fn(); console.log('  OK  ' + name); ok++; }
  catch (e) { console.log('  FAIL ' + name + ' -> ' + e.message); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m); }

function unit(podata) {
  const dom = new JSDOM('<!doctype html><html><body><div id="pv2" class="pv2"></div></body></html>', { runScripts: 'outside-only' });
  dom.window.eval(CORE);
  dom.window.PODATA = podata;
  return { html: dom.window.U.source({ editable: true }), win: dom.window };
}

const bench = {
  id: '7696392974585941280', name: '报告 941280 · 5 分镜', account: 'example_account_051',
  desc: '前半段为白墙窗边的木桌室内场景，随后切到汽车座椅。', thumb: '/tk_reverse/7696392974585941280/frames/f_001.jpg',
  reportNo: '941280', duration: 10, shots: 5, refs: 15, mode: '原片还原', source: 'S1 …'
};
const base = { form: { source: '', duration: '10', language: '', ratio: '9:16', mode: 'text' }, versions: [], analysis: null };

check('有对标 → 主卡带账号/报告号/指标/看报告', () => {
  const { html } = unit({ ...base, benchmark: bench, benchmarks: [bench] });
  assert(html.indexOf('① 选一条对标') >= 0, '缺标题');
  assert(html.indexOf('u-hero') >= 0, '缺主卡');
  assert(html.indexOf('@example_account_051') >= 0, '缺账号');
  assert(html.indexOf('报告 941280') >= 0, '缺报告号副标识');
  assert(html.indexOf('5 分镜') >= 0, '缺分镜数');
  assert(html.indexOf('data-drawer="bench:7696392974585941280"') >= 0, '缺看报告入口');
  assert(html.indexOf('素材库 · 1') >= 0, '缺素材库入口');
});

check('无对标 → 空态引导（导入 / 去反推）', () => {
  const { html } = unit({ ...base, benchmark: null, benchmarks: [] });
  assert(html.indexOf('还没有对标素材') >= 0, '缺空态文案');
  assert(html.indexOf('从反推报告导入') >= 0, '缺导入 CTA');
  assert(html.indexOf('去提示词反推') >= 0, '缺去反推链接');
  assert(html.indexOf('u-hero-empty') >= 0, '缺空态样式钩子');
});

check('步骤条：选了对标 → 第 2 步高亮；出了六版 → 第 3 步高亮', () => {
  const a = unit({ ...base, benchmark: bench, benchmarks: [bench] }).html;
  assert(/u-st on"[^>]*data-goto="product"/.test(a) || a.indexOf('class="u-st on" data-goto="product"') >= 0,
    '第 2 步应高亮：' + (a.match(/<button[^>]*data-goto="product"[^>]*>/) || [''])[0]);
  const b = unit({ ...base, benchmark: bench, benchmarks: [bench], versions: [{ id: 'v1' }] }).html;
  assert(b.indexOf('class="u-st on" data-goto="versions"') >= 0,
    '第 3 步应高亮：' + (b.match(/<button[^>]*data-goto="versions"[^>]*>/) || [''])[0]);
});

check('粘贴正文默认折叠，但输入框与计数仍在', () => {
  const { html } = unit({ ...base, benchmark: bench, benchmarks: [bench] });
  assert(html.indexOf('u-src-paste" hidden') >= 0, '粘贴区应默认折叠');
  assert(html.indexOf('data-field="source"') >= 0, '缺正文输入框');
  assert(html.indexOf('解析并归档') >= 0, '缺归档按钮');
});

check('旧档案（无 account/thumb）用兜底摘要，不显示 undefined', () => {
  const old = { id: '7700938992656583958', name: '报告 583958 · 1 分镜', duration: 15.1, shots: 1, refs: 3, mode: '原片还原', source: 'S1 …' };
  const { html } = unit({ ...base, benchmark: old, benchmarks: [old] });
  assert(html.indexOf('undefined') < 0, '出现了 undefined');
  assert(html.indexOf('15.1 秒片子拆成 1 个镜头') >= 0, '缺兜底摘要');
});

console.log('\n通过 ' + ok + ' / ' + (ok + fail));
process.exit(fail ? 1 : 0);
