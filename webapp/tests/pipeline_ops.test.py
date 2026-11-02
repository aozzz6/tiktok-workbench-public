# -*- coding: utf-8 -*-
"""pipeline_ops.test.py — 待接管队列推导（隔离临时目录）

覆盖：
  · 最新 pipeline 失败 → 进队列
  · 失败后又成功 → 不进队列（自愈）
  · pipeline 成功但 ledger 失败 → 进队列
  · run_summary（更权威）覆盖 jsonl 推断，带 attempts/原因
  · 无任何数据 → 空队列且 stale=True
运行：webapp/.venv/Scripts/python.exe tests/pipeline_ops.test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402
import pipeline_ops as po  # noqa: E402

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
    except Exception as e:
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


class Sandbox:
    """把 config 的两个路径常量指到临时目录。"""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="po_"))
        self.runs = self.tmp / "runs.jsonl"
        self.reports = self.tmp / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self._orig = (config.RUNS_JSONL, config.REPORTS_DIR)

    def __enter__(self):
        config.RUNS_JSONL = self.runs
        config.REPORTS_DIR = self.reports
        return self

    def __exit__(self, *a):
        config.RUNS_JSONL, config.REPORTS_DIR = self._orig

    def write_runs(self, rows):
        with self.runs.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def write_summary(self, data):
        (self.reports / "latest_run_summary.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")


def run(idx, kind, account, status, ago=60, exit_code=0):
    return {"id": f"r{idx}", "kind": kind, "account": account, "status": status,
            "started_at": time.time() - ago, "exit_code": exit_code,
            "title": f"{kind} {account}"}


def main() -> int:
    now = time.time()

    def case_basic():
        with Sandbox() as sb:
            sb.write_runs([
                run(1, "pipeline", "accA", "failed", 3600, 1),
                run(2, "pipeline", "accB", "failed", 3900, 1),
                run(3, "pipeline", "accB", "done", 3000),
                run(4, "pipeline", "accC", "done", 1800),
                run(5, "ledger", "accC", "failed", 1700, 1),
                run(6, "pipeline", "accD", "done", 600),
                run(7, "ledger", "accD", "done", 500),
            ])
            d = po.status(now)
            names = sorted(x["account"] for x in d["takeover"])
            assert_(names == ["accA", "accC"], f"应只含 accA/accC，实得 {names}")
            assert_(d["failed"] == 2, "failed 计数不对")
            assert_(d["stale"] is True, "无 run_summary 时应 stale=True")
            a = next(x for x in d["takeover"] if x["account"] == "accA")
            assert_("失败" in a["reason"], "原因缺失败字样")
            c = next(x for x in d["takeover"] if x["account"] == "accC")
            assert_("台账" in c["reason"], f"accC 原因应为台账，实得 {c['reason']}")

    def case_window():
        with Sandbox() as sb:
            sb.write_runs([run(1, "pipeline", "old", "failed", 10 * 86400, 1)])
            d = po.status(now)
            assert_(d["takeover"] == [], "超出窗口的历史失败不该进队列")

    def case_summary_override():
        with Sandbox() as sb:
            sb.write_runs([run(1, "pipeline", "accB", "failed", 600, 1)])
            sb.write_summary({
                "date": "2026-10-19", "level": "ERROR", "total": 2,
                "counts": {"ok": 1, "warn": 0, "failed": 1},
                "ended_at": now - 500, "duration_s": 120,
                "accounts": {"accB": {"status": "failed", "anomalies": ["coverage FAIL: 缺 1 条"]}},
                "attempts": {"accB": 3},
                "takeover": [{"account": "accB", "attempts": 3, "coverage": "1/2",
                              "gates": False, "payload": "x.json",
                              "anomalies": ["coverage FAIL: 缺 1 条"]}],
            })
            d = po.status(now)
            assert_(d["failed"] == 1, "summary 应只列 1 个")
            it = d["takeover"][0]
            assert_(it["attempts"] == 3, f"attempts 应为 3，实得 {it['attempts']}")
            assert_("coverage FAIL" in it["reason"], "原因应来自 summary anomalies")
            assert_(d["summary"]["level"] == "ERROR", "summary.level 未透出")
            # 2026-10-20 整改（P2-1）：回执本身是 ERROR 时不再算「当前有效」，
            # 旧契约「有 summary 即 stale=False」正是验收点名的缺陷，此处同步更新。
            assert_(d["stale"] is True, "ERROR 回执应 stale=True")
            assert_(any("ERROR" in x for x in d["stale_reasons"]), d["stale_reasons"])

    def case_running_dead_pid():
        """★ D6-C（2026-10-24）：running 回执的 pid 已退出 → 立刻判「疑似中断」。

        旧实现只按「running 超 6h」判，进程被杀后看板要等 6 小时才敢说它断了。
        增量回执会保留已完成账号的结论，所以这里的语义是「本轮没走完，但已完成的可靠」。
        """
        with Sandbox() as sb:
            dead = 999999          # 不存在的 pid
            sb.write_summary({
                "date": "2026-10-24", "state": "running", "round_id": "r_dead",
                "source": "daily_batch", "pid": dead,
                "started_at": now - 60, "ended_at": None,
                "total": 2, "counts": {"ok": 2, "warn": 0, "failed": 0},
                "level": "INFO", "accounts": {"accA": {"status": "ok"}},
                "stages": {"precheck": {"ok": True}}, "failed_stages": [],
                "rounds": [{"round_id": "r_dead", "state": "running", "pid": dead}],
            })
            d = po.status(now)
            assert_(d["receipt"]["state"] == "running", f"state 应透出 running：{d['receipt']}")
            # 2026-10-30：契约状态保持 running（下游按它判断「本轮在写」），
            # 但必须同时派生 interrupted，前端据此显示「已中断」而不是「运行中」。
            assert_(d["receipt"]["interrupted"] is True,
                    f"进程已退出应派生 interrupted=True：{d['receipt']}")
            assert_(d["summary"]["interrupted"] is True, "summary 也要带 interrupted")
            assert_(d["stale"] is True, "pid 已退出的 running 回执应 stale=True")
            joined = " ".join(d["stale_reasons"])
            assert_("已退出" in joined, f"应说明进程已退出：{d['stale_reasons']}")
            assert_("已完成账号" in joined, f"应说明已完成账号结论仍有效：{d['stale_reasons']}")

    def case_running_live_pid():
        """紧接着的对照：pid 是活着的（本进程）→ 不得凭空判 stale。"""
        with Sandbox() as sb:
            sb.write_summary({
                "date": "2026-10-24", "state": "running", "round_id": "r_live",
                "source": "daily_batch", "pid": os.getpid(),
                "started_at": now - 60, "ended_at": None,
                "total": 1, "counts": {"ok": 1, "warn": 0, "failed": 0},
                "level": "INFO", "accounts": {"accA": {"status": "ok"}},
                "stages": {"precheck": {"ok": True}}, "failed_stages": [],
                "rounds": [{"round_id": "r_live", "state": "running", "pid": os.getpid()}],
            })
            d = po.status(now)
            assert_(d["stale"] is False,
                    f"进程还活着、刚启动 60s，不该 stale：{d['stale_reasons']}")
            assert_(d["receipt"]["interrupted"] is False,
                    "进程活着时不得派生 interrupted，否则卡片会把在跑的批次写成中断")

    def case_empty():
        with Sandbox() as sb:
            d = po.status(now)
            assert_(d["takeover"] == [] and d["failed"] == 0, "空目录应为空队列")
            assert_(d["counts"]["failed"] == 0, "counts.failed 应为 0")

    def case_ai_dedup():
        with Sandbox() as sb:
            # runs.jsonl 同一 run id 有「开始 + 终态」两条，终态必须覆盖开始态
            sb.write_runs([
                {"id": "x1", "kind": "pipeline", "account": "accE", "status": "running",
                 "started_at": now - 700},
                {"id": "x1", "kind": "pipeline", "account": "accE", "status": "done",
                 "started_at": now - 700, "exit_code": 0},
            ])
            d = po.status(now)
            assert_(d["takeover"] == [], "running→done 不应判失败")

    print("pipeline_ops：")
    for name, fn in [("基础判定（失败/自愈/台账失败）", case_basic),
                     ("时间窗口过滤", case_window),
                     ("run_summary 覆盖 jsonl", case_summary_override),
                     ("空目录", case_empty),
                     ("同 id 记录合并", case_ai_dedup),
                     ("running + pid 已退出 → 立刻判中断", case_running_dead_pid),
                     ("running + pid 存活 → 不误判", case_running_live_pid)]:
        check(name, fn)
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
