/* tests/ps_pipeline.test.js — 模板流水线（③ 模板归纳 / ④ 产品图→批量方案）2026-10-26
 * 覆盖：档案→模板（拍序/时长占比/镜头/句式）、多档合并取交集、
 *       产品信息→按模板批量生成、按模板分组渲染、变体差异、时间轴累加、
 *       生成前置校验、模板持久化与删除、德语口播复用 ① 区
 * 跑法：NODE_PATH=…/node_modules node tests/ps_pipeline.test.js
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const psJs = fs.readFileSync('static/prompt_studio.js', 'utf8');
const ptJs = fs.readFileSync('static/ps_pipeline.js', 'utf8');

let fails = 0, total = 0;
function check(name, fn) {
  total++;
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }

/* 两份风格不同的对标文档：5 拍清洁湿巾 / 6 拍储物架 */
const DOC_A = [
  '# 德国清洁湿巾 15s 拆解',
  '## 卖点',
  '- 一张搞定油渍，不用二次擦',
  '- 无纺布不掉毛',
  '## 0-3s 钩子',
  '画面：厨房台面一片狼藉，油渍占满画面。',
  '口播：「Nach dem Kochen sieht die Arbeitsfläche so aus.」',
  '## 3-6s 演示',
  '画面：一只手抽出一张湿巾横向擦过台面。',
  '## 6-9s 细节',
  '画面：微距推到布面，油渍被推成一团。',
  '## 9-12s 证据',
  '画面：台面回到干净状态，3er-Pack 摆在旁边。',
  '## 12-15s 收口',
  '口播：「Im 3er-Pack – Nachschub für zwei Wochen.」'
].join('\n');

const DOC_B = [
  '# 金属储物架 18s 拆解',
  '## 卖点',
  '- 三层每层承重 5kg',
  '- 免工具安装',
  '## 0-2s 钩子',
  '画面：门厅堆满杂物，人侧身挤过去。',
  '## 2-5s 痛点',
  '口播：「Im Flur stapelt sich alles.」',
  '## 5-9s 演示',
  '画面：第一次人称视角把架子展开到位。',
  '## 9-12s 细节',
  '画面：微距拍冲孔层板与四脚可调。',
  '## 12-15s 证据',
  '口播：「Drei Ebenen, je 5 kg Traglast.」',
  '## 15-18s 收口',
  '口播：「Link in der Beschreibung.」'
].join('\n');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8789/#prompt' });
const w = dom.window;
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*15000\);/mg, ''));
w.eval(psJs);
w.eval(ptJs);
const d = w.document;
w.PS_load();

const PT = w.PT, PS = w.PS;
const click = (sel) => {
  const el = d.querySelector(sel);
  assert(el, '找不到元素 ' + sel);
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  return el;
};
const plans = () => d.querySelectorAll('#pt-plans .pt-group');
const allText = () => [...d.querySelectorAll('#pt-plans .pt-pre')].map((x) => x.textContent).join('\n');

/* ── 1. 区块接入 ─────────────────────────────────────────────────────── */
check('③ 模板归纳 / ④ 产品图与方案两个新区已接入视图', () => {
  assert(d.querySelector('#ps-sec-tpl'), '缺 #ps-sec-tpl');
  assert(d.querySelector('#ps-sec-gen'), '缺 #ps-sec-gen');
  assert(/③ 模板归纳/.test(d.querySelector('#ps-sec-tpl h2').textContent), '③ 标题不对');
  assert(/④ 产品图/.test(d.querySelector('#ps-sec-gen h2').textContent), '④ 标题不对');
});

check('跳转条含 6 个区块入口', () => {
  const anchors = [...d.querySelectorAll('.ps-head-anchors [data-jump]')];
  assert(anchors.length === 6, '入口数=' + anchors.length);
  const jumps = anchors.map((a) => a.dataset.jump);
  ['sec-var', 'sec-bench', 'sec-tpl', 'sec-gen', 'sec-variant', 'sec-prompt'].forEach((k) => {
    assert(jumps.indexOf(k) >= 0, '缺 ' + k);
  });
});

