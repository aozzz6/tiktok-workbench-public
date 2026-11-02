#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_s5_transcribe.py — 6 账号 s5 转写（openai 后端·断点续传·幂等）

每个账号音频目录跑 batch_transcribe_fw.py：
  - 语言映射：lifewillcool=es（西语账号）；其余 5 号=en
  - 后端：auto（2026-10-14 起）——有 CUDA 设备走 faster-whisper+GPU（实测 ~10x 实时），
    否则退回 openai/PyTorch CPU。历史 note：本机 onnxruntime 段错误 → VAD 不可用，
    脚本内 faster 后端固定 vad_filter=False
  - 断点续传：transcripts/.transcribe_state.json，重跑只转增量
  - OMP_NUM_THREADS=8 给 s1 音频补齐留 CPU
音频后补齐的重跑本 driver 即可补转写缺口。

用法: python scripts/run_s5_transcribe.py [--accounts charelttce ...]
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
VENV = str(PIPE / ".venv" / "Scripts" / "python.exe")
OUT = PIPE / "output"
SCR = PIPE / "scripts"
LOG = PIPE / "run_s5.log"
LANG = {"charelttce": "en", "greenforu": "en", "mini.cases3": "en",
        "selsky88": "en", "lifewillcool": "es", "kee.yaden": "en"}
ORDER = ["charelttce", "greenforu", "mini.cases3", "selsky88", "lifewillcool", "kee.yaden"]
ENV = {**os.environ, "OMP_NUM_THREADS": "8", "KMP_NUM_THREADS": "8"}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    accs = sys.argv[2:] if len(sys.argv) > 2 else ORDER
    for acc in accs:
        audio_dir = OUT / f"{acc}_audio"
        if not audio_dir.exists():
            log(f"{acc} 音频目录缺失，跳过")
            continue
        n_mp3 = len(list(audio_dir.glob("*.mp3")))
        log(f"{acc} start 音频={n_mp3} lang={LANG[acc]}")
        p = subprocess.run([VENV, str(SCR / "batch_transcribe_fw.py"),
                            "--audio-dir", str(audio_dir),
                            "--model", "medium",
                            "--lang", LANG[acc],
                            "--backend", "auto"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=ENV)
        tail = (p.stdout or "").strip().splitlines()
        last = tail[-1] if tail else ""
        log(f"{acc} exit={p.returncode} {last[:160]}")
    log("S5 全账号转写结束")


if __name__ == "__main__":
    main()
