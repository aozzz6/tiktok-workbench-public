#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""台账磁盘直读审计脚本 (data-steward)"""
import os, sys, json, glob
from openpyxl import load_workbook

LEDGER = r"D:/1/对标账号/tiktok_对标.xlsx"

def main():
    print("="*70)
    print("LEDGER DISK AUDIT")
    print("absolute path:", os.path.abspath(LEDGER))
    print("exists:", os.path.exists(LEDGER))
    if not os.path.exists(LEDGER):
        print("!!! LEDGER NOT FOUND")
        return
    print("size:", os.path.getsize(LEDGER), "bytes")
    print("="*70)

    wb = load_workbook(LEDGER, data_only=True, read_only=False)
    print("\n[1] SHEET ENUMERATION (order = wb.sheetnames)")
    for i, name in enumerate(wb.sheetnames):
        ws = wb[name]
        print(f"  {i}: {name!r}  dims={ws.dimensions}  max_row={ws.max_row} max_col={ws.max_column}")

    # 说明 sheet
    for sn in wb.sheetnames:
        if "说明" in sn:
            ws = wb[sn]
            print(f"\n[说明页] {sn} content:")
            for r in ws.iter_rows(values_only=True):
                if any(c is not None and str(c).strip() != "" for c in r):
                    print("   ", [str(c) if c is not None else "" for c in r])

    # per 对标记录 sheet
    print("\n[2] PER-SHEET STRUCTURE")
    for sn in wb.sheetnames:
        if not sn.startswith("对标记录"):
            continue
        ws = wb[sn]
        header = [c.value for c in ws[1]]
        print(f"\n  --- {sn} ---")
        print("   header:", header)
        print("   ncols:", len(header), " max_row:", ws.max_row,
              " data_rows(approx):", ws.max_row - 1)

    # full cell dump for the first 对标记录 sheet + null stats
    print("\n[3] NULL STATS + FULL DUMP PER SHEET")
    for sn in wb.sheetnames:
        if not sn.startswith("对标记录"):
            continue
        ws = wb[sn]
        header = [c.value for c in ws[1]]
        ncol = len(header)
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        n = len(rows)
        print(f"\n  === {sn} ===  data_rows={n} ncols={ncol}")
        # null stats
        for ci in range(ncol):
            col = header[ci]
            nulls = sum(1 for r in rows if ci >= len(r) or r[ci] is None or str(r[ci]).strip() == "")
            rate = (nulls / n * 100) if n else 0
            print(f"     col[{ci:02d}] {str(col):<14} nulls={nulls:>3}/{n}  rate={rate:5.1f}%")

    wb.close()

    # raw dump to json for downstream
    wb2 = load_workbook(LEDGER, data_only=True)
    dump = {}
    for sn in wb2.sheetnames:
        ws = wb2[sn]
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        dump[sn] = rows
    wb2.close()
    out = r"D:/workbuddy_project/tiktok/pipeline/scripts/_ledger_dump.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1, default=str)
    print("\n[4] raw dump written:", out)

if __name__ == "__main__":
    main()
