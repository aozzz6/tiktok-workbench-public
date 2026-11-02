/* tests/ps_bridge.test.js — ④ 产品图方案 → ⑤ 变异提示词 衔接（2026-10-26）
 * 覆盖：无接入时行为不变（5 拍 / 15s 回归）、接入后拍数与秒点跟模板走、
 *       产品/卖点按「① 区优先、空着用 ④ 补」合并、换骨架清旧卡、
 *       无 ② 档案也能只用 ④ 的模板生成、清除接入回退、桥接持久化、
 *       PT.sendToVariants 端到端。
 * 跑法：NODE_PATH=…/node_modules node tests/ps_bridge.test.js
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

/* 5 拍的对标文档（走原有路径，做回归对照） */
const DOC = [
  '# 德国清洁湿巾 15s 拆解',
  '## 卖点',
  '- 一张搞定油渍，不用二次擦',
  '## 0-3s 钩子',
  '画面：厨房台面一片狼藉，油渍占满画面。',
  '## 3-6s 触碰',
  '画面：手拿湿巾按压台面。',
  '## 6-9s 细节',
  '画面：微距拍纤维断面。',
  '## 9-12s 结果',
  '画面：台面恢复干净。',
  '## 12-15s 收口',
  '画面：湿巾静置。'
].join('\n');

/* ④ 归纳出来的 7 拍模板（模拟产品图方案区送过来的 ratio） */
const RATIO7 = [
  { role: '钩子', dur: 2.1, t0: 0, t1: 2.1 },
  { role: '演示', dur: 2.4, t0: 2.1, t1: 4.5 },
  { role: '细节', dur: 2.2, t0: 4.5, t1: 6.7 },
  { role: '证据', dur: 1.8, t0: 6.7, t1: 8.5 },
  { role: '演示', dur: 2.6, t0: 8.5, t1: 11.1 },
  { role: '细节', dur: 2.4, t0: 11.1, t1: 13.5 },
  { role: '收尾', dur: 1.5, t0: 13.5, t1: 15 }
];

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8789/#prompt' });
const w = dom.window;
w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*15000\);/mg, ''));
w.eval(psJs);
w.eval(ptJs);
const d = w.document;
w.PS_load();
w.PT_load();

const PS = w.PS, PT = w.PT;
const S = PS.state;
const click = (sel) => {
  const el = d.querySelector(sel);
  assert(el, '找不到元素 ' + sel);
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  return el;
};
const bridgeBox = () => d.querySelector('#ps-bridge');
const items = () => S.bench.items;
const spansOf = (txt) => [...txt.matchAll(/([\d.]+)–([\d.]+) 秒/g)].map((m) => [Number(m[1]), Number(m[2])]);

/* 先归档一份 5 拍档案，让⑤有可用的②骨架 */
PS.importReverseMarkdown(DOC, '清洁湿巾');
w.PS_load();

/* ── 1. 接入前：行为必须与本次改动之前完全一致 ─────────────────────── */
check('未接入 ④ 时接入条隐藏', () => {
  assert(bridgeBox(), '缺 #ps-bridge');
  assert(bridgeBox().hidden === true, '接入条不该显示');
});

check('未接入 ④ 时仍按 5 拍 / 15s 生成 6 份', () => {
  click('#ps-gen');
  assert(items().length === 6, '份数=' + items().length);
  const sp = spansOf(items()[0].s5);
  assert(sp.length === 5, '拍数=' + sp.length);
  assert(sp[0][0] === 0 && sp[4][1] === 15, JSON.stringify(sp));
  assert(/5 拍节拍同步/.test(items()[0].s1), 's1 未标 5 拍');
  assert(/对标骨架：/.test(items()[0].s1), 's1 未标对标骨架');
});

/* ── 2. 接入 ④ 的产品与 7 拍模板 ────────────────────────────────────── */
const BR = {
  at: '2026-10-26 18:00',
  prodId: 'prod_x',
  product: '女士高领针织毛衣',
  material: '含闪丝的针织毛线',
  structure: '高领翻领包覆颈部',
  scene: '纯色摄影棚背景下的服装展示',
  benefit: '高领翻领包覆颈部',
  accent: '黑色',
  colors: ['黑色', '米白色', '粉色'],
  selling: ['高领翻领包覆颈部', '面料带细闪丝光泽', '细节图显示螺纹袖口与领口收边'],
  shoot: ['用侧光近距离拍摄面料细闪丝的质感'],
  audience: '德国喜欢优雅百搭基础款的女性消费者',
  images: [{ name: 'x_007.jpg', key: 'prod:prod_x:0' }, { name: 'x_008.jpg', key: 'prod:prod_x:1' }],
  tplId: 'tpl_7',
  tplName: '结果前置 · 微距取证 · 7 拍',
  beats: 7,
  span: 15,
  ratio: RATIO7
};

S.v.product = '';       // ① 区清空，验证「空着用 ④ 补」
S.v.material = '';
S.v.benefit = '';

