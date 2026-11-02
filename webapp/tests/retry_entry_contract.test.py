# -*- coding: utf-8 -*-
"""retry_entry_contract.test.py — 正式重跑入口 × 回执契约（2026-10-21 独立验收 P1-D）

验收报告点名的两条 P1-D 路径，这里逐条复现并锁死：

  1. **入口接线**：前端「重跑」= `POST /api/pipeline/retry` → `pipeline_ops.start_retry()`
     启动的命令必须是 `pipeline/scripts/daily_batch.py`（唯一回执契约），
     不能再是 `pipeline/daily_update.py`；定时任务 `scheduler.build_daily_cmd()` 同源。
  2. **集成回归**：
     ① 新版失败整轮（alpha 失败、beta 成功、ledger_verify 失败）
     ② 走正式重跑入口只补 alpha
     ③ 未跑账号 beta 与全局失败阶段必须仍在（旧入口会覆写成「只有 alpha、INFO、无阶段」）
  3. **旧编排器同步升级**：即使有人直接调 `daily_update.write_run_summary()` 写单账号回执，
     也必须按账号并入当日整轮，且被标成 legacy（无阶段结论 → 可信度不足）。

全部用真实 daily_batch.main / daily_update.write_run_summary / pipeline_ops.start_retry，
只 mock 传输层、子进程与时钟；写盘落在临时目录，不碰线上回执与账号数据。

跑法：webapp/.venv/Scripts/python.exe tests/retry_entry_contract.test.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time as _real_time
import traceback
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WEBAPP = TESTS.parent
TIKTOK = WEBAPP.parent
PIPE = TIKTOK / "pipeline"
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
sys.path.insert(0, str(PIPE / "scripts"))
sys.path.insert(0, str(PIPE))

import config  # noqa: E402
import runner  # noqa: E402
import scheduler  # noqa: E402
import pipeline_ops as po  # noqa: E402
import daily_batch as db  # noqa: E402
import daily_update as du  # noqa: E402

DATE = "2026-10-21"
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
        traceback.print_exc()
        print(f"  ERR {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


class FakeTime:
    def __init__(self):
        self._mono = 1000.0

    def monotonic(self):
        return self._mono

    def sleep(self, s):
        self._mono += max(0.0, float(s))

    def time(self):
        return _real_time.time()


class Env:
    """隔离环境：临时 reports + 假传输层 + 假子进程（与 test_daily_batch_repair 同构，精简版）。"""

    def __init__(self, accounts=("alpha", "beta")):
        self.tmp = Path(tempfile.mkdtemp(prefix="retry_entry_"))
        self.reports = self.tmp / "reports"
        self.out = self.tmp / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.acc_json = self.tmp / "accounts.json"
        accs = []
        for n in accounts:
            (self.out / f"scrape_{n}.json").write_text(
                json.dumps({"items": [{"id": f"{n}1"}], "errors": []}), encoding="utf-8")
            accs.append({"account": n, "active": True, "region": "us", "out_dir": str(self.out)})
        self.acc_json.write_text(json.dumps({"accounts": accs}), encoding="utf-8")
        self.posts: list[dict] = []
        self.fail_accounts: set[str] = set()
        self.verify_rc = 0
        self.write_rc = 0
        self.rid_acct: dict[str, str] = {}
        self.started = 0
        self.journal = self.tmp / "runs.jsonl"
        self._orig = {}

    def http(self, path, body=None, timeout=60):
        if path == "/api/health":
            return {"ok": True}
        if path == "/api/run":
            b = dict(body or {})
            self.posts.append(b)
            acct = b.get("account") or ""
            self.started += 1
            rid = f"run_{acct}_{self.started}"
            self.rid_acct[rid] = acct
            return {"id": rid}
        if path.startswith("/api/runs/") and path.endswith("/stop"):
            return {"ok": True}
        if path.startswith("/api/runs/"):
            rid = path.split("/")[3]
            acct = self.rid_acct.get(rid, "")
            if acct in self.fail_accounts:
                return {"status": "failed", "exit_code": 3, "duration_s": 9.0,
                        "tail": ["scrape gate failed"]}
            return {"status": "done", "exit_code": 0, "duration_s": 120.0, "tail": []}
        raise AssertionError(f"未预期的请求 {path}")

    def __enter__(self):
        self._orig = {k: getattr(db, k) for k in
                      ("REPORTS_DIR", "ACCOUNTS_JSON", "_http", "post_update",
                       "extract_products", "time", "JOURNAL")}
        db.REPORTS_DIR = self.reports
        db.ACCOUNTS_JSON = self.acc_json
        db.JOURNAL = self.journal
        db._http = self.http
        db.time = FakeTime()
        db.extract_products = lambda accs, log: {
            a["account"]: {"rc": 0} for a in json.loads(
                self.acc_json.read_text(encoding="utf-8"))["accounts"]}

        def _pu(sub, log, timeout=3600):
            rc = self.write_rc if sub == "write" else self.verify_rc
            return {"rc": rc, "out": f"{sub} rc={rc}"}

        db.post_update = _pu
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(db, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def run(self, *args):
        argv = ["daily_batch.py", "--date", DATE, "--poll", "5", *args]
        old = sys.argv
        sys.argv = argv
        try:
            return db.main()
        finally:
            sys.argv = old

    def summary(self):
        p = self.reports / DATE / "run_summary.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def latest(self):
        p = self.reports / "latest_run_summary.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ────────────────────────── 用例 ──────────────────────────
def case_entry_points_use_daily_batch():
    """正式重跑入口与定时任务都必须拉起 scripts/daily_batch.py，不能再走 daily_update.py。"""
    captured = {}

    class FakeRun:
        id = "run_fake"

        def snapshot(self):
            return {"id": self.id}

    class FakeMgr:
        def start(self, **kw):
            captured.update(kw)
            return FakeRun()

    orig = runner.MANAGER
    runner.MANAGER = FakeMgr()
    try:
        po.start_retry(["alpha"], retry=2, timeout=900)
    finally:
        runner.MANAGER = orig
    cmd = " ".join(captured.get("cmd") or [])
    assert_(captured.get("cmd"), "start_retry 没有启动命令")
    assert_("scripts" in cmd and "daily_batch.py" in cmd,
            f"重跑入口必须走 scripts/daily_batch.py：{cmd}")
    assert_("daily_update.py" not in cmd, f"重跑入口不得再走旧编排：{cmd}")
    assert_("--accounts alpha" in cmd, f"应只补跑指定账号：{cmd}")
    assert_("--retry 2" in cmd, f"应透传重试次数：{cmd}")

    sched = " ".join(scheduler.build_daily_cmd(["alpha"]))
    assert_("scripts" in sched and "daily_batch.py" in sched,
            f"定时任务必须走 scripts/daily_batch.py：{sched}")
    assert_("daily_update.py" not in sched, f"定时任务不得再走旧编排：{sched}")


def case_retry_entry_keeps_unrun_accounts_and_stage_failures():
    """① 新版失败整轮 → ② 正式重跑入口只补 alpha → ③ beta 与 ledger_verify 失败必须仍在。"""
    with Env() as e:
        # ① 整轮：alpha 采集失败、beta 采集成功（全局终验失败 → warn）、ledger_verify 失败
        e.fail_accounts = {"alpha"}
        e.verify_rc = 1
        rc1 = e.run("--retry", "1")
        s1 = e.summary()
        beta_before = s1["accounts"]["beta"]
        assert_(rc1 == 1, f"整轮应 exit=1，实际 {rc1}")
        assert_(sorted(s1["accounts"]) == ["alpha", "beta"],
                f"整轮账号：{list(s1['accounts'])}")
        assert_("ledger_verify" in s1["failed_stages"],
                f"整轮应有 ledger_verify 失败：{s1['failed_stages']}")
        assert_([x["account"] for x in s1["takeover"]] == ["alpha"],
                f"整轮待接管应为 alpha：{s1['takeover']}")

        # ② 正式重跑入口 → 取它真正会执行的参数（只补 alpha，alpha 仍失败、终验仍失败）
        captured = {}

        class FakeRun:
            def snapshot(self):
                return {"id": "run_fake"}

        class FakeMgr:
            def start(self, **kw):
                captured.update(kw)
                return FakeRun()

        orig = runner.MANAGER
        runner.MANAGER = FakeMgr()
        try:
            po.start_retry(["alpha"], retry=2, timeout=900)
        finally:
            runner.MANAGER = orig
        entry_args = list(captured["cmd"][2:])
        assert_("--accounts" in entry_args, f"入口参数应含 --accounts：{entry_args}")

        e.posts.clear()
        e.verify_rc = 1
        rc2 = e.run(*entry_args)
        s2 = e.summary()

        # ③ 断言：旧入口会覆写成「只有 alpha、INFO、无阶段」，新入口必须保留一切
        assert_(rc2 == 1, f"重跑仍失败时 exit=1，实际 {rc2}")
        assert_(sorted(s2["accounts"]) == ["alpha", "beta"],
                f"未跑账号 beta 不得消失：{list(s2['accounts'])}")
        assert_(s2["total"] == 2, f"total 应保持 2，实际 {s2['total']}")
        assert_(s2["accounts"]["beta"] == beta_before,
                f"未跑账号 beta 的结果应逐字段不变：{s2['accounts']['beta']} != {beta_before}")
        assert_("ledger_verify" in s2["failed_stages"],
                f"全局失败阶段不得被抹掉：{s2['failed_stages']}")
        assert_(s2["level"] == "ERROR", f"仍有失败应是 ERROR：{s2['level']}")
        assert_(s2["partial"] is True, "子集补跑应标 partial")
        assert_(len(s2["rounds"]) == 2, f"应有 2 轮记录：{len(s2['rounds'])}")
        assert_(s2["source"] == "daily_batch", s2.get("source"))
        assert_((s2.get("contract") or {}).get("legacy_stages") is False,
                f"新契约不应被标 legacy：{s2.get('contract')}")
        assert_([x["account"] for x in s2["takeover"]] == ["alpha"],
                f"待接管仍应只有 alpha：{s2['takeover']}")


def case_legacy_writer_merges_instead_of_overwriting():
    """旧编排器（daily_update.write_run_summary）单账号回执不得抹掉整轮与全局失败。"""
    with Env() as e:
        e.fail_accounts = {"alpha"}
        e.verify_rc = 1
        e.run("--retry", "1")
        before = e.summary()
        assert_(sorted(before["accounts"]) == ["alpha", "beta"], "前置：整轮应有 alpha/beta")
        assert_("ledger_verify" in before["failed_stages"], "前置：应有 ledger_verify 失败")

        # 旧路：只有 beta 成功的一次单账号回执（这正是 12:47 那份 1560 字节回执的形态）
        cfg = {"reports_root": str(e.reports)}
        results = {"beta": {"status": "ok", "anomalies": [], "coverage": "1 条",
                            "gates": True, "duration_s": 12.0,
                            "ledger": {"synced": True, "payload": ""}}}
        summary = du.write_run_summary(cfg, results, {"beta": 1}, DATE,
                                       e.reports / DATE, _real_time.time() - 30, "INFO")
        after = e.summary()
        latest = e.latest()

        assert_("state" in after, f"旧路回执也必须带 state：{list(after)}")
        assert_(after.get("round_id"), "旧路回执必须有 round_id")
        assert_(after.get("source") == "daily_update", f"应标来源：{after.get('source')}")
        assert_(sorted(after["accounts"]) == ["alpha", "beta"],
                f"alpha 不得被单账号回执抹掉：{list(after['accounts'])}")
        assert_(after["accounts"]["alpha"]["status"] == "failed", "alpha 应仍为 failed")
        assert_("ledger_verify" in after["failed_stages"],
                f"全局失败阶段不得消失：{after['failed_stages']}")
        assert_(after["level"] == "ERROR", f"仍应有失败 → ERROR：{after['level']}")
        assert_((after.get("contract") or {}).get("legacy_stages") is True,
                f"旧路回执应标 legacy_stages：{after.get('contract')}")
        assert_(latest and latest["total"] == after["total"],
                "latest 指针必须与当日合并视图一致")
        assert_(len(after.get("rounds") or []) >= 2, f"轮次应累加：{after.get('rounds')}")

        # 返回值也要能支撑旧调用方按 counts/takeover 读
        assert_(summary["takeover"] and summary["takeover"][0]["account"] == "alpha",
                f"返回值 takeover 应含 alpha：{summary['takeover']}")


def case_pipeline_status_marks_legacy_low_confidence():
    """消费端：旧格式回执即便 level=INFO 也要标「可信度不足」。"""
    tmp = Path(tempfile.mkdtemp(prefix="legacy_receipt_"))
    orig_runs, orig_reports = config.RUNS_JSONL, config.REPORTS_DIR
    try:
        config.RUNS_JSONL = tmp / "runs.jsonl"
        config.REPORTS_DIR = tmp / "reports"
        (config.REPORTS_DIR / "2026-10-21").mkdir(parents=True, exist_ok=True)
        legacy = {"date": "2026-10-21", "level": "INFO",
                  "started_at": _real_time.time() - 60,
                  "ended_at": _real_time.time() - 10, "duration_s": 50.0, "total": 2,
                  "counts": {"ok": 2, "warn": 0, "failed": 0},
                  "attempts": {"alpha": 1, "beta": 1}, "takeover": [],
                  "accounts": {"alpha": {"status": "ok"}, "beta": {"status": "ok"}}}
        text = json.dumps(legacy, ensure_ascii=False)
        (config.REPORTS_DIR / "latest_run_summary.json").write_text(text, encoding="utf-8")
        (config.REPORTS_DIR / "2026-10-21" / "run_summary.json").write_text(
            text, encoding="utf-8")
        d = po.status()
        assert_(d["receipt"]["legacy"] is True, f"应识别为旧格式：{d['receipt']}")
        assert_(d["receipt"]["confidence"] == "low", d["receipt"])
        assert_(d["stale"] is True, "旧格式绿回执必须 stale=True（可信度不足）")
        assert_(any("缺阶段" in x for x in d["stale_reasons"]),
                f"原因应写明缺阶段结论：{d['stale_reasons']}")
        assert_(any("轮" in x for x in d["stale_reasons"]),
                f"原因应写明缺口轮标识：{d['stale_reasons']}")
    finally:
        config.RUNS_JSONL, config.REPORTS_DIR = orig_runs, orig_reports
        shutil.rmtree(tmp, ignore_errors=True)


print("正式重跑入口 × 回执契约（2026-10-21 P1-D）：")
for name, fn in [
    ("入口接线：重跑/定时均走 daily_batch.py", case_entry_points_use_daily_batch),
    ("集成：整轮失败 → 重跑入口 → 未跑账号与全局失败仍在",
     case_retry_entry_keeps_unrun_accounts_and_stage_failures),
    ("旧编排器：单账号回执按账号并入、不抹掉整轮",
     case_legacy_writer_merges_instead_of_overwriting),
    ("消费端：旧格式回执标可信度不足", case_pipeline_status_marks_legacy_low_confidence),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
