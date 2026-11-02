# -*- coding: utf-8 -*-
"""test_receipts_flush.py — 逐账号增量回执（2026-10-24 D6-C）

要解决的真实事故：`daily_batch` 旧实现只在整轮收尾调一次 persist()。
进程被中途杀掉时，这一轮**已经跑成功的账号全部不落账** —— 当日视图继续保留
上一轮的 failed，`repair/scan` 据此报假红，人去重跑本来已经成功的账号。
2026-10-24 实测丢了 4 个账号（跑得很顺，只是没记账）。

覆盖：
  · 每跑完一个账号就落一次盘（flush 次数 == 账号数）
  · 中间态 state=running、ended_at=None、round_kind 保持真实值
  · ★ 被杀时：已完成账号的 ok 结论必须在盘上（这是本模块存在的理由）
  · ★ full 轮中途被杀：当日其它账号的历史结论不能丢（中间态强制按账号合并）
  · 同一 round_id 多次 flush 不产生重复 rounds[] 条目
  · 正常收尾：final persist 仍按 full 替换 accounts（中间态不影响终态）

运行：pipeline/.venv/Scripts/python.exe pipeline/tests/test_receipts_flush.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE / "scripts"))
import daily_batch as db  # noqa: E402
import receipts as R  # noqa: E402

DATE = "2026-10-24"

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


def _row(ok: bool) -> dict:
    return {"status": "ok" if ok else "failed",
            "attempts": 1, "duration_s": 5.0,
            "run_ids": [], "gates": [], "coverage": {},
            "anomalies": [] if ok else ["采集失败"],
            "stages": {"collect": "ok" if ok else "failed"},
            "ledger": {"synced": False, "payload": ""}}


class Env:
    """极小桩：只替 run_account / 传输层 / 商品 / 台账，其余走真实 main()。"""

    def __init__(self, accounts=("A", "B"), fail=(), prev=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="db_flush_"))
        self.reports = self.tmp / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.out = self.tmp / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.acc_json = self.tmp / "accounts.json"
        self.fail = set(fail)
        accs = []
        for n in accounts:
            (self.out / f"scrape_{n}.json").write_text(
                json.dumps({"items": [{"id": f"{n}1"}], "errors": []}), encoding="utf-8")
            accs.append({"account": n, "active": True, "region": "us",
                         "out_dir": str(self.out)})
        self.acc_json.write_text(json.dumps({"accounts": accs}), encoding="utf-8")
        if prev is not None:
            (self.reports / DATE).mkdir(parents=True, exist_ok=True)
            (self.reports / DATE / "run_summary.json").write_text(
                json.dumps(prev, ensure_ascii=False), encoding="utf-8")
        self.flushes: list[dict] = []
        self._orig = {}
        self._kill_after = None      # 第 N 次 flush 之后模拟硬杀（SystemExit）

    def _fake_run(self, acc, *a, **k):
        return _row(acc["account"] not in self.fail)

    def _flush(self, summary, day_dir, reports_dir, date, ran, log=None):
        # ★ 必须调被替换前的原函数，否则 self._flush 会递归调用自己（踩过）
        out = self._orig_flush(summary, day_dir, reports_dir, date, ran, log=log)
        self.flushes.append({"summary": json.loads(json.dumps(summary)), "ran": list(ran)})
        if self._kill_after is not None and len(self.flushes) >= self._kill_after:
            raise SystemExit(9)      # 模拟 Ctrl-C / 断电：不执行收尾 persist
        return out

    def __enter__(self):
        self._orig = {k: getattr(db, k) for k in
                      ("REPORTS_DIR", "ACCOUNTS_JSON", "JOURNAL", "_http",
                       "run_account", "extract_products", "post_update")}
        self._orig_flush = db.R.flush
        db.REPORTS_DIR = self.reports
        db.ACCOUNTS_JSON = self.acc_json
        db.JOURNAL = self.tmp / "runs.jsonl"
        db._http = lambda path, body=None, timeout=60: (
            {"ok": True} if path == "/api/health" else {"id": "x1"})
        db.run_account = self._fake_run
        db.extract_products = lambda accs, log: {a["account"]: {"rc": 0} for a in accs}
        db.post_update = lambda sub, log, timeout=3600: {"rc": 0, "out": f"{sub} rc"}
        db.R.flush = self._flush
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(db, k, v)
        db.R.flush = self._orig_flush
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def run(self, *args):
        argv = ["daily_batch.py", "--date", DATE, "--retry", "1", *args]
        old = sys.argv
        sys.argv = argv
        try:
            return db.main()
        finally:
            sys.argv = old

    def summary(self):
        p = self.reports / DATE / "run_summary.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def rounds(self):
        return json.loads(((self.reports / DATE / "rounds") /
                           next((self.reports / DATE / "rounds").glob("*.json")).name)
                          .read_text(encoding="utf-8")) \
            if (self.reports / DATE / "rounds").exists() else None


def _prev_view(failed=("A",), extra=("Z",)) -> dict:
    """构造上一轮的当日视图：A 采集失败（ERROR）、Z 是别的账号的成功记录。"""
    accounts = {n: {"status": "failed" if n in failed else "ok",
                    "stages": {"collect": "failed" if n in failed else "ok"}}
                for n in tuple(failed) + tuple(extra)}
    return {"date": DATE, "state": "done", "round_id": "prev_round",
            "round_kind": "full", "source": "daily_batch", "pid": 0,
            "started_at": 1.0, "ended_at": 2.0, "total": len(accounts),
            "accounts": accounts, "stages": {"collect": {"ok": False}},
            "counts": {}, "takeover": [], "alerts": [], "failed_stages": [],
            "rounds": [], "merge_policy": "partial→按账号并入当日整轮；full→替换",
            "contract": {"version": 2, "legacy_stages": False}}


# ────────────────────────── 用例 ──────────────────────────
def case_flush_every_account():
    """每跑完一个账号落一次盘，且中间态是 running / ended_at=None。"""
    with Env(accounts=("A", "B", "C")) as e:
        rc = e.run()
        assert_(rc == 0, f"exit 应为 0，实际 {rc}")
        assert_(len(e.flushes) == 3, f"3 个账号应 flush 3 次，实际 {len(e.flushes)}")
        for i, f in enumerate(e.flushes, 1):
            s = f["summary"]
            assert_(s["state"] == "running", f"第 {i} 次 flush state 应为 running：{s['state']}")
            assert_(s["ended_at"] is None,
                    f"第 {i} 次 flush 不该写 ended_at（未结束）：{s['ended_at']}")
            assert_(len(s["accounts"]) == i,
                    f"第 {i} 次 flush 应带 {i} 个账号，实际 {len(s['accounts'])}")
            assert_(s.get("pid"), "中间态应带 pid（消费端据此判断进程是否还活着）")
        # 终态仍由 final persist 写：state=done、total=3
        s = e.summary()
        assert_(s["state"] == "done", f"终态 state 应为 done，实际 {s['state']}")
        assert_(s["total"] == 3, f"终态 total 应为 3，实际 {s['total']}")
        assert_(s["level"] == "INFO", f"终态 level 应为 INFO，实际 {s['level']}")


def case_kill_keeps_done_accounts():
    """★ 核心：第 1 个账号跑完就被杀 → 它的 ok 结论必须留在盘上（不再被判 failed）。"""
    with Env(accounts=("A", "B", "C"), prev=_prev_view()) as e:
        e._kill_after = 1
        try:
            e.run()
        except SystemExit:
            pass
        else:
            raise AssertionError("预期 main() 以 SystemExit 中断")
        s = e.summary()
        assert_(s["accounts"]["A"]["status"] == "ok",
                f"被杀前已跑完的 A 必须是 ok，实际 {s['accounts']['A']['status']}")
        assert_(s["state"] == "running", f"中断态 state 应为 running，实际 {s['state']}")
        assert_(s["counts"]["failed"] == 0,
                f"A 已成功，当日不应再有 failed 账号：{s['counts']}")
        assert_(s["level"] != "ERROR", f"A 成功后 level 不应还是 ERROR：{s['level']}")


def case_kill_keeps_other_accounts():
    """★ full 轮中途被杀：中间态强制按账号合并 → 其它账号的历史结论不能丢。"""
    with Env(accounts=("A", "B"), prev=_prev_view()) as e:
        e._kill_after = 1
        try:
            e.run()
        except SystemExit:
            pass
        s = e.summary()
        assert_("Z" in s["accounts"], f"full 轮中途被杀不能抹掉别的账号 Z：{list(s['accounts'])}")
        assert_(s["accounts"]["Z"]["status"] == "ok", "Z 的结论应原样保留")
        assert_(s["accounts"]["A"]["status"] == "ok", "A 的新结论应已并入")


def case_rounds_not_duplicated():
    """同一 round_id 多次 flush 只占 1 条 rounds[]（按 round_id 替换）。"""
    with Env(accounts=("A", "B", "C")) as e:
        e.run()
        s = e.summary()
        assert_(len(s["rounds"]) == 1, f"rounds 应只有 1 条，实际 {len(s['rounds'])}")
        assert_(s["last_round"]["round_id"] == s["round_id"], "last_round 应是本轮")
        assert_(s["last_round"]["state"] == "done",
                f"收尾后 last_round.state 应为 done，实际 {s['last_round'].get('state')}")


def case_full_final_replaces():
    """正常收尾：full 轮仍按 full 替换 accounts（上一轮的 Z 会消失）。"""
    with Env(accounts=("A", "B"), prev=_prev_view()) as e:
        e.run()
        s = e.summary()
        assert_(s["total"] == 2, f"full 收尾后 total 应为 2（只含本轮账号），实际 {s['total']}")
        assert_("Z" not in s["accounts"], f"full 收尾应替换 accounts，Z 不该在：{list(s['accounts'])}")


def case_partial_kind_preserved():
    """中间态 round_kind 保持真实值（partial 就写 partial，不因为强制合并而改）。"""
    with Env(accounts=("A", "B")) as e:
        e.run("--accounts", "A")
        assert_(e.flushes and e.flushes[0]["summary"]["round_kind"] == "partial",
                f"round_kind 应保持 partial：{e.flushes[0]['summary'].get('round_kind') if e.flushes else None}")


def case_failed_account_marks_error_midround():
    """中间态也要如实反映：已跑完的账号失败 → level 立刻 ERROR（不粉饰）。"""
    with Env(accounts=("A", "B"), fail=("A",)) as e:
        e._kill_after = 1
        try:
            e.run()
        except SystemExit:
            pass
        s = e.summary()
        assert_(s["accounts"]["A"]["status"] == "failed", "A 应判 failed")
        assert_(s["level"] == "ERROR", f"有 failed 账号时 level 应为 ERROR，实际 {s['level']}")


print("逐账号增量回执（2026-10-24 D6-C）：")
for name, fn in [
    ("每个账号落一次盘（running/ended_at=None/pid）", case_flush_every_account),
    ("★ 中途被杀：已成功账号不丢", case_kill_keeps_done_accounts),
    ("★ full 轮被杀：其它账号结论不丢", case_kill_keeps_other_accounts),
    ("同 round_id 不重复进 rounds[]", case_rounds_not_duplicated),
    ("正常收尾 full 仍替换 accounts", case_full_final_replaces),
    ("中间态 round_kind 保持真实值", case_partial_kind_preserved),
    ("中间态如实反映失败（level=ERROR）", case_failed_account_marks_error_midround),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
