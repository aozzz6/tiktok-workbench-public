# -*- coding: utf-8 -*-
"""post_update 的端口解析 + receipts 的 precheck 文案（2026-11-02，T-04）。

背景：8787 被收掉之后，`post_update.py` 里写死的
`http://127.0.0.1:8787/api/overview?refresh=true` 会永远连不上 →
`check_api` 捕获异常后恒返回 `skipped`，日更的 API 校验层**静默失效**
（回执看着是 INFO，其实这一层根本没跑）。

现在改成与 `daily_batch.py` / `webapp/_launch.py` 同序的端口优先链：
**8789 → 8791 → 8792 → 8793 → 8787**，逐个探测 `http://127.0.0.1:<port>/api/health`，
取第一个像工作台的端口；全都不通回退 8787（app.py 自带默认端口），
`skipped` 语义保持不变。

本测试**不联网、不起服务、不碰 8789**：
  · 端口链用注入的假 probe；
  · `check_api` 的 HTTP 出口用 `urllib.request.urlopen` 打桩。

跑法：webapp/.venv/Scripts/python.exe tests/post_update_port.test.py
"""
import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
WEBAPP = TESTS_DIR.parent
PIPE = WEBAPP.parent / "pipeline"

sys.path.insert(0, str(PIPE / "scripts"))
sys.path.insert(0, str(PIPE))

import post_update as pu  # noqa: E402
import receipts  # noqa: E402

EXPECTED_CHAIN = (8789, 8791, 8792, 8793, 8787)


class _Resp:
    """最小化的 urlopen 返回值替身。"""

    def __init__(self, payload):
        self._payload = payload

    def read(self, *a):
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class PortChainTests(unittest.TestCase):
    """端口优先链：可注入 probe，全不通回退 8787。"""

    def test_chain_matches_daily_batch(self):
        self.assertEqual(EXPECTED_CHAIN, tuple(pu.PORT_CANDIDATES))

    def test_only_8789_healthy_picks_8789(self):
        self.assertEqual(8789, pu.resolve_workbench_port(lambda port: port == 8789))

    def test_only_8787_healthy_picks_8787(self):
        self.assertEqual(8787, pu.resolve_workbench_port(lambda port: port == 8787))

    def test_nothing_healthy_falls_back_to_8787(self):
        self.assertEqual(8787, pu.resolve_workbench_port(lambda port: False))

    def test_probes_in_chain_order_and_stops_at_first_hit(self):
        seen = []

        def probe(port):
            seen.append(port)
            return port == 8792

        self.assertEqual(8792, pu.resolve_workbench_port(probe))
        self.assertEqual([8789, 8791, 8792], seen)

    def test_probe_exception_does_not_break_the_chain(self):
        def probe(port):
            if port == 8789:
                raise OSError("connection reset")
            return port == 8791

        self.assertEqual(8791, pu.resolve_workbench_port(probe))

    def test_workbench_base_uses_resolved_port(self):
        self.assertEqual("http://127.0.0.1:8789",
                         pu.workbench_base(lambda port: port == 8789))
        self.assertEqual("http://127.0.0.1:8787", pu.workbench_base(lambda port: False))


class CheckApiUsesResolvedBaseTests(unittest.TestCase):
    """DoD 2：check_api 真的用解析出来的 base，不再用写死的 8787 URL。"""

    def _check(self, base, payload=None, exc=None):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"] = url
            if exc is not None:
                raise exc
            return _Resp(payload if payload is not None else {"accounts": []})

        with patch.object(pu, "workbench_base", return_value=base), \
                patch.object(urllib.request, "urlopen", fake_urlopen):
            return pu.check_api(cfg={"accounts": []}), seen

    def test_overview_url_follows_resolved_port(self):
        result, seen = self._check("http://127.0.0.1:8789")
        self.assertEqual("http://127.0.0.1:8789/api/overview?refresh=true", seen.get("url"))
        self.assertEqual("ok", result["status"])

    def test_skipped_semantics_preserved(self):
        """服务不可达仍是 skipped，不判失败（回执不能因为服务没开就变红）。"""
        result, _ = self._check("http://127.0.0.1:8789", exc=OSError("拒绝连接"))
        self.assertEqual("skipped", result["status"])
        self.assertIn("reason", result)

    def test_source_has_no_hardcoded_overview_url(self):
        src = Path(pu.__file__).read_text(encoding="utf-8")
        self.assertNotIn("8787/api/overview", src)
        self.assertNotIn("WORKBENCH_URL", src)
        self.assertIn("/api/health", src, "端口探测必须探 /api/health")


class ReceiptsPrecheckTests(unittest.TestCase):
    """DoD 3：precheck 提示指向 8789，并说明直接跑 app.py 时默认 8787。"""

    def setUp(self):
        self.text = receipts.RECOVERY_ACTION["precheck"]

    def test_mentions_8789(self):
        self.assertIn("8789", self.text)

    def test_explains_app_py_default_8787(self):
        self.assertIn("8787", self.text)
        self.assertIn("app.py", self.text)

    def test_drops_old_app_py_colon_8787_wording(self):
        self.assertNotIn("app.py:8787", self.text)

    def test_still_points_at_daily_batch(self):
        self.assertIn("daily_batch.py", self.text)


class _HealthResp:
    """像真实响应一样支持 `read(n)`：不给 n 才返回全部（截断必须能被测出来）。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, n=None):
        return self._payload if n is None else self._payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.urls = []

    def open(self, url, timeout=None):
        self.urls.append(url)
        return _HealthResp(self._payload)


class HealthProbeBigResponseTests(unittest.TestCase):
    """`/api/health` 响应超过 8KB 时不能把真工作台判成「不是工作台」（T-04 评审）。

    旧代码 `resp.read(8192)`：`diagnostics.ledger_files` 一多（台账文件多 / 路径长），
    JSON 被截断 → `json.loads` 抛错 → 判 False → 整条端口链失败 → 回退 8787 →
    `check_api` 又静默 `skipped`，正是本任务要修的那类问题换个方式复发。
    """

    def test_health_payload_over_8kb_is_still_accepted(self):
        payload = json.dumps({
            "ok": True,
            "ts": 1730000000.0,
            "diagnostics": {
                "pipeline_dir": str(Path(__file__).resolve().parents[2] / "pipeline"),
                "ledger_files": [f"对标记录-account{i:03d}.xlsx" for i in range(300)],
            },
        }, ensure_ascii=False).encode("utf-8")

        # 先证明这条响应确实超过 8KB，而且截到 8192 就真的解析不了
        # —— 否则这条用例只是「碰巧通过」，挡不住回退。
        self.assertGreater(len(payload), 8192)
        with self.assertRaises(ValueError):
            json.loads(payload[:8192].decode("utf-8", "replace"))

        opener = _FakeOpener(payload)
        with patch.object(urllib.request, "build_opener", return_value=opener):
            self.assertTrue(pu._looks_like_workbench(8789),
                            ">8KB 的合法 health 响应必须仍被判为工作台（不能被截断）")
        self.assertEqual(["http://127.0.0.1:8789/api/health"], opener.urls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
