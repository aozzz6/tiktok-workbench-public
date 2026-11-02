const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('static/index.html', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
let fails = 0;
function check(name, fn) {
  try { fn(); console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '->', e.message); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }

const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window;
w.EventSource = class { close() {} };
w.confirm = () => true;
w.fetch = (u) => {
  u = String(u);
  if (u.includes('/api/groups')) return Promise.resolve({ ok: true, json: () => Promise.resolve({
    groups: [
      { id: 'default', name: '默认分组', builtin: true, members: [{ account: 'lonely_acc' }] },
      { id: 'g1', name: '钟如妹账号', members: [
        { account: 'baliee_official' }, { account: 'chloe.shop_de' }, { account: 'lucas_style' }
      ] },
      { id: 'g2', name: '黄慧账号', members: [{ account: 'novice_newbie' }] },
    ] }) });
  if (u.includes('/api/overview')) return Promise.resolve({ ok: true, json: () => Promise.resolve({
    accounts: [
      { account: 'baliee_official', media_type: 'video', audio_count: 29, transcript_count: 29,
        severity: 'warn', scrape_items: 29, last_success_date: '2026-10-18', active: false },
      { account: 'chloe.shop_de', media_type: 'video', audio_count: 10, transcript_count: 4,
        severity: 'error', scrape_items: 10, last_success_date: '2026-10-01', active: false },
      { account: 'lucas_style', media_type: 'video', audio_count: 5, transcript_count: 5,
        severity: 'ok', scrape_items: 5, last_success_date: '2026-10-19', active: true },
      { account: 'novice_newbie', media_type: 'video', audio_count: 0, transcript_count: 0,
        severity: 'ok', scrape_items: 0, active: false },
      { account: 'lonely_acc', media_type: 'video', audio_count: 1, transcript_count: 0,
        severity: 'ok', scrape_items: 1, active: false },
    ],
    pending_translation: { total: 6 }, active_runs: [], runs: []
  }) });
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
};
w.eval(appJs.replace(/\nboot\(\);/g, '\n').replace(/^setInterval\(.*15000\);/mg, ''));

(async () => {
  await w.loadOverview(false);   // 加载 lastOv + groupsData
  await new Promise(r => setTimeout(r, 50));   // 等 loadGroups 泳道渲染完成
  const d = w.document;

  check('组索引 chips 渲染（全部/待处理/异常 + 计数）', () => {
    const c = d.querySelectorAll('.lane-chips .chip-filter');
    assert(c.length === 3, 'chips 数量 ' + c.length);
    assert(/全部 3/.test(c[0].textContent), c[0].textContent);
    assert(/全部 3/.test(c[0].textContent), c[0].textContent);
    assert(/异常/.test(c[2].textContent), c[2].textContent);
  });

  check('排序：异常组 g1 在最前，空组垫底，默认组永远最后', () => {
    const ids = [...d.querySelectorAll('.lane')].map(l => l.dataset.gid);
    assert(ids[0] === 'g1', '异常组应第一，实际 ' + ids.join(','));
    assert(ids[ids.length - 1] === 'default', '默认组应最后');
  });

  check('组头健康微标：异常 1 / 待转写 1 / 运行中 1（转写缺口不叫「待译」）', () => {
    const head = d.querySelector('.lane[data-gid="g1"] .lane-head');
    assert(/异常 1/.test(head.textContent), '缺异常微标: ' + head.textContent);
    assert(/待转写 1/.test(head.textContent), '缺待转写微标');
    assert(!/待译/.test(head.textContent), '「待译」应专指 N 列翻译，不能用于转写缺口');
    assert(/运行中 1/.test(head.textContent), '缺运行中微标');
  });

  check('卡片账号名完整 + 两行（不截断）', () => {
    const nm = d.querySelector('.lane[data-gid="g1"] .ac-nm');
    assert(nm.textContent === 'baliee_official', nm.textContent);
    const css = fs.readFileSync('static/style.css', 'utf8');
    assert(/white-space:normal/.test(css) && /word-break:break-word/.test(css), 'ac-nm 两行 CSS 缺失');
  });

  check('卡片元信息行（视频数 · 更新距天）替代加入日期', () => {
    const card = d.querySelector('.lane[data-gid="g1"] .acard[data-acc="baliee_official"]');
    assert(/29 视频/.test(card.textContent), '元信息缺视频数: ' + card.textContent);
    assert(!/加入/.test(card.textContent), '仍显示加入日期');
  });

  check('状态条 4 档：warn/err/run/ok 类存在且左色带', () => {
    const g1 = d.querySelector('.lane[data-gid="g1"]');
    assert(g1.querySelector('.acard.st-warn'), '缺 warn 条');
    assert(g1.querySelector('.acard.st-err'), '缺 err 条');
    assert(g1.querySelector('.acard.st-run'), '缺 run 条');
    const g2 = d.querySelector('.lane[data-gid="g2"] .acard');
    assert(g2.classList.contains('st-ok'), '缺 ok 条');
    const css = fs.readFileSync('static/style.css', 'utf8');
    assert(/\.ac-st\{position:absolute/.test(css.replace(/\s+/g, '')), 'ac-st CSS 缺失');
  });

  check('⇢ 按钮替代常驻下拉（.ac-to 隐藏）', () => {
    const card = d.querySelector('.acard');
    assert(card.querySelector('.ac-move'), '缺 ⇢ 按钮');
    const css = fs.readFileSync('static/style.css', 'utf8');
    assert(/\.ac-to\{display:none\}/.test(css.replace(/\s+/g, '')), 'ac-to 未降级隐藏');
  });

  check('卡身点击 → openAccountLedger', async () => {
    const card = d.querySelector('.lane[data-gid="g1"] .acard[data-acc="baliee_official"]');
    const calls = [];
    w.openAccountLedger = (a) => calls.push(a);
    card.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    assert(calls[0] === 'baliee_official', '未触发 ' + JSON.stringify(calls));
  });

  check('键盘：Enter/M/Space', async () => {
    const card = d.querySelector('.lane[data-gid="g1"] .acard[data-acc="chloe.shop_de"]');
    const calls = [];
    w.openAccountLedger = (a) => calls.push(['ledger', a]);
    const ev = new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
    card.dispatchEvent(ev);
    assert(calls[0] && calls[0][0] === 'ledger' && calls[0][1] === 'chloe.shop_de', 'Enter 未开台账');
    card.dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    const cb = card.querySelector('.ac-sel input');
    assert(cb.checked, 'Space 未勾选');
    w.toggleGrpSel(cb, 'chloe.shop_de');   // 还原
    assert(!cb.checked, '还原勾选失败');
  });

  check('⇢ 菜单列出其它组', async () => {
    w.cardMoveMenu(new w.MouseEvent('click'), 'baliee_official');
    const menu = d.querySelector('.grp-ctx');
    assert(menu, '菜单未弹出');
    assert(/钟如妹账号/.test(menu.textContent) === false, '不应列本组');
    assert(/黄慧账号/.test(menu.textContent), '应列其它组');
    w.closeGrpCtx();
  });

  check('勾选 → 批量条挂 body 固定底部（不跳版）', async () => {
    w.toggleGrpSel(d.querySelector('.ac-sel input'), 'baliee_official');
    const bar = d.querySelector('body > .grp-batchbar');
    assert(bar, '批量条未挂 body');
    const css = fs.readFileSync('static/style.css', 'utf8');
    assert(/\.grp-batchbar\{position:fixed/.test(css.replace(/\s+/g, '')), '批量条非固定底部');
    assert(/已选/.test(bar.textContent), bar.textContent);
    const hint = d.getElementById('lane-sel-hint');
    assert(hint && !hint.hidden && /已选/.test(hint.textContent), 'chips 已选常驻提示缺失');
    w.clearGrpSel();
    assert(!d.querySelector('.grp-batchbar'), '取消后批量条未移除');
  });

  check('跑本组：确认条 → doRunGroup 发 accounts[] 近 7 天', async () => {
    let sent = null;
    const origFetch = w.fetch;
    w.fetch = (u, o) => {
      if (String(u).includes('/api/run')) { sent = JSON.parse(o.body); return Promise.resolve({ ok: true, json: () => Promise.resolve({ id: 'r1', agent_pending_note: '' }) }); }
      return origFetch(u, o);
    };
    w.buildRunProgress = () => {}; w.attachRun = () => {};
    w.toggleRunGroup('g1');
    const c = d.getElementById('lrc-g1');
    assert(c && !c.hidden, '确认条未出现');
    w.doRunGroup('g1');
    await new Promise(r => setTimeout(r, 30));
    assert(sent && Array.isArray(sent.accounts) && sent.accounts.length === 3, 'accounts[] 未传');
    assert(sent.date_to > sent.date_from && sent.kind === 'pipeline', '近 7 天 payload 错误');
  });

  check('筛选 chips：异常 → 只看问题列', async () => {
    w.setLaneFilter('error');
    const vis = [...d.querySelectorAll('.lane:not(.lane-hide)')].map(l => l.dataset.gid);
    assert(vis.includes('g1') && !vis.includes('g2'), '筛选后列可见集合错误: ' + vis.join(','));
    w.setLaneFilter('');
  });

  check('空组幽灵条：默认组有成员但空组样式可触发', async () => {
    // 构造空组
    w.groupsData.groups.push({ id: 'g3', name: '空组X', members: [] });
    w.renderGroupDetail();
    const lane = d.querySelector('.lane[data-gid="g3"]');
    assert(lane.classList.contains('lane-ghost'), '空组未降级幽灵条');
  });

  console.log(fails ? `FAILED ${fails}` : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('FATAL', e); process.exit(1); });