check('原 ③ 变异 / ④ 5 段式已顺延为 ⑤ / ⑥', () => {
  assert(/⑤ 变异提示词/.test(d.querySelector('#ps-sec-variant h2').textContent), '⑤ 未顺延');
  assert(/⑥ 5 段式/.test(d.querySelector('#ps-sec-prompt h2').textContent), '⑥ 未顺延');
});

check('无档案时 ③ 区给出明确空态', () => {
  assert(/还没有对标档案/.test(d.querySelector('#pt-archives').textContent), '空态文案缺失');
});

/* ── 2. 文档 → 模板 ──────────────────────────────────────────────────── */
PS.importReverseMarkdown(DOC_A, '清洁湿巾');
w.PT_load();

check('归纳单份档案得到 1 个模板，拍数沿用档案', () => {
  click('#pt-learn');
  assert(PT.state.tpls.length === 1, '模板数=' + PT.state.tpls.length);
  const t = PT.state.tpls[0];
  assert(t.beats === 5, '拍数=' + t.beats);
  assert(t.ratio.length === 5, 'ratio 长度=' + t.ratio.length);
});

check('模板抽到拍序角色（首拍钩子、末拍收尾）', () => {
  const t = PT.state.tpls[0];
  assert(t.ratio[0].role === '钩子', '首拍角色=' + t.ratio[0].role);
  assert(t.ratio[4].role === '收尾', '末拍角色=' + t.ratio[4].role);
  assert(t.ratio.some((r) => r.role === '细节'), '没识别到细节拍：' + t.ratio.map((r) => r.role).join(','));
});

check('时长占比合计 ≈100%，每拍 dur 与档案秒点一致', () => {
  const t = PT.state.tpls[0];
  const sum = t.ratio.reduce((a, r) => a + r.share, 0);
  assert(Math.abs(sum - 100) < 1.5, '占比合计=' + sum);
  assert(t.ratio.every((r) => r.dur === 3), '每拍时长=' + t.ratio.map((r) => r.dur).join(','));
});

check('模板抽到镜头词（微距）与句式（时间轴锚点/口播分工）', () => {
  const t = PT.state.tpls[0];
  assert(t.camera.indexOf('微距') >= 0, 'camera=' + t.camera.join(','));
  const ks = t.sentences.map((s) => s.k);
  assert(ks.length >= 1, '句式为空');
  assert(ks.some((k) => /时间轴|口播/.test(k)), '句式=' + ks.join(','));
});

check('模板名含钩子/手法/拍数，且记录来源档案', () => {
  const t = PT.state.tpls[0];
  assert(/\d+ 拍/.test(t.name), 'name=' + t.name);
  assert(t.from.length === 1 && t.from[0].beats === 5, 'from=' + JSON.stringify(t.from));
});

check('模板卡渲染出时长占比条与标签', () => {
  const card = d.querySelector('#pt-tpls .pt-tcard');
  assert(card, '没有模板卡');
  const segs = card.querySelectorAll('.pt-bar > span');
  assert(segs.length === 5, '占比条段数=' + segs.length);
  assert(card.querySelectorAll('.pt-tags > span').length >= 1, '标签缺失');
});

/* ── 3. 多档合并 ─────────────────────────────────────────────────────── */
PS.importReverseMarkdown(DOC_B, '储物架');
w.PT_load();
check('第二份档案进来后 ③ 区出现 2 个可选档案', () => {
  const chips = d.querySelectorAll('#pt-archives .pt-chip');
  assert(chips.length === 2, '档案 chips=' + chips.length);
});

check('勾两份后归纳 → 合并模板（拍数取均值、名字标「合并 2 份」）', () => {
  [...d.querySelectorAll('#pt-archives .pt-chip')].forEach((c) =>
    c.dispatchEvent(new w.MouseEvent('click', { bubbles: true })));
  click('#pt-learn');
  const merged = PT.state.tpls.filter((t) => /合并 2 份/.test(t.name))[0];
  assert(merged, '没生成合并模板：' + PT.state.tpls.map((t) => t.name).join(' | '));
  assert(merged.beats === 6 || merged.beats === 5, '合并拍数=' + merged.beats);
  assert(merged.from.length === 2, 'from 长度=' + merged.from.length);
});

