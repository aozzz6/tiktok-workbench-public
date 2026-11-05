# -*- coding: utf-8 -*-
"""端口可覆盖（2026-10-23）：WB_PORT / PORT 环境变量。

背景：8787 是约定端口，但本机别的工具也会抢；硬编码时只能杀进程。
默认必须仍是 8787 —— 自动化、daily_batch、文档都指它。

跑法：webapp/.venv/Scripts/python.exe tests/port_override.test.py
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A


class ListenPortTests(unittest.TestCase):
    def _p(self, env):
        clean = {k: v for k, v in os.environ.items() if k not in ("WB_PORT", "PORT")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            return A._listen_port()

    def test_default_is_8787(self):
        self.assertEqual(8787, self._p({}))

    def test_wb_port_wins(self):
        self.assertEqual(9001, self._p({"WB_PORT": "9001", "PORT": "9002"}))

    def test_port_fallback(self):
        self.assertEqual(9002, self._p({"PORT": "9002"}))

    def test_strips_spaces(self):
        self.assertEqual(9003, self._p({"WB_PORT": " 9003 "}))

    def test_bad_value_falls_back(self):
        self.assertEqual(8787, self._p({"WB_PORT": "八七八七"}))
        self.assertEqual(8787, self._p({"WB_PORT": ""}))

    def test_out_of_range_falls_back(self):
        self.assertEqual(8787, self._p({"WB_PORT": "80"}))       # < 1024
        self.assertEqual(8787, self._p({"WB_PORT": "70000"}))    # > 65535

    def test_wb_port_bad_then_port_good(self):
        self.assertEqual(9004, self._p({"WB_PORT": "abc", "PORT": "9004"}))


class DailyBatchBaseTests(unittest.TestCase):
    """daily_batch 必须跟着同一组变量走，否则换端口后自动化打到旧地址。"""

    def _base(self, env, workbench_on=None):
        import importlib
        clean = {k: v for k, v in os.environ.items() if k not in ("WB_PORT", "PORT", "WB_BASE")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
            import scripts.daily_batch as db
            importlib.reload(db)
            # 2026-10-24：workbench_base() 现在会先扫候选端口找真工作台。
            # 若不替掉探测，结果取决于本机当前有没有实例在跑（不确定性），
            # 所以这里固定探测结果，只断言"给什么环境变量就该拼什么地址"。
            with patch.object(db, "_looks_like_workbench",
                              side_effect=lambda port, timeout=2.0: port == workbench_on):
                return db.workbench_base()

    def test_default(self):
        # 一个都没探测到 → 回退约定端口 8787（报错信息仍指向它）
        self.assertEqual("http://127.0.0.1:8787", self._base({}))

    def test_reuses_running_workbench(self):
        # 8789 有真工作台 → 不再无条件用 8787（2026-10-24 端口发现）
        self.assertEqual("http://127.0.0.1:8789", self._base({}, workbench_on=8789))

    def test_follows_wb_port(self):
        self.assertEqual("http://127.0.0.1:8789", self._base({"WB_PORT": "8789"}))

    def test_wb_base_overrides(self):
        self.assertEqual("http://127.0.0.1:9009", self._base({"WB_BASE": "http://127.0.0.1:9009/",
                                                             "WB_PORT": "8789"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
