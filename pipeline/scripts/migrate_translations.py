# -*- coding: utf-8 -*-
"""migrate_translations.py — 删除 13 列错表 → 按模板重建 → 回填人工译文 → 终验"""
import json
import re
import subprocess
import sys
from pathlib import Path

import openpyxl

XLSX = Path(r"D:/1/对标账号/tiktok_对标.xlsx")
TEMPLATE = "对标记录-gylwwcxb"
OUT = Path(r"D:/workbuddy_project/tiktok/pipeline/output")
SCRIPTS = Path(r"D:/workbuddy_project/tiktok/pipeline/scripts")
PY = Path(r"D:/workbuddy_project/tiktok/webapp/.venv/Scripts/python.exe")
ACCOUNTS = ["gutpickde", "thesymmetrymarket", "example_account_022"]


def vid_of(cell) -> str:
    s = str(cell or "")
    m = re.search(r"ID:\s*(\d+)", s) or re.search(r"/video/(\d+)", s)
    return m.group(1) if m else ""


def main() -> int:
    # 1. 译文池
    # gutpickde：trans_result_gutpickde.json 的键是旧 sheet 行号，按已核实的行序映射 vid
    row_vid_gut = {
        "2": "7695833699652291873", "3": "7695401805366168864",
        "4": "7695038645761215776", "5": "7694685823152770337",
        "6": "7687619324957199649",
    }
    gut = json.loads((OUT / "trans_result_gutpickde.json").read_text(encoding="utf-8"))["gutpickde"]
    trans_gut = {row_vid_gut[k]: v for k, v in gut.items() if k in row_vid_gut}
    # thesymmetrymarket：trans_migrate_full.json 已按 vid 键
    sym = json.loads((OUT / "trans_migrate_full.json").read_text(encoding="utf-8"))["thesymmetrymarket"]
    print(f"译文池：gutpickde {len(trans_gut)} 条 / thesymmetrymarket {len(sym)} 条")

    # 2. 删除 3 个 13 列错表
    wb = openpyxl.load_workbook(XLSX)
    for acc in ACCOUNTS:
        name = f"对标记录-{acc}"
        if name in wb.sheetnames:
            del wb[name]
            print(f"[del] {name}")
    wb.save(XLSX)

    # 3. 按显式模板重建
    for acc in ACCOUNTS:
        r = subprocess.run([str(PY), str(SCRIPTS / "add_account_sheet.py"),
                            "--account", acc, "--template", TEMPLATE],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else (r.stderr or "")[-120:]
        print(f"[rebuild] {acc}: {tail}")
        if r.returncode != 0:
            print(f"  FAIL rc={r.returncode}")
            return 1

    # 4. 译文回填：新表 F 列 vid → 行号 → apply-trans JSON
    wb = openpyxl.load_workbook(XLSX)
    apply_json = {}
    for acc, pool in [("gutpickde", trans_gut), ("thesymmetrymarket", sym)]:
        ws = wb[f"对标记录-{acc}"]
        rows = {}
        for r in range(2, ws.max_row + 1):
            vid = vid_of(ws.cell(r, 6).value)
            if vid in pool:
                rows[str(r)] = pool[vid]
        apply_json[acc] = rows
        print(f"[map] {acc}: {len(rows)}/{len(pool)} 条译文定位到行")
    wb.close()
    apply_path = OUT / "trans_apply_now.json"
    apply_path.write_text(json.dumps(apply_json, ensure_ascii=False, indent=1), encoding="utf-8")

    # 5. apply-trans（含重开终验）
    r = subprocess.run([str(PY), str(SCRIPTS / "build_ledger_15col.py"),
                        "apply-trans", str(apply_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").strip()[-200:])
    if r.returncode != 0:
        return 1

    # 6. verify
    r = subprocess.run([str(PY), str(SCRIPTS / "post_update.py"), "verify"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
    print(tail)
    return 0 if "verify: INFO" in (r.stdout or "") else 1


if __name__ == "__main__":
    sys.exit(main())
