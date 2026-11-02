#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_s1_fillup.py — 6 账号 s1 音频补齐循环（幂等·缺口驱动·冷却重试）

背景：SSL 软风控间歇性掐断 yt-dlp 下载层，单轮必有缺口。
本脚本按「缺口数」判断是否需重跑（幂等：已下 → skipped，只补缺口）：
  1. 读 output/{acc}_ids.json（枚举缓存）= 该账号区间内应有视频数
  2. 跑 s1（tk_audio_download）→ 数 output/{acc}_audio/*.mp3
  3. 缺口 > 0 → 冷却 90s → 重跑；最多 MAX_ROUNDS 轮
  4. 全齐或到轮次上限 → 停（不达标账号列清单，留挂机/换 IP 代理续）

用法: python scripts/run_s1_fillup.py
"""
import json
import os
import subprocess
import time
from pathlib import Path

os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
PIPE = Path(__file__).resolve().parent.parent
VENV = str(PIPE / ".venv" / "Scripts" / "python.exe")
OUT = PIPE / "output"
SCR = PIPE / "scripts"
FROM, TO = "2026-07-08", "2026-10-08"
LOG = PIPE / "run_s1_fillup.log"
MAX_ROUNDS = 5
COOLDOWN = 90
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]
ENV = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:7897", "HTTP_PROXY": "http://127.0.0.1:7897"}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def mp3_count(acc):
    return len(list((OUT / f"{acc}_audio").glob("*.mp3")))


def main():
    # 缺 ids 缓存的账号先补枚举
    for acc in ORDER:
        ids_file = OUT / f"{acc}_ids.json"
        if not ids_file.exists():
            import sys
            sys.path.insert(0, str(SCR))
            from tk_audio_download import enumerate_videos  # noqa
            import datetime as dt
            GMT8 = dt.timezone(dt.timedelta(hours=8))
            entries = enumerate_videos(acc)
            ids = [str(e["id"]) for e in entries if e.get("timestamp")
                   and dt.date(2026, 7, 8) <=
                   dt.datetime.fromtimestamp(e["timestamp"], GMT8).date() <=
                   dt.date(2026, 10, 8)]
            ids_file.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
            log(f"{acc} 补枚举 {len(ids)} ids")

    pending = set(ORDER)
    for rnd in range(1, MAX_ROUNDS + 1):
        still = []
        for acc in list(pending):
            ids = json.loads((OUT / f"{acc}_ids.json").read_text(encoding="utf-8"))
            need = len(ids)
            have = mp3_count(acc)
            if have >= need:
                log(f"[R{rnd}] {acc} 完成 {have}/{need}")
                pending.discard(acc)
                continue
            log(f"[R{rnd}] {acc} 缺口 {need - have}/{need}，跑 s1 ...")
            p = subprocess.run([VENV, str(SCR / "tk_audio_download.py"),
                                "--account", acc, "--from", FROM, "--to", TO,
                                "--out", str(OUT / f"{acc}_audio")],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=ENV)
            have2 = mp3_count(acc)
            if have2 >= need:
                log(f"[R{rnd}] {acc} 补齐 {have2}/{need}")
                pending.discard(acc)
            else:
                still.append(acc)
                log(f"[R{rnd}] {acc} 仍缺 {need - have2}（SSL 风控/下架无音轨）")
            time.sleep(COOLDOWN)
        if not pending:
            log(f"[R{rnd}] 全 6 号音频补齐 ✅")
            break
        log(f"[R{rnd}] 结束，剩余 {len(pending)} 号缺口，冷却 90s 进下一轮")
        time.sleep(COOLDOWN)
    if pending:
        log(f"达轮次上限，未补齐: {sorted(pending)}（挂机/换多IP代理后续补）")
    log("FILLUP 结束")


if __name__ == "__main__":
    main()
