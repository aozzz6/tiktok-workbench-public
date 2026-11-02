# -*- coding: utf-8 -*-
"""repair_lock.test.py — R1 共享写者互斥（2026-10-24 独立验收整改）

验收报告 R1 指出的缺口：
  · 修复启动时只看已有 pipeline/daily/ledger，但修复启动**之后**，「待接管 → 重跑全部」
    仍能通过 start_retry 提交 daily —— 两个入口会同时写同一本台账；
  · 两个入口各自「先检查后启动」，同时点就是竞态。

本套用真实 RunManager + 真实子进程（python -c 替身）验证：
  1. 修复会话持租约时，start_retry 提交 daily 必须被拒绝（方向一）
  2. daily 在跑时，修复启动必须被拒绝（方向二）
  3. 修复内部的预期子任务（owner=会话租约）必须允许，且它们结束时**不**释放会话租约
  4. 两个方向同时启动：只能有一个拿到写者租约（原子性，无检查-启动窗口）
  5. 伪造/过期租约令牌不能借道启动子任务

跑法：webapp/.venv/Scripts/python.exe tests/repair_lock.test.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402
import pipeline_ops  # noqa: E402
import repair  # noqa: E402
import runner  # noqa: E402

# ★ 本套会启动真实的 daily/repair 子进程（短命 python -c）：把运行 journal 指到临时文件，
#   不要把「测试日更/并发日更」写进生产 data/runs.jsonl（那会出现在 /api/runs 与待接管统计里）。
_TMP_RUNS = Path(tempfile.mkdtemp(prefix="rplock_"))
_ORIG_RUNS = config.RUNS_JSONL
config.RUNS_JSONL = _TMP_RUNS / "runs.jsonl"

_ok = 0
_fail = 0
PY = config.python_exe()


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def item(i=1, kind="ledger", account=None):
    name = account or f"acc{i}"
    return {
        "id": f"rp{i:02d}", "kind": kind, "kind_label": repair.KIND_LABEL.get(kind, kind),
        "account": name, "title": f"{kind} 修复", "reason": "测试",
        "repairable": True, "skip_reason": "",
        "_ctx": {"out_dir": "D:/none", "audio_dir": "D:/none"},
    }


def plan(*items):
    return {"as_of": time.time(), "items": list(items),
            "counts": {"total": len(items), "repairable": len(items), "skipped": 0}}


def holders():
    return runner.RESOURCES.holders((runner.LEDGER,))


def wait_gone(timeout=12.0):
    """等所有任务停止、租约释放。"""
    end = time.time() + timeout
    while time.time() < end:
        if not runner.MANAGER.running() and not holders():
            return True
        time.sleep(0.1)
    return False


def force_cleanup():
    """用例失败时兜底：停任务、清会话、释放残留租约（只在本测试进程内）。"""
    repair._SESSION = None
    for r in runner.MANAGER.running():
        try:
            runner.MANAGER.stop(r["id"])
        except Exception:                        # noqa: BLE001
            pass
    for tok in list(getattr(runner.RESOURCES, "_leases", {})):
        runner.RESOURCES.release(tok)
    wait_gone(6)


def blocking_session(blocker, started, ended_status="done"):
    """替换 RepairSession.run：拿到租约后卡住，直到 blocker 放行。"""

    def run(self):
        started.set()
        blocker.wait(15)
        with self._lock:
            self.status = ended_status
            self.ended_at = time.time()

    return run


print("repair_lock — 共享写者互斥（R1）")

force_cleanup()

# ── 1. 方向一：修复持租约 → 待接管重跑（daily）必须被拒 ─────────────
blocker, started = threading.Event(), threading.Event()
with patch.object(repair.RepairSession, "run", blocking_session(blocker, started)):
    repair.start(plan(item(1, "ledger", "acc_a")))
    started.wait(5)
    err = None
    try:
        pipeline_ops.start_retry(["acc_a"])
    except ValueError as e:          # ResourceBusy 继承 ValueError
        err = e
    daily_running = [r for r in runner.MANAGER.running() if r.get("kind") == "daily"]
    holder_kinds = {h.get("kind") for h in holders()}
check("方向一：修复进行中，start_retry 提交 daily 被拒绝（不再并发写台账）", lambda: (
    assert_(err is not None, "start_retry 竟然成功提交了 daily"),
    assert_("台账" in str(err) or "修复" in str(err), f"错误文案没说清冲突：{err}"),
    assert_(not daily_running, f"不该有 daily 在跑：{daily_running}"),
    assert_("repair" in holder_kinds, f"租约持有者应是 repair：{holder_kinds}"),
))
check("方向一：拒绝后立即放行（不留下半启动的 daily 记录）", lambda: (
    assert_(all(r.get("kind") != "daily" for r in runner.MANAGER.all()), "留下了 daily 记录"),
))
blocker.set()
check("会话结束后租约释放（后续入口可正常启动）", lambda: (
    assert_(wait_gone(), f"仍有任务/租约：{runner.MANAGER.running()} {holders()}"),
    assert_(repair._SESSION is not None and repair._SESSION.status == "done",
            "会话应正常落 done"),
))
force_cleanup()

# ── 2. 方向二：daily 在跑 → 修复启动必须被拒 ────────────────────────
run = runner.MANAGER.start("daily", "测试日更", [PY, "-c", "import time;time.sleep(6)"],
                           account="acc_a")
err2 = None
try:
    repair.start(plan(item(1, "ledger", "acc_a")))
except ValueError as e:
    err2 = e
check("方向二：daily 持租约时，修复启动被拒绝且不留会话", lambda: (
    assert_(err2 is not None, "修复竟然启动了"),
    assert_("正在运行" in str(err2) or "台账" in str(err2), f"错误文案：{err2}"),
    assert_(repair._SESSION is None, "被拒绝时不该创建会话"),
))
runner.MANAGER.stop(run.id)
check("daily 结束后租约释放", lambda: assert_(wait_gone(), "租约未释放"))
force_cleanup()

# ── 3. 修复内部子任务：允许，且不释放会话租约 ──────────────────────
tok = runner.RESOURCES.acquire((runner.LEDGER,), kind="repair", title="会话租约")
internal = None
internal_err = None
try:
    internal = runner.MANAGER.start(
        "repair", "修复内部步骤", [PY, "-c", "import time;time.sleep(0.4)"],
        account="acc_a", owner=tok)
    try:
        runner.MANAGER.start("daily", "并发日更", [PY, "-c", "print('x')"], account="acc_a")
        daily_err = None
    except runner.ResourceBusy as e:
        daily_err = e
except Exception as e:                       # noqa: BLE001
    internal_err = e
    daily_err = None
check("修复内部子任务：owner=会话租约时允许启动（不能简单封死 runner.start）", lambda: (
    assert_(internal_err is None, f"内部子任务被挡：{internal_err}"),
    assert_(internal is not None, "内部子任务未启动"),
    assert_(daily_err is not None, "外部 daily 竟能趁修复内部子任务启动"),
))
end = time.time() + 8
while internal is not None and internal.status == "running" and time.time() < end:
    time.sleep(0.05)
check("内部子任务结束不释放会话租约（修复期间始终唯一写者）", lambda: (
    assert_(internal is not None and internal.status != "running", "内部子任务未结束"),
    assert_(runner.RESOURCES.owns(tok, (runner.LEDGER,)), "会话租约被内部子任务释放了"),
    assert_(len(holders()) == 1 and holders()[0].get("kind") == "repair", str(holders())),
))
runner.RESOURCES.release(tok)

# 伪造令牌不能借道
bogus = None
try:
    runner.MANAGER.start("repair", "伪造租约", [PY, "-c", "print('x')"], owner="deadbeef")
except runner.ResourceBusy as e:
    bogus = e
check("伪造/失效租约令牌：内部子任务入口被拒绝（不能借 owner 绕锁）", lambda: (
    assert_(bogus is not None, "伪造令牌竟然能启动"),
    assert_("租约" in str(bogus), str(bogus)),
))
force_cleanup()

# ── 4. 绕过「启动前看一眼」，验证原子锁本身（无 TOCTOU 窗口）────────
# 4a 确定性：daily 已持租约，但把 running() 替身成空（模拟「检查时还没看到」），
#    修复仍必须在**取租约**这一步被挡住 —— 证明保护不依赖那个 pre-check。
run4 = runner.MANAGER.start("daily", "测试日更", [PY, "-c", "import time;time.sleep(6)"],
                            account="acc_a")
err4 = None
with patch.object(runner.MANAGER, "running", lambda: []):
    try:
        repair.start(plan(item(1, "ledger", "acc_a")))
    except ValueError as e:
        err4 = e
check("绕过启动前检查：租约本身仍拒绝修复启动（原子，不看时序）", lambda: (
    assert_(err4 is not None, "绕过 pre-check 后修复竟然启动"),
    assert_("台账" in str(err4), f"应说明台账写者冲突：{err4}"),
    assert_(repair._SESSION is None, "被拒绝时不该创建会话"),
))
runner.MANAGER.stop(run4.id)
wait_gone(10)
force_cleanup()

# 4b 同时启动：两个线程在同一时刻争租约，只能有一个赢
def race_once():
    barrier = threading.Barrier(2)
    blocker2, started2 = threading.Event(), threading.Event()
    out: dict = {}

    def do_repair():
        barrier.wait(5)
        try:
            repair.start(plan(item(1, "ledger", "acc_a")))
            out["repair"] = True
        except ValueError as e:
            out["repair"] = False
            out["repair_err"] = str(e)

    def do_daily():
        barrier.wait(5)
        try:
            out["daily_run"] = runner.MANAGER.start(
                "daily", "并发日更", [PY, "-c", "import time;time.sleep(0.5)"], account="acc_a")
        except runner.ResourceBusy as e:
            out["daily"] = False
            out["daily_err"] = str(e)

    def won(r):
        return (1 if r.get("repair") else 0) + (1 if r.get("daily_run") else 0)

    patched_run = patch.object(repair.RepairSession, "run",
                               blocking_session(blocker2, started2))
    with patched_run, patch.object(runner.MANAGER, "running", lambda: []):
        t1 = threading.Thread(target=do_repair, daemon=True)
        t2 = threading.Thread(target=do_daily, daemon=True)
        t1.start(), t2.start()
        t1.join(10), t2.join(10)
        holders_now = holders()
        # 先断言再清理
        assert_(won(out) == 1, f"应恰好一个启动成功，实际 {won(out)}（{sorted(out)}）")
        assert_(len(holders_now) == 1, f"同时应只有一个租约持有者：{holders_now}")
        # 清理
        blocker2.set()
        if out.get("repair"):
            started2.wait(3)
        d = out.get("daily_run")
        if d is not None:
            runner.MANAGER.stop(d.id)
        end2 = time.time() + 10
        while time.time() < end2:
            if not runner.MANAGER.running() and not holders():
                break
            time.sleep(0.05)
    repair._SESSION = None
    force_cleanup()
    return out


race_results = []
try:
    for _ in range(4):
        race_results.append(race_once())
    race_ok = True
    race_err = ""
except AssertionError as e:
    race_ok, race_err = False, str(e)
    force_cleanup()


def _race_won(r):
    return (1 if r.get("repair") else 0) + (1 if r.get("daily_run") else 0)


check("同时启动（4 轮）：修复 vs daily 恰好一个拿到写者租约", lambda: (
    assert_(race_ok, race_err),
    assert_(len(race_results) == 4, f"只跑了 {len(race_results)} 轮"),
    assert_(all(_race_won(r) == 1 for r in race_results), str(race_results)),
))

force_cleanup()
config.RUNS_JSONL = _ORIG_RUNS
shutil.rmtree(_TMP_RUNS, ignore_errors=True)
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
