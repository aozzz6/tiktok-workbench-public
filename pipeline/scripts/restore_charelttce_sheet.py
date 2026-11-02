# -*- coding: utf-8 -*-
"""恢复脚本（2026-10-06）：重建被编辑器落盘误删的「对标记录-charelttce」分表。
数据源=scripts/ledger_write_charelttce.py（30 条翻译 Z 表）+ scripts/fill_style_labels.py（CH 标签表）
+ D:/1/对标账号/charelttce/ 下的 scrape/products/transcripts JSON（10-05 原始数据，未被修改）。
该 sheet 自 10-05 由上述两脚本创建后从未被修改，本脚本可精确还原其原始状态。
P 列直接写入风格标签（不再走'未标注'两步流）。"""
import json
from copy import copy

from openpyxl import load_workbook

XLSX = r"D:/1/对标账号/tiktok_对标记录.xlsx"
SHEET = "对标记录-charelttce"
REF_SHEET = "对标记录-vivi_ashop"
RECORD_DATE = "2026-10-05"
ACCOUNT = "charelttce"

scrape = json.load(open("D:/1/对标账号/charelttce/scrape_charelttce.json", encoding="utf-8"))
items = scrape if isinstance(scrape, list) else scrape.get("items", [])
products = json.load(open("D:/1/对标账号/charelttce/charelttce_products.json", encoding="utf-8"))
trans = json.load(open("D:/1/对标账号/charelttce/_transcripts_flat.json", encoding="utf-8"))

# —— 与 ledger_write_charelttce.py 完全一致的翻译表 ——
Z = json.load(open(__file__ + ".Z.json", encoding="utf-8")) if False else None
# 直接从原脚本提取（保持单一数据源，避免转录错误）：exec 原脚本的 Z 定义段
import re as _re
src = open("D:/workbuddy_project/tiktok/pipeline/scripts/ledger_write_charelttce.py", encoding="utf-8").read()
m = _re.search(r"Z = \{.*?\n\}", src, _re.S)
assert m, "Z 表提取失败"
Z = eval(m.group(0)[4:])  # noqa: S307 —— 源是本仓库自己的一次性脚本，非外部输入
assert len(Z) == 30

# —— 与 fill_style_labels.py 完全一致的 CH 标签表 ——
src2 = open("D:/workbuddy_project/tiktok/pipeline/scripts/fill_style_labels.py", encoding="utf-8").read()
m2 = _re.search(r"CH = \{.*?\n\}", src2, _re.S)
assert m2, "CH 表提取失败"
CH = eval(m2.group(0)[5:])  # noqa: S307
assert len(CH) == 30

items.sort(key=lambda x: x["publish_time_gmt8"], reverse=True)
assert len(items) == 30, f"items={len(items)}"
assert set(Z) == {it["id"] for it in items}, "翻译键与视频 ID 不一致"
assert set(CH) == {it["id"] for it in items}, "标签键与视频 ID 不一致"

wb = load_workbook(XLSX)
assert SHEET not in wb.sheetnames, f"{SHEET} 已存在，拒绝重复重建（幂等保护）"
ref = wb[REF_SHEET]
ws = wb.create_sheet(SHEET)

# 表头：复制 vivi 表头样式（与原脚本一致）
for col in range(1, 17):
    src_c, dst = ref.cell(1, col), ws.cell(1, col)
    dst.value = src_c.value
    dst.font = copy(src_c.font); dst.fill = copy(src_c.fill)
    dst.border = copy(src_c.border); dst.alignment = copy(src_c.alignment)
ws.row_dimensions[1].height = 30

# 列宽：照抄 vivi + 补 J-M/O（与原脚本一致）
for letter, dim in ref.column_dimensions.items():
    if dim.width:
        ws.column_dimensions[letter].width = dim.width
ws.column_dimensions["O"].width = 54.7
for letter in ("H", "I", "J", "K", "L", "M"):
    if not ws.column_dimensions[letter].width:
        ws.column_dimensions[letter].width = 9.7

# 数据行：发布时间倒序，样式照抄 vivi 数据行（与原脚本一致，含不复制 fill 的教训）
n_no_prod = n_no_trans = 0
for i, it in enumerate(items):
    vid = it["id"]
    prods = products.get(vid, [])
    if not prods:
        n_no_prod += 1
    links = "\n".join(f"https://shop.tiktok.com/view/product/{p['product_id']}" for p in prods)
    names = "\n".join(p["title"] for p in prods)
    ntxt = trans.get(vid, "")
    if not ntxt:
        n_no_trans += 1
    st = it.get("stats", {})
    row = [
        RECORD_DATE, ACCOUNT, it["publish_time_gmt8"], it.get("desc", ""),
        it.get("url", ""), links, names, None,
        st.get("playCount"), st.get("diggCount"), st.get("commentCount"),
        st.get("shareCount"), str(st.get("collectCount", "")) or None,
        ntxt, Z[vid], CH[vid],
    ]
    r = i + 2
    for col, val in enumerate(row, 1):
        src_c, dst = ref.cell(2, col), ws.cell(r, col)
        dst.value = val
        dst.font = copy(src_c.font)
        dst.border = copy(src_c.border); dst.alignment = copy(src_c.alignment)
        # 不复制 fill（vivi 第 2 行是人工涂黑标记行）

wb.save(XLSX)
print(f"恢复完成: {SHEET} rows={len(items)} no_prod={n_no_prod} no_trans={n_no_trans}")

# 磁盘终验
from collections import Counter
wb2 = load_workbook(XLSX, read_only=True)
assert SHEET in wb2.sheetnames
ws2 = wb2[SHEET]
rows = list(ws2.iter_rows(min_row=2, values_only=True))
assert len(rows) == 30, f"恢复后行数 {len(rows)} != 30"
c = Counter(r[15] for r in rows)
print("P列分布:", dict(c))
n_filled = sum(1 for r in rows if r[14])
print(f"O列翻译非空: {n_filled}/30, N列转写非空: {sum(1 for r in rows if r[13])}/30")
print("sheet顺序:", wb2.sheetnames)
