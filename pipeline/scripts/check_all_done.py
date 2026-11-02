#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_all_done.py — s5 转写完成度检查（6 账号 txt vs mp3 vs scrape 有效条）

exit 0 = 全部完成；exit 1 = 有缺口（打印明细）。
"""
import json
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
OUT = PIPE / "output"
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]

fails = []
print(f"{'账号':12} {'有效条':>5} {'mp3':>5} {'txt':>5}  状态")
for acc in ORDER:
    items = len(json.loads((OUT / f"scrape_{acc}.json").read_text(encoding="utf-8")).get("items", []))
    mp3 = len(list((OUT / f"{acc}_audio").glob("*.mp3")))
    txt = len(list((OUT / f"{acc}_audio" / "transcripts").glob("*.txt")))
    gap = items - txt
    ok = gap <= 0
    print(f"{acc:12} {items:5} {mp3:5} {txt:5}  {'✓' if ok else '⚠缺口'+str(gap)}")
    if not ok:
        fails.append((acc, gap))
if fails:
    print("未完成:", fails)
    sys.exit(1)
print("全部完成 ✅")
