# -*- coding: utf-8 -*-
"""pipeline_retry_receipt.test.py — /api/pipeline/retry 的三种范围（2026-10-28）

背景：队列为空、只剩「阶段告警 / 回执存疑」时，面板的「重跑全部」点下去只会
400「没有待接管账号」—— 而面板自己的跳过理由又让你去点它。这里把四条锁死：

  1. 队列有账号 → 只重跑这些账号（scope=accounts，行为不变）
  2. 队列空 + 告警/回执存疑 → 重跑上一轮账号重建回执（scope=receipt，
     daily_batch 按 partial 并入当日整轮，不丢未跑账号的结论）
  3. 拿不到上一轮账号 → 退化成整轮（不传 --accounts）
  4. 队列空且回执正常 → 仍然 400，不误触发全账号长跑

运行：webapp/.venv/Scripts/python.exe tests/pipeline_retry_receipt.test.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落临时文件，别污染生产 runs.jsonl
import app  # noqa: E402

_ok = 0
_fail = 0
_calls: list[list[str]] = []


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  OK  {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ERR {name} -> {e}")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ERR {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _fake_start(accounts=None, retry: int = 2, timeout: int = 5400):
    _calls.append(list(accounts or []))
    return {"id": "fake-run", "account": ",".join(accounts or []) or "all"}


def _call(st: dict):
    """用假 status 调端点；返回 (响应或 {error: 状态码}, start_retry 收到的账号)。"""
    _calls.clear()
    with patch.object(app.pipeline_ops, "status", lambda *a, **k: st), \
            patch.object(app.pipeline_ops, "start_retry", _fake_start):
        try:
            return app.pipeline_retry({}), _calls[:]
        except app.HTTPException as e:
            return {"error": e.status_code}, _calls[:]


def case_queue_accounts():
    st = {"takeover": [{"account": "accA"}], "alerts": [],
          "receipt": {"stale": False}, "last_round": {"accounts": []}}
    r, calls = _call(st)
    assert_(r.get("scope") == "accounts", f"scope 应保持 accounts：{r}")
    assert_(calls == [["accA"]], f"只应重跑队列账号：{calls}")


def case_empty_queue_stage_alert():
    st = {"takeover": [], "alerts": [{"stage": "ledger_verify", "reason": "verify rc=1"}],
          "receipt": {"stale": True}, "last_round": {"accounts": ["example_account_021"]}}
    r, calls = _call(st)
    assert_(r.get("scope") == "receipt", f"应走回执重建：{r}")
    assert_(calls == [["example_account_021"]], f"应重跑上一轮账号：{calls}")


def case_empty_queue_no_last_round():
    st = {"takeover": [], "alerts": [], "receipt": {"stale": True},
          "last_round": {"accounts": []}}
    r, calls = _call(st)
    assert_(r.get("scope") == "receipt", f"应走回执重建：{r}")
    assert_(calls == [[]], f"拿不到上一轮账号时应退化成整轮（不传账号）：{calls}")


def case_all_green_still_rejected():
    st = {"takeover": [], "alerts": [], "receipt": {"stale": False},
          "last_round": {"accounts": ["x"]}}
    r, calls = _call(st)
    assert_(r.get("error") == 400, f"都正常时应 400：{r}")
    assert_(calls == [], f"不得启动任何运行：{calls}")


print("重跑全部 / 重建回执（2026-10-28）：")
for name, fn in [
    ("队列有账号 → 只重跑账号", case_queue_accounts),
    ("队列空 + 阶段告警 → 重跑上一轮重建回执", case_empty_queue_stage_alert),
    ("拿不到上一轮账号 → 整轮", case_empty_queue_no_last_round),
    ("队列空且回执正常 → 仍 400", case_all_green_still_rejected),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
