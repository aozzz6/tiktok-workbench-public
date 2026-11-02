# -*- coding: utf-8 -*-
"""migrate_14col.py — 台账全部 sheet 迁移为 14 列规范（F 后插入「商品链接」）
- 15 列旧表：插入商品链接列 + 删「出单量」「风格标签」+ 表头换 HEADER_14
- 13 列表：插入商品链接列 + 表头换 HEADER_14
- G 商品链接先留空，由 scrape_product_links.py 抓取回填
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desc_split import HEADER_14  # noqa: E402

import openpyxl  # noqa: E402

XLSX = Path(r"D:/1/对标账号/tiktok_对标.xlsx")
BAK = XLSX.with_suffix(".xlsx.bak_14col_" + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S"))


def main() -> int:
    shutil.copy2(XLSX, BAK)
    print(f"backup -> {BAK.name}")
    wb = openpyxl.load_workbook(XLSX)
    for sn in [s for s in wb.sheetnames if s.startswith("对标记录-")]:
        ws = wb[sn]
        hdr = [str(ws.cell(1, c).value or "") for c in range(1, ws.max_column + 1)]
        ncols = len(hdr)
        if ncols == 14 and hdr[6] == "商品链接":
            print(f"[skip] {sn} 已是 14 列")
            continue
        if "出单量" in "".join(hdr):          # 15 列旧规范
            ws.insert_cols(7)                  # F 后插入商品链接
            ws.delete_cols(9)                  # 删出单量（插入后原 G→H）
            # 删风格标签（原 O：插入+1、删除-1 → 15）
            ws.delete_cols(15)
        elif ncols == 13:                       # 13 列用户定稿
            ws.insert_cols(7)
        else:
            print(f"[skip] {sn} 列数 {ncols} 非预期")
            continue
        # 表头统一
        for c, h in enumerate(HEADER_14, start=1):
            ws.cell(1, c).value = h
        print(f"[migrated] {sn}: -> 14 列")
    wb.save(XLSX)
    print("saved:", XLSX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
