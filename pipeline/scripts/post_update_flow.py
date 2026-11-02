#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""post_update_flow.py — 工作台「台账同步 + 翻译交接」一键流（2026-10-14 新增）

背景：pipeline.py 的 s3_4_sheet / s6_translate / s7_backfill 是 Agent 侧步骤，
工作台「一键运行」跑完 s1/s2/s5 后数据**不会**进台账（AGENT_PENDING_NOTE 一直提示这点）。
本脚本把其中**不需要 LLM 的部分**串成一条确定性链，让工作台能一键补齐：

  1. post_update.py write          台账同步（accounts.json 驱动，幂等对账，写前备份）
  2. post_update.py export-trans   导出待译清单 -> output/pending_trans.json
  3. post_update.py verify         磁盘终验（--skip-api，工作台在线时也可去掉该参数）

翻译本身需要 LLM（kunlun-fanyi），仍走 Agent：运行完成后把 pending_trans.json
交给 Agent（或点工作台「生成指令」），译文回来后 `post_update.py apply-trans <json>` 回填。

用法:
  .venv/Scripts/python.exe scripts/post_update_flow.py            # 全流程
  .venv/Scripts/python.exe scripts/post_update_flow.py --accounts thesymmetrymarket
  .venv/Scripts/python.exe scripts/post_update_flow.py --no-verify
退出码: 0 = 全部成功；1 = 有失败步骤（工作台运行记录会标红）
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
PY = str(PIPE / ".venv" / "Scripts" / "python.exe")
POST = str(PIPE / "scripts" / "post_update.py")
PENDING = PIPE / "output" / "pending_trans.json"


def run(step: str, args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run([PY, POST, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()][-3:]
    ok = proc.returncode == 0
    detail = (tail[-1] if tail else "")[:220] or (proc.stderr or "").strip()[-220:]
    print(f"[{step}] rc={proc.returncode} {'OK' if ok else 'FAIL'} | {detail}", flush=True)
    return ok, detail


def main() -> int:
    ap = argparse.ArgumentParser(description="台账同步 + 翻译交接 一键流")
    ap.add_argument("--accounts", nargs="*", default=None,
                    help="只处理指定账号（缺省=accounts.json 中全部 active 账号）")
    ap.add_argument("--no-verify", action="store_true", help="跳过最后一步磁盘终验")
    a = ap.parse_args()
    if a.accounts == []:
        print("[error] --accounts 需要至少一个账号，未执行")
        return 1

    extra = ["--accounts", *a.accounts] if a.accounts else []
    results = {}

    results["write"] = run("write 台账同步", ["write", *extra])
    cfg = json.loads((PIPE / "accounts.json").read_text(encoding="utf-8"))
    chosen = [x for x in cfg.get("accounts", []) if x.get("active", True)
              and (a.accounts is None or x.get("account") in a.accounts)]
    image_only = bool(chosen) and all(x.get("media_type") == "image" for x in chosen)
    if image_only:
        results["export_trans"] = (True, "图文无音频翻译，保留视频待译队列")
    else:
        results["export_trans"] = run("export-trans 导出翻译任务", ["export-trans", *extra])
    if not a.no_verify:
        results["verify"] = run("verify 磁盘终验", ["verify", "--skip-api", *extra])

    pending = {}
    if PENDING.exists():
        try:
            pending = json.loads(PENDING.read_text(encoding="utf-8"))
        except Exception:
            pass
    n_pending = sum(len(v) for v in pending.values() if isinstance(v, list))

    all_ok = all(ok for ok, _ in results.values())
    print(json.dumps({
        "steps": {k: "ok" if ok else "fail" for k, (ok, _) in results.items()},
        "pending_trans": n_pending,
        "pending_file": str(PENDING),
        "next": ("把 pending_trans.json 交给 Agent 翻译，译文回来后执行 "
                 "post_update.py apply-trans <译文json> 回填台账")
                if n_pending else "无待译条目",
        "all_ok": all_ok,
    }, ensure_ascii=False))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
