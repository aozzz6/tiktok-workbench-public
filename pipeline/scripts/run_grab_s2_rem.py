#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_grab_s2_rem.py — 剩余 3 账号 s2 抢救（枚举带冷却重试）

已落盘 3 账号：charelttce/greenforu/mini.cases3。
待补：selsky88(197) lifewillcool(204) kee.yaden(276)。
枚举失败=累计风控 → 冷却 120s 重试 ×3；s2 单文件最后写盘不能续传，
17:00 前跑不完的账号开机重跑。
用法: python scripts/run_grab_s2_rem.py
"""
import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

_proxy = os.environ.get("WB_PROXY") or detect_proxy()
os.environ.setdefault("HTTPS_PROXY", _proxy)
os.environ.setdefault("HTTP_PROXY", _proxy)
PIPE = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(PIPE / "scripts"))
from tk_audio_download import enumerate_videos  # noqa: E402
from proxy_detect import detect_proxy  # noqa: E402

VENV = str(PIPE / ".venv" / "Scripts" / "python.exe")
OUT = PIPE / "output"
AUX = Path(r"D:/1/对标账号")
SCR = PIPE / "scripts"
GMT8 = dt.timezone(dt.timedelta(hours=8))
d1, d2 = dt.date.fromisoformat("2026-07-08"), dt.date.fromisoformat("2026-10-08")
LOG = PIPE / "run_grab.log"
ORDER = ["selsky88", "lifewillcool", "kee.yaden"]
ENV = {**os.environ, "HTTPS_PROXY": _proxy, "HTTP_PROXY": _proxy}


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
    log(acc, step, f"exit={p.returncode} last={((tail[-1] if tail else ''))[:140]}")
    return p.returncode


def enum_with_retry(acc):
    ids_file = OUT / f"{acc}_ids.json"
    if ids_file.exists():
        return json.loads(ids_file.read_text(encoding="utf-8"))
    for attempt in range(3):
        try:
            entries = enumerate_videos(acc)
            ids = [str(e["id"]) for e in entries
                   if e.get("timestamp") and d1 <= dt.datetime.fromtimestamp(e["timestamp"], GMT8).date() <= d2]
            ids_file.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
            return ids
        except Exception as e:
            log(acc, "enum", f"attempt{attempt+1} fail: {str(e)[:90]}")
            time.sleep(120)  # 枚举风控冷却
    return []


def main():
    for acc in ORDER:
        ids = enum_with_retry(acc)
        log(acc, "ids", f"{len(ids)}")
        if not ids:
            log(acc, "skip", "no ids（枚举仍风控，开机再试）")
            continue
        time.sleep(60)
        run([str(SCR / "scrape_tiktok_video.py"), "--account", acc,
             "--ids", ",".join(ids), "--out-dir", str(OUT), "--policy", "tolerant"], acc, "s2")
        run([str(SCR / "extract_products.py"), "--account", acc,
             "--scrape", str(OUT / f"scrape_{acc}.json"),
             "--out", str(AUX / acc / f"{acc}_products.json")], acc, "product")
    log("ALL", "done", "剩余账号 s2 抢救结束")


if __name__ == "__main__":
    main()
