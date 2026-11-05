/* ============================================================
   derive.js · /api/benchmark → 页面数据（S096 与 bench2 共用）
   规则与 100 方案项目 tools/gen-data.mjs 一致：
     · 接口数值原样保留
     · 只新增可复核派生量：名次 / 占比 / 近 7 天 / 最高单条 / 中位数
   ============================================================ */
(function () {
  function pad2(n) { return String(n).padStart(2, '0'); }

  function median(arr) {
    const s = arr.slice().sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function mean(arr) {
    return Math.round(arr.reduce((s, x) => s + x, 0) / (arr.length || 1));
  }

  function derive(j) {
    const ts = new Date((j.ts || Date.now() / 1000) * 1000);
    const asOf = ts.getFullYear() + '-' + pad2(ts.getMonth() + 1) + '-' + pad2(ts.getDate()) +
      ' ' + pad2(ts.getHours()) + ':' + pad2(ts.getMinutes());

    const videos = (j.videos || []).map((v) => ({
      vid: v.vid,
      account: v.account,
      title: v.title,
      desc: String(v.desc || '').slice(0, 260),
      plays: v.plays,
      likes: v.likes,
      comments: v.comments,
      shares: v.shares,
      collects: v.collects,
      publish_time: v.publish_time,
      day: String(v.publish_time).slice(5, 10),
      fullDay: String(v.publish_time).slice(0, 10),
      url: v.url,
      products: (v.products || []).map((p) => ({ product_id: p.product_id, title: p.title })),
      source: v.source,
      engagement: v.engagement
    }));

    const dayMap = new Map();
    videos.forEach((v) => {
      const d = v.fullDay;
      const cur = dayMap.get(d) || { date: d.slice(5), full: d, videos: 0, plays: 0, likes: 0 };
      cur.videos += 1;
      cur.plays += v.plays || 0;
      cur.likes += v.likes || 0;
      dayMap.set(d, cur);
    });
    const series = [...dayMap.values()].sort((a, b) => (a.full < b.full ? -1 : 1));

    const lastDay = series.length ? series[series.length - 1].full : '';
    // 无数据（或最后一天不可解析）时没有 7 日窗口起点；空串表示「不设窗口」，
    // 避免 new Date('T00:00:00') 的 Invalid Date 让 toISOString() 抛 RangeError。
    let last7Cut = '';
    if (lastDay) {
      const cut = new Date(lastDay + 'T00:00:00');
      if (!isNaN(cut.getTime())) {
        cut.setDate(cut.getDate() - 6);
        last7Cut = cut.toISOString().slice(0, 10);
      }
    }

    const byAcc = new Map();
    videos.forEach((v) => {
      const list = byAcc.get(v.account) || [];
      list.push(v);
      byAcc.set(v.account, list);
    });

    const rows = (j.accounts || []).map((a) => {
      const vs = byAcc.get(a.account) || [];
      const maxPlays = vs.reduce((m, v) => Math.max(m, v.plays || 0), 0);
      const withProd = vs.filter((v) => v.products && v.products.length).length;
      const lastPublish = vs.reduce((m, v) => (!m || v.publish_time > m ? v.publish_time : m), '');
      const recent7 = vs.filter((v) => v.fullDay >= last7Cut).length;
      return {
        account: a.account,
        lang: a.lang,
        videos: a.videos,
        total_plays: a.total_plays,
        total_likes: a.total_likes,
        avg_plays: a.avg_plays,
        avg_engagement: a.avg_engagement,
        with_products: withProd,
        product_coverage: a.product_coverage,
        top_tags: a.top_tags,
        top_hooks: a.top_hooks,
        has_scrape: a.has_scrape,
        maxPlays,
        lastPublish: lastPublish ? lastPublish.slice(0, 16) : '',
        recent7
      };
    });

    const totalPlays = j.kpis.total_plays || rows.reduce((s, a) => s + a.total_plays, 0);
    const accounts = rows.slice()
      .sort((a, b) => b.total_plays - a.total_plays || (a.account < b.account ? -1 : 1));
    accounts.forEach((a, i) => {
      a.rank = i + 1;
      a.share = Math.round((a.total_plays / totalPlays) * 1000) / 10;
    });

    const products = (j.patterns.products_top || []).map((p) => ({
      product_id: p.product_id,
      title: p.title,
      anchor_type: p.anchor_type,
      fastmoss_url: p.fastmoss_url,
      vids: p.vids,
      accounts: p.accounts,
      total_plays: p.total_plays,
      pct: p.pct,
      videos: (p.videos || []).slice(0, 3)
    }));

    const ds = j.data_sources || {};
    return {
      meta: {
        asOf,
        source: '/api/benchmark',
        demo: false,
        from: series.length ? series[0].full : '',
        to: lastDay,
        days: series.length,
        ledgerRows: ds.ledger_rows,
        ledgerOnly: ds.ledger_only,
        gmvStatus: ds.gmv_status,
        scrapeDir: ds.scrape_dir,
        productsDir: ds.products_dir,
        nVideos: videos.length,
        nAccounts: accounts.length,
        rawAccounts: (j.videos || []).length,
        accountOptions: j.account_options || []
      },
      kpis: j.kpis,
      baselines: {
        medianPlays: median(accounts.map((a) => a.total_plays)),
        meanPlays: mean(accounts.map((a) => a.total_plays)),
        medianAvgPlays: median(accounts.map((a) => a.avg_plays)),
        medianEngagement: median(accounts.map((a) => a.avg_engagement)),
        medianCoverage: median(accounts.map((a) => a.product_coverage)),
        medianVideos: median(accounts.map((a) => a.videos))
      },
      series,
      accounts,
      videos,
      patterns: {
        play_buckets: j.patterns.play_buckets,
        top_tags: j.patterns.top_tags,
        hook_words: j.patterns.hook_words,
        top_videos: j.patterns.top_videos,
        products_top: products,
        n_videos_in_sample: j.patterns.n_videos_in_sample
      },
      takeaways: j.takeaways
    };
  }

  window.BENCH_DERIVE = derive;
})();
