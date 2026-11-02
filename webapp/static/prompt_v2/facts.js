/* ============================================================
   facts.js · 「已确认事实」结构化（2026-10-30）
   —— 把 ② 里那段自由文本拆成可逐条勾选的事实条，再原样序列化回文本，
      后端 prompt_optimizer.py 的 facts 字段口径完全不变（仍是字符串）。
   只做四件事：解析、判重 / 判冲突 / 判无依据、整理、序列化。
   不碰 DOM，可在 node 里直接跑单测。
   ============================================================ */
(function (root) {
  'use strict';

  /* 同一类允许出现多条；其余同类多条即视为“重复/冲突”候选 */
  var MULTI = { '卖点': 1, '其他': 1 };

  /* 前缀归一：扫描器写的是「可见卖点：」「可见材质：」 */
  var KEYMAP = {
    '可见卖点': '卖点', '卖点': '卖点', '核心卖点': '卖点',
    '可见材质': '材质', '材质': '材质', '面料': '材质', '成分': '材质',
    '可见场景': '场景', '场景': '场景', '拍摄场景': '场景',
    '颜色': '颜色', '色': '颜色', '配色': '颜色',
    '品类': '品类', '类目': '品类', '产品': '品类',
    '人群': '人群', '受众': '人群', '目标人群': '人群',
    '尺码': '尺码', '尺寸': '尺码', '版型': '版型'
  };
  var KEYS = ['品类', '材质', '颜色', '场景', '人群', '尺码', '版型', '卖点', '其他'];

  /* 不可视觉核验的功效/参数类说法：标出来，由人决定留不留 */
  var WEAK = /显瘦|遮肉|显高|拉长|减龄|保暖|透气|清凉|免烫|不起球|不易起球|不缩水|不掉色|不褪色|亲肤|抗菌|除味|防水|防风|速干|防晒|显白/;

  var seq = 0;
  function nid() { seq += 1; return 'f' + seq; }

  function norm(v) {
    return String(v == null ? '' : v)
      .replace(/[（(].*?[)）]/g, '')      /* 括号里的补充不影响判重 */
      .replace(/[\s、，,。;；/|]+/g, '')
      .toLowerCase();
  }

  function pickKey(raw) {
    var k = String(raw || '').trim();
    if (!k) return '';
    if (KEYMAP[k]) return KEYMAP[k];
    for (var i = 0; i < KEYS.length; i++) if (k.indexOf(KEYS[i]) >= 0) return KEYS[i];
    return '';
  }

  /* 文本 → 行。认识「键：值」，其余整行算「其他」 */
  function parse(text) {
    var rows = [];
    String(text == null ? '' : text).split(/\r?\n/).forEach(function (line) {
      var s = line.trim();
      if (!s) return;
      var m = s.match(/^([^：:]{1,8})[：:]\s*(.+)$/);
      if (m) {
        rows.push({ id: nid(), key: pickKey(m[1]) || '其他', value: m[2].trim(), raw: s });
      } else {
        rows.push({ id: nid(), key: '其他', value: s, raw: s });
      }
    });
    return rows;
  }

  /* 行 → 文本（键名统一用短名，后端只按标点切分，不挑前缀） */
  function serialize(rows) {
    return (rows || [])
      .filter(function (r) { return r && String(r.value || '').trim(); })
      .map(function (r) {
        var v = String(r.value).trim();
        return (r.key && r.key !== '其他') ? (r.key + '：' + v) : v;
      })
      .join('\n');
  }

  /* 判重 / 判冲突 / 判无依据：返回带 st 的行 + 计数 */
  function analyze(rows) {
    var list = (rows || []).map(function (r) { return { id: r.id, key: r.key, value: r.value, src: r.src || '', raw: r.raw, st: 'ok' }; });
    var byKey = {};
    list.forEach(function (r) {
      if (!r.value) { r.st = 'empty'; return; }
      (byKey[r.key] = byKey[r.key] || []).push(r);
    });

    Object.keys(byKey).forEach(function (key) {
      var group = byKey[key];
      if (MULTI[key]) {
        /* 多条合法：只把完全重复的挑出来 */
        var seen = {};
        group.forEach(function (r) {
          var n = norm(r.value);
          if (!n) return;
          if (seen[n]) r.st = 'dup'; else seen[n] = 1;
        });
        return;
      }
      /* 单值字段：同义（互为子串）算重复，不同值算冲突 */
      for (var i = 0; i < group.length; i++) {
        for (var j = i + 1; j < group.length; j++) {
          var a = norm(group[i].value), b = norm(group[j].value);
          if (!a || !b) continue;
          if (a === b || a.indexOf(b) >= 0 || b.indexOf(a) >= 0) {
            if (group[j].st !== 'dup') group[j].st = 'dup';
          } else {
            group[i].st = 'conflict'; group[j].st = 'conflict';
          }
        }
      }
    });

    list.forEach(function (r) {
      if (r.st === 'ok' && WEAK.test(String(r.value))) r.st = 'weak';
    });

    var counts = { total: list.length, ok: 0, dup: 0, conflict: 0, weak: 0, empty: 0 };
    list.forEach(function (r) { counts[r.st] = (counts[r.st] || 0) + 1; });
    return { rows: list, counts: counts };
  }

  /* 去重：同组互为子串的视为同一事实，保留信息更长的那条（针织面料 > 针织） */
  function dedupe(rows) {
    var list = analyze(rows).rows;
    var drop = {};
    var byKey = {};
    list.forEach(function (r) { if (r.value) (byKey[r.key] = byKey[r.key] || []).push(r); });

    Object.keys(byKey).forEach(function (key) {
      var group = byKey[key];
      if (MULTI[key]) {
        var seen = {};
        group.forEach(function (r) {
          var n = norm(r.value);
          if (!n) return;
          if (seen[n]) drop[r.id] = 1; else seen[n] = 1;
        });
        return;
      }
      var kept = [];
      group.forEach(function (r) {
        var n = norm(r.value);
        var host = null;
        for (var i = 0; i < kept.length; i++) {
          var kn = norm(kept[i].value);
          if (kn === n || kn.indexOf(n) >= 0 || n.indexOf(kn) >= 0) { host = kept[i]; break; }
        }
        if (!host) { kept.push(r); return; }
        if (n.length > norm(host.value).length) {
          drop[host.id] = 1;
          kept[kept.indexOf(host)] = r;
        } else {
          drop[r.id] = 1;
        }
      });
    });
    /* 跨字段的完全同文：留带字段名的那条（「其他」是被识别失败的行） */
    var exact = {};
    list.forEach(function (r) {
      if (drop[r.id] || !r.value) return;
      var n = norm(r.value);
      if (!n) return;
      var seen = exact[n];
      if (!seen) { exact[n] = r; return; }
      if (seen.key === '其他' && r.key !== '其他') { drop[seen.id] = 1; exact[n] = r; }
      else drop[r.id] = 1;
    });
    return list.filter(function (r) { return !drop[r.id]; });
  }

  /* 一键整理：先去重，再对冲突组各留第一条（保留用户先写的那个） */
  function tidy(rows) {
    var list = dedupe(rows);
    var seenKey = {};
    return list.filter(function (r) {
      if (r.st !== 'conflict') return true;
      if (seenKey[r.key]) return false;
      seenKey[r.key] = 1;
      return true;
    });
  }

  /* 冲突组：给 UI 画“留哪条”的按钮 */
  function conflicts(rows) {
    var groups = {};
    (rows || []).forEach(function (r) {
      if (r.st === 'conflict') (groups[r.key] = groups[r.key] || []).push(r);
    });
    return Object.keys(groups).map(function (k) { return { key: k, rows: groups[k] }; });
  }

  var API = {
    KEYS: KEYS.slice(), MULTI: MULTI, WEAK: WEAK,
    parse: parse, serialize: serialize, analyze: analyze,
    dedupe: dedupe, tidy: tidy, conflicts: conflicts, norm: norm, nid: nid
  };
  root.FACTS = API;
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : globalThis);
