/* ══════════════════════════════════════════════════════════════════
   提示词优化 · 独立板块（2026-10-26）
   ──────────────────────────────────────────────────────────────────
   来源：D:\ai\planA\2026-10-26-11-00-29\de-tiktok-script-studio.html
   移植范围（纯前端，零后端改动）：
     ① 产品变量 11 字段 + 5 句德语口播 + 5 段式提示词引擎（A 整段 / B 拆段）
     ② 对标档案：.md/.txt 上传与粘贴 → 拍数/卖点/手法/钩子/节奏解析 → 可复用规律
     ③ 六叙事角度变异提示词生成（继承对标骨架，换成本产品动作）+ 复制/导出
     ④ 德国合规风险词扫描 + 10 项自检
   与工作台的衔接（复用既有能力，不另起一套）：
     · /api/reverse/list + /tk_reverse/<vid>/{report,manifest}.json —— 反推报告一键入档
     · api() / toast() / .panel/.btn/.kpi 设计令牌 —— 沿用工作台交互与视觉
   数据：localStorage（本机浏览器），键前缀 wb.po.*，与其它板块互不影响。
   ══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.PS) return;

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };
  var apiFetch = function (url, opts) {
    return (typeof window.api === "function") ? window.api(url, opts) : fetch(url, opts).then(function (r) {
      if (!r.ok) throw new Error(r.status + " " + url);
      return r.json();
    });
  };
  var say = function (msg) {
    if (typeof window.toast === "function") window.toast(msg); else console.log("[PS]", msg);
  };

  /* ───────────── 常量 · 产品预设 ───────────── */
  var PRESETS = {
    wipes: {
      label: "清洁湿巾",
      v: {
        product: "一次性清洁湿巾（3er-Pack）",
        material: "水刺无纺布，表面细密压纹",
        structure: "单张 20×20cm，3er-Pack 塑封",
        scene: "柏林老公寓厨房，白色瓷砖背景",
        surface: "浅灰人造石台面",
        surfaceTrace: "水渍圈、砧板留下的浅划痕、一点面包屑",
        heroAction: "抽出一张湿巾，横向擦过台面",
        benefit: "油渍被推成一团，布面没有破",
        accent: "橄榄绿包装",
        flaw: "包装角轻微压褶",
        sounds: "抽纸的摩擦声、水龙头关紧的滴水、楼道远处关门声"
      },
      voice: [
        "Nach dem Kochen sieht die Arbeitsfläche immer so aus.",
        "Ein Tuch raus, einmal drüber, fertig.",
        "Reißfest, fusselt kaum, der Geruch bleibt dezent.",
        "Für Herd, Arbeitsfläche und Türgriffe.",
        "Im 3er-Pack – Nachschub für die nächsten Wochen."
      ]
    },
    shelf: {
      label: "金属储物架",
      v: {
        product: "三层金属储物架（92cm）",
        material: "哑光粉末喷涂钢架，层板带细密冲孔",
        structure: "高 92cm，三层，每层承重 5kg，四脚可调",
        scene: "柏林 45 平米小公寓门厅，米色墙面",
        surface: "浅灰亚麻地垫",
        surfaceTrace: "鞋底带进的细沙、墙角一点踢痕",
        heroAction: "把层板卡进立柱，再压下卡扣",
        benefit: "卡扣落位时层板纹丝不动，手指推不动",
        accent: "哑光黑立柱",
        flaw: "立柱底端有一道搬运划痕",
        sounds: "金属卡扣咔哒落位、鞋底摩擦地垫、楼道门吸合上"
      },
      voice: [
        "Kleiner Flur, viel Zeug – jeden Tag dasselbe.",
        "Regal raus, zwei Handgriffe, steht.",
        "Drei Ebenen, jede hält fünf Kilo.",
        "Für Schuhe, Taschen und die Post.",
        "92 Zentimeter hoch – passt in die Nische."
      ]
    },
    bedding: {
      label: "床品四件套",
      v: {
        product: "棉质床品四件套（2 Bezüge）",
        material: "水洗棉，200 针密织，表面细微绒感",
        structure: "一条被套 200×200cm，一个枕套 80×80cm",
        scene: "带斜顶的柏林卧室，晨光从右侧窗进来",
        surface: "浅灰木质床架",
        surfaceTrace: "床单上一道未抚平的褶、枕边一点毛球",
        heroAction: "抓住被套两角向上抖开，让它自己落下",
        benefit: "落下时布料鼓起又缓缓贴住床垫，接缝笔直",
        accent: "燕麦色",
        flaw: "被套内角有一处线头未剪",
        sounds: "布料抖开的破风声、落在床垫上的闷响、窗外远处电车"
      },
      voice: [
        "Im Winter wache ich jede Nacht auf.",
        "Bezug drauf, einmal schütteln, fertig.",
        "Baumwolle, 200er Dichte, weich gewaschen.",
        "Für Doppelbett, Gästebett und Sofa.",
        "Zwei Bezüge im Set – heute kein Waschtag."
      ]
    },
    trap: {
      label: "果蝇诱捕器",
      v: {
        product: "果蝇诱捕器（6 Stück）",
        material: "半透明 PP 罩体 + 涂胶内壁",
        structure: "6 只装，单只直径 6cm，含诱饵片",
        scene: "柏林厨房窗台，旁边一盘熟透的香蕉",
        surface: "白色窗台石面",
        surfaceTrace: "窗台上一点水渍、果盘留下的浅色印",
        heroAction: "撕开背胶，把小罩体按在果盘旁边",
        benefit: "罩体内壁可见已粘住的细小虫体，边缘胶层不溢",
        accent: "透明罩体",
        flaw: "罩口边缘有一点注塑毛刺",
        sounds: "背胶撕开的细响、罩体按下的轻微吸附声、窗外鸟叫"
      },
      voice: [
        "Im Sommer summt es wieder über dem Obst.",
        "Aufstellen, hinstellen, in Ruhe lassen.",
        "Der Lockstoff zieht sie rein, die Klebefläche hält.",
        "Für Küche, Balkon und Mülltonne.",
        "Sechs Stück im Set – reicht für die Saison."
      ]
    },
    apparel: {
      label: "针织外套",
      v: {
        product: "粗针针织外套",
        material: "棉羊毛混纺，5 针粗棒针，罗纹领口",
        structure: "落肩版型，前襟四颗角制纽扣，两侧贴袋",
        scene: "柏林街角咖啡店门口，上午斜光",
        surface: "灰色水泥台阶",
        surfaceTrace: "台阶上一点落叶碎屑、鞋印",
        heroAction: "把外套从肩上拉正，袖口往上推两折",
        benefit: "推袖时罗纹回弹，袖口停在手腕上不滑落",
        accent: "燕麦米色",
        flaw: "左袖有一处轻微起球",
        sounds: "针织摩擦声、拉链外的纽扣磕碰、街角自行车铃"
      },
      voice: [
        "Morgens zehn Grad, mittags zwanzig.",
        "Jacke drüber, Ärmel hoch, fertig.",
        "Baumwollmix, Rippstrick, hält die Form.",
        "Für Büro, Bahn und Abend draußen.",
        "Vier Farben – eine Größe größer für oversized."
      ]
    }
  };

  var FIELDS = [
    { k: "product", label: "产品名", ph: "产品全称" },
    { k: "material", label: "材质", ph: "先材质后工艺" },
    { k: "structure", label: "结构 / 规格", ph: "与参考图锁定的部件与尺寸", area: true },
    { k: "flaw", label: "产品瑕疵（必填）", ph: "至少 1 处，撑起写实" },
    { k: "scene", label: "真实环境", ph: "哪个房间、哪座城市" },
    { k: "surface", label: "接触面", ph: "台面 / 地面 / 床面" },
    { k: "surfaceTrace", label: "环境痕迹", ph: "水渍、划痕、碎屑", area: true },
    { k: "heroAction", label: "核心动作", ph: "一只手做的那个动作" },
    { k: "benefit", label: "收益点证据", ph: "肉眼能看到的证据" },
    { k: "accent", label: "点缀色", ph: "画面里的色彩锚点" },
    { k: "sounds", label: "同期声（逐项）", ph: "用顿号分隔", area: true }
  ];

  var BEATS = [
    { name: "钩子", t0: 0, t1: 3 },
    { name: "触碰", t0: 3, t1: 6 },
    { name: "细节", t0: 6, t1: 9 },
    { name: "结果", t0: 9, t1: 12 },
    { name: "安定", t0: 12, t1: 15 }
  ];
  var NUM = ["①", "②", "③", "④", "⑤"];

  var CHECKS = [
    "产品外观 / 结构 / 配色 / logo 位置与参考图一致",
    "手指数量正确，无融合、无六指",
    "皮肤有毛孔与纹理，无磨皮塑料感",
    "光影来自画面内可见光源，无无源打光",
    "画面无字幕、无水印、无乱码文字",
    "德语口播 5 句齐全，无绝对化 / 最高级 / 价格对比",
    "若出镜口播：口型与德语同步，无一帧漂移",
    "5 拍齐全，每拍有动作 + 镜头 + 细节 + 声音",
    "结尾留白，无 logo 砸屏、无旋转炫技、无光晕",
    "单段 ≤10s（Mini）；15s 由 A 版或 B 版两段拼接达成"
  ];

  /* 德国站合规：绝对化 / 价格 / 紧迫 / 功效 / 环保 五类 */
  var LEX = [
    { cat: "绝对化", fix: "换成 praktisch / solide / für den Alltag",
      words: ["beste", "besten", "bester", "Nr. 1", "unschlagbar", "weltweit führend", "perfekt", "100%", "garantiert", "einzigartig", "revolutionär"] },
    { cat: "价格", fix: "不提价，只提规格（如 im 3er-Pack）",
      words: ["Sonderpreis", "statt", "reduziert", "günstigster", "Rabatt", "halber Preis"] },
    { cat: "紧迫", fix: "删除虚假紧迫，改为中性补充",
      words: ["nur heute", "nur noch", "letzte Chance", "solange Vorrat reicht", "jetzt oder nie"] },
    { cat: "功效", fix: "改为 für die tägliche Reinigung（HWG 约束）",
      words: ["desinfiziert", "keimfrei", "antibakteriell", "hygienisch sicher", "steril", "heilt", "entfernt 99"] },
    { cat: "环保", fix: "无认证则不提环保属性",
      words: ["umweltfreundlich", "klimaneutral", "biologisch abbaubar", "CO2-neutral", "nachhaltig"] }
  ];

  /* 对标解析：钩子分类 / 手法识别 / 规律映射 */
  var HOOKS = [
    { k: "pain", label: "痛点开场", re: /脏|乱|难|麻烦|烦|总是|每次|每天|immer|schmutz|fleck|nervt|leid|problem/i },
    { k: "result", label: "结果前置", re: /先看结果|先给结果|已经|擦干净|搞定|完成了|so sieht|ergebnis|fertig|danach/i },
    { k: "myth", label: "反常识", re: /不可能|其实不是|别再|不要再|错了|falsch|trick|hör auf|bringt nichts/i },
    { k: "shock", label: "视觉冲击", re: /微距|特写|倒进去|泼|喷|爆|macro|zoom|nahaufnahme/i },
    { k: "question", label: "悬念提问", re: /\?|？|warum|wie oft|wer kennt/i }
  ];

  var TECH_RULES = [
    ["微距取证", /微距|特写|极致细节|纹理|macro|nahaufnahme|close.?up/i],
    ["对比演示", /前后对比|左右对比|两半|一半|对比演示|vorher|nachher|before.?after/i],
    ["第一人称", /POV|第一人称|手持|手机实拍|iphone/i],
    ["同期声", /无配乐|同期声|原声|环境音|现场声/i],
    ["画外口播", /画外音|画外|off.?voice|旁白/i],
    ["字幕", /字幕|caption|text.?over|文案上屏/i],
    ["快切", /快切|跳切|jump.?cut|变速|切镜/i],
    ["使用场景", /厨房|浴室|卧室|客厅|阳台|办公室|küche|bad|wohnzimmer/i],
    ["数据背书", /%|Prozent|承重|kg|cm|测试|测评/i],
    ["一镜到底", /一镜到底|不切|单镜头|one.?take/i]
  ];

  var HOOK_PRINCIPLE = {
    pain: "前 3 秒不出现产品，只给问题现场；一句德语陈述句把观众钉在共鸣上，不要开场打招呼或介绍自己",
    result: "先给处理完的结果，再倒回过程；观众为「怎么做到的」留下来",
    myth: "开场先否定一个大家都在做的动作，用同一机位证伪，反差本身就是留存",
    shock: "开场用一个强视觉动作（倒、泼、挤、撕）占满画面，靠画面留人而不是靠台词",
    question: "开场抛一个具体问题（「你的台面也这样吗」），把评论区当第二屏"
  };

  var TECH_PRINCIPLE = {
    "微距取证": "用极近微距把材质压纹填满画面，让纹理替口播做证；不写「高清」，写「能数清纤维走向」",
    "对比演示": "同机位、同光位做前后对照，中间不切镜头、不加箭头，观众自己看出差异",
    "第一人称": "POV 俯视 45°，机位在胸口高度，手入画出画，不用第三人称全景",
    "同期声": "全程无配乐，只留抽纸、水流、关门声；声音是真实感的锚点",
    "画外口播": "德语走画外音，人物不必露脸，省掉对不上口型的问题",
    "字幕": "关键词上德语字幕，位置贴底部 1/3，避开平台 UI 遮挡区",
    "快切": "前 6 秒至少 3 个切点，每拍只讲一件事，不在一拍里塞两个信息",
    "使用场景": "场景写具体：柏林老公寓厨房而不是「现代厨房」，杂乱的现实比整洁的样板间可信",
    "数据背书": "把数字说成可验证的量——3 层、5 kg、20×20cm，不用「超大容量」这类无法核对的词",
    "一镜到底": "一镜到底或极少量切点，用连续动作证明「真的在做」而不是剪辑出来的效果"
  };

  /* 六叙事角度：只改写第 1 拍与第 5 拍，中间三拍复用产品标准动作 */
  var ANGLES = [
    {
      k: "pain", label: "痛点直击", aud: "有固定家务动线的主理人（25–45，德国公寓）", sp: 0,
      hook: function (v) { return { name: "问题现场",
        action: v.surface + "上一片待处理的杂乱（" + v.surfaceTrace + "）占满画面，" + v.scene + "的窗光斜射进来，产品不在画面里，手也不出现。",
        camera: "固定机位，POV 俯视 45°，全程不运动，焦点压在脏污最重的那一处。",
        detail: "杂乱占画面三分之一，手不入画；窗光扫过，表面反光不规则。" }; },
      cta: function (v) { return { name: "收口",
        action: "画面停在" + v.benefit + "的状态，产品静置原位，手撤出画面。",
        camera: "不推近、不切镜头，末尾留 1 秒静止。",
        detail: "只留环境物理反馈，不加 logo、不加光晕、不加转场。" }; }
    },
    {
      k: "result", label: "结果前置", aud: "已经搜过解决方案、只想确认效果的人", sp: 0,
      hook: function (v) { return { name: "结果前置",
        action: "先给答案：" + v.surface + "已处理干净的那一半特写，产品只露一角，0–3 秒不解释、不给全貌。",
        camera: "固定微距，焦点压在干净与未处理的交界线上。",
        detail: "交界线两侧纹理对比直给，水光还没干。" }; },
      cta: function (v) { return { name: "收口",
        action: v.heroAction + "完整做完最后一格，画面不再移动。",
        camera: "原位不动，只保留呼吸浮动。",
        detail: "结尾不出现任何文字与 logo，靠画面收。" }; }
    },
    {
      k: "myth", label: "反常识验证", aud: "试过别的方法但没效果的人", sp: 1,
      hook: function (v) { return { name: "反常识开场",
        action: "手先用旧方式处理一下（干擦 / 纸巾），留下碎屑——然后停住，产品才入画。",
        camera: "机位不动，只让手出入画，用一次停顿表达「刚才那下不对」。",
        detail: "碎屑清晰可见，停半秒再推进。" }; },
      cta: function (v) { return { name: "收口",
        action: "把旧方式留下的痕迹一并处理掉，" + v.benefit + "。",
        camera: "略微后拉，把两种结果同时纳入画面。",
        detail: "不做分屏、不加字幕对比，让画面自己说。" }; }
    },
    {
      k: "routine", label: "日常代入", aud: "下班回家 / 刚做完饭的即时场景人群", sp: 3,
      hook: function (v) { return { name: "日常切入",
        action: "从" + v.scene + "的门口视角起手，人走进画面，手上已经拿着产品。",
        camera: "第一人称向前走两步，然后俯视落到" + v.surface + "。",
        detail: "环境里有生活杂物轮廓，不提前清理现场。" }; },
      cta: function (v) { return { name: "收口",
        action: "产品放回原位，人转身离开画面，环境回到可用状态。",
        camera: "固定在俯视角，不跟随人离开。",
        detail: "留 1 秒空镜，声音收到环境底噪。" }; }
    },
    {
      k: "compare", label: "对比实验", aud: "怀疑效果、需要被说服的人", sp: 2,
      hook: function (v) { return { name: "对比实验",
        action: v.surface + " 划成左右两半：左半保持原状，右半用产品处理一次。",
        camera: "高角度俯拍，左右对称构图，机位锁死。",
        detail: "两半的光照完全一致，不做任何后期差异。" }; },
      cta: function (v) { return { name: "收口",
        action: "镜头停在左右差异上，手撤出画面，不下结论。",
        camera: "固定，构图不变，留 2 秒让观众自己看。",
        detail: "不加箭头、不加文字标注。" }; }
    },
    {
      k: "list", label: "连续演示", aud: "一次想看全用法的人（收藏党）", sp: 2,
      hook: function (v) { return { name: "连续演示",
        action: "同一动作在三个位置连续做一遍（" + v.surface + "、边缘、角落），每个位置约 1 秒。",
        camera: "机位不动，只靠手部移动切换落点，不切镜头。",
        detail: "三个落点的材质反光各不相同。" }; },
      cta: function (v) { return { name: "收口",
        action: "第三个位置做完，手停在画面上方半秒再撤出。",
        camera: "固定，不推近。",
        detail: "结尾留白，不加 CTA 字幕。" }; }
    }
  ];

  var VAR_DEFS = [
    ["product", "产品"], ["material", "材质"], ["structure", "结构"], ["scene", "环境"],
    ["surface", "接触面"], ["surfaceTrace", "痕迹"], ["heroAction", "核心动作"],
    ["benefit", "收益证据"], ["accent", "点缀色"], ["flaw", "瑕疵"], ["sounds", "同期声"]
  ];

  var DEMO = [
    "# 德国清洁湿巾 15s 爆款拆解（反推）",
    "",
    "来源：@sauber_berlin",
    "目标受众：25–45 岁德国公寓住户，做完饭不想擦台面的人",
    "",
    "## 卖点",
    "- 一张搞定油渍，不用二次擦",
    "- 无纺布不掉毛，擦完不留絮",
    "- 3er-Pack 补货一次够两周",
    "",
    "## 0-3s 钩子",
    "画面：厨房台面一片狼藉，油点和面包屑占满画面，产品不在镜头里。",
    "镜头：固定俯视，微微推近。",
    "细节：窗光照出油膜反光。",
    "口播：「Nach dem Kochen sieht die Arbeitsfläche immer so aus.」",
    "",
    "## 3-6s 触碰",
    "画面：一只手从画面下方入画，抽出一张湿巾横向擦过台面。",
    "镜头：跟随手部平移。",
    "",
    "## 6-9s 细节",
    "画面：微距推到布面，油渍被推成一团，纤维没有破。",
    "镜头：极近距离变焦。",
    "细节：布面残留一点水光。",
    "",
    "## 9-12s 结果",
    "画面：台面回到干净状态，湿巾被折起扔进垃圾桶。",
    "镜头：略微后拉。",
    "",
    "## 12-15s 收口",
    "画面：包装静置台面，手撤出画面。",
    "口播：「Im 3er-Pack – Nachschub für die nächsten Wochen.」"
  ].join("\n");

  /* ───────────── 状态与持久化 ───────────── */
  var KEY = { vars: "wb.po.vars.v1", voice: "wb.po.voice.v1", ver: "wb.po.ver.v1", bench: "wb.po.bench.v2", checks: "wb.po.checks.v1", bridge: "wb.po.bridge.v1" };
  var MAX_BENCH = 20;

  var S = {
    preset: "wipes",
    v: Object.assign({}, PRESETS.wipes.v),
    voice: PRESETS.wipes.voice.slice(),
    ver: "A",
    checks: {},
    bench: { list: [], sel: null, items: [], angles: {} },
    bridge: null,          // ④ 送过来的「产品 + 模板」（ps_pipeline.js 调 PS.setBridge）
    rv: { list: [], loading: false },
    ready: false,
    t: 0
  };

  function loadState() {
    S.bridge = null;
    try {
      var v = JSON.parse(localStorage.getItem(KEY.vars) || "null");
      if (v && typeof v === "object") {
        Object.keys(PRESETS.wipes.v).forEach(function (k) { if (typeof v[k] === "string") S.v[k] = v[k]; });
        if (v.__preset) S.preset = v.__preset;
      }
      var vo = JSON.parse(localStorage.getItem(KEY.voice) || "null");
      if (Array.isArray(vo) && vo.length === 5) S.voice = vo.map(String);
      var ver = localStorage.getItem(KEY.ver);
      if (ver === "A" || ver === "B") S.ver = ver;
      var ck = JSON.parse(localStorage.getItem(KEY.checks) || "null");
      if (ck && typeof ck === "object") S.checks = ck;
      var br = JSON.parse(localStorage.getItem(KEY.bridge) || "null");
      S.bridge = normalizeBridge(br);
    } catch (e) { /* 坏存储按默认值走，不让页面初始化中断 */ }
    ANGLES.forEach(function (a) { if (S.bench.angles[a.k] === undefined) S.bench.angles[a.k] = true; });
    loadBench();
  }

  function saveVars() {
    try {
      localStorage.setItem(KEY.vars, JSON.stringify(Object.assign({ __preset: S.preset }, S.v)));
      localStorage.setItem(KEY.voice, JSON.stringify(S.voice));
      localStorage.setItem(KEY.ver, S.ver);
    } catch (e) { /* 配额满时静默：变量可重建，不打断输入 */ }
  }

  /* ═════════════════ 提示词引擎（移植自 studio buildSections） ═════════════════ */
  /* ══════════ ④ → ⑤ 桥接（2026-10-26）══════════
     ④ 区把「识别出的产品 + 归纳好的模板」打包送到这里。没有接入时
     下面每个函数都退化成原值，⑤ 的行为与接入前完全一致。 */

  /* 接入边界统一校验，累计时间按十分之一秒分配，尾拍精确收在总时长。 */
  function normalizeBridge(input) {
    if (!input || typeof input !== "object" || !String(input.product || "").trim() ||
        !Array.isArray(input.ratio) || !input.ratio.length || input.ratio.length > 32) return null;
    var br;
    try { br = JSON.parse(JSON.stringify(input)); } catch (e) { return null; }
    var weights = br.ratio.map(function (r) {
      if (!r || typeof r !== "object") return NaN;
      return r.dur != null ? Number(r.dur) : Number(r.t1) - Number(r.t0);
    });
    if (weights.some(function (x) { return !Number.isFinite(x) || x <= 0; })) return null;
    var sum = weights.reduce(function (a, b) { return a + b; }, 0);
    var span = br.span == null ? sum : Number(br.span);
    if (!Number.isFinite(span) || span <= 0 || span > 600) return null;
    var ticks = Math.round(span * 10), cursor = 0, accum = 0;
    if (ticks < weights.length) return null;
    br.ratio = br.ratio.map(function (r, i) {
      accum += weights[i];
      var end = i === weights.length - 1 ? ticks : Math.round(accum / sum * ticks);
      end = Math.min(ticks - (weights.length - i - 1), Math.max(cursor + 1, end));
      var out = { role: String(r.role || "演示"), t0: cursor / 10, t1: end / 10, dur: (end - cursor) / 10 };
      cursor = end;
      return out;
    });
    br.span = ticks / 10; br.beats = br.ratio.length;
    ["product", "material", "structure", "scene", "benefit", "accent", "audience", "tplName", "category"].forEach(function (k) { br[k] = String(br[k] || ""); });
    ["selling", "shoot", "colors"].forEach(function (k) { br[k] = Array.isArray(br[k]) ? br[k].filter(function (x) { return typeof x === "string"; }) : []; });
    br.images = Array.isArray(br.images) ? br.images.filter(function (x) { return x && typeof x === "object"; }) : [];
    br._applied = br._applied && typeof br._applied === "object" ? br._applied : {};
    br._manual = br._manual && typeof br._manual === "object" ? br._manual : {};
    return br;
  }

  function apparelMode() {
    return !!S.bridge && /服装|毛衣|针织|衬衫|外套|连衣裙|裤|T恤|卫衣|运动.*套装|apparel|clothing|sweater|shirt|dress/i.test([S.v.product || S.bridge.product, S.bridge.category].join(" "));
  }

  function isPresetVoice(line) {
    return Object.keys(PRESETS).some(function (k) { return PRESETS[k].voice.indexOf(line) >= 0; });
  }

  function saveBridge() {
    try {
      if (S.bridge) localStorage.setItem(KEY.bridge, JSON.stringify(S.bridge));
      else localStorage.removeItem(KEY.bridge);
    } catch (e) { say("接入信息未能保存，刷新会丢失；请先导出生成结果"); }
  }

  function markManual(k) {
    if (!S.bridge) return;
    S.bridge._manual[k] = true;
    delete S.bridge._applied[k];
    saveBridge();
  }

  function apparelBeat(role, v) {
    var detail = /细节|微距|特写/.test(role);
    return { name: role || "穿着演示",
      action: detail ? "近看参考图中实际可见的缝线、边缘与材质纹理：" + (v.material || "〔材质待补〕") + "。"
        : /证据|结果|效果|对比/.test(role) ? "保持相同光线与姿势，展示可核对的细节：" + (v.benefit || "〔收益证据待补〕") + "。"
        : (v.heroAction || "模特自然转身展示衣物轮廓与活动时的布料变化") + "。",
      camera: detail ? "缓慢靠近衣物细节，保持焦点稳定。" : "平视构图，跟随穿着者的自然动作。",
      detail: "只呈现参考图与实际动作可支持的质感，不添加未证实的功能。" };
  }

  function apparelEnds(angle, v) {
    var hooks = {
      pain: "从选择日常穿搭的场景起手，展示衣物当前状态，再将产品完整纳入画面。",
      result: "先展示完整穿着效果，再靠近衣物细节。",
      myth: "先展示整体外观，再近看材质与结构，用实拍细节核对第一印象。",
      routine: "从日常穿搭场景起手，穿着者自然整理衣物并走动。",
      compare: "在同一光线下依次展示正面与侧面，便于比较轮廓与结构。",
      list: "依次展示整体轮廓、结构细节和活动状态。"
    };
    return [{name:angle.label, action:hooks[angle.k], camera:"平视手持构图，以服装和穿着者为主体。", detail:"按当前拍的时长完成动作，不加字幕或效果承诺。"},
      {name:"收口", action:"停留在" + (v.product || "产品") + "的完整穿着效果上，保持自然姿态。",camera:"固定构图，保留轻微呼吸浮动。",detail:"不添加 logo、夸张转场或未验证的宣传。"}];
  }

  function fieldLabel(k) {
    var d = VAR_DEFS.filter(function (x) { return x[0] === k; })[0];
    return d ? d[1] : k;
  }

  /* ① 区字段优先，空着的用 ④ 的识别结果补；不覆盖用户手写的内容 */
  function effVars() {
    var v = Object.assign({}, S.v);
    var br = S.bridge;
    if (!br) return v;
    var sell = (br.selling || []).filter(function (x) { return String(x || "").trim(); });
    var fill = {
      product: br.product, material: br.material, scene: br.scene,
      structure: br.structure, accent: br.accent,
      benefit: String(br.benefit || "").trim() || sell[0] || ""
    };
    Object.keys(fill).forEach(function (k) {
      var cur = String(v[k] == null ? "" : v[k]).trim();
      var put = String(fill[k] == null ? "" : fill[k]).trim();
      if (!cur && put) v[k] = put;
    });
    /* 痕迹/核心动作/瑕疵/同期声/接触面属「只能人补」的信息：一律不猜。
       服装类没有「台面」，硬塞会把针织毛衣写成放在台面上擦的东西。 */
    var defaults = (PRESETS[S.preset] || {}).v || {};
    ["surface", "surfaceTrace", "heroAction", "flaw", "sounds"].forEach(function (k) {
      if (!br._manual[k] && v[k] === defaults[k]) v[k] = "";
    });
    return v;
  }

  function bridgeRatio() {
    var br = S.bridge;
    return br && Array.isArray(br.ratio) && br.ratio.length ? br.ratio : null;
  }

  /* 时间轴：有接入就用模板的秒点，否则沿用原来的 5 拍 */
  function beatSpan(i) {
    var r = bridgeRatio();
    if (r && r[i]) return { t0: r[i].t0, t1: r[i].t1 };
    return BEATS[i] || BEATS[BEATS.length - 1];
  }

  function numAt(i) { return NUM[i] || String(i + 1); }

  /* 模板拍数不是 5 时，口播循环取，不丢句子也不硬凑 */
  function voiceAt(i, n) {
    var idx = n > 5 && i === n - 1 ? 4 : i;
    var line = (n > 5 && i >= 4 && i < n - 1) ? "" : String((S.voice || [])[idx] || "").trim();
    if (S.bridge && isPresetVoice(line)) line = "";
    return line || "〔第 " + (i + 1) + " 拍德语口播待补〕";
  }

  /* 模板拍名 → 中间拍动作（beatCopy 1/2/3 是原来的「触碰/细节/结果」语义） */
  function beatForRole(role, v) {
    var r = String(role || "");
    if (apparelMode()) return apparelBeat(r, v);
    if (/细节|微距|特写/.test(r)) return beatCopy(2, v);
    if (/证据|结果|效果|对比|验证/.test(r)) return beatCopy(3, v);
    if (/收尾|安定|收口/.test(r)) return beatCopy(4, v);
    return beatCopy(1, v);
  }

  function beatCopy(i, vv) {
    var v = vv || S.v, P = v.product || "〔产品〕";
    switch (i) {
      case 0: return {
        action: (v.surface || "〔接触面〕") + " 上一片待处理的杂乱（" + (v.surfaceTrace || "〔痕迹〕") + "）占据画面主体，" + P + " 只从画面右下角露出一角。",
        camera: "从 30cm 高度极缓推近，焦点先落在杂乱处，再轻微前移。",
        detail: "窗光扫过脏污，表面反光呈不规则高光。" };
      case 1: return {
        action: "一只手从画面下方入画，" + (v.heroAction || "〔核心动作〕") + "。",
        camera: "机位跟随手部横向平移，焦点锁在接触点上。",
        detail: "手指按压时材质轻微形变，回弹有延迟。" };
      case 2: return {
        action: (v.benefit || "〔收益点证据〕") + "，对 " + (v.material || "〔材质〕") + " 做极致微距。",
        camera: "从前景纹理缓慢变焦到背后的 " + (v.accent || "〔点缀色〕") + " 虚化区。",
        detail: "纤维断面可见、纹理里残留一点真实水光。此拍是整条的成败关键。" };
      case 3: return {
        action: P + " 被放回真实使用位置，环境回到可用状态；手再做一次短促的收尾动作。",
        camera: "机位后拉半米，把产品和环境一起纳入画面。",
        detail: "环境光在物件边缘形成一条真实高光，不出现泛光。" };
      default: return {
        action: "手撤出画面，" + P + " 静置于原位。",
        camera: "固定，仅保留呼吸浮动，窗光随云影略微暗下。",
        detail: "最后一粒尘埃穿过光柱落定。" };
    }
  }

  function buildSections() {
    var v = S.v;
    var vo = function (i) { return S.voice[i] || "〔德语口播〕"; };
    var split = S.ver === "B";

    var s1 = "写实 UGC 带货硬广 | 第一人称 POV 手机实拍 | 家庭真实可用光 | 节拍同步 5 拍 | 杜绝 CG 渲染感、影棚塑料光、磨皮美颜";

    var s2 = "产品：参照上传的产品参考图，外观、比例、配色、" + (v.structure || "〔结构〕") + "、印刷文字与 logo 位置百分百还原，不得增删部件、不得改变材质。表面保留真实使用痕迹：" + (v.flaw || "〔瑕疵〕") + "，边缘一道极细划痕恰好被侧光勾出。\n" +
      "材质：" + (v.material || "〔材质〕") + "——写质地不写形容词，表面能读出纤维走向与压纹深度。\n" +
      "场景：" + (v.scene || "〔环境〕") + "，" + (v.surface || "〔接触面〕") + "上有真实生活痕迹：" + (v.surfaceTrace || "〔痕迹〕") + "。靠窗的下午日光斜射入画，室内吸顶灯混进画面形成略杂乱的混合色温。背景轻微失焦，能看到真实家居杂物的轮廓，绝不是纯色无缝背幕。";

    var s3 = "视觉基底：模拟 iPhone 15 Pro 后置主摄拍摄，24mm 等效焦距，f/1.8，第一人称手持 POV，从胸口高度俯视台面 45°。\n" +
      "色彩与影调：自然光为主，窗光轻微过曝，低对比度，" + (v.accent || "〔点缀色〕") + " 小面积点缀。保留传感器噪点与镜头上一点真实污渍带来的柔光折射。\n" +
      "皮肤与真实感：出镜为真实人手，手背可见毛孔、指节细小绒毛、指甲边缘不平整、指腹有轻微干燥纹路。若画面出现人物面部：realistic human, natural facial expressions, authentic relaxed smile, realistic skin texture, no uncanny AI look。全程不磨皮、不加美颜滤镜、不做对称修饰。\n" +
      "护栏：手部五指结构完整、关节自然、指甲形态不对称；产品表面有真实使用痕迹；画面带轻微手持抖动与传感器噪点；画面内不出现任何非产品自带的可读文字。\n" +
      "声音：无配乐，仅保留现场同期声——" + (v.sounds || "〔同期声〕") + "。";

    var s4 = "单镜头，一镜到底，无剪辑，全程第一人称。\n" +
      "角度：POV 俯视，机位在胸口高度，看向台面 45°。\n" +
      "运动：极缓向前推进，配合一次横向跟随手部的平移；跟随动作，不做环绕炫技。\n" +
      "呼吸感：手持镜头。全程保持一种极其微弱、类似呼吸般的镜头浮动，以增强临场感。\n" +
      "声音：无配乐，仅保留现场同期声。德语口播为画外音。";

    var seg = function (i) {
      var B = BEATS[i], c = beatCopy(i);
      return B.t0 + "–" + B.t1 + " 秒 · " + B.name + "\n" +
        "动作：" + c.action + "\n镜头：" + c.camera + "\n细节：" + c.detail + "\n" +
        "声音：德语口播" + NUM[i] + "「" + vo(i) + "」";
    };

    var s5 = BEATS.map(function (_, i) { return seg(i); }).join("\n\n");
    s5 += "\n收尾：不砸 logo。不做产品旋转炫技。不爆镜头光晕。不加任何特效。";

    if (split) {
      s5 = "—— 前段 0–7.5s ——\n" + [0, 1, 2].map(seg).join("\n\n") +
        "\n7.5 秒处收在微距纹理上，画面不切、不加转场；手部保持在画面内且位置固定，便于下一段接帧。\n\n" +
        "—— 后段 7.5–15s ——\n首帧承接上一段最后一帧：手部位置、产品位置、光线方向、背景景深保持一致，不重新布置场景。\n\n" +
        [3, 4].map(seg).join("\n\n") +
        "\n收尾：不砸 logo。不做产品旋转炫技。不爆镜头光晕。不加任何特效。";
    }

    return [
      { n: "01", title: "核心主题", body: "【1 · 核心主题】\n" + s1 },
      { n: "02", title: "产品与场景", body: "【2 · 产品与场景】\n" + s2 },
      { n: "03", title: "氛围与画质", body: "【3 · 氛围与画质】\n" + s3 },
      { n: "04", title: "运镜规则", body: "【4 · 运镜规则】\n" + s4 },
      { n: "05", title: split ? "分镜（拆段）" : "分镜（5 拍）", body: "【5 · 分镜】\n" + s5 }
    ];
  }

  function promptText() { return buildSections().map(function (s) { return s.body; }).join("\n\n"); }
  function voiceText() { return S.voice.map(function (l, i) { return NUM[i] + " " + l; }).join("\n"); }
  function wordCount(t) { return String(t || "").trim() ? String(t).trim().split(/\s+/).length : 0; }

  /* 合规扫描：逐句命中，返回字面与建议（词边界按德语字母判断，避免 statt 命中 stattfinden） */
  function scanVoice() {
    var hits = [];
    S.voice.forEach(function (line, i) {
      LEX.forEach(function (g) {
        g.words.forEach(function (w) {
          var lit = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          var re = new RegExp("(^|[^0-9A-Za-zÄÖÜäöüß])" + lit + "($|[^0-9A-Za-zÄÖÜäöüß])", "i");
          if (re.test(line)) hits.push({ line: i, word: w, cat: g.cat, fix: g.fix });
        });
      });
    });
    return hits;
  }

  /* ═════════════════ 对标库：解析 / 规律 / 变异 ═════════════════ */
  function beatTime(h) {
    var m = h.match(/(\d{1,2}):(\d{2})\s*[-–—~到至]\s*(\d{1,2}):(\d{2})/);
    if (m) return (+m[2] < 60 && +m[4] < 60) ? [+m[1] * 60 + +m[2], +m[3] * 60 + +m[4]] : null;
    m = h.match(/(\d+(?:[.,]\d+)?)\s*(?:sec|s|秒)?\s*[-–—~到至]\s*(\d+(?:[.,]\d+)?)/i);
    if (m) return [parseFloat(m[1].replace(",", ".")), parseFloat(m[2].replace(",", "."))];
    return null;
  }

  function benchField(body, re) {
    var l = body.filter(function (x) { return re.test(x); })[0];
    if (!l) return "";
    var t = l.replace(/^[\s>*\-–—•\d.)、]+/, "").trim();
    var i = t.search(/[:：]/);
    return (i > -1 ? t.slice(i + 1) : t).trim().slice(0, 240);
  }

  function benchQuote(body) {
    for (var i = 0; i < body.length; i++) {
      var m = body[i].match(/[「“"]([^」”"]{4,120})[」”"]/);
      if (m) return m[1].trim();
    }
    return "";
  }

  function parseBench(raw, title) {
    var text = String(raw || "").replace(/\r\n?/g, "\n").trim();
    var lines = text.split("\n");
    var h1 = (lines.filter(function (l) { return /^#\s+\S/.test(l); })[0] || "").replace(/^#\s+/, "").trim();
    var name = ((title || "").trim() || h1 || (lines.filter(function (l) { return l.trim(); })[0] || "").trim()).slice(0, 80);
    var src = ((text.match(/(?:来源|链接|博主|账号|source)\s*[:：]\s*(\S+)/i) || [])[1] || "").slice(0, 80);
    var aud = ((text.match(/(?:受众|目标人群|目标受众|audience|target)\s*[:：]\s*(.+)/i) || [])[1] || "").trim().slice(0, 60);

    /* 按标题切小节（含「1. 0-3s xx」这类带序号的秒点行） */
    var secs = [], cur = { head: "", body: [] };
    lines.forEach(function (l) {
      if (/^#{1,6}\s+/.test(l) || (/^\s*\d/.test(l) && beatTime(l))) {
        if (cur.head || cur.body.join("").trim()) secs.push(cur);
        cur = { head: l.replace(/^#{1,6}\s+/, "").trim(), body: [] };
      } else cur.body.push(l);
    });
    if (cur.head || cur.body.join("").trim()) secs.push(cur);

    var beats = [];
    secs.forEach(function (s) {
      var t = beatTime(s.head);
      if (!t || t[1] <= t[0]) return;
      beats.push({
        t0: t[0], t1: t[1],
        name: s.head.replace(/^[\d.,\s\-–—~s秒至:]+/, "").trim().slice(0, 20) || "拍",
        visual: benchField(s.body, /(画面|镜头|视觉|动作|场景|shot|camera|visual|action)/i),
        voice: benchField(s.body, /(口播|台词|旁白|voice|caption|字幕)/i) || benchQuote(s.body),
        note: benchField(s.body, /(细节|要点|备注|detail|note)/i)
      });
    });
    beats.sort(function (a, b) { return a.t0 - b.t0; });

    /* 没有秒点：按小节均分 15s */
    if (!beats.length) {
      var body2 = secs.filter(function (s) {
        return s.head && s.head !== h1 &&
          !/卖点|亮点|受众|来源|备注|总结|points?|benefit/i.test(s.head) &&
          s.body.join("").trim().length > 8;
      }).slice(0, 10);
      if (body2.length >= 2) {
        var step = 15 / body2.length;
        body2.forEach(function (s, i) {
          beats.push({
            t0: +(i * step).toFixed(1), t1: +((i + 1) * step).toFixed(1),
            name: (s.head || "第 " + (i + 1) + " 段").slice(0, 20),
            visual: benchField(s.body, /(画面|镜头|视觉|动作|场景)/i) || s.body.join(" ").replace(/\s+/g, " ").trim().slice(0, 160),
            voice: benchField(s.body, /(口播|台词|旁白)/i) || benchQuote(s.body),
            note: benchField(s.body, /(细节|要点|备注)/i)
          });
        });
      }
    }

    /* 卖点：优先「卖点」小节，否则按关键词挑 bullet */
    var sp = [];
    var pushSp = function (t) {
      t = String(t).replace(/^[\s>*\-–—•\d.)、]+/, "").replace(/\*\*/g, "").trim().slice(0, 64);
      if (t.length >= 3 && !sp.some(function (x) { return x === t; })) sp.push(t);
    };
    var spSec = secs.filter(function (s) { return /卖点|亮点|points?|benefit|价值/i.test(s.head); })[0];
    if (spSec) spSec.body.forEach(function (l) { if (l.trim() && !/^#{1,6}/.test(l)) pushSp(l); });
    if (sp.length < 3) {
      lines.forEach(function (l) {
        if (!/^[\s>*\-–—•]+/.test(l)) return;
        if (/(省|快|耐用|轻|不|防|容量|材质|吸收|撕|承重|透气|可水洗|复用|安装|折叠|德国|欧盟|环保|用完|一次)/.test(l)) pushSp(l);
      });
    }

    var techs = TECH_RULES.filter(function (r) { return r[1].test(text); }).map(function (r) { return r[0]; });

    var firstTxt = (beats[0] ? [beats[0].name, beats[0].visual, beats[0].voice, beats[0].note].join(" ") : "") + " " + text.slice(0, 400);
    var hook = "pain", best = 0;
    HOOKS.forEach(function (h) {
      var m = firstTxt.match(new RegExp(h.re.source, "gi"));
      var sc = m ? m.length : 0;
      if (sc > best) { best = sc; hook = h.k; }
    });

    var span = beats.length ? Math.max.apply(null, beats.map(function (b) { return b.t1; })) : 15;
    var fast = beats.filter(function (b) { return (b.t1 - b.t0) <= 1.5; }).length;

    var vars = [];
    (text.match(/\{\{\s*([\w\u4e00-\u9fa5]+)\s*\}\}/g) || []).forEach(function (s) {
      var k = s.replace(/[{}\s]/g, "");
      if (vars.indexOf(k) < 0) vars.push(k);
    });

    return {
      id: "b" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5),
      at: new Date().toISOString().slice(0, 10),
      name: name || "未命名对标",
      source: src, audience: aud,
      beats: beats,
      sellingPoints: sp.slice(0, 6),
      techniques: techs,
      hook: hook,
      rhythm: { span: +span.toFixed(1), shots: beats.length, avg: beats.length ? +(span / beats.length).toFixed(1) : 0, fast: fast },
      vars: vars.slice(0, 14),
      raw: text
    };
  }

  function loadBench() {
    try {
      var arr = JSON.parse(localStorage.getItem(KEY.bench) || "[]");
      S.bench.list = Array.isArray(arr) ? arr.filter(function (x) {
        return x && typeof x.raw === "string";
      }).map(function (x) {
        var r = parseBench(x.raw, x.name);
        r.id = x.id || r.id;
        r.at = x.at || r.at;
        r.assets = x.assets || null;
        r.report = x.report || null;
        return r;
      }).filter(function (x) { return x.beats.length >= 1; }).slice(0, MAX_BENCH) : [];
    } catch (e) { S.bench.list = []; }
    if (S.bench.list.length && !S.bench.sel) S.bench.sel = S.bench.list[0].id;
  }

  function saveBench() {
    try {
      localStorage.setItem(KEY.bench, JSON.stringify(S.bench.list.slice(0, MAX_BENCH).map(function (b) {
        var o = { id: b.id, at: b.at, name: b.name, raw: b.raw };
        if (b.assets) o.assets = b.assets;
        if (b.report) o.report = b.report;
        return o;
      })));
    } catch (e) { say("本机存储已满，档案未落盘，建议先导出 .md"); }
  }

  function selBench() {
    return S.bench.list.filter(function (b) { return b.id === S.bench.sel; })[0] || null;
  }

  function addBench(rec) {
    var dup = -1;
    S.bench.list.forEach(function (b, i) { if (b.raw && rec.raw && b.raw === rec.raw) dup = i; });
    if (dup > -1) {
      var prev = S.bench.list[dup];
      rec.id = prev.id;                                    // 沿用 id，避免素材记录成孤儿
      if (!rec.assets && prev.assets) rec.assets = prev.assets;
      if (!rec.report && prev.report) rec.report = prev.report;
      S.bench.list[dup] = rec;
    } else {
      if (S.bench.list.length >= MAX_BENCH) {
        say("档案已满（" + MAX_BENCH + " 份），请先导出备份并删除不需要的档案");
        return false;
      }
      S.bench.list.unshift(rec);
    }
    S.bench.list = S.bench.list.slice(0, MAX_BENCH);
    S.bench.sel = rec.id;
    saveBench();
    S.bench.items = [];
    renderBench();
    return true;
  }

  /* H1 是「原片还原提示词」这类通用词时，用文件名 + 首个拍名当档案名，否则列表里分不清 */
  function smartName(rec, base) {
    var generic = /^(原片还原提示词|提示词反推|提示词优化|提示词文档|提示词|反推|prompts?)$/i;
    if (rec.name && !generic.test(String(rec.name).trim())) return rec.name;
    var tag = String(base || "").replace(/\.[^.]+$/, "").trim();
    var first = rec.beats[0] ? String(rec.beats[0].name || "").trim() : "";
    return ([tag || "对标"].concat(first ? ["· " + first] : []).join(" ")).slice(0, 80);
  }

  function hookLabel(k) {
    var h = HOOKS.filter(function (x) { return x.k === k; })[0];
    return h ? h.label : "未判定";
  }

  function buildPrinciples(b) {
    var out = [{ k: "钩子", t: HOOK_PRINCIPLE[b.hook] || HOOK_PRINCIPLE.pain }];
    b.techniques.slice(0, 4).forEach(function (t) { if (TECH_PRINCIPLE[t]) out.push({ k: t, t: TECH_PRINCIPLE[t] }); });
    if (b.rhythm.avg) {
      out.push({ k: "节奏", t: b.rhythm.avg <= 2
        ? "平均镜头 " + b.rhythm.avg + "s，共 " + b.rhythm.shots + " 拍" + (b.rhythm.fast ? "，其中 " + b.rhythm.fast + " 拍 ≤1.5s" : "") + "——靠切点维持注意力，不靠单镜头调度"
        : "平均镜头 " + b.rhythm.avg + "s，共 " + b.rhythm.shots + " 拍——靠连续动作和真实感留人，切点越少越可信" });
    }
    var last = b.beats[b.beats.length - 1];
    if (last) out.push({ k: "收口", t: "结尾停在「" + (last.name || "最后") + "」拍，不砸 logo、不加转场、不喊口号；留白让观众回看细节" });
    return out.slice(0, 7);
  }

  function spPool(v) {
    return [
      { k: "收益", t: v.benefit || "〔收益证据〕" },
      { k: "材质", t: v.material || "〔材质〕" },
      { k: "规格", t: v.structure || "〔结构〕" },
      { k: "场景", t: (v.scene || "〔环境〕") + " / " + (v.surface || "〔接触面〕") }
    ];
  }

  function buildVariant(angle, b) {
    var v = effVars();
    var P = v.product || "〔产品〕";
    var ratio = bridgeRatio();
    var n = ratio ? ratio.length : 5;
    var ends = apparelMode() ? apparelEnds(angle, v) : [angle.hook(v), angle.cta(v)];
    var beat1 = ends[0], beat5 = ends[1];
    var seq = [];

    if (ratio) {
      /* 有 ④ 接入：拍数与秒点都跟模板走，第 1 拍放钩子、末拍放收口，
         中间拍按模板的 role 选动作语义（细节→微距、证据→结果、其余→核心动作） */
      for (var i = 0; i < n; i++) {
        if (i === 0) seq.push(beat1);
        else if (i === n - 1 && n > 1) seq.push(beat5);
        else seq.push(beatForRole((ratio[i] || {}).role, v));
      }
    } else {
      seq = [beat1, beatCopy(1), beatCopy(2), beatCopy(3), beat5];
    }

    var tplName = (S.bridge && S.bridge.tplName) || "未命名模板";
    var skel = ratio
      ? ("④ 模板：" + tplName + "（" +
          (tplName.indexOf(n + " 拍") >= 0 ? "" : n + " 拍 / ") + (S.bridge.span || "—") + "s）")
      : ("对标骨架：" + (b ? b.name : "未指定"));
    var refLine = (S.bridge && S.bridge.images && S.bridge.images.length)
      ? "（已上传 " + S.bridge.images.length + " 张，第 1 张为主展示面）"
      : "";

    var s1 = "写实 UGC 带货硬广 | " + angle.label + " | 第一人称 POV 手机实拍 | " + n + " 拍节拍同步 | " + skel;
    var s2 = "产品：" + P + refLine + "。参照上传的产品参考图，外观、比例、配色、" + (v.structure || "〔结构〕") +
      "、印刷文字与 logo 位置百分百还原，不得增删部件、不得改变材质。表面保留真实使用痕迹：" +
      (v.flaw || "〔瑕疵〕") + "，边缘一道极细划痕恰好被侧光勾出。\n" +
      "材质：" + (v.material || "〔材质〕") + "——写质地不写形容词，表面能读出纤维走向与压纹深度。\n" +
      "场景：" + (v.scene || "〔环境〕") + "，" + (v.surface || "〔接触面〕") + "上有真实生活痕迹：" +
      (v.surfaceTrace || "〔痕迹〕") + "。靠窗的下午日光斜射入画，室内吸顶灯混进来形成略杂乱的混合色温。";
    var s3 = "视觉基底：iPhone 15 Pro 后置主摄 24mm 等效，f/1.8，第一人称手持 POV，从胸口高度俯视 45°。\n" +
      "色彩与影调：自然光为主，窗光轻微过曝，低对比度，" + (v.accent || "〔点缀色〕") + " 小面积点缀。保留传感器噪点与镜头上一点真实污渍的柔光折射。\n" +
      "皮肤与真实感：出镜为真实人手，手背可见毛孔、指节细小绒毛、指甲边缘不平整。若出现面部：realistic human, natural facial expressions, authentic relaxed smile, realistic skin texture, no uncanny AI look。全程不磨皮、不加美颜。";
    var s4 = "单镜头，一镜到底，无剪辑，全程第一人称。\n" +
      "角度：POV 俯视，机位在胸口高度，看向台面 45°。\n" +
      "运动：极缓向前推进，配合一次横向跟随手部的平移；跟随动作，不做环绕炫技。\n" +
      "呼吸感：手持镜头，全程极微弱、类似呼吸的浮动。\n" +
      "声音：无配乐，仅保留现场同期声——" + (v.sounds || "〔同期声〕") + "。德语口播为画外音。" +
      ((S.bridge && S.bridge.shoot && S.bridge.shoot.length)
        ? "\n拍摄参考（来自 ④ 的识别）：" + S.bridge.shoot.slice(0, 2).join("；")
        : "");

    if (apparelMode()) {
      s1 = "写实服装 UGC | " + angle.label + " | 自然穿着展示 | " + n + " 拍节拍同步 | " + skel;
      s2 = "产品：" + P + refLine + "。外观、比例、配色与结构以参考图为准：" + (v.structure || "〔结构待补〕") + "。\n" +
        "材质：" + (v.material || "〔材质待补〕") + "。场景：" + (v.scene || "〔场景待补〕") + "。\n" +
        "真实状态：" + (v.flaw || "〔按实物补充自然褶皱或使用痕迹〕") + "。不凭空增加破损或宣传功效。";
      s3 = "画质：自然光、真实肤质与衣物纹理；保留轻微手持浮动，不磨皮，不改变身体比例。点缀色：" + (v.accent || "〔颜色待补〕") + "。";
      s4 = "镜头以平视穿着展示为主，按模板拍序连续调度；细节拍靠近衣物，不强制俯拍。\n" +
        "声音：无配乐，现场同期声——" + (v.sounds || "〔同期声待补〕") + "；德语口播为画外音。" +
        (S.bridge.shoot.length ? "\n拍摄参考（来自 ④ 的识别）：" + S.bridge.shoot.slice(0, 2).join("；") : "");
    }
    var s5 = seq.map(function (c, i) {
      var B = beatSpan(i);
      return B.t0 + "–" + B.t1 + " 秒 · " + (c.name || (ratio && ratio[i].role) || "演示") + "\n" +
        "动作：" + c.action + "\n镜头：" + c.camera + "\n细节：" + c.detail + "\n" +
        "声音：德语口播" + numAt(i) + "「" + voiceAt(i, n) + "」";
    }).join("\n\n") + "\n收尾：不砸 logo、不做产品旋转炫技、不爆镜头光晕、不加任何特效。";

    return { s1: s1, s2: s2, s3: s3, s4: s4, s5: s5, text: [s1, s2, s3, s4, s5].join("\n\n") };
  }

  function genVariants() {
    var b = selBench();
    if (!b && !bridgeRatio()) {
      say("先归档一份档案，或去 ④ 生成方案后点「→ ⑤」把模板接进来");
      return;
    }
    var picked = ANGLES.filter(function (a) { return S.bench.angles[a.k]; });
    if (!picked.length) { say("至少选一个叙事角度"); return; }
    var v = effVars(), pool = spPool(v);
    S.bench.items = picked.map(function (a, i) {
      var built = buildVariant(a, b);
      var sp = pool[a.sp] || pool[0];
      return {
        i: i + 1, key: a.k, label: a.label, audience: (S.bridge && S.bridge.audience) || (apparelMode() ? "关注衣物材质与日常穿搭的人" : a.aud), sp: sp,
        angle: a.label + "：" + a.hook(v).name + " → " + a.cta(v).name,
        s1: built.s1, s2: built.s2, s3: built.s3, s4: built.s4, s5: built.s5, text: built.text
      };
    });
    renderVariants();
    renderBridge();        // 生成时顺带刷新「① 区还缺什么」，状态始终一致
    say("已生成 " + S.bench.items.length + " 份变异提示词");
  }

  /* ── 导出 ── */
  function variantMD() {
    var b = selBench();
    var head = [
      "# 变异提示词 · " + (S.bridge ? S.bridge.tplName : (b ? b.name : "无对标")), "",
      "- 生成时间：" + new Date().toISOString().slice(0, 16).replace("T", " "),
      "- 对标骨架：" + (S.bridge ? "采用 ④ 模板" : b ? b.beats.length + " 拍 / 钩子 " + hookLabel(b.hook) + " / " + b.techniques.join("、") : "—"),
      "- 产品：" + (effVars().product || ""), ""
    ];
    if (S.bridge && S.bridge.tplName) {
      head.splice(3, 0, "- ④ 模板：" + S.bridge.tplName + "（" +
        (S.bridge.ratio ? S.bridge.ratio.length : S.bridge.beats || "—") + " 拍 / " + (S.bridge.span || "—") + "s）");
    }
    var body = S.bench.items.map(function (it) {
      return [
        "## V" + String(it.i).padStart(2, "0") + " · " + it.label, "",
        "- **目标受众**：" + it.audience,
        "- **核心卖点**：" + it.sp.k + " · " + it.sp.t,
        "- **叙事角度**：" + it.angle,
        "- **可替换变量**：" + VAR_DEFS.map(function (d) { return "`{{" + d[0] + "}}`"; }).join(" "),
        "", "```text", it.text, "```", ""
      ].join("\n");
    }).join("\n");
    return head.join("\n") + "\n" + body;
  }

  function benchFence(raw) {
    var runs = String(raw).match(/`+/g) || [];
    var n = 3;
    runs.forEach(function (x) { if (x.length + 1 > n) n = x.length + 1; });
    return "`".repeat(n);
  }

  function benchAllMD() {
    return S.bench.list.map(function (b) {
      var lines = [
        "# " + b.name, "",
        "- 归档：" + b.at + (b.source ? " · 来源 " + b.source : ""),
        "- 结构：" + b.beats.length + " 拍，平均 " + b.rhythm.avg + "s，钩子 " + hookLabel(b.hook),
        "- 卖点：" + (b.sellingPoints.join("；") || "—"),
        "- 手法：" + (b.techniques.join("、") || "—"),
        "", "## 分镜"
      ];
      b.beats.forEach(function (x) {
        lines.push("- **" + x.t0 + "–" + x.t1 + "s · " + x.name + "** " + (x.visual || "") + (x.voice ? "｜口播：" + x.voice : ""));
      });
      var fence = benchFence(b.raw);
      lines.push("", "## 原始反推文档", "", fence + "markdown", b.raw, fence, "");
      return lines.join("\n");
    }).join("\n---\n\n");
  }

  function downloadText(name, text) {
    var blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function copyText(text, label) {
    var ok = function () { say("已复制" + (label ? "：" + label : "")); };
    var fallback = function () {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
        document.body.appendChild(ta);
        ta.select();
        var done = document.execCommand("copy");
        ta.remove();
        if (done) ok(); else say("复制失败，请手动选中");
      } catch (e) { say("复制失败：" + e.message); }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok, fallback);
    } else fallback();
  }

  /* ═════════════════ 反推报告 → 反推文档（与提示词反推板块的数据衔接） ═════════════════ */
  function cleanOneLine(t, max) {
    return String(t || "").replace(/\s+/g, " ").trim().slice(0, max || 60);
  }

  function reportToDoc(rep, man, vid) {
    var meta = (man && man.meta) || {};
    var shots = (rep && Array.isArray(rep.shots)) ? rep.shots : [];
    var title = [meta.nickname || meta.author || "", cleanOneLine(meta.desc, 30)].filter(Boolean).join(" · ");
    var L = [];
    L.push("# " + (title || ("反推 " + vid)) + " · " + shots.length + " 拍拆解");
    L.push("");
    if (meta.url) L.push("来源：" + meta.url);
    if (meta.duration) L.push("时长：" + meta.duration + "s");
    L.push("");
    if (rep.scene_summary) { L.push("## 场景（整片）", cleanOneLine(rep.scene_summary, 220), ""); }
    if (rep.product_summary) { L.push("## 产品（整片）", cleanOneLine(rep.product_summary, 220), ""); }
    shots.forEach(function (s, i) {
      var name = cleanOneLine(s.name || ("第 " + (i + 1) + " 拍"), 24);
      L.push("## " + (+s.t_start).toFixed(1) + "-" + (+s.t_end).toFixed(1) + "s " + name);
      if (s.initial_state) L.push("画面：" + cleanOneLine(s.initial_state, 240));
      if (Array.isArray(s.action_steps) && s.action_steps.length) L.push("动作：" + cleanOneLine(s.action_steps.join("；"), 300));
      if (s.ending_state) L.push("结果：" + cleanOneLine(s.ending_state, 160));
      if (s.camera_motion) L.push("镜头：" + cleanOneLine(s.camera_motion, 160));
      if (s.boundary_evidence) L.push("细节：" + cleanOneLine(s.boundary_evidence, 160));
      L.push("");
    });
    return { doc: L.join("\n").trim(), name: title };
  }

  async function loadReverseList() {
    if (S.rv.loading) return;
    S.rv.loading = true;
    try {
      var r = await apiFetch("/api/reverse/list");
      S.rv.list = ((r && r.items) || []).filter(function (x) { return x && x.has_report; });
    } catch (e) {
      S.rv.list = [];
    } finally { S.rv.loading = false; }
    renderRvSelect();
  }

  function renderRvSelect() {
    var sel = $("#ps-rv-sel");
    if (!sel) return;
    if (!S.rv.list.length) {
      sel.innerHTML = '<option value="">反推板块暂无已完成报告</option>';
      return;
    }
    sel.innerHTML = S.rv.list.map(function (x) {
      var m = x.meta || {};
      var who = m.nickname || m.author || "未署名";
      var id = String(x.video_id || "");
      var d = x.created_at ? new Date(x.created_at * 1000) : null;
      var when = d ? (String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0")) : "";
      var n = x.shots ? x.shots + " 拍" : "";
      return '<option value="' + esc(id) + '">@' + esc(who) + " · " + esc(id.slice(-6)) + " · " + esc([n, when].filter(Boolean).join(" · ")) + "</option>";
    }).join("");
  }

  async function importReverse() {
    var sel = $("#ps-rv-sel");
    var vid = sel && sel.value;
    if (!vid) { say("没有可导入的反推报告"); return; }
    var btn = $("#ps-rv-import");
    if (btn) { btn.disabled = true; btn.textContent = "导入中…"; }
    try {
      var base = "/tk_reverse/" + encodeURIComponent(vid) + "/";
      var rep = await apiFetch(base + "report.json");
      var man = null;
      try { man = await apiFetch(base + "manifest.json"); } catch (e) { man = null; }
      var conv = reportToDoc(rep, man, vid);
      if (!conv.doc) { say("该报告没有分镜数据"); return; }
      var rec = parseBench(conv.doc, conv.name);
      if (!rec.beats.length) { say("该报告没有可用的分镜（shots 为空）"); return; }
      if (!addBench(rec)) return;
      var hint = $("#ps-hint");
      if (hint) hint.textContent = "已导入反推报告 · " + rec.beats.length + " 拍 / " + rec.techniques.length + " 手法";
      say("已入档：" + rec.name);
    } catch (e) {
      say("导入失败：" + (e && e.message ? e.message : e));
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "⤓ 导入反推"; }
    }
  }

  /* ═════════════════ 渲染 ═════════════════ */
  function renderKPI() {
    var box = $("#ps-kpis");
    if (!box) return;
    var b = selBench();
    var hits = scanVoice();
    var pt = promptText();
    var cells = [
      { cap: "对标档案", v: S.bench.list.length, u: "份", note: S.bench.list.length ? "本机浏览器存储" : "上传或导入反推报告", tone: "" },
      { cap: "当前对标", v: b ? b.beats.length : "—", u: b ? "拍" : "", note: b ? "平均 " + b.rhythm.avg + "s · " + b.name.slice(0, 12) : "先选中一份档案", tone: "" },
      { cap: "钩子手法", v: b ? hookLabel(b.hook) : "—", u: "", note: b ? (b.techniques.slice(0, 3).join(" / ") || "未识别手法") : "解析后自动判定", tone: "" },
      { cap: "提示词字数", v: pt.length, u: "字", note: "5 段 · " + (S.ver === "A" ? "15s 整段" : "7.5s ×2 拆段"), tone: "" },
      { cap: "德语口播", v: wordCount(S.voice.join(" ")), u: "词", note: "5 句 · 与画面同步", tone: "" },
      { cap: "合规风险", v: hits.length, u: "处", note: hits.length ? "德国站绝对化 / 价格 / 功效" : "未命中风险词", tone: hits.length ? "is-err" : "" }
    ];
    box.innerHTML = cells.slice(0, 6).map(function (c) {
      return '<div class="kpi ' + c.tone + '">' +
        '<div class="kpi-l">' + esc(c.cap) + "</div>" +
        '<div class="kpi-v">' + esc(c.v) + (c.u ? '<span class="u">' + esc(c.u) + "</span>" : "") + "</div>" +
        '<div class="kpi-cap">' + esc(c.note) + "</div></div>";
    }).join("");
  }

  function renderPresets() {
    var box = $("#ps-presets");
    if (!box) return;
    box.innerHTML = Object.keys(PRESETS).map(function (k) {
      return '<button type="button" class="ps-chip' + (k === S.preset ? " on" : "") + '" data-preset="' + k + '">' + esc(PRESETS[k].label) + "</button>";
    }).join("");
  }

  function renderForm() {
    var box = $("#ps-form");
    if (!box) return;
    box.innerHTML = FIELDS.map(function (f) {
      var val = S.v[f.k] == null ? "" : S.v[f.k];
      var tag = f.area
        ? '<textarea rows="2" data-vk="' + f.k + '" placeholder="' + esc(f.ph) + '">' + esc(val) + "</textarea>"
        : '<input type="text" data-vk="' + f.k + '" value="' + esc(val) + '" placeholder="' + esc(f.ph) + '" />';
      return '<label class="ps-field' + (f.area ? " ps-wide" : "") + '"><span>' + esc(f.label) + "</span>" + tag + "</label>";
    }).join("");
  }

  function renderVoice() {
    var box = $("#ps-voice");
    if (!box) return;
    var hits = scanVoice();
    box.innerHTML = S.voice.map(function (line, i) {
      var bad = hits.filter(function (h) { return h.line === i; });
      return '<label class="ps-vline' + (bad.length ? " ps-bad" : "") + '">' +
        '<span class="ps-vnum">' + NUM[i] + "</span>" +
        '<textarea rows="2" data-vi="' + i + '" spellcheck="false" placeholder="德语口播第 ' + (i + 1) + ' 句">' + esc(line) + "</textarea>" +
        '<span class="ps-vw">' + wordCount(line) + " W</span></label>";
    }).join("");
    renderRisk(hits);
  }

  function renderRisk(hits) {
    var box = $("#ps-risk");
    if (!box) return;
    if (!hits.length) { box.innerHTML = '<span class="ps-okbadge">合规扫描：未命中风险词</span>'; return; }
    var seen = {};
    var chips = hits.filter(function (h) {
      var k = h.cat + h.word;
      if (seen[k]) return false;
      seen[k] = 1; return true;
    }).map(function (h) {
      return '<span class="ps-riskchip" title="' + esc(h.fix) + '">' + esc(h.word) + '<em>' + esc(h.cat) + "</em></span>";
    });
    box.innerHTML = '<span class="ps-riskhd">命中 ' + hits.length + " 处：</span>" + chips.join("");
  }

  function renderBench() {
    document.dispatchEvent(new CustomEvent('wb:archives-changed'));
    var list = $("#ps-blist");
    var cnt = $("#ps-bench-count");
    if (cnt) cnt.textContent = S.bench.list.length + " 份";
    if (!list) return;
    list.innerHTML = S.bench.list.length
      ? S.bench.list.map(function (b) {
        var tg = [hookLabel(b.hook)].concat(b.techniques.slice(0, 3));
        return '<div class="ps-bcard' + (b.id === S.bench.sel ? " on" : "") + '" data-bsel="' + esc(b.id) + '" role="button" tabindex="0">' +
          '<button type="button" class="ps-del" data-bdel="' + esc(b.id) + '" title="删除该档案">✕</button>' +
          "<b>" + esc(b.name) + "</b>" +
          '<div class="ps-bmeta">' + esc(b.at) + " · " + b.beats.length + " 拍 · 平均 " + b.rhythm.avg + "s · " +
          (b.sellingPoints.length ? b.sellingPoints.length + " 卖点" : "无卖点") + "</div>" +
          '<div class="ps-tg">' + tg.map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") +
          (b.assets && (b.assets.images || b.assets.videos)
            ? '<button type="button" class="ps-basset" data-basset="' + esc(b.id) + '" title="查看包内参考素材">素材 ' +
              (b.assets.images ? b.assets.images + " 图" : "") +
              (b.assets.videos ? (b.assets.images ? " / " : "") + b.assets.videos + " 视频" : "") + "</button>"
            : "") +
          "</div></div>";
      }).join("")
      : '<p class="ps-empty">还没有档案。拖一份反推 .md 进上面的虚线框，或选一份反推报告点「导入反推」。</p>';
    renderPattern();
    renderVariants();
    renderKPI();
    /* ③ 模板归纳区的档案勾选要跟着变（否则刚归档的档案在那边看不到，得刷新页面） */
    if (window.PT && window.PT.syncArchives) window.PT.syncArchives();
  }

  /* ── 素材抽屉：从 IndexedDB 取回 Blob，按参考图 / 关键帧 / 视频 / 音频分组展示 ── */
  var _urls = [];
  function releaseUrls() {
    _urls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) { } });
    _urls = [];
  }
  function urlOf(blob) {
    var u = URL.createObjectURL(blob);
    _urls.push(u);
    return u;
  }

  function reportBlock(r) {
    if (!r || (!r.product && !r.scene && !r.audio)) return "";
    return '<div class="ps-drep">' +
      (r.product ? "<p><b>产品</b>" + esc(r.product) + "</p>" : "") +
      (r.scene ? "<p><b>场景</b>" + esc(r.scene) + "</p>" : "") +
      (r.audio ? "<p><b>音频</b>" + esc(r.audio) + "</p>" : "") +
      "</div>";
  }

  function closeDrawer() {
    var box = $("#ps-drawer");
    if (box) box.hidden = true;
    releaseUrls();
  }

  function openDrawer(id) {
    var rec = S.bench.list.filter(function (b) { return b.id === id; })[0];
    var box = $("#ps-drawer"), bd = $("#ps-drawer-bd");
    if (!rec || !box || !bd) return;
    box.hidden = false;
    $("#ps-drawer-title").textContent = rec.name;
    $("#ps-drawer-sub").textContent = "读取本机素材…";
    bd.innerHTML = '<p class="ps-empty">读取中…</p>';

    idbGet(id).then(function (files) {
      releaseUrls();
      var a = rec.assets || {};
      var head = reportBlock(rec.report);
      if (!files || !files.length) {
        $("#ps-drawer-sub").textContent = "无素材";
        bd.innerHTML = head + '<p class="ps-empty">素材没有存在本机（常因清理过浏览数据，或导入时超出配额）。把同一个 zip 再拖一次即可恢复。</p>';
        return;
      }
      var imgs = files.filter(function (f) { return f.kind === "image"; });
      var vids = files.filter(function (f) { return f.kind === "video"; });
      var auds = files.filter(function (f) { return f.kind === "audio"; });
      var refs = imgs.filter(function (f) { return /(^|\/)x_/i.test(f.name); });
      var frames = imgs.filter(function (f) { return /(^|\/)f_/i.test(f.name); });
      var others = imgs.filter(function (f) { return refs.indexOf(f) < 0 && frames.indexOf(f) < 0; });
      var mb = (files.reduce(function (n, f) { return n + (f.blob ? f.blob.size : 0); }, 0) / 1048576).toFixed(1);
      $("#ps-drawer-sub").textContent = [imgs.length ? imgs.length + " 图" : "", vids.length ? vids.length + " 视频" : "",
        auds.length ? "音频" : "", mb + " MB", a.from || ""].filter(Boolean).join(" · ");

      var html = head;
      var group = function (title, list) {
        if (!list.length) return "";
        return '<section class="ps-dgroup"><h4>' + title + ' <em>' + list.length + '</em></h4><div class="ps-thumbs">' +
          list.map(function (f) {
            return '<figure><img loading="lazy" src="' + urlOf(f.blob) + '" alt="' + esc(f.name) + '" /><figcaption>' +
              esc(f.name.split("/").pop()) + "</figcaption></figure>";
          }).join("") + "</div></section>";
      };
      html += group("参考图", refs);
      html += group("关键帧", frames);
      html += group("其它图片", others);
      if (vids.length) {
        html += '<section class="ps-dgroup"><h4>参考视频 <em>' + vids.length + '</em></h4><div class="ps-vids">' +
          vids.map(function (f) {
            return '<figure><video controls preload="metadata" src="' + urlOf(f.blob) + '"></video><figcaption>' +
              esc(f.name.split("/").pop()) + "</figcaption></figure>";
          }).join("") + "</div></section>";
      }
      if (auds.length) {
        html += '<section class="ps-dgroup"><h4>原音轨 <em>' + auds.length + '</em></h4>' +
          auds.map(function (f) {
            return '<div class="ps-audio"><audio controls preload="metadata" src="' + urlOf(f.blob) + '"></audio><span>' +
              esc(f.name.split("/").pop()) + "</span></div>";
          }).join("") + "</section>";
      }
      html += '<div class="ps-dfoot"><button type="button" class="btn sm" data-dcopy="' + esc(rec.id) + '">复制素材清单</button></div>';
      bd.innerHTML = html;
    }).catch(function (e) {
      $("#ps-drawer-sub").textContent = "读取失败";
      bd.innerHTML = '<p class="ps-empty">读取素材失败：' + esc(e && e.message ? e.message : e) + "</p>";
    });
  }

  function copyAssetList(id) {
    idbGet(id).then(function (files) {
      if (!files || !files.length) { say("本机没有该档案的素材"); return; }
      var lines = ["# 参考素材 · " + ((S.bench.list.filter(function (b) { return b.id === id; })[0] || {}).name || ""), ""];
      ["image", "video", "audio"].forEach(function (k) {
        var list = files.filter(function (f) { return f.kind === k; });
        if (!list.length) return;
        lines.push("## " + (k === "image" ? "图片" : k === "video" ? "视频" : "音频"));
        list.forEach(function (f) { lines.push("- " + f.name); });
        lines.push("");
      });
      copyText(lines.join("\n"), "素材清单");
    });
  }

  function renderPattern() {
    var box = $("#ps-pattern");
    if (!box) return;
    var b = selBench();
    if (!b) {
      box.innerHTML = '<div class="ps-pat"><div class="ps-pat-hd"><b>可复用规律</b><em>BENCHMARK</em></div>' +
        '<p class="ps-empty">归档一份反推文档后，这里会列出钩子、取证方式、节奏与收口四类规律。</p></div>';
      return;
    }
    var ps = buildPrinciples(b);
    var tech = b.techniques.length ? b.techniques.join(" / ") : "未识别手法";
    box.innerHTML = '<div class="ps-pat">' +
      '<div class="ps-pat-hd"><b>可复用规律 · ' + ps.length + " 条</b>" +
      "<em>" + (b.beats.length === 1 ? "单镜头 · " : "") + b.beats.length + " 拍 · 钩子『" + esc(hookLabel(b.hook)) + "』· " + esc(tech) + "</em></div>" +
      "<ol>" + ps.map(function (p) { return "<li>" + esc(p.t) + '<span class="k">' + esc(p.k) + "</span></li>"; }).join("") + "</ol>" +
      (b.sellingPoints.length
        ? '<div class="ps-sp">' + b.sellingPoints.map(function (s) {
          return '<span class="badge gray" title="' + esc(s) + '">' + esc(s.slice(0, 14)) + (s.length > 14 ? "…" : "") + "</span>";
        }).join("") + "</div>"
        : "") +
      "</div>";
  }

  function renderAngles() {
    var box = $("#ps-angles");
    if (!box) return;
    box.innerHTML = ANGLES.map(function (a) {
      return '<button type="button" class="ps-ang' + (S.bench.angles[a.k] ? " on" : "") + '" data-ang="' + a.k + '" aria-pressed="' + (S.bench.angles[a.k] ? "true" : "false") + '">' + esc(a.label) + "</button>";
    }).join("");
    syncAngleHint();
  }

  /* 切换角度只同步状态到已有按钮，不重建 DOM —— 重建会抢焦点、也会让连点失效 */
  function syncAngles() {
    $$("#ps-angles .ps-ang").forEach(function (b) {
      var on = !!S.bench.angles[b.dataset.ang];
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    syncAngleHint();
  }

  function syncAngleHint() {
    var hint = $("#ps-ang-hint");
    if (!hint) return;
    var n = ANGLES.filter(function (a) { return S.bench.angles[a.k]; }).length;
    hint.textContent = "已选 " + n + " / " + ANGLES.length + " 个角度";
  }

  /* ══════════ ④ → ⑤ 接入条 ══════════
     ④ 区「→ ⑤」把产品 + 模板送进来；这里负责显示来源、提示①区还缺什么、以及退出接入。 */
  function bridgeBits() {
    var br = S.bridge;
    if (!br) return [];
    var out = [];
    if (br.product) out.push("产品 " + br.product);
    if (br.tplName) {
      var r = bridgeRatio();
      var n = r ? r.length : (br.beats || 0);
      /* 模板名里常自带「7 拍」，别在这里再写一遍 */
      out.push("模板 " + br.tplName + "（" + (br.tplName.indexOf(n + " 拍") >= 0 ? "" : n + " 拍 / ") + (br.span || "—") + "s）");
    }
    if (br.images && br.images.length) out.push("参考图 " + br.images.length + " 张");
    if (br.selling && br.selling.length) out.push("识别卖点 " + br.selling.length + " 条");
    return out;
  }

  function renderBridge() {
    var box = $("#ps-bridge");
    if (!box) return;
    if (!S.bridge) { box.hidden = true; box.innerHTML = ""; return; }
    /* 这 5 项是「看图和模板都推不出来」的信息，只能人补。
       注意：预设里本来就有湿巾语境的默认值，所以除了「空」，还要认出
       「还是预设原文」——产品换成毛衣后，那些湿巾描述其实也是错的。 */
    var presetV = (PRESETS[S.preset] || {}).v || {};
    var empty = [], stale = [];
    ["surface", "surfaceTrace", "heroAction", "flaw", "sounds"].forEach(function (k) {
      var cur = String(S.v[k] || "").trim();
      if (!cur) { empty.push(k); return; }
      if (presetV[k] && cur === String(presetV[k]).trim()) stale.push(k);
    });
    var shortList = function (arr) {
      var names = arr.map(fieldLabel);
      return names.slice(0, 3).join(" / ") + (names.length > 3 ? " 等" : "");
    };
    var presetName = (PRESETS[S.preset] || {}).label || "预设";
    var tip = "";
    if (empty.length && stale.length) {
      tip = '<em class="ps-br-miss">① 区 ' + empty.length + " 项空着、" + stale.length +
        " 项还是「" + esc(presetName) + "」预设（" + esc(shortList(empty.concat(stale))) + "），按新产品改一下</em>";
    } else if (empty.length) {
      tip = '<em class="ps-br-miss">① 区还空 ' + empty.length + " 项（" + esc(shortList(empty)) + "），补齐更准</em>";
    } else if (stale.length) {
      tip = '<em class="ps-br-miss">' + stale.length + " 项还是「" + esc(presetName) + "」预设的写法（" +
        esc(shortList(stale)) + "），建议按新产品改</em>";
    } else {
      tip = '<em class="ps-br-ok">① 区字段齐全</em>';
    }
    /* ① 区产品名与 ④ 不是同一个 → 生成时会用 ① 区，必须说清楚，否则用户以为没接上 */
    var curProd = String(S.v.product || "").trim();
    var brProd = String((S.bridge && S.bridge.product) || "").trim();
    var prodNote = "";
    if (brProd && curProd && curProd !== brProd) {
      prodNote = '<em class="ps-br-miss">① 区产品名是「' + esc(curProd.slice(0, 26)) +
        "」，不是 ④ 的「" + esc(brProd.slice(0, 26)) + "」，将按 ① 区生成</em>";
    }
    box.hidden = false;
    box.innerHTML =
      '<b>已接入 ④</b>' +
      '<span class="ps-br-txt">' + esc(bridgeBits().join(" · ")) + "</span>" +
      '<div class="ps-br-ops">' +
      '<button type="button" class="btn sm" data-brgo="1">去 ④ 换模板</button>' +
      '<button type="button" class="btn sm" data-brclear="1">清除接入</button>' +
      "</div>" +
      '<div class="ps-br-tips">' + prodNote + tip +
      '<em>分镜：' + (apparelMode() ? '服装穿着展示' : '通用产品展示') + '；预设口播不沿用，多余拍位标为待补。参考图需另行上传到视频生成工具。</em></div>';
  }

  /* 接入即换产品：④ 的识别结果写进 ① 区。
     ★ 只覆盖「空着」或「还是当前预设默认值」的字段 —— 预设里全是湿巾语境的文案，
       产品换成毛衣后那些值本身就是错的；但用户手改过的内容一律不动。 */
  function absorbBridgeVars() {
    var br = S.bridge;
    if (!br) return 0;
    var presetV = (PRESETS[S.preset] || {}).v || {};
    var map = {
      product: br.product, material: br.material, scene: br.scene,
      structure: br.structure, accent: br.accent,
      benefit: String(br.benefit || "").trim() || (br.selling || [])[0] || ""
    };
    var n = 0;
    var prev = br._previousApplied || {};
    Object.keys(map).forEach(function (k) {
      var put = String(map[k] == null ? "" : map[k]).trim();
      var cur = String(S.v[k] == null ? "" : S.v[k]).trim();
      var def = String(presetV[k] == null ? "" : presetV[k]).trim();
      if (!br._manual[k] && (!cur || (def && cur === def) || (Object.prototype.hasOwnProperty.call(prev, k) && cur === prev[k]))) {
        S.v[k] = put; br._applied[k] = put; n++;
      }
    });
    if (n) { saveVars(); renderForm(); touchPrompt(); }
    return n;
  }

  function setBridge(br) {
    var next = br == null ? null : normalizeBridge(br);
    if (br != null && !next) { say("无法接入：产品名、模板拍序或时长无效，请重新选择模板"); return false; }
    var previous = S.bridge;
    if (next) {
      next._previousApplied = previous ? previous._applied : {};
      next._manual = Object.assign({}, previous ? previous._manual : {}, next._manual);
      next._applied = {};
    }
    S.bridge = next;
    if (next) { absorbBridgeVars(); delete next._previousApplied; }
    saveBridge();
    S.bench.items = [];
    renderBridge(); renderVariants(); renderKPI();
    return !!S.bridge;
  }

  function renderVariants() {
    var box = $("#ps-vgrid");
    if (!box) return;
    var b = selBench();
    if (!S.bench.items.length) {
      box.innerHTML = '<p class="ps-empty">' + ((b || bridgeRatio())
        ? "选好角度后点「生成变异提示词」——把" + (bridgeRatio() ? "④ 的模板拍序" : "对标的钩子与节奏") +
          "套到当前产品上，输出可直接粘贴的结构化提示词。"
        : "先归档一份对标档案，或在 ④ 区生成方案后点「→ ⑤」把模板接进来。") + "</p>";
      var bt = $("#ps-copy-all");
      if (bt) bt.disabled = true;      // 没有内容时不给可点的「复制全部」
      return;
    }
    box.innerHTML = S.bench.items.map(function (it) {
      var vars = VAR_DEFS.map(function (d) {
        return '<span class="ps-var" title="' + esc(d[1] + "：" + (S.v[d[0]] || "空")) + '">{{' + d[0] + "}}</span>";
      }).join("");
      return '<div class="ps-vcard">' +
        '<div class="ps-vcard-hd"><b>' + esc(it.label) + '</b><span class="ix">V' + String(it.i).padStart(2, "0") + "</span></div>" +
        "<dl>" +
        "<dt>受众</dt><dd>" + esc(it.audience) + "</dd>" +
        "<dt>卖点</dt><dd>" + esc(it.sp.k) + " · " + esc(it.sp.t) + "</dd>" +
        "<dt>角度</dt><dd>" + esc(it.angle) + "</dd>" +
        "<dt>变量</dt><dd>" + vars + "</dd>" +
        "</dl>" +
        "<pre>" + esc(it.text) + "</pre>" +
        '<div class="ps-vcard-ft">' +
        '<button type="button" class="btn sm" data-vcopy="' + it.i + '">复制提示词</button>' +
        '<button type="button" class="btn sm" data-vsplit="' + it.i + '">复制 5 段</button>' +
        "</div></div>";
    }).join("");
    var btnAll = $("#ps-copy-all");
    if (btnAll) btnAll.disabled = false;
  }
  function renderPrompt() {
    var box = $("#ps-prompt");
    if (!box) return;
    var secs = buildSections();
    box.innerHTML = secs.map(function (s) {
      return '<details class="ps-sec" open><summary><b>段 ' + s.n + " · " + esc(s.title) + "</b>" +
        '<span class="ps-sec-ops"><button type="button" class="btn sm" data-seccopy="' + s.n + '">复制本段</button></span></summary>' +
        "<pre>" + esc(s.body) + "</pre></details>";
    }).join("");
    var stat = $("#ps-prompt-stat");
    if (stat) stat.textContent = promptText().length + " 字 · " + (S.ver === "A" ? "A 整段" : "B 拆段");
  }

  function renderChecks() {
    var box = $("#ps-checks");
    if (!box) return;
    box.innerHTML = CHECKS.map(function (c, i) {
      return '<label class="ps-ck"><input type="checkbox" data-ck="' + i + '"' + (S.checks[i] ? " checked" : "") + " /><span>" + esc(c) + "</span></label>";
    }).join("");
    var ck = $("#ps-ck-count");
    if (ck) ck.textContent = Object.keys(S.checks).filter(function (k) { return S.checks[k]; }).length + " / 10";
  }

  function renderAll() {
    renderPresets();
    renderForm();
    renderVoice();
    renderAngles();
    renderBench();
    renderPrompt();
    renderChecks();
    renderBridge();
    renderKPI();
  }

  /* ═════════════════ 事件 ═════════════════ */
  var rafId = 0;
  function touchPrompt() {
    if (S.bench.items.length) { S.bench.items = []; renderVariants(); }
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(function () {
      rafId = 0;
      /* 变量或口播一改，已生成的变异正文就过期了：连同卡片一起清掉，
         避免旧正文被冠上新的产品名导出（源项目同款修复项） */
      renderBridge();
      renderPrompt();
      renderKPI();
    });
  }

  function applyPreset(k) {
    var p = PRESETS[k];
    if (!p) return;
    S.preset = k;
    S.v = Object.assign({}, p.v);
    S.voice = p.voice.slice();
    S.bench.items = [];
    saveVars();
    renderPresets();
    renderForm();
    renderVoice();
    renderVariants();
    touchPrompt();
    say("已切换预设：" + p.label);
  }

  /* ── zip 导入（2026-10-26）────────────────────────────────
     不引第三方库：读中央目录 → store(0) 直取 / deflate(8) 走浏览器原生
     DecompressionStream('deflate-raw')。只取 .md/.markdown/.txt/.text，
     跳过目录、__MACOSX、.DS_Store 与 zip64 条目。
     防解压炸弹：包体 ≤8MB、条目 ≤200、单份 ≤2MB、解压总量 ≤32MB。 */
  var ZIP_LIMIT = { bag: 32 * 1024 * 1024, entry: 24 * 1024 * 1024, total: 160 * 1024 * 1024, count: 400 };

  function mimeOf(name) {
    var ext = (String(name).split(".").pop() || "").toLowerCase();
    return {
      jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif", avif: "image/avif",
      mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime", webm: "video/webm", mkv: "video/x-matroska",
      m4a: "audio/mp4", mp3: "audio/mpeg", wav: "audio/wav", aac: "audio/aac", ogg: "audio/ogg"
    }[ext] || "application/octet-stream";
  }

  /* ── 素材仓：IndexedDB（localStorage 存不下二进制；20 份包的图/视频放这里）──
     key = 档案 id，value = [{name, kind, mime, blob}]。删档案时同步清。 */
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
      rq.onerror = function () { rej(rq.error || new Error("IndexedDB 打开失败")); };
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

  function decodeBytes(u8) {
    var txt = new TextDecoder("utf-8").decode(u8);
    if (txt.indexOf("\uFFFD") < 0) return txt;          // 纯 UTF-8 直接返回
    try { return new TextDecoder("gbk").decode(u8); } catch (e) { return txt; }   // 老压缩工具常见 GBK
  }

  async function unzipEntries(file) {
    if (file.size > ZIP_LIMIT.bag) throw new Error("zip 超过 32MB");
    var buf = new Uint8Array(await file.arrayBuffer());
    var dv = new DataView(buf.buffer);
    var eocd = -1;
    for (var i = buf.length - 22; i >= 0 && i >= buf.length - 22 - 65535; i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error("不是有效的 zip 文件");

    var total = dv.getUint16(eocd + 10, true);
    var off = dv.getUint32(eocd + 16, true);
    var out = [], sum = 0, seen = 0;

    for (var n = 0; n < total; n++) {
      if (off + 46 > buf.length || dv.getUint32(off, true) !== 0x02014b50) break;
      var method = dv.getUint16(off + 10, true);
      var csize = dv.getUint32(off + 20, true);
      var usize = dv.getUint32(off + 24, true);
      var nameLen = dv.getUint16(off + 28, true);
      var extraLen = dv.getUint16(off + 30, true);
      var commentLen = dv.getUint16(off + 32, true);
      var lho = dv.getUint32(off + 42, true);
      var name = decodeBytes(buf.subarray(off + 46, off + 46 + nameLen));
      off += 46 + nameLen + extraLen + commentLen;

      if (/\/$/.test(name)) continue;                                   // 目录项
      if (/(^|\/)(__MACOSX\/|\.DS_Store$)/.test(name)) continue;         // 系统垃圾
      if (seen++ >= ZIP_LIMIT.count) break;
      var ext = (name.split(".").pop() || "").toLowerCase();
      var kind = /^(md|markdown|txt|text)$/.test(ext) ? "text"
        : /^(jpg|jpeg|png|webp|gif|avif)$/.test(ext) ? "image"
          : /^(mp4|mov|webm|mkv|m4v)$/.test(ext) ? "video"
            : /^(m4a|mp3|wav|aac|ogg)$/.test(ext) ? "audio"
              : ext === "json" ? "data" : "";
      if (!kind) continue;                                               // 其它类型不读
      if (csize === 0xffffffff || usize === 0xffffffff) { out.push({ name: name, error: "zip64 条目，暂不支持" }); continue; }
      if (usize > ZIP_LIMIT.entry) { out.push({ name: name, error: "单份超过 24MB" }); continue; }
      if (sum + usize > ZIP_LIMIT.total) { out.push({ name: name, error: "解压总量超 160MB，已跳过" }); continue; }

      var lnameLen = dv.getUint16(lho + 26, true);
      var lextraLen = dv.getUint16(lho + 28, true);
      var head = lho + 30 + lnameLen + lextraLen;
      var data = buf.subarray(head, head + csize);
      var bytes;
      if (method === 0) bytes = data;
      else if (method === 8) {
        if (typeof DecompressionStream !== "function") { out.push({ name: name, error: "当前浏览器不支持 deflate 解压，请先解压再上传" }); continue; }
        var stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        bytes = new Uint8Array(await new Response(stream).arrayBuffer());
      } else { out.push({ name: name, error: "不支持的压缩方式（仅 store / deflate）" }); continue; }

      sum += bytes.length;
      if (kind === "text" || kind === "data") out.push({ name: name, kind: kind, text: decodeBytes(bytes) });
      else out.push({ name: name, kind: kind, mime: mimeOf(name), bytes: bytes });
    }
    return out;
  }

  function readText(file) {
    return new Promise(function (res, rej) {
      var fr = new FileReader();
      fr.onerror = function () { rej(new Error("读取失败")); };
      fr.onload = function () { res(String(fr.result || "")); };
      fr.readAsText(file, "utf-8");
    });
  }

  /* 一段文本 → 拆整库 → 解析 → 入档候选（普通文件与 zip 内文件共用） */
  function ingestText(name, text, rows, recs) {
    if (!String(text || "").trim()) { rows.push({ name: name, note: "空文件", err: true }); return; }
    var chunks;
    try { chunks = splitLibrary(text) || [text]; }
    catch (e) { rows.push({ name: name, note: e.message, err: true }); return; }
    var base = String(name).split(" › ").pop().replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
    var kept = 0, firstRec = null;
    chunks.forEach(function (raw, i) {
      var hasH1 = /^#\s+\S/m.test(raw);
      var label = chunks.length > 1 ? base + " #" + (i + 1) : base;
      var rec = parseBench(raw, hasH1 ? "" : label);
      rec.name = smartName(rec, label);
      if (!rec.beats.length) { rows.push({ name: name, note: "未识别到分镜（需 1 个带秒点的小节，或 ≥2 个内容小节）", err: true }); return; }
      /* 同原文已在库：沿用旧 id / 素材 / 报告摘要。
         否则每次导入都换 id → 旧 id 的 IndexedDB 素材成孤儿（白占空间），
         且「重拖同一个 zip 恢复素材」会挂到新 id 上、旧记录永远没人删。 */
      var old = S.bench.list.filter(function (b) { return b.raw === rec.raw; })[0] ||
        recs.filter(function (b) { return b.raw === rec.raw; })[0];
      if (old) {
        rec.id = old.id;
        if (!rec.assets && old.assets) rec.assets = old.assets;
        if (!rec.report && old.report) rec.report = old.report;
      }
      var exists = !!old;
      var incoming = recs.filter(function (r) {
        return !S.bench.list.some(function (b) { return b.raw === r.raw; });
      }).length;
      if (!exists && S.bench.list.length + incoming >= MAX_BENCH) {
        rows.push({ name: name, note: "档案已满（20 份），未导入；请先导出备份并删除旧档案", err: true });
        return;
      }
      kept++;
      if (!firstRec) firstRec = rec;
      recs.push(rec);
      rows.push({
        name: chunks.length > 1 ? name + " → " + rec.name.slice(0, 16) : name,
        note: rec.beats.length + " 拍 / " + rec.sellingPoints.length + " 卖点"
      });
    });
    if (!kept && !chunks.length) rows.push({ name: name, note: "空内容", err: true });
    return firstRec;
  }

  /* 把包内图 / 视频 / 音频挂到刚入档的档案上：元信息进 localStorage，二进制进 IndexedDB */
  function attachAssets(rec, assets, reportJson, fromName, rows) {
    var imgs = assets.filter(function (a) { return a.kind === "image"; });
    var vids = assets.filter(function (a) { return a.kind === "video"; });
    var auds = assets.filter(function (a) { return a.kind === "audio"; });
    var refs = imgs.filter(function (a) { return /(^|\/)x_/i.test(a.name); }).length;
    var bytes = assets.reduce(function (n, a) { return n + (a.bytes ? a.bytes.length : 0); }, 0);

    rec.assets = {
      images: imgs.length, videos: vids.length, audios: auds.length,
      refs: refs, frames: imgs.length - refs,
      sample: imgs.slice(0, 6).map(function (a) { return a.name.split("/").pop(); }),
      from: fromName, bytes: bytes
    };
    if (reportJson) {
      rec.report = {
        product: cleanOneLine(reportJson.product_summary, 160),
        scene: cleanOneLine(reportJson.scene_summary, 160),
        audio: reportJson.audio && reportJson.audio.note ? cleanOneLine(reportJson.audio.note, 120) : ""
      };
    }

    var blobs = assets.map(function (a) {
      var mime = a.mime || mimeOf(a.name);
      return { name: a.name, kind: a.kind, mime: mime, blob: new Blob([a.bytes], { type: mime }) };
    });
    idbPut(rec.id, blobs).then(function () {
      if (S.bench.sel === rec.id) renderBench();
    }).catch(function () {
      say("素材存本机失败（可能超出浏览器配额），本次仅在内存中可用");
    });

    var last = rows[rows.length - 1];
    if (last && !last.err) {
      var tail = [];
      if (imgs.length) tail.push(imgs.length + " 图");
      if (vids.length) tail.push(vids.length + " 视频");
      if (auds.length) tail.push("音频");
      if (tail.length) last.note += " · 素材 " + tail.join(" / ");
    }
  }

  async function readFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var rows = [], recs = [];

    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      var lower = String(f.name || "").toLowerCase();
      var zip = /\.zip$/i.test(lower) || /zip/.test(String(f.type || ""));

      if (zip) {
        var entries;
        try { entries = await unzipEntries(f); }
        catch (e) { rows.push({ name: f.name, note: e.message, err: true }); continue; }
        if (!entries.length) { rows.push({ name: f.name, note: "包内没有可识别的文件（md / txt / 图片 / 视频）", err: true }); continue; }
        var texts = [], assets = [], reportJson = null;
        entries.forEach(function (en) {
          if (en.error) { rows.push({ name: f.name + " › " + en.name, note: en.error, err: true }); return; }
          if (en.kind === "text") { texts.push(en); return; }
          if (en.kind === "data") {
            if (/(^|\/)report\.json$/i.test(en.name)) { try { reportJson = JSON.parse(en.text); } catch (e) { reportJson = null; } }
            return;
          }
          assets.push(en);
        });
        var mark = recs.length;
        texts.forEach(function (en) { ingestText(f.name + " › " + en.name, en.text, rows, recs); });
        if (assets.length) {
          var owner = recs[mark] || recs[recs.length - 1] || null;
          if (owner) attachAssets(owner, assets, reportJson, f.name, rows);
          else rows.push({ name: f.name, note: "包内有 " + assets.length + " 个图片/视频，但没有可入档的 md，素材未保存", err: true });
        }
        continue;
      }

      if (!/\.(md|markdown|txt|text)$/.test(lower)) {
        rows.push({ name: f.name, note: "类型不支持（支持 .md / .txt / .zip）", err: true });
        continue;
      }
      if (f.size > 512 * 1024) { rows.push({ name: f.name, note: "超过 512KB", err: true }); continue; }
      var txt;
      try { txt = await readText(f); }
      catch (e) { rows.push({ name: f.name, note: "读取失败", err: true }); continue; }
      ingestText(f.name, txt, rows, recs);
    }

    var box = $("#ps-files");
    var MAX_ROWS = 24;
    if (box) {
      var shown = rows.slice(0, MAX_ROWS).map(function (r) {
        return '<div class="ps-file' + (r.err ? " err" : "") + '"><span>' + esc(r.name) + "</span><em>" + esc(r.note) + "</em></div>";
      });
      if (rows.length > MAX_ROWS) {
        shown.push('<div class="ps-file"><span>…</span><em>另有 ' + (rows.length - MAX_ROWS) + " 条未显示</em></div>");
      }
      box.innerHTML = shown.join("");
    }
    if (!recs.length) { say("没有可解析的文件"); return; }
    recs.forEach(function (r) {
      var dup = -1;
      S.bench.list.forEach(function (b, i) { if (b.raw && r.raw && b.raw === r.raw) dup = i; });
      if (dup > -1) S.bench.list[dup] = r; else S.bench.list.unshift(r);
    });
    S.bench.list = S.bench.list.slice(0, MAX_BENCH);
    var first = S.bench.list.filter(function (b) { return b.raw === recs[0].raw; })[0];
    S.bench.sel = first ? first.id : S.bench.list[0].id;
    S.bench.items = [];
    saveBench();
    renderBench();
    var hint = $("#ps-hint");
    if (hint) hint.textContent = "已从 " + recs.length + " 份文档归档 · 共 " + recs.reduce(function (a, r) { return a + r.beats.length; }, 0) + " 拍";
    say("已归档 " + recs.length + " 份");
  }

  /* 整库往返：识别 `## 原始反推文档` + 围栏，按档拆回 */
  function splitLibrary(text) {
    var lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      if (!/^##\s*原始反推文档\s*$/.test(lines[i])) continue;
      var j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      var fence = (lines[j] || "").match(/^(`{3,})(?:markdown|md)?\s*$/);
      if (!fence) continue;
      var begin = ++j;
      while (j < lines.length && lines[j].trim() !== fence[1]) j++;
      if (j === lines.length) throw new Error("整库文档围栏未闭合，请检查导出文件");
      var raw = lines.slice(begin, j).join("\n").trim();
      if (raw) out.push(raw);
      i = j;
    }
    return out.length ? out : null;
  }

  function parseNow() {
    var rawEl = $("#ps-raw"), titleEl = $("#ps-title");
    var raw = (rawEl && rawEl.value || "").trim();
    if (raw.length < 30) { say("反推文档太短，至少 30 字"); return; }
    if (new Blob([raw]).size > 512 * 1024) { say("超过 512KB"); return; }
    var rec = parseBench(raw, titleEl ? titleEl.value : "");
    rec.name = smartName(rec, "粘贴文档");
    if (!rec.beats.length) { say("未识别到分镜（需 1 个带秒点的小节，或 ≥2 个内容小节）"); return; }
    if (!addBench(rec)) return;
    var hint = $("#ps-hint");
    if (hint) hint.textContent = "已归档 · " + rec.beats.length + " 拍 / " + rec.sellingPoints.length + " 卖点 / " + rec.techniques.length + " 手法";
    if (rawEl) rawEl.value = "";
    if (titleEl) titleEl.value = "";
    say("已解析：" + rec.name);
  }

  function bind() {
    var root = $("#view-prompt");
    if (!root) return;

    /* 变量输入：委托 input 事件，避免重绘抢焦点 */
    root.addEventListener("input", function (e) {
      var t = e.target;
      if (t.dataset && t.dataset.vk) {
        S.v[t.dataset.vk] = t.value;
        markManual(t.dataset.vk);
        saveVars();
        touchPrompt();      // 旧变异由 touchPrompt 统一清空并重绘
        return;
      }
      if (t.dataset && t.dataset.vi != null && t.dataset.vi !== "") {
        var line = Number(t.dataset.vi);
        S.voice[line] = t.value;
        saveVars();
        var w = t.parentNode.querySelector(".ps-vw");
        if (w) w.textContent = wordCount(t.value) + " W";
        var hits = scanVoice();
        var lbl = t.closest(".ps-vline");
        if (lbl) lbl.classList.toggle("ps-bad", hits.some(function (h) { return h.line === line; }));
        renderRisk(hits);
        touchPrompt();
        return;
      }
    });

    root.addEventListener("change", function (e) {
      var t = e.target;
      if (t.id === "ps-ver") { S.ver = t.value === "B" ? "B" : "A"; saveVars(); renderPrompt(); renderKPI(); return; }
      if (t.id === "ps-file") { readFiles(t.files); t.value = ""; return; }
      if (t.dataset && t.dataset.ck != null) {
        S.checks[t.dataset.ck] = !!t.checked;
        try { localStorage.setItem(KEY.checks, JSON.stringify(S.checks)); } catch (err) { }
        renderChecks();
        renderKPI();
      }
    });

    root.addEventListener("click", function (e) {
      var el;
      if ((el = e.target.closest("[data-preset]"))) { applyPreset(el.dataset.preset); return; }
      if ((el = e.target.closest("[data-basset]"))) { openDrawer(el.dataset.basset); return; }
      if (e.target.closest("[data-dclose]")) { closeDrawer(); return; }
      if ((el = e.target.closest("[data-dcopy]"))) { copyAssetList(el.dataset.dcopy); return; }
      if (e.target.closest("[data-brclear]")) {
        setBridge(null);
        say("已清除 ④ 接入，⑤ 回到用 ② 的档案骨架");
        return;
      }
      if (e.target.closest("[data-brgo]")) {
        var gen = document.getElementById("ps-sec-gen");
        if (gen && gen.scrollIntoView) gen.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if ((el = e.target.closest("[data-bdel]"))) {
        var id = el.dataset.bdel;
        S.bench.list = S.bench.list.filter(function (x) { return x.id !== id; });
        idbDel(id);
        if (S.bench.sel === id) S.bench.sel = S.bench.list[0] ? S.bench.list[0].id : null;
        S.bench.items = [];
        saveBench(); renderBench(); renderKPI(); say("已删除档案");
        return;
      }
      if ((el = e.target.closest("[data-bsel]"))) {
        S.bench.sel = el.dataset.bsel;
        S.bench.items = [];
        renderBench(); renderKPI();
        return;
      }
      if ((el = e.target.closest("[data-ang]"))) {
        var k = el.dataset.ang;
        S.bench.angles[k] = !S.bench.angles[k];
        if (!ANGLES.some(function (a) { return S.bench.angles[a.k]; })) {
          ANGLES.forEach(function (a) { S.bench.angles[a.k] = true; });
        }
        syncAngles();
        return;
      }
      if ((el = e.target.closest("[data-vcopy]"))) {
        var it = S.bench.items[Number(el.dataset.vcopy) - 1];
        if (it) copyText(it.text, "V" + it.i + " 提示词");
        return;
      }
      if ((el = e.target.closest("[data-vsplit]"))) {
        var it2 = S.bench.items[Number(el.dataset.vsplit) - 1];
        if (it2) copyText([it2.s1, it2.s2, it2.s3, it2.s4, it2.s5].map(function (s, i) { return "【段 " + (i + 1) + "】\n" + s; }).join("\n\n"), "5 段");
        return;
      }
      if ((el = e.target.closest("[data-seccopy]"))) {
        var sec = buildSections().filter(function (s) { return s.n === el.dataset.seccopy; })[0];
        if (sec) copyText(sec.body, "段 " + sec.n);
        return;
      }
      if (e.target.closest("#ps-gen")) { genVariants(); return; }
      if (e.target.closest("#ps-copy-all")) {
        if (!S.bench.items.length) { say("先生成变异提示词"); return; }
        copyText(variantMD(), "变异提示词 Markdown");
        return;
      }
      if (e.target.closest("#ps-copy-prompt")) { copyText(promptText(), "5 段式提示词"); return; }
      if (e.target.closest("#ps-copy-voice")) { copyText(voiceText(), "德语口播 5 句"); return; }
      if (e.target.closest("#ps-export-md")) {
        downloadText("提示词-" + (S.v.product || "未命名") + "-" + new Date().toISOString().slice(0, 10) + ".md",
          "# " + (S.v.product || "提示词") + "\n\n" + promptText() + "\n\n## 德语口播\n\n" + voiceText() + "\n");
        return;
      }
      if (e.target.closest("#ps-export-all")) {
        if (!S.bench.list.length) { say("还没有档案"); return; }
        downloadText("对标档案库-" + new Date().toISOString().slice(0, 10) + ".md", benchAllMD());
        say("已导出 " + S.bench.list.length + " 份档案");
        return;
      }
      if (e.target.closest("#ps-parse")) { parseNow(); return; }
      if (e.target.closest("#ps-demo")) {
        var rawEl = $("#ps-raw"), tEl = $("#ps-title");
        if (rawEl) rawEl.value = DEMO;
        if (tEl) tEl.value = "德国清洁湿巾 15s 爆款拆解";
        say("已填入示例，可直接点「解析并归档」");
        return;
      }
      if (e.target.closest("#ps-drop") || e.target.closest("#ps-pick")) {
        var fi = $("#ps-file");
        if (fi) fi.click();
        return;
      }
      if (e.target.closest("#ps-rv-import")) { importReverse(); return; }
      if (e.target.closest("#ps-refresh")) { refresh(); return; }
      if ((el = e.target.closest("[data-jump]"))) {
        var target = $("#ps-" + el.dataset.jump);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        var dr = $("#ps-drawer");
        if (dr && !dr.hidden) closeDrawer();
      }
    });

    root.addEventListener("keydown", function (e) {
      if ((e.key === "Enter" || e.key === " ") && e.target.id === "ps-drop") { e.preventDefault(); var fi = $("#ps-file"); if (fi) fi.click(); }
    });

    /* 拖拽入库：只在录入面板内生效，拖到页面其它位置不劫持 */
    var drop = $("#ps-drop");
    if (drop) {
      var depth = 0;
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          e.preventDefault(); e.stopPropagation();
          if (ev === "dragenter") depth++;
          drop.classList.add("over");
        });
      });
      drop.addEventListener("dragleave", function (e) {
        e.preventDefault(); e.stopPropagation();
        depth = Math.max(0, depth - 1);
        if (!depth) drop.classList.remove("over");
      });
      drop.addEventListener("drop", function (e) {
        e.preventDefault(); e.stopPropagation();
        depth = 0; drop.classList.remove("over");
        readFiles(e.dataTransfer && e.dataTransfer.files);
      });
    }
  }

  /* ═════════════════ 启动 / 对外接口 ═════════════════ */
  function refresh() {
    loadState();
    S.bench.items = [];
    renderAll();
    var up = $("#ps-updated");
    if (up) up.textContent = "已刷新 " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
    loadReverseList();
  }

  function mount() {
    if (S.ready) return;
    loadState();
    bind();
    renderAll();
    loadReverseList();
    S.ready = true;
  }

  /* app.js 的 switchView 调用；首次进入才初始化，之后只补刷反推列表 */
  window.PS_load = function () {
    mount();
    renderBridge();
    loadReverseList();
    if (window.PT_load) window.PT_load();      // ③ 模板归纳 / ④ 产品图与方案（ps_pipeline.js）
  };
  window.PS = {
    load: window.PS_load,
    refresh: refresh,
    importFiles: function (files) { return readFiles(files); },   // 上传/拖拽/zip 的统一入口（测试与外部调用）
    unzip: function (file) { return unzipEntries(file); },
    importReverseMarkdown: function (md, name) {
      mount();
      var rec = parseBench(md, name || "");
      rec.name = smartName(rec, name || "");
      if (!rec.beats.length) return false;
      return addBench(rec);
    },
    state: S,
    /* ④ 产品图方案 → ⑤ 变异：{product, material, ..., ratio[], tplName, images[]} */
    setBridge: setBridge,
    normalizeBridge: normalizeBridge,
    isPresetVoice: isPresetVoice,
    bridge: function () { return S.bridge; },
    /* 产品图识别结果 → ① 区变量（只写进已知字段，未知键丢弃） */
    applyVars: function (patch) {
      var n = 0;
      Object.keys(patch || {}).forEach(function (k) {
        var def = VAR_DEFS.filter(function (d) { return d[0] === k; })[0];
        if (!def) return;
        S.v[k] = String(patch[k] == null ? "" : patch[k]);
        markManual(k);
        n++;
      });
      if (!n) return 0;
      saveVars();
      renderForm();
      touchPrompt();
      renderKPI();
      return n;
    }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
