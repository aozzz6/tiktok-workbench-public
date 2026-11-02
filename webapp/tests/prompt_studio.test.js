/* tests/prompt_studio.test.js — 「提示词优化」独立板块（2026-10-26）
 * 覆盖：导航/视图接入、变量与口播渲染、5 段式引擎、对标解析与归档、
 *       六角度变异生成、角度筛选、合规扫描、反推报告入档入口
 * 跑法：NODE_PATH=…/node_modules node tests/prompt_studio.test.js
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const psJs = fs.readFileSync('static/prompt_studio.js', 'utf8');

let fails = 0;
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }

const DEMO = [
  '# 德国清洁湿巾 15s 爆款拆解（反推）',
  '来源：@sauber_berlin',
  '## 卖点',
  '- 一张搞定油渍，不用二次擦',
  '- 无纺布不掉毛，擦完不留絮',
  '- 3er-Pack 补货一次够两周',
  '## 0-3s 钩子',
  '画面：厨房台面一片狼藉，油点和面包屑占满画面。',
  '## 3-6s 触碰',
  '画面：一只手抽出一张湿巾横向擦过台面。',
  '## 6-9s 细节',
  '画面：微距推到布面，油渍被推成一团。',
  '## 9-12s 结果',
  '画面：台面回到干净状态。',
  '## 12-15s 收口',
  '口播：「Im 3er-Pack – Nachschub für die nächsten Wochen.」'
].join('\n');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8789/#overview' });
const w = dom.window;
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*15000\);/mg, ''));
w.eval(psJs);
const d = w.document;
/* jsdom 里 readyState 仍是 loading，等 DOMContentLoaded 的那条路径不会同步触发；
   与真实页面 switchView → PS_load 等价，这里直接调用一次完成挂载 */
w.PS_load();

check('导航新增「提示词优化」入口且指向 #prompt', () => {
  const nav = d.querySelector('.nav-item[data-view="prompt"]');
  assert(nav, '缺 .nav-item[data-view=prompt]');
  assert(/提示词优化/.test(nav.textContent), '文案不符：' + nav.textContent);
  assert(nav.getAttribute('href') === '#prompt', 'href=' + nav.getAttribute('href'));
  assert(nav.querySelector('svg'), '缺图标');
});

check('视图容器 #view-prompt 存在且与其它视图同级', () => {
  const v = d.querySelector('#view-prompt');
  assert(v, '缺 #view-prompt');
  assert(v.classList.contains('view'), '缺 .view');
  assert(v.querySelector('.page-head h1').textContent.trim() === '提示词优化', '标题不符');
  const ids = [...d.querySelectorAll('main .view')].map((x) => x.id);
  assert(ids.includes('view-overview') && ids.includes('view-bench') && ids.includes('view-reverse'), '原有视图被破坏');
});

check('app.js 的 switchView 已挂载 prompt 分支', () => {
  assert(/name === "prompt" && window\.PS_load/.test(appJs), '未挂载 PS_load');
});

check('模块自注册：window.PS / PS_load 可用', () => {
  assert(typeof w.PS === 'object' && w.PS, 'window.PS 缺失');
  assert(typeof w.PS_load === 'function', 'PS_load 不是函数');
});

check('变量表单 11 字段 + 德语口播 5 句 + 预设 5 类', () => {
  assert(d.querySelectorAll('#ps-form [data-vk]').length === 11, '字段数=' + d.querySelectorAll('#ps-form [data-vk]').length);
  assert(d.querySelectorAll('#ps-voice textarea').length === 5, '口播行数=' + d.querySelectorAll('#ps-voice textarea').length);
  assert(d.querySelectorAll('#ps-presets .ps-chip').length === 5, '预设数=' + d.querySelectorAll('#ps-presets .ps-chip').length);
});

check('5 段式引擎输出含产品名、德语口播与 5 拍时间轴', () => {
  const t = [...d.querySelectorAll('#ps-prompt pre')].map((x) => x.textContent).join('\n');
  assert(t.includes('【1 · 核心主题】'), '缺第 1 段');
  assert(t.includes('【5 · 分镜】'), '缺第 5 段');
  assert(t.includes('一次性清洁湿巾'), '缺产品名');
  assert(t.includes('Nach dem Kochen'), '缺德语口播');
  assert(t.includes('12–15 秒'), '缺收口拍');
});

check('切换预设：产品变量与口播整组替换', () => {
  d.querySelector('[data-preset="shelf"]').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const val = d.querySelector('#ps-form [data-vk="product"]').value;
  assert(val.includes('金属储物架'), '产品名未切换：' + val);
  assert(d.querySelector('#ps-voice textarea').value.includes('Kleiner Flur'), '口播未切换');
});

check('合规扫描命中风险词并标红该句', () => {
  const ta = d.querySelector('#ps-voice textarea');
  ta.value = 'Der beste Reiniger, 100% garantiert.';
  ta.dispatchEvent(new w.Event('input', { bubbles: true }));
  const chips = d.querySelectorAll('#ps-risk .ps-riskchip').length;
  assert(chips >= 3, '命中数=' + chips);
  assert(d.querySelectorAll('#ps-voice .ps-vline.ps-bad').length >= 1, '未标红');
});