/* ── 4. 产品 → 批量方案 ──────────────────────────────────────────────── */
check('没有产品时生成被拒（不产出方案）', () => {
  const before = PT.state.plans.length;
  click('#pt-gen');
  assert(PT.state.plans.length === before, '无产品仍生成了方案');
});

PT.setProduct({
  product: '三层金属储物架（92cm）',
  material: '哑光粉末喷涂钢架',
  structure: '高 92cm，三层，每层承重 5kg',
  scene: '柏林小公寓门厅，米色墙面',
  benefit: '杂物全部上墙，地面空出来',
  accent: '哑光黑'
});

check('产品信息落地到 ④ 区字段（可编辑）', () => {
  const input = d.querySelector('#pt-product [data-pfield="product"]');
  assert(input, '缺产品字段');
  assert(input.value === '三层金属储物架（92cm）', 'value=' + input.value);
});

// 明确模拟用户维护过的口播，避免把默认湿巾句子误作手填基线。
PS.state.voice = ['Hier siehst du das Regal.', 'So sieht die Oberfläche aus.', 'Schau dir die Details an.', 'Alles bleibt sichtbar.', 'Das ist die gezeigte Variante.'];
check('生成方案：每个启用模板出 3 份，按模板分组', () => {
  click('#pt-gen');
  const groups = plans();
  assert(groups.length === PT.state.tpls.length, '分组数=' + groups.length + '，模板数=' + PT.state.tpls.length);
  assert(groups[0].querySelectorAll('.pt-plan').length === 3, '每组份数=' + groups[0].querySelectorAll('.pt-plan').length);
  assert(/\d+ 份/.test(d.querySelector('#pt-plan-count').textContent), '方案计数未更新');
});

