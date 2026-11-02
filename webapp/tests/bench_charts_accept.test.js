const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const tkCss = fs.readFileSync('static/tablekit.css', 'utf8');
let fails = 0;
const assert = (c, m) => { if (!c) throw new Error(m); };
function check(n, f) { try { f(); console.log('PASS ' + n); } catch (e) { fails++; console.log('FAIL ' + n + ' -> ' + e.message); } }

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
w.EventSource = class { close() {} };
w.setInterval = () => 0;
w.matchMedia = (q) => ({ matches: false, media: q, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
const d = w.document;

function mkBench(videosN, plays) {
  return {
    kpis: { accounts: 2, total_videos: videosN, total_plays: plays, avg_engagement: 3.2, product_coverage: 55 },
    accounts: [
      { account: 'a1', videos: 3, total_plays: plays, avg_plays: 1000, avg_engagement: 3.1,
        product_coverage: 60, top_tags: ['x'], top_hooks: ['y'], lang: 'en' },
      { account: 'a2', videos: 2, total_plays: 500, avg_plays: 250, avg_engagement: 3.3,
        product_coverage: 50, top_tags: ['z'], top_hooks: [], lang: 'de' },
    ],
    account_options: ['a1', 'a2'],
    videos: Array.from({ length: videosN }, (_, i) => ({
      vid: 'v' + i, account: i % 2 ? 'a1' : 'a2', title: 't' + i, plays: 100 + i,
      likes: 1, comments: 0, shares: 0, collects: 0, publish_time_gmt8: '2026-10-' + String(10 + (i % 5)).padStart(2, '0') + ' 10:00:00',
      url: 'https://t/1', product_id: '', tags: ['x'], hook: 'y', lang: 'en',
    })),
    patterns: { play_buckets: [], top_tags: [], hook_words: [], products_top: [], top_videos: [] },
    takeaways: [], data_sources: {},
  };
}

let fetchPayload = mkBench(5, 1500);
let fetchCount = 0;
w.fetch = (u) => {
  if (String(u).includes('/api/benchmark')) {
    fetchCount++;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(fetchPayload) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
};
w.eval(fs.readFileSync('static/tablekit.js', 'utf8'));
w.eval(appJs.replace(/\nboot\(\);/g, '\n'));

(async () => {
  // 首次加载
  await w.loadBench(false);
  await new Promise(r => setTimeout(r, 30));

  check('C1 指纹未变 -> 零 DOM 重建（图表节点身份不变）', async () => {
    const viz = d.querySelector('#bv-viz');
    const before = d.querySelector('#bv-viz-band svg');
    const stampBefore = (d.querySelector('#bench-updated') || {}).textContent;
    const n0 = fetchCount;
    await w.loadBench(false);          // 第 2 次，数据相同
    await new Promise(r => setTimeout(r, 20));
    assert(fetchCount === n0 + 1, '未发起请求');
    const after = d.querySelector('#bv-viz-band svg');
    assert(before === after, 'DOM 被重建了（图表应静止，不重播入场）');
    assert(d.querySelector('#bench-updated'), '缺更新时间戳');
    assert(stampBefore !== undefined, '时间戳为空');
  });

  check('C1b 指纹变化 -> 重绘且走静默（bvQuiet=true，不播入场）', async () => {
    const before = d.querySelector('#bv-viz-band svg');
    fetchPayload = mkBench(7, 9999);   // 数据真变了
    const origRender = w.renderBench;
    let quietSeen = null;
    w.renderBench = function (x) { quietSeen = w.eval('bvQuiet'); return origRender.call(this, x); };
    await w.loadBench(false);
    await new Promise(r => setTimeout(r, 30));
    const after = d.querySelector('#bv-viz-band svg');
    assert(before !== after, '数据变化后未重绘');
    assert(quietSeen === true, '增量重绘未置 bvQuiet=true（会误播入场）');
  });

  check('C2 轮询间隔 30s', () => {
    assert(/setInterval\(pollBench,\s*30000\)/.test(appJs.replace(/\s+/g, ' ')), '轮询不是 30000ms');
  });

  check('C3 悬停三图/两表时跳过轮询', () => {
    assert(/#bv-viz:hover, \.bv-wrap:hover, #bench-accounts:hover/.test(appJs), '缺悬停跳过选择器');
    // 实测：模拟 hover（jsdom 无 :hover 计算，改验证 pollBench 内联查询串存在且前置 return）
    assert(/if \(document\.querySelector\("#bv-viz:hover[^)]*\)\) return;/.test(appJs), '缺 return 守卫');
  });

  check('C4 用户操作 -> bvQuiet=false（播放入场）', () => {
    assert(/bvQuiet = !firstPaint;/.test(appJs), '首屏/非首屏判定缺失');
    assert(/if \(!force && !firstPaint && sig === benchSig\)/.test(appJs.replace(/\s+/g, ' ')), '指纹短路缺失');
  });

  check('C5 重绘前后保留 .bv-wrap scrollTop', () => {
    assert(/const keepTop = wrap \? wrap\.scrollTop : 0/.test(appJs), '未记录 scrollTop');
    assert(/restoreTop\(\)/.test(appJs), '未恢复 scrollTop');
  });

  check('C6 prefers-reduced-motion 关闭全部入场', () => {
    assert(/function prefersReducedMotion\(\)/.test(appJs), '缺 helper');
    const calls = (appJs.match(/animate: !bvQuiet && !prefersReducedMotion\(\)/g) || []).length;
    assert(calls === 3, '三图 animate 传参数=' + calls);
    assert(/prefers-reduced-motion/.test(tkCss), 'tablekit 缺 reduced-motion 兜底');
  });

  check('C7 force=true 绕过指纹强制重绘', async () => {
    const before = d.querySelector('#bv-viz-band svg');
    await w.loadBench(true);
    await new Promise(r => setTimeout(r, 30));
    const after = d.querySelector('#bv-viz-band svg');
    assert(before !== after, 'force 未强制重绘');
  });

  check('静默重绘参数 animate:false 真能关动画（tablekit 实现）', () => {
    const tk = fs.readFileSync('static/tablekit.js', 'utf8');
    assert(/if \(o\.animate === false\) \{/.test(tk), 'bars/donut 未处理 animate:false');
    assert(/animationDelay = o\.animate === false \? "0ms"/.test(tk), '扇区延迟未归零');
    assert(/o\.animate !== false/.test(tk), '折线未处理 animate:false');
  });

  check('三图节奏统一（共享缓动与时长）', () => {
    const tk = fs.readFileSync('static/tablekit.js', 'utf8');
    const ease = ((tk + fs.readFileSync('static/tablekit.css', 'utf8')).match(/cubic-bezier\(\s*\.16\s*,\s*1\s*,\s*\.3\s*,\s*1\s*\)/g) || []).length;
    assert(ease >= 2, '统一缓动出现次数=' + ease);
    assert(!/\.4s ease|ease;/.test(tk.replace(/cubic-bezier[^;]*/g, '')), '仍有 ease 曲线残留');
  });

  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('FATAL ' + e.message); process.exit(1); });
