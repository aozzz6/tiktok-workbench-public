# -*- coding: utf-8 -*-
"""lease_owner.test.py — 修复会话的台账租约下传（2026-10-24 B 方案）

背景（实测故障）：
  一键修复先 `RESOURCES.acquire((LEDGER,), kind="repair")` 持有台账写者租约，
  它的 collect 项 cmd 是 `daily_batch.py`，该脚本在**子进程里再 POST /api/run**，
  不带令牌时那次申请被同一个租约挡回 → 服务端未捕获 ResourceBusy → **HTTP 500**，
  daily_batch 2.9s 退出，回执记 `pipeline failed exit=None`。collect 永远起不来。

覆盖：
  · runner._child_env  持有租约的 run 都下传 WB_LEASE_OWNER：内部子任务带会话租约；
                       顶层 run 带**自己的**租约（2026-10-31 自锁修复：待接管重跑的
                       daily_batch 子 POST 曾被父任务自己的租约挡回 400）；无租约 run 清掉继承脏值
  · app._lease_owner   空 → ""；有效令牌 → 原样；无效/过期 → 400
  · POST /api/run      带 lease_owner 时把它传给 MANAGER.start（不吞）
  · ResourceBusy       统一转 400 且带 holders（不再冒成 500）
  · daily_batch        env 有令牌 → body.lease_owner 带回；无令牌 → 不带
  · tk_reverse._assert_playable  仅 audio 的格式表 → 判图文帖；有视频/空表 → 放行
运行：webapp/.venv/Scripts/python.exe tests/lease_owner.test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import app  # noqa: E402
import runner  # noqa: E402
import tk_reverse  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PIPE_SCRIPTS = WEBAPP.parent / "pipeline" / "scripts"
sys.path.insert(0, str(PIPE_SCRIPTS))
import daily_batch  # noqa: E402

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


def mk_run(lease: str, owns: bool) -> runner.Run:
    return runner.Run(id="t1", kind="repair", title="t", cmd=["x"], cwd=str(WEBAPP),
                      lease=lease, owns_lease=owns)


CHILD = mk_run("tok-1", False)          # 修复派生的内部子任务
TOP = mk_run("tok-2", True)             # 自己申请的顶层 run
BARE = mk_run("", False)                # 不涉及台账的 kind（script）


# ── 1. runner._child_env ───────────────────────────────────────
def _down():
    assert_(runner.MANAGER._child_env(CHILD).get("WB_LEASE_OWNER") == "tok-1",
            str(runner.MANAGER._child_env(CHILD).get("WB_LEASE_OWNER")))


def _top_own():
    env = runner.MANAGER._child_env(TOP)
    assert_(env.get("WB_LEASE_OWNER") == "tok-2",
            f'应为自己的租约 tok-2，实际 {env.get("WB_LEASE_OWNER")!r}')


def _bare_no():
    assert_("WB_LEASE_OWNER" not in runner.MANAGER._child_env(BARE))


def _dirty_env():
    with patch.dict(os.environ, {"WB_LEASE_OWNER": "forged"}, clear=False):
        assert_(runner.MANAGER._child_env(TOP).get("WB_LEASE_OWNER") == "tok-2",
                "顶层 run 的自有租约必须覆盖继承来的脏值")
        assert_("WB_LEASE_OWNER" not in runner.MANAGER._child_env(BARE),
                "无租约 run 也必须清掉")


def _env_kept():
    env = runner.MANAGER._child_env(CHILD)
    # ADR-0008（2026-11-05，T-27 / CRD-0003）：run_env() 不再写 HF_HUB_OFFLINE，
    # 离线与否由子进程的 model_cache 决定；父环境残留的键也不许透传。
    assert_("HF_HUB_OFFLINE" not in env, "run_env() 不该再带 HF_HUB_OFFLINE（ADR-0008）")
    assert_(env.get("HTTPS_PROXY"), "run_env 的代理丢了")


check("内部子任务：把租约下传成环境变量", _down)
check("环境变量名两侧一致（webapp/config 与 pipeline/daily_batch 各写一份常量）",
      lambda: assert_(__import__("config").LEASE_ENV == daily_batch.LEASE_ENV,
                      f'{__import__("config").LEASE_ENV} != {daily_batch.LEASE_ENV}'))
check("顶层 run：下传自己的租约（daily_batch 子 POST 并入自己的租约，2026-10-31 自锁修复）", _top_own)
check("无租约的 run：环境里不留该变量", _bare_no)
check("外部环境里的脏令牌会被清掉（不能靠 env 伪造 owner）", _dirty_env)
check("下传只加一个键，run_env 的代理仍在、不再强制离线", _env_kept)


# ── 2. app._lease_owner ───────────────────────────────────────
def _empty():
    assert_(app._lease_owner({}) == "")
    assert_(app._lease_owner({"lease_owner": "   "}) == "")
    assert_(app._lease_owner({"lease_owner": None}) == "")


def _valid_owner():
    lease = runner.RESOURCES.acquire((runner.LEDGER,), kind="repair", title="单测")
    try:
        assert_(app._lease_owner({"lease_owner": lease}) == lease, "有效令牌应原样返回")
    finally:
        runner.RESOURCES.release(lease)


def _bad_owner():
    try:
        app._lease_owner({"lease_owner": "not-a-lease"})
    except HTTPException as e:
        assert_(e.status_code == 400, f"应 400，实际 {e.status_code}")
        assert_("lease_owner" in str(e.detail), str(e.detail))
        return
    raise AssertionError("伪造令牌必须被拒")


check("空/缺失 → 返回空串（普通请求不受影响）", _empty)
check("有效令牌（当前确实持锁）→ 原样返回", _valid_owner)
check("无效/过期令牌 → 400，且说明原因", _bad_owner)


# ── 3. POST /api/run 透传 owner + ResourceBusy → 400 ──────────
def _passthrough():
    lease = runner.RESOURCES.acquire((runner.LEDGER,), kind="repair", title="单测")
    seen = {}

    class FakeRun:
        id = "r1"

        def snapshot(self):
            return {"id": "r1", "status": "running"}

    def fake_start(kind, title, cmd, cwd=None, account="", owner=""):
        seen.update({"kind": kind, "owner": owner, "cmd": cmd})
        return FakeRun()

    # 2026-10-31：/api/run 现在会校验账号必须存在于 accounts.json（未知账号 400 且
    # 不起进程）。本用例只测 owner 透传，桩账号 acc1 不在真实台账里，所以账号表也打桩。
    try:
        with patch.object(runner.MANAGER, "start", fake_start), \
             patch.object(app.config, "load_accounts",
                          lambda *a, **k: {"accounts": [{"account": "acc1"}]}), \
             patch.object(app.scheduler, "validate_accounts", lambda a: ["acc1"]):
            app.start_run({"kind": "pipeline", "account": "acc1",
                           "date_from": "2026-10-17", "date_to": "2026-10-24",
                           "lease_owner": lease})
    finally:
        runner.RESOURCES.release(lease)
    assert_(seen.get("owner") == lease, f"owner 没透传：{seen}")
    assert_(seen.get("kind") == "pipeline", str(seen))


def _busy_400():
    exc = runner.ResourceBusy("有任务正在写同一本台账（repair「一键修复」）",
                              [{"resource": runner.LEDGER, "kind": "repair",
                                "title": "一键修复", "since": 1.0}])
    resp = asyncio.run(app.resource_busy(None, exc))
    body = json.loads(resp.body.decode("utf-8"))
    assert_(resp.status_code == 400, f"应 400，实际 {resp.status_code}")
    assert_("台账" in body["detail"], body)
    assert_(body["holders"] and body["holders"][0]["kind"] == "repair", body)


check("带 lease_owner 的 /api/run 把 owner 透传给执行层", _passthrough)
check("ResourceBusy 统一转 400 并带 holders（不再冒 500）", _busy_400)


# ── 4. daily_batch 把令牌带回 body ────────────────────────────
def _daily_body(env_value):
    seen = {}

    def fake_http(path, body=None, timeout=60):
        seen["path"] = path
        seen["body"] = body
        return {"id": "rid"}

    def fake_wait(rid, poll, timeout, log, stop_grace=None):
        return {"status": "done", "exit_code": 0, "duration_s": 100.0}

    acc = {"account": "acc1", "media_type": "video", "lang": "en"}
    old = os.environ.pop(daily_batch.LEASE_ENV, None)
    if env_value is not None:
        os.environ[daily_batch.LEASE_ENV] = env_value
    try:
        with patch.object(daily_batch, "_http", fake_http), \
             patch.object(daily_batch, "wait_run", fake_wait):
            daily_batch.run_account(acc, "2026-10-17", "2026-10-24",
                                    retry=1, timeout=60, poll=1,
                                    log=lambda m: None)
    finally:
        os.environ.pop(daily_batch.LEASE_ENV, None)
        if old is not None:
            os.environ[daily_batch.LEASE_ENV] = old
    assert_(seen.get("path") == "/api/run", str(seen.get("path")))
    return seen["body"] or {}


def _db_with():
    assert_(_daily_body("tok-9").get("lease_owner") == "tok-9", str(_daily_body("tok-9")))


def _db_without():
    assert_("lease_owner" not in _daily_body(None))


def _db_noresume():
    assert_(_daily_body("tok-9").get("no_resume") is True)


check("daily_batch：env 有令牌 → body.lease_owner 带回", _db_with)
check("daily_batch：无令牌（用户手动跑）→ body 不带该字段", _db_without)
check("daily_batch：no_resume 仍在（别把老坑带回来）", _db_noresume)


# ── 4b. HTTP 错误原因不再被吞（今天 500 只剩一句废话的教训） ──
def _http_detail():
    import io
    import urllib.error

    class Opener:
        def open(self, req, timeout=None):
            body = io.BytesIO(json.dumps(
                {"detail": "有任务正在写同一本台账（repair「一键修复」）"}).encode("utf-8"))
            raise urllib.error.HTTPError(daily_batch.WORKBENCH + "/api/run", 400,
                                         "Bad Request", {}, body)

    with patch.object(daily_batch, "_LOCAL_OPENER", Opener()):
        try:
            daily_batch._http("/api/run", {"kind": "pipeline"})
        except urllib.error.HTTPError as e:
            assert_(e.code == 400, f"code 必须保留，实际 {e.code}")
            assert_("台账" in str(e.reason), f"detail 没拼进 reason：{e.reason}")
            return
    raise AssertionError("应抛 HTTPError")


def _http_no_detail():
    import io
    import urllib.error

    class Opener:
        def open(self, req, timeout=None):     # 无 body（如 404／代理 502）
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))

    with patch.object(daily_batch, "_LOCAL_OPENER", Opener()):
        try:
            daily_batch._http("/api/runs/x")
        except urllib.error.HTTPError as e:
            assert_(e.code == 404, f"code 必须保留，实际 {e.code}")
            assert_(e.reason == "Not Found", str(e.reason))
            return
    raise AssertionError("应抛 HTTPError")


check("_http：4xx 的 detail 拼进异常 reason（日志能看到真因）", _http_detail)
check("_http：没有 detail 时原样抛出，code 仍是 404（stop 逻辑依赖）", _http_no_detail)


# ── 5. tk_reverse 图文帖识别 ─────────────────────────────────
def _audio_only():
    meta = {"formats": [{"format_id": "audio", "vcodec": "none", "ext": "mp3"}]}
    try:
        tk_reverse._assert_playable(meta)
    except tk_reverse.ReverseError as e:
        assert_("图文帖" in str(e), str(e))
        return
    raise AssertionError("只有音轨的格式表必须判为图文帖")


def _has_video():
    meta = {"formats": [{"format_id": "audio", "vcodec": "none"},
                        {"format_id": "h264_720p", "vcodec": "h264"}]}
    tk_reverse._assert_playable(meta)          # 不该抛


def _empty_formats():
    tk_reverse._assert_playable({})


check("仅 audio 的格式表 → 明确判「图文帖」，不再甩 yt-dlp 进度尾巴", _audio_only)
check("含视频轨 → 放行", _has_video)
check("拿不到 formats（本地导入/接口异常）→ 不臆断，放行", _empty_formats)


# ── 6. 真子进程闭环：修复派生的子进程确实拿到令牌 ────────────
def _run_script_run(tmp: str) -> str:
    """跑一个 kind=script 的子进程（无台账租约），返回它看到的令牌。"""
    code = ("import os,pathlib;"
            f"pathlib.Path(r'{tmp}').write_text(os.environ.get('WB_LEASE_OWNER',''))")
    run = runner.MANAGER.start("script", "单测 · env 透传",
                               [__import__("config").python_exe(), "-c", code])
    for _ in range(300):
        if run.status != "running":
            break
        import time as _t
        _t.sleep(0.1)
    return Path(tmp).read_text(encoding="utf-8", errors="replace")


def _repair_child_gets_lease():
    import tempfile
    import time
    import repair

    tmp = tempfile.mktemp(suffix=".lease")
    code = ("import os,pathlib;"
            f"pathlib.Path(r'{tmp}').write_text(os.environ.get('WB_LEASE_OWNER',''))")
    it = {"id": "rp01", "kind": "collect", "kind_label": "采集", "account": "acc1",
          "title": "采集重跑", "reason": "单测", "repairable": True, "skip_reason": "",
          "_ctx": {"out_dir": "D:/none", "audio_dir": "D:/none"}}
    try:
        with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
             patch.object(repair, "_steps_for", lambda *a, **k: [("单测", ["x"])]), \
             patch.object(repair, "_verify", lambda *a, **k: (True, "")), \
             patch.object(repair.RepairSession, "_run_cmd", _fake_run_cmd(code)):
            repair.start({"items": [it]}, ids=["rp01"], days=1, retry=1, timeout=60)
            lease = repair._SESSION.lease
            for _ in range(300):
                if repair.status().get("status") != "running":
                    break
                time.sleep(0.1)
        seen = Path(tmp).read_text(encoding="utf-8", errors="replace")
        assert_(lease, "修复会话必须先拿到租约")
        assert_(seen == lease, f"子进程看到的令牌 {seen!r} != 会话租约 {lease!r}")
    finally:
        repair._SESSION = None
        Path(tmp).unlink(missing_ok=True)


def _fake_run_cmd(code):
    """把修复的执行器换成「用 owner=租约 起一个 python 脚本」——走的仍是 runner 真实路径。"""
    import config as _cfg

    def fn(self, it, label, cmd):
        run = runner.MANAGER.start(kind="repair", title="单测 · 子任务",
                                   cmd=[_cfg.python_exe(), "-c", code],
                                   account="acc1", owner=self.lease)
        self.current_run_id = run.id
        for _ in range(300):
            if run.status != "running":
                break
            import time as _t
            _t.sleep(0.1)
        snap = run.snapshot()
        return snap.get("status") == "done", "ok", run.id
    return fn


def _top_child_has_no_lease():
    import tempfile
    tmp = tempfile.mktemp(suffix=".lease")
    try:
        assert_(_run_script_run(tmp) == "", "顶层脚本不该继承任何租约令牌")
    finally:
        Path(tmp).unlink(missing_ok=True)


check("真子进程：修复派生任务拿到会话租约（端到端 env 透传）", _repair_child_gets_lease)
check("真子进程：顶层脚本 run 的环境里没有令牌", _top_child_has_no_lease)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
