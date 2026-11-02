/* ============================================================
   reverse_v2.js · 「提示词反推」新版（S006 = A1 三栏工位 / P18 奶咖 / airy）
   —— 只负责这一个视图：渲染 + 真实动作（建任务 / AI 反推 / 停止 / 报告 / 素材包 /
      优化衔接 / 交接包 / 重建 / 打开目录 / 删除 / 口播保存）+ 轮询
   复用：/static/rv2/{ovlib,rcore}.js（图表与 11 个功能单元）、/static/reverse_v2.css（由设计库 base/ui/arch-a 收进 #rv2 作用域）
   回退：index.html 里去掉 #rv2 与两个 <link>/<script>，并把旧标记放回去
   ============================================================ */
(function () {
  'use strict';

  var MODE_KEY = 'tkrev_mode';
  var MODEL_KEY = 'tkrev_model';
  var POLL_MS = 4000;

  var UAPI = window.RVU || window.U;   // 组件库（rcore）
  var SAPI = window.RVOV || window.OV; // 图表与原子件（ovlib）
  var HOST = null;
  var D = null;                 // 与设计库同构的数据
  var REPORTS = {};             // vid -> 报告
  var FOCUS = null;
  var VOICE = {};               // vid -> {state, language}
  var MODELS = [];
  var DEFAULT_MODEL = '';
  var TIMER = null;
  var TICK = null;
  var LOADING = false;
  var LAST_LOAD = 0;
  var LAST_ERR = '';
  var OPEN_MENU = null;
  var SHOW_ALL = false;
  var FIRST_RENDERED = false;
  var MOUNTED = false;
  var INITED = false;
  var PAGE_SIZE = 6;

  /* ── 小工具 ────────────────────────────────────────── */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toast(msg, kind) { if (window.toast) window.toast(msg, kind); else console.log('[rv2]', msg); }
  function clip(s, n) { s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n) + '…' : s; }
  function mmss(sec) {
    var n = Math.max(0, Math.round(Number(sec) || 0)), m = Math.floor(n / 60), s = n % 60;
    return m ? m + ':' + (s < 10 ? '0' : '') + s : s + ' 秒';
  }
  function dayKey(ts) { var d = new Date(ts * 1000), p = function (n) { return (n < 10 ? '0' : '') + n; }; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); }
  function hhmm(ts) { var d = new Date(ts * 1000), p = function (n) { return (n < 10 ? '0' : '') + n; }; return p(d.getHours()) + ':' + p(d.getMinutes()); }
  function nowText() { var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; }; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()); }

  function localMode() { try { var v = localStorage.getItem(MODE_KEY); return v === 'ad' ? 'ad' : 'restore'; } catch (e) { return 'restore'; } }
  function localModel() { try { return localStorage.getItem(MODEL_KEY) || ''; } catch (e) { return ''; } }

  function getJSON(url, ms) {
    return fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(ms || 15000) }).then(function (r) {
      if (!r.ok) throw new Error(url.replace(/^.*\/api\//, '/api/') + ' → ' + r.status);
      return r.json();
    });
  }

  /* ── 数据：真实接口 → 设计库结构 ───────────────────── */
  var STAGE_TEXT = { queued: '排队', downloading: '下载原片', frames: '抽帧', ready: '素材就绪', analyzing: 'AI 反推', done: '已完成', failed: '失败', preparing: '生成素材中', pending: '排队中', stopped: '已停止' };
  var STAGE_TONE = { done: 'ok', failed: 'bad', ready: 'warn', stopped: '', queued: '', pending: '', preparing: 'acc', analyzing: 'acc', downloading: 'acc', frames: 'acc' };

  function mapReport(rp) {
    return {
      model: rp._model || '', mode: rp._mode || '', sourceDuration: Number(rp.source_duration || 0),
      product: clip(rp.product_summary, 220), person: clip(rp.person_summary, 200), scene: clip(rp.scene_summary, 200),
      uncertainties: (rp.uncertainties || []).slice(0, 6).map(function (x) { return clip(x, 150); }),
      shots: (rp.shots || []).map(function (s) {
        return {
          idx: s.idx, name: clip(s.name, 90), t0: Number(s.t_start || 0), t1: Number(s.t_end || 0),
          action: clip(s.action_steps, 240), start: clip(s.initial_state, 120),
          end: clip(s.ending_state, 120), camera: clip(s.camera_motion, 120)
        };
      }),
      prompts: (rp.prompts || []).map(function (p) {
        return { idx: p.idx, zh: clip(p.text, 400), en: clip(p.english, 260), multi: clip(p.multi, 200), notes: clip(p.notes, 160) };
      }),
      audio: { strategy: (rp.audio || {}).strategy || '', available: !!(rp.audio || {}).available, codec: (rp.audio || {}).codec || '', note: clip((rp.audio || {}).note, 120) },
      gallery: (rp.gallery || []).length,
      galleryTail: (rp.gallery || []).slice(0, 8).map(function (g) { return { id: g.id, t: g.t }; })
    };
  }

  function buildData(list, models, health) {
    var items = (list.items || []).slice();
    MODELS = (models.items || []);
    DEFAULT_MODEL = models['default'] || '';

    var tasks = items.map(function (it) {
      var meta = it.meta || {}, probe = it.probe || {}, st = it.stage || 'queued';
      var created = Number(it.created_at || 0), updated = Number(it.updated_at || 0);
      var rep = REPORTS[String(it.video_id)];
      var log = Array.isArray(it.log) ? it.log : [];
      var elapsed = 0;
      if (log.length > 1) {
        /* ★ 2026-10-30：首尾相减会把「机器休眠 / 等人工」的空档算进耗时 ——
           一条 15s 的片子曾显示 748:01（12.5 小时），平均耗时也被带到 251 分。
           现在只累计连续处理的时间：相邻两条日志间隔 > 10 分钟视为中途停过，不计入。 */
        var acc = 0;
        for (var k = 1; k < log.length; k++) {
          var a = Number(log[k - 1].t), b = Number(log[k].t);
          if (a && b && b > a && (b - a) <= 600) acc += (b - a);
        }
        if (acc > 0) elapsed = Math.round(acc);
      }
      if (!elapsed && created && updated) elapsed = Math.min(Math.max(0, updated - created), 3 * 3600);
      return {
        id: String(it.video_id),
        account: meta.author || (meta.origin === 'local' ? '本地素材' : '—'),
        nickname: meta.nickname || '',
        origin: meta.origin === 'local' ? 'local' : 'link',
        url: it.url || '',
        desc: clip(meta.desc || meta.desc_full || '', 150),
        lang: meta.language_guess || '',
        duration: Number(probe.duration || meta.duration || 0),
        size: [probe.width, probe.height].filter(Boolean).join('×'),
        fps: Number(probe.fps || 0),
        audio: !!probe.has_audio,
        stage: st,
        stageKey: it.stage || '',
        stageLabel: it.stage_label || STAGE_TEXT[st] || st,
        progress: Number(it.progress != null ? it.progress : 0),
        busy: !!it.busy,
        busySince: Number(it.busy_since || 0),
        updatedAt: updated,
        steps: it.steps || [],
        mode: it.mode || (rep && rep.mode) || '',
        modeLabel: (it.mode || (rep && rep.mode)) === 'ad' ? '广告拆解' : (it.mode || (rep && rep.mode)) === 'restore' ? '原片还原' : '',
        model: it.model || (rep && rep.model) || '',
        frames: Number(it.frames || 0),
        aiFrames: Number(it.ai_frames || 0),
        photo: Number(it.photo_images || 0),   // ★ 图文帖：素材是图集合成的，卡片要标出来
        shots: Number(it.shots || (rep ? rep.shots.length : 0) || 0),
        prompts: rep ? rep.prompts.length : 0,
        scenes: String(it.scenes || ''),
        refs: Number(it.refs || (rep ? rep.gallery : 0) || 0),
        hasReport: !!it.has_report,
        bundle: it.bundle || '',
        reportUrl: it.report || '',
        createdAt: created,
        createdDay: created ? dayKey(created) : '',
        createdHM: created ? hhmm(created) : '',
        elapsedSec: elapsed,
        error: clip(it.error || '', 220)
      };
    }).sort(function (a, b) { return b.createdAt - a.createdAt; });

    var counts = {
      total: tasks.length,
      done: tasks.filter(function (t) { return t.stage === 'done'; }).length,
      failed: tasks.filter(function (t) { return t.stage === 'failed'; }).length,
      ready: tasks.filter(function (t) { return t.stage === 'ready'; }).length,
      busy: tasks.filter(function (t) { return t.busy; }).length
    };
    var withRep = tasks.filter(function (t) { return REPORTS[t.id]; });
    var avgSec = Math.round(withRep.reduce(function (s, t) { return s + t.elapsedSec; }, 0) / (withRep.length || 1));
    var refsTotal = tasks.reduce(function (s, t) { return s + t.refs; }, 0);

    var series = [], today = new Date();
    for (var i = 29; i >= 0; i--) {
      var d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
      var key = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
      var dayTasks = tasks.filter(function (x) { return x.createdDay === key; });
      var tm = dayTasks.filter(function (x) { return x.elapsedSec > 0; });
      series.push({
        date: key, tasks: dayTasks.length,
        done: dayTasks.filter(function (x) { return x.stage === 'done'; }).length,
        failed: dayTasks.filter(function (x) { return x.stage === 'failed'; }).length,
        avgMin: tm.length ? Math.round(tm.reduce(function (s, x) { return s + x.elapsedSec; }, 0) / tm.length / 60 * 10) / 10 : 0
      });
    }

    var stages = [
      { key: 'download', label: '下载原片', n: tasks.length, stuck: counts.failed },
      { key: 'frames', label: '抽帧 / 切点', n: tasks.filter(function (t) { return t.frames > 0; }).length, stuck: 0 },
      { key: 'ready', label: '素材就绪', n: tasks.filter(function (t) { return t.frames > 0; }).length, stuck: counts.ready },
      { key: 'analyze', label: '读帧分析', n: tasks.filter(function (t) { return t.hasReport; }).length, stuck: 0 },
      { key: 'prompts', label: '提示词分段', n: tasks.filter(function (t) { return t.hasReport; }).length, stuck: 0 },
      { key: 'done', label: '出报告', n: tasks.filter(function (t) { return t.hasReport; }).length, stuck: 0 }
    ];

    var dg = (health && health.diagnostics) || {};
    var focus = tasks.filter(function (t) { return REPORTS[t.id]; })
      .sort(function (a, b) { return (REPORTS[b.id].shots.length) - (REPORTS[a.id].shots.length); })[0];
    if (!focus) focus = tasks[0];

    var data = {
      meta: { asOf: nowText(), demo: false, source: '工作台实时接口', note: '刷新即最新；轮询只读。' },
      models: MODELS.map(function (m) { return { id: m.id, name: m.name }; }),
      defaultModel: DEFAULT_MODEL,
      counts: counts, avgSec: avgSec, refsTotal: refsTotal, concurrency: 2,
      day: { series: series, total: counts.total, done: counts.done, failed: counts.failed, live: counts.busy },
      stages: stages,
      tasks: tasks,
      reports: REPORTS,
      focusId: focus ? focus.id : null,
      diag: {
        ffmpeg: true, python: !!dg.python_exists, proxy: dg.proxy || '未配置',
        uploads: 'reverse/_uploads', maxMB: 500, maxSec: 60, models: MODELS.length,
        note: '抽帧 2fps；读帧按批送模型；原声回挂，未做人声分离。'
      }
    };
    /* rcore 里的辅助函数（modelName / stageTone）读 window.RVDATA，这里同步一份 */
    window.RVDATA = data;
    return data;
  }

  function ensureReports(ids) {
    var need = ids.filter(function (id) { return id && !REPORTS[id]; });
    if (!need.length) return Promise.resolve();
    return Promise.all(need.map(function (id) {
      return getJSON('/tk_reverse/' + encodeURIComponent(id) + '/report.json', 15000)
        .then(function (rp) { REPORTS[id] = mapReport(rp); })
        .catch(function () { REPORTS[id] = null; });
    })).then(function () {
      Object.keys(REPORTS).forEach(function (k) { if (!REPORTS[k]) delete REPORTS[k]; });
    });
  }

  /* ── 渲染：外壳 ────────────────────────────────────── */
  function composeHtml() {
    var opts = MODELS.map(function (m) {
      var sel = (localModel() || DEFAULT_MODEL) === m.id ? ' selected' : '';
      return '<option value="' + esc(m.id) + '"' + sel + '>' + esc(m.name) + '</option>';
    }).join('');
    var mode = localMode();
    return '<section class="u-compose" data-unit="compose">' +
      '<header class="u-compose-h"><h2>新建反推</h2><span class="tiny muted">第一步不花 token，只出素材</span></header>' +
      '<div class="u-compose-row">' +
        '<label class="u-field grow"><span>视频链接</span>' +
          '<input id="rqv-url" type="text" autocomplete="off" spellcheck="false" placeholder="https://www.tiktok.com/@xxx/video/123456… 或 vm.tiktok.com 短链，也接受纯 video id">' +
        '</label>' +
        '<label class="u-field"><span>反推模式</span><select id="rqv-mode">' +
          '<option value="restore"' + (mode === 'restore' ? ' selected' : '') + '>原片还原 · 保留原声</option>' +
          '<option value="ad"' + (mode === 'ad' ? ' selected' : '') + '>广告拆解 · 旁白改写</option></select></label>' +
        '<label class="u-field"><span>分析模型</span><select id="rqv-model">' + opts + '</select></label>' +
        '<div class="u-field-acts">' +
          '<button class="btn pri" id="rqv-create">① 生成素材</button>' +
          '<button class="btn ghost" id="rqv-import">导入本地视频</button>' +
          '<input id="rqv-file" type="file" accept=".mp4,.mov,.webm,.mkv" style="display:none">' +
        '</div>' +
      '</div>' +
      '<p class="u-note">链接生成素材或导入本地视频（≤ 500MB）后，点卡片上的「② AI 反推」。原片还原按实际镜头拆动作与运镜，导出多张参考图并保留原音轨；当前支持 60 秒以内。</p>' +
      '</section>';
  }

  function shellHtml() {
    return '<div class="pg a1">' +
      '<header class="pg-top">' +
        '<div><h1>提示词反推</h1><p class="pg-sub">链接或本地视频 → 分镜、参考图与生成提示词 · 原片还原 / 广告拆解</p></div>' +
        '<div class="u-stamps" id="rqv-stamps"></div>' +
        '<div class="u-acts">' +
          '<span class="tiny muted" id="rqv-updated"></span>' +
          '<button class="btn ghost" id="rqv-refresh">刷新</button>' +
        '</div>' +
      '</header>' +
      composeHtml() +
      '<div id="rqv-finding"></div>' +
      '<div id="rqv-kpis"></div>' +
      '<div class="a1-body">' +
        '<aside class="a1-left">' +
          '<div class="a1-sec">任务队列 <span class="tiny muted" id="rqv-task-count"></span></div>' +
          '<div id="rqv-tasks" data-unit="tasks"></div>' +
          '<div id="rqv-queue"></div>' +
        '</aside>' +
        '<main class="a1-main">' +
          '<div id="rqv-shots"></div>' +
          '<div id="rqv-trend"></div>' +
        '</main>' +
        '<aside class="a1-right">' +
          '<div id="rqv-refs"></div>' +
          '<div id="rqv-prompts"></div>' +
          '<div id="rqv-voice"></div>' +
        '</aside>' +
      '</div>' +
      '<footer class="a1-foot" id="rqv-diag"></footer>' +
      '</div>';
  }

  function mount() {
    /* 2026-10-31 修复 P1-2：页面加载时 DOMContentLoaded 处理里先 mount() 再 init()，
       但那时 HOST 还是 null（只在 init 里赋值）→ mount 直接 return；
       init 之后把 INITED 置真。用户第一次点「提示词反推」时 RV_load 才真正 mount，
       可 init 已被 INITED 挡住不再 refresh，于是任务队列一片空白，
       切走再回来才有数据。这里补两处：mount 自己取一次 HOST；RV_load 兜底 refresh。 */
    if (!HOST) HOST = document.getElementById('rv2');
    if (!HOST || MOUNTED) return;
    MOUNTED = true;
    HOST.innerHTML = shellHtml();
    $('#rqv-refresh').addEventListener('click', function () { refresh(true); });
    $('#rqv-create').addEventListener('click', create);
    $('#rqv-url').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); create(); }   /* 粘完链接直接回车也能生成 */
    });
    $('#rqv-import').addEventListener('click', function () { $('#rqv-file').click(); });
    $('#rqv-file').addEventListener('change', function (e) { importVideo(e.target.files[0]); });
    $('#rqv-mode').addEventListener('change', function () { try { localStorage.setItem(MODE_KEY, this.value); } catch (e) {} });
  }

  /* ── 渲染：各区块 ──────────────────────────────────── */
  function renderAll() {
    if (!D || !$('#rqv-stamps')) return;
    $('#rqv-stamps').innerHTML =
      '<span class="chip"><i class="dot ok"></i>本地 ' + esc(window.location.port || '8787') + '</span>' +
      '<span class="chip">更新 ' + esc(D.meta.asOf.slice(11)) + '</span>' +
      '<span class="chip acc">' + D.models.length + ' 个模型</span>' +
      '<span class="chip">上传上限 500MB · 60s 内</span>';
    $('#rqv-finding').innerHTML = UAPI.finding(D);
    $('#rqv-kpis').innerHTML = UAPI.kpis(D, { variant: 'cards' });
    $('#rqv-queue').innerHTML = UAPI.queue(D, { variant: 'bars', title: '阶段漏斗' });
    $('#rqv-diag').innerHTML = UAPI.diag(D, { variant: 'strip' });
    renderTasks();
    renderDetail();
    bindKpiDrill();
    settleCounts();
    if (!FIRST_RENDERED) { FIRST_RENDERED = true; reveal(); }
  }

  function settleCounts() {
    $$('#rv2 [data-count]').forEach(function (el) { el.textContent = el.getAttribute('data-count'); });
  }

  function renderTasks() {
    var host = $('#rqv-tasks');
    if (!host || !D) return;
    var list = SHOW_ALL ? D.tasks : D.tasks.slice(0, PAGE_SIZE);
    host.innerHTML = list.map(function (t) { return taskCard(t); }).join('') +
      (D.tasks.length > PAGE_SIZE
        ? '<button class="btn ghost rqv-more-list" data-x="toggle-all">' +
          (SHOW_ALL ? '收起，只看最近 ' + PAGE_SIZE + ' 条' : '显示全部 ' + D.tasks.length + ' 条') + '</button>'
        : '');
    $('#rqv-task-count').textContent = '共 ' + D.tasks.length + ' 条 · 显示 ' + list.length;
  }

  function taskCard(t) {
    var tone = STAGE_TONE[t.stageKey] || 'acc';
    var isFocus = t.id === FOCUS;
    var sel = localModel() === t.model ? ' selected' : '';
    var acts = [];
    var noMaterial = !t.frames && !t.hasReport;      /* 素材都没生成出来（多为取元数据就失败） */
    if (t.busy) {
      acts.push('<button class="btn ghost" data-x="stop" data-vid="' + t.id + '">停止</button>');
    } else if (t.stage === 'failed' && noMaterial) {
      /* 这种失败点「② 反推」只会报「素材还没生成」，给个能真正重来的按钮 */
      acts.push('<button class="btn pri" data-x="retry" data-vid="' + t.id + '">重试</button>');
    } else if (t.hasReport || t.stage === 'ready') {
      acts.push('<button class="btn pri" data-x="ai" data-vid="' + t.id + '">' + (t.hasReport ? '重跑' : '② 反推') + '</button>');
    }
    if (t.hasReport && t.reportUrl) acts.push('<button class="btn ghost" data-x="report" data-vid="' + t.id + '">报告</button>');
    if (t.bundle) acts.push('<button class="btn ghost" data-x="bundle" data-vid="' + t.id + '">素材包</button>');
    if ((t.mode || 'restore') === 'restore' && t.hasReport) acts.push('<button class="btn ghost" data-x="optimize" data-vid="' + t.id + '">优化</button>');
    acts.push('<button class="btn ghost rqv-more-btn" data-x="more" data-vid="' + t.id + '" aria-label="更多">⋯</button>');

    var prog = t.busy || t.stageKey === 'preparing' || t.stageKey === 'analyzing' ? progHtml(t) : '';
    var err = t.error ? '<p class="u-task-err" title="' + esc(t.error) + '">' + esc(t.error) + '</p>' : '';
    return '<article class="u-task rqv-task' + (isFocus ? ' is-focus' : '') + (OPEN_MENU === t.id ? ' menu-open' : '') + '" data-task="' + t.id + '">' +
      '<header class="u-task-h">' +
        '<span class="chip ' + tone + '">' + esc(t.stageLabel) + '</span>' +
        (t.modeLabel ? '<span class="chip">' + esc(t.modeLabel) + '</span>' : '') +
        (t.origin === 'local' ? '<span class="chip">本地视频</span>' : '') +
        (t.photo ? '<span class="chip">图文帖 · ' + t.photo + ' 图</span>' : '') +
        '<span class="u-task-id mono">' + esc(t.id) + '</span>' +
        '<span class="u-task-at tiny muted">' + esc(t.createdDay.slice(5) + ' ' + t.createdHM) + '</span>' +
      '</header>' +
      '<div class="u-task-t">@' + esc(t.account) + (t.nickname ? ' · ' + esc(t.nickname) : '') + '</div>' +
      '<p class="u-task-d">' + esc(t.desc || '（简介为空）') + '</p>' +
      '<div class="u-task-kv">' +
        '<span><i>时长</i><b class="tnum">' + t.duration.toFixed(1) + 's</b></span>' +
        '<span><i>分镜</i><b class="tnum">' + t.shots + '</b></span>' +
        '<span><i>参考图</i><b class="tnum">' + t.refs + '</b></span>' +
        '<span><i>抽帧</i><b class="tnum">' + t.frames + '/' + t.aiFrames + '</b></span>' +
        '<span><i>耗时</i><b class="tnum rqv-elapsed" data-since="' + (t.busySince || 0) + '">' + (t.busy ? '已用 ' + mmss(Math.round(Date.now() / 1000) - (t.busySince || 0)) : mmss(t.elapsedSec)) + '</b></span>' +
      '</div>' + err + prog +
      '<div class="u-task-bar"><i class="' + tone + '" style="width:' + Math.min(100, t.progress) + '%"></i></div>' +
      '<footer class="u-task-acts">' + acts.join('') + '</footer>' +
      (OPEN_MENU === t.id ? moreMenu(t) : '') +
      '</article>';
  }

  function progHtml(t) {
    var steps = t.steps || [];
    var p = Math.max(0, Math.min(100, Number(t.progress) || 0));
    var cur = 0;
    for (var i = 0; i < steps.length; i++) if (p >= (Number(steps[i].at) || 0)) cur = i;
    var items = steps.map(function (s, i) {
      var cls = i < cur ? 'done' : (i === cur ? 'cur' : '');
      return '<li class="' + cls + '">' + esc(s.label) + '</li>';
    }).join('');
    return '<div class="rqv-prog">' +
      '<div class="rqv-prog-t"><span>' + esc(t.stageLabel) + '</span><b class="tnum">' + p + '%</b></div>' +
      '<div class="rqv-bar"><i class="' + (t.busy ? 'live' : '') + '" style="width:' + Math.max(2, p) + '%"></i></div>' +
      (items ? '<ol class="rqv-steps">' + items + '</ol>' : '') +
      '</div>';
  }

  function moreMenu(t) {
    return '<div class="rqv-menu" role="menu">' +
      (t.busy ? '<button data-x="stop" data-vid="' + t.id + '">停止当前任务</button>' : '') +
      (t.hasReport ? '<button data-x="handoff" data-vid="' + t.id + '">Agent 交接包</button>' : '') +
      '<button data-x="rebuild" data-vid="' + t.id + '">重建素材</button>' +
      '<button data-x="open" data-vid="' + t.id + '">打开目录</button>' +
      '<button data-x="del" data-vid="' + t.id + '" class="danger">删除任务</button>' +
      '</div>';
  }

  function renderDetail() {
    if (!D) return;
    var t = D.tasks.filter(function (x) { return x.id === FOCUS; })[0];
    var R = t ? REPORTS[t.id] : null;
    $('#rqv-shots').innerHTML = UAPI.shots(D, { variant: 'table', task: FOCUS });
    $('#rqv-trend').innerHTML = UAPI.trend(D, { kind: 'area' });
    $('#rqv-refs').innerHTML = UAPI.refs(D, { variant: 'panel', task: FOCUS });
    $('#rqv-prompts').innerHTML = UAPI.prompts(D, { variant: 'list', n: 4, task: FOCUS });
    $('#rqv-voice').innerHTML = UAPI.voice(D, { task: FOCUS });
    fillVoice(t);
    bindKpiDrill();
  }

  /* ── 口播 ─────────────────────────────────────────── */
  function fillVoice(t) {
    var box = $('#rqv-voice');
    if (!box || !t || !window.ReverseVoice) return;
    var apply = function (v) {
      VOICE[t.id] = v;
      var sel = box.querySelector('[data-voice]');
      if (!sel) return;
      var lang = v && v.state === 'spoken' ? v.language : (v && v.state) || 'unknown';
      var known = ['unknown', 'none'].concat(window.ReverseVoice.languages);
      sel.value = known.indexOf(lang) >= 0 ? lang : 'unknown';
      var chip = box.querySelector('.u-voice-row .chip');
      if (chip) {
        chip.className = 'chip ' + (v && v.state !== 'unknown' ? 'ok' : 'warn');
        chip.textContent = v && v.state === 'spoken' ? '已确认 · ' + v.language
          : v && v.state === 'none' ? '已确认 · 无口播' : '尚未确认';
      }
    };
    if (VOICE[t.id]) apply(VOICE[t.id]);
    else window.ReverseVoice.read(t.id).then(apply).catch(function () {});
  }

  function saveVoice(vid) {
    var box = $('#rqv-voice');
    var sel = box && box.querySelector('[data-voice]');
    if (!sel) return;
    var choice = sel.value;
    var value = { state: choice, language: choice === 'spoken' ? '' : choice };
    if (choice === 'unknown' || choice === 'none') value = { state: choice, language: '' };
    else value = { state: 'spoken', language: choice };
    var btn = box.querySelector('[data-act="save-voice"]');
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    (window.ReverseVoice ? window.ReverseVoice.save(vid, value) : Promise.reject(new Error('口播接口未加载')))
      .then(function (v) {
        VOICE[vid] = v;
        fillVoice({ id: vid });
        toast('口播信息已保存');
      })
      .catch(function (e) { toast('保存失败：' + e.message, 'error'); })
      .finally(function () { if (btn) { btn.disabled = false; btn.textContent = '保存口播信息'; } });
  }

  /* ── 动作 ─────────────────────────────────────────── */
  /* ★ 2026-10-30 修复「点了没反应」：
     ① 失败时 catch 返回 null，调用方据此不再往下走（旧实现 catch 吞掉错误后，
        create 的 then 照样执行，把 4ms 前那条真实报错顶成「已开始生成素材：（空）」）；
     ② 动作触发的刷新改走 refresh(false)，不再弹「已刷新 · HH:MM」盖住确认文案。 */
  function post(url, body, okMsg) {
    return fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.detail || ('HTTP ' + r.status)); });
      return r.json().catch(function () { return {}; });
    }).then(function (r) { if (okMsg) toast(okMsg); refresh(false, true); return r; })
      .catch(function (e) { toast('失败：' + e.message, 'error'); return null; });
  }

  /* 从 App/网页「分享」复制来的常是一整段话（“看看这个 https://… ”）或带全角标点，
     先把真链接摘出来，再把 ?is_from_webapp=1&… 这类尾巴去掉，避免后端解析失败。 */
  function cleanInput(raw) {
    var s = String(raw == null ? '' : raw).trim();
    var m = s.match(/https?:\/\/[^\s"'<>，。；、）】」]+/i);
    if (m) s = m[0];
    s = s.replace(/[）】」》，。；、"'<>]+$/, '');
    var v = s.match(/tiktok\.com\/@([\w.\-]+)\/video\/(\d{6,})/i);
    if (v) return 'https://www.tiktok.com/@' + v[1] + '/video/' + v[2];
    return s;
  }

  function create() {
    var inp = $('#rqv-url');
    var url = cleanInput(inp ? inp.value : '');
    if (!url) { toast('先填 TikTok 视频链接，再点「① 生成素材」', 'error'); if (inp) inp.focus(); return; }
    var btn = $('#rqv-create');
    var label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '解析中…'; }
    fetch('/api/reverse/create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
        return d;
      });
    }).then(function (r) {
      if (inp) inp.value = '';
      var vid = (r && r.video_id) || '';
      toast(r && r.reused
        ? (r.notice || '这条视频的素材已经在了，已定位到该任务')
        : ('已开始生成素材' + (vid ? '：' + vid : '') + '，卡片会出现在左侧任务队列'));
      return refresh(false, true).then(function () { if (vid) focusTask(vid); });
    }).catch(function (e) {
      toast('失败：' + e.message, 'error');   /* 错误文案留在屏幕上，且不再被任何提示覆盖 */
    }).finally(function () {
      if (btn) { btn.disabled = false; btn.textContent = label || '① 生成素材'; }
    });
  }

  /* 新建 / 复用后把那条卡片滚进视野并高亮一次：避免「提示一闪，列表没变化」的错觉 */
  function focusTask(id, tries) {
    if (!id) return;
    tries = (tries == null) ? 2 : tries;
    var has = D && D.tasks.some(function (t) { return t.id === id; });
    if (!has) {
      if (tries <= 0) return;
      return setTimeout(function () {
        refresh(false, true).then(function () { focusTask(id, tries - 1); });
      }, 600);
    }
    FOCUS = id;
    if (!SHOW_ALL && D.tasks.length > PAGE_SIZE &&
        D.tasks.findIndex(function (t) { return t.id === id; }) >= PAGE_SIZE) SHOW_ALL = true;
    renderTasks();
    ensureReports([id]).then(function () {
      renderDetail();
      ['#rqv-shots', '#rqv-refs', '#rqv-prompts', '#rqv-voice'].forEach(function (s) { reveal(s); });
      var card = $('#rqv-tasks [data-task="' + id + '"]');
      if (!card) return;
      var box = card.getBoundingClientRect();
      if (box.top < 60 || box.bottom > window.innerHeight - 8) card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.classList.add('is-flash');
      setTimeout(function () { card.classList.remove('is-flash'); }, 1800);
    });
  }

  function importVideo(file) {
    if (!file) return;
    if (!/\.(mp4|mov|webm|mkv)$/i.test(file.name) || file.size > 500 * 1024 * 1024) {
      toast('请选择 500MB 以内的 mp4 / mov / webm / mkv', 'error'); return;
    }
    var btn = $('#rqv-import'), label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '上传中 0%'; }
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/reverse/import?filename=' + encodeURIComponent(file.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    if (xhr.upload) xhr.upload.onprogress = function (e) {
      if (!btn || !e.lengthComputable) return;
      var pct = Math.round(e.loaded / e.total * 100);
      btn.textContent = pct < 100 ? '上传中 ' + pct + '%' : '处理中…';
    };
    xhr.onload = function () {
      var data = {}; try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) {}
      if (xhr.status >= 200 && xhr.status < 300) {
        var vid = data && data.video_id ? String(data.video_id) : '';
        toast('本地视频已导入，正在生成素材');
        refresh(false, true).then(function () { if (vid) focusTask(vid); });
      }
      else toast(data.detail || '导入失败', 'error');
    };
    xhr.onerror = function () { toast('上传失败（网络中断）', 'error'); };
    xhr.onloadend = function () {
      if (btn) { btn.disabled = false; btn.textContent = label || '导入本地视频'; }
      var f = $('#rqv-file'); if (f) f.value = '';
    };
    xhr.send(file);
  }

  function analyze(vid) {
    var btn = $('[data-x="ai"][data-vid="' + vid + '"]');
    if (btn && btn.textContent.indexOf('重跑') >= 0 && !confirm('重跑会覆盖现有报告与首帧，继续？')) return;
    if (btn) btn.disabled = true;
    post('/api/reverse/analyze/' + encodeURIComponent(vid),
      { model: localModel(), mode: localMode() },
      '已开始反推，正在读帧分析');
  }

  function handoff(vid) {
    toast('正在生成交接包…');
    getJSON('/api/reverse/handoff/' + encodeURIComponent(vid), 30000).then(function (d) {
      openDrawer('Agent 交接包', '<p class="tiny muted">已存 ' + esc(d.saved || '') + '</p><pre class="rqv-pre">' + esc(d.text) + '</pre>' +
        '<div style="margin-top:10px"><button class="btn pri" data-x="copy-ho">复制全文</button></div>', { text: d.text });
    }).catch(function (e) { toast('交接包失败：' + e.message, 'error'); });
  }

  function doAction(act, vid) {
    var t = D && D.tasks.filter(function (x) { return x.id === vid; })[0];
    if (act === 'ai') return analyze(vid);
    if (act === 'retry') {
      if (!t || !t.url) { toast('这条任务没有原始链接，无法重试（本地视频请重新导入）', 'error'); return; }
      return post('/api/reverse/create', { url: t.url }, '已重新提交，正在取元数据');
    }
    if (act === 'stop') return post('/api/reverse/stop/' + encodeURIComponent(vid), {}, '已请求停止，当前步骤结束后停');
    if (act === 'report') { if (t && t.reportUrl) window.open(t.reportUrl, '_blank', 'noopener'); return; }
    if (act === 'bundle') { if (t && t.bundle) window.open(t.bundle, '_blank', 'noopener'); return; }
    if (act === 'optimize') {
      if (window.PO && window.PO.importReport) {
        if (window.switchView) window.switchView('prompt');
        if (location.hash !== '#prompt') history.pushState(null, '', '#prompt');
        window.PO.importReport(vid);
      } else { location.href = '/?optimize_from=' + encodeURIComponent(vid) + '#prompt'; }
      return;
    }
    if (act === 'handoff') return handoff(vid);
    if (act === 'rebuild') {
      if (!confirm('重建会清理派生帧图与当前报告。本地素材保留原片，链接素材会重新下载。继续？')) return;
      return post('/api/reverse/rebuild/' + encodeURIComponent(vid), {}, '已开始重建素材');
    }
    if (act === 'open') return post('/api/reverse/open/' + encodeURIComponent(vid), {}, '');
    if (act === 'del') {
      if (!confirm('删除任务目录（含原片、帧图、报告）？此操作不可恢复。')) return;
      return fetch('/api/reverse/' + encodeURIComponent(vid), { method: 'DELETE' })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); toast('已删除'); delete REPORTS[vid]; refresh(false, true); })
        .catch(function (e) { toast('删除失败：' + e.message, 'error'); });
    }
    if (act === 'save-voice') return saveVoice(FOCUS);
  }

  /* ── 抽屉 ─────────────────────────────────────────── */
  function openDrawer(title, body, extra) {
    var drawer = $('#rqv-drawer'), scrim = $('#rqv-scrim');
    if (!drawer) {
      scrim = document.createElement('div'); scrim.className = 'scrim'; scrim.id = 'rqv-scrim';
      drawer = document.createElement('aside'); drawer.className = 'drawer'; drawer.id = 'rqv-drawer';
      drawer.setAttribute('role', 'dialog'); drawer.setAttribute('aria-modal', 'true');
      HOST.appendChild(scrim); HOST.appendChild(drawer);
      scrim.addEventListener('click', closeDrawer);
    }
    drawer.innerHTML = '<header class="drawer-h"><div><div class="eyebrow">DETAIL</div><h2>' + esc(title) + '</h2></div>' +
      '<button class="btn ghost" data-x="close">关闭</button></header>' +
      '<div class="drawer-b">' + body + '</div>';
    drawer.classList.add('on'); scrim.classList.add('on');
    drawer.querySelector('[data-x="close"]').addEventListener('click', closeDrawer);
    if (extra && extra.text) {
      drawer.querySelector('[data-x="copy-ho"]').addEventListener('click', function () {
        navigator.clipboard.writeText(extra.text).then(function () { toast('已复制交接包'); },
          function () { toast('复制失败，请手动选择', 'error'); });
      });
    }
  }
  function closeDrawer() {
    var d = $('#rqv-drawer'), s = $('#rqv-scrim');
    if (d) d.classList.remove('on');
    if (s) s.classList.remove('on');
  }

  function drill(key) {
    if (!D) return;
    var c = D.counts;
    var rows = function (head, body) { return '<table class="tb"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>'; };
    if (key === 'all' || key === 'done' || key === 'failed' || key === 'ready') {
      var title = { all: '全部反推任务', done: '已出报告', failed: '失败任务', ready: '素材就绪' }[key];
      var list = key === 'all' ? D.tasks : D.tasks.filter(function (t) {
        return key === 'done' ? t.stage === 'done' : key === 'failed' ? t.stage === 'failed' : t.stage === 'ready';
      });
      openDrawer(title + ' · ' + list.length, rows('<th>任务</th><th>账号</th><th class="r">分镜</th><th class="r">耗时</th><th class="r">状态</th>',
        list.map(function (t) {
          return '<tr><td class="mono tiny">' + esc(t.id) + '</td><td>' + esc(t.account) + '</td><td class="r">' + t.shots +
            '</td><td class="r">' + mmss(t.elapsedSec) + '</td><td class="r"><span class="chip ' + (STAGE_TONE[t.stageKey] || '') + '">' + esc(t.stageLabel) + '</span></td></tr>';
        }).join('')) + (key === 'failed' ? '<p class="u-note">失败任务都停在下载原片；重跑会换格式再取一次，不消耗模型额度。</p>' : ''));
      return;
    }
    if (key === 'avg') return openDrawer('耗时口径', '<p class="u-note">按日志首末（最后一次连续过程）计算，重跑造成的跨天间隔不计入。当前平均 ' + mmss(D.avgSec) + '。</p>');
    if (key === 'refs') return openDrawer('参考图与素材包', rows('<th>任务</th><th class="r">参考图</th><th class="r">送模型</th><th>素材包</th>',
      D.tasks.filter(function (t) { return t.refs; }).map(function (t) {
        return '<tr><td class="mono tiny">' + esc(t.id) + '</td><td class="r">' + t.refs + '</td><td class="r">' + t.aiFrames +
          '</td><td class="tiny">' + (t.bundle ? 'bundle.zip' : '—') + '</td></tr>';
      }).join('')));
    openDrawer('详情', '<p class="u-note muted">未登记的明细。</p>');
  }

  function bindKpiDrill() {
    $$('#rv2 [data-drill]').forEach(function (el) {
      if (el.__rv2) return; el.__rv2 = 1;
      el.addEventListener('click', function () { drill(el.getAttribute('data-drill')); });
    });
  }

  /* ── 动效：鼠标跟随光斑 + 入场（只加这一处，轮询不重播） ── */
  var MOTION_OFF = false;
  try { MOTION_OFF = matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  function bindMotion() {
    if (MOTION_OFF) return;
    /* 光斑跟随：只在 #rv2 内的卡片/面板上算，pointermove 用 passive，不阻塞滚动 */
    document.addEventListener('pointermove', function (ev) {
      var el = ev.target && ev.target.closest ? ev.target.closest('#rv2 .rqv-task, #rv2 .mod, #rv2 .k, #rv2 .u-compose') : null;
      if (!el) return;
      var r = el.getBoundingClientRect();
      el.style.setProperty('--mx', Math.round(ev.clientX - r.left) + 'px');
      el.style.setProperty('--my', Math.round(ev.clientY - r.top) + 'px');
    }, { passive: true });
  }

  function reveal(scope) {
    if (MOTION_OFF) return;
    var host = scope ? $(scope) : null;
    var sel = host ? '.mod, .rqv-task, .k, .u-find' : '#rv2 .mod, #rv2 .rqv-task, #rv2 .k, #rv2 .u-find';
    var els = $$(sel, host || document);
    els.forEach(function (el, i) {
      el.classList.remove('rqv-in');
      void el.offsetWidth;                       // 重排一次，动画才会重播
      el.style.setProperty('--rv-d', Math.min(240, i * 18) + 'ms');
      el.classList.add('rqv-in');
      /* 动画结束后摘掉类：否则 fill-mode:both 的 transform:none 会压住 hover 的位移 */
      el.addEventListener('animationend', function () { el.classList.remove('rqv-in'); }, { once: true });
    });
  }

  /* ── 事件委托 ─────────────────────────────────────── */
  var OPEN_MENU_COPY = null;

  function onHostClick(ev) {
    // 抽屉关闭
    if (ev.target.closest('[data-x="close"]')) return closeDrawer();
    // 展开/收起任务列表
    var toggle = ev.target.closest('[data-x="toggle-all"]');
    if (toggle) { SHOW_ALL = !SHOW_ALL; renderTasks(); return; }

    // 更多菜单
    var more = ev.target.closest('[data-x="more"]');
    if (more) {
      ev.stopPropagation();
      var vid = more.getAttribute('data-vid');
      OPEN_MENU = OPEN_MENU === vid ? null : vid;
      renderTasks();
      return;
    }
    // 动作
    var act = ev.target.closest('[data-x]');
    if (act) {
      var a = act.getAttribute('data-x'), v = act.getAttribute('data-vid');
      if (a === 'close' || a === 'more' || a === 'copy-ho') return;
      OPEN_MENU = null;
      return doAction(a, v);
    }
    // 卡片选中
    var card = ev.target.closest('[data-task]');
    if (card) {
      OPEN_MENU = null;
      return selectTask(card.getAttribute('data-task'));
    }
    // 7/30 天
    var per = ev.target.closest('[data-period]');
    if (per) {
      var box = per.closest('.mod') || per.closest('section');
      $$('[data-period]', box).forEach(function (b) { b.classList.toggle('on', b === per); });
      var wrap = box.querySelector('[data-chart="trend"]');
      var n = per.getAttribute('data-period') === '7' ? 7 : 30;
      if (wrap && D) wrap.innerHTML = SAPI.area(D.day.series.slice(-n), { h: 180, get: function (d) { return d.tasks; }, label: '任务' });
      return;
    }
    // 复制提示词 / 口播保存（U.* 产出的 data-act）
    var dact = ev.target.closest('[data-act]');
    if (dact) {
      var kind = dact.getAttribute('data-act');
      if (kind === 'copy') {
        var R = REPORTS[FOCUS];
        var p = R && R.prompts.filter(function (x) { return String(x.idx) === dact.getAttribute('data-idx'); })[0];
        if (p) navigator.clipboard.writeText(p.zh).then(function () { toast('第 ' + p.idx + ' 段已复制'); }, function () { toast('复制失败', 'error'); });
        return;
      }
      if (kind === 'save-voice') return doAction('save-voice', FOCUS);
      if (kind === 'prompts-all') {
        var RR = REPORTS[FOCUS];
        if (!RR) return toast('这条还没有报告');
        return openDrawer('还原提示词 · ' + RR.prompts.length + ' 段',
          RR.prompts.map(function (p) {
            return '<div class="rqv-pr"><b>第 ' + p.idx + ' 段</b><p>' + esc(p.zh) + '</p><p class="tiny muted">' + esc(p.en) + '</p></div>';
          }).join(''));
      }
      if (kind === 'report' || kind === 'bundle' || kind === 'handoff' || kind === 'retry' || kind === 'refresh' ||
          kind === 'create' || kind === 'import') {
        var vv = dact.getAttribute('data-task');
        if (kind === 'retry') return doAction('ai', vv || FOCUS);
        if (kind === 'handoff') return doAction('handoff', vv || FOCUS);
        return;
      }
    }
  }

  function selectTask(id) {
    if (FOCUS === id) return;
    FOCUS = id;
    renderTasks();
    var need = id;
    ensureReports([need]).then(function () {
      renderDetail();
      /* 中栏分镜 + 右栏三块一起淡入；每次切任务重播，轮询不触发 */
      ['#rqv-shots', '#rqv-refs', '#rqv-prompts', '#rqv-voice'].forEach(function (id) { reveal(id); });
    });
  }

  /* ── 轮询 ─────────────────────────────────────────── */
  function anyBusy() {
    return !!(D && D.tasks.some(function (t) { return t.busy || t.stageKey === 'preparing' || t.stageKey === 'analyzing'; }));
  }

  function refresh(manual, force) {
    if (!HOST) return Promise.resolve();
    return load(force).then(function () {
      renderAll();
      if (manual) toast('已刷新 · ' + D.meta.asOf.slice(11));
      schedule();
    }).catch(function (e) {
      LAST_ERR = e.message;
      if (window.console) console.error('[rv2] refresh failed:', e && e.stack || e);
      if (!D) $('#rqv-finding').innerHTML = '<div class="mod"><div class="mod-b">读取失败：' + esc(e.message) + '</div></div>';
    });
  }

  function schedule() {
    if (TIMER) { clearTimeout(TIMER); TIMER = null; }
    // ★ 有任务在跑：4 秒刷新一次（用户口径）；没在跑就不打扰
    if (!anyBusy()) return;
    TIMER = setTimeout(function () { if (document.visibilityState !== 'hidden') load().then(function () { renderAll(); schedule(); }); else schedule(); }, POLL_MS);
  }

  function load(force) {
    if (LOADING) return Promise.resolve();
    /* 首屏 app.js 的 RV_load 与自己的 init 会几乎同时触发，800ms 内合并成一次；
       动作后要看到结果的那次传 force=true，跳过合并 */
    if (!force && Date.now() - LAST_LOAD < 800) return Promise.resolve();
    LAST_LOAD = Date.now();
    LOADING = true;
    return Promise.all([
      getJSON('/api/reverse/list'),
      getJSON('/api/reverse/models').catch(function () { return { items: [], 'default': '' }; }),
      getJSON('/api/health').catch(function () { return null; })
    ]).then(function (res) {
      var list = res[0];
      var ids = (list.items || []).filter(function (it) { return it.has_report; }).map(function (it) { return String(it.video_id); });
      FOCUS = FOCUS && (list.items || []).some(function (it) { return String(it.video_id) === FOCUS; }) ? FOCUS : null;
      var want = (FOCUS ? [FOCUS] : []).concat(ids.slice(0, 4));
      return ensureReports(want).then(function () {
        D = buildData(list, res[1], res[2]);
        if (!FOCUS || !D.tasks.some(function (t) { return t.id === FOCUS; })) FOCUS = D.focusId;
        if (FOCUS) return ensureReports([FOCUS]);
      });
    }).finally(function () { LOADING = false; });
  }

  /* ── 启动 ─────────────────────────────────────────── */
  function init() {
    HOST = document.getElementById('rv2');
    if (!HOST || INITED) return;
    INITED = true;
    if (TICK) clearInterval(TICK);
    TICK = setInterval(function () {
      $$('#rv2 .rqv-elapsed[data-since]').forEach(function (el) {
        var since = Number(el.getAttribute('data-since')) || 0;
        if (since) el.textContent = '已用 ' + mmss(Math.round(Date.now() / 1000 - since));
      });
    }, 1000);

    HOST.addEventListener('click', onHostClick);
    bindMotion();
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeDrawer(); if (OPEN_MENU) { OPEN_MENU = null; renderTasks(); } }
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && HOST.classList.contains('active')) refresh(false);
    });

    refresh(false);
  }

  /* 工作台切换视图时会调 window.RV_load()；这里接管 */
  window.RV_load = function () {
    if (!HOST) HOST = document.getElementById('rv2');
    if (!HOST) return;
    if (!MOUNTED) { mount(); init(); }
    /* 2026-10-31 修复 P1-2：init 可能因 INITED 已置真而提前返回（首屏 mount 晚了一步），
       这里补一次 refresh，保证第一次进入就有任务队列。 */
    refresh(false);
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('rv2')) { mount(); init(); }
  });
  else if (document.getElementById('rv2')) { mount(); init(); }

  window.RV2 = { refresh: function () { refresh(true); }, state: function () { return { focus: FOCUS, tasks: D && D.tasks.length }; } };
  /* 兼容旧内联调用 */
  window.reverseCreate = create;
  window.reverseImport = importVideo;
})();


