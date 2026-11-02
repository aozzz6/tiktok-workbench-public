# -*- coding: utf-8 -*-
"""运行系列接口语义（2026-11-02，T-01 · P2-2 / P2-3 / P2-4）。

锁死三件事（都是「重启/误用之后才暴露」的那类静默问题）：

1. **limit 边界**：`/api/runs?limit=-5`、`0`、`501` → **422**；`50` → 200。
   现在 `limit: int = 50` 没有边界，负数会让 `load_all(limit=-5)` 返回**除最后 5 条外**
   的全部记录（`out[-limit:]` 语义），501 又没有上限。
2. **stop 不存在**：`POST /api/runs/{不存在}/stop` → **404「运行不存在」**。
   现在是 200 `{"stopped": false}`，前端把「id 打错/已被清理」误读成「停止失败」。
3. **历史运行回落**：重启后 `/api/runs` 列表里的历史 id 来自 `runs.jsonl`，但
   `/api/runs/{id}` 与 `/stream` 只查内存 → 恒 404「运行不存在」。
   现在：详情回落到 journal（只读），`lines=[]`；`/stream` 发一条 `done:true` 后结束。
   内存里**运行中**的 run 行为必须一点不变。

隔离：`import _isolate` 把 `WB_RUNS_FILE` 指到临时文件；本测试**绝不读生产**
`data/runs.jsonl`（文件末尾有守卫断言）。
不联网、不起进程、不碰 8789。

跑法：webapp/.venv/Scripts/python.exe tests/run_api_semantics.test.py
"""
import asyncio
import json
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
WEBAPP = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(WEBAPP))

import _isolate  # noqa: E402,F401  —— 必须在 app/config 之前：journal 落到临时文件
import app as A  # noqa: E402
import config  # noqa: E402
import store  # noqa: E402

HIST_ID = "histt01aa0001"
HIST_ROW = {
    "id": HIST_ID, "kind": "pipeline", "title": "历史 · 测试",
    "account": "acc1", "cmd": "python pipeline.py", "started_at": 1730000000.123,
    "status": "done", "exit_code": 0, "finished_at": 1730000060.5,
}


def _call(method: str, path: str, query: str = "", headers: dict | None = None,
          body: bytes = b"") -> tuple[int, bytes]:
    """直接走 ASGI（venv 里没有 httpx，不用 TestClient），返回 (status, body)。"""
    messages: list = []

    async def run():
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": query.encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "server": ("127.0.0.1", 8789), "client": ("127.0.0.1", 1),
        }
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # 之后「永不返回」= 连接没断。不能马上回 http.disconnect：
            # Starlette 会因此取消 StreamingResponse 的生成器，SSE 一个 body 都拿不到。
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await A.app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, raw


def _json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}


def _sse_events(raw: bytes) -> list[dict]:
    events = []
    for block in raw.decode("utf-8", "replace").split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        try:
            events.append(json.loads(block[len("data:"):].strip()))
        except ValueError:
            continue
    return events


class _FakeRun:
    """内存里一条 run 的最小替身（只要详情/SSE 用到的字段）。"""

    def __init__(self, status: str = "done", exit_code=0):
        self.status = status
        self.exit_code = exit_code

    def snapshot(self) -> dict:
        return {"id": "mem_run_1", "kind": "pipeline", "title": "内存 · 测试",
                "status": self.status, "exit_code": self.exit_code,
                "started_at": 1730001000.0}


class LimitBoundTests(unittest.TestCase):
    """P2-3：limit 越界必须 422，不能把负数当「切片」用。"""

    def test_out_of_range_limits_are_422(self):
        for value in ("-5", "0", "501"):
            with self.subTest(limit=value):
                status, raw = _call("GET", "/api/runs", query=f"limit={value}")
                self.assertEqual(422, status,
                                 f"limit={value} 应 422，实际 {status}: {raw[:200]!r}")

    def test_limit_50_is_200(self):
        status, raw = _call("GET", "/api/runs", query="limit=50")
        self.assertEqual(200, status, raw[:300])
        self.assertIn("runs", _json(raw))

    def test_limit_1_and_500_are_allowed(self):
        for value in ("1", "500"):
            with self.subTest(limit=value):
                status, _ = _call("GET", "/api/runs", query=f"limit={value}")
                self.assertEqual(200, status)


class StopRunTests(unittest.TestCase):
    """P2-4：不存在的 run → 404；内存中存在的 run 行为不变。"""

    def test_stop_unknown_run_is_404(self):
        status, raw = _call("POST", "/api/runs/zzz_not_exist/stop")
        self.assertEqual(404, status, raw[:300])
        self.assertEqual("运行不存在", _json(raw).get("detail"))

    def test_stop_running_run_still_200(self):
        calls = []
        alive = object()
        with patch.object(A.runner.MANAGER, "get",
                          lambda rid: alive if rid == "run_alive" else None), \
                patch.object(A.runner.MANAGER, "stop",
                             lambda rid: (calls.append(rid), True)[1]):
            status, raw = _call("POST", "/api/runs/run_alive/stop")
        self.assertEqual(200, status, raw[:300])
        self.assertEqual({"stopped": True}, _json(raw))
        self.assertEqual(["run_alive"], calls)

    def test_stop_memory_run_already_finished_still_200_false(self):
        """内存里有、但已结束：保持 200 {"stopped": false}，不因 404 改造变 404。"""
        with patch.object(A.runner.MANAGER, "get", lambda rid: object()), \
                patch.object(A.runner.MANAGER, "stop", lambda rid: False):
            status, raw = _call("POST", "/api/runs/hist_done/stop")
        self.assertEqual(200, status, raw[:300])
        self.assertEqual({"stopped": False}, _json(raw))


