#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_s1_audio.py — 6 账号近三月音频下载（幂等·挂机可中断）

tk_audio_download.py 逐号下载，音频落 pipeline/output/{acc}_audio/
（与 s2 推导视频 ID 的目录一致，s5 转写也读这里）。
幂等：已下载 mp3 → skipped；断点可中断，重跑自动补齐。
顺序小→大。yt-dlp 下载层易触发 SSL 软风控，账号间冷却 90s。

用法: python scripts/run_s1_audio.py
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
LOG = PIPE / "run_s1.log"
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]
ENV = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:7897", "HTTP_PROXY": "http://127.0.0.1:7897"}


def log(acc, msg):
    line = f"[{time.strftime('%H:%M:%S')}] {acc} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    for acc in ORDER:
        audio_dir = OUT / f"{acc}_audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        p = subprocess.run([VENV, str(SCR / "tk_audio_download.py"),
                            "--account", acc, "--from", FROM, "--to", TO,
                            "--out", str(audio_dir)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=ENV)
        tail = (p.stdout or "").strip().splitlines()
        last = tail[-1] if tail else (p.stderr or "")[-200:]
        log(acc, f"s1 exit={p.returncode} {last[:200]}")
        n = len(list(audio_dir.glob("*.mp3")))
        log(acc, f"音频落盘 {n} mp3")
        time.sleep(90)  # 账号间冷却
    log("ALL", "done", "6 账号音频下载完成")


if __name__ == "__main__":
    main()
