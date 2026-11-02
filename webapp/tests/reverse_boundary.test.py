# -*- coding: utf-8 -*-
"""反推删除边界回归（2026-10-21 独立验收 P1-1）。

用部署源码里真实的 DELETE 处理器 + 临时反推根目录，走真实 ASGI 请求
（raw path = /api/reverse/%2E%2E），断言：
  · 越界/非数字 ID 返回 4xx，不落 ok/removed=true
  · 反推根目录的兄弟哨兵文件完整保留
  · 合法数字 ID 仍能正常删除自己的任务目录

不碰线上 reverse 目录：REVERSE_DIR 全部改到临时目录。

跑法：webapp/.venv/Scripts/python.exe tests/reverse_boundary.test.py
"""
import ast
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T
from fastapi import FastAPI, HTTPException

APP_PY = Path(__file__).resolve().parents[1] / "app.py"


def _isolated_app() -> FastAPI:
    """把部署 app.py 里的 reverse_delete 原样摘出来挂到一个独立应用上。"""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8-sig"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "reverse_delete")
    isolated = FastAPI()
    ns = {"app": isolated, "tk_reverse": T, "HTTPException": HTTPException}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "actual_reverse_delete.py", "exec"), ns)
    return isolated


def _delete(app: FastAPI, raw: bytes, decoded: str) -> tuple[int, dict]:
    messages = []

    async def request():
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "DELETE", "scheme": "http", "path": decoded, "raw_path": raw,
            "query_string": b"", "root_path": "", "headers": [],
            "server": ("test", 80), "client": ("127.0.0.1", 1),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)

    asyncio.run(request())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, (json.loads(body) if body else {})


class ReverseDeleteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reverse"
        self.root.mkdir()
        self.sibling = Path(self.tmp.name) / "sibling_business_data.txt"
        self.sibling.write_text("disposable acceptance sentinel", encoding="utf-8")
        self._p1 = patch.object(T, "REVERSE_DIR", self.root)
        self._p2 = patch.object(T, "_STATE", {})
        self._p3 = patch.object(T, "_WORKERS", {})
        for p in (self._p1, self._p2, self._p3):
            p.start()
        self.app = _isolated_app()

    def tearDown(self):
        for p in (self._p3, self._p2, self._p1):
            p.stop()
        self.tmp.cleanup()

    def test_parent_escape_is_rejected_and_sibling_survives(self):
        for raw, decoded in ((b"/api/reverse/%2E%2E", "/api/reverse/.."),
                             (b"/api/reverse/..%2Fwebapp", "/api/reverse/../webapp")):
            with self.subTest(raw=raw):
                status, body = _delete(self.app, raw, decoded)
                self.assertGreaterEqual(status, 400, body)
                self.assertLess(status, 500, body)
                self.assertNotEqual(body.get("ok"), True, body)
        self.assertTrue(self.sibling.exists(), "兄弟哨兵文件被删了")
        self.assertTrue(self.root.exists(), "反推根目录被删了")

    def test_non_numeric_ids_are_rejected(self):
        for raw, decoded in ((b"/api/reverse/abc", "/api/reverse/abc"),
                             (b"/api/reverse/%2E", "/api/reverse/."),
                             (b"/api/reverse/%EF%BC%91", "/api/reverse/１")):
            with self.subTest(raw=raw):
                status, body = _delete(self.app, raw, decoded)
                self.assertEqual(status, 400, body)
        self.assertTrue(self.sibling.exists())

    def test_valid_numeric_id_still_deletes_its_own_dir(self):
        victim = self.root / "7698863537006939394"
        victim.mkdir()
        (victim / "status.json").write_text('{"stage": "done"}', encoding="utf-8")
        status, body = _delete(self.app, b"/api/reverse/7698863537006939394",
                               "/api/reverse/7698863537006939394")
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"ok": True, "removed": True})
        self.assertFalse(victim.exists())
        self.assertTrue(self.root.exists())
        self.assertTrue(self.sibling.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
