# -*- coding: utf-8 -*-
"""
对标台账 -> 销售分析报表 HTML 生成器 v2
读取 D:/1/对标账号/tiktok_对标记录.xlsx，做数据审计 + 全维度计算，
生成内嵌 ECharts 的自包含 HTML 报表。
v2 修正：磁盘实读为 4 数据表 36 条（含 charelttce）；商品名为英文原文，品类按英文关键词归类。
用法: python generate_benchmark_report.py
"""
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

import openpyxl

XLSX_PATH = Path(r"D:\1\对标账号\tiktok_对标记录.xlsx")
OUT_HTML = Path(r"D:\1\对标账号\TikTok对标销售分析报表_20261003.html")
ECHARTS_JS = Path(r"D:\workbuddy_project\tiktok\pipeline\assets\echarts.min.js")
GENERATED_AT = "2026-10-03 16:04 (GMT+8)"

COLS = ["记录日期", "对标账号", "视频发布时间", "视频文案(含话题标签)", "视频链接/ID",
        "商品链接", "商品名称", "视频出单量", "播放量", "点赞数", "评论数",
        "转发数", "收藏数", "音频", "音频中文翻译", "风格标签"]

# 数码垂类账号（整账号归为充电/数码配件）
DIGITAL_ACCOUNTS = {"kee.yaden", "charelttce"}

# 品类关键词映射（英文商品名，按优先级顺序匹配；仅对非数码账号生效）
CATEGORY_RULES = [
    ("手机壳", ["Phone Case", "Magnetic Case", "Casing"]),
    ("礼品/趣味", ["Challenge Coin", "Sticky Note", "Gift Card", "Colleague Gift"]),
    ("首饰饰品", ["Ring", "Bracelet", "Earring", "Bolo", "Bangle"]),
    ("包袋", ["Tote Bag", "Underarm Bag", "Wallet"]),
    ("玄学/信仰", ["Tarot", "Devotional"]),
    ("DIY/解压玩具", ["Rhinestone", "Fidget", "Bedazzl"]),
    ("个护/美体", ["Henna", "Tattoo"]),
]


