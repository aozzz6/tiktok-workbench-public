/* bench_vidlink.test.js — 视频明细「视频链接」列
   校验：表头存在该列、每行链接与条目 url 一一对应、新窗口 + noopener、
   缺 url 时用 @账号+vid 兜底、无 url 无 vid 显示占位。 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const ok = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

let fails = 0;
function assert(c, msg) { if (!c) throw new Error(msg); }
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}

function mk(i, patch = {}) {
  return {
    vid: '73000000000000000' + i, account: 'acc' + i, title: '标题' + i, desc: '文案' + i,
    plays: i * 100, likes: 1, comments: 1, shares: 1, collects: 1,
    publish_time: '2026-10-18 10:00:00', engagement: 1, source: 'scrape', products: [],
    url: `https://www.tiktok.com/@acc${i}/video/73000000000000000${i}`, ...patch,
  };
}

const VIDEOS = [mk(1), mk(2), mk(3, { url: '' }), mk(4, { url: '', vid: '' })];
const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.fetch = () => ok({});
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*\);/mg, ''));
const d = w.document;
const render = () => w.renderBench({
  ts: Date.now() / 1000, kpis: {}, accounts: [], account_options: [{ account: 'acc1' }],
  videos: VIDEOS, patterns: {}, takeaways: [],
});
render();
const rows = () => [...d.querySelectorAll('#bench-videos tbody tr')];
const cell = (tr) => tr.querySelector('a.vid-link');

check('表头：存在「视频链接」列且在「标题 / 文案」之前', () => {
  const hs = [...d.querySelectorAll('#bench-videos thead th')].map((x) => x.textContent.trim());
  const i = hs.indexOf('视频链接');
  assert(i > -1, '缺视频链接列表头：' + hs.join('|'));
  assert(hs[i + 1] === '标题 / 文案', '列序不对：' + hs.slice(i).join('|'));
});

check('列数：表头与单元格数一致（12 列）', () => {
  const hn = d.querySelectorAll('#bench-videos thead th').length;
  const cn = rows()[0].cells.length;
  assert(hn === 12, '表头应 12 列，实得 ' + hn);
  assert(hn === cn, `表头 ${hn} 列 / 单元格 ${cn} 列不一致`);
});

check('关联：每行链接 href 与该条目 url 完全一致', () => {
  const fmt = w.eval('bvFiltered()');
  const want = (v) => v.url || (v.vid ? `https://www.tiktok.com/@${v.account}/video/${v.vid}` : '');
  rows().forEach((tr, i) => {
    if (!fmt[i].url && !fmt[i].vid) return;   // 双缺行走占位分支，另有专项校验
    const a = cell(tr);
    assert(a, '第 ' + (i + 1) + ' 行缺链接');
    assert(a.getAttribute('href') === want(fmt[i]), '第 ' + (i + 1) + ' 行 href 与条目 url 不一致');
    assert(a.getAttribute('target') === '_blank', '第 ' + (i + 1) + ' 行缺 target=_blank');
    assert(/noopener/.test(a.getAttribute('rel') || ''), '第 ' + (i + 1) + ' 行缺 rel=noopener');
    assert(a.getAttribute('title') === want(fmt[i]), '第 ' + (i + 1) + ' 行 title 未暴露完整链接');
  });
});

check('兜底：url 为空时用 @账号 + vid 拼原链', () => {
  const fmt = w.eval('bvFiltered()');
  const i = fmt.findIndex((v) => !v.url && v.vid);
  assert(i > -1, '测试数据缺「无 url 有 vid」条目');
  const a = cell(rows()[i]);
  assert(a, '兜底行未渲染链接');
  assert(a.getAttribute('href') === `https://www.tiktok.com/@${fmt[i].account}/video/${fmt[i].vid}`,
         '兜底链接不对：' + a.getAttribute('href'));
});

check('占位：url 与 vid 都缺时显示 —，不产生死链', () => {
  const fmt = w.eval('bvFiltered()');
  const i = fmt.findIndex((v) => !v.url && !v.vid);
  assert(i > -1, '测试数据缺「无 url 无 vid」条目');
  const tr = rows()[i];
  assert(!cell(tr), '无数据行不应有链接');
  assert(/—/.test(tr.cells[10].textContent), '缺占位符 —');
});

check('vid 尾码：链接旁显示 vid 后 6 位，与条目对应', () => {
  const fmt = w.eval('bvFiltered()');
  rows().forEach((tr, i) => {
    const code = tr.querySelector('.vid-code');
    if (!fmt[i].vid) { assert(!code, '第 ' + (i + 1) + ' 行不该有尾码'); return; }
    assert(code, '第 ' + (i + 1) + ' 行缺 vid 尾码');
    assert(code.textContent === String(fmt[i].vid).slice(-6), '第 ' + (i + 1) + ' 行尾码不对');
    assert(cell(tr).getAttribute('data-vid') === String(fmt[i].vid), '第 ' + (i + 1) + ' 行 data-vid 不对');
  });
});

dom.window.close();
console.log(`\n通过 ${6 - fails} / 6`);
process.exit(fails ? 1 : 0);
