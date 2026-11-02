# -*- coding: utf-8 -*-
"""run_account_exists.test.py — 运行入口必须校验账号真的在 accounts.json 里（P1-2）

背景（2026-11-01 全功能体检 P1-2）
    `POST /api/run` 只调 `scheduler.validate_accounts` → `config.validate_account_name`，
    那一步只做**正则/长度**校验，不查账号是否存在。于是传一个格式合法但
    accounts.json 里没有的账号（如 `acc_not_exist_zz`）+ 合法日期 → 200、真起子进程、
    runs 记录 +1（活 SSE 抓到子进程 stderr 与 exit_code=2）。三种参数形态
    （account / accounts 数组）全部复现。`POST /api/automations/{flow_id}/run`
    同样只做账号名正则校验。

本用例覆盖任务 T-03 的 DoD 1-5
    1. kind=pipeline + 不存在的账号 → 400「账号不存在」，start 未调用、不写运行记录
    2. 合法账号 → 仍调用 runner.MANAGER.start；停用账号（active=false 但文件里有）仍允许
    3. accounts=[] → 400「至少选择一个账号」（原行为不变）
    4. POST /api/automations/{flow_id}/run 带不存在账号 → 400「账号不存在」、不触发；
       不带账号（全部）→ 行为不变
    5. kind=daily 同样校验

隔离
    - `import _isolate`：运行记录落到临时文件，绝不写生产 data/runs.jsonl
    - 临时 accounts.json（一个启用账号 + 一个停用账号），patch config.ACCOUNTS_JSON
    - patch config.ledger_path → 临时路径（`_build_pipeline_cmd` 只取字符串，不读内容）
    - patch runner.MANAGER.start → 假 run（绝不真起子进程）
    - patch store.append → 断言「不写运行记录」
运行：webapp/.venv/Scripts/python.exe tests/run_account_exists.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  运行 journal 落到临时文件，不污染生产 runs.jsonl
import app  # noqa: E402
import config  # noqa: E402
import runner  # noqa: E402
import store  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FAKE = "acc_not_exist_zz"       # 正则合法（小写+下划线），但 accounts.json 里没有
ON = "acc_on"                   # 启用账号
OFF = "acc_off"                 # 停用账号：active=false，但文件里有 → 仍允许

DATE = {"date_from": "2026-10-29", "date_to": "2026-10-29"}

FLOW_DAILY = {"id": "flow_daily", "kind": "daily", "name": "日更测试", "accounts": []}
FLOW_SCRIPT = {"id": "flow_script", "kind": "script", "name": "脚本测试",
               "script": "check_all_done.py", "accounts": []}


class _FakeRun:
    """runner.MANAGER.start 的替身：只回一个够用的快照，绝不真起进程。"""

    def __init__(self, kind, title, cmd, cwd=None, account="", owner=""):
        self.id = "run_fake_0001"
        self.kind = kind
        self.title = title
        self.cmd = list(cmd)
        self.account = account
        self.status = "running"

    def snapshot(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "account": self.account, "status": self.status}


class RunAccountExistsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # 一个启用账号 + 一个停用账号（停用账号在文件里 → 存在性校验必须放行）
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({
            "accounts": [
                {"account": ON, "active": True, "lang": "en"},
                {"account": OFF, "active": False, "lang": "en"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        self.ledger = self.root / "视频主簿_对标.xlsx"   # 只需路径，_build_pipeline_cmd 不读内容

        for name, val in (("ACCOUNTS_JSON", self.accounts),
                          ("ledger_path", lambda: self.ledger)):
            p = patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)

        p_start = patch.object(runner.MANAGER, "start", side_effect=_FakeRun)
        self.start_mock = p_start.start()
        self.addCleanup(p_start.stop)

        p_append = patch.object(store, "append")
        self.append_mock = p_append.start()
        self.addCleanup(p_append.stop)

    # ── 小工具 ────────────────────────────────────────────────────
    def _runs_lines(self) -> int:
        p = config.RUNS_JSONL
        return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0

    def _assert_rejected(self, fn, *args, **kwargs):
        """调用应抛 400「账号不存在」，且不起进程、不写运行记录。"""
        before = self._runs_lines()
        with self.assertRaises(HTTPException) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("账号不存在", str(ctx.exception.detail))
        self.start_mock.assert_not_called()
        self.append_mock.assert_not_called()
        self.assertEqual(self._runs_lines(), before)
        return ctx.exception

    # ── DoD 1：pipeline + 不存在的账号 ────────────────────────────
    def test_pipeline_unknown_account_rejected(self):
        """account= 单值形态：400，且不起进程、不写记录（P1-2 原始复现）。"""
        exc = self._assert_rejected(
            app.start_run, {"kind": "pipeline", "account": FAKE, **DATE})
        self.assertIn(FAKE, str(exc.detail))

    def test_pipeline_unknown_account_in_list_rejected(self):
        """accounts 数组形态：混一个不存在的账号也必须整体 400，不跑剩下那个。"""
        exc = self._assert_rejected(
            app.start_run, {"kind": "pipeline", "accounts": [ON, FAKE], **DATE})
        self.assertIn(FAKE, str(exc.detail))

    # ── DoD 2：合法 / 停用账号仍允许 ─────────────────────────────
    def test_pipeline_valid_account_still_starts(self):
        """合法账号行为不变：仍然起 run。"""
        data = app.start_run({"kind": "pipeline", "account": ON, **DATE})
        self.start_mock.assert_called_once()
        self.assertEqual(self.start_mock.call_args.kwargs.get("account"), ON)
        self.assertEqual(data["account"], ON)

    def test_pipeline_inactive_account_still_allowed(self):
        """停用账号（active=false，但文件里有）仍允许起 run。"""
        app.start_run({"kind": "pipeline", "account": OFF, **DATE})
        self.start_mock.assert_called_once()
        self.assertEqual(self.start_mock.call_args.kwargs.get("account"), OFF)

    # ── DoD 3：accounts=[] 原行为不变 ────────────────────────────
    def test_pipeline_empty_accounts_keeps_original_message(self):
        """accounts=[] → 400「至少选择一个账号」（不是「账号不存在」）。"""
        with self.assertRaises(HTTPException) as ctx:
            app.start_run({"kind": "pipeline", "accounts": [], **DATE})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("至少选择一个账号", str(ctx.exception.detail))
        self.start_mock.assert_not_called()
        self.append_mock.assert_not_called()

    # ── DoD 5：kind=daily 同样校验 ───────────────────────────────
    def test_daily_unknown_account_rejected(self):
        self._assert_rejected(app.start_run, {"kind": "daily", "accounts": [FAKE]})

    def test_daily_valid_account_still_starts(self):
        app.start_run({"kind": "daily", "accounts": [ON]})
        self.start_mock.assert_called_once()

    def test_daily_without_accounts_still_runs_all(self):
        """不带 accounts = 全部账号，行为不变。"""
        app.start_run({"kind": "daily"})
        self.start_mock.assert_called_once()
        self.assertEqual(self.start_mock.call_args.kwargs.get("account"), "all")

    # ── DoD 4：自动化流程入口 ────────────────────────────────────
    def test_automation_with_unknown_account_rejected(self):
        """payload 带不存在账号 → 400，不触发 daily。"""
        with patch.object(config, "load_automations", lambda: [dict(FLOW_DAILY)]):
            self._assert_rejected(app.run_automation, "flow_daily", {"accounts": [FAKE]})

    def test_automation_script_flow_with_unknown_account_rejected(self):
        """kind=script 流程带不存在账号 → 400（不起脚本）。"""
        with patch.object(config, "load_automations", lambda: [dict(FLOW_SCRIPT)]):
            self._assert_rejected(app.run_automation, "flow_script", {"accounts": [FAKE]})

    def test_automation_flow_configured_unknown_account_rejected(self):
        """账号来自流程配置（payload 没带）时同样校验。"""
        flow = {**FLOW_DAILY, "accounts": [FAKE]}
        with patch.object(config, "load_automations", lambda: [flow]):
            self._assert_rejected(app.run_automation, "flow_daily", {})

    def test_automation_without_accounts_unchanged(self):
        """不带账号（全部）→ 行为不变：仍然起 run，account='all'。"""
        with patch.object(config, "load_automations", lambda: [dict(FLOW_DAILY)]):
            app.run_automation("flow_daily", {})
        self.start_mock.assert_called_once()
        self.assertEqual(self.start_mock.call_args.kwargs.get("account"), "all")

    def test_automation_with_valid_account_still_starts(self):
        """合法账号仍能触发「全部流程」之外的单账号运行。"""
        with patch.object(config, "load_automations", lambda: [dict(FLOW_DAILY)]):
            app.run_automation("flow_daily", {"accounts": [ON]})
        self.start_mock.assert_called_once()
        self.assertEqual(self.start_mock.call_args.kwargs.get("account"), ON)


if __name__ == "__main__":
    unittest.main(verbosity=2)