check('方案正文含产品名与模板拍数', () => {
  const txt = allText();
  assert(txt.indexOf('三层金属储物架（92cm）') >= 0, '正文缺产品名');
  assert(/## 二、分镜/.test(txt), '正文缺分镜段');
});

check('时间轴从 0 开始并累加到总时长', () => {
  const first = d.querySelector('#pt-plans .pt-pre').textContent;
  const heads = [...first.matchAll(/### (\d+)｜([\d.]+)–([\d.]+)s/g)].map((m) => [Number(m[1]), Number(m[2]), Number(m[3])]);
  assert(heads.length >= 5, '分镜小节=' + heads.length);
  assert(heads[0][1] === 0, '起始=' + heads[0][1]);
  for (let i = 1; i < heads.length; i++) {
    assert(Math.abs(heads[i][1] - heads[i - 1][2]) < 0.05, `第${i + 1}段起点与上一段终点不衔接`);
  }
  const last = heads[heads.length - 1][2];
  const tplSpan = PT.state.plans[0].tpl.span;
  assert(Math.abs(last - tplSpan) < 0.6, '末段终点=' + last + '，模板时长=' + tplSpan);
});

check('同一模板的 3 份方案互不相同（结果/痛点/对比三种开场）', () => {
  const cards = [...d.querySelectorAll('#pt-plans .pt-group')[0].querySelectorAll('.pt-plan')];
  const texts = cards.map((c) => c.querySelector('.pt-pre').textContent);
  assert(new Set(texts).size === 3, '有重复方案');
  assert(/结果/.test(texts[0]) && /糟糕现状/.test(texts[1]) && /分屏/.test(texts[2]), '三种开场未区分');
});

check('德语口播复用 ① 区已维护的句子', () => {
  const voice = PS.state.voice.filter(Boolean);
  const txt = allText();
  const hit = voice.filter((v) => txt.indexOf(v) >= 0).length;
  assert(hit >= 2, '命中的 ① 区德语句数=' + hit);
  assert(/## 三、德语口播/.test(txt), '缺口播段');
});

check('硬性约束含德国合规与不回字样', () => {
  const txt = allText();
  assert(/绝对化/.test(txt), '缺绝对化约束');
  assert(/logo/.test(txt), '缺 logo 约束');
  assert(/不口播价格/.test(txt), '缺价格约束');
});

check('每份有「模板依据」段（拍序 + 句式）', () => {
  const txt = allText();
  assert(/## 五、模板依据/.test(txt), '缺模板依据段');
  assert(/拍序：钩子 →/.test(txt), '缺拍序');
});

/* ── 5. 按模板分类：启用/停用影响分组 ─────────────────────────────────── */
check('停用一个模板后重新生成，分组数随之减少', () => {
  const first = PT.state.tpls[0];
  click('[data-tuse="' + first.id + '"]');
  const on = PT.state.tpls.filter((t) => PT.state.useTpl[t.id]).length;
  click('#pt-gen');
  assert(plans().length === on, '分组数=' + plans().length + '，启用模板=' + on);
  click('[data-tuse="' + first.id + '"]');            // 还原
});

check('每份方案可单复制、每组可导出（按钮存在且带定位）', () => {
  const card = d.querySelector('#pt-plans .pt-plan');
  const copy = card.querySelector('[data-pcopy]');
  assert(copy && /:/.test(copy.dataset.pcopy), '单份复制按钮缺定位');
  const g = d.querySelector('#pt-plans .pt-group');
  assert(g.querySelector('[data-pgroupcopy]'), '缺导出该组');
  assert(g.querySelector('[data-pgroupregen]'), '缺重生成');
});

check('折叠按钮可切换全文展开', () => {
  const btn = d.querySelector('#pt-plans [data-pfold]');
  const pre = d.querySelector('#pt-plans .pt-pre');
  assert(pre.classList.contains('is-clip'), '初始应折叠');
  btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  assert(!pre.classList.contains('is-clip'), '点击后未展开');
  assert(btn.textContent === '收起', '按钮文案=' + btn.textContent);
});

/* ── 6. 持久化与删除 ────────────────────────────────────────────────── */
check('模板落盘到 wb.pp.tpl.v1，刷新可恢复', () => {
  const raw = w.localStorage.getItem('wb.pp.tpl.v1');
  assert(raw, '未写入 localStorage');
  const arr = JSON.parse(raw);
  assert(Array.isArray(arr) && arr.length === PT.state.tpls.length, '落盘条数=' + (arr && arr.length));
  assert(arr[0].ratio && arr[0].ratio.length, '落盘缺 ratio');
});

check('产品信息落盘到 wb.pp.prod.v1', () => {
  const raw = w.localStorage.getItem('wb.pp.prod.v1');
  assert(raw, '未写入产品');
  const p = JSON.parse(raw);
  assert(p.edited.product === '三层金属储物架（92cm）', 'product=' + (p.edited && p.edited.product));
});

check('删除模板后计数与卡片同步', () => {
  const before = PT.state.tpls.length;
  const id = PT.state.tpls[0].id;
  click('[data-tdel="' + id + '"]');
  assert(PT.state.tpls.length === before - 1, '删除后=' + PT.state.tpls.length);
  assert(d.querySelectorAll('#pt-tpls .pt-tcard').length === before - 1, '卡片数未同步');
});

check('KPI 3 卡（模板/产品图/方案）', () => {
  const cells = d.querySelectorAll('#pt-kpi .kpi');
  assert(cells.length === 3, 'KPI 卡数=' + cells.length);
  assert(/模板/.test(cells[0].textContent) && /方案/.test(cells[2].textContent), 'KPI 文案不对');
});

/* ── 7. 复用边界：不干扰既有能力 ────────────────────────────────────── */
check('⑤ 变异提示词仍能生成（原 ③ 能力未受影响）', () => {
  click('#ps-gen');
  assert(d.querySelectorAll('#ps-vgrid .ps-vcard').length > 0, '变异卡没生成');
});

check('⑥ 5 段式提示词仍可用', () => {
  const secs = d.querySelectorAll('#ps-prompt pre');
  assert(secs.length === 5, '5 段式段数=' + secs.length);
});

check('产品回填 ① 区变量后可写回（applyVars）', () => {
  const n = PS.applyVars({ product: '回填测试产品', material: '测试材质' });
  assert(n === 2, '回填字段数=' + n);
  assert(PS.state.v.product === '回填测试产品', 'v.product=' + PS.state.v.product);
  assert(d.querySelector('#ps-form [data-vk="product"]').value === '回填测试产品', '表单未重绘');
});

console.log('\n=== ' + (fails ? 'FAILED ' + fails : 'ALL PASS') + ' / ' + total + ' ===');
process.exit(fails ? 1 : 0);