check('反推文档入档：解析拍数/卖点/手法 + 规律卡', () => {
  const okAdd = w.PS.importReverseMarkdown(DEMO, '德国清洁湿巾 15s');
  assert(okAdd === true, '入档失败');
  const b = w.PS.state.bench.list[0];
  assert(b.beats.length === 5, '拍数=' + b.beats.length);
  assert(b.sellingPoints.length === 3, '卖点数=' + b.sellingPoints.length);
  assert(b.techniques.length >= 1, '未识别手法');
  assert(d.querySelectorAll('#ps-blist .ps-bcard').length === 1, '档案卡未渲染');
  assert(d.querySelectorAll('#ps-pattern ol li').length >= 3, '规律卡条目不足');
});

check('过短文档不入档（0 拍）', () => {
  assert(w.PS.importReverseMarkdown('只有一行字的无效文档，没有小节也没有秒点。', '坏档') === false, '坏档被收入');
});

check('六角度变异生成 6 份，第 1 拍换钩子、正文含当前产品', () => {
  d.querySelector('#ps-gen').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const cards = d.querySelectorAll('#ps-vgrid .ps-vcard');
  assert(cards.length === 6, '生成数=' + cards.length);
  const pre = cards[0].querySelector('pre').textContent;
  assert(pre.includes('金属储物架'), '正文缺产品名');
  assert(/对标骨架：德国清洁湿巾/.test(pre), '未继承对标骨架');
  assert(cards[0].querySelectorAll('.ps-var').length === 11, '变量标签数=' + cards[0].querySelectorAll('.ps-var').length);
});

check('角度筛选：取消 4 个 → 只生成 2 份；全取消自动回全选', () => {
  const pills = [...d.querySelectorAll('#ps-angles .ps-ang')];
  pills.slice(0, 4).forEach((p) => p.dispatchEvent(new w.MouseEvent('click', { bubbles: true })));
  d.querySelector('#ps-gen').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  assert(d.querySelectorAll('#ps-vgrid .ps-vcard').length === 2, '筛选后=' + d.querySelectorAll('#ps-vgrid .ps-vcard').length);
  pills.forEach((p) => { if (p.getAttribute('aria-pressed') === 'true') p.dispatchEvent(new w.MouseEvent('click', { bubbles: true })); });
  assert(d.querySelectorAll('#ps-angles .ps-ang.on').length === 6, '未恢复全选');
});

check('档案持久化键与其它板块隔离（wb.po.*）', () => {
  const raw = w.localStorage.getItem('wb.po.bench.v2');
  assert(raw && JSON.parse(raw).length === 1, 'store=' + raw);
});

check('自检 10 项可勾选并计数', () => {
  const ck = d.querySelector('#ps-checks input');
  ck.checked = true;
  ck.dispatchEvent(new w.Event('change', { bubbles: true }));
  assert(d.querySelector('#ps-ck-count').textContent.trim() === '1 / 10', '计数=' + d.querySelector('#ps-ck-count').textContent);
});

/* 2026-10-26 修：单镜头成片提示词（反推导出的 prompts.md）只有一个带秒点的小节，
   原来被「≥2 拍」门槛误拒；现放宽为「0 拍才拒」，并在规律卡标注单镜头。 */
const SINGLE = [
  '# 原片还原提示词',
  '',
  '## S1 车尾取包后沿人行道展示高腰裤并走向12号店门（连续跟拍） · 0.00–15.04s',
  '',
  '- 图片1：images/f_001.jpg（起始构图与已经形成的姿势）',
  '',
  '### 中文单图',
  '沿用图片1的起始构图和现有姿势：女性背对镜头、上身前倾站在打开的后备箱前，双臂正探向车尾区域；从这一姿势直接继续，不重演探身动作。',
  '### English',
  'Continue from the starting composition and existing pose in Image 1: the woman faces away from the camera.',
  '### 中文多图',
  '按图片编号绑定参考职责：图片1为起始构图与已形成姿势参考。'
].join('\n');

check('单镜头文档（仅 1 个秒点小节）可入档', () => {
  assert(w.PS.importReverseMarkdown(SINGLE, 'prompts') === true, '单镜头文档仍被拒');
  const rec = w.PS.state.bench.list[0];
  assert(rec.beats.length === 1, '拍数=' + rec.beats.length);
  assert(rec.beats[0].t0 === 0 && Math.abs(rec.beats[0].t1 - 15.04) < 0.01, '秒点解析错：' + JSON.stringify(rec.beats[0]));
});

check('通用 H1「原片还原提示词」回退为「文件名 · 首拍名」', () => {
  const rec = w.PS.state.bench.list[0];
  assert(/^prompts · S1 /.test(rec.name), '档案名=' + rec.name);
});

check('单镜头规律卡带「单镜头」标记且条目 ≥3', () => {
  const em = d.querySelector('.ps-pat-hd em').textContent;
  assert(/单镜头/.test(em), '未标注：' + em);
  assert(d.querySelectorAll('#ps-pattern ol li').length >= 3, '规律条=' + d.querySelectorAll('#ps-pattern ol li').length);
});

check('真正无分镜的文档仍然被拒（0 拍）', () => {
  assert(w.PS.importReverseMarkdown('随便一段没有任何小节的说明文字，长度够三十字但没有分镜结构。', '垃圾') === false, '垃圾文档被放行');
});

w.close();
console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
process.exit(fails ? 1 : 0);