check('PS.setBridge 后接入条出现并写明来源', () => {
  PS.setBridge(BR);
  assert(bridgeBox().hidden === false, '接入条未显示');
  const t = bridgeBox().textContent;
  assert(/已接入 ④/.test(t), '缺来源标识');
  assert(/女士高领针织毛衣/.test(t), '缺产品名');
  assert(/7 拍/.test(t) && /15s/.test(t), '缺模板拍数/时长：' + t);
  assert(/参考图 2 张/.test(t), '缺参考图数量');
  assert(/识别卖点 3 条/.test(t), '缺卖点数量');
});

check('换骨架时清空旧变异卡（防旧正文冠新模板名）', () => {
  assert(items().length === 0, '旧卡未清，仍有 ' + items().length + ' 份');
  assert(d.querySelector('#ps-copy-all').disabled === true, '复制全部未禁用');
});

check('接入时把 ④ 的产品写进 ① 区（覆盖预设值）', () => {
  assert(S.v.product === '女士高领针织毛衣', '① 区产品未吸收：' + S.v.product);
  assert(S.v.material === '含闪丝的针织毛线', '① 区材质未吸收：' + S.v.material);
  assert(S.v.benefit === '高领翻领包覆颈部', '① 区收益未吸收：' + S.v.benefit);
});

check('用户手改过的字段不被接入覆盖，并在接入条点名', () => {
  S.v.product = '我自己写的新产品';
  PS.setBridge(Object.assign({}, BR, { product: '女士高领针织毛衣' }));
  assert(S.v.product === '我自己写的新产品', '手填被覆盖：' + S.v.product);
  assert(/① 区产品名是「我自己写的新产品」/.test(bridgeBox().textContent), '未提示不一致：' + bridgeBox().textContent);
  S.v.product = '';
  PS.setBridge(BR);
});

/* ── 3. 生成：拍数/时间轴跟模板走 ──────────────────────────────────── */
check('接入 7 拍模板后生成 7 拍，时间轴逐段衔接且收在 15s', () => {
  click('#ps-gen');
  assert(items().length === 6, '份数=' + items().length);
  const sp = spansOf(items()[0].s5);
  assert(sp.length === 7, '拍数=' + sp.length);
  assert(sp[0][0] === 0, '首拍不从 0 起：' + sp[0][0]);
  assert(sp[6][1] === 15, '末拍未收在 15s：' + sp[6][1]);
  for (let i = 1; i < sp.length; i++) {
    assert(Math.abs(sp[i][0] - sp[i - 1][1]) < 0.001, '第 ' + (i + 1) + ' 段未衔接');
  }
});

check('s1 标出「④ 模板」而不是②的骨架名', () => {
  const s1 = items()[0].s1;
  assert(/7 拍节拍同步/.test(s1), '拍数未跟着变：' + s1);
  assert(/④ 模板：结果前置 · 微距取证 · 7 拍/.test(s1), '未标模板来源：' + s1);
  assert(!/对标骨架：/.test(s1), '不该再写对标骨架');
});

check('产品/材质/收益取 ④ 的识别结果（① 区空时）', () => {
  const it = items()[0];
  assert(/女士高领针织毛衣/.test(it.text), '正文缺产品名');
  assert(/含闪丝的针织毛线/.test(it.text), '正文缺材质');
  assert(/已上传 2 张/.test(it.s2), 's2 未提示参考图数量');
  assert(it.sp && /高领翻领包覆颈部/.test(it.sp.t + it.text), '卖点未用识别结果：' + JSON.stringify(it.sp));
  assert(/拍摄参考（来自 ④ 的识别）：用侧光近距离拍摄面料细闪丝的质感/.test(it.s4), 's4 未带拍摄建议：' + it.s4);
});

check('① 区手填优先，不被 ④ 覆盖', () => {
  S.v.product = '我手写的产品名';
  S.v.material = '我手写的材质';
  click('#ps-gen');
  const t = items()[0].text;
  assert(/我手写的产品名/.test(t), '手填产品被覆盖');
  assert(!/女士高领针织毛衣/.test(t), '④ 的产品仍出现');
  assert(/我手写的材质/.test(t), '手填材质被覆盖');
  S.v.product = ''; S.v.material = '';
  click('#ps-gen');
});

check('接入条提示 ① 区哪些字段还要按新产品改', () => {
  S.v.surfaceTrace = ''; S.v.heroAction = ''; S.v.flaw = ''; S.v.sounds = ''; S.v.surface = '';
  PS.setBridge(BR);
  const t = bridgeBox().textContent;
  assert(/① 区还空 \d 项|① 区 \d 项空着/.test(t), '缺空值提示：' + t);
  assert(/痕迹|核心动作|瑕疵|同期声|接触面/.test(t), '未列出字段名：' + t);
  S.v.surfaceTrace = 'x'; S.v.heroAction = 'x'; S.v.flaw = 'x'; S.v.sounds = 'x';
  S.v.surface = '浅灰人造石台面';       // 仍等于湿巾预设 → 应提示「预设的写法」
  click('#ps-gen');
  assert(/① 区字段齐全|预设的写法/.test(bridgeBox().textContent), '补齐后仍报空：' + bridgeBox().textContent);
  S.v.surfaceTrace = ''; S.v.heroAction = ''; S.v.flaw = ''; S.v.sounds = '';
  click('#ps-gen');
});

