# -*- coding: utf-8 -*-
"""daily_receipt.test.py — 回执可信度与运行态查询（2026-10-20 独立验收整改）

覆盖：
  P2-2 · RunManager.running() 不受 list(limit=50) 窗口限制（含多账号逗号串）
  P2-2 · app._running_account_set() 直接消费 running()
  P2-1 · pipeline_ops.status 暴露 state/failed_stages/alerts/回执时间
  P2-1 · 前置失败回执（无 accounts、state=failed）不再被忽略
  P2-1 · 过期回执（>30h）与卡在 running 的登记（>6h）判 stale
  P1-3 · 子集补跑合并后的回执（total=2、A 仍失败）被正确读取

运行：webapp/.venv/Scripts/python.exe tests/daily_receipt.test.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import app  # noqa: E402  （import 不启动服务，仅建 FastAPI 对象）
import config  # noqa: E402
import pipeline_ops as po  # noqa: E402
import runner  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  OK  {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ERR {name} -> {e}")
        _fail += 1
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"  ERR {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def mkrun(rid, status, started_at, account="A"):
    return runner.Run(id=rid, kind="pipeline", title="t", cmd=["x"], cwd=".",
                      account=account, status=status, started_at=started_at)


class Sandbox:
    """把 config 的两个路径常量指到临时目录。"""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dr_"))
        self.runs = self.tmp / "runs.jsonl"
        self.reports = self.tmp / "reports"
        (self.reports / "2026-10-20").mkdir(parents=True, exist_ok=True)
        self._orig = (config.RUNS_JSONL, config.REPORTS_DIR)

    def __enter__(self):
        config.RUNS_JSONL = self.runs
        config.REPORTS_DIR = self.reports
        return self

    def __exit__(self, *exc):
        config.RUNS_JSONL, config.REPORTS_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def write_summary(self, data: dict, date: str = "2026-10-20") -> None:
        p = self.reports / date / "run_summary.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (self.reports / "latest_run_summary.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")


def good_summary(now=None, **over):
    now = now or time.time()
    d = {
        "date": "2026-10-20", "state": "done", "level": "INFO",
        "round_id": "20261020T080000_001_1234", "round_kind": "full",
        "started_at": now - 600, "ended_at": now - 60, "duration_s": 540.0,
        "total": 2,
        "counts": {"ok": 2, "warn": 0, "failed": 0},
        "accounts": {
            "A": {"status": "ok", "attempts": 1, "anomalies": [], "coverage": "10 条",
                  "gates": True, "duration_s": 120.0, "ledger": {"synced": True}},
            "B": {"status": "ok", "attempts": 1, "anomalies": [], "coverage": "8 条",
                  "gates": True, "duration_s": 110.0, "ledger": {"synced": True}},
        },
        "stages": {"collect": {"ok": True}, "products": {"ok": True},
                   "ledger_write": {"ok": True, "rc": 0},
                   "ledger_verify": {"ok": True, "rc": 0}},
        "failed_stages": [], "takeover": [], "alerts": [],
        "rounds": [{"round_id": "20261020T080000_001_1234", "level": "INFO"}],
    }
    d.update(over)
    return d


# ────────────────────────── P2-2 ──────────────────────────
def case_running_beyond_window():
    """60 条较新的 done 之后仍有一条 running：running() 必须仍能看到它。"""
    m = runner.RunManager()
    for i in range(60):
        r = mkrun(f"d{i}", "done", 1000 + i, account=f"acc{i}")
        m._runs[r.id] = r
    old = mkrun("old", "running", 10, account="A,B")
    m._runs[old.id] = old

    assert_("old" not in [x["id"] for x in m.list(limit=50)],
            "前置条件：list(limit=50) 应已看不到 early running（复现旧缺陷）")
    ids = [x["id"] for x in m.running()]
    assert_(ids == ["old"], f"running() 应返回全部运行中任务：{ids}")
    assert_(len(m.all()) == 61, f"all() 应返回全部任务：{len(m.all())}")


def case_running_account_set():
    class Fake:
        def running(self):
            return [{"account": "A,B"}, {"account": " C "}, {"account": ""}]

    class FakeMgr:
        MANAGER = Fake()

    orig = app.runner
    app.runner = FakeMgr
    try:
        got = app._running_account_set()
    finally:
        app.runner = orig
    assert_(got == {"A", "B", "C"}, f"应拆开逗号串并去空白：{got}")


# ────────────────────────── P2-1 ──────────────────────────
def case_fresh_ok_not_stale():
    with Sandbox() as sb:
        sb.write_summary(good_summary())
        d = po.status()
        assert_(d["stale"] is False, f"新鲜 INFO 回执不应 stale：{d['stale_reasons']}")
        assert_(d["receipt"]["state"] == "done", d["receipt"])
        assert_(d["receipt"]["failed_stages"] == [], d["receipt"])
        assert_(d["summary"]["state"] == "done", d["summary"])


def case_precheck_failed_receipt():
    """前置检查失败：accounts 为空但 state=failed，必须被消费并判 stale。"""
    with Sandbox() as sb:
        sb.write_summary(good_summary())          # 旧的成功回执
        now = time.time()
        sb.write_summary({
            "date": "2026-10-20", "state": "failed", "level": "ERROR",
            "round_id": "20261020T120000_123_9999", "round_kind": "full",
            "started_at": now - 2, "ended_at": now - 1, "duration_s": 1.0,
            "total": 0, "counts": {"ok": 0, "warn": 0, "failed": 0},
            "accounts": {}, "takeover": [],
            "stages": {"precheck": {"ok": False, "reason": "工作台不可达"}},
            "failed_stages": ["precheck"],
            "alerts": [{"stage": "precheck", "reason": "工作台不可达 http://127.0.0.1:8787",
                        "recovery": "先启动工作台服务"}],
        })
        d = po.status()
        assert_(d["summary"] is not None, "不应把失败回执当成「无回执」丢掉")
        assert_(d["summary"]["state"] == "failed", d["summary"])
        assert_(d["summary"]["failed_stages"] == ["precheck"], d["summary"])
        assert_(d["stale"] is True, "本轮失败必须 stale=True")
        assert_(any("precheck" in x for x in d["stale_reasons"]),
                f"stale_reasons 应含失败阶段：{d['stale_reasons']}")
        assert_(d["alerts"] and d["alerts"][0]["stage"] == "precheck",
                f"alerts 应透传：{d['alerts']}")


def case_expired_receipt():
    with Sandbox() as sb:
        sb.write_summary(good_summary(now=time.time() - 40 * 3600))
        d = po.status()
        assert_(d["stale"] is True, "40h 前的回执应判过期")
        assert_(any("未更新" in x for x in d["stale_reasons"]), d["stale_reasons"])
        assert_(d["receipt"]["age_s"] > 39 * 3600, d["receipt"]["age_s"])


def case_running_stuck():
    with Sandbox() as sb:
        now = time.time()
        sb.write_summary({
            "date": "2026-10-20", "state": "running", "level": "RUNNING",
            "round_id": "20261020T010000_000_1", "round_kind": "full",
            "started_at": now - 8 * 3600, "ended_at": None,
            "total": 0, "counts": {}, "accounts": {}, "takeover": [],
            "stages": {}, "failed_stages": [], "alerts": [],
        })
        d = po.status()
        assert_(d["receipt"]["state"] == "running", d["receipt"])
        assert_(d["stale"] is True, "卡住 8h 的 running 登记应判 stale")
        assert_(any("running" in x for x in d["stale_reasons"]), d["stale_reasons"])


def case_running_fresh_not_stale():
    with Sandbox() as sb:
        now = time.time()
        sb.write_summary({
            "date": "2026-10-20", "state": "running", "level": "RUNNING",
            "round_id": "20261020T181000_000_2", "round_kind": "full",
            "started_at": now - 60, "ended_at": None,
            "total": 0, "counts": {}, "accounts": {}, "takeover": [],
            "stages": {}, "failed_stages": [], "alerts": [],
        })
        d = po.status()
        assert_(d["stale"] is False, f"刚启动 1 分钟的运行登记不应 stale：{d['stale_reasons']}")


def case_partial_merged_summary():
    """子集补跑合并后：total=2、A 仍 failed、B 已 ok。"""
    with Sandbox() as sb:
        s = good_summary(level="ERROR", partial=True)
        s["counts"] = {"ok": 1, "warn": 0, "failed": 1}
        s["accounts"]["A"] = {"status": "failed", "attempts": 2,
                              "anomalies": ["pipeline failed exit=1"],
                              "coverage": "0 条", "gates": False,
                              "duration_s": 30.0, "ledger": {"synced": False}}
        s["takeover"] = [{"account": "A", "status": "failed", "stage": "collect",
                          "attempts": 2, "anomalies": ["pipeline failed exit=1"],
                          "recovery": "重跑该账号"}]
        s["alerts"] = [{"stage": "collect", "reason": "1 个账号采集失败",
                        "recovery": "按 takeover 逐账号补跑"}]
        s["failed_stages"] = []
        s["rounds"] = [{"round_id": "r1"}, {"round_id": "r2"}]
        sb.write_summary(s)
        d = po.status()
        assert_(d["summary"]["total"] == 2, d["summary"])
        assert_(d["summary"]["partial"] is True, d["summary"])
        assert_(d["receipt"]["rounds"] == 2, d["receipt"])
        names = [x["account"] for x in d["takeover"]]
        assert_(names == ["A"], f"未补跑的 A 必须仍在待接管：{names}")
        assert_(d["counts"]["failed"] == 1, d["counts"])
        assert_(d["stale"] is True, "仍有失败账号的 ERROR 回执应 stale")


print("回执可信度与运行态查询：")
for name, fn in [
    ("P2-2 running() 突破 50 条窗口（含逗号串）", case_running_beyond_window),
    ("P2-2 运行态账号集合拆分", case_running_account_set),
    ("P2-1 新鲜 INFO 回执不 stale", case_fresh_ok_not_stale),
    ("P2-1 前置失败回执被消费且 stale", case_precheck_failed_receipt),
    ("P2-1 过期回执判 stale", case_expired_receipt),
    ("P2-1 卡住 running 登记判 stale", case_running_stuck),
    ("P2-1 刚启动 running 不误判", case_running_fresh_not_stale),
    ("P1-3 合并回执读取（A 仍在待接管）", case_partial_merged_summary),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
