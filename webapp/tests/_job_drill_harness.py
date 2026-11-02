# -*- coding: utf-8 -*-
"""runner Job Object 演练用的最小宿主（不是测试用例，不被 *.test.py 收集）。

用真实 runner.MANAGER 拉起子任务，打印 pid / 谁在 Job 里 / 业务代码何时开始执行，
然后自己睡死，等外部把本进程强杀来模拟「webapp 崩溃」。

两种模式：
  child —— 子进程只是一个长睡进程（旧形态，验证父子两代）
  tree  —— 子进程启动后**立刻创建一个孙进程**（长睡），并把孙 pid 落盘。
           这是 P1-R2 点名要补的回归：「子任务快速创建孙进程」。

演练夹具 WB_HARNESS_SLOW_STORE=<秒>：把**第一次** journal 写入拖慢 N 秒。
  · 旧 runner 的顺序 Popen(立即运行) → store.update(pid) → Assign，拖慢 update 就等于
    把「入 Job 前的窗口」拉长 —— 子进程在这段时间里就能创建孙进程。
  · 新 runner 的顺序 Popen(挂起) → Assign → store.update → 恢复执行，拖慢 update 只能
    证明「恢复执行之前子进程一行业务代码都没跑」。
  两种顺序的差别，正是本演练要区分的东西。线上不设置该变量。

用法：python tests/_job_drill_harness.py <tmpdir> [child|tree]
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))

import config  # noqa: E402
import runner  # noqa: E402
import store  # noqa: E402

MODE = sys.argv[2] if len(sys.argv) > 2 else "child"

# 子进程脚本：第一件事写 marker（证明它开始执行了），再创建孙进程并落盘孙 pid
TREE_CHILD = r"""
import subprocess, sys, time, pathlib
tmp = pathlib.Path(sys.argv[1])
(tmp / "child_started.txt").write_text(str(time.time()))
g = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"])
(tmp / "grandchild.pid").write_text(str(g.pid))
time.sleep(300)
"""

SLEEP_CHILD = "import time; time.sleep(120)"


def _open_proc(pid: int):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    return k32.OpenProcess(0x1000, False, int(pid))


def _in_job(job, proc_handle) -> bool:
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.BOOL))
        k32.IsProcessInJob.restype = wintypes.BOOL
        res = wintypes.BOOL()
        if not k32.IsProcessInJob(wintypes.HANDLE(proc_handle), job, ctypes.byref(res)):
            return False
        return bool(res.value)
    except Exception:
        return False


def _pid_in_job(job, pid: int) -> bool:
    if not job or not pid:
        return False
    h = _open_proc(pid)
    if not h:
        return False
    try:
        return _in_job(job, h)
    finally:
        try:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(h))
        except Exception:                    # noqa: BLE001
            pass


def _install_slow_store(seconds: float) -> None:
    orig = store.update

    def slow_update(run_id, patch):
        state = slow_update.__dict__
        if not state.get("done"):
            state["done"] = True
            time.sleep(seconds)
        return orig(run_id, patch)

    store.update = slow_update
    runner.store.update = slow_update


def main() -> int:
    tmp = Path(sys.argv[1])
    config.RUNS_JSONL = tmp / "runs.jsonl"        # 不碰线上 journal
    window = float(os.environ.get("WB_HARNESS_SLOW_STORE") or 0)
    if window:
        _install_slow_store(window)

    cmd = ([sys.executable, "-c", TREE_CHILD, str(tmp)] if MODE == "tree"
           else [sys.executable, "-c", SLEEP_CHILD])
    run = runner.MANAGER.start("probe", f"job drill ({MODE})", cmd)
    for _ in range(2000):
        if run.proc is not None:
            break
        time.sleep(0.005)
    if run.proc is None:
        print(json.dumps({"error": "proc never started"}), flush=True)
        return 2

    # 窗口中途取样：此刻子进程的 marker 是否已经出现（出现 = 入 Job 前就跑了业务代码）
    marker_during_window = None
    if window:
        time.sleep(min(0.6, window / 2))
        marker_during_window = (tmp / "child_started.txt").exists()

    assigned = False
    deadline = time.time() + max(5.0, window + 3)
    while time.time() < deadline:             # 等 Assign + 恢复执行生效
        if getattr(run, "job_assigned", False):
            assigned = True
            break
        if os.environ.get("WB_RUNNER_NO_JOB") == "1":
            break                             # 对照组不挂 Job，不存在 assigned
        if (tmp / "child_started.txt").exists() and not window:
            break                             # 旧代码不会置 job_assigned，别干等
        time.sleep(0.005)
    job = getattr(run, "job", None) or runner._job_handle()

    grandchild = None
    child_started = False
    if MODE == "tree":
        for _ in range(800):                  # 恢复后子进程会立刻建孙进程
            gp = tmp / "grandchild.pid"
            if gp.exists() and gp.read_text().strip():
                grandchild = int(gp.read_text().strip())
                child_started = (tmp / "child_started.txt").exists()
                break
            time.sleep(0.01)

    print(json.dumps({
        "host": os.getpid(),
        "child": run.proc.pid,
        "job": bool(job),
        "job_name": getattr(run, "job_name", None),
        "assigned": assigned,
        "in_job": _pid_in_job(job, run.proc.pid),
        "grandchild": grandchild,
        "grandchild_in_job": (_pid_in_job(job, grandchild) if grandchild else None),
        "child_started": child_started,
        "marker_during_window": marker_during_window,
    }), flush=True)
    time.sleep(120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
