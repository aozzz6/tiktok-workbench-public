# -*- coding: utf-8 -*-
"""restore_template_sheet.py — 从备份 xlsx 恢复模板 sheet（含样式/列宽/行高/冻结）到当前台账。
用法: python restore_template_sheet.py <backup_xlsx> <target_xlsx> <sheet_name>
"""
import sys
from copy import copy
import openpyxl


def main() -> int:
    bak_path, tgt_path, sheet = sys.argv[1], sys.argv[2], sys.argv[3]
    src_wb = openpyxl.load_workbook(bak_path)
    if sheet not in src_wb.sheetnames:
        print(f"backup missing sheet: {sheet}")
        return 1
    tgt_wb = openpyxl.load_workbook(tgt_path)
    if sheet in tgt_wb.sheetnames:
        print(f"target already has sheet: {sheet}")
        return 0
    ws_src = src_wb[sheet]
    ws = tgt_wb.create_sheet(sheet)

    # 列宽 / 行高
    for idx, dim in ws_src.column_dimensions.items():
        tgt_dim = ws.column_dimensions[idx]
        tgt_dim.width = dim.width
        tgt_dim.hidden = dim.hidden
    for idx, dim in ws_src.row_dimensions.items():
        tgt_dim = ws.row_dimensions[idx]
        tgt_dim.height = dim.height
        tgt_dim.hidden = dim.hidden

    # 单元格值 + 样式（官方跨工作簿方式：逐样式对象 copy，勿用 _style 索引——
    # StyleArray 索引指向源工作簿样式表，跨簿赋值会让目标 save 时 IndexError）
    for row in ws_src.iter_rows(min_row=1, max_row=ws_src.max_row,
                                max_col=ws_src.max_column):
        for s in row:
            d = ws.cell(row=s.row, column=s.column)
            d.value = s.value
            d.font = copy(s.font)
            d.fill = copy(s.fill)
            d.border = copy(s.border)
            d.alignment = copy(s.alignment)
            d.number_format = s.number_format
            d.protection = copy(s.protection)

    ws.freeze_panes = ws_src.freeze_panes
    ws.sheet_view.showGridLines = ws_src.sheet_view.showGridLines

    tgt_wb.save(tgt_path)
    print(f"restored: {sheet} rows={ws_src.max_row} cols={ws_src.max_column} "
          f"freeze={ws_src.freeze_panes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
