/* ============================================================================
   ps_pipeline.js — 提示词优化 · 模板流水线（2026-10-26）

   解决的三件事：
     1) 文档 → 模板：把对标档案的「拍序 / 时长分配 / 镜头 / 句式 / 口播规律」
        抽成结构化模板（不是再看一遍原文，而是可套用的骨架）。
     2) 图片 → 产品：上传产品图，调后端视觉模型识别品类/材质/颜色/场景/卖点；
        AI 不可用时降级为「手填 + 本地主色采样」，流程不断。
     3) 模板 × 产品 → 方案：按模板骨架批量拼装多份提示词，**按模板分组展示**，
        每份可单复制、每组可整导出。

   与 prompt_studio.js 的边界：
     · 只读 `PS.state.bench.list`（档案）与 `PS.state.v`（产品变量），
       解析逻辑一律复用，不在这里重写 parseBench。
     · 回填 ① 区走 `PS.applyVars()`。
     · 存储独立：模板 wb.pp.tpl.v1 / 产品 wb.pp.prod.v1；图片走 IndexedDB
       （与素材仓同库不同 key，前缀 prod:）。
   ========================================================================= */
(function () {
  "use strict";

  var KEY = { tpl: "wb.pp.tpl.v1", prod: "wb.pp.prod.v1" };
  var MAX_TPL = 12;
  var MAX_IMG = 4;
  var MAX_IMG_BYTES = 4 * 1024 * 1024;

  var S = {
    tpls: [],          // 模板
    pick: {},          // 归纳用的档案勾选 { archiveId: true }
    useTpl: {},        // 生成时启用的模板 { tplId: true }
    prod: null,        // 当前产品 { id, images:[{name,mime,key}], recog, edited, at, degraded }
    plans: [],         // 生成结果 [{ tplId, tplName, items:[...] }]
    per: 3,            // 每模板份数
    scanState: "idle"  // idle | scanning | ok | degraded
  };

  /* ── 工具 ───────────────────────────────────────────────────────────── */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function say(msg) {
    if (typeof window.toast === "function") window.toast(msg); else console.log("[PT]", msg);
  }
  function words(s) { return String(s || "").trim().split(/\s+/).filter(Boolean).length; }
  function uid(p) { return (p || "t") + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); }
  function today() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function copyText(text, label) {
    var done = function () { say("已复制" + (label ? "：" + label : "")); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
        document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); done();
      } catch (e) { say("复制失败，请手动全选"); }
    }
  }
  function downloadText(name, text) {
    var blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  /* ── IndexedDB：产品图（复用素材仓的库，key 前缀 prod:）──────────────── */
  var IDB_NAME = "wb.po.assets", IDB_STORE = "files", _idbP = null;
  function idb() {
    if (_idbP) return _idbP;
    _idbP = new Promise(function (res, rej) {
      if (!window.indexedDB) { rej(new Error("浏览器不支持 IndexedDB")); return; }
      var rq = indexedDB.open(IDB_NAME, 1);
      rq.onupgradeneeded = function () {
        if (!rq.result.objectStoreNames.contains(IDB_STORE)) rq.result.createObjectStore(IDB_STORE);
      };
      rq.onsuccess = function () { res(rq.result); };
      rq.onerror = function () { rej(rq.error || new Error("打开本机素材仓失败")); };
    });
    return _idbP;
  }
  function idbPut(key, val) {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(IDB_STORE, "readwrite");
        tx.objectStore(IDB_STORE).put(val, key);
        tx.oncomplete = function () { res(true); };
        tx.onerror = function () { rej(tx.error || new Error("写入失败")); };
      });
    });
  }
  function idbGet(key) {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(IDB_STORE, "readonly");
        var rq = tx.objectStore(IDB_STORE).get(key);
        rq.onsuccess = function () { res(rq.result || null); };
        rq.onerror = function () { rej(rq.error); };
      });
    });
  }
  function idbDel(key) {
    return idb().then(function (db) {
      return new Promise(function (res) {
        var tx = db.transaction(IDB_STORE, "readwrite");
        tx.objectStore(IDB_STORE).delete(key);
        tx.oncomplete = function () { res(true); };
        tx.onerror = function () { res(false); };
      });
    }).catch(function () { return false; });
  }

  /* ── 词表 ───────────────────────────────────────────────────────────── */

  /* 每拍角色：按「位置 + 关键词」判，优先级从上到下 */
  var ROLES = [
    ["钩子", /钩子|开场|第一秒|前\s*3\s*秒|问句|你是不|hook/i],
    ["收尾", /收尾|结尾|最后|cta|行动|链接|link|下单/i],
    ["痛点", /痛点|烦恼|麻烦|乱|脏|异味|费时|难搞|油渍/i],
    ["对比", /前后对比|左右对比|两半|一半|对比演示|vorher|nachher/i],
    ["证据", /数字|承重|\d+\s*(层|kg|cm|mm|ml|件|张|包|天|周)|销量|评价|认证|测试|结果|效果/i],
    ["细节", /细节|特写|微距|纹路|光泽|缝线|质感|压纹/i],
    ["演示", /演示|使用|擦|装|取|放|试穿|上手|安装|展开|折叠|抽出/i]
  ];

  /* 镜头/调度词：从拍的画面描述里抽，也可直接来自档案 techniques */
  var CAMERA = [
    ["微距", /微距|极近|贴近|macro/i],
    ["特写", /特写|close.?up/i],
    ["全景", /全景|全身|wide/i],
    ["俯拍", /俯拍|俯视|top.?down|从上往下/i],
    ["手持", /手持|handheld|轻微手抖/i],
    ["一镜到底", /一镜到底|长镜头|不切镜|one.?take/i],
    ["第一人称", /第一人称|pov|主观镜头/i],
    ["推近", /推近|缓缓推|push.?in/i],
    ["跟拍", /跟拍|跟随|follow/i],
    ["摇镜", /摇镜|横摇|pan\b/i]
  ];

  /* 句式模式：从原文抽「提示词写法」，抽到就变成可填空的模板句 */
  var SENTENCES = [
    ["把 X 说成 Y", /把(.{1,14}?)说成/, "把{卖点}说成{可验证的数字或对比}"],
    ["不是 X 而是 Y", /不是(.{1,12}?)而是/, "不是{否定项}，而是{实际效果}"],
    ["先 X 再 Y", /先(.{1,14}?)再/, "先{第一步动作}，再{第二步动作}"],
    ["数字 + 单位", /(\d+(?:\.\d+)?)\s*(层|kg|cm|mm|ml|天|周|秒|件|张|包)/, "{数量}{单位} —— 只用可量化的数字，不写「超大容量」"],
    ["否定式约束", /不(硬广|logo|转场|口号|滤镜|回字样)/, "不出现{禁止项}"],
    ["时间轴锚点", /(\d{1,2}(?:\.\d)?)\s*[-–—]\s*(\d{1,2}(?:\.\d)?)\s*s/, "每段标 {起}s–{止}s，让剪辑能对轴"],
    ["口播与画面分工", /口播|voice|旁白/i, "画面负责{看得到的证据}，口播只补{看不到的原因}"]
  ];

  /* 品类 → 德语通用名（口播要完整可用，不能留占位符） */
  var DE_NOUN = [
    [/运动套装|休闲套装|卫衣套装/, "das Trainingsset"],
    [/卫衣|hoodie/i, "der Hoodie"],
    [/裤|hose/i, "die Hose"],
    [/储物架|置物架|货架/, "das Regal"],
    [/湿巾|清洁布|reinigungstuch/i, "das Reinigungstuch"],
    [/床品|床单|被套/, "die Bettwäsche"],
    [/清洁剂|清洁液/, "der Reiniger"],
    [/诱捕|捕虫|果蝇/, "die Falle"],
    [/收纳|整理箱/, "die Aufbewahrungsbox"],
    [/包|背包|taschen/i, "die Tasche"],
    [/鞋|sneaker/i, "die Schuhe"],
    [/灯|照明/, "die Lampe"],
    [/厨|锅|餐具/, "das Küchenhelfer"]
  ];

  /* 中文特征词 → 德语（只翻「看得见」的属性，保证口播真实不虚构） */
  var DE_FEAT = [
    ["粉色", "rosa"], ["深蓝", "dunkelblau"], ["浅蓝", "hellblau"], ["米白", "creme"], ["白色", "weiß"],
    ["黑色", "schwarz"], ["灰色", "grau"], ["绿色", "grün"], ["橄榄绿", "olivgrün"], ["红色", "rot"],
    ["针织", "Strick"], ["棉", "Baumwolle"], ["抓绒", "Fleece"], ["金属", "Metall"], ["钢", "Stahl"],
    ["塑料", "Kunststoff"], ["木", "Holz"], ["无纺布", "Vlies"], ["抽绳", "Kordelzug"], ["罗纹", "Rippbündchen"],
    ["连帽", "Kapuze"], ["口袋", "Tasche"], ["三层", "drei Ebenen"], ["两层", "zwei Ebenen"],
    ["承重", "Traglast"], ["冲孔", "Lochblech"], ["可调", "verstellbar"], ["折叠", "faltbar"],
    ["成套", "im Set"], ["一套", "im Set"], ["防漏", "auslaufsicher"], ["可拆", "abnehmbar"]
  ];

  var VAR_HINTS = {
    product: "产品（含规格）", material: "材质", structure: "结构 / 尺寸", scene: "环境",
    surface: "接触面", surfaceTrace: "痕迹", heroAction: "核心动作", benefit: "收益证据",
    accent: "点缀色", flaw: "瑕疵", sounds: "同期声"
  };

  /* ── 模板归纳 ───────────────────────────────────────────────────────── */

  function roleOf(beat, i, n) {
    var name = String(beat.name || "");
    if (i === 0) return "钩子";
    if (i === n - 1 && n > 1) return "收尾";
    /* 标题优先：`## 6-9s 细节` 的「细节」比画面里的「油渍」更权威；
       否则「油渍被推成一团」会把细节拍误判成痛点拍。 */
    for (var k = 0; k < ROLES.length; k++) {
      if (ROLES[k][1].test(name)) return ROLES[k][0];
    }
    var text = name + " " + (beat.visual || "") + " " + (beat.voice || "");
    for (var j = 0; j < ROLES.length; j++) {
      if (ROLES[j][1].test(text)) return ROLES[j][0];
    }
    return i >= n - 2 ? "收尾" : "演示";
  }

  function camerasOf(text) {
    var out = [];
    CAMERA.forEach(function (c) { if (c[1].test(text)) out.push(c[0]); });
    return out;
  }

  function sentencesOf(text) {
    var out = [];
    SENTENCES.forEach(function (s) {
      if (s[1].test(text)) out.push({ k: s[0], t: s[2] });
    });
    return out;
  }

  function sectionsOf(raw) {
    var out = [];
    String(raw || "").replace(/\r\n?/g, "\n").split("\n").forEach(function (line) {
      var m = line.match(/^(#{2,3})\s+(.+?)\s*$/);
      if (!m) return;
      var t = m[2].replace(/^\d+[\s.、·]*/, "").replace(/[·:：].*$/, "").trim();
      if (t && out.indexOf(t) < 0) out.push(t.slice(0, 20));
    });
    return out.slice(0, 10);
  }

  function paceOf(avg, n) {
    if (avg && avg <= 2.2) return "快切";
    if (n >= 7) return "快切";
    if (avg && avg <= 3.4) return "中速";
    return "慢推";
  }

  /* 让 Σdur 严格等于 span：合并多档时每拍取的是平均时长，
     总和会落在「较长那份」上（如 6 拍合计 18s、但平均 span 16.5s），
     不归一化就会出现「分镜标到 18s、头部写 16.5s」的自相矛盾。 */
  function normalizeDurations(t, span) {
    var sum = t.ratio.reduce(function (a, r) { return a + r.dur; }, 0);
    if (!sum || !span) return t;
    var k = span / sum;
    t.ratio.forEach(function (r) { r.dur = +(r.dur * k).toFixed(1); });
    var got = t.ratio.reduce(function (a, r) { return a + r.dur; }, 0);
    var diff = +(span - got).toFixed(1);
    if (Math.abs(diff) >= 0.05) {
      var last = t.ratio[t.ratio.length - 1];
      last.dur = +(last.dur + diff).toFixed(1);
    }
    t.ratio.forEach(function (r) { r.share = +(r.dur / span * 100).toFixed(1); });
    return t;
  }

  /* 单份档案 → 一个模板 */
  function learnOne(a) {
    var beats = (a.beats || []).filter(function (b) { return b; });
    if (!beats.length) return null;
    var n = beats.length;
    var span = (a.rhythm && a.rhythm.span) || 0;
    if (!span) {
      var last = beats[n - 1];
      span = Number(last.t1) || (n * 2);
    }
    var raw = String(a.raw || "");
    var allText = raw + " " + beats.map(function (b) {
      return (b.name || "") + (b.visual || "") + (b.voice || "");
    }).join(" ");

    var ratio = beats.map(function (b, i) {
      var t0 = Number(b.t0) || 0;
      var t1 = Number(b.t1) || (t0 + 2);
      var dur = Math.max(0.4, +(t1 - t0).toFixed(1));
      return {
        i: i + 1, role: roleOf(b, i, n), name: String(b.name || "").slice(0, 28),
        t0: t0, t1: t1, dur: dur,
        share: span > 0 ? +(dur / span * 100).toFixed(1) : +(100 / n).toFixed(1),
        cam: camerasOf((b.name || "") + " " + (b.visual || "")),
        voiceWords: words(b.voice)
      };
    });

    var tech = (a.techniques || []).slice(0, 5);
    var camAll = [];
    ratio.forEach(function (r) { r.cam.forEach(function (c) { if (camAll.indexOf(c) < 0) camAll.push(c); }); });
    camAll = tech.concat(camAll.filter(function (c) { return tech.indexOf(c) < 0; })).slice(0, 6);

    var avg = (a.rhythm && a.rhythm.avg) || +(span / n).toFixed(1);
    var voiceWords = 0, voiceLines = 0;
    beats.forEach(function (b) { if (b.voice) { voiceLines++; voiceWords += words(b.voice); } });

    var tpl = {
      id: uid("tpl"),
      at: today(),
      name: [hookLabel(a.hook), tech[0] || camAll[0] || "通用", n + " 拍"].filter(Boolean).join(" · "),
      from: [{ id: a.id, name: a.name, beats: n }],
      beats: n, span: +Number(span).toFixed(1), avg: +Number(avg).toFixed(1),
      hook: a.hook || "", tech: tech,
      pace: paceOf(avg, n),
      ratio: ratio,
      sections: sectionsOf(raw),
      camera: camAll,
      sentences: sentencesOf(allText).slice(0, 5),
      voice: { lines: voiceLines || n, words: voiceLines ? +(voiceWords / voiceLines).toFixed(0) : 0, lang: "de" },
      hasPrice: /preis|€|euro|价格/i.test(raw),
      chars: raw.length
    };
    return normalizeDurations(tpl, tpl.span);
  }

  /* 多份档案 → 合成一个模板：钩子取多数、手法取交集、拍序按平均拍数重采样 */
  function mergeMany(list) {
    var ones = list.map(learnOne).filter(Boolean);
    if (!ones.length) return null;
    if (ones.length === 1) {
      ones[0].from = list.map(function (a) { return { id: a.id, name: a.name, beats: (a.beats || []).length }; });
      return ones[0];
    }

    var counts = {};
    ones.forEach(function (t) { counts[t.hook] = (counts[t.hook] || 0) + 1; });
    var hook = Object.keys(counts).sort(function (x, y) { return counts[y] - counts[x]; })[0] || "";

    var tech = ones[0].tech.filter(function (t) {
      return ones.every(function (o) { return o.tech.indexOf(t) >= 0; });
    });
    if (!tech.length) {
      var pool = [];
      ones.forEach(function (o) { o.tech.forEach(function (t) { if (pool.indexOf(t) < 0) pool.push(t); }); });
      tech = pool.slice(0, 4);
    }

    var target = Math.round(ones.reduce(function (s, o) { return s + o.beats; }, 0) / ones.length);
    var base = ones.slice().sort(function (x, y) { return Math.abs(x.beats - target) - Math.abs(y.beats - target); })[0];
    var ratio = base.ratio.map(function (r, i) {
      var same = ones.map(function (o) {
        var idx = Math.round(i / base.beats * o.beats);
        return o.ratio[Math.min(idx, o.beats - 1)];
      }).filter(Boolean);
      var dur = same.reduce(function (s, x) { return s + x.dur; }, 0) / Math.max(1, same.length);
      var roleCount = {};
      same.forEach(function (x) { roleCount[x.role] = (roleCount[x.role] || 0) + 1; });
      var role = Object.keys(roleCount).sort(function (x, y) { return roleCount[y] - roleCount[x]; })[0] || r.role;
      return {
        i: i + 1, role: role, name: r.name, t0: r.t0, t1: r.t1,
        dur: +dur.toFixed(1), share: r.share, cam: r.cam, voiceWords: r.voiceWords
      };
    });

    var sent = [];
    ones.forEach(function (o) {
      o.sentences.forEach(function (s) {
        if (!sent.some(function (x) { return x.k === s.k; })) sent.push(s);
      });
    });

    var spanAll = ones.reduce(function (s, o) { return s + o.span; }, 0) / ones.length;
    var merged = {
      id: uid("tpl"), at: today(),
      name: [hookLabel(hook), tech[0] || "通用", target + " 拍", "合并 " + ones.length + " 份"].join(" · "),
      from: list.map(function (a) { return { id: a.id, name: a.name, beats: (a.beats || []).length }; }),
      beats: target, span: +spanAll.toFixed(1), avg: +(spanAll / target).toFixed(1),
      hook: hook, tech: tech, pace: paceOf(spanAll / target, target),
      ratio: ratio,
      sections: base.sections,
      camera: ones.reduce(function (acc, o) {
        o.camera.forEach(function (c) { if (acc.indexOf(c) < 0) acc.push(c); });
        return acc;
      }, []).slice(0, 6),
      sentences: sent.slice(0, 5),
      voice: {
        lines: Math.round(ones.reduce(function (s, o) { return s + o.voice.lines; }, 0) / ones.length),
        words: Math.round(ones.reduce(function (s, o) { return s + o.voice.words; }, 0) / ones.length),
        lang: "de"
      },
      hasPrice: ones.some(function (o) { return o.hasPrice; }),
      chars: Math.round(ones.reduce(function (s, o) { return s + o.chars; }, 0) / ones.length)
    };
    merged.span = +spanAll.toFixed(1);
    merged.avg = +(merged.span / merged.beats).toFixed(1);
    return normalizeDurations(merged, merged.span);
  }

  function hookLabel(k) {
    return { result: "结果前置", pain: "痛点开场", question: "提问开场", contrast: "反常识",
      demo: "直接演示", proof: "证据开场" }[k] || "未判定";
  }

  /* ── 模板存取 ───────────────────────────────────────────────────────── */
  function saveTpls() {
    try {
      localStorage.setItem(KEY.tpl, JSON.stringify(S.tpls.slice(0, MAX_TPL)));
    } catch (e) { say("模板保存失败（本机存储满），建议先导出模板库"); }
  }
  function loadTpls() {
    try {
      var arr = JSON.parse(localStorage.getItem(KEY.tpl) || "[]");
      S.tpls = Array.isArray(arr) ? arr.filter(function (t) {
        return t && Array.isArray(t.ratio) && t.ratio.length;
      }).slice(0, MAX_TPL) : [];
    } catch (e) { S.tpls = []; }
    S.tpls.forEach(function (t) { if (S.useTpl[t.id] === undefined) S.useTpl[t.id] = true; });
  }

  function archives() {
    var st = window.PS && window.PS.state;
    return (st && st.bench && st.bench.list) ? st.bench.list : [];
  }

  /* ── 渲染：③ 模板区 ─────────────────────────────────────────────────── */
  function syncArchPick() {
    $$("#pt-archives .pt-chip").forEach(function (el) {
      var on = !!S.pick[el.dataset.tarch];
      el.classList.toggle("on", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
    var n = Object.keys(S.pick).filter(function (k) { return S.pick[k]; }).length;
    var hint = $("#pt-pick-hint");
    if (hint) hint.textContent = n ? "已选 " + n + " 份" : "全选则每份各出一个";
  }

  function renderArchivePick() {
    var box = $("#pt-archives");
    if (!box) return;
    var list = archives();
    var sig = list.map(function (a) { return a.id; }).join(",");
    if (box.dataset.sig === sig && box.querySelector(".pt-chip")) { syncArchPick(); return; }
    box.dataset.sig = sig;
    if (!list.length) {
      box.innerHTML = '<p class="ps-empty">还没有对标档案。先去 ② 区上传或导入反推报告，再回来归纳模板。</p>';
      return;
    }
    box.innerHTML = list.map(function (a) {
      var on = !!S.pick[a.id];
      return '<button type="button" class="pt-chip' + (on ? " on" : "") + '" data-tarch="' + esc(a.id) + '" aria-pressed="' + on + '">' +
        esc(a.name.slice(0, 30)) + ' <em>' + (a.beats || []).length + " 拍</em></button>";
    }).join("");
    var n = Object.keys(S.pick).filter(function (k) { return S.pick[k]; }).length;
    var hint = $("#pt-pick-hint");
    if (hint) hint.textContent = n ? "已选 " + n + " 份" : "全选则每份各出一个模板";
  }

  function skeletonBar(ratio) {
    var colors = { "钩子": "#BA7517", "演示": "#378ADD", "痛点": "#E24B4A", "证据": "#0F6E56",
      "细节": "#7F77DD", "对比": "#D4537E", "收尾": "#888780" };
    return '<div class="pt-bar" title="按拍数时长占比">' + ratio.map(function (r) {
      return '<span style="flex:' + Math.max(1, r.dur) + ';background:' + (colors[r.role] || "#B4B2A9") +
        '" title="' + esc(r.role + " " + r.dur + "s（" + r.share + "%）") + '">' +
        (r.dur >= 1.6 ? esc(r.role) : "") + "</span>";
    }).join("") + "</div>";
  }

  function renderTpls() {
    var box = $("#pt-tpls");
    var cnt = $("#pt-tpl-count");
    if (cnt) cnt.textContent = S.tpls.length + " 个";
    if (!box) return;
    if (!S.tpls.length) {
      box.innerHTML = '<p class="ps-empty">还没有模板。勾选上面的档案 → 点「归纳为模板」，' +
        '系统会把拍序、时长分配、镜头与句式抽成可套用的骨架。</p>';
      renderUsePick();
      return;
    }
    box.innerHTML = S.tpls.map(function (t) {
      var on = !!S.useTpl[t.id];
      return '<article class="pt-tcard' + (on ? " on" : "") + '" data-tplcard="' + esc(t.id) + '">' +
        '<header><button type="button" class="pt-usebtn" data-tuse="' + esc(t.id) + '" aria-pressed="' + on + '" title="生成方案时是否启用">' +
        (on ? "✓" : "+") + "</button>" +
        "<b>" + esc(t.name) + "</b>" +
        '<button type="button" class="pt-del" data-tdel="' + esc(t.id) + '" title="删除模板">✕</button></header>' +
        '<div class="pt-tmeta">' + t.beats + " 拍 · 平均 " + t.avg + "s · " + esc(t.pace) +
        " · " + t.span + "s · 来自 " + t.from.length + " 份档案</div>" +
        skeletonBar(t.ratio) +
        '<div class="pt-tags">' +
        (t.hook ? "<span>" + esc(hookLabel(t.hook)) + "</span>" : "") +
        t.camera.slice(0, 4).map(function (c) { return "<span>" + esc(c) + "</span>"; }).join("") +
        (t.voice.lines ? "<span>" + t.voice.lines + " 句德语 / 均 " + t.voice.words + " 词</span>" : "") +
        "</div>" +
        (t.sentences.length
          ? '<ul class="pt-tsent">' + t.sentences.slice(0, 3).map(function (s) {
            return "<li><b>" + esc(s.k) + "</b>" + esc(s.t) + "</li>";
          }).join("") + "</ul>"
          : '<p class="pt-tnone">未抽到明显句式（文档偏概述），生成时用通用骨架。</p>') +
        '<footer>' +
        (t.sections.length ? '<span class="pt-tsec">' + t.sections.slice(0, 5).map(esc).join(" › ") + "</span>" : "") +
        '<button type="button" class="btn sm" data-tshow="' + esc(t.id) + '">看骨架</button>' +
        "</footer></article>";
    }).join("");
    renderUsePick();
    syncTplUse();
  }

  function syncTplUse() {
    $$("#pt-tpls .pt-tcard").forEach(function (card) {
      var id = card.dataset.tplcard;
      var on = !!S.useTpl[id];
      card.classList.toggle("on", on);
      var b = card.querySelector("[data-tuse]");
      if (b) { b.textContent = on ? "✓" : "+"; b.setAttribute("aria-pressed", on ? "true" : "false"); }
    });
    $$("#pt-picktpl .pt-chip").forEach(function (el) {
      var on = !!S.useTpl[el.dataset.ttpl];
      el.classList.toggle("on", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function renderUsePick() {
    var box = $("#pt-picktpl");
    if (!box) return;
    if (!S.tpls.length) { box.innerHTML = '<span class="pt-tnone">先在 ③ 区归纳模板</span>'; return; }
    box.innerHTML = S.tpls.map(function (t) {
      var on = !!S.useTpl[t.id];
      return '<button type="button" class="pt-chip' + (on ? " on" : "") + '" data-ttpl="' + esc(t.id) + '" aria-pressed="' + on + '">' +
        esc(t.name.slice(0, 22)) + "</button>";
    }).join("");
  }

  function showSkeleton(id) {
    var t = S.tpls.filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    var lines = ["# 模板骨架 · " + t.name, "",
      "- 拍数 " + t.beats + " / 总时长 " + t.span + "s / 平均 " + t.avg + "s / " + t.pace,
      "- 钩子：" + hookLabel(t.hook) + "　手法：" + (t.tech.join("、") || "—"),
      "- 来自：" + t.from.map(function (f) { return f.name + "(" + f.beats + "拍)"; }).join("、"), ""];
    t.ratio.forEach(function (r) {
      lines.push(r.i + ". " + r.t0 + "–" + r.t1 + "s　" + r.role + "　" + r.share + "%　" +
        (r.cam.length ? "[" + r.cam.join("/") + "] " : "") + (r.name || ""));
    });
    if (t.sections.length) lines.push("", "段落序列：" + t.sections.join(" › "));
    if (t.sentences.length) {
      lines.push("", "句式模式：");
      t.sentences.forEach(function (s) { lines.push("- " + s.k + "：" + s.t); });
    }
    copyText(lines.join("\n"), "模板骨架");
  }

  /* ── 产品图：读取 / 缩小 / 识别 ─────────────────────────────────────── */

  function readAsDataURL(file) {
    return new Promise(function (res, rej) {
      var fr = new FileReader();
      fr.onerror = function () { rej(new Error("读取失败")); };
      fr.onload = function () { res(String(fr.result || "")); };
      fr.readAsDataURL(file);
    });
  }

  /* 手机原图常有 4–8MB：缩到最长边 1024 再送模型，省 token 也更快 */
  function shrink(dataUrl, maxSide) {
    return new Promise(function (res) {
      var img = new Image();
      img.onerror = function () { res({ dataUrl: dataUrl, w: 0, h: 0 }); };
      img.onload = function () {
        var w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, (maxSide || 1024) / Math.max(w, h));
        if (scale >= 1) { res({ dataUrl: dataUrl, w: w, h: h }); return; }
        var cv = document.createElement("canvas");
        cv.width = Math.round(w * scale); cv.height = Math.round(h * scale);
        var ctx = cv.getContext("2d");
        ctx.drawImage(img, 0, 0, cv.width, cv.height);
        res({ dataUrl: cv.toDataURL("image/jpeg", 0.86), w: cv.width, h: cv.height });
      };
      img.src = dataUrl;
    });
  }

  /* 主色采样：AI 不可用时的兜底，至少给出「画面里有什么颜色」 */
  function dominantColors(dataUrl, n) {
    return new Promise(function (res) {
      var img = new Image();
      img.onerror = function () { res([]); };
      img.onload = function () {
        var cv = document.createElement("canvas");
        var s = Math.min(1, 96 / Math.max(img.naturalWidth, img.naturalHeight));
        cv.width = Math.max(1, Math.round(img.naturalWidth * s));
        cv.height = Math.max(1, Math.round(img.naturalHeight * s));
        var ctx = cv.getContext("2d");
        ctx.drawImage(img, 0, 0, cv.width, cv.height);
        var data;
        try { data = ctx.getImageData(0, 0, cv.width, cv.height).data; } catch (e) { res([]); return; }
        var buckets = {};
        for (var i = 0; i < data.length; i += 16) {
          var r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
          if (a < 128) continue;
          var key = [Math.round(r / 48), Math.round(g / 48), Math.round(b / 48)].join(",");
          buckets[key] = buckets[key] || { n: 0, r: 0, g: 0, b: 0 };
          buckets[key].n++; buckets[key].r += r; buckets[key].g += g; buckets[key].b += b;
        }
        res(Object.keys(buckets).map(function (k) {
          var b2 = buckets[k];
          return { n: b2.n, hex: rgb2hex(b2.r / b2.n, b2.g / b2.n, b2.b / b2.n) };
        }).sort(function (x, y) { return y.n - x.n; }).slice(0, n || 3).map(function (x) { return x.hex; }));
      };
      img.src = dataUrl;
    });
  }

  function rgb2hex(r, g, b) {
    return "#" + [r, g, b].map(function (v) {
      return Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    }).join("");
  }

  function renderThumbs() {
    var box = $("#pt-thumbs");
    if (!box) return;
    if (!S.prod || !S.prod.images.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = S.prod.images.map(function (im, i) {
      return '<figure class="pt-thumb" data-timg="' + i + '">' +
        '<img alt="' + esc(im.name) + '" src="' + esc(im.thumb || "") + '" />' +
        '<button type="button" data-timgdel="' + i + '" title="移除">✕</button>' +
        '<figcaption>' + esc(im.name.slice(0, 16)) + "</figcaption></figure>";
    }).join("");
  }

  function newProduct() {
    return { id: uid("prod"), at: today(), images: [], recog: null, edited: {}, degraded: false };
  }

  async function addFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    if (!S.prod) S.prod = newProduct();
    var room = MAX_IMG - S.prod.images.length;
    if (room <= 0) { say("最多 " + MAX_IMG + " 张，先移除再添加"); return; }
    var skipped = 0, added = 0;

    for (var i = 0; i < list.length && added < room; i++) {
      var f = list[i];
      if (!/^image\//.test(f.type)) { skipped++; continue; }
      if (f.size > MAX_IMG_BYTES) { skipped++; continue; }
      var raw = await readAsDataURL(f);
      var small = await shrink(raw, 1024);
      var thumb = (await shrink(raw, 320)).dataUrl;
      var key = "prod:" + S.prod.id + ":" + uid("img");
      var b64 = small.dataUrl.split(",")[1] || "";
      S.prod.images.push({
        name: f.name || ("图" + (added + 1)), mime: "image/jpeg", key: key,
        thumb: thumb, b64: b64, w: small.w, h: small.h, size: f.size
      });
      try { await idbPut(key, { name: f.name, mime: "image/jpeg", blob: dataUrlToBlob(small.dataUrl) }); }
      catch (e) { say("图片未能存入本机素材仓，请检查浏览器存储后重新上传"); S.prod.images.pop(); continue; }
      added++;
    }

    renderThumbs();
    saveProd();
    if (skipped) say("跳过 " + skipped + " 个非图片或超过 4MB 的文件");
    setScanNote("idle", "已选 " + S.prod.images.length + " 张，点「识别产品」");
  }

  function dataUrlToBlob(du) {
    try {
      var parts = String(du).split(",");
      var mime = (parts[0].match(/data:([^;]+)/) || [])[1] || "image/jpeg";
      var bin = atob(parts[1] || "");
      var arr = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return new Blob([arr], { type: mime });
    } catch (e) { return new Blob([]); }
  }

  function setScanNote(state, text) {
    S.scanState = state;
    var el = $("#pt-scannote");
    if (!el) return;
    el.className = "pt-scannote " + (state === "ok" ? "is-ok" : state === "degraded" ? "is-warn" : state === "scanning" ? "is-busy" : "");
    el.textContent = text;
  }

  async function scanProduct() {
    if (!S.prod || !S.prod.images.length) { say("先拖入 1–4 张产品图"); return; }
    var imgs = S.prod.images.slice(0, MAX_IMG).map(function (im) {
      return { name: im.name, mime: im.mime, data: im.b64 };
    });
    setScanNote("scanning", "识别中…（首次约 3 秒）");
    var btn = $("#pt-scan");
    if (btn) btn.disabled = true;
    try {
      var r = await fetch("/api/ps/product-scan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ images: imgs, hint: ($("#pt-hint") || {}).value || "" })
      }).then(function (x) { return x.json(); });

      if (r && r.ok && r.product) {
        S.prod.recog = r.product;
        S.prod.model = r.model || "";
        S.prod.degraded = false;
        S.prod.edited = recogToVars(r.product);
        renderProduct();
        saveProd();
        setScanNote("ok", "识别完成 · " + (r.cached ? "命中缓存" : (r.latency_ms || 0) + "ms") + " · " + (r.model || ""));
        return;
      }
      var why = (r && r.reason) || "识别不可用";
      S.prod.degraded = true;
      S.prod.edited = await degradeVars();
      renderProduct();
      saveProd();
      setScanNote("degraded", "已降级：" + why + " · 请手填");
    } catch (e) {
      S.prod.degraded = true;
      S.prod.edited = await degradeVars();
      renderProduct(); saveProd();
      setScanNote("degraded", "接口不可用（服务可能未重启）· 已按主色采样降级，请手填");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* 识别结果 → ① 区变量字段（只映射「识别到的东西」，不做臆造） */
  function recogToVars(p) {
    var selling = (p.selling || []).filter(Boolean);
    /* 产品名只用品类：category 已是「女士运动休闲套装」这种可直接用的名字；
       把 subject（一句画面描述）拼进去会让提示词标题变得又长又像旁白。 */
    var out = {
      product: String(p.category || p.subject || "").slice(0, 40),
      material: p.material && p.material !== "未知" ? p.material : "",
      scene: p.scene || "",
      benefit: selling[0] || "",
      accent: (p.colors || [])[0] || "",
      structure: "",
      flaw: "",
      heroAction: (p.shoot || [])[0] ? "（按识别建议）" + (p.shoot || [])[0].slice(0, 40) : ""
    };
    if (p.audience) out.sounds = "";      // 不为凑字段编内容
    return out;
  }

  async function degradeVars() {
    var first = S.prod.images[0];
    var hex = first && first.thumb ? await dominantColors(first.thumb, 3) : [];
    return {
      product: "", material: "", structure: "", scene: "", benefit: "",
      accent: hex[0] || "",
      flaw: "", heroAction: "", surface: "", surfaceTrace: "", sounds: "",
      colorProbe: hex
    };
  }

  function renderProduct() {
    var box = $("#pt-product");
    if (!box) return;
    var e = (S.prod && S.prod.edited) || {};
    var hasFill = Object.keys(e).some(function (k) { return VAR_HINTS[k] && String(e[k] || "").trim(); });
    if (!S.prod || (!S.prod.images.length && !hasFill)) {
      box.innerHTML = '<p class="ps-empty">还没有产品图。拖 1–4 张进来（正面 + 细节 + 使用场景最有用），再点「识别产品」；' +
        "也可以直接点「识别产品」下方的空字段手填。</p>";
      return;
    }
    var rec = S.prod.recog;
    var rows = [
      ["product", "产品"], ["material", "材质"], ["structure", "结构 / 尺寸"], ["scene", "场景"],
      ["benefit", "收益证据"], ["accent", "主色"]
    ];
    var fields = rows.map(function (r) {
      return '<label class="pt-f"><span>' + esc(r[1]) + "</span>" +
        '<input type="text" data-pfield="' + r[0] + '" value="' + esc(e[r[0]] || "") +
        '" placeholder="' + esc(VAR_HINTS[r[0]] || "") + '" /></label>';
    }).join("");

    var recogBox = "";
    if (rec) {
      recogBox = '<div class="pt-recog">' +
        '<h4>识别结果 <em>' + esc(S.prod.model || "") + "</em></h4>" +
        (rec.subject ? "<p>" + esc(rec.subject) + "</p>" : "") +
        ((rec.colors || []).length ? '<div class="pt-swatches">' + rec.colors.map(function (c) {
          return "<span>" + esc(c) + "</span>";
        }).join("") + "</div>" : "") +
        ((rec.selling || []).length ? '<ul class="pt-sell">' + rec.selling.map(function (s) {
          return "<li>" + esc(s) + "</li>";
        }).join("") + "</ul>" : "") +
        ((rec.shoot || []).length ? '<p class="pt-shoot">拍摄建议：' + esc(rec.shoot.join("；")) + "</p>" : "") +
        (rec.audience ? '<p class="pt-aud">人群：' + esc(rec.audience) + "</p>" : "") +
        "</div>";
    } else if (S.prod.degraded) {
      recogBox = '<div class="pt-recog is-warn"><h4>识别不可用</h4>' +
        '<p>已按图片主色采样给出「主色」' + ((e.colorProbe || []).length ? "（" + e.colorProbe.join(" / ") + "）" : "") +
        "，其余请手填。样本图识别需要 AI；也可直接手填后生成。</p></div>";
    }

    box.innerHTML = recogBox +
      '<div class="pt-fgrid">' + fields + "</div>" +
      '<div class="pt-pactions">' +
      '<button type="button" class="btn sm" id="pt-fill">回填 ① 区变量</button>' +
      '<span class="pt-fhint">回填后 ④ 区 5 段式也能直接用这份产品</span></div>';
  }

  function fillVars() {
    if (!S.prod || !S.prod.edited) { say("先识别或手填产品信息"); return; }
    if (!window.PS || !window.PS.applyVars) { say("① 区尚未就绪，请刷新页面"); return; }
    var patch = {};
    Object.keys(S.prod.edited).forEach(function (k) {
      if (VAR_HINTS[k] && S.prod.edited[k]) patch[k] = S.prod.edited[k];
    });
    if (!Object.keys(patch).length) { say("没有可回填的字段"); return; }
    window.PS.applyVars(patch);
    say("已回填 " + Object.keys(patch).length + " 个字段到 ① 区");
  }

  function saveProd() {
    if (S.prod && S.prod._workflowOnly) return; // 新工作流的产品只由项目仓持久化，避免覆盖旧迁移源。
    try {
      localStorage.setItem(KEY.prod, JSON.stringify(S.prod ? {
        id: S.prod.id, at: S.prod.at, model: S.prod.model || "",
        degraded: !!S.prod.degraded, recog: S.prod.recog, edited: S.prod.edited || {},
        images: (S.prod.images || []).map(function (im) {
          return { name: im.name, mime: im.mime, key: im.key, thumb: im.thumb, w: im.w, h: im.h };
        })
      } : null));
    } catch (e) { say("产品信息保存失败（本机存储满）"); }
  }

  function loadProd() {
    try {
      var d = JSON.parse(localStorage.getItem(KEY.prod) || "null");
      if (!d || !d.id) return;
      d.images = (d.images || []).map(function (im) { return Object.assign({}, im); });
      S.prod = d;
      renderThumbs(); renderProduct();
      if (d.images && d.images.length) {
        setScanNote(d.degraded ? "degraded" : (d.recog ? "ok" : "idle"),
          d.recog ? "已识别 · " + (d.model || "") + "（图需重传才能再识别）" : "已选 " + d.images.length + " 张");
      }
    } catch (e) { /* 坏存储按空处理 */ }
  }

  /* ── 德语口播 ───────────────────────────────────────────────────────── */

  function deNoun(prod) {
    var hay = [prod.category, prod.subject, prod.name, prod.material].filter(Boolean).join(" ");
    for (var i = 0; i < DE_NOUN.length; i++) if (DE_NOUN[i][0].test(hay)) return DE_NOUN[i][1];
    return "das Set";
  }

  function deFeats(prod) {
    var hay = [prod.category, prod.subject, prod.material, (prod.colors || []).join(" "),
      (prod.selling || []).join(" "), prod.name].filter(Boolean).join(" ");
    var out = [];
    DE_FEAT.forEach(function (f) { if (f[0] && hay.indexOf(f[0]) >= 0 && out.indexOf(f[1]) < 0) out.push(f[1]); });
    /* 颜色词常来自识别结果，再扫一遍 selling 里的中文特征 */
    return out.slice(0, 4);
  }

  /* 口播：优先用 ① 区已维护的德语（那是用户认可的表达），不够再用合规模板补 */
  function voiceLines(prod, tpl) {
    var need = Math.max(3, Math.min(5, tpl.voice.lines || 5));
    var base = (window.PS && window.PS.state && window.PS.state.voice) ? window.PS.state.voice.slice() : [];
    var currentProduct = String((prod.edited && prod.edited.product) || prod.category || prod.name || "").trim();
    var originalProduct = window.PS && window.PS.state ? String(window.PS.state.v.product || "").trim() : "";
    base = base.filter(function (x) {
      return String(x || "").trim() && !(currentProduct !== originalProduct && window.PS && window.PS.isPresetVoice && window.PS.isPresetVoice(x));
    });
    var out = base.slice(0, need);

    if (out.length < need) {
      var noun = deNoun(prod);
      var f = deFeats(prod);
      var auto = [
        "Schau mal, " + noun + ".",
        (f[0] ? f[0] + (f[1] ? " und " + f[1] : "") + " – so wie hier." : "So sieht es aus, wenn es ankommt."),
        "Kein Schnickschnack, sondern " + (f[2] || "genau das, was du siehst") + ".",
        "Hier siehst du die Details aus der Nähe.",
        "Link in der Beschreibung."
      ];
      for (var i = 0; out.length < need && i < auto.length; i++) {
        if (out.indexOf(auto[i]) < 0) out.push(auto[i]);
      }
      while (out.length < need) out.push("Link in der Beschreibung.");
    }
    return out.slice(0, need);
  }

  /* ── 方案生成 ───────────────────────────────────────────────────────── */

  var VARIANTS = [
    { k: "A", name: "结果前置 · 快切", open: "result", hint: "第一秒就给结果，把「怎么做」留给后面" },
    { k: "B", name: "痛点直击 · 细节取证", open: "pain", hint: "先说糟糕现状，再用细节镜头证明" },
    { k: "C", name: "对比验证 · 收尾 CTA", open: "contrast", hint: "前后对照，末尾一句行动指引" }
  ];

  function pickOpen(role, v, e, i, n) {
    if (i === 0) {
      if (v.open === "pain") return "画面先给糟糕现状：" + (e.surfaceTrace || e.flaw || "使用前的一片狼藉") + "，产品不在画面里。";
      if (v.open === "contrast") return "左右分屏：左侧是" + (e.surfaceTrace || e.flaw || "使用前") + "，右侧是" + (e.benefit || "使用后的干净状态") + "。";
      return "画面直接给结果：" + (e.benefit || "产品已经在用，效果就在眼前") + "，产品入画但不居中。";
    }
    return "";
  }

  function shotText(role, r, e, i, n) {
    var cam = r.cam.length ? r.cam.join(" / ") : (i === 0 ? "固定机位，微微推近" : "手持微晃，跟住动作");
    if (role === "钩子") return cam + "；不到 1 秒就交代清楚，不做片头";
    if (role === "演示") return cam + "；一镜完成一个完整动作，不切碎";
    if (role === "细节") return cam + "；贴到 10cm 内，让纹理和边缘占满画面";
    if (role === "证据") return cam + "；数字与实物同框，不要只拍字";
    if (role === "对比") return cam + "；两个状态用同一机位同一构图，方便观众对照";
    if (role === "收尾") return cam + "；动作收干净，画面留 0.3 秒静止";
    return cam;
  }

  function actionText(role, e, i, n) {
    if (role === "钩子") return e.heroAction || "主体动作直接开始，不铺垫";
    if (role === "演示") return (e.heroAction || "完整做一遍核心动作") + "；" + (e.surface ? "接触面是" + e.surface + "，" : "");
    if (role === "细节") return (e.material || "材质") + "在侧光下显出纹路；" + (e.accent ? "点缀色是" + e.accent + "。" : "");
    if (role === "证据") return (e.benefit || "效果给足") + "；只呈现镜头里真的看得到的变化";
    if (role === "对比") return "同机位同构图，先" + (e.surfaceTrace || "用前状态") + "，再" + (e.benefit || "用后状态");
    if (role === "收尾") return "产品摆正、动作停住；" + (e.flaw ? "保留" + e.flaw + "这类真实痕迹，不要修得太完美。" : "画面保持真实，不做美化。");
    return e.heroAction || "按模板节奏推进";
  }

  function buildPlan(tpl, prod, vi) {
    var v = VARIANTS[vi % VARIANTS.length];
    var e = prod.edited || {};
    var pname = e.product || "（未填产品，请回 ① 区补）";
    var voice = voiceLines(prod, tpl);
    var n = tpl.ratio.length;
    var lines = [];
    var ctx = [pname, e.material, e.scene, (prod.colors || []).join(" ")].filter(Boolean).join(" ");

    lines.push("# " + pname + " · " + tpl.name + " · " + v.k);
    lines.push("");
    lines.push("> 策略 " + v.name + "：" + v.hint + "　|　约 " + tpl.span + "s / " + tpl.beats + " 拍 / " +
      tpl.pace + "　|　模板来自 " + tpl.from.map(function (f) { return f.name; }).join("、"));
    lines.push("");
    lines.push("## 一、整体设定");
    lines.push("");
    lines.push("- 画幅：9:16（1080×1920），24fps；单段超过 10s 就按下面的分镜切段生成");
    lines.push("- 产品：" + pname + (e.structure ? "（" + e.structure + "）" : ""));
    lines.push("- 材质/外观：" + (e.material || "按参考图") + (e.accent ? "；主色 " + e.accent : ""));
    lines.push("- 环境：" + (e.scene || "按参考图"));
    lines.push("- 调度：" + (tpl.tech.join(" / ") || tpl.camera.join(" / ") || "固定机位 + 手持补拍") + "；" + tpl.pace);
    lines.push("");
    lines.push("## 二、分镜（时长按模板分配，合计 " + tpl.span + "s）");
    lines.push("");

    var timed = ratioWithTime(tpl);
    var cursor = 0;
    tpl.ratio.forEach(function (r, i) {
      var t0 = timed[i] ? timed[i].t0 : +cursor.toFixed(1);
      var t1 = timed[i] ? timed[i].t1 : +(cursor + r.dur).toFixed(1);
      cursor = t1;
      var role = r.role;
      var viIdx = Math.min(voice.length - 1, Math.round(i / Math.max(1, n - 1) * (voice.length - 1)));
      lines.push("### " + (i + 1) + "｜" + t0 + "–" + t1 + "s｜" + role + (r.name ? "｜" + r.name : ""));
      lines.push("");
      var open = pickOpen(role, v, e, i, n);
      lines.push("画面：" + (open || actionText(role, e, i, n)));
      if (open) lines.push("动作：" + actionText(role, e, i, n));
      lines.push("镜头：" + shotText(role, r, e, i, n));
      lines.push("口播（德）：\"" + (voice[viIdx] || "") + "\"");
      lines.push("");
    });

    lines.push("## 三、德语口播（连续念，与口型对轴）");
    lines.push("");
    voice.forEach(function (l, i) { lines.push((i + 1) + ". \"" + l + "\""); });
    lines.push("");
    lines.push("## 四、硬性约束");
    lines.push("");
    lines.push("- 产品外观、层数/结构、配色必须与参考图一致；不添加图里没有的配件");
    lines.push("- 不出现品牌 logo、水印、回字样；不口播价格与折扣（德国站价格需合规表述）");
    lines.push("- 德语不用绝对化/最高级（beste、garantiert、100%、Nr.1），改用可验证的描述");
    lines.push("- 不做前后夸张对比；画面里的变化必须是这个动作真能造成的");
    if (e.flaw) lines.push("- 保留真实痕迹（" + e.flaw + "），不要修成完美样品");
    lines.push("");
    lines.push("## 五、模板依据（换产品时可复用）");
    lines.push("");
    lines.push("- 拍序：" + tpl.ratio.map(function (r) { return r.role; }).join(" → "));
    if (tpl.sentences.length) {
      lines.push("- 句式：" + tpl.sentences.map(function (x) { return x.k; }).join("、"));
    }
    if (tpl.sections.length) lines.push("- 原文档段落：" + tpl.sections.join(" › "));

    return {
      i: vi + 1, key: v.k, variant: v.name, text: lines.join("\n"),
      chars: lines.join("\n").length, ctx: ctx
    };
  }

  function generatePlans() {
    var tpls = S.tpls.filter(function (t) { return S.useTpl[t.id]; });
    if (!tpls.length) { say("先在 ③ 区勾选至少一个模板（点模板卡左上角的 +）"); return; }
    var e = (S.prod && S.prod.edited) || {};
    if (!e.product) {
      say(S.prod && S.prod.images.length ? "产品名还空着：点「识别产品」或直接手填" : "先上传产品图，或直接手填产品名");
      return;
    }

    var per = Math.max(1, Math.min(3, Number(S.per) || 3));
    S.plans = tpls.map(function (t) {
      var items = [];
      for (var i = 0; i < per; i++) items.push(buildPlan(t, S.prod, i));
      return { tplId: t.id, tplName: t.name, tpl: t, items: items };
    });
    renderPlans();
    say("已生成 " + S.plans.reduce(function (a, g) { return a + g.items.length; }, 0) + " 份方案 · " + S.plans.length + " 个模板");
  }

  function renderPlans() {
    var box = $("#pt-plans");
    var cnt = $("#pt-plan-count");
    var total = S.plans.reduce(function (a, g) { return a + g.items.length; }, 0);
    if (cnt) cnt.textContent = total + " 份";
    if (!box) return;
    if (!S.plans.length) {
      box.innerHTML = '<p class="ps-empty">还没有方案。上传产品图 → 识别（或手填）→ 勾选模板 → 点「生成方案」，' +
        '结果会按模板分组排在这里，方便横向对比。</p>';
      return;
    }
    box.innerHTML = S.plans.map(function (g) {
      return '<section class="pt-group" data-pgroup="' + esc(g.tplId) + '">' +
        '<header><b>' + esc(g.tplName) + "</b>" +
        '<span class="pt-gmeta">' + g.tpl.beats + " 拍 · " + g.tpl.pace + " · " + g.tpl.span + "s · " + g.items.length + " 份</span>" +
        '<div class="pt-gactions">' +
        '<button type="button" class="btn sm primary" data-psend="' + esc(g.tplId) + '"' +
        ' title="把这个模板 + 当前产品接入 ⑤ 做六角度变异">→ ⑤ 变异</button>' +
        '<button type="button" class="btn sm" data-pgroupcopy="' + esc(g.tplId) + '">导出该组</button>' +
        '<button type="button" class="btn sm" data-pgroupregen="' + esc(g.tplId) + '">重生成</button>' +
        "</div></header>" +
        skeletonBar(g.tpl.ratio) +
        '<div class="pt-cards">' + g.items.map(function (it) {
          return '<article class="pt-plan" data-pitem="' + esc(g.tplId) + ":" + it.i + '">' +
            '<header><b>V0' + it.i + "</b><em>" + esc(it.variant) + "</em>" +
            '<button type="button" class="btn sm" data-pcopy="' + esc(g.tplId) + ":" + it.i + '">复制</button></header>' +
            '<div class="pt-pmeta">' + g.tpl.beats + " 拍 · " + g.tpl.span + "s · " + it.chars + " 字 · " + it.key + "</div>" +
            '<pre class="pt-pre is-clip">' + esc(it.text) + "</pre>" +
            '<footer><button type="button" class="pt-fold" data-pfold="' + esc(g.tplId) + ":" + it.i + '">展开全文</button></footer>' +
            "</article>";
        }).join("") + "</div></section>";
    }).join("");
    renderKPI();
  }

  function renderKPI() {
    var box = $("#pt-kpi");
    if (!box) return;
    var total = S.plans.reduce(function (a, g) { return a + g.items.length; }, 0);
    var e = (S.prod && S.prod.edited) || {};
    var cells = [
      { cap: "模板", v: S.tpls.length, u: "个", note: S.tpls.length ? "来自 " + S.tpls.reduce(function (a, t) { return a + t.from.length; }, 0) + " 份档案" : "先去 ③ 区归纳" },
      { cap: "产品图", v: (S.prod && S.prod.images.length) || 0, u: "张", note: S.prod && S.prod.recog ? "已识别" : "待识别" },
      { cap: "方案", v: total, u: "份", note: total ? S.plans.length + " 个模板分组" : "点「生成方案」" }
    ];
    box.innerHTML = cells.map(function (c) {
      return '<div class="kpi"><div class="kpi-l">' + esc(c.cap) + '</div>' +
        '<div class="kpi-v">' + esc(c.v) + '<span class="u">' + esc(c.u) + "</span></div>" +
        '<div class="kpi-cap">' + esc(c.note) + "</div></div>";
    }).join("");
  }

  function planText(gid, i) {
    var g = S.plans.filter(function (x) { return x.tplId === gid; })[0];
    if (!g) return "";
    var it = g.items.filter(function (x) { return x.i === i; })[0];
    return it ? it.text : "";
  }

  function groupMarkdown(g) {
    return ["# " + (S.prod.edited.product || "方案") + " · " + g.tplName, ""]
      .concat(g.items.map(function (it) { return it.text; })).join("\n\n---\n\n");
  }

  /* ══════════ ④ → ⑤ 衔接 ══════════
     把「当前产品 + 选中的模板」打包成一份桥接对象交给 ⑤：
     拍数、秒点、需求语义都跟模板走，产品与参考图数量也一并带过去。 */
  function ratioWithTime(tpl) {
    var normalized = window.PS.normalizeBridge({product:"校验",ratio:tpl.ratio,span:tpl.span});
    return normalized ? normalized.ratio : [];
  }

  function sendToVariants(tplId) {
    if (!window.PS || !window.PS.setBridge) { say("⑤ 区还没就绪，刷新页面再试"); return; }

    var tpl = S.tpls.filter(function (t) { return t.id === tplId; })[0];
    if (!tpl) {
      var g = S.plans.filter(function (x) { return x.tplId === tplId; })[0];
      tpl = g && g.tpl;
    }
    if (!tpl) { say("模板不存在，先重新归纳"); return; }

    var e = (S.prod && S.prod.edited) || {};
    if (!String(e.product || "").trim()) { say("产品名还空着：先点「识别产品」或手填，再接入 ⑤"); return; }

    var ratio = ratioWithTime(tpl);
    if (!ratio.length) { say("模板拍序或时长无效，请重新归纳后接入"); return; }
    var span = ratio.length ? +ratio[ratio.length - 1].t1.toFixed(1) : tpl.span;
    var rec = (S.prod && S.prod.recog) || {};

    var br = {
      at: new Date().toISOString().slice(0, 16).replace("T", " "),
      prodId: (S.prod && S.prod.id) || "",
      product: String(e.product || "").slice(0, 60),
      material: e.material || "", structure: e.structure || "",
      scene: e.scene || "", benefit: e.benefit || "", accent: e.accent || "",
      colors: rec.colors || [], selling: rec.selling || [],
      shoot: rec.shoot || [], audience: rec.audience || "", category: rec.category || "",
      images: ((S.prod && S.prod.images) || []).map(function (im) {
        return { name: im.name, key: im.key };
      }),
      tplId: tpl.id, tplName: tpl.name,
      beats: ratio.length || tpl.beats, span: span
    };
    if (ratio.length) br.ratio = ratio;

    if (!window.PS.setBridge(br)) return;
    var sec = document.getElementById("ps-sec-variant");
    if (sec && sec.scrollIntoView) sec.scrollIntoView({ behavior: "smooth", block: "start" });
    say("已接入 ⑤：" + br.product + " · " + tpl.name + "（" + br.beats + " 拍，点「生成变异提示词」）");
  }

  function allMarkdown() {
    var head = ["# 批量方案 · " + ((S.prod && S.prod.edited && S.prod.edited.product) || "未命名产品"),
      "", "- 生成时间：" + new Date().toLocaleString("zh-CN"),
      "- 模板数：" + S.plans.length + "　方案数：" + S.plans.reduce(function (a, g) { return a + g.items.length; }, 0), ""];
    return head.join("\n") + "\n" + S.plans.map(function (g) { return groupMarkdown(g); }).join("\n\n---\n\n");
  }

  /* ── 事件 ───────────────────────────────────────────────────────────── */

  function learnFrom(list) {
    var picked = list.filter(function (a) { return S.pick[a.id]; });
    if (!picked.length) picked = list.slice(0, 1);          // 没勾就取最新一份，别让按钮点了没反应
    if (!picked.length) { say("还没有对标档案"); return; }

    var tpl = mergeMany(picked);
    if (!tpl) { say("没解析出分镜，换一份档案试试"); return; }

    var dup = S.tpls.filter(function (t) {
      return t.name === tpl.name && t.beats === tpl.beats &&
        t.from.length === tpl.from.length &&
        t.from.every(function (f, i) { return tpl.from[i] && f.id === tpl.from[i].id; });
    })[0];
    if (dup) { S.tpls = S.tpls.filter(function (t) { return t.id !== dup.id; }); }

    S.tpls.unshift(tpl);
    S.tpls = S.tpls.slice(0, MAX_TPL);
    S.useTpl[tpl.id] = true;
    saveTpls();
    renderTpls();
    renderKPI();
    say("已归纳模板：" + tpl.name);
  }

  function bind() {
    var root = $("#view-prompt");
    if (!root || root.dataset.ptBound === "1") return;
    root.dataset.ptBound = "1";

    root.addEventListener("click", function (e) {
      var el;
      if ((el = e.target.closest("[data-tarch]"))) {
        var id = el.dataset.tarch;
        S.pick[id] = !S.pick[id];
        syncArchPick();
        return;
      }
      if ((el = e.target.closest("[data-tuse]"))) {
        var tid = el.dataset.tuse;
        S.useTpl[tid] = !S.useTpl[tid];
        syncTplUse();
        return;
      }
      if ((el = e.target.closest("[data-ttpl]"))) {
        var t2 = el.dataset.ttpl;
        S.useTpl[t2] = !S.useTpl[t2];
        syncTplUse();
        return;
      }
      if ((el = e.target.closest("[data-tdel]"))) {
        S.tpls = S.tpls.filter(function (t) { return t.id !== el.dataset.tdel; });
        saveTpls(); renderTpls(); renderKPI(); say("已删除模板");
        return;
      }
      if ((el = e.target.closest("[data-tshow]"))) { showSkeleton(el.dataset.tshow); return; }
      if ((el = e.target.closest("[data-timgdel]"))) {
        var ix = Number(el.dataset.timgdel);
        if (S.prod && S.prod.images[ix]) {
          idbDel(S.prod.images[ix].key);
          S.prod.images.splice(ix, 1);
          renderThumbs(); saveProd();
          if (!S.prod.images.length) { S.prod.recog = null; S.prod.edited = {}; renderProduct(); setScanNote("idle", "未识别"); }
        }
        return;
      }
      if (e.target.closest("#pt-learn")) { learnFrom(archives()); return; }
      if (e.target.closest("#pt-learn-all")) {
        S.pick = {};
        archives().forEach(function (a) { S.pick[a.id] = true; });
        renderArchivePick(); learnFrom(archives());
        return;
      }
      if (e.target.closest("#pt-tpl-export")) {
        if (!S.tpls.length) { say("还没有模板"); return; }
        var md = ["# 模板库 · " + today(), ""].concat(S.tpls.map(function (t) {
          return ["## " + t.name, "",
            "- " + t.beats + " 拍 / " + t.span + "s / 平均 " + t.avg + "s / " + t.pace,
            "- 钩子：" + hookLabel(t.hook) + "　手法：" + (t.tech.join("、") || "—"),
            "- 拍序：" + t.ratio.map(function (r) { return r.role + "(" + r.dur + "s)"; }).join(" → "),
            t.sentences.length ? "- 句式：" + t.sentences.map(function (x) { return x.k; }).join("、") : "",
            ""].filter(Boolean).join("\n");
        })).join("\n");
        downloadText("模板库-" + today() + ".md", md);
        say("已导出 " + S.tpls.length + " 个模板");
        return;
      }
      if (e.target.closest("#pt-scan")) { scanProduct(); return; }
      if (e.target.closest("#pt-fill")) { fillVars(); return; }
      if (e.target.closest("#pt-gen")) { generatePlans(); return; }
      if (e.target.closest("#pt-copy-all")) {
        if (!S.plans.length) { say("还没有方案"); return; }
        copyText(allMarkdown(), "全部方案");
        return;
      }
      if (e.target.closest("#pt-export-all")) {
        if (!S.plans.length) { say("还没有方案"); return; }
        downloadText("批量方案-" + ((S.prod.edited && S.prod.edited.product) || "产品") + "-" + today() + ".md", allMarkdown());
        say("已导出全部方案");
        return;
      }
      if ((el = e.target.closest("[data-pcopy]"))) {
        var parts = String(el.dataset.pcopy).split(":");
        copyText(planText(parts[0], Number(parts[1])), "V0" + parts[1]);
        return;
      }
      if ((el = e.target.closest("[data-psend]"))) { sendToVariants(el.dataset.psend); return; }
      if ((el = e.target.closest("[data-pgroupcopy]"))) {
        var g = S.plans.filter(function (x) { return x.tplId === el.dataset.pgroupcopy; })[0];
        if (!g) return;
        downloadText("方案-" + g.tplName.replace(/[\\/:*?"<>|]/g, "-") + "-" + today() + ".md", groupMarkdown(g));
        say("已导出该组 " + g.items.length + " 份");
        return;
      }
      if ((el = e.target.closest("[data-pgroupregen]"))) {
        var gid = el.dataset.pgroupregen;
        var tpl = S.tpls.filter(function (t) { return t.id === gid; })[0];
        if (!tpl || !S.prod || !S.prod.edited.product) { say("模板或产品信息不完整"); return; }
        var items = [];
        for (var i = 0; i < Math.max(1, Math.min(3, Number(S.per) || 3)); i++) items.push(buildPlan(tpl, S.prod, i));
        S.plans = S.plans.map(function (x) { return x.tplId === gid ? { tplId: gid, tplName: tpl.name, tpl: tpl, items: items } : x; });
        renderPlans();
        say("已重新生成 " + items.length + " 份");
        return;
      }
      if ((el = e.target.closest("[data-pfold]"))) {
        var card = el.closest(".pt-plan");
        var pre = card && card.querySelector(".pt-pre");
        if (!pre) return;
        var clipped = pre.classList.toggle("is-clip");
        el.textContent = clipped ? "展开全文" : "收起";
        return;
      }
    });

    root.addEventListener("input", function (e) {
      var el = e.target;
      if (el.dataset && el.dataset.pfield) {
        if (!S.prod) S.prod = newProduct();
        S.prod.edited = S.prod.edited || {};
        S.prod.edited[el.dataset.pfield] = el.value;
        saveProd();
        return;
      }
    });

    root.addEventListener("change", function (e) {
      if (e.target.id === "pt-file") {
        addFiles(e.target.files);
        e.target.value = "";
        return;
      }
      if (e.target.id === "pt-per") { S.per = Number(e.target.value) || 3; }
    });

    /* 拖拽：只在 ④ 区的投放框上生效，避免和 ② 区的 md 拖拽打架 */
    var drop = $("#pt-drop");
    if (drop) {
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
          e.preventDefault(); e.stopPropagation();
          drop.classList.add("is-over");
        });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("is-over"); });
      });
      drop.addEventListener("drop", function (e) {
        e.stopPropagation();
        if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
      });
      drop.addEventListener("click", function () {
        var f = $("#pt-file");
        if (f) f.click();
      });
    }
  }

  /* ── 入口 ───────────────────────────────────────────────────────────── */

  function load() {
    if (!$("#view-prompt")) return;
    bind();
    loadTpls();
    loadProd();
    renderArchivePick();
    renderTpls();
    renderUsePick();
    renderProduct();
    renderPlans();
    renderKPI();
    var per = $("#pt-per");
    if (per) per.value = String(S.per);
    var st = $("#pt-archives");
    if (st && !st.dataset.bound) st.dataset.bound = "1";
  }

  /* ★ 自注册：app.js 末尾直接调 boot()，深链 #prompt 时那次 switchView 早于本脚本，
     PS_load 里的 PT_load 分支会落空（表现为「区域在、但按钮点了没反应」）。
     这里补一次：视图已激活就自己初始化。切视图时仍由 PS_load 兜底。 */
  function autoStart() {
    var v = document.getElementById("view-prompt");
    if (v && v.classList.contains("active")) load();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoStart);
  else autoStart();

  window.PT_load = load;
  window.PT = window.PT || {};
  window.PT.load = load;
  window.PT.learn = function () { learnFrom(archives()); };
  window.PT.generate = generatePlans;
  window.PT.syncArchives = renderArchivePick;   // ② 区归档/删除后由 prompt_studio.js 回调
  window.PT.sendToVariants = sendToVariants;     // ④ → ⑤ 衔接
  window.PT.buildPlan = buildPlan;
  window.PT.scanProduct = scanProduct;
  window.PT.addFiles = addFiles;
  window.PT.getAsset = idbGet;
  window.PT.replaceProduct = function (product) { S.prod = product; S.prod._workflowOnly = true; };
  window.PT.setProduct = function (edited, images) {
    if (!S.prod) S.prod = newProduct();
    if (edited) S.prod.edited = Object.assign(S.prod.edited || {}, edited);
    if (images) S.prod.images = images;
    renderThumbs(); renderProduct(); saveProd();
  };
  window.PT.voiceLines = voiceLines;
  window.PT.state = S;
  window.PT.key = KEY;

  window.PT.learnOne = learnOne;
  window.PT.mergeMany = mergeMany;
  window.PT.state = S;
  window.PT.key = KEY;

  window.PT.learnOne = learnOne;
  window.PT.mergeMany = mergeMany;
  window.PT.state = S;
  window.PT.key = KEY;
})();
