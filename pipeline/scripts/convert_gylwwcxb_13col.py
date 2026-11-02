# -*- coding: utf-8 -*-
"""convert_gylwwcxb_13col.py — 把 15 列旧规范的 gylwwcxb sheet 原地转换为 13 列定稿
映射：A日期/B账号/C发布时间 不变；D 标题+E 话题标签 = split_desc(旧E 文案)；
F 链接 不变；G-K = 旧 H-L（播放/赞/评/转/藏）；L = 旧 M 转写；M = 旧 N 翻译。
丢弃：旧 G 出单量、旧 O 风格标签（13 列定稿已删除这两列）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desc_split import HEADER_13, split_desc  # noqa: E402

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402

XLSX = Path(r"D:/1/对标账号/tiktok_对标.xlsx")
SHEET = "对标记录-gylwwcxb"
THIN = Side(style="thin", color="FFD9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(vertical="top", wrap_text=True)


def main() -> int:
    wb = openpyxl.load_workbook(XLSX)
    ws_old = wb[SHEET]
    rows = [list(r) for r in ws_old.iter_rows(min_row=2, values_only=True) if any(r)]
    print(f"旧表 {len(rows)} 行（15 列）")

    # 重建为 13 列
    del wb[SHEET]
    ws = wb.create_sheet(SHEET, 1)  # 放在 说明 之后（index 1）
    for c, h in enumerate(HEADER_13, start=1):
        cell = ws.cell(1, c, h)
        cell.font = Font(bold=True, size=11, color="FFFFFFFF")
        cell.fill = PatternFill("solid", fgColor="FF2F5597")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    widths = [11, 14, 17, 24, 18, 34, 8, 8, 8, 8, 8, 40, 40]
    for i, w in enumerate(widths, start=1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "C2"

    n_trans = 0
    for i, old in enumerate(rows, start=2):
        # 旧列：1日期 2账号 3发布时间 4标题 5文案(含标签) 6链接 7出单 8播放
        #       9赞 10评 11转 12藏 13备注(转写) 14备注中文翻译 15风格标签
        body, tags = split_desc(str(old[4] or ""))
        vals = [
            old[0], old[1], old[2],
            body, tags,
            old[5],
            old[7], old[8], old[9], old[10], old[11],
            old[12] or "", old[13] or "",
        ]
        if vals[12]:
            n_trans += 1
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(i, c, v)
            cell.border = BORDER
            cell.alignment = WRAP if c in (4, 12, 13) else Alignment(vertical="top")
        ws.row_dimensions[i].height = 90

    wb.save(XLSX)
    print(f"完成：{SHEET} -> 13 列，{len(rows)} 行，译文 {n_trans} 条已保留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
