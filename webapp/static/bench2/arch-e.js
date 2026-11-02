/* ============================================================
   arch-e.js · A9 值守大屏 / A10 象限落点
   ============================================================ */
(function () {
  const S = window.BS, U = window.BU;
  const ARCH = window.ARCH;

  /* ── A9 值守大屏 ───────────────────────────────────────── */
  ARCH.A9 = {
    name: '值守大屏',
    render(D) {
      const k = D.kpis;
      const tick = [
        '样本 ' + S.int(D.videos.length) + ' 条视频 · ' + k.accounts + ' 个账号',
        '总播放 ' + S.int(k.total_plays) + ' · 总点赞 ' + S.int(k.total_likes),
        '平均互动率 ' + k.avg_engagement + '% · 挂车率 ' + k.product_coverage + '%',
        '播放前三：' + D.accounts.slice(0, 3).map((a) => a.account + ' ' + S.nf(a.total_plays)).join(' · '),
        '79.0% 的视频播放不到 1 千 · 头部集中',
        '出单 / GMV 未接入 · 快照 ' + D.meta.asOf
      ].join('　／　');

      return '<div class="pg a9">' +
        '<header class="a9-top">' +
          '<div class="a9-brand"><span class="b-mark">TT</span><div><b>对标值守屏</b><small>BENCH WALL · ' + S.esc(D.meta.asOf) + '</small></div></div>' +
          '<div class="a9-clock" data-clock>--:--:--</div>' +
          '<div class="a9-top-r">' + U.stamps(D) + U.actions() + '</div>' +
        '</header>' +
        U.kpis(D, { variant: 'mega' }) +
        '<div class="a9-main">' +
          '<section class="a9-hero">' + U.trend(D, { n: 20, h: 210, title: '每日发布与播放', sub: D.meta.days + ' 天 · 累计值' }) + '</section>' +
          '<aside class="a9-side">' + U.topvideos(D, { n: 5, title: '播放 TOP 5' }) + U.products(D, { n: 4, title: '挂车最多' }) + '</aside>' +
        '</div>' +
        '<div class="a9-band">' +
          U.accbars(D, { variant: 'single', n: 10, title: '账号总播放 TOP 10', sub: '横向可比' }) +
          U.patterns(D, { title: '爆款规律', sub: '全样本' }) +
        '</div>' +
        '<div class="a9-band2">' + U.acctable(D, { n: 8, title: '账号对比' }) + U.videos(D, { n: 5, title: '视频明细' }) + '</div>' +
        U.takeaways(D, { variant: 'cards', sub: '6 条 · 自动推导' }) +
        U.foot(D) +
        '<div class="a9-ticker"><span class="a9-tk-l">LIVE</span><div class="a9-tk-v"><span>' + S.esc(tick) + '</span><span>' + S.esc(tick) + '</span></div></div>' +
        '</div>';
    }
  };

  /* ── A10 象限落点 ──────────────────────────────────────── */
  ARCH.A10 = {
    name: '象限落点',
    render(D) {
      const mx = D.baselines.medianAvgPlays, my = D.baselines.medianEngagement;
      const q = { hi: [], lo: [] };
      D.accounts.forEach((a) => {
        const x = a.avg_plays >= mx ? 'high' : 'low';
        const y = a.avg_engagement >= my ? 'high' : 'low';
        const key = x + '-' + y;
        (q[key] = q[key] || []).push(a);
      });
      const quad = [
        { k: 'high-high', t: '标杆', d: '高播放 · 高互动', tone: 'ok' },
        { k: 'high-low', t: '流量', d: '高播放 · 低互动', tone: '' },
        { k: 'low-high', t: '潜力', d: '低播放 · 高互动', tone: '' },
        { k: 'low-low', t: '沉寂', d: '低播放 · 低互动', tone: 'bad' }
      ];
      const legend = '<aside class="a10-leg">' + quad.map((x) => {
        const arr = (q[x.k] || []).slice().sort((a, b) => b.total_plays - a.total_plays);
        return '<div class="a10-q"><div class="a10-qh"><i class="dot ' + (x.tone || '') + '"></i><b>' + x.t + '</b>' +
          '<span class="tiny muted">' + x.d + '</span><b class="a10-qn tnum">' + arr.length + '</b></div>' +
          '<div class="a10-ql">' + arr.slice(0, 3).map((a) =>
            '<span class="b-tag" data-acc="' + S.esc(a.account) + '">' + S.esc(a.account) + '<i>' + S.nf(a.total_plays) + '</i></span>').join('') +
          (arr.length > 3 ? '<span class="tiny muted">…共 ' + arr.length + ' 个</span>' : '') + '</div></div>';
      }).join('') + '<p class="tiny muted a10-note">虚线 = 全站中位数：均播 ' + S.int(mx) + ' · 互动 ' + S.pct(my, 2) + '</p></aside>';

      return '<div class="pg a10">' +
        U.head(D) +
        U.kpis(D, { variant: 'strip' }) +
        '<div class="a10-hero">' +
          U.accbars(D, { variant: 'scatter', title: '账号落点 · 均播 × 互动率', sub: '点 = 一个账号，点大小 = 总播放' }) +
          legend +
        '</div>' +
        '<div class="a10-row">' + U.trend(D, { n: 20, kind: 'bars', title: '每日发布与播放' }) + U.patterns(D, { title: '爆款规律' }) + '</div>' +
        '<div class="a10-row2">' + U.acctable(D, { n: 6 }) + U.videos(D, { n: 4 }) + '</div>' +
        '<div class="a10-row3">' + U.products(D, { n: 3 }) + U.topvideos(D, { n: 3 }) + '</div>' +
        U.takeaways(D, { variant: 'list' }) +
        U.foot(D) +
        '</div>';
    }
  };
})();

