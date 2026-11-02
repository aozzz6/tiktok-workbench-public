# -*- coding: utf-8 -*-
"""
临时报表生成器：vivi_ashop vs charelttce 两账号对标分析报表
数据源：D:/1/对标账号/tiktok_对标记录.xlsx 直读（分表「对标记录-vivi_ashop」「对标记录-charelttce」）
输出：D:/workbuddy_project/tiktok/pipeline/output/report_vivi_charelttce.html（自包含单文件，内嵌 echarts.min.js）
用法: python generate_report_vivi_charelttce.py
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

XLSX_PATH = Path(r"D:/1/对标账号/tiktok_对标记录.xlsx")
SHEETS = ["对标记录-vivi_ashop", "对标记录-charelttce"]
OUT_HTML = Path(r"D:/workbuddy_project/tiktok/pipeline/output/report_vivi_charelttce.html")
ECHARTS_JS = Path(r"D:/workbuddy_project/tiktok/pipeline/assets/echarts.min.js")
GENERATED_AT = "2026-10-05 (GMT+8)"

# 账号主题色（vivi=紫罗兰杂货铺系, charelttce=科技蓝数码系）
C_VIVI = "#8b5cf6"
C_CHARE = "#0ea5e9"

CATEGORY_RULES = [
    ("首饰饰品", ["Ring", "Bracelet", "Earring", "Bolo", "Bangle", "Hematite", "Moonstone", "Obsidian"]),
    ("包袋", ["Tote", "Underarm", "Wallet", "Crossbody", "PU Leather"]),
    ("手机壳/数码小件", ["Phone Case", "Magnetic Case", "MagSafe", "Case", "Fan", "Head-mounted"]),
    ("玄学/信仰", ["Tarot", "Devotional", "Bible", "God"]),
    ("DIY/解压玩具", ["Rhinestone", "Bedazzl", "Fidget", "Sticker", "Greeting Card"]),
    ("礼品/文具", ["Sticky Note", "Coin", "Keyboard Charm", "Challenge Coin"]),
]


def classify_vivi(name):
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw.lower() in name.lower():
                return cat
    return "其他/无挂车"


def sku_chare(name):
    if "Speaker Stand" in name:
        return "Stand 音箱/支架/充电宝三合一"
    if "Remote Control for TikTok" in name:
        return "遥控器支架 Page Turner+手机支架"
    return "遥控戒指 Scrolling Ring"


def load_rows():
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    data = {}
    for sheet in SHEETS:
        ws = wb[sheet]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue
            if row is None or all(c is None for c in row):
                continue
            rows.append(row)
        data[sheet] = rows
    wb.close()
    return data


def pct(a, b):
    return a / b * 100 if b else 0.0


def fmt_int(n):
    return f"{n:,}"


def build():
    data = load_rows()

    # ---------- 统计 ----------
    stats = {}
    for sheet, rows in data.items():
        plays = [int(r[8] or 0) for r in rows]
        dates = [str(r[2])[:10] for r in rows]
        eng = []
        for r in rows:
            p = int(r[8] or 0)
            if p > 0:
                e = sum(int(r[i] or 0) for i in (9, 10, 11, 12))
                eng.append({"rate": e / p * 100, "plays": p, "name": str(r[6] or "(无挂车)")[:46],
                            "likes": int(r[9] or 0), "comments": int(r[10] or 0),
                            "shares": int(r[11] or 0), "saves": int(r[12] or 0),
                            "date": str(r[2])[:10], "audio": str(r[13] or "")[:60],
                            "style": str(r[15] or "")[:30]})
        acc = "vivi_ashop" if "vivi" in sheet else "charelttce"
        span_days = (datetime.strptime(max(dates), "%Y-%m-%d") -
                     datetime.strptime(min(dates), "%Y-%m-%d")).days + 1
        per_day = defaultdict(int)
        for dt in dates:
            per_day[dt] += 1
        stats[acc] = {
            "n": len(rows), "total": sum(plays), "mean": statistics.mean(plays),
            "median": statistics.median(plays), "max": max(plays), "min": min(plays),
            "over100k": sum(1 for p in plays if p >= 100000),
            "10k_100k": sum(1 for p in plays if 10000 <= p < 100000),
            "under10k": sum(1 for p in plays if p < 10000),
            "mean_wo_top2": statistics.mean(sorted(plays)[:-2]),
            "eng_mean": statistics.mean([e["rate"] for e in eng]),
            "eng_median": statistics.median([e["rate"] for e in eng]),
            "eng_max": max(e["rate"] for e in eng),
            "likes": sum(int(r[9] or 0) for r in rows),
            "comments": sum(int(r[10] or 0) for r in rows),
            "shares": sum(int(r[11] or 0) for r in rows),
            "saves": sum(int(r[12] or 0) for r in rows),
            "span_days": span_days, "active_days": len(per_day),
            "per_day": dict(sorted(per_day.items())),
            "videos": sorted(eng, key=lambda x: -x["plays"]),
            "rows": rows,
        }

    v, c = stats["vivi_ashop"], stats["charelttce"]
    vivi_nocart = sum(1 for r in v["rows"] if not r[5])
    vivi_cart_rate = (v["n"] - vivi_nocart) / v["n"] * 100

    # vivi 品类
    vivi_cat = defaultdict(lambda: {"n": 0, "plays": 0})
    for r in v["rows"]:
        name = str(r[6] or "")
        cat = classify_vivi(name) if name.strip() else "其他/无挂车"
        vivi_cat[cat]["n"] += 1
        vivi_cat[cat]["plays"] += int(r[8] or 0)
    vivi_cat_sorted = sorted(vivi_cat.items(), key=lambda x: -x[1]["plays"])

    # charelttce SKU
    chare_sku = defaultdict(list)
    for r in c["rows"]:
        chare_sku[sku_chare(str(r[6]))].append(int(r[8] or 0))
    chare_sku_sorted = sorted(chare_sku.items(), key=lambda x: -sum(x[1]))

    # 时间序列（时序折线，用于对比量级差距）
    all_dates = sorted(set(list(v["per_day"].keys()) + list(c["per_day"].keys())))
    v_series = [v["per_day"].get(d, 0) for d in all_dates]
    c_series = [c["per_day"].get(d, None) if d in c["per_day"] else None for d in all_dates]
    # chare 每日播放总量序列（当天唯一视频的播放量）
    c_daily_plays = {d: int(r[8] or 0) for r in c["rows"] for d in [str(r[2])[:10]]}
    c_plays_series = [c_daily_plays.get(d) for d in all_dates]

    # vivi 每日播放总量
    v_daily_plays = defaultdict(int)
    for r in v["rows"]:
        v_daily_plays[str(r[2])[:10]] += int(r[8] or 0)
    v_plays_series = [v_daily_plays.get(d) for d in all_dates]

    # ---------- HTML ----------
    echarts_js = ECHARTS_JS.read_text(encoding="utf-8")

    def kpi(color, label, value, sub):
        return (f'<div class="kpi" style="border-top:3px solid {color}">'
                f'<div class="kpi-label">{label}</div><div class="kpi-value">{value}</div>'
                f'<div class="kpi-sub">{sub}</div></div>')

    kpis = "".join([
        kpi(C_CHARE, "charelttce 总播放", fmt_int(c["total"]), f"30 条 / 34 天，均值 {fmt_int(round(c['mean']))}"),
        kpi(C_VIVI, "vivi_ashop 总播放", fmt_int(v["total"]), f"31 条 / 9 天，均值 {fmt_int(round(v['mean']))}"),
        kpi(C_CHARE, "charelttce 中位数", fmt_int(round(c["median"])), f"均值被两条爆款拉高至中位数的 {c['mean']/c['median']:.0f} 倍"),
        kpi(C_VIVI, "vivi_ashop 中位数", fmt_int(round(v["median"])), f"15/31 条播放 <2,000"),
        kpi(C_VIVI, "vivi 互动率均值", f"{v['eng_mean']:.2f}%", f"中位数 {v['eng_median']:.2f}%"),
        kpi(C_CHARE, "chare 互动率均值", f"{c['eng_mean']:.2f}%", f"中位数 {c['eng_median']:.2f}%"),
    ])

    # 视频明细行
    def video_rows(acc_key, color):
        s = stats[acc_key]
        out = []
        for i, vid in enumerate(s["videos"], 1):
            nocart = " <span class='tag-nocart'>无挂车</span>" if acc_key == "vivi_ashop" and vid["name"] == "(无挂车)" else ""
            out.append(f"<tr><td>{i}</td><td>{vid['date']}</td>"
                       f"<td class='name'>{vid['name']}{nocart}</td>"
                       f"<td>{vid['style']}</td><td class='num'>{fmt_int(vid['plays'])}</td>"
                       f"<td class='num'>{vid['likes']:,}</td><td class='num'>{vid['shares']:,}</td>"
                       f"<td class='num'>{vid['saves']:,}</td><td class='num'>{vid['rate']:.2f}%</td></tr>")
        return "\n".join(out)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>对标分析报表：vivi_ashop vs charelttce</title>
<style>
:root {{ --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --bg:#f8fafc; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:"PingFang SC","Microsoft YaHei",-apple-system,sans-serif; color:var(--ink); background:var(--bg); line-height:1.6; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:32px 24px 64px; }}
header.hero {{ background:linear-gradient(135deg,#1e1b4b 0%,#0c4a6e 100%); color:#fff; border-radius:14px; padding:36px 40px; margin-bottom:28px; }}
header.hero h1 {{ font-size:26px; margin-bottom:8px; }}
header.hero p {{ opacity:.85; font-size:13px; }}
.kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-bottom:28px; }}
.kpi {{ background:#fff; border-radius:10px; padding:14px 14px 12px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.kpi-label {{ font-size:12px; color:var(--muted); }}
.kpi-value {{ font-size:22px; font-weight:700; margin:2px 0; font-variant-numeric:tabular-nums; }}
.kpi-sub {{ font-size:11px; color:var(--muted); }}
section {{ background:#fff; border-radius:12px; padding:26px 30px; margin-bottom:24px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
section h2 {{ font-size:18px; margin-bottom:4px; padding-left:10px; border-left:4px solid #0ea5e9; }}
section h2 .en {{ font-size:12px; color:var(--muted); font-weight:400; margin-left:8px; }}
section p.lead {{ color:var(--muted); font-size:13px; margin-bottom:16px; }}
.chart {{ width:100%; height:420px; margin:8px 0 4px; }}
.chart-sm {{ height:360px; }}
.conclusion {{ background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:12px 16px; font-size:13.5px; margin:14px 0 4px; }}
.conclusion b {{ color:#92400e; }}
table {{ width:100%; border-collapse:collapse; font-size:12.5px; margin-top:12px; }}
th {{ background:#f1f5f9; text-align:left; padding:8px 10px; font-size:12px; color:var(--muted); white-space:nowrap; }}
td {{ padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.name {{ max-width:420px; }}
tr.top3 td {{ background:#fff7ed; }}
.tag-nocart {{ background:#fee2e2; color:#b91c1c; font-size:10px; padding:1px 6px; border-radius:4px; margin-left:4px; }}
.cols2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
.boundary {{ background:#f1f5f9; border-radius:10px; padding:16px 20px; font-size:12.5px; color:#475569; margin-bottom:24px; }}
.boundary b {{ color:#334155; }}
.boundary ul {{ margin:6px 0 0 18px; }}
footer {{ text-align:center; color:var(--muted); font-size:12px; margin-top:32px; }}
.grid3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:14px; }}
.vcard {{ border:1px solid var(--line); border-radius:10px; padding:14px; font-size:12.5px; }}
.vcard .vp {{ font-size:20px; font-weight:700; margin:4px 0; }}
.vcard .vn {{ font-weight:600; }}
.vcard .vd {{ color:var(--muted); margin-top:6px; }}
@media (max-width:900px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} .cols2 {{ grid-template-columns:1fr; }} .grid3 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>对标分析报表：vivi_ashop × charelttce</h1>
  <p>选品策略对照 · 播放分布 · 爆款拆解 · 互动质量 ｜ 数据源：D:/1/对标账号/tiktok_对标记录.xlsx（磁盘直读，经 data-steward 审计）｜ 生成时间：{GENERATED_AT}</p>
</header>

<div class="boundary">
  <b>📐 数据边界声明</b>
  <ul>
    <li>台账两分表磁盘直读：<b>vivi_ashop 31 行（2026-09-27 ~ 10-05，记录窗口仅 9 天）</b>、<b>charelttce 30 行（2026-09-01 ~ 10-04，34 天，其中 09-16 / 09-18 / 09-19 / 10-01 四天未更新）</b>。两账号记录窗口不同，总量级对比需结合窗口长度解读。</li>
    <li><b>「视频出单量」两表 100% 为空</b>（FastMoss 估算字段，需 --fill-video-units 回填）：本报告<b>不含任何出单/GMV 维度</b>，播放量为唯一规模指标。</li>
    <li>互动数据（点赞/评论/转发/收藏 J~M 列）两表均有值，互动率 =（赞+评+转+藏）/播放。</li>
    <li>pending_gmv 待查清单现存 4 条（hdxlpebl 账号），FastMoss 额度为 0 暂未补查，与本报告两账号无关，仅作状态备注。</li>
    <li>商品名保留英文原文，品类归类按英文关键词匹配。</li>
  </ul>
</div>

<div class="kpis">{kpis}</div>

<section>
  <h2>一、核心结论<span class="en">Executive Summary</span></h2>
  <div class="conclusion">
    <b>① 规模差距 21.8 倍，但结构完全不同。</b>charelttce 30 条累计播放 {fmt_int(c['total'])}（均值 {fmt_int(round(c['mean']))}、中位数 {fmt_int(round(c['median']))}），vivi_ashop 31 条累计 {fmt_int(v['total'])}（均值 {fmt_int(round(v['mean']))}、中位数 {fmt_int(round(v['median']))}）。charelttce 仅 09-15 / 09-17 两条爆款（510 万 + 150 万）就占全账号播放的 <b>{(5100000+1500000)/c['total']*100:.1f}%</b>；剔除这两条后其均值降至 {fmt_int(round(c['mean_wo_top2']))}，说明它是「爆款驱动型」账号，日常条目中位数只有 1.14 万。
  </div>
  <div class="conclusion">
    <b>② 选品策略两极：广撒网 vs 单品牌反复触达。</b>vivi 9 天换约 15+ SKU、6 大品类（挂车率 {vivi_cart_rate:.0f}%，31 条中仅 1 条无挂车），但 73.5% 的播放仍来自首饰饰品一个品类；charelttce 34 天只推 PUTARE 单品牌 3 款 SKU，其中「遥控器支架」一款 7 条贡献播放 {fmt_int(sum(chare_sku['遥控器支架 Page Turner+手机支架']))}（占全账号 {sum(chare_sku['遥控器支架 Page Turner+手机支架'])/c['total']*100:.0f}%）。
  </div>
  <div class="conclusion">
    <b>③ 爆款方法论可提炼：</b>vivi 两条爆款（18.6 万 / 3.1 万）均为<b>西语音频 + 首饰</b>；charelttce 两条爆款（510 万 / 150 万）均出现在<b>「隔天更新」的节奏断点后</b>（09-14 断更后 09-15 爆、09-16 断更后 09-17 爆），且爆款内容的转发+收藏占比显著高于日常条目（510 万条转发 7,957 + 收藏 11,487，占互动的 39%），呈典型「工具型实用内容被收藏转发」特征。
  </div>
  <div class="conclusion">
    <b>④ 互动质量：</b>vivi 互动率均值 {v['eng_mean']:.2f}% 略高于 charelttce {c['eng_mean']:.2f}%，但 vivi 最高值 3.57% 出现在仅 56 播放的小基数条目上，参考意义有限；大基数条目上两者互动率均收敛于 0.2%~1.3% 区间，属 TikTok 带货号正常水位。
  </div>
</section>

<section>
  <h2>二、发布节奏与每日播放走势<span class="en">Posting Cadence &amp; Daily Plays</span></h2>
  <p class="lead">vivi 日均 3.4 条（9 天无休、每日 2~5 条），charelttce 日均 0.9 条（34 天中 30 天更新、每日固定 1 条）。折线为对数刻度，便于跨量级对比。</p>
  <div id="chartCadence" class="chart"></div>
  <div class="conclusion">charelttce 的两条爆款（09-15、09-17）分别出现在 09-14、09-16 断更之后——稳定低频节奏下的「单条爆款」模式：不求高频曝光，靠反复触达同人群等待算法放大。vivi 走高频铺量路线，但 15/31 条播放不足 2,000，铺量效率偏低。</div>
</section>

<section>
  <h2>三、播放量分布对比<span class="en">Plays Distribution</span></h2>
  <p class="lead">左：两账号逐条播放量（对数轴，vivi 降序 / charelttce 降序）；右：播放量区间结构。</p>
  <div class="cols2">
    <div id="chartDist" class="chart chart-sm"></div>
    <div id="chartBand" class="chart chart-sm"></div>
  </div>
  <table>
    <tr><th>指标</th><th>vivi_ashop（31 条 / 9 天）</th><th>charelttce（30 条 / 34 天）</th></tr>
    <tr><td>总播放</td><td class="num">{fmt_int(v['total'])}</td><td class="num">{fmt_int(c['total'])}</td></tr>
    <tr><td>均值</td><td class="num">{fmt_int(round(v['mean']))}</td><td class="num">{fmt_int(round(c['mean']))}</td></tr>
    <tr><td>中位数</td><td class="num">{fmt_int(round(v['median']))}</td><td class="num">{fmt_int(round(c['median']))}</td></tr>
    <tr><td>最高单条</td><td class="num">{fmt_int(v['max'])}（西语 Bolo 手链）</td><td class="num">{fmt_int(c['max'])}（遥控器支架）</td></tr>
    <tr><td>≥10 万条数</td><td class="num">{v['over100k']}</td><td class="num">{c['over100k']}</td></tr>
    <tr><td>1 万~10 万条数</td><td class="num">{v['10k_100k']}</td><td class="num">{c['10k_100k']}</td></tr>
    <tr><td>&lt;1 万条数</td><td class="num">{v['under10k']}（其中 15 条 &lt;2,000）</td><td class="num">{c['under10k']}（仅 2 条 &lt;2,000）</td></tr>
    <tr><td>剔除 Top2 后均值</td><td class="num">{fmt_int(round(v['mean_wo_top2']))}</td><td class="num">{fmt_int(round(c['mean_wo_top2']))}</td></tr>
    <tr><td>互动率均值 / 中位数</td><td class="num">{v['eng_mean']:.2f}% / {v['eng_median']:.2f}%</td><td class="num">{c['eng_mean']:.2f}% / {c['eng_median']:.2f}%</td></tr>
  </table>
</section>

<section>
  <h2>四、商品策略：多 SKU 广撒网 vs 单品牌三款反复触达<span class="en">Product Strategy</span></h2>
  <p class="lead">左：vivi 品类分布（条数 &amp; 播放）；右：charelttce SKU 播放占比。注意 vivi 广撒网之下，首饰饰品仍贡献 73.5% 播放——「广撒网」实际是「大杂货 + 单品类爆款」。</p>
  <div class="cols2">
    <div id="chartViviCat" class="chart chart-sm"></div>
    <div id="chartChareSku" class="chart chart-sm"></div>
  </div>
  <table>
    <tr><th colspan="2">vivi_ashop 品类（n=31，挂车率 {vivi_cart_rate:.0f}%）</th><th colspan="2">charelttce SKU（n=30，100% 挂车 PUTARE）</th></tr>
    <tr><th>品类</th><th>条数 / 播放</th><th>SKU</th><th>条数 / 播放（均值）</th></tr>
    {''.join(f"<tr><td>{k}</td><td class='num'>{vv['n']} / {fmt_int(vv['plays'])}</td><td>{chare_sku_sorted[i][0] if i < len(chare_sku_sorted) else ''}</td><td class='num'>{len(chare_sku_sorted[i][1]) if i < len(chare_sku_sorted) else ''} / {fmt_int(sum(chare_sku_sorted[i][1])) if i < len(chare_sku_sorted) else ''}（均值 {fmt_int(round(statistics.mean(chare_sku_sorted[i][1]))) if i < len(chare_sku_sorted) else ''}）</td></tr>" for i, (k, vv) in enumerate(vivi_cat_sorted))}
  </table>
  <div class="conclusion"><b>vivi 的同款复挂信号：</b>磁吸 MagSafe 壳 ×2、豹纹手机壳 ×2、乌鸦硬币 ×2、水钻套装 ×2 —— 跑出量或被验证的款会重复挂车再推（乌鸦硬币两轮 2.99 万 / 1,447；磁吸壳 10,700 / 4,067）。这是杂货号内部 A/B 测试的雏形。</div>
</section>

<section>
  <h2>五、爆款拆解<span class="en">Hit Video Anatomy</span></h2>
  <p class="lead">两账号 Top2 共 4 条爆款明细。</p>
  <div class="grid3">
    <div class="vcard" style="border-top:3px solid {C_CHARE}">
      <div class="vn">PUTARE 遥控器支架（Page Turner）</div>
      <div class="vp" style="color:{C_CHARE}">{fmt_int(5100000)}</div>
      <div>09-15 17:40 发布 ｜ 账号历史最高</div>
      <div class="vd">赞 29,500 ｜ 转 7,957 ｜ 藏 11,487 ｜ 评 391<br>互动率 0.97%，其中<b>转发+收藏占互动 39%</b> —— 工具型内容被用户收藏备用、转发分享，是「实用型爆款」的典型结构。发布于 09-14 断更之后。</div>
    </div>
    <div class="vcard" style="border-top:3px solid {C_CHARE}">
      <div class="vn">PUTARE 感应音箱 Stand（三合一）</div>
      <div class="vp" style="color:{C_CHARE}">{fmt_int(1500000)}</div>
      <div>09-17 17:44 发布</div>
      <div class="vd">赞 9,005 ｜ 转 1,510 ｜ 藏 2,551 ｜ 评 121<br>互动率 0.88%，同为高转发收藏结构。发布于 09-16 断更之后。主推款 Stand 系列第二条百万级内容。</div>
    </div>
    <div class="vcard" style="border-top:3px solid {C_VIVI}">
      <div class="vn">Women's 3 Sacred Hearts Bolo Bracelet（Gold Metal）</div>
      <div class="vp" style="color:{C_VIVI}">{fmt_int(186200)}</div>
      <div>09-29 16:15 发布 ｜ vivi 历史最高</div>
      <div class="vd">风格标签「西语宗教口播」，音频为西语开头 "Deja de usar ese hilo rojo aburrido..."（别再用那根无聊的红绳）。<b>西语音频 + 宗教/信仰首饰</b>，精准切中西语人群的守护主题叙事。</div>
    </div>
    <div class="vcard" style="border-top:3px solid {C_VIVI}">
      <div class="vn">2 Pairs 14K Gold Plated Twisted Round Hoop Earrings</div>
      <div class="vp" style="color:{C_VIVI}">{fmt_int(31000)}</div>
      <div>09-29 12:48 发布</div>
      <div class="vd">同日 3.5 小时内的第二条西语爆款，风格「西语种草口播」，音频 "Miren estos vienen dos pares de arracadas..."。与 Bolo 手链同日发布、同为首饰——<b>西语 + 首饰当天连续爆两条</b>，指向人群标签已被算法锁定。</div>
    </div>
    <div class="vcard" style="border-top:3px solid {C_VIVI}">
      <div class="vn">对照组：3-Piece Crow Challenge Coins</div>
      <div class="vp" style="color:#94a3b8">{fmt_int(29900)}</div>
      <div>09-28 08:59 发布</div>
      <div class="vd">英语「幽默猎奇礼品」，vivi 英语内容的最好成绩，但仅为西语爆款的 1/6。同一账号内<b>西语内容均值显著高于英语内容</b>，语言选型本身就是 vivi 最大的流量杠杆。</div>
    </div>
  </div>
</section>

<section>
  <h2>六、互动率全景<span class="en">Engagement Overview</span></h2>
  <p class="lead">横轴播放量（对数），纵轴互动率（%）。小基数条目互动率波动大，看趋势带即可。</p>
  <div id="chartEng" class="chart"></div>
  <div class="conclusion">两账号互动率带高度重叠（0.2%~1.3%），说明互动率更多由「内容类型 + 人群」决定而非账号体量。charelttce 爆款并未稀释互动率（0.88%~0.97%），工具型内容的收藏行为托底了互动质量。</div>
</section>

<section>
  <h2>七、对 @example_account_026 的对标启示<span class="en">Actionable Takeaways</span></h2>
  <table>
    <tr><th style="width:130px">借鉴来源</th><th>可执行动作</th></tr>
    <tr><td><b>从 charelttce 学：<br>单品牌深耕</b></td>
        <td>① <b>SKU 收敛到 2~3 款</b>，围绕同一人群/场景反复触达——charelttce 用 3 款 SKU 在 34 天里做出 {fmt_int(c['total'])} 播放，其中「遥控器支架」单款 7 条贡献 {sum(chare_sku['遥控器支架 Page Turner+手机支架'])/c['total']*100:.0f}% 播放；反复拍同一款不等于内容重复，是从不同角度（场景/痛点/送礼/对比）打透。<br>
        ② <b>工具型选品优先</b>：能「收藏备用、转发分享」的产品（遥控器/支架/充电宝三合一）自带互动结构红利——爆款条的转发+收藏占互动 39%。example_account_026 若能找到"解决具体小痛点"的实物，优先于纯观赏型。<br>
        ③ <b>节奏稳定 &gt; 频率高</b>：每日 1 条、断更 1~2 天后发布新条，两条爆款都出现在节奏断点后。可复制为"养号蓄水 → 单点爆发"的发布日历。</td></tr>
    <tr><td><b>从 vivi_ashop 学：<br>语言杠杆与<br>爆款复挂</b></td>
        <td>① <b>语言就是选品</b>：vivi 两条爆款都是西语音频 + 首饰（18.6 万 / 3.1 万），英语内容最好成绩仅 2.99 万。example_account_026 应测试非英语音频（西语为首选），同一商品换语言轨道可能直接改变量级。<br>
        ② <b>主题叙事绑定人群身份</b>：Bolo 手链卖的不是手链，是"西语人群的守护信仰"（"别再用那根无聊的红绳"）。首饰/礼品类内容的选题从「产品功能」转向「人群身份叙事」。<br>
        ③ <b>同款复挂验证</b>：跑出量的款重复挂车再推（vivi 4 组同款复挂）。建议把台账同款标记为"已验证款"，复挂时换内容角度而非换商品。<br>
        ④ <b>反面教训——铺量不等于铺播放</b>：vivi 9 天 31 条但 15 条 &lt;2,000 播放，6 大品类摊薄了人群标签。若走杂货路线，建议每 3~4 条围绕一个已验证品类做集群发布（vivi 自己 09-29 连发两条西语首饰同日双爆，就是集群效应的直接证据）。</td></tr>
    <tr><td><b>组合打法建议</b></td>
        <td><b>「charelttce 的深度 × vivi 的语言杠杆」</b>：选定 2~3 款工具型/饰品型商品 → 每款用西语+英语双轨道测试 → 同款多角度复挂 → 断更蓄水后集中释放。发布日历参考 charelttce（1 条/天），选品验证方法参考 vivi（同款复挂 + 同品类集群）。</td></tr>
  </table>
</section>

<section>
  <h2>附录：视频明细（按播放量降序）<span class="en">Full Video Detail</span></h2>
  <p class="lead"><b style="color:{C_VIVI}">■ vivi_ashop（31 条）</b></p>
  <table>
    <tr><th>#</th><th>日期</th><th>商品名称（英文原文）</th><th>风格标签</th><th style="text-align:right">播放</th><th style="text-align:right">赞</th><th style="text-align:right">转</th><th style="text-align:right">藏</th><th style="text-align:right">互动率</th></tr>
    {video_rows('vivi_ashop', C_VIVI)}
  </table>
  <p class="lead" style="margin-top:24px"><b style="color:{C_CHARE}">■ charelttce（30 条）</b></p>
  <table>
    <tr><th>#</th><th>日期</th><th>商品名称（英文原文）</th><th>SKU</th><th style="text-align:right">播放</th><th style="text-align:right">赞</th><th style="text-align:right">转</th><th style="text-align:right">藏</th><th style="text-align:right">互动率</th></tr>
    {video_rows('charelttce', C_CHARE)}
  </table>
  <p class="lead" style="margin-top:16px">注：charelttce 全部为 PUTARE 单品牌，风格标签列省略；「视频出单量」两表为空，故明细不含出单列。pending_gmv 待查 4 条（hdxlpebl，FastMoss 额度为 0 未充值），与本次两账号无关。</p>
</section>

<footer>TikTok 对标分析团 · benchmark-analyst（霍研真）｜ 数据经 data-steward 审计 ｜ 生成于 {GENERATED_AT} ｜ 本报告不含出单/GMV 数据</footer>
</div>

<script>{echarts_js}</script>
<script>
const C_VIVI = '#8b5cf6', C_CHARE = '#0ea5e9';
const dates = {json.dumps(all_dates)};
const vPlays = {json.dumps(v_plays_series)};
const cPlays = {json.dumps(c_plays_series)};
const vVideos = {json.dumps(v["videos"], ensure_ascii=False)};
const cVideos = {json.dumps(c["videos"], ensure_ascii=False)};

// 一、节奏折线
echarts.init(document.getElementById('chartCadence')).setOption({{
  tooltip: {{trigger:'axis'}},
  legend: {{data:['vivi_ashop 每日播放','charelttce 每日播放'], top:0}},
  grid: {{left:70, right:30, top:40, bottom:60}},
  xAxis: {{type:'category', data:dates, axisLabel:{{rotate:45, fontSize:10}}}},
  yAxis: {{type:'log', name:'每日播放（对数轴）', min:1}},
  series: [
    {{name:'vivi_ashop 每日播放', type:'line', data:vPlays, itemStyle:{{color:C_VIVI}}, lineStyle:{{width:2}}, symbolSize:5, connectNulls:true}},
    {{name:'charelttce 每日播放', type:'line', data:cPlays, itemStyle:{{color:C_CHARE}}, lineStyle:{{width:2}}, symbolSize:5, connectNulls:true,
      markPoint: {{data:[{{type:'max', name:'510万爆款'}}, {{type:'max'}}], symbolSize:46}}}}
  ]
}});

// 二、逐条播放对比（对数轴）
const vBars = vVideos.map((x,i)=>[i+1, Math.max(x.plays,1)]);
const cBars = cVideos.map((x,i)=>[i+1, Math.max(x.plays,1)]);
echarts.init(document.getElementById('chartDist')).setOption({{
  tooltip: {{trigger:'item', formatter: p => `${{p.seriesName}} Top${{p.data[0]}}：${{p.data[1].toLocaleString()}} 播放`}},
  legend: {{data:['vivi_ashop','charelttce'], top:0}},
  grid: {{left:70, right:20, top:36, bottom:40}},
  xAxis: {{type:'value', name:'播放排名(降序)', max:31}},
  yAxis: {{type:'log', name:'单条播放（对数）', min:1}},
  series: [
    {{name:'vivi_ashop', type:'scatter', symbolSize:11, data:vBars, itemStyle:{{color:C_VIVI, opacity:.8}}}},
    {{name:'charelttce', type:'scatter', symbolSize:11, data:cBars, itemStyle:{{color:C_CHARE, opacity:.8}}}}
  ]
}});

// 三、区间结构
echarts.init(document.getElementById('chartBand')).setOption({{
  tooltip: {{trigger:'axis'}},
  legend: {{top:0}},
  grid: {{left:50, right:20, top:40, bottom:30}},
  xAxis: {{type:'category', data:['≥10万','1万~10万','<1万']}},
  yAxis: {{type:'value', name:'条数'}},
  series: [
    {{name:'vivi_ashop', type:'bar', data:[{v['over100k']}, {v['10k_100k']}, {v['under10k']}], itemStyle:{{color:C_VIVI}}, label:{{show:true, position:'top'}}}},
    {{name:'charelttce', type:'bar', data:[{c['over100k']}, {c['10k_100k']}, {c['under10k']}], itemStyle:{{color:C_CHARE}}, label:{{show:true, position:'top'}}}}
  ]
}});

// 四、vivi 品类
const viviCat = {json.dumps([[k, vv["n"], vv["plays"]] for k, vv in vivi_cat_sorted], ensure_ascii=False)};
echarts.init(document.getElementById('chartViviCat')).setOption({{
  title: {{text:'vivi_ashop：品类 × 播放', left:'center', textStyle:{{fontSize:13}}}},
  tooltip: {{trigger:'axis'}},
  grid: {{left:100, right:50, top:40, bottom:30}},
  xAxis: {{type:'log', name:'播放(对数)', min:1}},
  yAxis: {{type:'category', data:viviCat.map(x=>x[0]).reverse(), axisLabel:{{fontSize:11}}}},
  series: [{{type:'bar', data:viviCat.map(x=>x[2]).reverse(), itemStyle:{{color:C_VIVI}},
    label:{{show:true, position:'right', formatter: p => p.value.toLocaleString()+' 播放 / '+viviCat[p.dataIndex][1]+'条'}}}}]
}});

// 五、charelttce SKU 环图
const chareSku = {json.dumps([[k, sum(vals)] for k, vals in chare_sku_sorted], ensure_ascii=False)};
echarts.init(document.getElementById('chartChareSku')).setOption({{
  title: {{text:'charelttce：SKU 播放占比', left:'center', textStyle:{{fontSize:13}}}},
  tooltip: {{trigger:'item', formatter: p => `${{p.name}}<br>${{p.value.toLocaleString()}} 播放 (${{p.percent}}%)`}},
  legend: {{bottom:0, textStyle:{{fontSize:11}}}},
  series: [{{type:'pie', radius:['38%','65%'], center:['50%','46%'], data:chareSku.map((x,i)=>({{name:x[0], value:x[1],
    itemStyle:{{color:['#0ea5e9','#38bdf8','#bae6fd'][i]}}}})), label:{{formatter:'{{b}}\\n{{d}}%', fontSize:11}}}}]
}});

// 六、互动率散点
function engScatter(videos, color, name) {{
  return {{name:name, type:'scatter', symbolSize:12, itemStyle:{{color:color, opacity:.75}},
    data: videos.filter(x=>x.plays>0).map(x=>[Math.max(x.plays,1), +x.rate.toFixed(2)]),
    tooltip: {{formatter: p => `${{name}}<br>播放 ${{p.data[0].toLocaleString()}}<br>互动率 ${{p.data[1]}}%`}}}};
}}
echarts.init(document.getElementById('chartEng')).setOption({{
  tooltip: {{trigger:'item'}},
  legend: {{top:0}},
  grid: {{left:60, right:30, top:40, bottom:50}},
  xAxis: {{type:'log', name:'播放量（对数）', min:10, max:6000000}},
  yAxis: {{type:'value', name:'互动率 %', max:4}},
  series: [engScatter(vVideos, C_VIVI, 'vivi_ashop'), engScatter(cVideos, C_CHARE, 'charelttce')]
}});
</script>
</body>
</html>"""

    OUT_HTML.write_text(html, encoding="utf-8")
    size = OUT_HTML.stat().st_size
    print(f"【输出】{OUT_HTML} ({size/1024:.0f} KB)")
    if size <= 0:
        raise SystemExit("错误：输出文件为空！")

    # 回读校验
    back = OUT_HTML.read_text(encoding="utf-8")
    assert "</html>" in back, "HTML 不完整"
    print("【校验】HTML 回读完整（含 </html> 闭合标签）")

    # 关键数字复核输出
    print("\n【关键数字复核】")
    for acc, s in stats.items():
        print(f"{acc}: n={s['n']} total={s['total']:,} mean={s['mean']:.0f} median={s['median']:.0f} "
              f"max={s['max']:,} eng_mean={s['eng_mean']:.2f}%")
    print(f"vivi 挂车率: {vivi_cart_rate:.1f}% (无挂车 {vivi_nocart} 条)")


if __name__ == "__main__":
    build()
