# -*- coding: utf-8 -*-
"""repair_exec.test.py — 一键修复的「执行器」行为（真跑子进程，但用 python -c 替身）

覆盖：
  · 顺序执行 + 进度快照（done/total/percent 单调递增，终态 100%）
  · 失败项捕获退出码与日志尾（不是只说「失败了」）
  · 秒退护栏：<2s 结束视为空跑（历史坑：断点续跑把步骤全跳过）
  · 超时强停：卡住的子进程被终止并给「超时」原因
  · 不可修项不启动子进程
  · 采集项复核：exit=0 但账号仍在待接管 → 判失败
运行：webapp/.venv/Scripts/python.exe tests/repair_exec.test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import config  # noqa: E402
import pipeline_ops  # noqa: E402
import repair  # noqa: E402
import runner  # noqa: E402

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


def item(i, kind="ledger", account=None, repairable=True, skip_reason=""):
    name = account or f"acc{i}"
    return {
        "id": f"rp{i:02d}", "kind": kind, "kind_label": repair.KIND_LABEL.get(kind, kind),
        "account": name, "title": f"{kind} 修复", "reason": "测试",
        "repairable": repairable, "skip_reason": skip_reason,
        "_ctx": {"out_dir": "D:/none", "audio_dir": "D:/none"},
    }


def plan(*items):
    rep = sum(1 for x in items if x["repairable"])
    return {"as_of": time.time(), "items": list(items),
            "counts": {"total": len(items), "repairable": rep,
                       "skipped": len(items) - rep}}


def script_step(code):
    """把替身脚本包成 _steps_for 的返回结构。"""
    return [("测试步骤", [PY, "-c", code])]


def run_until_done(timeout=40.0):
    """轮询进度，返回 [(percent, done)] 序列与终态快照。"""
    seq = []
    end = time.time() + timeout
    while time.time() < end:
        snap = repair.status()
        seq.append((snap.get("percent"), snap.get("done")))
        if snap.get("status") != "running":
            return seq, snap
        time.sleep(0.15)
    raise AssertionError(f"{timeout}s 内未落终态，最后快照：{repair.status()}")


print("repair_exec — 执行器")

# ── 1. 全部成功 + 进度单调 ────────────────────────────────────────
repair._SESSION = None
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_verify", lambda it: (True, "")), \
     patch.object(repair, "_steps_for",
                  lambda it, d, r, t: script_step("import time;time.sleep(0.35);print('done')")):
    repair.start(plan(item(1), item(2), item(3)))
    seq, snap = run_until_done()
percents = [p for p, _ in seq]
check("三项全成功：终态 ok=3、percent=100", lambda: (
    assert_(snap["status"] == "done", snap["status"]),
    assert_(snap["summary"]["ok"] == 3, str(snap["summary"])),
    assert_(snap["percent"] == 100, str(snap["percent"])),
))
check("进度单调不减，中间态能看到「已完成 N / 总数」", lambda: (
    assert_(all(b >= a for a, b in zip(percents, percents[1:])), str(percents)),
    assert_(any(0 < p < 100 for p in percents), f"没观察到中间进度：{percents}"),
    assert_(snap["done"] == snap["total"] == 3, f"{snap['done']}/{snap['total']}"),
))

# ── 2. 失败项带退出码与日志尾 ─────────────────────────────────────
repair._SESSION = None
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_steps_for",
                  lambda it, d, r, t: script_step(
                      "import sys;print('boom: 台账缺列「商品链接」');sys.stdout.flush();sys.exit(3)")):
    repair.start(plan(item(1)))
    _, snap = run_until_done()
detail = snap["summary"]["failed_items"][0]["reason"] if snap["summary"]["failed_items"] else ""
check("失败项：汇总里带 exit=3 与日志尾（可判断要不要重试）", lambda: (
    assert_(snap["summary"]["failed"] == 1, str(snap["summary"])),
    assert_("exit=3" in detail, detail),
    assert_("台账缺列" in detail, detail),
))

# ── 3. 秒退护栏 ──────────────────────────────────────────────────
repair._SESSION = None
with patch.object(repair, "MIN_RUNTIME_S", 2.0), \
     patch.object(repair, "_steps_for", lambda it, d, r, t: script_step("print('skip all')")):
    repair.start(plan(item(1)))
    _, snap = run_until_done()
check("秒退护栏：<2s 结束判「疑似空跑」而不是成功", lambda: (
    assert_(snap["summary"]["failed"] == 1, str(snap["summary"])),
    assert_("空跑" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
))

# ── 4. 超时强停 ──────────────────────────────────────────────────
repair._SESSION = None
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_steps_for",
                  lambda it, d, r, t: script_step("import time;time.sleep(30);print('never')")):
    repair.start(plan(item(1)), timeout=2)
    t0 = time.time()
    _, snap = run_until_done(timeout=25)
elapsed = time.time() - t0
check("超时：2s 后终止子进程，原因写「超时」", lambda: (
    assert_(snap["summary"]["failed"] == 1, str(snap["summary"])),
    assert_("超时" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
    assert_(elapsed < 20, f"实际耗时 {elapsed:.1f}s，未被及时终止"),
    assert_(not runner.MANAGER.running(), "超时后不应残留运行中任务"),
))

# ── 5. 不可修项不启动子进程 ──────────────────────────────────────
repair._SESSION = None
before_runs = len(runner.MANAGER.all())
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_steps_for", lambda it, d, r, t: script_step("print('x')")):
    repair.start(plan(item(1, repairable=False, skip_reason="本地服务无 LLM"), item(2)))
    _, snap = run_until_done()
skip_row = [x for x in snap["items"] if x["status"] == "skipped"]
check("不可修项：直接标跳过（原因透传），不为它启动子进程", lambda: (
    assert_(snap["summary"]["skipped"] == 1, str(snap["summary"])),
    assert_(len(skip_row) == 1 and "LLM" in skip_row[0]["detail"], str(skip_row)),
    assert_(len(runner.MANAGER.all()) == before_runs + 1,
            f"只该为可修项起 1 个任务，实际新增 {len(runner.MANAGER.all()) - before_runs}"),
))

# ── 6. 采集项复核：exit=0 但仍在待接管 ───────────────────────────
repair._SESSION = None
it6 = item(1, kind="collect")
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_steps_for", lambda it, d, r, t: script_step("print('ok')")), \
     patch.object(pipeline_ops, "status",
                  lambda *a, **k: {"takeover": [{"account": it6["account"],
                                                 "reason": "重跑后仍失败（exit=1）"}]}):
    repair.start(plan(it6))
    _, snap = run_until_done()
check("采集复核：退出码 0 但仍在待接管 → 判失败并说明原因", lambda: (
    assert_(snap["summary"]["failed"] == 1, str(snap["summary"])),
    assert_("仍在待接管" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
))

# ── 7. 中止 ─────────────────────────────────────────────────────
repair._SESSION = None
with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
     patch.object(repair, "_steps_for",
                  lambda it, d, r, t: script_step("import time;time.sleep(20)")):
    repair.start(plan(item(1)))
    time.sleep(1.3)
    repair.stop()
    _, snap = run_until_done(timeout=25)
check("中止：会话落 stopped，当前项记「用户中止」", lambda: (
    assert_(snap["status"] == "stopped", snap["status"]),
    assert_(snap["summary"]["failed"] + snap["summary"]["skipped"] >= 1, str(snap["summary"])),
    assert_(any("中止" in (x["detail"] or "") for x in snap["items"]), 
            str([x["detail"] for x in snap["items"]])),
    assert_(not runner.MANAGER.running(), "中止后不应残留运行中任务"),
))

repair._SESSION = None
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
