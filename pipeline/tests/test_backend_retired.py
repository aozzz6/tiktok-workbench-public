# -*- coding: utf-8 -*-
"""T-20 回归：openai 转写后端退役后，日更 / 一键运行不许再把它下发下去。

背景（代码评审）：`daily_batch.run_account` 曾把 `"backend": "openai"` 写死进 `/api/run` 的 body，
`webapp/app.py` 又原样转成 `--backend openai`。T-20 让 openai 退 2（CRD-0006）之后，
**每天定时日更的 s5 转写必然失败 → 整个 run 失败**。既有测试全打桩了传输层/子进程，抓不到。

判据：
1. `daily_batch.run_account` **真正构造出来的 body**：`backend ∈ {"faster", "auto"}`，绝不出现 openai
   （打桩 `_post_run_when_ledger_free` 截获 body，不联网、不起子进程）；
2. `webapp/app.py::_build_pipeline_cmd` 收到 `backend=openai` 时**不再原样下发**（按 auto 执行），
   非法值仍然 400，`faster`/缺省值行为不变。

跑法（仓库根）：`pipeline\\.venv\\Scripts\\python.exe pipeline\\tests\\test_backend_retired.py`
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent      # pipeline/tests
PIPE = HERE.parent                          # pipeline
REPO = PIPE.parent                          # 仓库根
SCRIPTS = PIPE / "scripts"
WEBAPP = REPO / "webapp"
WEBAPP_PY = WEBAPP / ".venv" / "Scripts" / "python.exe"

sys.path.insert(0, str(SCRIPTS))
import daily_batch  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
    except Exception as exc:                # noqa: BLE001
        _fail += 1
        print(f"  FAIL {name} -> {exc}")
        return
    _ok += 1
    print(f"  ok   {name}")


def assert_(cond, message=""):
    if not cond:
        raise AssertionError(message or "断言失败")


# ───────────── 1. daily_batch.run_account 真正构造的 body ─────────────
def case_daily_batch_body():
    captured: dict = {}
    originals = (daily_batch._post_run_when_ledger_free, daily_batch.wait_run)

    def fake_post(body, log):
        captured["body"] = dict(body)
        return {"id": "probe-run"}

    def fake_wait(rid, poll, timeout, log, stop_grace):
        return {"status": "failed", "abort_retry": True,
                "abort_reason": "probe（本用例不真跑）", "tail": ["probe"], "exit_code": 1}

    daily_batch._post_run_when_ledger_free = fake_post
    daily_batch.wait_run = fake_wait
    try:
        daily_batch.run_account({"account": "probe_acc", "lang": "en"},
                                "2026-11-01", "2026-11-02",
                                retry=1, timeout=1, poll=1, log=lambda m: None)
    finally:
        daily_batch._post_run_when_ledger_free, daily_batch.wait_run = originals

    body = captured.get("body") or {}
    assert_(body, "没有截获到 run_account 构造的 body（打桩没生效？）")
    backend = body.get("backend")
    assert_(backend in ("faster", "auto"),
            f"/api/run body 里的 backend 必须是 faster 或 auto，实得 {backend!r}（openai 已退役）")
    assert_(backend != "openai", "body 里仍然是 openai：日更的 s5 转写会整轮失败")
    assert_(body.get("kind") == "pipeline", f"kind 变了：{body.get('kind')!r}")
    assert_(body.get("no_resume") is True, "no_resume 丢了（会导致整轮静默空跑）")


# ───────────── 2. webapp/app.py::_build_pipeline_cmd ─────────────
APP_PROBE = r'''
import json, sys
sys.path.insert(0, r"%(webapp)s")
import app
from fastapi import HTTPException

base = {"account": "probe_acc", "date_from": "2026-11-01", "date_to": "2026-11-02",
        "steps": ["s5_transcribe"], "lang": "en"}
out = {}
def build(**extra):
    return app._build_pipeline_cmd({**base, **extra})

out["openai"] = build(backend="openai")
out["faster"] = build(backend="faster")
out["auto"] = build(backend="auto")
out["none"] = build()
try:
    build(backend="invalid")
    out["invalid"] = None
except HTTPException as exc:
    out["invalid"] = {"status": exc.status_code, "detail": str(exc.detail)}
print(json.dumps(out, ensure_ascii=False))
'''


def _app_cmds():
    assert_(WEBAPP_PY.is_file(), f"找不到 webapp 解释器：{WEBAPP_PY}")
    code = APP_PROBE % {"webapp": str(WEBAPP)}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([str(WEBAPP_PY), "-c", code], cwd=str(REPO), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    assert_(proc.returncode == 0,
            f"探测 app.py 失败 rc={proc.returncode}：{(proc.stderr or '')[-900:]!r}")
    line = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
    assert_(line, f"app.py 探测没有输出 JSON：{(proc.stdout or '')[-500:]!r}")
    return json.loads(line[-1])


def _backend_of(cmd):
    if "--backend" not in cmd:
        return None
    idx = cmd.index("--backend")
    return cmd[idx + 1] if idx + 1 < len(cmd) else "<missing>"


def case_app_retires_openai():
    cmds = _app_cmds()
    openai_cmd = cmds["openai"]
    assert_(_backend_of(openai_cmd) == "auto",
            f"app.py 收到 backend=openai 时必须按 auto 执行，实得 {openai_cmd!r}")
    assert_("openai" not in openai_cmd, f"命令里仍然出现 openai：{openai_cmd!r}")


def case_app_keeps_other_values():
    cmds = _app_cmds()
    assert_(_backend_of(cmds["faster"]) == "faster", f"faster 应变透传：{cmds['faster']!r}")
    assert_(_backend_of(cmds["auto"]) == "auto", f"auto 应原样：{cmds['auto']!r}")
    assert_(_backend_of(cmds["none"]) is None,
            f"没给 backend 时不该加 --backend（让 pipeline.py 用默认 auto）：{cmds['none']!r}")


def case_app_still_rejects_invalid():
    cmds = _app_cmds()
    bad = cmds["invalid"]
    assert_(isinstance(bad, dict) and bad.get("status") == 400,
            f"非法 backend 必须仍然 400，实得 {bad!r}")
    assert_(bad.get("detail"), "400 必须带一句可读中文说明")


print("[1] 日更入口不再下发 openai")
check("daily_batch.run_account 构造的 body：backend ∈ {faster, auto}，且 no_resume 还在",
      case_daily_batch_body)

print("[2] /api/run 的 backend 校验（真实 import app.py 跑一遍）")
check("app.py：backend=openai → 按 auto 执行，命令里不再出现 openai", case_app_retires_openai)
check("app.py：faster / auto 原样透传，缺省不加 --backend", case_app_keeps_other_values)
check("app.py：非法值仍然 400 且带说明", case_app_still_rejects_invalid)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
