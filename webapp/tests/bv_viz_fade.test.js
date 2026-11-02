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
let reduced = false;
w.matchMedia = (q) => ({
  matches: reduced && /reduce/.test(q), media: q,
  addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
});

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
      likes: 1, comments: 0, shares: 0, collects: 0,
      publish_time_gmt8: '2026-10-' + String(10 + (i % 5)).padStart(2, '0') + ' 10:00:00',
      url: 'https://t/1', product_id: '', tags: ['x'], hook: 'y', lang: 'en',
    })),
    patterns: { play_buckets: [], top_tags: [], hook_words: [], products_top: [], top_videos: [] },
    takeaways: [], data_sources: {},
  };
}

let payload = mkBench(5, 1500);
w.fetch = (u) => (String(u).includes('/api/benchmark')
  ? Promise.resolve({ ok: true, json: () => Promise.resolve(payload) })
  : Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
w.eval(fs.readFileSync('static/tablekit.js', 'utf8'));
w.eval(appJs.replace(/\nboot\(\);/g, '\n'));
const d = w.document;

(async () => {
  await w.loadBench(false);
  await new Promise(r => setTimeout(r, 30));
  const box = d.querySelector('#bv-viz');

  check('首屏不加深淡类（走原入场动画）', () => {
    assert(box, '缺 #bv-viz');
    assert(!box.classList.contains('bv-fading'), '首屏误加 bv-fading');
  });

  check('静默刷新（数据变化）后加深淡类', async () => {
    box.classList.remove('bv-fading');
    payload = mkBench(9, 8888);
    await w.loadBench(false);
    await new Promise(r => setTimeout(r, 30));
    assert(box.classList.contains('bv-fading'), '静默刷新未加 bv-fading');
  });

  check('连续两次静默刷新都能重播（先 remove 再 offsetWidth 回流再加）', async () => {
    payload = mkBench(11, 7777);
    await w.loadBench(false);
    await new Promise(r => setTimeout(r, 30));
    assert(box.classList.contains('bv-fading'), '第二次静默刷新未加深淡类');
    assert(/void box\.offsetWidth/.test(appJs), '缺强制回流，连续刷新不会重播过渡');
  });

  check('force 刷新（用户主动）不加深淡类', async () => {
    box.classList.remove('bv-fading');
    payload = mkBench(13, 6666);
    await w.loadBench(true);
    await new Promise(r => setTimeout(r, 30));
    assert(!box.classList.contains('bv-fading'), 'force 误加静默淡入');
  });

  check('reduced-motion 下不加深淡类', async () => {
    reduced = true;
    box.classList.remove('bv-fading');
    payload = mkBench(15, 5555);
    await w.loadBench(false);
    await new Promise(r => setTimeout(r, 30));
    assert(!box.classList.contains('bv-fading'), '减少动效时仍加淡入');
    reduced = false;
  });

  check('CSS：120ms 透明度过渡 + 不动位移/缩放 + reduced-motion 兜底', () => {
    assert(/animation:\s*tk-viz-fade\s*\.12s/.test(tkCss), '缺 120ms 动画');
    const kf = /@keyframes tk-viz-fade\s*\{[^}]*from\s*\{\s*opacity:\s*\.35;\s*\}\s*to\s*\{\s*opacity:\s*1;\s*\}\s*\}/.test(tkCss);
    assert(kf, '关键帧不是纯 opacity');
    assert(!/tk-viz-fade[\s\S]{0,80}transform/.test(tkCss), '含 transform（会产生位移）');
    assert(/prefers-reduced-motion: reduce[\s\S]{0,140}bv-fading\s*\{\s*animation:\s*none/.test(tkCss.replace(/\s+/g, ' ')), '缺 reduced-motion 兜底');
  });

  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('FATAL ' + e.message); process.exit(1); });
