/* TikTok 对标工作台 · 提示词反推（tk_reverse）前端
   独立文件：删掉 index.html 里对应的 <link>/<script> 与 nav 项即可完全回滚。
   依赖 app.js 暴露的全局工具：$ / $$ / api / toast / fmtTime。 */
(function () {
  "use strict";

  var MODEL_KEY = "tkrev_model";
  var MODE_KEY = "tkrev_mode";
  var ITEMS = [];
  var TIMER = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  var STAGE = {
    pending: ["排队中", "rvb-idle"],
    preparing: ["生成素材中", "rvb-run"],
    analyzing: ["AI 反推中", "rvb-run"],
    ready: ["素材就绪", "rvb-ready"],
    done: ["已完成", "rvb-done"],
    failed: ["失败", "rvb-fail"],
    stopped: ["已停止", "rvb-idle"],
  };

  function badge(stage) {
    var s = STAGE[stage] || ["未知", "rvb-idle"];
    return '<span class="rv-badge ' + s[1] + '">' + esc(s[0]) + "</span>";
  }

  function fmtDur(s) {
    var n = Number(s) || 0;
    return n ? n.toFixed(2) + "s" : "-";
  }

  function mmss(sec) {
    var n = Math.max(0, Math.round(Number(sec) || 0));
    var m = Math.floor(n / 60);
    var s = n % 60;
    if (m) return m + ":" + (s < 10 ? "0" : "") + s;
    return s + " 秒";
  }

  /* ── 进度条 ──
     后端 status 给的是「阶段 + 真实百分比 + steps 清单 + busy_since」。
     长步骤（模型读图）没有细粒度心跳，所以这里如实画两件事：
       1) 已走到第几步、当前步是哪一格；
       2) 已用多久、状态多久前更新过 —— 数字在动就说明进程还活着。
     ★ 不做按时间推测百分比：进度只跟真实阶段前进。 */
  function stepsBlock(it) {
    var steps = it.steps || [];
    if (!steps.length) return "";
    var p = Math.max(0, Math.min(100, Number(it.progress) || 0));
    var cur = 0;
    for (var i = 0; i < steps.length; i++) {
      if (p >= (Number(steps[i].at) || 0)) cur = i;
    }
    var items = steps.map(function (s, i) {
      var cls = i < cur ? "done" : (i === cur ? "cur" : "");
      return '<li class="' + cls + '" title="' + esc(s.label) + " @" + esc(s.at) + '%">' +
        "<span>" + (i + 1) + "</span>" + esc(s.label) + "</li>";
    }).join("");
    var since = Number(it.busy_since) || 0;
    var upd = Number(it.updated_at) || 0;
    return '<div class="rv-prog" data-since="' + since + '" data-updated="' + upd + '">' +
      '<div class="rv-prog-t">' +
        "<span>" + esc(it.stage_label || "处理中") + "</span>" +
        "<span><b>" + p + "%</b> · <i data-elapsed>" +
          (since ? "已用 " + mmss(Math.round(Date.now() / 1000) - since) : "已用 -") + "</i></span>" +
      "</div>" +
      '<div class="rv-bar"><i class="' + (it.busy ? "rv-bar-live" : "") + '" style="width:' +
        Math.max(2, p) + '%"></i></div>' +
      '<ol class="rv-steps">' + items + "</ol>" +
      '<p class="rv-hint">模型读图是长步骤，百分比只随阶段前进；' +
        '<i data-age>' + (upd ? "更新于 " + Math.max(0, Math.round(Date.now() / 1000 - upd)) + " 秒前" : "等待首次更新") +
        "</i> 在变说明进程还在跑。</p>" +
      "</div>";
  }

  var TICK = null;
  function startTick() {
    if (TICK) return;
    TICK = setInterval(function () {
      var nodes = document.querySelectorAll(".rv-prog[data-since]");
      if (!nodes.length) { clearInterval(TICK); TICK = null; return; }
      var nowSec = Date.now() / 1000;
      Array.prototype.forEach.call(nodes, function (el) {
        var since = Number(el.getAttribute("data-since")) || 0;
        var upd = Number(el.getAttribute("data-updated")) || 0;
        var a = el.querySelector("[data-elapsed]");
        if (a && since) a.textContent = "已用 " + mmss(nowSec - since);
        var b = el.querySelector("[data-age]");
        if (b && upd) b.textContent = "更新于 " + Math.max(0, Math.round(nowSec - upd)) + " 秒前";
      });
    }, 1000);
  }

  /* ── 模型下拉 ── */
  function loadModels() {
    var sel = $("#rv-model");
    if (!sel) return Promise.resolve();
    return api("/api/reverse/models")
      .then(function (d) {
        var cur = "";
        try { cur = localStorage.getItem(MODEL_KEY) || ""; } catch (e) { cur = ""; }
        var val = cur || d["default"] || "";
        sel.innerHTML = (d.items || [])
          .map(function (m) {
            return '<option value="' + esc(m.id) + '"' +
              (m.id === val ? " selected" : "") + ">" + esc(m.name) + "</option>";
          })
          .join("");
      })
      .catch(function () {
        sel.innerHTML = '<option value="">（读不到 models.json）</option>';
      });
  }

  function curModel() {
    var sel = $("#rv-model");
    var v = sel ? sel.value : "";
    try { if (v) localStorage.setItem(MODEL_KEY, v); } catch (e) { /* 隐私模式忽略 */ }
    return v;
  }

  function curMode() {
    var select = $("#rv-mode");
    var value = select ? select.value : "restore";
    try { localStorage.setItem(MODE_KEY, value); } catch (e) { /* optional preference */ }
    return value;
  }

  /* ── 列表 ── */
  function load(manual) {
    if (TIMER) { clearTimeout(TIMER); TIMER = null; }
    return api("/api/reverse/list")
      .then(function (d) {
        ITEMS = d.items || [];
        render();
        if (manual && $("#rv-updated")) $("#rv-updated").textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
        schedule();
      })
      .catch(function (e) {
        var host = $("#rv-list");
        if (host) host.innerHTML = '<div class="panel muted">读取失败：' + esc(e.message) + "</div>";
      });
  }

  function anyBusy() {
    return ITEMS.some(function (it) {
      return it.busy || it.stage === "preparing" || it.stage === "analyzing";
    });
  }

  function schedule() {
    if (TIMER) { clearTimeout(TIMER); TIMER = null; }
    if (!anyBusy()) return;
    // ★ 有任务在跑就一直轮询（哪怕当前不在这个视图），否则切走再切回来进度是死的。
    TIMER = setTimeout(function () { load(false); }, 2000);
  }

  function render() {
    var host = $("#rv-list");
    if (!host) return;
    var voiceNodes = {};
    Array.prototype.forEach.call(host.querySelectorAll('.rv-voice'), function(n) { voiceNodes[n.getAttribute('data-voice-id')] = n; });
    // 轮询会整表重建：把已展开的交接包内容按 video_id 暂存，重建后回填，
    // 否则用户刚点开的交接包会被下一次 3s 轮询清空。
    var keep = {};
    Array.prototype.forEach.call(host.querySelectorAll(".rv-panel-host"), function (h) {
      var c = h.closest(".rv-card");
      if (c && h.innerHTML.trim()) keep[c.getAttribute("data-vid")] = h.innerHTML;
    });
    // ★ 2026-10-23 审查 R14：轮询会整表重建，日志展开态必须跟着 vid 存下来，
    //   否则用户点开「过程日志」2 秒后又被自动折叠；滚动位置同理。
    var openLogs = {};
    Array.prototype.forEach.call(host.querySelectorAll(".rv-log"), function (d) {
      if (d.open) {
        var c = d.closest(".rv-card");
        if (c) openLogs[c.getAttribute("data-vid")] = true;
      }
    });
    var y = window.scrollY;
    if (!ITEMS.length) {
      host.innerHTML = '<div class="panel rv-empty">' +
        '<p class="muted">还没有任务。导入本地视频，或粘贴 TikTok 链接并点「① 生成素材」开始。</p>' +
        '<p class="muted">素材阶段只下原片、抽帧、导出首帧，不消耗 token；' +
        '点「② AI 反推」才会调用视觉模型读帧写提示词。</p></div>';
      return;
    }
    host.innerHTML = ITEMS.map(card).join("");
    Array.prototype.forEach.call(host.querySelectorAll('.rv-voice'), function(n) {
      var id = n.getAttribute('data-voice-id');
      if (voiceNodes[id]) n.replaceWith(voiceNodes[id]);
      else if (window.ReverseVoice) {
        window.ReverseVoice.read(id).then(function(v) { fillVoice(n, v); }).catch(function(e) {
          n.querySelector('summary').textContent = '原片口播：读取失败，可重新确认';
          voiceMessage(n, e.message, true); n.querySelector('fieldset').disabled = false;
        });
      }
    });
    Object.keys(keep).forEach(function (vid) {
      var h = host.querySelector('.rv-card[data-vid="' + vid + '"] .rv-panel-host');
      if (h) h.innerHTML = keep[vid];
    });
    Object.keys(openLogs).forEach(function (vid) {
      var d = host.querySelector('.rv-card[data-vid="' + vid + '"] .rv-log');
      if (d) d.open = true;
    });
    if (y) window.scrollTo(0, y);
    if (anyBusy()) startTick();
  }

  function card(it) {
    var meta = it.meta || {};
    var probe = it.probe || {};
    var stage = it.stage || "pending";
    var showBar = it.busy || stage === "preparing" || stage === "analyzing";
    var dur = probe.duration || meta.duration || 0;
    var res = (probe.width ? probe.width + "×" + probe.height : (meta.width ? meta.width + "×" + meta.height : "-"));
    var logs = (it.log || []).slice(-6);

    var btns = [];
    if (showBar) {
      // ★ 2026-10-23 审查 R3：后台能取消了，前端必须有入口，否则还是只能干等。
      btns.push('<button class="btn ghost sm" data-act="stop" data-vid="' + esc(it.video_id) + '">停止</button>');
    }
    if (it.frames) {
      btns.push('<button class="btn primary sm" data-act="ai" data-vid="' + esc(it.video_id) + '">' +
        (it.has_report ? "重跑 AI 反推" : "② AI 反推") + "</button>");
    }
    if (it.has_report) {
      if ((it.report_mode || it.mode) === 'restore') btns.push('<button class="btn sm" data-act="optimize" data-vid="' + esc(it.video_id) + '">用这个对标优化</button>');
      btns.push('<button class="btn sm" data-act="report" data-vid="' + esc(it.video_id) + '" data-url="' +
        esc(it.report) + '">打开报告</button>');
      if (it.bundle) btns.push('<button class="btn sm" data-act="bundle" data-vid="' + esc(it.video_id) + '" data-url="' + esc(it.bundle) + '">下载素材包</button>');
    }
    if (it.frames) {
      if ((it.report_mode || it.mode) !== "restore") btns.push('<button class="btn ghost sm" data-act="handoff" data-vid="' + esc(it.video_id) + '">交接包</button>');
      btns.push('<button class="btn ghost sm" data-act="rebuild" data-vid="' + esc(it.video_id) + '">重建素材</button>');
    }
    btns.push('<button class="btn ghost sm" data-act="open" data-vid="' + esc(it.video_id) + '">打开目录</button>');
    btns.push('<button class="btn ghost sm danger" data-act="del" data-vid="' + esc(it.video_id) + '">删除</button>');

    return '' +
      '<section class="panel rv-card" data-vid="' + esc(it.video_id) + '">' +
        '<div class="rv-head">' +
          '<div class="rv-title">' +
            badge(stage) +
            '<b>@' + esc(meta.author || "（未知作者）") + "</b>" +
            '<span class="rv-id">' + esc(it.video_id) + "</span>" +
          "</div>" +
          '<div class="rv-acts">' + btns.join("") + "</div>" +
        "</div>" +
        '<div class="rv-kv">' +
          "<span>时长 <b>" + fmtDur(dur) + "</b></span>" +
          "<span>模式 <b>" + ((it.report_mode || it.mode) === "restore" ? "原片还原" : ((it.report_mode || it.mode) === "ad" ? "广告拆解" : "按上方选择")) + "</b></span>" +
          "<span>分辨率 <b>" + esc(res) + "</b></span>" +
          "<span>帧率 <b>" + esc((probe.fps || meta.fps || "-")) + " fps</b></span>" +
          "<span>抽帧 <b>" + (it.frames || 0) + "</b></span>" +
          "<span>送模型 <b>" + (it.ai_frames || 0) + "</b></span>" +
          "<span>镜头 <b>" + (it.shots || 0) + "</b></span>" +
          "<span>首帧 <b>" + (it.refs || 0) + "</b></span>" +
          "<span>声音 <b>" + (probe.has_audio === false ? "无音轨" : probe.has_audio === true ? "有音轨 · 口播未转写" : "音轨待确认") + "</b></span>" +
          "<span>播放/点赞 <b>" + (meta.view || 0) + "/" + (meta.like || 0) + "</b></span>" +
        "</div>" +
        (showBar ? stepsBlock(it) : "") +
        (it.error ? '<div class="rv-err">' + esc(it.error) + "</div>" : "") +
        (meta.desc ? '<p class="rv-desc">' + esc(String(meta.desc).slice(0, 180)) + "</p>" : "") +
        voiceBlock(it) +
        (logs.length
          ? '<details class="rv-log"><summary>过程日志（' + (it.log || []).length + " 条）</summary><pre>" +
            esc(logs.map(function (l) { return "· " + l.msg; }).join("\n")) + "</pre></details>"
          : "") +
        '<div class="rv-panel-host"></div>' +
      "</section>";
  }

  function voiceBlock(it) {
    if (!window.ReverseVoice) return '';
    var id=esc(it.video_id), hint=({de:'德语',en:'英语',fr:'法语',es:'西班牙语',it:'意大利语',pt:'葡萄牙语',zh:'中文',ja:'日语',ko:'韩语'})[(it.meta||{}).language_guess];
    return '<details class="rv-voice" data-voice-id="'+id+'"><summary>原片口播：正在读取…</summary><fieldset disabled><div class="rv-voice-fields"><label>原片是否有口播 / 语言<select data-voice-choice><option value="unknown">尚未确认</option><option value="none">无口播（仅音乐 / 环境声 / 静音）</option>'+ReverseVoice.languages.map(function(v){return '<option value="'+esc(v)+'">有口播 · '+esc(v)+'</option>';}).join('')+'<option value="custom">有口播 · 自定义语言</option></select></label><label data-voice-custom-wrap hidden>口播语言<input data-voice-custom maxlength="100" placeholder="例如：荷兰语 / 德语和英语"></label><button type="button" class="btn sm" data-act="save-voice" data-vid="'+id+'">保存口播信息</button></div></fieldset><p>这里确认的是原视频的口播语言，保存后优化功能自动带入。'+(hint?'简介语言提示：'+esc(hint)+'，仅供核对，不代表音频识别。':'')+'</p><p data-voice-message role="status"></p><details class="rv-voice-play"><summary>播放原片核对</summary><video controls preload="none" src="/tk_reverse/'+id+'/src.mp4"></video></details></details>';
  }
  function voiceMessage(n, text, error) { var el=n.querySelector('[data-voice-message]');el.textContent=text;el.classList.toggle('rv-voice-error',!!error); }
  function fillVoice(n, v) {
    var value=v.state==='spoken'?v.language:v.state, custom=v.state==='spoken'&&ReverseVoice.languages.indexOf(value)<0;
    n.querySelector('[data-voice-choice]').value=custom?'custom':value;
    n.querySelector('[data-voice-custom]').value=custom?v.language:'';
    n.querySelector('[data-voice-custom-wrap]').hidden=!custom;
    n.querySelector('summary').textContent=ReverseVoice.label(v);
    n.querySelector('fieldset').disabled=false;n.dataset.dirty='';
  }

  /* ── 操作 ── */
  function create() {
    var inp = $("#rv-url");
    var url = inp ? inp.value.trim() : "";
    if (!url) { toast("先填 TikTok 视频链接"); return; }
    var btn = $("#rv-create");
    if (btn) { btn.disabled = true; btn.textContent = "解析中…"; }
    api("/api/reverse/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    })
      .then(function (r) {
        // ★ 2026-10-23 审查 R13：命中「素材已存在」时后端只复用、并没有在跑，
        //   旧文案「已开始生成素材」会让用户以为 AI 在工作。
        toast(r.reused ? r.notice : "已开始生成素材：" + r.video_id);
        if (inp) inp.value = "";
        return load(true);
      })
      .catch(function (e) { toast("创建失败：" + e.message, "error"); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = "① 生成素材"; }
      });
  }

  function post(url, body, okMsg) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    })
      .then(function (r) { if (okMsg) toast(okMsg); load(true); return r; })
      .catch(function (e) { toast("失败：" + e.message, "error"); });
  }

  function importVideo(file) {
    if (!file) return;
    if (!/\.(mp4|mov|webm|mkv)$/i.test(file.name) || file.size > 500 * 1024 * 1024) {
      toast("请选择500MB以内的mp4、mov、webm或mkv视频", "error");
      return;
    }
    var button = $("#rv-import");
    var label = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = "上传中 0%"; }

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/reverse/import?filename=" + encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    // ★ 2026-10-23 审查 R15：500MB 直传期间只有「导入中…」，用户会以为卡死刷新；
    //   用 upload.onprogress 报真实百分比（个别 XHR 桩没有 upload 对象，先判空）。
    if (xhr.upload) {
      xhr.upload.onprogress = function (e) {
        if (!button || !e.lengthComputable) return;
        var pct = Math.round((e.loaded / e.total) * 100);
        button.textContent = pct < 100 ? "上传中 " + pct + "%" : "处理中…";
      };
    }
    xhr.onload = function () {
      var data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (e) { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        toast("本地视频已导入，正在生成素材");
        load(true);
      } else {
        toast(data.detail || "导入失败", "error");
      }
    };
    xhr.onerror = function () { toast("上传失败（网络中断）", "error"); };
    xhr.onloadend = function () {
      if (button) { button.disabled = false; button.textContent = label || "导入本地视频"; }
      var input = $("#rv-file"); if (input) input.value = "";
    };
    xhr.send(file);
  }

  function handoff(vid, host) {
    host.innerHTML = '<p class="muted">生成中…</p>';
    api("/api/reverse/handoff/" + encodeURIComponent(vid))
      .then(function (d) {
        host.innerHTML = '<div class="rv-ho"><div class="rv-ho-h"><b>Agent 交接包</b>' +
          '<span class="muted">已存 ' + esc(d.saved) + "</span>" +
          '<button class="btn sm" data-copy="' + encodeURIComponent(vid) + '">复制全文</button></div>' +
          "<pre>" + esc(d.text) + "</pre></div>";
        var pre = host.querySelector("pre");
        var cp = function () {
          navigator.clipboard.writeText(pre.innerText).then(function () { toast("已复制交接包"); },
            function () { toast("复制失败，请手动选择文本", "error"); });
        };
        var b = host.querySelector("[data-copy]");
        if (b) b.addEventListener("click", cp);
        cp();
      })
      .catch(function (e) { host.innerHTML = '<p class="rv-err">' + esc(e.message) + "</p>"; });
  }

  function bind() {
    var host = $("#rv-list");
    if (!host) return;
    host.addEventListener('input',function(ev){
      if(!ev.target.matches('[data-voice-choice],[data-voice-custom]'))return;
      var n=ev.target.closest('.rv-voice');n.dataset.dirty='yes';
      n.querySelector('[data-voice-custom-wrap]').hidden=n.querySelector('[data-voice-choice]').value!=='custom';
      voiceMessage(n,'尚未保存；点击“保存口播信息”后才会传给优化。',false);
    });
    // 交接包复制：走委托，重绘/回填后依然有效
    host.addEventListener("click", function (ev) {
      var cpBtn = ev.target.closest("[data-copy]");
      if (!cpBtn) return;
      var cardEl = cpBtn.closest(".rv-card");
      var pre = cardEl ? cardEl.querySelector(".rv-ho pre") : null;
      if (!pre) return;
      navigator.clipboard.writeText(pre.innerText).then(function () { toast("已复制交接包"); },
        function () { toast("复制失败，请手动选择文本", "error"); });
    });
    host.addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-act]");
      if (!b) return;
      var vid = b.getAttribute("data-vid");
      var act = b.getAttribute("data-act");
      var cardEl = b.closest(".rv-card");
      var ph = cardEl ? cardEl.querySelector(".rv-panel-host") : null;
      if (act === "copy") return;   // 由下方 [data-copy] 委托处理
      if (act === 'save-voice') {
        var n=b.closest('.rv-voice'),choice=n.querySelector('[data-voice-choice]').value;
        var value={state:choice==='unknown'||choice==='none'?choice:'spoken',language:choice==='custom'?n.querySelector('[data-voice-custom]').value:choice};
        if(value.state==='spoken'&&!value.language.trim()){voiceMessage(n,'请填写原片口播语言。',true);return;}
        n.querySelector('fieldset').disabled=true;voiceMessage(n,'正在保存…',false);
        ReverseVoice.save(vid,value).then(function(v){fillVoice(n,v);voiceMessage(n,'已保存到这条反推任务，导入优化时自动带入。',false);}).catch(function(e){n.querySelector('fieldset').disabled=false;voiceMessage(n,e.message,true);});
      } else if (act === "ai") {
        var model = curModel();
        var mode = curMode();
        if (b.textContent.indexOf("重跑") >= 0 &&
            !confirm(mode === "restore" ? "将重新拆镜并生成还原报告，成功后保存新版本与原报告备份。继续？" : "重跑会覆盖现有报告与首帧，继续？")) return;
        b.disabled = true;
        post("/api/reverse/analyze/" + encodeURIComponent(vid), { model: model, mode: mode },
             mode === "restore" ? "已开始原片还原，正在核对镜头与动作" : "已开始广告拆解");
        setTimeout(function () { b.disabled = false; }, 2000);
      } else if (act === "optimize") {
        if(cardEl.querySelector('.rv-voice[data-dirty="yes"]')){toast('请先保存这条视频的口播信息，再导入优化。','error');return;}
        if (window.PO && window.PO.importReport) {
          switchView('prompt');
          history.pushState(null, '', '#prompt');
          window.PO.importReport(vid);
        } else { location.href = '/?optimize_from=' + encodeURIComponent(vid) + '#prompt'; }
      } else if (act === "report" || act === "bundle") {
        var u = b.getAttribute("data-url");
        if (u) window.open(u, "_blank", "noopener");
      } else if (act === "stop") {
        post("/api/reverse/stop/" + encodeURIComponent(vid), {}, "已请求停止，当前步骤结束后停");
      } else if (act === "rebuild") {
        if (!confirm("重建会清理派生帧图与当前报告。本地导入保留原片，链接素材会重新下载。继续？")) return;
        post("/api/reverse/rebuild/" + encodeURIComponent(vid), {}, "已开始重建素材");
      } else if (act === "open") {
        post("/api/reverse/open/" + encodeURIComponent(vid), {}, "");
      } else if (act === "handoff") {
        if (ph) handoff(vid, ph);
      } else if (act === "del") {
        if (!confirm("删除任务目录（含原片、帧图、报告）？此操作不可恢复。")) return;
        api("/api/reverse/" + encodeURIComponent(vid), { method: "DELETE" })
          .then(function () { toast("已删除"); load(true); })
          .catch(function (e) { toast("删除失败：" + e.message, "error"); });
      }
    });
  }

  function init() {
    var mode = $("#rv-mode");
    if (mode) {
      try { var saved = localStorage.getItem(MODE_KEY); if (saved === "ad" || saved === "restore") mode.value = saved; } catch (e) { /* optional preference */ }
      // ★ 2026-10-23 验收 A4：旧实现只在点「AI 反推」时存模式，
      //   选完切走直接刷新会丢。改成 change 即刻落盘，不等按钮。
      mode.addEventListener("change", function () {
        try { localStorage.setItem(MODE_KEY, mode.value); } catch (e) { /* optional preference */ }
      });
    }
    // 标签页切回来时立刻补一次，别等下一个轮询周期
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && $("#view-reverse") &&
          $("#view-reverse").classList.contains("active")) window.RV_load();
    });
    bind();          // 只在进入视图时拉数据（见 window.RV_load），首屏不额外发请求
    // app.js may activate a #reverse deep link before this script is loaded.
    var view = $("#view-reverse");
    if (view && view.classList.contains("active")) window.RV_load();
  }

  window.RV_load = function () {
    var sel = $("#rv-model");
    if (!sel || !sel.options.length) loadModels();
    load(true);
  };
  window.reverseCreate = create;
  window.reverseImport = importVideo;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
