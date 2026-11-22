"""daily_schedule 回归：调度窗口 / 当日幂等 / 台账占用 / 配置校验 / journal 恢复。

运行：py -3 webapp/tests/daily_schedule.test.py（工作区根目录）。
"""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config                        # noqa: E402
import runner                        # noqa: E402
import scheduler                     # noqa: E402
import store                         # noqa: E402

TODAY_STR, TOMORROW_STR = "2026-10-24", "2026-10-25"
ACCOUNTS = {"accounts": [{"account": "alice", "active": True},   # 生产 accounts.json 桩
                         {"account": "bob", "active": True}]}


class DailyScheduleTest(unittest.TestCase):
    """trigger 全程打桩；SCHEDULE_JSON / RUNS_JSONL 指向 TemporaryDirectory。"""

    def setUp(self):
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(config, "SCHEDULE_JSON", tmp / "schedule.json"))
        self.enterContext(mock.patch.object(config, "RUNS_JSONL", tmp / "runs.jsonl"))
        self.enterContext(mock.patch.object(config, "load_accounts", return_value=ACCOUNTS))
        self.trigger = self.enterContext(mock.patch.object(
            scheduler, "trigger", side_effect=[{"id": "run-1"}, {"id": "run-2"}]))

    def _save(self, time="09:30", accounts=None, enabled=True):
        scheduler.save({"enabled": enabled, "time": time,
                        "accounts": [] if accounts is None else list(accounts)})

    def _now(self, day=24, hh=9, mm=31):
        return dt.datetime(2026, 10, day, hh, mm)

    def _trigger_accounts(self):
        call = self.trigger.call_args
        return call.args[0] if call.args else call.kwargs.get("accounts")


    def test_dispatch_before_due_same_day_and_next_day(self):
        self._save(time="09:30")
        scheduler._tick(now=self._now(hh=9, mm=29))      # 未到点不派发
        self.assertEqual(self.trigger.call_count, 0)
        scheduler._tick(now=self._now())                 # 到点即派发；[] 代表全部账号
        self.assertEqual(self.trigger.call_count, 1)
        self.assertEqual(self._trigger_accounts(), [])
        scheduler._tick(now=self._now(hh=18))            # 同日不重跑
        self.assertEqual(self.trigger.call_count, 1)
        scheduler._tick(now=self._now(day=25))           # 次日恢复派发
        self.assertEqual(self.trigger.call_count, 2)
        cfg = scheduler.load()
        self.assertEqual((cfg["last_run"], cfg["last_run_id"]), (TOMORROW_STR, "run-2"))

    def test_saved_changes_preserve_last_run(self):
        self._save(time="09:30")
        scheduler._tick(now=self._now())
        scheduler.save({"enabled": True, "time": "22:00", "accounts": ["alice"]})
        cfg = scheduler.load()
        self.assertEqual((cfg["time"], cfg["accounts"]), ("22:00", ["alice"]))
        self.assertEqual((cfg["last_run"], cfg["last_run_id"]), (TODAY_STR, "run-1"))
        scheduler._tick(now=self._now(hh=22))            # 改时间不重置当日已跑
        self.assertEqual(self.trigger.call_count, 1)

    def test_busy_then_retry_next_tick(self):
        self._save(time="09:30")
        self.trigger.side_effect = [runner.ResourceBusy("busy"),
                                    {"id": "run-9", "status": "running"}]
        scheduler._tick(now=self._now())                 # 被占用：不崩、不记当日已跑
        cfg = scheduler.load()
        self.assertIsNone(cfg["last_run_id"])
        self.assertNotEqual(cfg["last_run"], TODAY_STR)
        scheduler._tick(now=self._now(hh=10))            # 下一拍重试成功
        self.assertEqual(self.trigger.call_count, 2)
        self.assertEqual(scheduler.load()["last_run_id"], "run-9")

    def test_eligible_subset_never_widens_to_all(self):
        self._save(time="09:30", accounts=["alice"])
        scheduler._tick(now=self._now())
        self.assertEqual(self._trigger_accounts(), ["alice"])   # 子集原样下发
        marked = {"accounts": [{"account": "alice", "active": True, "ledger_skip": True},
                               {"account": "bob", "active": True}]}
        with mock.patch.object(config, "load_accounts", return_value=marked):
            scheduler._tick(now=self._now(day=25))       # 子集无人可选：不派发
        self.assertEqual(self.trigger.call_count, 1)     # 也绝不放宽成全部账号

    def test_recovers_dispatch_from_journal_after_crash(self):
        store.append({"id": "run-crash", "kind": "daily",
                      "title": f"每日增量更新（定时 {TODAY_STR}）", "status": "running"})
        self._save(time="09:30")
        scheduler._tick(now=self._now())                 # 崩溃在 journal 落盘后、写设置前
        self.assertEqual(self.trigger.call_count, 0)     # 复用 journal 任务，不重复起跑
        cfg = scheduler.load()
        self.assertEqual((cfg["last_run"], cfg["last_run_id"]), (TODAY_STR, "run-crash"))


    def test_validation_rejects_bad_payloads(self):
        for time in ("24:00", "09:60", "9:30", "0930", ""):
            with self.subTest(time=time):
                with self.assertRaises(ValueError):
                    scheduler.save({"enabled": True, "time": time, "accounts": []})
        with self.assertRaises(ValueError):              # 未知账号
            scheduler.save({"enabled": True, "time": "09:30", "accounts": ["ghost"]})
        with self.assertRaises(ValueError):              # 伪造字段不许经 save 注入
            scheduler.save({"enabled": True, "time": "09:30", "accounts": [],
                            "last_run": "2026-01-01"})
        rows = {"accounts": [{"account": "alice", "active": True},
                             {"account": "bob", "active": False}]}
        with mock.patch.object(config, "load_accounts", return_value=rows):
            with self.assertRaises(ValueError):          # 停用账号不可选
                scheduler.save({"enabled": True, "time": "09:30", "accounts": ["bob"]})
            saved = scheduler.save({"enabled": True, "time": "09:30",
                                    "accounts": ["alice"]})
            self.assertEqual(saved["accounts"], ["alice"])

    def test_last_result_reflects_journal_truth(self):
        self._save(time="09:30")
        scheduler._tick(now=self._now())                 # trigger 被打桩：journal 为空
        self.assertIsNone(scheduler.status()["last_result"])
        store.append({"id": "run-1", "kind": "daily", "status": "running"})
        self.assertEqual(scheduler.status()["last_result"]["status"], "running")
        store.append({"id": "run-1", "kind": "daily", "status": "done", "exit_code": 0})
        self.assertEqual(scheduler.status()["last_result"]["status"], "done")

    def test_last_result_uses_scheduled_round_after_manual_update(self):
        self._save()
        rid = "abc123def456"
        self.trigger.side_effect = [{"id": rid}]
        scheduler._tick(now=self._now())
        store.append({"id": rid, "kind": "daily", "status": "done", "exit_code": 0})
        root = config.SCHEDULE_JSON.parent / "reports"
        rounds = root / TODAY_STR / "rounds"
        rounds.mkdir(parents=True)
        (rounds / f"first-{rid}.json").write_text(json.dumps({"level": "WARN", "counts": {"warn": 1}}))
        (rounds / "later-manual.json").write_text(json.dumps({"level": "INFO", "counts": {"ok": 1}}))
        with mock.patch.dict("os.environ", {"DAILY_BATCH_REPORTS_DIR": str(root)}):
            result = scheduler.status()["last_result"]
        self.assertEqual((result["level"], result["counts"]), ("WARN", {"warn": 1}))


if __name__ == "__main__":
    unittest.main()
