/* ============================================================
   arch-t1.js · 线上版 T1「中控台」（T106：P18 奶咖 / airy / bold）
   与画廊版的区别：① ② 是可编辑表单；③ 的按钮接真实生成；
   没有结果时 ⑤⑥ 换成引导卡；④ 未分析时给出「开始分析」。
   ============================================================ */
(function () {
  const U = window.U;
  window.ARCH = window.ARCH || {};

  function emptyAnalysis() {
    return '<section class="mod t1-empty" data-unit="analysis">' +
      '<div class="mod-h"><h2>④ 原片分析</h2><span class="hint">还没有分析结果</span></div>' +
      '<div class="mod-b t1-empty-b">' +
        '<p class="t1-empty-t">先看清原片：保留哪些、哪里有问题、换成你的产品后哪些动作必须改。</p>' +
        '<button class="btn" data-act="analyze">分析对标提示词（约 30–60 秒）</button>' +
      '</div></section>';
  }

  function emptyResult() {
    const D = window.PODATA || {};
    return '<section class="mod t1-empty" data-unit="versions">' +
      '<div class="mod-h"><h2>⑤ 六个版本</h2><span class="hint">还没有生成结果</span></div>' +
      '<div class="mod-b t1-empty-b">' +
        '<p class="t1-empty-t">确认左边三步后点「生成优化版 + 五个新版本」：一次给出 1 个优化版和 5 个不同创意，每版含分段提示词。</p>' +
        '<div class="t1-empty-kv">' +
          '<div><b>6</b><span>个版本</span></div>' +
          '<div><b>' + ((D.plan && D.plan.count) || 0) + '</b><span>计划段数</span></div>' +
          '<div><b>' + ((D.plan && D.plan.segments && D.plan.segments[0]) || 0) + '</b><span>每段秒数</span></div>' +
        '</div>' +
      '</div></section>';
  }

  function errorBanner(msg) {
    return '<div class="mod t1-err" role="alert"><div class="mod-h"><h2>上次没跑通</h2>' +
      '<span class="hint">输入和上次结果都还在，改完可以直接再试</span></div>' +
      '<div class="mod-b t1-err-b"><p class="t1-err-t">' + window.PO.esc(msg) + '</p>' +
      '<button class="btn sm" data-act="generate">重试生成</button>' +
      '<button class="btn sm ghost" data-act="clear-error">知道了</button></div></div>';
  }

  window.ARCH.T1 = {
    name: '中控台',
    render() {
      const D = window.PODATA || {};
      const hasVer = (D.versions || []).length > 0;
      const hasAna = !!(D.analysis && D.analysis.summary);
      return '<div class="pg t1">' +
        U.head({ eyebrow: '提示词优化 · 中控台' }) +
        U.status({ variant: 'strip' }) +
        '<div class="t1-main">' +
          '<div class="t1-left">' +
            U.source({ editable: true }) +
            U.product({ editable: true }) +
            U.mode({ variant: 'plan', ratio: false, busy: !!(D.ui && D.ui.busy), regenerate: hasVer }) +
          '</div>' +
          '<div class="t1-right">' +
            ((D.ui && D.ui.error) ? errorBanner(D.ui.error) : '') +
            (hasAna ? U.analysis({ variant: 'panel', rep: 96 }) : emptyAnalysis()) +
            (hasVer ? U.versions({ variant: 'grid', diff: 34, dl: 1, dlCut: 54 }) : emptyResult()) +
            (hasVer ? U.selfcheck({ variant: 'matrix' }) : '') +
          '</div>' +
        '</div>' +
        '<div class="t1-foot">' +
          U.history({ variant: 'list' }) +
          U.exportBar({ variant: 'row' }) +
        '</div>' +
      '</div>';
    }
  };
})();
