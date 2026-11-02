#!/usr/bin/env python
"""把「日更编排」交给常驻工作台托管（2026-10-25）。

问题背景
--------
自动化里直接跑 `daily_batch.py` 需要握 1.5~3 小时的 shell。实测（10-24 08:03、
10-25 00:01、10-25 08:01 三次同款）编排进程活不过第一分钟就消失，只剩服务端
那一个 pipeline run 自己跑完 —— 结果是**每天只更新 1 个账号**，其余长期过期。

做法
----
工作台（FastAPI 常驻进程）已有 `POST /api/run {"kind":"script"}` 分支，
可以让 daily_batch.py 作为**服务端托管的 run** 跑：进程挂在服务里、日志进
`/api/runs`、能停、能看，自动化只负责触发与短轮询，不握长 shell。

用法
----
  # 触发整轮（默认 --retry 2 --timeout 5400）
  python wb_daily.py
  # 只跑指定账号
  python wb_daily.py --accounts a1 a2 a3
  # 触发后最多等 110 秒再返回（自动化里短轮询用，够不上长连接）
  python wb_daily.py --wait 110
  # 只看回执，不触发
  python wb_daily.py --status

退出码：触发成功 0；工作台不可达 / 已在跑 400 等异常 1；--wait 到点没跑完 3（不代表失败）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_batch as db  # noqa: E402  复用端口发现与 _http


def _post_run(args: list[str]) -> dict:
    return db._http("/api/run", {"kind": "script",
                                 "script": "daily_batch.py",
                                 "args": args}, timeout=120)


def _run_state(run_id: str) -> dict:
    return db._http(f"/api/runs/{run_id}?since=0", timeout=60)


def _status() -> dict:
    return db._http("/api/pipeline/status", timeout=120)


def _print_status(s: dict) -> None:
    r = s.get("receipt") or {}
    print(f"回执 date={r.get('date')} state={r.get('state')} level={r.get('level')} "
          f"partial={r.get('partial')} stale={s.get('stale')}")
    print(f"  counts={s.get('counts')}  age={round(r.get('age_s') or 0)}s")
    for x in (s.get("stale_reasons") or []):
        print("  - " + x)


def main() -> int:
    ap = argparse.ArgumentParser(description="让工作台托管 daily_batch（不再握长 shell）")
    ap.add_argument("--accounts", nargs="*", default=None,
                    help="只跑这些账号；不给就是整轮")
    ap.add_argument("--retry", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--no-products", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--wait", type=float, default=0,
                    help="触发后最多等这么多秒（到点返回 3，任务仍在服务端继续）")
    ap.add_argument("--status", action="store_true", help="只读回执，不触发")
    a = ap.parse_args()

    base = db.workbench_base()
    print(f"[wb] 工作台 = {base}", flush=True)

    if a.status:
        _print_status(_status())
        return 0

    cmd = ["--retry", str(a.retry), "--timeout", str(a.timeout)]
    if a.days is not None:
        cmd += ["--days", str(a.days)]
    if a.accounts:
        cmd += ["--accounts", *a.accounts]
    if a.no_products:
        cmd.append("--no-products")
    if a.no_verify:
        cmd.append("--no-verify")

    try:
        d = _post_run(cmd)
    except Exception as e:                                   # noqa: BLE001
        body = getattr(e, "read", lambda: b"")()
        print(f"[wb] 触发失败：{type(e).__name__}: {e} {body[:300]!r}", flush=True)
        return 1

    rid = d.get("id") or ""
    print(f"[wb] 已托管 run={rid} status={d.get('status')} args={' '.join(cmd)}", flush=True)
    if not a.wait:
        return 0

    deadline = time.time() + a.wait
    last = ""
    while True:
        time.sleep(min(20, max(1, deadline - time.time())))
        s = _run_state(rid)
        st = s.get("status")
        dur = round(s.get("duration_s") or 0)
        tail = (s.get("lines") or [{}])[-1]
        line = str(tail.get("text") or tail)[:100]
        if line != last:
            print(f"[wb] {st} {dur}s | {line}", flush=True)
            last = line
        if st not in ("running", "queued"):
            print(f"[wb] 结束 status={st} rc={s.get('exit_code')} dur={dur}s", flush=True)
            _print_status(_status())
            return 0 if s.get("exit_code") == 0 else 1
        if time.time() >= deadline:
            print(f"[wb] 等满 {a.wait:.0f}s 仍在跑（服务端继续，稍后再查）", flush=True)
            _print_status(_status())
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