class HistoryFallbackTests(unittest.TestCase):
    """P2-2：只在 runs.jsonl 里的历史 run，详情与 SSE 都不能 404。"""

    @classmethod
    def setUpClass(cls):
        store.append(HIST_ROW)      # journal = 临时文件（_isolate 保证）

    def test_list_contains_history_row(self):
        status, raw = _call("GET", "/api/runs", query="limit=10")
        self.assertEqual(200, status, raw[:300])
        ids = [r.get("id") for r in _json(raw).get("runs", [])]
        self.assertIn(HIST_ID, ids, f"历史行必须出现在列表里：{ids}")

    def test_detail_falls_back_to_journal(self):
        status, raw = _call("GET", "/api/runs", query="limit=10")
        self.assertEqual(200, status)
        listed = next((r for r in _json(raw).get("runs", []) if r.get("id") == HIST_ID), None)
        self.assertIsNotNone(listed, "列表里应先有这条历史行")

        status, raw = _call("GET", f"/api/runs/{HIST_ID}")
        self.assertEqual(200, status,
                         f"历史 run 详情应回落 journal，实际 {status}: {raw[:300]!r}")
        detail = _json(raw)
        for key in ("id", "status", "kind", "started_at"):
            self.assertEqual(listed[key], detail[key], f"{key} 必须与列表一致")
        self.assertEqual([], detail["lines"])
        self.assertEqual("done", detail["status"])

    def test_stream_history_ends_with_done(self):
        status, raw = _call("GET", f"/api/runs/{HIST_ID}/stream")
        self.assertEqual(200, status,
                         f"历史 run 的 SSE 不应 404，实际 {status}: {raw[:300]!r}")
        events = _sse_events(raw)
        self.assertTrue(events, f"应至少有一条 SSE 事件：{raw[:300]!r}")
        self.assertTrue(any(e.get("done") is True for e in events), events)
        self.assertFalse(any("line" in e for e in events),
                         f"历史 run 没有日志行，不该发 line 事件：{events}")

    def test_unknown_id_still_404(self):
        """journal 里也没有的 id，仍然是 404（不能把回落做成「永远 200」）。"""
        status, raw = _call("GET", "/api/runs/zzz_no_such_run")
        self.assertEqual(404, status, raw[:300])
        self.assertEqual("运行不存在", _json(raw).get("detail"))


class MemoryRunUnchangedTests(unittest.TestCase):
    """P2-2 的反向约束：内存里的 run 行为一点不能变。"""

    def test_detail_in_memory_uses_manager_lines(self):
        run = _FakeRun()
        with patch.object(A.runner.MANAGER, "get", lambda rid: run), \
                patch.object(A.runner.MANAGER, "lines",
                             lambda rid, since=0: [{"i": 7, "text": "hello", "t": 1.0}]):
            status, raw = _call("GET", "/api/runs/mem_run_1")
        self.assertEqual(200, status, raw[:300])
        body = _json(raw)
        self.assertEqual("mem_run_1", body["id"])
        self.assertEqual([{"i": 7, "text": "hello", "t": 1.0}], body["lines"])

    def test_stream_in_memory_finished_run_unchanged(self):
        run = _FakeRun(status="done")
        with patch.object(A.runner.MANAGER, "get", lambda rid: run), \
                patch.object(A.runner.MANAGER, "lines",
                             lambda rid, since=0: [{"i": 1, "text": "hello", "t": 1.0}]), \
                patch.object(A.runner.MANAGER, "subscribe", lambda rid: queue.Queue()), \
                patch.object(A.runner.MANAGER, "unsubscribe", lambda rid, q: None):
            status, raw = _call("GET", "/api/runs/mem_run_1/stream")
        self.assertEqual(200, status)
        events = _sse_events(raw)
        self.assertTrue(any("line" in e for e in events), events)
        self.assertTrue(any(e.get("done") is True for e in events), events)

    def test_stream_running_run_still_streams_then_end(self):
        """运行中：先报 running(done:false)，订阅队列收到 done 后正常结束（不挂住）。"""
        run = _FakeRun(status="running", exit_code=None)
        done_item = queue.Queue()
        done_item.put({"done": True, "status": "done", "exit_code": 0})
        with patch.object(A.runner.MANAGER, "get", lambda rid: run), \
                patch.object(A.runner.MANAGER, "lines", lambda rid, since=0: []), \
                patch.object(A.runner.MANAGER, "subscribe", lambda rid: done_item), \
                patch.object(A.runner.MANAGER, "unsubscribe", lambda rid, q: None):
            status, raw = _call("GET", "/api/runs/mem_run_1/stream")
        self.assertEqual(200, status)
        events = _sse_events(raw)
        self.assertEqual("running", events[0].get("status"))
        self.assertFalse(events[0].get("done"))
        self.assertTrue(events[-1].get("done"))


class IsolationGuardTests(unittest.TestCase):
    """测试自己不许碰生产 journal。"""

    def test_journal_is_not_production_file(self):
        prod = (WEBAPP / "data" / "runs.jsonl").resolve()
        self.assertNotEqual(str(prod), str(Path(config.RUNS_JSONL).resolve()),
                            "测试必须写临时 journal，不能读/写生产 data/runs.jsonl")


if __name__ == "__main__":
    unittest.main(verbosity=2)
