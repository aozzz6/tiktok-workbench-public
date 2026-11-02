// 2026-10-22 删除「差评痛点」后的浏览器回归：导航项 / 视图切换 / 控制台错误
process.chdir(__dirname);
const { chromium } = require('playwright');
const EXE = 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe';
const BASE = 'http://127.0.0.1:8901/';

(async () => {
  const browser = await chromium.launch({ executablePath: EXE, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  const badReq = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
  page.on('response', (r) => { if (r.status() >= 400) badReq.push(r.status() + ' ' + r.url()); });

  await page.goto(BASE, { waitUntil: 'networkidle' });

  const nav = await page.$$eval('.nav-item', (els) => els.map((e) => ({
    label: e.textContent.trim(), view: e.dataset.view || '', ext: e.classList.contains('nav-ext'),
  })));
  console.log('nav:', JSON.stringify(nav, null, 0));

  console.log('review nav count:', nav.filter((n) => n.view === 'review' || n.label.indexOf('差评') >= 0).length);
  console.log('view-review exists:', await page.$('#view-review') !== null);
  console.log('bench-review exists:', await page.$('#bench-review') !== null);

  // 切到对标分析，等渲染
  await page.click('.nav-item[data-view="bench"]');
  await page.waitForSelector('#view-bench.active', { timeout: 15000 });
  await page.waitForTimeout(2500);
  const bench = await page.evaluate(() => {
    const v = document.querySelector('#view-bench');
    return {
      active: !!v && v.classList.contains('active'),
      kpis: document.querySelectorAll('#bench-kpis .kpi-card').length,
      hasReviewSection: Array.from(v.querySelectorAll('.sec-title')).some((h) => h.textContent.indexOf('差评') >= 0),
      prNodes: document.querySelectorAll('[class*="pr-"],[class*="rvp-"]').length,
    };
  });
  console.log('bench:', JSON.stringify(bench));

  // 其余视图逐个点开，确认没有因删除而报错
  for (const view of ['reverse', 'accounts', 'overview']) {
    await page.click(`.nav-item[data-view="${view}"]`);
    await page.waitForTimeout(1200);
    const ok = await page.evaluate((v) => {
      const el = document.querySelector('#view-' + v);
      return !!el && el.classList.contains('active');
    }, view);
    console.log('view', view, 'active:', ok);
  }

  // 深链 #review 应回落到总览（无该视图）
  await page.goto(BASE + '#review', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  const deep = await page.evaluate(() => ({
    hash: location.hash,
    active: (document.querySelector('.nav-item.active') || {}).dataset ? document.querySelector('.nav-item.active').dataset.view : null,
  }));
  console.log('deeplink #review ->', JSON.stringify(deep));

  await page.screenshot({ path: 'attic/review_removal_20261022/bench_after_removal.png', fullPage: false });
  console.log('console errors:', errs.length ? JSON.stringify(errs.slice(0, 8)) : 'none');
  console.log('http >=400:', badReq.length ? JSON.stringify(badReq.filter((u) => u.indexOf('/favicon') < 0).slice(0, 8)) : 'none');
  await browser.close();
})();
