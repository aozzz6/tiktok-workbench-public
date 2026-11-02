# -*- coding: utf-8 -*-
"""rebuild_nonstandard_sheets.py — 重建非规范 sheet 并迁移人工译文
步骤：1) 旧 sheet 重命名为 *_旧_20261015（保留） 2) 收集旧表译文映射
"""
import re
import sys
from pathlib import Path

import openpyxl

XLSX = Path(r"D:/1/对标账号/tiktok_对标.xlsx")
SUFFIX = "_旧_20261015"
ACCOUNTS = ["thesymmetrymarket", "example_account_022"]


def vid_from_cell(cell) -> str:
    """F 列兼容两种格式：「https://.../video/123（ID: 123）」或纯 URL。"""
    s = str(cell or "")
    m = re.search(r"ID:\s*(\d+)", s)
    if m:
        return m.group(1)
    m = re.search(r"/video/(\d+)", s)
    if m:
        return m.group(1)
    return ""


def main() -> int:
    wb = openpyxl.load_workbook(XLSX)
    translations = {}   # {account: {vid: 译文}}

    for acc in ACCOUNTS:
        name = f"对标记录-{acc}"
        if name not in wb.sheetnames:
            print(f"[skip] {name} 不存在")
            continue
        ws = wb[name]
        # 收集旧表译文（第 13 列）与转写（第 12 列，备用）
        tr = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            vid = vid_from_cell(row[5] if len(row) > 5 else None)
            trans = row[12] if len(row) > 12 else None
            if vid and trans:
                tr[vid] = str(trans)
        translations[acc] = tr
        # 重命名保留旧表
        new_title = f"{name}{SUFFIX}"
        if new_title in wb.sheetnames:
            del wb[new_title]
        ws.title = new_title
        print(f"[renamed] {name} -> {new_title}（{ws.max_row - 1} 行，译文 {len(tr)} 条）")

    wb.save(XLSX)

    # 输出译文 JSON（apply-trans 格式：{acc: {vid: 译文}}，行号由 add_account_sheet 后的 vid->row 映射生成）
    out = Path(r"D:/workbuddy_project/tiktok/pipeline/output/trans_migrate.json")
    out.write_text(
        "\n".join(f"{acc}: {len(tr)} 条译文" for acc, tr in translations.items()),
        encoding="utf-8",
    )
    # 完整 JSON 落盘（vid 键，稍后映射行号）
    import json
    out2 = Path(r"D:/workbuddy_project/tiktok/pipeline/output/trans_migrate_full.json")
    out2.write_text(json.dumps(translations, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[saved] trans_migrate_full.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
