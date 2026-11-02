# -*- coding: utf-8 -*-
"""test_orchestrator.py — daily_update 编排层（重试 / 断点续跑 / 超时 / 回执）

运行：pipeline/.venv/Scripts/python.exe tests/test_orchestrator.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))
import daily_update as du  # noqa: E402

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


def ok_result(new=2):
    return {"status": "ok", "window": ["a", "b"], "new": new, "known": 5,
            "skipped_known": 3, "scrape_failed": 0, "snapshot_records": 5,
            "coverage": "5/5", "gates": True, "anomalies": [],
            "ledger": {"rows_to_add": new, "rows_to_update": 3, "payload": ""},
            "sample_check_url": None}


def sandbox():
    tmp = Path(tempfile.mkdtemp(prefix="orch_"))
    rd = tmp / "2026-10-19"
    rd.mkdir(parents=True)
    return tmp, rd, {"reports_root": str(tmp), "accounts": []}


def main() -> int:
    orig = du.run_account

    def case_retry():
        tmp, rd, cfg = sandbox()
        accs = [{"account": "a1"}, {"account": "a2"}, {"account": "a3"}]
        calls = {"a1": 0, "a2": 0, "a3": 0}

        def fake(acc, cfg_, today, report_dir, timeout=None):
            calls[acc["account"]] += 1
            if acc["account"] == "a1" and calls["a1"] == 1:
                return du._empty_result("首轮网络失败")
            if acc["account"] == "a3":
                return du._empty_result("持续失败")
            return ok_result()
        du.run_account = fake
        res, att = du.run_all(accs, cfg, "2026-10-19", rd, retry=3, timeout=30)
        assert_(calls["a1"] == 2 and calls["a2"] == 1 and calls["a3"] == 3,
                f"重试轮次不对: {calls}")
        assert_(res["a1"]["status"] == "ok", "a1 第二轮应成功")
        assert_(res["a3"]["status"] == "failed" and att["a3"] == 3, "a3 应 3 轮后仍失败")
        assert_(all("duration_s" in r for r in res.values()), "缺 duration_s")

    def case_summary():
        tmp, rd, cfg = sandbox()
        res = {"a": ok_result(), "b": dict(ok_result(), status="warn",
                                          anomalies=["转写未全过"]),
               "c": du._empty_result("抓取失败")}
        att = {"a": 1, "b": 1, "c": 2}
        s = du.write_run_summary(cfg, res, att, "2026-10-19", rd, time.time() - 5, "ERROR")
        assert_(s["counts"] == {"ok": 1, "warn": 1, "failed": 1}, f"counts={s['counts']}")
        assert_([x["account"] for x in s["takeover"]] == ["c"], "takeover 应只含 c")
        assert_(s["takeover"][0]["attempts"] == 2, "takeover 应带重试次数")
        assert_((rd / "run_summary.json").exists(), "缺 run_summary.json")
        assert_((tmp / "latest_run_summary.json").exists(), "缺稳定指针文件")
        saved = json.loads((rd / "run_summary.json").read_text(encoding="utf-8"))
        assert_(saved["level"] == "ERROR" and saved["total"] == 3, "回执内容不完整")

    def case_resume():
        tmp, rd, cfg = sandbox()
        accs = [{"account": "a1"}, {"account": "a2"}]
        calls = []

        def fake(acc, cfg_, today, report_dir, timeout=None):
            calls.append(acc["account"])
            return ok_result()
        du.run_account = fake
        du.run_all(accs, cfg, "2026-10-19", rd, retry=1, timeout=30)
        du.write_run_summary(cfg, {"a1": ok_result(), "a2": ok_result()},
                             {"a1": 1, "a2": 1}, "2026-10-19", rd, time.time(), "INFO")
        calls.clear()
        res, att = du.run_all(accs, cfg, "2026-10-19", rd, retry=1, timeout=30, resume=True)
        assert_(calls == [], f"resume 不应重跑，实跑 {calls}")
        assert_(att == {"a1": 0, "a2": 0}, f"resume 账号 attempts 应为 0，实得 {att}")
        assert_(res["a1"]["status"] == "ok", "resume 应复用旧结果")

    def case_timeout():
        py = sys.executable
        t0 = time.time()
        rc, out = du.run_cmd([py, "-c", "import time;time.sleep(20)"], timeout=1)
        assert_(rc == -9, f"超时返回码应为 -9，实得 {rc}")
        assert_("TIMEOUT" in out, f"输出应含 TIMEOUT: {out[:80]}")
        assert_(time.time() - t0 < 15, "超时未按期终止")

    def case_run_cmd_json():
        tmp = Path(tempfile.mkdtemp(prefix="cmd_"))
        script = tmp / "j.py"
        script.write_text('import json;print("noise");print(json.dumps({"ok":1}))',
                          encoding="utf-8")
        rc, out = du.run_cmd([sys.executable, str(script)], timeout=30)
        assert_(rc == 0 and out == '{"ok": 1}', f"应取末行 JSON，实得 {out!r}")

    print("daily_update 编排层：")
    for name, fn in [("失败重试（降噪：只重跑失败者）", case_retry),
                     ("运行回执（counts/takeover/稳定指针）", case_summary),
                     ("断点续跑（resume 复用成功账号）", case_resume),
                     ("单账号超时强杀", case_timeout),
                     ("无超时路径仍解析末行 JSON", case_run_cmd_json)]:
        check(name, fn)
    du.run_account = orig
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
