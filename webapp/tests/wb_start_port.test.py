# -*- coding: utf-8 -*-
"""自启 / 手动入口的端口统一（2026-11-01，T-01）。

只做**静态**断言，不启动任何进程——生产的 8789 与旧 8787 实例不能被打扰。
（真实启动验证在 QA 用例 `qa/T-01/case_autostart_port.py`，用隔离数据目录在 8897 端口跑。）

守住的四件事：
  1. `wb_start.ps1` 不带参数时默认 `$Port = 8789`；
  2. 启动子进程**之前**设置 `$env:WB_PORT = "$Port"`（`_boot2.py` 不接收 `--port`，
     只能靠环境变量传给 `app.py` 的 `_listen_port()`）；
  3. ps1 里不再留下默认 8787 的启动路径；
  4. `start.bat` 打开 8789，并把 `WB_PORT=8789` 传给 app。

外加：`wb_start.ps1` 必须保持 ASCII-only（Windows PowerShell 5.1 用 ANSI 读 BOM-less
中文会乱码，脚本第 14-15 行有说明）。

跑法：webapp/.venv/Scripts/python.exe tests/wb_start_port.test.py
"""
import re
import sys
import unittest
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1]
PS1 = WEBAPP / "wb_start.ps1"
BAT = WEBAPP / "start.bat"


def _ps1_text() -> str:
    """按 ASCII 解码；顺带就是 ASCII-only 的断言。"""
    return PS1.read_bytes().decode("ascii")


def _bat_text() -> str:
    return BAT.read_bytes().decode("utf-8")


class WbStartPortTests(unittest.TestCase):
    def test_default_port_is_8789(self):
        text = _ps1_text()
        self.assertRegex(text, r"param\s*\([^)]*\[int\]\s*\$Port\s*=\s*8789",
                         "wb_start.ps1 不带参数时默认端口必须是 8789")

    def test_wb_port_env_is_set_before_starting_child(self):
        text = _ps1_text()
        m = re.search(r"\$env:WB_PORT\s*=\s*\"\$Port\"", text)
        self.assertIsNotNone(m, '启动前必须设置 $env:WB_PORT = "$Port"（_boot2.py 不收 --port）')
        start = text.find("Start-Process")
        self.assertNotEqual(-1, start, "找不到 Start-Process 那一行")
        self.assertLess(m.start(), start,
                        "WB_PORT 必须在 Start-Process 之前设置，否则子进程继承不到")

    def test_no_default_8787_start_path_left(self):
        text = _ps1_text()
        self.assertNotIn("8787", text,
                         "wb_start.ps1 里不应再出现 8787（文件头注释的 8787 说明也要改成 8789）")

    def test_already_log_keeps_documented_shape(self):
        """幂等分支的日志行是 DoD 的检查点：`ALREADY port=8789`。"""
        self.assertIn("ALREADY port={0}", _ps1_text())

    def test_still_ascii_only(self):
        """PowerShell 5.1 会把 BOM-less UTF-8 的中文按 ANSI 读成乱码，必须纯 ASCII。"""
        _ps1_text()   # decode('ascii') 成功即无 >127 字节


class StartBatTests(unittest.TestCase):
    def test_points_to_8789(self):
        text = _bat_text()
        self.assertIn("http://127.0.0.1:8789", text, "start.bat 必须打开 8789")
        self.assertNotIn("8787", text, "start.bat 不应再出现 8787")

    def test_sets_wb_port_for_app(self):
        text = _bat_text()
        self.assertRegex(text, r"(?im)^\s*set\s+WB_PORT=8789\s*$",
                         "start.bat 必须设置 WB_PORT=8789 传给 app.py")

    def test_wb_port_set_before_launch(self):
        text = _bat_text()
        m = re.search(r"(?im)^\s*set\s+WB_PORT=8789\s*$", text)
        self.assertIsNotNone(m)
        launch = text.find("app.py")
        self.assertNotEqual(-1, launch)
        self.assertLess(m.start(), launch, "WB_PORT 必须在启动 app.py 之前设置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
