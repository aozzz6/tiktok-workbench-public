# -*- coding: utf-8 -*-
"""dump_ledger_and_transcripts.py — 2026-10-08 日更写入前置：
1) dump 台账 sheet 名单/行数/E列URL→行号映射/P列标签体系
2) dump 10 条新视频的转写全文（N列用）
"""
import json
import re
from pathlib import Path
from openpyxl import load_workbook

XLSX = r"D:/1/对标账号/tiktok_对标记录.xlsx"
REPORTS = Path(r"D:/workbuddy_project/tiktok/pipeline/reports/2026-10-08")

# --- 1. ledger state ---
wb = load_workbook(XLSX, read_only=True, data_only=True)
state = {"sheets": {}}
for ws in wb.worksheets:
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    e_map = {}
    labels = {}
    for idx, r in enumerate(rows, start=1):
        if len(r) >= 5 and r[4]:  # E 列
            m = re.search(r"/video/(\d+)", str(r[4]))
            if m:
                e_map[m.group(1)] = idx
        if len(r) >= 16 and r[15]:  # P 列风格标签
            labels[str(r[15])] = labels.get(str(r[15]), 0) + 1
    state["sheets"][ws.title] = {
        "max_row": ws.max_row,
        "data_rows": sum(1 for r in rows if len(r) >= 5 and r[4]),
        "video_count": len(e_map),
        "labels": labels,
    }
    state.setdefault("e_maps", {})[ws.title] = e_map
wb.close()
(REPORTS / "ledger_state.json").write_text(
    json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
print("sheets:", {k: (v["max_row"], v["video_count"]) for k, v in state["sheets"].items()})
for sh in ("对标记录-vivi_ashop", "对标记录-kee.yaden"):
    print(f"\n[{sh}] 标签体系:", state["sheets"][sh]["labels"])

# --- 2. transcripts of new videos ---
out = {}
for acc in ("vivi_ashop", "kee.yaden"):
    payload = json.loads((REPORTS / f"ledger_update_{acc}.json").read_text(encoding="utf-8"))
    for row in payload["add_rows"]:
        tf = Path(row["transcript_file"])
        txt = tf.read_text(encoding="utf-8", errors="replace") if tf.exists() else "<MISSING>"
        # 去时间戳行（保留纯文本行）
        lines = []
        for ln in txt.splitlines():
            s = ln.strip()
            if not s:
                continue
            s = re.sub(r"^\[[\d:.,\s>]+\]\s*", "", s)   # [00:01:23] 前缀
            s = re.sub(r"^\d{1,2}:\d{2}[:\.]?\d{0,2}\s*-->\s*\d{1,2}:\d{2}[:\.]?\d{0,2}\s*", "", s)
            lines.append(s)
        out[f"{acc}|{row['video_id']}"] = {
            "desc": row["desc"],
            "chars": len(" ".join(lines)),
            "transcript": " ".join(lines),
        }
(REPORTS / "new_transcripts.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\ntranscripts dumped: {len(out)} 条 -> new_transcripts.json")
for k, v in out.items():
    print(f"  {k}  chars={v['chars']}  head={v['transcript'][:80]!r}")
