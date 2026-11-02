# -*- coding: utf-8 -*-
"""R15 回归：跨站写请求必须被中间件挡在路由之前（2026-10-23）。

用真实 ASGI 调用（不走 TestClient，venv 里没有 httpx），不触发任何写操作。
跑法：webapp/.venv/Scripts/python.exe tests/reverse_csrf.test.py
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A


def _call(method: str, path: str, headers: dict | None = None,
          body: bytes = b"") -> tuple[int, dict]:
    messages: list = []

    async def run():
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 1),
        }
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await A.app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    return status, payload


class CsrfGuardTests(unittest.TestCase):
    def test_cross_site_post_is_403(self):
        status, body = _call("POST", "/api/reverse/create",
                             {"content-type": "application/json",
                              "sec-fetch-site": "cross-site",
                              "origin": "https://evil.example"},
                             b'{"url":"https://www.tiktok.com/@a/video/123456789012345"}')
        self.assertEqual(status, 403, body)

    def test_foreign_origin_even_with_same_origin_site_is_403(self):
        status, body = _call("POST", "/api/reverse/stop/123456789012345",
                             {"sec-fetch-site": "same-origin",
                              "origin": "https://evil.example"})
        self.assertEqual(status, 403, body)

    def test_headless_client_is_not_blocked(self):
        status, body = _call("POST", "/api/reverse/stop/123456789012345")
        self.assertEqual(status, 400, body)          # 到路由了：没有在跑的任务


if __name__ == "__main__":
    unittest.main(verbosity=2)
