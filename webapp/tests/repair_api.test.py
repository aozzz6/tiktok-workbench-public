# -*- coding: utf-8 -*-
"""repair_api.test.py — 一键修复的接口契约（不真正执行 pipeline）

覆盖：
  · GET  /api/repair/scan   计划可下发、不含内部字段、running 标记正确
  · POST /api/repair/start  无可修项 → 400；有流水线在跑 → 400；ids 非法/失效 → 400
  · POST /api/repair/start  正常启动 → 快照带 total/items，后台线程跑到终态后 summary 完整
  · GET  /api/repair/status 空闲态 / 运行态 / 完成态三态
  · POST /api/repair/stop   空闲时是安全操作
运行：webapp/.venv/Scripts/python.exe tests/repair_api.test.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import app  # noqa: E402
import repair  # noqa: E402
import runner  # noqa: E402
from fastapi import HTTPException  # noqa: E402

_ok = 0
_fail = 0


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


def reset():
    repair._SESSION = None


def plan_fixture(repairable=2):
    items = []
    for i in range(repairable):
        items.append({
            "id": f"rp{i + 1:02d}", "kind": "ledger", "kind_label": "台账同步",
            "account": f"acc{i}", "title": "台账补入账", "reason": "缺 1 条",
            "repairable": True, "skip_reason": "", "_ctx": {"out_dir": "D:/o"},
        })
    items.append({
        "id": f"rp{len(items) + 1:02d}", "kind": "translation", "kind_label": "翻译回填",
        "account": "", "title": "翻译回填 2 条", "reason": "N 列为空",
        "repairable": False, "skip_reason": "本地服务无 LLM",
    })
    return {
        "as_of": time.time(), "items": items,
        "counts": {"total": len(items), "repairable": repairable,
                   "skipped": len(items) - repairable},
        "busy_runs": [],
    }


def fake_run(self):
    """替身：把所有可修项标成功，然后落终态（不启动子进程）。"""
    for it in self.items:
        if it.get("repairable"):
            self._mark(it, "ok", "测试桩完成", 1.2)
        else:
            self._mark(it, "skipped", it.get("skip_reason") or "")
    with self._lock:
        self.status = "done"
        self.ended_at = time.time()


def wait_done(timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if repair._SESSION and repair._SESSION.status != "running":
            return True
        time.sleep(0.02)
    return False


print("repair_api — 接口契约")
reset()

# ── scan ──────────────────────────────────────────────────────────
with patch.object(app, "_repair_plan", lambda: plan_fixture()), \
     patch.object(repair, "is_running", lambda: False):
    out = app.repair_scan()
check("scan：返回计划、counts 正确、下发行不含 _ctx", lambda: (
    assert_(out["counts"]["repairable"] == 2, f"可修 {out['counts']['repairable']}"),
    assert_(out["running"] is False, "running 标记错"),
    assert_(all(not k.startswith("_") for it in out["items"] for k in it), "泄露内部字段"),
))

# ── start：无可修项 ───────────────────────────────────────────────
with patch.object(app, "_repair_plan", lambda: plan_fixture(repairable=0)), \
     patch.object(runner.MANAGER, "running", lambda: []):
    try:
        app.repair_start({})
        raised = None
    except HTTPException as e:
        raised = e
check("start：全部不可修 → 400 且提示人工处理", lambda: (
    assert_(raised is not None, "应抛 400"),
    assert_(raised.status_code == 400, str(raised.status_code)),
    assert_("没有可自动修复" in str(raised.detail), str(raised.detail)),
))

# ── start：有流水线在跑 → 拒绝（并发写台账护栏）───────────────────
with patch.object(app, "_repair_plan", lambda: plan_fixture()), \
     patch.object(runner.MANAGER, "running",
                  lambda: [{"id": "r1", "kind": "daily", "title": "日更"}]), \
     patch.object(repair.RepairSession, "run", fake_run):
    try:
        app.repair_start({})
        raised2 = None
    except HTTPException as e:
        raised2 = e
check("start：日更在跑时拒绝，避免并发写同一本台账", lambda: (
    assert_(raised2 is not None and raised2.status_code == 400, "应抛 400"),
    assert_("正在运行" in str(raised2.detail), str(raised2.detail)),
    assert_(repair._SESSION is None, "被拒绝时不应留会话"),
))

# ── start：ids 非法 ───────────────────────────────────────────────
with patch.object(app, "_repair_plan", lambda: plan_fixture()):
    try:
        app.repair_start({"ids": "rp01"})
        bad = None
    except HTTPException as e:
        bad = e
check("start：ids 不是数组 → 400", lambda: (
    assert_(bad is not None and bad.status_code == 400, "应抛 400")
))

with patch.object(app, "_repair_plan", lambda: plan_fixture()):
    try:
        app.repair_start({"ids": ["nope"]})
        bad2 = None
    except HTTPException as e:
        bad2 = e
check("start：ids 全部失效 → 400（提示重新扫描）", lambda: (
    assert_(bad2 is not None and bad2.status_code == 400, "应抛 400"),
    assert_("重新扫描" in str(bad2.detail), str(bad2.detail)),
))

# ── start：正常启动 → 快照 + 终态 ─────────────────────────────────
reset()
with patch.object(app, "_repair_plan", lambda: plan_fixture()), \
     patch.object(runner.MANAGER, "running", lambda: []), \
     patch.object(repair.RepairSession, "run", fake_run):
    snap = app.repair_start({})
check("start：返回运行态快照（total/done/percent/items）", lambda: (
    assert_(snap["total"] == 3, f"total {snap['total']}"),
    assert_(snap["status"] in ("running", "done"), snap["status"]),
    assert_(len(snap["items"]) == 3, "items 不全"),
    assert_("percent" in snap and "counts" in snap, "缺进度字段"),
))
check("后台线程跑到终态：summary 汇总成功/失败/跳过", lambda: (
    assert_(wait_done(), "3s 内未落终态"),
    assert_(repair.status()["status"] == "done", repair.status()["status"]),
    assert_(repair.status()["summary"]["ok"] == 2, str(repair.status()["summary"])),
    assert_(repair.status()["summary"]["skipped"] == 1, "跳过数不对"),
    assert_(repair.status()["percent"] == 100, f"percent {repair.status()['percent']}"),
))

# ── start：ids 子集 ───────────────────────────────────────────────
reset()
with patch.object(app, "_repair_plan", lambda: plan_fixture()), \
     patch.object(runner.MANAGER, "running", lambda: []), \
     patch.object(repair.RepairSession, "run", fake_run):
    snap = app.repair_start({"ids": ["rp02"]})
check("start：ids 子集只跑指定项（用于重试失败项）", lambda: (
    assert_(snap["total"] == 1, f"total {snap['total']}"),
    assert_(snap["items"][0]["id"] == "rp02", snap["items"][0]["id"]),
))
wait_done()
reset()

# ── status / stop ────────────────────────────────────────────────
check("status：无会话 → idle 且字段齐全", lambda: (
    assert_(app.repair_status()["status"] == "idle", "应为 idle"),
    assert_(app.repair_status()["percent"] == 0, "percent 应为 0"),
))
check("stop：空闲时安全返回 idle", lambda: (
    assert_(app.repair_stop()["status"] == "idle", "stop 应返回 idle")
))

# ── stop：运行中请求中止 ─────────────────────────────────────────
blocker = threading.Event()


def slow_run(self):
    blocker.wait(1.0)
    with self._lock:
        self.status = "stopped"
        self.ended_at = time.time()


reset()
with patch.object(app, "_repair_plan", lambda: plan_fixture()), \
     patch.object(runner.MANAGER, "running", lambda: []), \
     patch.object(repair.RepairSession, "run", slow_run):
    app.repair_start({})
    out = app.repair_stop()
    running_flag = repair.is_running()
check("stop：运行中调用置停止位并回快照", lambda: (
    assert_("status" in out, "应回快照"),
    assert_(repair._SESSION._stop.is_set(), "停止位未置"),
))
blocker.set()
wait_done()
reset()

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