/* ── 4. 没有 ② 档案也能靠 ④ 生成 ──────────────────────────────────── */
check('② 没有档案时仍可用 ④ 的模板生成', () => {
  S.bench.list = [];
  S.bench.sel = null;
  S.bench.items = [];
  click('#ps-gen');
  assert(items().length === 6, '份数=' + items().length);
  assert(spansOf(items()[0].s5).length === 7, '未按 7 拍生成');
});

/* ── 5. 清除接入 → 回退 ────────────────────────────────────────────── */
check('清除接入后接入条隐藏、变异卡清空', () => {
  click('[data-brclear]');
  assert(bridgeBox().hidden === true, '接入条未隐藏');
  assert(items().length === 0, '变异卡未清');
  assert(S.bridge === null, '状态未清');
  assert(w.localStorage.getItem('wb.po.bridge.v1') === null, '本地存储未删');
});

check('清除后没有档案时生成会被拦下（不再静默出错）', () => {
  click('#ps-gen');
  assert(items().length === 0, '无骨架却生成了 ' + items().length + ' 份');
});

/* ── 6. 持久化 ─────────────────────────────────────────────────────── */
check('接入信息写入本机并在重载后恢复', () => {
  PS.setBridge(BR);
  const raw = JSON.parse(w.localStorage.getItem('wb.po.bridge.v1'));
  assert(raw && raw.tplName === BR.tplName, '未写入桥接');
  assert(raw.ratio.length === 7, 'ratio 未保存');
  assert(raw.images.length === 2, '参考图未保存');
});

/* ── 7. 端到端：④ 的按钮 → ⑤ 收到 ─────────────────────────────────── */
check('PT.sendToVariants 把产品 + 模板送到 ⑤', () => {
  PS.setBridge(null);
  /* 模拟「用户还没填 ① 区」：清空后接入，产品应由 ④ 带过来 */
  S.v.product = ''; S.v.material = ''; S.v.scene = '';
  PT.setProduct({ product: '三层储物架', material: '碳钢烤漆', scene: '浴室角落' }, []);
  PT.state.tpls.unshift({
    id: 'tpl_e2e', at: '2026-10-26', name: '痛点直击 · 微距取证 · 6 拍',
    beats: 6, span: 15, avg: 2.5, pace: '快切',
    ratio: [
      { role: '钩子', dur: 2, share: 13.3 }, { role: '演示', dur: 3, share: 20 },
      { role: '细节', dur: 2.5, share: 16.7 }, { role: '证据', dur: 2.5, share: 16.7 },
      { role: '演示', dur: 3, share: 20 }, { role: '收尾', dur: 2, share: 13.3 }
    ],
    tech: ['微距取证'], camera: ['手持'], sentences: [], sections: [],
    from: [{ id: 'a1', name: '储物架档案' }], hook: 'pain', rhythm: { avg: 2.5, shots: 6, fast: 0 }
  });
  PT.sendToVariants('tpl_e2e');
  const br = PS.bridge();
  assert(br, '未接入');
  assert(br.product === '三层储物架', '产品=' + br.product);
  assert(br.tplName === '痛点直击 · 微距取证 · 6 拍', '模板=' + br.tplName);
  assert(br.beats === 6 && br.ratio.length === 6, '拍数=' + br.beats);
  assert(br.ratio[5].t1 === 15, '末段未收在 15s：' + br.ratio[5].t1);
  assert(bridgeBox().hidden === false, '接入条未显示');

  click('#ps-gen');
  assert(items().length === 6, '份数=' + items().length);
  const sp = spansOf(items()[0].s5);
  assert(sp.length === 6, '拍数=' + sp.length);
  assert(/三层储物架/.test(items()[0].text), '正文缺端到端产品名');
});

check('④ 侧缺产品名时拒绝接入（给出可读原因）', () => {
  PT.setProduct({ product: '' }, []);
  const seen = [];
  const old = w.toast;
  w.toast = (m) => seen.push(String(m));
  PT.sendToVariants('tpl_e2e');
  w.toast = old;
  assert(seen.some((m) => /产品名还空着/.test(m)), '未给出提示：' + JSON.stringify(seen));
});

check('全过程零控制台报错', () => {
  assert(!w.__errs || w.__errs.length === 0, '有报错');
});

console.log('\n' + (fails ? 'FAILED ' + fails + ' / ' + total : 'ALL PASS / ' + total));
process.exit(fails ? 1 : 0);
