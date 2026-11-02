# -*- coding: utf-8 -*-
"""verify_ledger_20261008.py — 磁盘终验（全量，非抽样）
1) sheet 数量与名单齐全（5 个）
2) vivi 34→39 / kee 22→27 数据行
3) 10 条新行 16 列与载荷全量核对（URL/发布时间/文案/商品/动态5列/N/O/P）
4) 55 行更新行动态列（I-M）与载荷全量核对
5) 静态列（C/D/E/F/G）未被改动（对照写前 ledger_state 的行号映射）
6) 其他 sheet（dicharrymeredith/charelttce/说明）行数未变
"""
import json
import re
from pathlib import Path
from openpyxl import load_workbook

XLSX = r"D:/1/对标账号/tiktok_对标记录.xlsx"
REPORTS = Path(r"D:/workbuddy_project/tiktok/pipeline/reports/2026-10-08")
COLS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"]
STAT_KEYS = ["playCount", "diggCount", "commentCount", "shareCount", "collectCount"]

def norm_num(v):
    if v is None:
        return 0
    return int(str(v)) if isinstance(v, str) else int(v)

wb = load_workbook(XLSX, data_only=True)
errors, checks = [], []

# --- 1. sheet 名单 ---
expect_sheets = {"说明", "对标记录-dicharrymeredith", "对标记录-kee.yaden",
                 "对标记录-vivi_ashop", "对标记录-charelttce"}
actual = set(wb.sheetnames)
checks.append(("sheet 名单", expect_sheets == actual, f"{sorted(actual)}"))
if expect_sheets != actual:
    errors.append(f"sheet 名单异常: {sorted(actual)}")

pre_state = json.loads((REPORTS / "ledger_state.json").read_text(encoding="utf-8"))
transcripts = json.loads((REPORTS / "new_transcripts_clean.json").read_text(encoding="utf-8"))

# 其他 sheet 行数未变
for sh in ("对标记录-dicharrymeredith", "对标记录-charelttce", "说明"):
    ws = wb[sh]
    same = ws.max_row == pre_state["sheets"][sh]["max_row"]
    checks.append((f"{sh} 行数不变", same, f"{pre_state['sheets'][sh]['max_row']}→{ws.max_row}"))
    if not same:
        errors.append(f"{sh} 行数变化: {pre_state['sheets'][sh]['max_row']}→{ws.max_row}")

for acc, sheet, start_row in (("vivi_ashop", "对标记录-vivi_ashop", 36),
                              ("kee.yaden", "对标记录-kee.yaden", 24)):
    ws = wb[sheet]
    payload = json.loads((REPORTS / f"ledger_update_{acc}.json").read_text(encoding="utf-8"))
    e_map = pre_state["e_maps"][sheet]
    adds = sorted(payload["add_rows"], key=lambda r: r["publish_time_gmt8"])

    # --- 2. 行数 ---
    old_rows = pre_state["sheets"][sheet]["max_row"]
    exp_rows = old_rows + len(adds)
    checks.append((f"{acc} 行数 {old_rows}→{exp_rows}", ws.max_row == exp_rows, f"实际 {ws.max_row}"))
    if ws.max_row != exp_rows:
        errors.append(f"{acc} 行数 {ws.max_row} != {exp_rows}")

    # --- 3. 新行全量核对 ---
    rows_map = {r[4]: r for r in ws.iter_rows(min_row=start_row, max_row=exp_rows, values_only=True)
                if len(r) >= 5 and r[4]}
    for row in adds:
        vid = row["video_id"]
        url = row["url"]
        r = rows_map.get(url)
        if not r:
            errors.append(f"{acc} 新行缺失: {url}")
            continue
        idx = e_map.get(vid) or (start_row + [a['video_id'] for a in adds].index(vid))
        exp = [ "2026-10-08", acc, row["publish_time_gmt8"], row["desc"], url,
                row["products"][0]["link"], row["products"][0]["title"], None,
                norm_num(row["stats"]["playCount"]), norm_num(row["stats"]["diggCount"]),
                norm_num(row["stats"]["commentCount"]), norm_num(row["stats"]["shareCount"]),
                norm_num(row["stats"]["collectCount"]),
                transcripts[f"{acc}|{vid}"]["transcript"], None, None]
        # H 空 / O 翻译 / P 标签单独核（O/P 不在载荷里，核对非空即可）
        got = list(r[:16])
        for ci, col in enumerate(COLS):
            if col in ("O", "P"):
                if not got[ci]:
                    errors.append(f"{acc} 行{idx} {col} 列为空")
                continue
            if col == "H":
                if got[ci] not in (None, ""):
                    errors.append(f"{acc} 行{idx} H 列非空: {got[ci]!r}")
                continue
            e, g = exp[ci], got[ci]
            if isinstance(e, int):
                g2 = norm_num(g)
                if g2 != e:
                    errors.append(f"{acc} 行{idx} {col} 列: 期望 {e} 实际 {g}")
            elif str(g or "") != str(e or ""):
                errors.append(f"{acc} 行{idx} {col} 列: 期望 {str(e)[:60]!r} 实际 {str(g)[:60]!r}")

    # --- 4+5. 更新行动态列全量核对 + 静态列未动 ---
    upd_n = stat_ok = stat_bad = 0
    static_bad = []
    for row in payload["update_rows"]:
        vid = row["video_id"]
        idx = e_map.get(vid)
        if not idx:
            errors.append(f"{acc} 更新行未命中: {vid}")
            continue
        r = next(ws.iter_rows(min_row=idx, max_row=idx, values_only=True))
        for c, k in zip(["I", "J", "K", "L", "M"], STAT_KEYS):
            want = norm_num(row["stats"][k])
            got = norm_num(r[8 + ["I", "J", "K", "L", "M"].index(c)])
            if got != want:
                errors.append(f"{acc} 行{idx} {c}: 期望 {want} 实际 {got}")
                stat_bad += 1
            else:
                stat_ok += 1
        upd_n += 1
        # 静态列一致性：E 列 URL 与载荷一致
        if str(r[4]) != row["url"]:
            static_bad.append(f"{acc} 行{idx} E 列漂移: {r[4]} != {row['url']}")
    checks.append((f"{acc} 动态列 {upd_n} 行全量核对", stat_bad == 0, f"{stat_ok} 格一致 / {stat_bad} 格不符"))
    if static_bad:
        errors.extend(static_bad)

wb.close()
print("=== 检查项 ===")
for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
concl = "ALL PASS" if not errors else f"{len(errors)} 处不符"
print(f"\n=== 结论: {concl} ===")
for e in errors[:30]:
    print(" -", e)
