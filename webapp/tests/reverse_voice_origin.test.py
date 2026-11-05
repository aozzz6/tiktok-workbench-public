# -*- coding: utf-8 -*-
"""口播保存的 Origin 白名单：本机回环放行，非本机 403（2026-11-01，T-01）。

背景：`reverse_voice.save` 原来把允许的 Origin 逐条写死成
`http://127.0.0.1:8789` / `http://localhost:8789` / `:8791` 四条。
于是只要工作台换到别的端口（8787 旧实例、未来新端口），保存口播必然 403。
现在改成按 **host 判定**：host 是 `127.0.0.1` 或 `localhost`（任意端口）→ 放行；
其它 host → 403「仅允许本机工作台保存口播信息」；不带 Origin → 放行（保持原行为）。

隔离：`tk_reverse.REVERSE_DIR` patch 到临时目录，不碰生产 `reverse/`。
跑法：webapp/.venv/Scripts/python.exe tests/reverse_voice_origin.test.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须在 import tk_reverse 之前就把反推根目录指到临时目录，
# 否则模块级 `REVERSE_DIR.mkdir()` 会碰生产目录。导入完立刻还原环境，
# 免得同进程里别的用例被这个变量带走。
_BOOT_TMP = tempfile.mkdtemp(prefix="wb_rv_origin_boot_")
_prev_reverse_dir = os.environ.get("WB_REVERSE_DIR")
os.environ["WB_REVERSE_DIR"] = _BOOT_TMP

import tk_reverse as T
import reverse_voice as RV

if _prev_reverse_dir is None:
    os.environ.pop("WB_REVERSE_DIR", None)
else:
    os.environ["WB_REVERSE_DIR"] = _prev_reverse_dir
atexit.register(lambda: shutil.rmtree(_BOOT_TMP, ignore_errors=True))

VID = "123456789012345678"
FORBIDDEN = "仅允许本机工作台保存口播信息"


class _FakeRequest:
    """`save()` 只用到 `request.headers.get('origin')`，给一个最小替身。"""

    def __init__(self, headers=None):
        self.headers = dict(headers or {})


def _make_task(root: Path) -> Path:
    directory = root / VID
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text('{"video_id": "%s"}' % VID, encoding="utf-8")
    return directory


class LocalOriginAllowedTests(unittest.TestCase):
    """本机 host + 任意端口都要放行（含 8787 旧实例和未来的新端口）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wb_rv_origin_")
        self.root = Path(self._tmp.name)
        self.directory = _make_task(self.root)
        self._patch = patch.object(T, "REVERSE_DIR", self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _save(self, origin=None):
        headers = {} if origin is None else {"origin": origin}
        return RV.save(VID, {"state": "spoken", "language": "en"}, _FakeRequest(headers))

    def test_loopback_ipv4_any_port(self):
        for origin in (
            "http://127.0.0.1:8789",   # 当前生产
            "http://127.0.0.1:8791",   # 旧白名单里的第二个
            "http://127.0.0.1:8787",   # 开机自启的旧实例（原来的复现点）
            "http://127.0.0.1:8899",   # 未来新端口
            "http://127.0.0.1:80",     # 无端口写法的边界
        ):
            with self.subTest(origin=origin):
                self.assertTrue(self._save(origin)["ok"])

    def test_localhost_any_port(self):
        for origin in (
            "http://localhost:8789",
            "http://localhost:8791",
            "http://localhost:18999",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(self._save(origin)["ok"])

    def test_saved_file_is_written_for_allowed_origin(self):
        self._save("http://127.0.0.1:8899")
        target = self.directory / "voice.json"
        self.assertTrue(target.is_file(), "放行的 Origin 必须真的写盘")
        self.assertIn("spoken", target.read_text(encoding="utf-8"))

    def test_no_origin_keeps_old_behaviour(self):
        """不带 Origin（curl / 无头客户端）保持放行。"""
        self.assertTrue(self._save(None)["ok"])


class ForeignOriginForbiddenTests(unittest.TestCase):
    """非本机 host 一律 403，且不得写盘。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wb_rv_origin_")
        self.root = Path(self._tmp.name)
        self.directory = _make_task(self.root)
        self._patch = patch.object(T, "REVERSE_DIR", self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _save(self, origin):
        return RV.save(VID, {"state": "spoken", "language": "en"},
                       _FakeRequest({"origin": origin}))

    def test_foreign_origin_is_403_with_expected_message(self):
        for origin in (
            "http://evil.example.com",
            "https://evil.example.com",
            "http://evil.example.com:8789",
            "http://192.168.1.10:8789",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(Exception) as ctx:
                    self._save(origin)
                exc = ctx.exception
                self.assertEqual(403, getattr(exc, "status_code", None), str(exc))
                self.assertEqual(FORBIDDEN, getattr(exc, "detail", None))

    def test_lookalike_hosts_are_not_local(self):
        """`localhost.evil.com` / `127.0.0.1.evil.com` 不能被当成回环地址。"""
        for origin in (
            "http://localhost.evil.com:8789",
            "http://127.0.0.1.evil.com",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(Exception) as ctx:
                    self._save(origin)
                self.assertEqual(403, getattr(ctx.exception, "status_code", None), str(ctx.exception))

    def test_forbidden_origin_writes_nothing(self):
        with self.assertRaises(Exception):
            self._save("http://evil.example.com")
        self.assertFalse((self.directory / "voice.json").exists(),
                         "403 时不得写给口播文件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
