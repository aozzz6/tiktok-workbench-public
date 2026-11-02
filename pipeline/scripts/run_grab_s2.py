#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_grab_s2.py — 6 账号 s2 页面抓取 + 商品链接（关机安全·curl_cffi 优先）

诊断（2026-10-08 15:03）：单 IP 代理 SSL 风控态（yt-dlp 下载层 BoringSSL 掐断），
但 curl_cffi 页面抓取正常（charelttce 验证 2 条全通）。
策略：今天 4h 窗口保 s2 全量 986 条 scrape JSON（15 列台账 10 列数据源）+ 商品链接落盘；
      s1 音频下载 + s5 转写 留开机（断点续传）。
顺序小→大（先保小号闭环）；账号间冷却 120s 防枚举风控；每步幂等+落盘可中断续跑。

用法: python scripts/run_grab_s2.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE / "scripts"))
from tk_audio_download import enumerate_videos  # noqa: E402

VENV = str(PIPE / ".venv" / "Scripts" / "python.exe")
OUT = PIPE / "output"
AUX = Path(r"D:/1/对标账号")
SCR = PIPE / "scripts"
FROM, TO = "2026-07-08", "2026-10-08"
GMT8 = dt.timezone(dt.timedelta(hours=8))
d1, d2 = dt.date.fromisoformat(FROM), dt.date.fromisoformat(TO)
LOG = PIPE / "run_grab.log"
# 体量小→大
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]
ENV = {**os.environ,
       "HTTPS_PROXY": "http://127.0.0.1:7897", "HTTP_PROXY": "http://127.0.0.1:7897"}


def log(acc, step, msg):
    line = f"[{time.strftime('%H:%M:%S')}] {acc} {step} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, acc, step):
    log(acc, step, "start ...")
    p = subprocess.run([VENV] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    tail = (p.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    log(acc, step, f"exit={p.returncode} last={last[:140]}")
    return p.returncode, last


def enum_ids(acc):
    entries = enumerate_videos(acc)
    ids = []
    for e in entries:
        ts = e.get("timestamp")
        if not ts:
            continue
        day = dt.datetime.fromtimestamp(ts, GMT8).date()
        if d1 <= day <= d2:
            ids.append(str(e["id"]))
    return ids


def main():
    for i, acc in enumerate(ORDER):
        ids_file = OUT / f"{acc}_ids.json"
        if ids_file.exists():
            ids = json.loads(ids_file.read_text(encoding="utf-8"))
            log(acc, "ids", f"cache {len(ids)}")
        else:
            log(acc, "enum", "enumerating...")
            ids = enum_ids(acc)
            ids_file.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
            log(acc, "enum", f"{len(ids)} ids saved")
        if not ids:
            log(acc, "skip", "no ids in range")
            continue
        time.sleep(60)  # 枚举后冷却再抓
        csv = ",".join(ids)
        # s2 页面抓取（curl_cffi，tolerant，--ids 全量）
        run([str(SCR / "scrape_tiktok_video.py"), "--account", acc,
             "--ids", csv, "--out-dir", str(OUT), "--policy", "tolerant"], acc, "s2")
        # 商品链接（best-effort，时间够才全量）
        run([str(SCR / "extract_products.py"), "--account", acc,
             "--scrape", str(OUT / f"scrape_{acc}.json"),
             "--out", str(AUX / acc / f"{acc}_products.json")], acc, "product")
        if i < len(ORDER) - 1:
            log(acc, "cooldown", "120s between accounts")
            time.sleep(120)
    log("ALL", "done", "s2 全量落盘（s1 音频 + s5 转写留开机）")


if __name__ == "__main__":
    main()
