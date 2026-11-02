# -*- coding: utf-8 -*-
"""test_daily_batch_repair.py — 日更控制流回归（2026-10-20 独立验收整改）

覆盖验收报告点名的失败路径（全部用真实 main/run_account/wait_run/terminate_run，
只 mock 传输层、子进程与时钟；写盘落在临时目录，不碰线上回执）：

  P1-1 · 单阶段失败：products rc=1 / ledger write rc=1 / 多阶段同时失败
  P1-2 · 超时必 stop；stop 确认后允许重试；stop 未确认 → 只起 1 个 run 且记待接管
  P1-3 · 子集补跑按账号并入整轮：未补跑账号不从 total/takeover 消失；rounds 留档
  P2-1 · 前置检查失败 / 未捕获异常也写终态回执（state=failed、level=ERROR）
  · 正常完成：exit=0、level=INFO、ledger.synced=true

运行：python pipeline/tests/test_daily_batch_repair.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time as _real_time
import traceback
import urllib.error
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE / "scripts"))
import daily_batch as db  # noqa: E402

DATE = "2026-10-20"

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
    """可推进的单调时钟：sleep 只推进虚拟时间，不真睡。"""

    def __init__(self):
        self._mono = 1000.0

    def monotonic(self):
        return self._mono

    def sleep(self, s):
        self._mono += max(0.0, float(s))

    def time(self):
        return _real_time.time()


class Env:
    """隔离环境：临时目录 + 假传输层 + 假子进程。"""

    def __init__(self, accounts=("A", "B"), health_ok=True):
        self.tmp = Path(tempfile.mkdtemp(prefix="db_rep_"))
        self.reports = self.tmp / "reports"
        self.out = self.tmp / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.acc_json = self.tmp / "accounts.json"
        accs = []
        for n in accounts:
            (self.out / f"scrape_{n}.json").write_text(
                json.dumps({"items": [{"id": f"{n}1"}], "errors": []}), encoding="utf-8")
            accs.append({"account": n, "active": True, "region": "us",
                         "out_dir": str(self.out)})
        self.acc_json.write_text(json.dumps({"accounts": accs}), encoding="utf-8")

        self.health_ok = health_ok
        self.posts: list[dict] = []
        self.stops: list[str] = []
        self.fail_accounts: set[str] = set()     # 这些账号的运行结果 = failed
        self.hang_first: bool = False            # 每账号第一次运行：一直 running，直到被 stop
        self.poll_exc: bool = False              # 所有 GET 轮询抛异常
        self.stop_ok: bool = True                # stop 端点可用
        self.gone_runs: set[str] = set()         # 这些 run_id 的查询返回 404（模拟重启后管理器丢失）
        self.rid_acct: dict[str, str] = {}
        self.attempts: dict[str, int] = {}
        self._stopped: set[str] = set()
        self.started = 0
        self.journal = self.tmp / "runs.jsonl"   # 隔离的 journal，不碰线上 webapp/data
        self._orig = {}

    # ── 假传输层 ───────────────────────────────────────────
    def http(self, path, body=None, timeout=60):
        if path == "/api/health":
            if not self.health_ok:
                raise OSError("[Errno 111] Connection refused")
            return {"ok": True}
        if path == "/api/run":
            b = dict(body or {})
            self.posts.append(b)
            acct = b.get("account") or ""
            self.started += 1
            rid = f"run_{acct}_{self.started}"
            self.rid_acct[rid] = acct
            self.attempts[acct] = self.attempts.get(acct, 0) + 1
            return {"id": rid}
        if path.startswith("/api/runs/") and path.endswith("/stop"):
            rid = path.split("/")[3]
            if not self.stop_ok:
                raise OSError("stop 端点不可用")
            self.stops.append(rid)
            self._stopped.add(rid)
            return {"ok": True}
        if path.startswith("/api/runs/"):
            rid = path.split("/")[3]
            if rid in self.gone_runs:
                raise urllib.error.HTTPError(f"http://127.0.0.1:8787{path}", 404,
                                             "Not Found", None, None)
            acct = self.rid_acct.get(rid, "")
            if self.poll_exc:
                raise OSError("read timeout")
            if acct in self.fail_accounts:
                return {"status": "failed", "exit_code": 3, "duration_s": 9.0,
                        "tail": ["scrape gate failed"]}
            if self.hang_first and self.attempts.get(acct, 0) == 1:
                if rid in self._stopped:
                    return {"status": "stopped", "exit_code": None, "duration_s": 1.0}
                return {"status": "running", "duration_s": 1.0}
            return {"status": "done", "exit_code": 0, "duration_s": 120.0, "tail": []}
        raise AssertionError(f"未预期的请求 {path}")

    # ── 上下文 ─────────────────────────────────────────────
    def __enter__(self):
        self._orig = {k: getattr(db, k) for k in
                      ("REPORTS_DIR", "ACCOUNTS_JSON", "_http", "post_update",
                       "extract_products", "time", "JOURNAL")}
        self.fake_time = FakeTime()
        db.REPORTS_DIR = self.reports
        db.ACCOUNTS_JSON = self.acc_json
        db.JOURNAL = self.journal
        db._http = self.http
        db.time = self.fake_time
        self.set_products({a["account"]: 0 for a in
                           json.loads(self.acc_json.read_text(encoding="utf-8"))["accounts"]})
        self.set_post_update()
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(db, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    # ── 便捷 ───────────────────────────────────────────────
    def set_products(self, mapping):
        db.extract_products = lambda accs, log: {n: {"rc": rc} for n, rc in mapping.items()}

    def set_post_update(self, write_rc=0, verify_rc=0):
        def _pu(sub, log, timeout=3600):
            return {"rc": write_rc if sub == "write" else verify_rc, "out": f"{sub} rc"}
        db.post_update = _pu

    def run(self, *args):
        argv = ["daily_batch.py", "--date", DATE, "--poll", "5", "--retry", "1",
                "--stop-grace", "30", *args]
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

    def round_files(self):
        return sorted((self.reports / DATE / "rounds").glob("*.json"))


# ────────────────────────── 用例 ──────────────────────────
def case_normal():
    with Env() as e:
        rc = e.run()
        s = e.summary()
        assert_(rc == 0, f"正常完成 exit 应为 0，实际 {rc}")
        assert_(s["level"] == "INFO", f"level 应为 INFO，实际 {s['level']}")
        assert_(s["failed_stages"] == [], f"不应有失败阶段：{s['failed_stages']}")
        assert_(s["total"] == 2 and s["counts"]["ok"] == 2, f"counts 异常 {s['counts']}")
        assert_(all(r["ledger"]["synced"] is True for r in s["accounts"].values()),
                "确认入账后 synced 应为 true")
        assert_(all(p.get("no_resume") is True for p in e.posts), "POST 必须带 no_resume=true")
        assert_(s["state"] == "done", f"终态回执 state 应为 done，实际 {s['state']}")
        assert_(len(e.round_files()) == 1, "应留 1 份 round 记录")


def case_products_fail():
    with Env() as e:
        e.set_products({"A": 1, "B": 0})
        rc = e.run()
        s = e.summary()
        assert_(rc == 1, f"商品失败 exit 应为 1，实际 {rc}")
        assert_(s["level"] == "ERROR", f"level 应为 ERROR，实际 {s['level']}")
        assert_("products" in s["failed_stages"],
                f"failed_stages 缺 products：{s['failed_stages']}")
        assert_([x["account"] for x in s["takeover"]] == ["A"],
                f"takeover 应只有 A：{s['takeover']}")
        a = s["accounts"]["A"]
        assert_(a["status"] == "failed", "A 应判 failed")
        assert_(a["stages"]["products"] == "failed", "阶段明细应标 products=failed")
        assert_(a["ledger"]["synced"] is not True, "A 未入账前不得 synced=true")
        assert_(s["takeover"][0]["stage"] == "products", "takeover 应标阶段 products")
        assert_(s["takeover"][0]["recovery"], "takeover 应带恢复动作")
        assert_(any("商品提取失败" in x for x in a["anomalies"]),
                f"应写明失败原因：{a['anomalies']}")
        assert_(s["accounts"]["B"]["status"] == "ok", "B 不受影响")


def case_ledger_write_fail():
    with Env() as e:
        e.set_post_update(write_rc=1, verify_rc=0)
        rc = e.run()
        s = e.summary()
        assert_(rc == 1, f"write 失败 exit 应为 1，实际 {rc}")
        assert_(s["level"] == "ERROR", f"level 应为 ERROR，实际 {s['level']}")
        assert_("ledger_write" in s["failed_stages"],
                f"failed_stages 缺 ledger_write：{s['failed_stages']}")
        assert_(all(r["ledger"]["synced"] is False for r in s["accounts"].values()),
                "台账未确认时 synced 必须为 false")
        assert_(all(r["status"] == "warn" for r in s["accounts"].values()),
                f"采集成功但没入账 → warn：{[r['status'] for r in s['accounts'].values()]}")
        assert_(s["alerts"] and s["alerts"][0]["stage"] == "ledger_write",
                f"alerts 应含 ledger_write：{s.get('alerts')}")
        assert_(s["alerts"][0]["recovery"], "alert 应带恢复动作")


def case_multi_stage_fail():
    with Env(accounts=("A", "B", "C")) as e:
        e.fail_accounts = {"C"}
        e.set_products({"A": 0, "B": 2})     # C 无商品阶段（采集就失败）
        e.set_post_update(write_rc=1, verify_rc=4)
        rc = e.run()
        s = e.summary()
        assert_(rc == 1, "多阶段失败 exit 应为 1")
        assert_(s["level"] == "ERROR", "level 应为 ERROR")
        assert_(set(s["failed_stages"]) == {"collect", "products", "ledger_write", "ledger_verify"},
                f"四阶段应全部判失败：{s['failed_stages']}")
        assert_(s["counts"]["failed"] == 2, f"C 与 B 应 failed：{s['counts']}")
        names = sorted(x["account"] for x in s["takeover"])
        assert_(names == ["B", "C"], f"takeover 应为 B/C：{names}")
        assert_(len(s["alerts"]) >= 3, f"alerts 应覆盖各失败阶段：{s['alerts']}")


def case_timeout_stop_then_retry():
    """轮询持续 running + 超时：先 stop，确认终态后才允许重试，且重试成功。"""
    with Env(accounts=("A",)) as e:
        e.hang_first = True
        rc = e.run("--retry", "2", "--timeout", "30")
        s = e.summary()
        assert_(len(e.posts) == 2, f"确认终止后应重试，共 2 次 POST，实际 {len(e.posts)}")
        assert_(len(e.stops) == 1, f"超时后必须 stop 一次，实际 {len(e.stops)}")
        a = s["accounts"]["A"]
        assert_(a["stop_confirmed"] is True, "stop 应确认终态")
        assert_(a["run_ids"] and len(a["run_ids"]) == 2, f"应记录两个 run_id：{a['run_ids']}")
        assert_(a["status"] == "ok", f"重试成功应 ok：{a['status']}")
        assert_(rc == 0, f"全部成功后 exit=0，实际 {rc}")


def case_timeout_stop_poll_exc_no_retry():
    """超时 + stop 后仍无法确认终态 → 只起 1 个 run，不自动重试，留待接管。"""
    with Env(accounts=("A",)) as e:
        e.poll_exc = True
        rc = e.run("--retry", "2", "--timeout", "20")
        s = e.summary()
        assert_(len(e.posts) == 1, f"未确认终止时不得重试：POST={len(e.posts)}")
        assert_(len(e.stops) == 1, f"超时必须尝试 stop：{e.stops}")
        a = s["accounts"]["A"]
        assert_(a["status"] == "failed", "应判 failed")
        assert_(a["aborted_retry"] is True, "应标记已中止自动重试")
        assert_(a["stop_confirmed"] is False, "应记录未确认终止")
        assert_(rc == 1, "exit 应为 1")
        assert_(s["takeover"] and s["takeover"][0]["account"] == "A", "应进入待接管")
        assert_(any("未确认终止" in x for x in s["takeover"][0]["anomalies"]),
                f"待接管原因应写明未确认终止：{s['takeover'][0]['anomalies']}")


def case_stop_endpoint_down():
    """stop 端点也不可用 → 同样不得重试。"""
    with Env(accounts=("A",)) as e:
        e.poll_exc = True
        e.stop_ok = False
        rc = e.run("--retry", "2", "--timeout", "20")
        s = e.summary()
        assert_(len(e.posts) == 1, f"stop 失败时不得重试：POST={len(e.posts)}")
        assert_(s["accounts"]["A"]["aborted_retry"] is True, "应中止重试")
        assert_(rc == 1, "exit 应为 1")


def case_partial_merge():
    """A/B 整轮都失败，再只补跑 B 成功 → 整轮 total 仍为 2，A 仍在待接管。"""
    with Env() as e:
        e.fail_accounts = {"A", "B"}
        rc1 = e.run("--retry", "1")
        s1 = e.summary()
        assert_(rc1 == 1 and s1["counts"]["failed"] == 2, f"首轮应 2 失败：{s1['counts']}")
        assert_(s1["total"] == 2, f"首轮 total 应为 2：{s1['total']}")

        e.fail_accounts = set()      # 只补跑 B，且成功
        rc2 = e.run("--retry", "1", "--accounts", "B")
        s2 = e.summary()
        assert_(rc2 == 0, f"补跑 B 成功 exit 应为 0，实际 {rc2}")
        assert_(s2["total"] == 2, f"整轮 total 应保持 2，实际 {s2['total']}")
        assert_(sorted(s2["accounts"].keys()) == ["A", "B"],
                f"账号应仍为 A/B：{list(s2['accounts'])}")
        assert_([x["account"] for x in s2["takeover"]] == ["A"],
                f"未补跑的 A 必须仍在待接管：{s2['takeover']}")
        assert_(s2["partial"] is True, "应标记这是并入的子集补跑")
        assert_(len(s2["rounds"]) == 2, f"rounds 应留 2 轮记录：{len(s2['rounds'])}")
        assert_(len(e.round_files()) == 2, f"rounds 文件应 2 份：{e.round_files()}")
        assert_(len(e.posts) == 3, f"POST 次数应为 3（A/B + B补跑），实际 {len(e.posts)}")
        # A 的失败明细不能被 B 的成功覆盖掉
        assert_(s2["accounts"]["A"]["status"] == "failed", "A 应仍为 failed")
        assert_(s2["accounts"]["B"]["status"] == "ok", "B 应更新为 ok")
        assert_(s2["level"] == "ERROR", f"仍有失败账号时 level 应为 ERROR：{s2['level']}")


def case_precheck_health_down():
    with Env(health_ok=False) as e:
        rc = e.run()
        s = e.summary()
        latest = e.latest()
        assert_(rc == 1, "health 不通 exit 应为 1")
        assert_(s is not None, "前置失败也必须写当日回执")
        assert_(s["state"] == "failed", f"state 应为 failed：{s['state']}")
        assert_(s["level"] == "ERROR", f"level 应为 ERROR：{s['level']}")
        assert_(s["failed_stages"] == ["precheck"],
                f"failed_stages 应为 precheck：{s['failed_stages']}")
        assert_(latest and latest["state"] == "failed", "latest 指针也应指向失败回执")
        assert_(s["alerts"] and "工作台不可达" in s["alerts"][0]["reason"],
                f"alert 应写明原因：{s.get('alerts')}")
        files = e.round_files()
        assert_(len(files) == 1, f"应留 1 份 round 记录：{files}")
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        assert_(rec.get("state") == "failed", "round 记录应为终态")


def case_no_accounts():
    with Env() as e:
        rc = e.run("--accounts", "NOT_EXIST")
        s = e.summary()
        assert_(rc == 1, "无账号应 exit 1")
        assert_(s and s["state"] == "failed" and s["failed_stages"] == ["precheck"],
                f"应写失败回执：{s}")


def case_uncaught_exception():
    with Env() as e:
        orig = db.load_accounts

        def boom(only=None):
            raise RuntimeError("accounts.json 解析炸了")

        db.load_accounts = boom
        try:
            rc = e.run()
        finally:
            db.load_accounts = orig
        s = e.summary()
        assert_(rc == 1, "未捕获异常 exit 应为 1")
        assert_(s and s["state"] == "failed", "未捕获异常也要写终态回执")
        assert_("internal" in s["failed_stages"], f"阶段应为 internal：{s['failed_stages']}")
        assert_("accounts.json" in s["alerts"][0]["reason"], f"原因应可读：{s['alerts']}")


def _spawn_sleep_child():
    return subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kill_child(child):
    try:
        child.terminate()
        child.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


# ── P1-R2 夹具：一个「父进程已退出、但 Job 里还有活进程」的命名 Job ──────────
def _named_job_with_live_child() -> dict:
    """建一个命名 Job，把一只长睡进程放进去；返回 {name, handle, child, child_alive}。

    这就是 runner 新版每轮创建的那种 Job（WB_RUN_<run_id>）：父进程退出后，
    孙进程仍可能留在里面 —— daily_batch 必须能跨进程查到它。
    """
    import ctypes
    from ctypes import wintypes

    if os.name != "nt":
        raise AssertionError("P1-R2 门禁只在 Windows 上有意义")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    name = f"WB_TEST_R2_{os.getpid()}_{int(_real_time.time() * 1000) % 100000}"
    job = k32.CreateJobObjectW(None, name)
    assert_(bool(job), "夹具：命名 Job 创建失败")
    child = _spawn_sleep_child()
    h = k32.OpenProcess(0x1F0FFF, False, child.pid)
    # 子进程可能比自己创建的更早被调度：给它一点时间
    ok = False
    for _ in range(50):
        if h and k32.AssignProcessToJobObject(job, h):
            ok = True
            break
        _real_time.sleep(0.02)
    if h:
        k32.CloseHandle(h)
    assert_(ok, "夹具：把子进程放进 Job 失败")
    alive = True
    for _ in range(50):
        if not db._pid_alive(child.pid):
            alive = False
            break
        _real_time.sleep(0.02)
    assert_(alive, "夹具：Job 里的进程没跑起来")
    procs = db._job_active_processes(name)
    assert_(procs and procs > 0, f"夹具：Job 活动进程数应 >0，实际 {procs}")
    return {"name": name, "handle": job, "child": child, "child_alive": True}


def _terminate_named_job(ctx: dict) -> None:
    import ctypes
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        if ctx.get("handle"):
            k32.TerminateJobObject(wintypes.HANDLE(ctx["handle"]), 1)
            k32.CloseHandle(wintypes.HANDLE(ctx["handle"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        _kill_child(ctx["child"])
    except Exception:  # noqa: BLE001
        pass


def case_pid_alive_helper():
    """_pid_alive：True=在跑 / False=确认已退出 / None=无法判断（None 一律按未确认处理）。"""
    assert_(db._pid_alive(os.getpid()) is True, "当前进程应判为存活")
    assert_(db._pid_alive(None) is None, "没有 pid 应判为无法判断")
    assert_(db._pid_alive("not-a-pid") is None, "非法 pid 应判为无法判断")
    assert_(db._pid_alive(0) is None, "pid<=0 应判为无法判断")
    child = _spawn_sleep_child()
    try:
        pid = child.pid
        assert_(db._pid_alive(pid) is True, "存活子进程应判为存活")
    finally:
        _kill_child(child)
    assert_(db._pid_alive(pid) is False, "已终止的子进程应判为确认退出")


def case_journal_interrupted_no_retry():
    """P1-2：重启清扫后的 interrupted 只是推断 —— 旧进程还活着时不得起第二个 run。"""
    with Env(accounts=("A",)) as e:
        child = _spawn_sleep_child()
        try:
            # 重启清扫后的真实形态：journal 里只剩 interrupted（没有任何进程终止证据）
            e.journal.write_text(json.dumps({
                "id": "run_A_1", "status": "interrupted", "account": "A", "pid": child.pid,
            }) + "\n", encoding="utf-8")
            e.gone_runs = {"run_A_1"}
            rc = e.run("--retry", "2", "--timeout", "20")
            s = e.summary()
            a = s["accounts"]["A"]
            assert_(child.poll() is None, "夹具旧子进程应仍存活（证明未核实到退出）")
            assert_(len(e.posts) == 1, f"旧进程未确认退出时不得重试：POST={len(e.posts)}")
            assert_(len(e.stops) == 0, f"管理器已无该 run，stop 无意义：{e.stops}")
            assert_(a["status"] == "failed", f"应判 failed：{a['status']}")
            assert_(a["aborted_retry"] is True, "应标记已中止自动重试")
            assert_(rc == 1, f"exit 应为 1，实际 {rc}")
            assert_([x["account"] for x in s["takeover"]] == ["A"], "应进入待接管")
            assert_(any("interrupted" in x or "未确认" in x for x in a["anomalies"]),
                    f"待接管原因应写明未确认：{a['anomalies']}")
        finally:
            _kill_child(child)


def case_journal_tree_alive_no_retry():
    """P1-R2 重试门禁：父 pid 已退出，但它那棵命名 Job 里还有活进程（孙进程）→ 不许重试。

    复现验收演练的后半段：journal 记的父 PID 确实退出了，只查 pid 会放行第二轮；而
    runner 现在把整棵树挂在命名 Job 里，daily_batch 必须连 Job 的活动进程数一起查。
    """
    ctx = _named_job_with_live_child()
    try:
        with Env(accounts=("A",)) as e:
            dead = _spawn_sleep_child()
            dead_pid = dead.pid
            _kill_child(dead)
            assert_(db._pid_alive(dead_pid) is False, "夹具：父 pid 必须确认已退出")
            e.journal.write_text(json.dumps({
                "id": "run_A_1", "status": "interrupted", "account": "A",
                "pid": dead_pid, "job_name": ctx["name"],
            }) + "\n", encoding="utf-8")
            e.gone_runs = {"run_A_1"}
            rc = e.run("--retry", "2", "--timeout", "20")
            s = e.summary()
            a = s["accounts"]["A"]
            assert_(ctx["child_alive"], "夹具：Job 里的孙进程应仍活着")
            assert_(len(e.posts) == 1,
                    f"旧树还有活进程时不得重试：POST={len(e.posts)}")
            assert_(a["aborted_retry"] is True, "应标记已中止自动重试")
            assert_(any("进程树" in x or "Job" in x for x in a["anomalies"]),
                    f"待接管原因必须写明旧进程树未退出：{a['anomalies']}")
            assert_(rc == 1, f"exit 应为 1，实际 {rc}")
    finally:
        _terminate_named_job(ctx)


def case_journal_tree_gone_retries():
    """P1-R2 正向控制：Job 里没有活进程（整棵树已回收）→ 允许重试。"""
    ctx = _named_job_with_live_child()
    _terminate_named_job(ctx)
    with Env(accounts=("A",)) as e:
        dead = _spawn_sleep_child()
        dead_pid = dead.pid
        _kill_child(dead)
        e.journal.write_text(json.dumps({
            "id": "run_A_1", "status": "interrupted", "account": "A",
            "pid": dead_pid, "job_name": ctx["name"],
        }) + "\n", encoding="utf-8")
        e.gone_runs = {"run_A_1"}
        rc = e.run("--retry", "2", "--timeout", "20")
        s = e.summary()
        assert_(db._job_active_processes(ctx["name"]) in (0, None),
                "夹具：Job 应已没有活动进程")
        assert_(len(e.posts) == 2, f"整棵树确认退出后应重试：POST={len(e.posts)}")
        assert_(s["accounts"]["A"]["status"] == "ok", "重试应成功")
        assert_(rc == 0, f"全部成功 exit=0，实际 {rc}")


def case_journal_interrupted_pid_gone_retries():
    """P1-2 正向控制：pid 核实确实已退出后才允许重试。"""
    with Env(accounts=("A",)) as e:
        child = _spawn_sleep_child()
        dead_pid = child.pid
        _kill_child(child)
        e.journal.write_text(json.dumps({
            "id": "run_A_1", "status": "interrupted", "account": "A", "pid": dead_pid,
        }) + "\n", encoding="utf-8")
        e.gone_runs = {"run_A_1"}
        rc = e.run("--retry", "2", "--timeout", "20")
        s = e.summary()
        assert_(len(e.posts) == 2, f"确认旧进程已退出后应重试：POST={len(e.posts)}")
        assert_(s["accounts"]["A"]["status"] == "ok", "重试应成功")
        assert_(rc == 0, f"全部成功 exit=0，实际 {rc}")


def case_stop_404_not_confirmed():
    """P1-2：stop 返回 404 只说明管理器丢了 run，不等于进程已退出 → 不重试。"""
    with Env(accounts=("A",)) as e:
        e.hang_first = True
        base_http = e.http

        def http(path, body=None, timeout=60):
            if path.startswith("/api/runs/") and path.endswith("/stop"):
                raise urllib.error.HTTPError(f"http://127.0.0.1:8787{path}", 404,
                                             "Not Found", None, None)
            return base_http(path, body, timeout)

        db._http = http
        rc = e.run("--retry", "2", "--timeout", "20")
        s = e.summary()
        a = s["accounts"]["A"]
        assert_(len(e.posts) == 1, f"stop 404 后不得重试：POST={len(e.posts)}")
        assert_(a["aborted_retry"] is True, "应中止自动重试")
        assert_(a["stop_confirmed"] is False, f"不得记为已确认终止：{a['stop_confirmed']}")
        assert_(rc == 1, "exit 应为 1")


def case_precheck_failure_keeps_day_view():
    """P2-2 + P2-3：前置失败写独立轮次但保住当日账号；恢复后不继承 precheck 告警。"""
    with Env() as e:
        e.fail_accounts = {"A"}
        e.run("--retry", "1")
        first = e.summary()
        assert_([x["account"] for x in first["takeover"]] == ["A"],
                f"首轮待接管应为 A：{first['takeover']}")

        e.health_ok = False
        rc = e.run("--retry", "1")
        s = e.summary()
        assert_(rc == 1, f"前置失败 exit 应为 1，实际 {rc}")
        assert_(sorted(s["accounts"]) == ["A", "B"],
                f"当日视图不得抹掉账号结果：{list(s['accounts'])}")
        assert_([x["account"] for x in s["takeover"]] == ["A"],
                f"待接管必须保留：{[x['account'] for x in s['takeover']]}")
        assert_("precheck" in s["failed_stages"], f"本轮前置失败要并入阶段：{s['failed_stages']}")
        assert_(any(x["stage"] == "precheck" for x in s["alerts"]), "本轮前置告警要并入 alerts")
        assert_(s["state"] == "failed", "state 应反映本轮失败")
        assert_(s["kept_previous"] is True, "应标记账号沿用当日已有回执")
        assert_(len(s["rounds"]) == 2, f"应有 2 轮记录：{len(s['rounds'])}")
        rid = s["last_round"]["round_id"]
        rec = json.loads((e.reports / DATE / "rounds" / f"{rid}.json").read_text(encoding="utf-8"))
        assert_(rec["accounts"] == {}, f"本轮独立留档不应写入别的账号：{list(rec['accounts'])}")
        assert_(rec["failed_stages"] == ["precheck"], f"本轮失败阶段应为 precheck：{rec['failed_stages']}")
        assert_(rec["exit_code"] == 1 and rec["exit_scope"] == "round", "独立轮次应标注 exit 归属")

        e.health_ok = True
        e.fail_accounts = set()
        e.posts.clear()
        rc2 = e.run("--retry", "1", "--accounts", "B")
        s2 = e.summary()
        assert_(rc2 == 0, f"恢复后本轮成功 exit 应为 0，实际 {rc2}")
        assert_("precheck" not in s2["failed_stages"],
                f"恢复后不得继承 precheck 失败：{s2['failed_stages']}")
        assert_(all(x["stage"] != "precheck" for x in s2["alerts"]),
                f"不得继承 precheck 告警：{s2['alerts']}")
        assert_(s2["accounts"]["B"]["status"] == "ok", "B 应更新为 ok")
        assert_(s2["accounts"]["A"]["status"] == "failed", "A 应保留 failed")
        assert_(s2["level"] == "ERROR", f"A 仍失败 → 合并视图仍 ERROR：{s2['level']}")
        assert_(s2["last_round"]["exit_code"] == 0, "last_round 应显示本轮 exit=0")
        assert_(s2["last_round"]["level"] == "INFO", "last_round 应显示本轮 level=INFO")


print("日更控制流回归（2026-10-20 整改）：")
for name, fn in [
    ("正常路径 exit=0/INFO/synced=true", case_normal),
    ("P1-1 商品阶段 rc=1 → ERROR/待接管", case_products_fail),
    ("P1-1 台账 write rc=1 → ERROR/synced=false", case_ledger_write_fail),
    ("P1-1 多阶段同时失败 → 四阶段全记", case_multi_stage_fail),
    ("P1-2 超时 stop 确认后再重试", case_timeout_stop_then_retry),
    ("P1-2 stop 未确认 → 不重试、留待接管", case_timeout_stop_poll_exc_no_retry),
    ("P1-2 stop 端点不可用 → 不重试", case_stop_endpoint_down),
    ("P1-3 子集补跑并入整轮（A 不消失）", case_partial_merge),
    ("P2-1 前置检查失败也写终态回执", case_precheck_health_down),
    ("P2-1 无可跑账号也写终态回执", case_no_accounts),
    ("P2-1 未捕获异常也写终态回执", case_uncaught_exception),
    ("P1-2 pid 存活判定（True/False/None）", case_pid_alive_helper),
    ("P1-2 journal interrupted + 旧进程存活 → 不重试", case_journal_interrupted_no_retry),
    ("P1-2 journal interrupted + pid 已退出 → 允许重试", case_journal_interrupted_pid_gone_retries),
    ("P1-R2 旧树仍有活进程（Job）→ 不重试", case_journal_tree_alive_no_retry),
    ("P1-R2 整棵树已回收（Job 空）→ 允许重试", case_journal_tree_gone_retries),
    ("P1-2 stop 404 不算已确认终止 → 不重试", case_stop_404_not_confirmed),
    ("P2-2/P2-3 前置失败保留当日账号、恢复后清告警", case_precheck_failure_keeps_day_view),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
