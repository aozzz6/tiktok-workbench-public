// 2026-10-21 验收取证：提示词反推视图 / 报告页 / 视频明细近1天·近3天
process.chdir(__dirname);
const { chromium } = require('playwright');

const EXE = 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe';
const OUT = 'D:/ai/planA/工作台搭建/验收包_20261021_提示词反推与日更修复/证据截图/';

(async () => {
  const browser = await chromium.launch({ executablePath: EXE, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  const failedReq = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
  page.on('requestfailed', (r) => failedReq.push(r.url() + ' :: ' + (r.failure() || {}).errorText));

  // 1) 反推视图
  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.click('.nav-item[data-view="reverse"]');
  await page.waitForSelector('#view-reverse', { timeout: 15000 });
  await page.waitForTimeout(2500);
  const cards = await page.evaluate(() => {
    const v = document.querySelector('#view-reverse');
    return {
      visible: !!v && !v.classList.contains('hidden'),
      cards: v ? v.querySelectorAll('.rv-card').length : 0,
      text: v ? v.innerText.replace(/\s+/g, ' ').slice(0, 400) : '',
    };
  });
  console.log('反推视图:', JSON.stringify(cards));
  await page.screenshot({ path: OUT + '01_提示词反推视图.png' });

  // 2) 报告页（七章 + 首帧）
  const rpt = 'http://127.0.0.1:8787/tk_reverse/7698863537006939394/' + encodeURIComponent('TK反推报告_7698863537006939394.html');
  const r = await page.goto(rpt, { waitUntil: 'networkidle' });
  console.log('报告 HTTP:', r.status());
  const rptInfo = await page.evaluate(() => ({
    title: document.title,
    h2: [...document.querySelectorAll('h2')].map((h) => h.textContent.trim()),
    imgs: [...document.images].map((i) => ({ ok: i.complete && i.naturalWidth > 0, w: i.naturalWidth, h: i.naturalHeight })),
    chars: document.body.innerText.length,
  }));
  console.log('报告章节:', JSON.stringify(rptInfo.h2));
  console.log('报告图片:', rptInfo.imgs.length, '张，全部加载=', rptInfo.imgs.every((i) => i.ok), JSON.stringify(rptInfo.imgs.slice(0, 3)));
  console.log('报告文本长度:', rptInfo.chars);
  await page.screenshot({ path: OUT + '02_反推报告_首屏.png' });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.55));
  await page.waitForTimeout(500);
  await page.screenshot({ path: OUT + '03_反推报告_分镜与首帧.png' });

  // 3) 视频明细近 1 天 / 近 3 天
  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.click('.nav-item[data-view="bench"]');
  await page.waitForSelector('#bench-videos tbody tr', { timeout: 20000 });
  const opts = await page.$$eval('#bv-days option', (os) => os.map((o) => `${o.value}|${o.textContent.trim()}`));
  console.log('时间筛选选项:', JSON.stringify(opts));
  const read = async () => page.evaluate(() => ({
    count: (document.querySelector('#bv-count') || {}).textContent,
    rows: document.querySelectorAll('#bench-videos tbody tr').length,
    sel: document.querySelector('#bv-days').value,
  }));
  for (const v of ['', '1', '3', '7']) {
    await page.selectOption('#bv-days', v);
    await page.waitForTimeout(700);
    const x = await read();
    console.log(`days=${JSON.stringify(v)} select=${JSON.stringify(x.sel)} 行数=${x.rows} 计数=${x.count}`);
    if (v === '1' || v === '3') {
      await page.evaluate(() => document.querySelector('#bv-days').scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(300);
      await page.screenshot({ path: OUT + `0${v === '1' ? 4 : 5}_视频明细_近${v}天.png` });
    }
  }

  console.log('控制台错误:', errs.length ? JSON.stringify(errs.slice(0, 8)) : '无');
  console.log('失败请求:', failedReq.length ? JSON.stringify(failedReq.slice(0, 8)) : '无');
  await browser.close();
})();
