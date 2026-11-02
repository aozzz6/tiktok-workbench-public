// 真实浏览器验收：视频明细时间筛选新增「近 1 天 / 近 3 天」
process.chdir(__dirname);
const { chromium } = require('playwright');

const EXE = 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe';

(async () => {
  const browser = await chromium.launch({ executablePath: EXE, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));

  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.click('.nav-item[data-view="bench"]');
  await page.waitForSelector('#bench-videos tbody tr', { timeout: 20000 });

  const opts = await page.$$eval('#bv-days option', (os) => os.map((o) => `${o.value}|${o.textContent.trim()}`));
  console.log('选项:', JSON.stringify(opts));

  const read = async () => page.evaluate(() => ({
    count: (document.querySelector('#bv-count') || {}).textContent,
    rows: document.querySelectorAll('#bench-videos tbody tr').length,
    sel: document.querySelector('#bv-days').value,
  }));

  const apply = async (v) => {
    await page.selectOption('#bv-days', v);
    await page.waitForTimeout(700);   // 等淡入/重绘
    return read();
  };

  for (const v of ['', '1', '3', '7', '30', '90']) {
    const r = await apply(v);
    console.log(`days=${JSON.stringify(v)} → select=${JSON.stringify(r.sel)} 行数=${r.rows} 计数=${r.count}`);
  }
  const shot = async (v, file) => {
    await apply(v);
    await page.evaluate(() => document.querySelector('#bv-days').scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(400);
    await page.screenshot({ path: file });
  };
  await shot('3', '_shot_days_3_20261021.png');
  await shot('1', '_shot_days_1_20261021.png');
  // 下拉展开后的选项清单（DOM 级，原生弹层不入截图）
  const labels = await page.$$eval('#bv-days option', (os) => os.map((o) => o.textContent.trim()));
  console.log('可见文案:', labels.join(' / '));

  console.log('控制台错误:', errs.length ? errs.slice(0, 5) : '无');
  await browser.close();
})();