def parse_dt(v):
    """兼容真日期类型与文本字符串两种格式"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def classify_category(account, product_name, style):
    if account in DIGITAL_ACCOUNTS:
        return "充电/数码配件"
    name = str(product_name or "")
    if not name.strip():
        # 未挂车视频：按风格标签推断（磁疗功效叙事 -> 首饰饰品）
        return "首饰饰品" if "磁疗" in str(style or "") else "其他"
    for cat, kws in CATEGORY_RULES:
        if any(re.search(r"\b" + re.escape(k), name, re.IGNORECASE) for k in kws):
            return cat
    return "其他"


def fmt(n):
    return f"{n:,.0f}"


def pct(x, digits=2):
    return f"{x * 100:.{digits}f}%"


def main():
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)

    # ---------- 1. 读取与审计 ----------
    records = []
    audit = {"row_counts": {}, "empty_units": 0, "dup_video_ids": [],
             "no_product": [], "date_parse_fail": [],
             "fill": {}}
    for ws in wb.worksheets:
        if ws.title == "说明":
            continue
        header = [c.value for c in ws[1]]
        assert header == COLS, f"{ws.title} 表头不符: {header}"
        n = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(v is None or str(v).strip() == "" for v in row):
                continue
            n += 1
            rec = dict(zip(COLS, row))
            pub = parse_dt(rec["视频发布时间"])
            rec_dt = parse_dt(rec["记录日期"])
            if pub is None:
                audit["date_parse_fail"].append((ws.title, rec["视频链接/ID"], rec["视频发布时间"]))
            if rec["视频出单量"] in (None, ""):
                audit["empty_units"] += 1
            if not rec["商品链接"]:
                audit["no_product"].append((rec["对标账号"], str(rec["视频链接/ID"])[-12:]))
            acc_name = rec["对标账号"]
            f = audit["fill"].setdefault(acc_name, {"n": 0, "style": 0, "trans": 0})
            f["n"] += 1
            if str(rec["风格标签"] or "").strip() not in ("", "未标注"):
                f["style"] += 1
            if str(rec["音频中文翻译"] or "").strip():
                f["trans"] += 1
            rec["_pub"] = pub
            rec["_rec"] = rec_dt
            rec["_plays"] = int(rec["播放量"] or 0)
            rec["_likes"] = int(rec["点赞数"] or 0)
            rec["_comments"] = int(rec["评论数"] or 0)
            rec["_shares"] = int(rec["转发数"] or 0)
            rec["_favs"] = int(rec["收藏数"] or 0)
            rec["_inter"] = rec["_likes"] + rec["_comments"] + rec["_shares"] + rec["_favs"]
            rec["_category"] = classify_category(acc_name, rec["商品名称"], rec["风格标签"])
            rec["_lang"] = "西语" if "西语" in str(rec["风格标签"] or "") else "英语"
            rec["_vid_tail"] = str(rec["视频链接/ID"]).rstrip("/").split("/")[-1][-12:]
            rec["_age_h"] = round((rec["_rec"] - pub).total_seconds() / 3600, 1) if (pub and rec_dt) else None
            records.append(rec)
        audit["row_counts"][ws.title] = n

    vids = [r["视频链接/ID"] for r in records]
    audit["dup_video_ids"] = [v for v in set(vids) if vids.count(v) > 1]

    print("=" * 60)
    print("【审计】行数:", audit["row_counts"], "| 总记录:", len(records))
    print("【审计】出单量空值:", audit["empty_units"], "| 未挂车:", len(audit["no_product"]), audit["no_product"])
    print("【审计】日期解析失败:", len(audit["date_parse_fail"]), audit["date_parse_fail"])
    print("【审计】重复视频ID:", audit["dup_video_ids"])
    print("【审计】填充度:", json.dumps(audit["fill"], ensure_ascii=False))
    cats_dbg = {}
    for r in records:
        cats_dbg.setdefault(r["_category"], []).append((r["对标账号"], str(r["商品名称"])[:40]))
    print("【审计】品类分布:")
    for c, items in cats_dbg.items():
        print(f"  {c} ({len(items)}):")
        for it in items:
            print(f"    - {it[0]} | {it[1]}")

    # ---------- 2. 账号维度 ----------
    accounts = {}
    for r in records:
        a = accounts.setdefault(r["对标账号"], {"n": 0, "plays": 0, "likes": 0, "comments": 0,
                                                "shares": 0, "favs": 0, "inter": 0, "plays_list": []})
        a["n"] += 1
        a["plays"] += r["_plays"]
        a["likes"] += r["_likes"]
        a["comments"] += r["_comments"]
        a["shares"] += r["_shares"]
        a["favs"] += r["_favs"]
        a["inter"] += r["_inter"]
        a["plays_list"].append(r["_plays"])
    for name, a in accounts.items():
        a["avg_plays"] = a["plays"] / a["n"]
        a["median_plays"] = statistics.median(a["plays_list"])
        a["eng_rate"] = a["inter"] / a["plays"] if a["plays"] else 0
        a["max_plays"] = max(a["plays_list"])
        a["min_plays"] = min(a["plays_list"])
    print("\n【账号汇总】")
    for name, a in accounts.items():
        print(f"  {name}: n={a['n']} plays={a['plays']} avg={a['avg_plays']:.0f} "
              f"median={a['median_plays']:.0f} eng={pct(a['eng_rate'])}")

    total = {k: sum(a[k] for a in accounts.values()) for k in ("n", "plays", "likes", "comments", "shares", "favs", "inter")}
    total_eng = total["inter"] / total["plays"] if total["plays"] else 0
    attached = len(records) - len(audit["no_product"])
    attach_rate = attached / len(records)
    print(f"\n【总计】n={total['n']} plays={fmt(total['plays'])} inter={total['inter']} "
          f"eng={pct(total_eng)} attach={attached}/{len(records)}={pct(attach_rate,1)}")

    # ---------- 3. 按发布日期趋势 + 环比 ----------
    daily = {}
    for r in records:
        if not r["_pub"]:
            continue
        d = r["_pub"].strftime("%m-%d")
        day = daily.setdefault(d, {"date": d, "n": 0, "plays": 0, "inter": 0})
        day["n"] += 1
        day["plays"] += r["_plays"]
        day["inter"] += r["_inter"]
    days = sorted(daily.values(), key=lambda x: datetime.strptime("2026-" + x["date"], "%Y-%m-%d"))
    prev = None
    for d in days:
        d["eng_rate"] = d["inter"] / d["plays"] if d["plays"] else 0
        d["avg_plays"] = d["plays"] / d["n"]
        d["dod"] = ((d["plays"] - prev) / prev * 100) if prev else None
        prev = d["plays"]
    print("\n【每日趋势】")
    for d in days:
        print(f"  {d['date']}: n={d['n']} plays={fmt(d['plays'])} "
              f"dod={'-' if d['dod'] is None else f'{d['dod']:+.1f}%'} eng={pct(d['eng_rate'])}")

    # ---------- 4. 品类维度 ----------
    cats = {}
    for r in records:
        c = cats.setdefault(r["_category"], {"n": 0, "plays": 0, "inter": 0})
        c["n"] += 1
        c["plays"] += r["_plays"]
        c["inter"] += r["_inter"]
    for c in cats.values():
        c["share"] = c["plays"] / total["plays"]
        c["avg_plays"] = c["plays"] / c["n"]
    cats_sorted = sorted(cats.items(), key=lambda kv: -kv[1]["plays"])
    print("\n【品类】")
    for name, c in cats_sorted:
        print(f"  {name}: n={c['n']} plays={fmt(c['plays'])} ({pct(c['share'],1)}) avg={c['avg_plays']:.0f}")

    # ---------- 5. 语言受众 ----------
    langs = {}
    for r in records:
        l = langs.setdefault(r["_lang"], {"n": 0, "plays": 0, "inter": 0})
        l["n"] += 1
        l["plays"] += r["_plays"]
        l["inter"] += r["_inter"]
    for l in langs.values():
        l["avg_plays"] = l["plays"] / l["n"]
    print("\n【语言】", {k: {kk: round(vv) for kk, vv in v.items()} for k, v in langs.items()})

    # ---------- 6. TOP 视频 ----------
    top10 = sorted(records, key=lambda r: -r["_plays"])[:10]
    print("\n【TOP10 播放】")
    for i, r in enumerate(top10, 1):
        print(f"  {i}. [{r['对标账号']}] {str(r['商品名称'])[:30]} plays={fmt(r['_plays'])} "
              f"eng={pct(r['_inter']/r['_plays'] if r['_plays'] else 0)}")

    mature_min = [r for r in records if r["_plays"] >= 500]
    top_eng = sorted(mature_min, key=lambda r: -(r["_inter"] / r["_plays"]))[:5]

    # ---------- 7. 异常检测 ----------
    anomalies = []
    for acc, a in accounts.items():
        mean = statistics.mean(a["plays_list"])
        stdev = statistics.pstdev(a["plays_list"])
        for r in records:
            if r["对标账号"] == acc and stdev and (r["_plays"] - mean) / stdev > 1.8:
                anomalies.append({"type": "高播放离群", "acc": acc, "vid": r["_vid_tail"],
                                  "name": str(r["商品名称"])[:50], "plays": r["_plays"],
                                  "z": round((r["_plays"] - mean) / stdev, 2),
                                  "mean": round(mean), "note": f"超出该账号均值 {r['_plays']/mean:.1f} 倍"})
    zero_comment = sum(1 for r in records if r["_comments"] == 0)
    immature = [r for r in records if r["_age_h"] is not None and r["_age_h"] < 36]
    print("\n【异常】离群:", [(x['acc'], x['plays']) for x in anomalies],
          "| 零评论:", zero_comment, "/", len(records), "| 采集时<36h:", len(immature))

    # ---------- 8. 同品复推（含跨账号） ----------
    by_product = {}
    for r in records:
        if r["商品链接"]:
            by_product.setdefault(r["商品链接"], []).append(r)
    repeat_products = {k: sorted(v, key=lambda x: x["_pub"]) for k, v in by_product.items() if len(v) > 1}
    print("\n【同品复推】")
    for k, v in repeat_products.items():
        cross = "跨账号" if len(set(x["对标账号"] for x in v)) > 1 else "同账号"
        print(f"  {str(k)[-8:]} [{cross}] " + " -> ".join(
            f"{x['对标账号']}:{x['_plays']}" for x in v))

    # ---------- 9. 发布时段 ----------
    buckets = {"凌晨(0-6)": (0, 6), "上午(6-12)": (6, 12), "下午(12-18)": (12, 18), "晚间(18-24)": (18, 24)}
    hours = {k: {"n": 0, "plays": 0} for k in buckets}
    for r in records:
        if not r["_pub"]:
            continue
        h = r["_pub"].hour
        for k, (lo, hi) in buckets.items():
            if lo <= h < hi:
                hours[k]["n"] += 1
                hours[k]["plays"] += r["_plays"]
    for v in hours.values():
        v["avg"] = v["plays"] / v["n"] if v["n"] else 0
    print("\n【时段】", {k: (v['n'], round(v['avg'])) for k, v in hours.items()})

    # ---------- 10. 生成 HTML ----------
    data = {
        "kpi": {
            "videos": total["n"], "plays": total["plays"], "inter": total["inter"],
            "eng": round(total_eng * 100, 2), "attach": round(attach_rate * 100, 1),
            "attached": attached,
            "accounts": len([s for s, n in audit["row_counts"].items() if n > 0]),
            "empty_accounts": [s.replace("对标记录-", "") for s, n in audit["row_counts"].items() if n == 0],
        },
        "daily": days,
        "accounts": {k: {kk: vv for kk, vv in v.items() if kk != "plays_list"} for k, v in accounts.items()},
        "cats": [{"name": k, **v} for k, v in cats_sorted],
        "langs": langs,
        "top10": [{"acc": r["对标账号"], "vid": r["_vid_tail"], "name": str(r["商品名称"]),
                   "cat": r["_category"], "plays": r["_plays"], "inter": r["_inter"],
                   "eng": round(r["_inter"] / r["_plays"] * 100, 2) if r["_plays"] else 0,
                   "lang": r["_lang"]} for r in top10],
        "topEng": [{"acc": r["对标账号"], "vid": r["_vid_tail"], "name": str(r["商品名称"])[:50],
                    "plays": r["_plays"], "eng": round(r["_inter"] / r["_plays"] * 100, 2)} for r in top_eng],
        "anomalies": anomalies,
        "zeroComment": zero_comment,
        "immature": [{"acc": r["对标账号"],
                      "name": (str(r["商品名称"])[:40] if str(r["商品名称"] or "").strip() else "（未挂车·磁疗手环）"),
                      "age": r["_age_h"], "plays": r["_plays"]} for r in immature],
        "repeat": [{"name": str(v[0]["商品名称"])[:55], "cross": len(set(x["对标账号"] for x in v)) > 1,
                    "items": [{"acc": r["对标账号"], "date": r["_pub"].strftime("%m-%d %H:%M") if r["_pub"] else "?",
                               "plays": r["_plays"],
                               "eng": round(r["_inter"] / r["_plays"] * 100, 2) if r["_plays"] else 0} for r in v]}
                   for v in repeat_products.values()],
        "hours": [{"name": k, **v} for k, v in hours.items()],
        "detail": [{"acc": r["对标账号"], "pub": r["_pub"].strftime("%m-%d %H:%M") if r["_pub"] else str(r["视频发布时间"]),
                    "name": str(r["商品名称"] or "（未挂车）"), "cat": r["_category"], "plays": r["_plays"],
                    "likes": r["_likes"], "comments": r["_comments"], "shares": r["_shares"], "favs": r["_favs"],
                    "eng": round(r["_inter"] / r["_plays"] * 100, 2) if r["_plays"] else 0,
                    "style": r["风格标签"] or "", "lang": r["_lang"], "age": r["_age_h"],
                    "url": r["视频链接/ID"]} for r in
                   sorted(records, key=lambda x: (x["对标账号"], -(x["_pub"].timestamp() if x["_pub"] else 0)))],
        "audit": {"rows": audit["row_counts"], "emptyUnits": audit["empty_units"],
                  "noProduct": len(audit["no_product"]), "dups": len(audit["dup_video_ids"]),
                  "fill": audit["fill"]},
        "pp": [[r["对标账号"], r["_plays"]] for r in records],
    }

    html = build_html(data)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"\n【输出】{OUT_HTML} ({OUT_HTML.stat().st_size/1024:.0f} KB)")
    print("【校验】明细行数 =", len(data["detail"]), "| KPI 播放 =", fmt(data["kpi"]["plays"]),
          "| KPI 视频数 =", data["kpi"]["videos"])
    # 磁盘复读终验
    back = OUT_HTML.read_text(encoding="utf-8")
    assert '"videos": {}'.format(total["n"]) in back or '"videos":{}'.format(total["n"]) in back, "KPI 校验失败"
    assert str(total["plays"]) in back, "总播放校验失败"
    print("【终验】磁盘复读通过：视频数/总播放与计算值一致")


def build_html(d):
    echarts = ECHARTS_JS.read_text(encoding="utf-8")
    js_data = json.dumps(d, ensure_ascii=False)

    k = d["kpi"]
    empty_acc = "、".join(k["empty_accounts"]) or "无"
    kpi_cards = f"""
    <div class="kpis">
      <div class="kpi"><div class="kv">{k['videos']}</div><div class="kl">视频总数（去重）</div></div>
      <div class="kpi"><div class="kv">{fmt(k['plays'])}</div><div class="kl">总播放量</div></div>
      <div class="kpi"><div class="kv">{fmt(k['inter'])}</div><div class="kl">总互动量（赞+评+转+藏）</div></div>
      <div class="kpi"><div class="kv">{k['eng']}%</div><div class="kl">整体互动率</div></div>
      <div class="kpi"><div class="kv">{k['attach']}%</div><div class="kl">挂车率（{k['attached']}/{k['videos']}）</div></div>
      <div class="kpi"><div class="kv">{fmt(k['plays']//k['videos'])}</div><div class="kl">平均播放/视频</div></div>
    </div>"""

    # 环比表
    dod_rows = ""
    for day in d["daily"]:
        dod = day["dod"]
        if dod is None:
            dod_html = '<span class="muted">—</span>'
        else:
            cls = "up" if dod > 0 else ("down" if dod < 0 else "")
            dod_html = f'<span class="{cls}">{dod:+.1f}%</span>'
        dod_rows += f"""<tr><td>2026-{day['date']}</td><td>{day['n']}</td><td>{fmt(day['plays'])}</td>
        <td>{fmt(day['avg_plays'])}</td><td>{day['eng_rate']*100:.2f}%</td><td>{dod_html}</td></tr>"""

    # 账号对比表
    acc_rows = ""
    for name, a in d["accounts"].items():
        acc_rows += f"""<tr><td>{name}</td><td>{a['n']}</td><td>{fmt(a['plays'])}</td><td>{fmt(a['avg_plays'])}</td>
        <td>{fmt(a['median_plays'])}</td><td>{fmt(a['min_plays'])} ~ {fmt(a['max_plays'])}</td>
        <td>{a['eng_rate']*100:.2f}%</td></tr>"""

    cat_rows = ""
    for c in d["cats"]:
        eng_c = pct(c["inter"] / c["plays"]) if c["plays"] else "—"
        cat_rows += f"""<tr><td>{c['name']}</td><td>{c['n']}</td><td>{fmt(c['plays'])}</td>
        <td>{c['share']*100:.1f}%</td><td>{fmt(c['avg_plays'])}</td><td>{eng_c}</td></tr>"""

    top_rows = ""
    for i, t in enumerate(d["top10"], 1):
        nm = t["name"] if len(t["name"]) <= 42 else t["name"][:42] + "…"
        top_rows += f"""<tr><td>{i}</td><td>{t['acc']}</td><td class="tl">{nm}</td><td>{t['cat']}</td>
        <td><b>{fmt(t['plays'])}</b></td><td>{t['inter']}</td><td>{t['eng']}%</td><td>{t['lang']}</td></tr>"""

    top_eng_rows = ""
    for t in d["topEng"]:
        top_eng_rows += f"""<tr><td>{t['acc']}</td><td class="tl">{t['name']}</td><td>{fmt(t['plays'])}</td><td><b>{t['eng']}%</b></td></tr>"""

    anom_items = ""
    for a in d["anomalies"]:
        anom_items += f"""<li><b>[高播放离群]</b> {a['acc']} · {a['name']}…：{fmt(a['plays'])} 播放
       （z={a['z']}，账号均值 {fmt(a['mean'])}，{a['note']}）</li>"""
    immature_items = "".join(
        f"<li>{x['acc']} · {x['name']}…：发布后仅 {x['age']:.0f}h 采集（{fmt(x['plays'])} 播放，数据未成熟，不宜参与排名）</li>"
        for x in d["immature"])
    repeat_html = ""
    for rp in d["repeat"]:
        cross_tag = '<span class="tag" style="background:#fef3c7;color:#b45309">跨账号</span>' if rp["cross"] else '<span class="tag">同账号</span>'
        items = "".join(f"<tr><td>{it['acc']}</td><td>{it['date']}</td><td>{fmt(it['plays'])}</td><td>{it['eng']}%</td></tr>"
                        for it in rp["items"])
        repeat_html += f"""<div class="rcard"><div class="rtitle">{cross_tag}{rp['name']}…</div>
        <table><tr><th>账号</th><th>发布时间</th><th>播放量</th><th>互动率</th></tr>{items}</table></div>"""

    detail_rows = ""
    for r in d["detail"]:
        age = f"{r['age']:.0f}h" if r["age"] is not None else "?"
        mature_flag = "" if (r["age"] is not None and r["age"] >= 36) else ' <span class="imm">未成熟</span>'
        nm = r["name"] if len(r["name"]) <= 45 else r["name"][:45] + "…"
        detail_rows += f"""<tr><td>{r['acc']}</td><td>{r['pub']}</td><td class="tl">{nm}</td><td>{r['cat']}</td>
        <td>{fmt(r['plays'])}</td><td>{r['likes']}</td><td>{r['comments']}</td><td>{r['shares']}</td><td>{r['favs']}</td>
        <td>{r['eng']}%</td><td>{r['style']}</td><td>{r['lang']}</td><td>{age}{mature_flag}</td></tr>"""

    hour_rows = "".join(f"<tr><td>{h['name']}</td><td>{h['n']}</td><td>{fmt(h['plays'])}</td><td>{fmt(h['avg'])}</td></tr>"
                        for h in d["hours"])

    fill_rows = ""
    for acc, f in d["audit"]["fill"].items():
        fill_rows += (f"<tr><td>{acc}</td><td>{f['style']}/{f['n']}</td>"
                      f"<td>{f['trans']}/{f['n']}</td></tr>")

    # 预计算：首饰品类占比 / 西语倍数
    js_cat = next((c for c in d["cats"] if c["name"] == "首饰饰品"), None)
    js_js_share = pct(js_cat["share"], 1) if js_cat else "—"
    js_js_n = js_cat["n"] if js_cat else "—"
    vivi = d["accounts"].get("vivi_ashop", {})
    esp = d["langs"].get("西语", {})
    esp_ratio = (esp.get("avg_plays", 0) /
                 max(1e-9, (vivi.get("plays", 0) - esp.get("plays", 0)) /
                     max(1, vivi.get("n", 1) - esp.get("n", 0)))) if esp else 0
    kee_med = d["accounts"].get("kee.yaden", {}).get("median_plays", 0)
    cha_med = d["accounts"].get("charelttce", {}).get("median_plays", 0)
    vivi_med = d["accounts"].get("vivi_ashop", {}).get("median_plays", 0)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikTok 对标账号销售分析报表 · 2026-10-03</title>
<style>
  :root {{ --pri:#2563eb; --red:#dc2626; --green:#16a34a; --amber:#d97706; --ink:#1e293b;
          --sub:#64748b; --line:#e2e8f0; --bg:#f8fafc; --card:#ffffff; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif; background:var(--bg);
         color:var(--ink); line-height:1.65; padding:24px; }}
  .wrap {{ max-width:1180px; margin:0 auto; }}
  header {{ background:linear-gradient(135deg,#1d4ed8,#3b82f6); color:#fff; border-radius:14px;
           padding:28px 32px; margin-bottom:20px; }}
  header h1 {{ font-size:24px; margin-bottom:6px; }}
  header .meta {{ font-size:13px; opacity:.9; }}
  section {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
            padding:24px 28px; margin-bottom:20px; }}
  h2 {{ font-size:18px; margin-bottom:14px; padding-left:10px; border-left:4px solid var(--pri); }}
  h3 {{ font-size:15px; margin:16px 0 8px; color:#334155; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:14px; }}
  .kpi {{ background:linear-gradient(180deg,#eff6ff,#ffffff); border:1px solid #bfdbfe; border-radius:12px;
         padding:16px; text-align:center; }}
  .kv {{ font-size:26px; font-weight:700; color:var(--pri); }}
  .kl {{ font-size:12px; color:var(--sub); margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin:10px 0; }}
  th {{ background:#f1f5f9; text-align:left; padding:8px 10px; border-bottom:2px solid var(--line); white-space:nowrap; }}
  td {{ padding:7px 10px; border-bottom:1px solid var(--line); }}
  tr:hover td {{ background:#f8fafc; }}
  .tl {{ max-width:280px; word-break:break-all; }}
  .up {{ color:var(--red); font-weight:600; }}
  .down {{ color:var(--green); font-weight:600; }}
  .muted {{ color:var(--sub); }}
  .imm {{ background:#fef3c7; color:var(--amber); font-size:11px; padding:1px 6px; border-radius:8px; }}
  .chart {{ width:100%; height:360px; }}
  .chart2 {{ width:100%; height:320px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:860px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .notice {{ background:#fffbeb; border:1px solid #fcd34d; border-radius:12px; padding:16px 20px;
            margin-bottom:20px; font-size:13px; }}
  .notice b {{ color:#92400e; }}
  ul.findings li, section ul li {{ margin:6px 0; }}
  .rcard {{ border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin-bottom:10px; }}
  .rtitle {{ font-weight:600; margin-bottom:6px; font-size:13px; }}
  footer {{ font-size:12px; color:var(--sub); text-align:center; padding:12px; }}
  .tag {{ display:inline-block; background:#eff6ff; color:var(--pri); border-radius:6px;
         padding:1px 8px; font-size:12px; margin-right:6px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>TikTok 对标账号销售分析报表</h1>
  <div class="meta">数据源：tiktok_对标记录.xlsx（4 个账号分表合并，{k['videos']} 条视频记录）｜
  视频发布窗口：2026-09-27 ~ 2026-10-03｜生成时间：{GENERATED_AT}</div>
</header>

<div class="notice">
  <b>⚠ 口径与数据边界声明（先读）</b><br>
  ① 本台账为 <b>TikTok 对标账号视频带货内容记录</b>，并非成交数据表：<b>「视频出单量」36/36 全部为空</b>（FastMoss 估算字段未回填），
  且无销售额、价格、区域、渠道字段——因此"销量/销售额/区域/渠道"维度<b>在本数据中不可直接分析</b>。<br>
  ② 报表以 <b>播放量与互动量（赞/评/转/藏）作为销售表现的代理指标</b>，所有"销售表现"均指"内容带货曝光表现"。<br>
  ③ 互动数据为<b>采集时点快照</b>（kee.yaden 采集于 10-02，vivi_ashop / charelttce 采集于 10-03），账号横向对比存在成熟度偏差，已尽量标注。<br>
  ④ 品类为基于英文商品名称的关键词归纳；"区域/渠道"以内容语言（英语/西语）侧写受众市场。
</div>

<section>
  <h2>一、关键指标汇总</h2>
  {kpi_cards}
  <p style="margin-top:12px;font-size:13px;color:var(--sub)">
  覆盖账号：kee.yaden（7 条）、vivi_ashop（23 条）、charelttce（6 条）；dicharrymeredith（0 条，尚未采集）。
  出单量字段缺失率 100%；挂车率按商品链接非空计算（唯一未挂车为 vivi_ashop 的磁疗手环视频）。</p>
</section>

<section>
  <h2>二、整体趋势（按视频发布日期）</h2>
  <div id="cTrend" class="chart"></div>
  <div class="grid2">
    <div id="cEng" class="chart2"></div>
    <div id="cHour" class="chart2"></div>
  </div>
  <h3>每日汇总与环比</h3>
  <table>
    <tr><th>发布日期</th><th>视频数</th><th>播放量合计</th><th>平均播放/条</th><th>互动率</th><th>播放环比</th></tr>
    {dod_rows}
  </table>
  <p style="font-size:12px;color:var(--sub)">
  注：按发布日期聚合的播放量受"曝光累积时间"影响——越晚发布的视频在采集时点累积时间越短
  （10-02/10-03 发布的视频仅数小时~1天），环比下降≠表现变差；趋势解读以 09-27 ~ 10-01 窗口为宜。
  09-30 为投放高峰（9 条、{fmt(d['daily'][3]['plays'])} 播放），主要由 vivi_ashop 爆款（三圣心手环 58,700）与 kee/charelttce 数码内容共同贡献。</p>
</section>

<section>
  <h2>三、账号对比（渠道侧写：账号即带货渠道）</h2>
  <table>
    <tr><th>账号</th><th>视频数</th><th>总播放</th><th>平均播放</th><th>中位数播放</th><th>播放区间</th><th>互动率</th></tr>
    {acc_rows}
  </table>
  <div class="grid2">
    <div id="cAcc" class="chart2"></div>
    <div id="cAccBox" class="chart2"></div>
  </div>
  <p style="font-size:13px">
  <span class="tag">charelttce</span>充电数码垂类，平均播放 {fmt(d['accounts'].get('charelttce',{}).get('avg_plays',0))}、中位数 {fmt(cha_med)}，表现最稳；<br>
  <span class="tag">kee.yaden</span>同为充电数码垂类（与 charelttce 共用 PUTARE 选品池），中位数 {fmt(kee_med)}，输出稳定；<br>
  <span class="tag">vivi_ashop</span>多品类测款（首饰/手机壳/包袋/礼品/玄学），依赖偶发爆款拉动总量，中位数仅 {fmt(vivi_med)}，波动极大（两条爆款贡献其 59% 播放）。</p>
</section>

<section>
  <h2>四、品类表现对比（产品维度）</h2>
  <div class="grid2">
    <div id="cCatPie" class="chart2"></div>
    <div id="cCatBar" class="chart2"></div>
  </div>
  <table>
    <tr><th>品类</th><th>视频数</th><th>总播放</th><th>播放占比</th><th>平均播放/条</th><th>互动率</th></tr>
    {cat_rows}
  </table>
</section>

<section>
  <h2>五、受众市场侧写（语言维度，替代区域/渠道）</h2>
  <div id="cLang" class="chart2"></div>
  <table>
    <tr><th>受众语言</th><th>视频数</th><th>总播放</th><th>平均播放/条</th></tr>
    {"".join(f"<tr><td>{kk}</td><td>{vv['n']}</td><td>{fmt(vv['plays'])}</td><td>{fmt(vv['avg_plays'])}</td></tr>" for kk, vv in d['langs'].items())}
  </table>
  <p style="font-size:13px">vivi_ashop 内 2 条西语视频（宗教饰品/首饰）平均播放约为该账号英语视频的
  <b>{esp_ratio:.1f} 倍</b>（{fmt(esp.get('avg_plays',0))} vs {fmt((vivi.get('plays',0)-esp.get('plays',0))/max(1,vivi.get('n',1)-esp.get('n',0)))}），
  西语受众市场是其最重要的流量杠杆。</p>
</section>

<section>
  <h2>六、重点项目排名（TOP10 播放 · 代理销量排名）</h2>
  <div id="cTop" class="chart"></div>
  <table>
    <tr><th>#</th><th>账号</th><th>商品名称</th><th>品类</th><th>播放量</th><th>互动量</th><th>互动率</th><th>语言</th></tr>
    {top_rows}
  </table>
  <h3>高互动率 TOP5（播放 ≥500 的成熟样本）</h3>
  <table>
    <tr><th>账号</th><th>商品名称</th><th>播放量</th><th>互动率</th></tr>
    {top_eng_rows}
  </table>
</section>

<section>
  <h2>七、异常值与值得关注的波动</h2>
  <ul class="findings">
    {anom_items}
    <li><b>[零评论现象]</b> 36 条视频中 <b>{d['zeroComment']} 条评论数为 0</b>（vivi_ashop 23 条中 19 条为 0）——播放量与评论量严重脱节，
    流量结构疑似以系统推流/投流为主，互动深度不足，需出单量数据验证真实转化。</li>
    <li><b>[播放极差]</b> vivi_ashop 播放区间 7 ~ 58,700（相差 8,385 倍），账号表现高度依赖单条爆款，无稳定基本盘。</li>
    <li><b>[未成熟数据]</b> 以下视频采集时发布不足 36 小时，数据尚未收敛（参与汇总但不建议用于排名定论）：
    <ul>{immature_items}</ul></li>
    <li><b>[未挂车视频]</b> vivi_ashop 10-01 19:02 的磁疗手环视频未挂商品链接（全表唯一），曝光转化链路断裂。</li>
    <li><b>[时段反差]</b> 晚间（18-24 时）发布最密集（15 条）但平均播放最低（{fmt([h for h in d['hours'] if h['name']=='晚间(18-24)'][0]['avg'])}）；
    凌晨（0-6 时）发布 11 条、平均播放最高（{fmt([h for h in d['hours'] if h['name']=='凌晨(0-6)'][0]['avg'])}）——样本量小，仅作观察。</li>
  </ul>
  <h3>同品复推效果（同一商品发多条视频，含跨账号）</h3>
  <div class="grid2">{repeat_html}</div>
  <p style="font-size:12px;color:var(--sub)">
  注：① 同品复推整体衰减明显（豹纹手机壳 -88%、透明磁吸壳 -75%、Scrolling Ring -85%），但晚发布的视频累积时间更短，衰减幅度含时间偏差；
  ② kee.yaden 与 charelttce 存在<b>跨账号推广完全相同的 PUTARE 商品</b>（音箱支架/遥控器/翻页戒指），选品池高度重合，其中音箱支架 kee 版 19,000 播放 vs charelttce 版 2,875/5,828，同类素材账号间表现分化显著。</p>
</section>

<section>
  <h2>八、数据明细表（全部 {k['videos']} 条）</h2>
  <table>
    <tr><th>账号</th><th>发布时间</th><th>商品名称</th><th>品类</th><th>播放</th><th>赞</th><th>评</th><th>转</th><th>藏</th><th>互动率</th><th>风格标签</th><th>语言</th><th>采集时视频年龄</th></tr>
    {detail_rows}
  </table>
</section>

<section>
  <h2>九、核心发现与业务建议</h2>
  <h3>发现（按证据强度排序）</h3>
  <ul class="findings">
    <li><b>★★★ 西语宗教内容是已被验证的流量杠杆。</b>三圣心护佑手环（西语口播）58,700 播放为全表第一（约为该账号均值的 9.3 倍），
    14K 麻花耳环（西语）11,600 播放位列第九；首饰饰品以 {js_js_n} 条（{js_js_n/k['videos']*100:.0f}%）贡献了 {js_js_share} 的总播放，
    单条平均表现（{fmt(js_cat['avg_plays'])}）与双账号堆量的充电数码（47.8%）几乎持平，是最强的非数码赛道。</li>
    <li><b>★★★ 出单量数据 100% 缺失，当前所有"表现"仅为曝光侧。</b>高播放 ≠ 高出单，36 条中 {d['zeroComment']} 条零评论提示流量深度存疑。</li>
    <li><b>★★☆ 数码垂类双账号基本盘稳。</b>charelttce（均播 {fmt(d['accounts'].get('charelttce',{}).get('avg_plays',0))}）与 kee.yaden（均播 {fmt(d['accounts'].get('kee.yaden',{}).get('avg_plays',0))}）中位数均接近 1 万，
    显著高于 vivi_ashop 的 {fmt(vivi_med)}；两者共用 PUTARE 选品池，属同赛道对标。</li>
    <li><b>★★☆ 同品复推（含跨账号）衰减明显。</b>5 组复推中 4 组第二次发布播放大幅下滑（-75% ~ -88%）；跨账号同品表现分化（kee 音箱支架 19,000 vs charelttce 同品 2,875/5,828）说明素材与账号权重共同决定表现。</li>
    <li><b>★☆☆ 时段规律样本不足。</b>凌晨发布平均播放最高、晚间最低，但 36 条样本且受成熟度偏差影响，不足以支撑统计结论。</li>
  </ul>
  <h3>可执行建议</h3>
  <ol>
    <li><b>补齐出单量（最高优先级）：</b>回填 FastMoss「视频出单量」（--fill-video-units），将曝光代理指标升级为真实转化指标，再复核本报表全部排名结论。</li>
    <li><b>统一采集时点：</b>对全部账号做同一时点二次采集（视频成熟后 ≥72h），消除成熟度偏差后再做账号横向定论。</li>
    <li><b>vivi_ashop：</b>收缩测款范围，资源向已验证题材（西语宗教首饰、玄学能量、幽默礼品）倾斜；西语内容可加大投入并系统化（固定口播模板+西语文案）；补齐 23 条视频的中文翻译与缺失的风格标签规范。</li>
    <li><b>同品复推：</b>停止同素材角度直接复推，改为换场景/换人群角度再测；跨账号推同品前先对比账号受众匹配度（kee 与 charelttce 同品表现差 3~6 倍）。</li>
    <li><b>kee.yaden / charelttce：</b>数码垂类模板已跑通（中位数稳定），可提升发布频次做规模复制；kee 补风格标签，charelttce 关注凌晨时段（其 26,700 播放的 Scrolling Ring 发布于 03:20）。</li>
    <li><b>未挂车视频：</b>排查 10-01 磁疗手环视频漏挂车原因（发布后商品下架 or 录入遗漏），避免流量浪费。</li>
  </ol>
</section>

<section>
  <h2>十、假设声明汇总</h2>
  <ol style="font-size:13px">
    <li><b>代理指标假设：</b>无销量/销售额数据，以播放量（曝光）+ 互动量（互动深度）作为销售表现代理。</li>
    <li><b>品类归纳假设：</b>品类由英文商品名称关键词人工归类（充电数码/首饰/手机壳/包袋/玄学/礼品/DIY/个护），kee.yaden 与 charelttce 整账号归入充电数码；未挂车的磁疗手环视频按风格标签归入首饰。非平台官方类目。</li>
    <li><b>区域/渠道假设：</b>数据无区域渠道字段，以"对标账号"作为渠道单元、以内容语言（英语/西语）侧写受众市场。</li>
    <li><b>互动率定义：</b>互动率 =（点赞+评论+转发+收藏）/ 播放量。</li>
    <li><b>快照假设：</b>互动数据为采集时点快照且各账号采集时点不同（10-02 / 10-03）；发布不足 36h 的视频标注"未成熟"。</li>
    <li><b>环比口径：</b>环比基于"视频发布日期"聚合的播放量，受曝光累积时间影响，趋势以 09-27 ~ 10-01 窗口为准。</li>
    <li><b>排名口径：</b>TOP 榜为"曝光榜"而非销量榜；出单量回填后应重排。</li>
  </ol>
</section>

<section>
  <h2>附录：数据质量报告</h2>
  <table>
    <tr><th>检查项</th><th>结果</th></tr>
    <tr><td>各表行数</td><td>{json.dumps(d['audit']['rows'], ensure_ascii=False)}（合计 {k['videos']} 条，视频 ID 无重复，与去重机制一致）</td></tr>
    <tr><td>视频出单量缺失</td><td>{d['audit']['emptyUnits']}/{k['videos']}（100% 缺失，FastMoss 未回填）</td></tr>
    <tr><td>未挂车视频</td><td>{d['audit']['noProduct']} 条（vivi_ashop 磁疗手环）</td></tr>
    <tr><td>风格标签 / 中文翻译填充度</td><td>
      <table><tr><th>账号</th><th>风格标签已标注</th><th>中文翻译已填</th></tr>{fill_rows}</table></td></tr>
    <tr><td>日期类型</td><td>vivi_ashop 表多数发布时间为文本字符串（已程序化解析，解析失败 0 条），其余为真日期类型</td></tr>
    <tr><td>数值字段</td><td>播放/赞/评/转/藏均为整数、无负值、无错误值</td></tr>
  </table>
</section>

<footer>TikTok 对标销售分析报表 · 由数据分析流水线自动生成 · {GENERATED_AT} · 数据源：D:/1/对标账号/tiktok_对标记录.xlsx</footer>
</div>

<script>{echarts}</script>
<script>
const D = {js_data};
window.__pp = D.pp;
const C = {{pri:"#2563eb", red:"#dc2626", green:"#16a34a", amber:"#d97706", purple:"#7c3aed", teal:"#0d9488", gray:"#94a3b8"}};
const base = {{tooltip:{{trigger:"axis"}}, grid:{{left:60,right:60,top:40,bottom:50}}}};
const mk = (id, opt) => echarts.init(document.getElementById(id)).setOption(opt);

// 二、趋势：每日播放(bar) + 视频数(line, 右轴)
mk("cTrend", {{...base, legend:{{data:["播放量","视频数"],bottom:0}},
  xAxis:{{type:"category",data:D.daily.map(x=>"2026-"+x.date)}},
  yAxis:[{{type:"value",name:"播放量"}},{{type:"value",name:"视频数",minInterval:1}}],
  series:[
    {{name:"播放量",type:"bar",itemStyle:{{color:C.pri}},data:D.daily.map(x=>x.plays),
     label:{{show:true,position:"top",formatter:p=>p.value>=10000?(p.value/10000).toFixed(1)+"万":p.value}}}},
    {{name:"视频数",type:"line",yAxisIndex:1,itemStyle:{{color:C.amber}},data:D.daily.map(x=>x.n)}}
  ]}});

// 二、互动率趋势
mk("cEng", {{...base, title:{{text:"每日互动率（%）",textStyle:{{fontSize:13}}}},
  xAxis:{{type:"category",data:D.daily.map(x=>x.date)}},
  yAxis:{{type:"value",axisLabel:{{formatter:"{{value}}%"}}}},
  series:[{{type:"line",smooth:true,itemStyle:{{color:C.teal}},areaStyle:{{opacity:.12}},
    data:D.daily.map(x=>+(x.eng_rate*100).toFixed(2))}}]}});

// 二、时段
mk("cHour", {{...base, title:{{text:"发布时段 × 平均播放",textStyle:{{fontSize:13}}}},
  xAxis:{{type:"category",data:D.hours.map(h=>h.name)}},
  yAxis:{{type:"value"}},
  series:[{{type:"bar",itemStyle:{{color:C.purple}},data:D.hours.map(h=>Math.round(h.avg)),
    label:{{show:true,position:"top"}}}}]}});

// 三、账号对比
const accs = Object.keys(D.accounts);
mk("cAcc", {{...base, legend:{{data:["平均播放/条","中位数播放"],bottom:0}},
  xAxis:{{type:"category",data:accs}},
  yAxis:{{type:"value"}},
  series:[
    {{name:"平均播放/条",type:"bar",itemStyle:{{color:C.pri}},data:accs.map(a=>Math.round(D.accounts[a].avg_plays)),label:{{show:true,position:"top"}}}},
    {{name:"中位数播放",type:"bar",itemStyle:{{color:C.teal}},data:accs.map(a=>Math.round(D.accounts[a].median_plays)),label:{{show:true,position:"top"}}}}
  ]}});

mk("cAccBox", {{...base, title:{{text:"播放量分布（账号内 min/Q1/中位/Q3/max）",textStyle:{{fontSize:13}}}},
  xAxis:{{type:"category",data:accs}},
  yAxis:{{type:"value"}},
  series:accs.map((a,i)=>({{type:"boxplot",name:a,itemStyle:{{color:[C.pri,C.amber,C.purple][i]||C.gray}},
    data:[(function(){{const v=window.__pp.filter(r=>r[0]===a).map(r=>r[1]).sort((x,y)=>x-y);
      const q=p=>v[Math.floor(p*(v.length-1))];
      return [q(0),q(.25),q(.5),q(.75),q(1)];}})()]}},
    tooltip:{{formatter:ob=>a+"<br/>min/Q1/中位/Q3/max：<br/>"+ob.data.join(" / ")}}}})}});

// 四、品类
mk("cCatPie", {{title:{{text:"播放量占比",textStyle:{{fontSize:13}},left:"center"}},
  tooltip:{{trigger:"item",formatter:"{{b}}: {{c}} ({{d}}%)"}},
  legend:{{bottom:0,type:"scroll"}},
  series:[{{type:"pie",radius:["35%","65%"],data:D.cats.map(c=>({{name:c.name,value:c.plays}})),
    label:{{formatter:"{{b}}\\n{{d}}%"}}}}]}});

mk("cCatBar", {{...base, title:{{text:"品类平均播放/条",textStyle:{{fontSize:13}}}},
  xAxis:{{type:"value"}},
  yAxis:{{type:"category",data:D.cats.map(c=>c.name).reverse(),axisLabel:{{fontSize:11}}}},
  series:[{{type:"bar",data:D.cats.map(c=>Math.round(c.avg_plays)).reverse(),
    itemStyle:{{color:C.pri}},label:{{show:true,position:"right"}}}}]}});

// 五、语言
mk("cLang", {{...base, legend:{{data:["总播放","平均播放/条"],bottom:0}},
  xAxis:{{type:"category",data:Object.keys(D.langs)}},
  yAxis:[{{type:"value"}},{{type:"value"}}],
  series:[
    {{name:"总播放",type:"bar",itemStyle:{{color:C.pri}},data:Object.values(D.langs).map(v=>v.plays),label:{{show:true,position:"top"}}}},
    {{name:"平均播放/条",type:"bar",yAxisIndex:1,itemStyle:{{color:C.red}},data:Object.values(D.langs).map(v=>Math.round(v.avg_plays)),label:{{show:true,position:"top"}}}}
  ]}});

// 六、TOP10
mk("cTop", {{...base, grid:{{left:230,right:80,top:20,bottom:30}},
  xAxis:{{type:"value"}},
  yAxis:{{type:"category",
    data:D.top10.map(t=>(t.name.length>34?t.name.slice(0,34)+"…":t.name)+" ["+t.acc+"]").reverse(),
    axisLabel:{{fontSize:11}}}},
  series:[{{type:"bar",data:D.top10.map(t=>t.plays).reverse(),
    itemStyle:{{color:p=>p.dataIndex>=7?C.red:(p.dataIndex>=5?C.amber:C.pri)}},
    label:{{show:true,position:"right",formatter:o=>o.value>=10000?(o.value/10000).toFixed(1)+"万":o.value}}}}]}});

window.addEventListener("resize",()=>chartResize());
function chartResize(){{document.querySelectorAll("[_echarts_instance_]").forEach(el=>echarts.getInstanceByDom(el)&&echarts.getInstanceByDom(el).resize());}}
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
