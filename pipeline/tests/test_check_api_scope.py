# -*- coding: utf-8 -*-
"""test_check_api_scope.py — 终验 check_api 的账号范围口径（2026-10-21 独立验收 B）

验收报告 B：`ledger_verify` 天天 ERROR，唯一 bad 是一个**停用账号**（`example_account_047`）。
根因是 `post_update.check_api()` 在 scope=None 时遍历 overview 全部 47 条、不按
configured 启用名单过滤，而 `check_ledger()` 是按 `active` + `ledger_skip` 过滤的。

本套件用假的 overview 响应（不联网、不碰线上服务）锁死五条：
  1. 停用账号（accounts.json active=false）非 ok **不参与**终验 → status=ok，
     并且该账号仍出现在 `skipped_disabled` 里（历史可见性不丢）。
  2. 启用账号 severity=error → status=error 并进 `bad`。
  3. 启用账号 severity=warn（「商品提取不全」「抓取未全部入账」这类业务待办）→
     **不顶红**，单列进 `warn`。
     ★ 2026-10-28：以前 warn 也判 error，任何一个账号 warn 就让 verify 恒 rc=1，
       日更回执永远 failed_stages=[ledger_verify]，工作台待接管面板长期「回执存疑」
       且一键修复恒为 0 项 —— 和 2026-10-21 的「停用账号顶红」是同一型问题。
  4. `ledger_skip=true` 的账号同样不参与（与 check_ledger 完全同口径）。
  5. 显式 scope 仍然生效（只核指定账号）。

跑法：python pipeline/tests/test_check_api_scope.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE / "scripts"))
import post_update as pu  # noqa: E402

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


class Resp:
    def __init__(self, payload):
        self._d = payload

    def read(self):
        return json.dumps(self._d, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOverview:
    """把 overview 响应换成给定账号表；不改任何线上文件。"""

    def __init__(self, accounts):
        self.accounts = accounts
        self._orig = None

    def __enter__(self):
        self._orig = urllib.request.urlopen
        payload = {"accounts": self.accounts}

        def fake_urlopen(url, timeout=None):
            return Resp(payload)

        urllib.request.urlopen = fake_urlopen
        return self

    def __exit__(self, *a):
        urllib.request.urlopen = self._orig


def acc(name, severity="ok", active=True, ledger_skip=False, media="video"):
    return {"account": name, "severity": severity, "gaps": [],
            "_cfg": {"account": name, "active": active,
                     "ledger_skip": ledger_skip, "media_type": media}}


def make_cfg(tmp: Path, items: list[dict]) -> dict:
    cfg = {"accounts": [i["_cfg"] for i in items]}
    p = tmp / "accounts.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg


def _overview_rows(items):
    return [{"account": i["account"], "severity": i["severity"],
             "active": True, "has_data": True, "enabled": i["_cfg"].get("active", True),
             "gaps": []} for i in items]


def case_disabled_account_does_not_fail():
    """验收 B 原始形态：唯一非 ok 的是停用账号 → 必须判 ok，且该账号仍可见。"""
    tmp = Path(tempfile.mkdtemp(prefix="chkapi_"))
    items = [acc("alpha"), acc("beta"),
             acc("example_account_047", severity="warn", active=False, media="image")]
    cfg = make_cfg(tmp, items)
    with FakeOverview(_overview_rows(items)):
        r = pu.check_api(cfg=cfg)
    assert_(r["status"] == "ok", f"停用账号不得把终验顶红：{r}")
    assert_(r["checked"] == 2, f"只应核 2 个启用账号，实际 {r['checked']}")
    assert_(r["bad"] == [], f"bad 应为空：{r['bad']}")
    assert_(r["skipped_disabled"] == ["example_account_047"],
            f"停用账号仍需列出（历史可见）：{r['skipped_disabled']}")
    assert_(r["overview_total"] == 3, f"应记录 overview 总数：{r['overview_total']}")
    assert_(r["scope_basis"] == "accounts.json active+ledger_skip", r.get("scope_basis"))


def case_enabled_warn_not_error():
    """启用账号 severity=warn → 单列进 warn，不把终验顶红（2026-10-28 口径）。"""
    tmp = Path(tempfile.mkdtemp(prefix="chkapi_"))
    items = [acc("alpha", severity="warn"), acc("beta"),
             acc("gone", active=False)]
    cfg = make_cfg(tmp, items)
    with FakeOverview(_overview_rows(items)):
        r = pu.check_api(cfg=cfg)
    assert_(r["status"] == "ok", f"warn 不应判 ERROR：{r}")
    assert_(r["bad"] == [], f"bad 应为空：{r['bad']}")
    assert_([w["account"] for w in r["warn"]] == ["alpha"],
            f"warn 应只列 alpha：{r['warn']}")


def case_enabled_error_still_fails():
    """启用账号 severity=error 仍必须 error（放宽 warn 不能顺带漏报真错）。"""
    tmp = Path(tempfile.mkdtemp(prefix="chkapi_"))
    items = [acc("alpha", severity="error"), acc("beta", severity="warn"),
             acc("gone", active=False)]
    cfg = make_cfg(tmp, items)
    with FakeOverview(_overview_rows(items)):
        r = pu.check_api(cfg=cfg)
    assert_(r["status"] == "error", f"启用账号 error 必须 error：{r}")
    assert_([b["account"] for b in r["bad"]] == ["alpha"], f"bad 应只有 alpha：{r['bad']}")
    assert_([w["account"] for w in r["warn"]] == ["beta"], f"warn 应只有 beta：{r['warn']}")


def case_ledger_skip_excluded():
    """ledger_skip 账号与 check_ledger 同口径，不参与 API 终验。"""
    tmp = Path(tempfile.mkdtemp(prefix="chkapi_"))
    items = [acc("alpha"), acc("legacy", severity="warn", ledger_skip=True)]
    cfg = make_cfg(tmp, items)
    with FakeOverview(_overview_rows(items)):
        r = pu.check_api(cfg=cfg)
    assert_(r["status"] == "ok", f"ledger_skip 账号不得参与：{r}")
    assert_("legacy" in r["skipped_disabled"], r["skipped_disabled"])


def case_scope_still_works():
    """显式 scope：只核指定账号，且仍受启用名单约束。"""
    tmp = Path(tempfile.mkdtemp(prefix="chkapi_"))
    items = [acc("alpha", severity="warn"), acc("beta"),
             acc("gone", severity="warn", active=False)]
    cfg = make_cfg(tmp, items)
    with FakeOverview(_overview_rows(items)):
        beta = pu.check_api(scope=["beta"], cfg=cfg)
        both = pu.check_api(scope=["beta", "gone"], cfg=cfg)
    assert_(beta["status"] == "ok" and beta["checked"] == 1, f"scope=beta：{beta}")
    assert_(both["status"] == "ok" and both["checked"] == 1,
            f"停用账号即使在 scope 里也不得参与：{both}")


print("终验 check_api 账号范围（2026-10-21 B）：")
for name, fn in [
    ("停用账号非 ok 不顶红、仍列明", case_disabled_account_does_not_fail),
    ("启用账号 warn 不顶红、单列 warn", case_enabled_warn_not_error),
    ("启用账号 error 仍 error", case_enabled_error_still_fails),
    ("ledger_skip 账号不参与终验", case_ledger_skip_excluded),
    ("显式 scope 仍生效且受启用名单约束", case_scope_still_works),
]:
    check(name, fn)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
