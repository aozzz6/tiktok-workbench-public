#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_grab_6accounts.py — 6 账号近三月抓取编排（关机安全：分步独立 + 幂等 + 可中断）

顺序按区间体量小→大（先保小账号闭环）：
  charelttce(90) → greenforu(102) → mini.cases3(117) → selsky88(197) → lifewillcool(204) → kee.yaden(276)

每账号 3 步（独立子进程，上一步失败不阻塞下一步，关机续跑时自动补齐）：
  A s1 音频下载   tk_audio_download.py（幂等：已下载 mp3 → skipped）
  B s2 页面抓取   scrape_tiktok_video.py --policy tolerant（下架/私有进 errors，有效条进 items）
  C 商品挂车      extract_products.py（小黄车商品链接 → D:/1/对标账号/{acc}/{acc}_products.json）

不跑 s5 转写（medium CPU 一天跑不完 986 条，关机后开机用断点续传补，--backend openai）。
不落库（openpyxl 写库必须一次性完成，中途关机有写坏风险；待抓取+转写全齐后统一落库+终验）。

用法: python scripts/run_grab_6accounts.py
      关机前随时可中断（产物均已落盘，重跑自动补）
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from proxy_detect import detect_proxy  # noqa: E402

_proxy = os.environ.get("WB_PROXY") or detect_proxy()

PIPE = Path(__file__).resolve().parent.parent
VENV = str(PIPE / ".venv" / "Scripts" / "python.exe")
OUT_ROOT = PIPE / "output"
AUX = Path(r"D:/1/对标账号")
SCR = PIPE / "scripts"
FROM, TO = "2026-07-08", "2026-10-08"
LOG = PIPE / "run_grab.log"

# 体量小→大（近三月区间内视频数）
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]

ENV = {**os.environ,
       "HTTPS_PROXY": _proxy, "HTTP_PROXY": _proxy}


def log(acc, step, msg):
    line = f"[{time.strftime('%H:%M:%S')}] {acc} {step} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, acc, step):
    log(acc, step, "start " + " ".join(args[2:4]) + "...")
    p = subprocess.run([VENV] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    tail = (p.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    log(acc, step, f"exit={p.returncode} last={last[:160]}")
    return p.returncode, last


def main():
    for acc in ORDER:
        audio_dir = OUT_ROOT / f"{acc}_audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        prod_dir = AUX / acc
        prod_dir.mkdir(parents=True, exist_ok=True)
        prod_out = prod_dir / f"{acc}_products.json"
        scrape_json = OUT_ROOT / f"scrape_{acc}.json"

        # A: s1 音频（幂等）
        run([str(SCR / "tk_audio_download.py"), "--account", acc,
             "--from", FROM, "--to", TO, "--out", str(audio_dir)], acc, "s1")
        # B: s2 抓取 tolerant
        run([str(SCR / "scrape_tiktok_video.py"), "--account", acc,
             "--audio-dir", str(audio_dir), "--out-dir", str(OUT_ROOT),
             "--policy", "tolerant"], acc, "s2")
        # C: 商品
        run([str(SCR / "extract_products.py"), "--account", acc,
             "--scrape", str(scrape_json), "--out", str(prod_out)], acc, "product")
        log(acc, "done", "三步骤完")
    log("ALL", "done", f"{len(ORDER)} 账号抓取侧完成（转写/落库待开机续）")


if __name__ == "__main__":
    main()
