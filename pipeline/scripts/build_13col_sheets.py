# -*- coding: utf-8 -*-
"""build_13col_sheets.py — 按用户定稿 13 列规范重建三个账号 sheet
数据源：scrape JSON（A-F/G-K）+ 转写 txt（L）+ 已有人工译文（M）
规范：desc_split.py（单一事实源）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

OUTPUT_DIR = Path(r"D:/workbuddy_project/tiktok/pipeline/output")
from desc_split import HEADER_13, split_desc  # noqa: E402
from add_account_sheet import read_transcript  # noqa: E402

XLSX = Path(r"D:/1/对标账号/tiktok_对标.xlsx")
OUT = Path(r"D:/workbuddy_project/tiktok/pipeline/output")
ACCOUNTS = ["gutpickde", "thesymmetrymarket", "example_account_022"]
RECORD_DATE = "2026-10-15"

# 译文池
TRANS = {}
_gut = json.loads((OUT / "trans_result_gutpickde.json").read_text(encoding="utf-8"))["gutpickde"]
_row_vid = {"2": "7695833699652291873", "3": "7695401805366168864",
            "4": "7695038645761215776", "5": "7694685823152770337",
            "6": "7687619324957199649"}
TRANS["gutpickde"] = {_row_vid[k]: v for k, v in _gut.items() if k in _row_vid}
_sym = json.loads((OUT / "trans_migrate_full.json").read_text(encoding="utf-8"))["thesymmetrymarket"]
TRANS["thesymmetrymarket"] = {str(k): v for k, v in _sym.items()}
TRANS["example_account_022"] = {}

THIN = Side(style="thin", color="FFD9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill("solid", fgColor="FF2F5597")
HDR_FONT = Font(bold=True, size=11, color="FFFFFFFF")
WRAP = Alignment(vertical="top", wrap_text=True)
WIDTHS = [11, 14, 17, 24, 18, 34, 8, 8, 8, 8, 8, 40, 40]


def vid_of(cell) -> str:
    s = str(cell or "")
    m = re.search(r"ID:\s*(\d+)", s) or re.search(r"/video/(\d+)", s)
    return m.group(1) if m else ""


def main() -> int:
    wb = openpyxl.load_workbook(XLSX)
    for acc in ACCOUNTS:
        name = f"对标记录-{acc}"
        if name in wb.sheetnames:
            del wb[name]

        sp = OUTPUT_DIR / f"scrape_{acc}.json"
        items = json.loads(sp.read_text(encoding="utf-8")).get("items", [])
        ws = wb.create_sheet(name)

        # 表头
        for c, h in enumerate(HEADER_13, start=1):
            cell = ws.cell(1, c, h)
            cell.font = HDR_FONT
            cell.fill = HDR_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = BORDER
        for i, w in enumerate(WIDTHS, start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        ws.row_dimensions[1].height = 24
        ws.freeze_panes = "C2"

        # 数据行（发布时间倒序，与历史口径一致）
        items = sorted(items, key=lambda x: str(x.get("publish_time_gmt8", "")), reverse=True)
        r = 1
        for it in items:
            r += 1
            vid = str(it.get("id") or "")
            desc = it.get("desc") or ""
            body, tags = split_desc(desc)
            stats = it.get("stats", {}) or {}
            text, _nseg, has_audio = read_transcript(OUTPUT_DIR, acc, vid)
            note = text if has_audio else "（纯BGM，无口播）" if text == "" and has_audio else ""
            if not has_audio:
                note = "未标注"
            vals = [
                RECORD_DATE, f"@{acc}", it.get("publish_time_gmt8", ""),
                body, tags,
                f"{it.get('url', '')}（ID: {vid}）",
                int(stats.get("playCount") or 0), int(stats.get("diggCount") or 0),
                int(stats.get("commentCount") or 0), int(stats.get("shareCount") or 0),
                int(stats.get("collectCount") or 0),
                note, TRANS.get(acc, {}).get(vid, ""),
            ]
            for c, v in enumerate(vals, start=1):
                cell = ws.cell(r, c, v)
                cell.border = BORDER
                cell.alignment = WRAP if c in (4, 12, 13) else Alignment(vertical="top")
            ws.row_dimensions[r].height = 90
        print(f"[build] {name}: {r - 1} 行 x 13 列（译文 {len(TRANS.get(acc, {}))} 条）")

    wb.save(XLSX)
    print("saved:", XLSX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
