# -*- coding: utf-8 -*-
"""提示词反推 · 第一批整改验收（2026-10-23 审查 R1 / R2 / R5 / R6）。

与 tests/tk_reverse_hardening.test.py 的关系：那份覆盖 R1–R17 的纯函数与状态层，
这里只补它没有的四类：真·多进程并发写、analyze 并发竞态、200+非 JSON 重试、
_render_report 抗模型垃圾输入。两边都绿才算这一批过。


覆盖：
  R1  _save 的状态文件写入必须扛住多进程并发（旧版固定 status.tmp，实测 20% 失败）；
      写完后不留 .status-*.tmp 残片，status.json 仍可解析。
  R2  analyze() 的「查阶段 → 置 analyzing」必须原子：
      N 个线程同时请求，只能有一个成功，其余拿「正在处理中」。
  R5  _chat 对 429/5xx/网络超时按退避重试；400/401 不重试；200+非 JSON 也重试。
  R6  validate_ad_structure 容错解析（"S1" / "2.0s" / 越界 / 重复 idx），
      并保证 _render_report 不再因类型问题抛异常。

跑法：webapp/.venv/Scripts/python.exe tests/reverse_batch1_accept.test.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T

WEBAPP = str(Path(__file__).resolve().parents[1])

RUNNER = r'''
import sys
sys.path.insert(0, r"%s")
import tk_reverse as T
vid, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
bad = 0
for i in range(n):
    try:
        T._save(vid, stage_label="%%s-%%d" %% (tag, i))
    except Exception:
        bad += 1
print(bad)
sys.exit(0 if bad == 0 else 3)
''' % WEBAPP


def _isolate(testcase):
    """把 REVERSE_DIR 指到临时目录并清空内存状态。"""
    testcase.tmp = tempfile.TemporaryDirectory()
    testcase.root = Path(testcase.tmp.name) / "reverse"
    testcase.root.mkdir()
    testcase.patch = patch.object(T, "REVERSE_DIR", testcase.root)
    testcase.patch.start()
    with T._LOCK:
        T._STATE.clear()
        T._WORKERS.clear()
        T._WORK_START.clear()
        T._CANCEL.clear() if hasattr(T, "_CANCEL") else None


class StateWriteConcurrencyTests(unittest.TestCase):
    """R1：多进程同时写同一 vid 的状态文件。"""

    def setUp(self):
        _isolate(self)
        self.runner = Path(self.tmp.name) / "runner.py"
        self.runner.write_text(RUNNER, encoding="utf-8")
        (self.root / "555").mkdir()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_two_processes_write_same_task_without_error(self):
        env = dict(os.environ, WB_REVERSE_DIR=str(self.root))
        procs = [subprocess.Popen([sys.executable, str(self.runner), "555", tag, "400"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, text=True)
                 for tag in ("A", "B", "C")]
        results = []
        for p in procs:
            out, err = p.communicate(timeout=180)
            results.append((p.returncode, (out or "").strip(), (err or "").strip()[-300:]))

        for code, out, err in results:
            self.assertEqual(code, 0, f"子进程写入失败：{err}")
        # status.json 必须仍是合法 JSON，且两进程的写入没把文件写坏
        st = json.loads((self.root / "555" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["video_id"], "555")
        self.assertIn(st["stage_label"][:1], ("A", "B", "C"), st["stage_label"])

    def test_no_tmp_residue_after_writes(self):
        T._save("556", stage="ready", frames=3)
        leftovers = [p.name for p in (self.root / "556").glob("*.tmp")]
        self.assertEqual(leftovers, [], f"状态临时文件没有清理：{leftovers}")
        self.assertFalse((self.root / "556" / "status.tmp").exists(),
                         "旧版固定名 status.tmp 不应再出现")


class AnalyzeRaceTests(unittest.TestCase):
    """R2：并发 analyze 只能有一个成功。"""

    def setUp(self):
        _isolate(self)
        self.saved_bg = []
        self.patch_bg = patch.object(T, "_bg", lambda *a, **k: self.saved_bg.append(a))
        self.patch_bg.start()
        T._save("777", stage="ready", frames=12, ai_frames=8)
        (T.task_dir("777") / "manifest.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.patch_bg.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def test_six_concurrent_analyze_only_one_wins(self):
        ok, rejected = [], []
        barrier = threading.Barrier(6)

        def call(i):
            barrier.wait()
            try:
                ok.append(T.analyze("777", "stub", "ad"))
            except T.ReverseError as e:
                rejected.append(str(e))

        with patch.object(T, "pick_model", lambda name="": {"id": "stub", "url": "http://x", "apiKey": "k"}):
            threads = [threading.Thread(target=call, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

        self.assertEqual(len(ok), 1, f"应只允许 1 个请求通过，实际 {len(ok)}")
        self.assertEqual(len(rejected), 5, f"其余应被拒：{rejected}")
        self.assertTrue(all("正在处理中" in r for r in rejected), rejected)
        self.assertEqual(len(self.saved_bg), 1, "只能起一个后台线程")

    def test_analyze_requires_material(self):
        T._save("778", stage="ready", frames=0)
        with patch.object(T, "pick_model", lambda name="": {"id": "stub"}):
            with self.assertRaises(T.ReverseError) as ctx:
                T.analyze("778", "stub", "ad")
        self.assertIn("素材还没生成", str(ctx.exception))


class _FakeOpener:
    """按脚本依次抛异常或返回响应。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        item = self.script.pop(0) if self.script else self.script_default()
        if isinstance(item, BaseException):
            raise item
        return io.BytesIO(json.dumps(item).encode("utf-8"))

    @staticmethod
    def script_default():
        return {"choices": [{"message": {"content": "ok"}}]}


def _resp(text="ok"):
    return {"choices": [{"message": {"content": text}}]}


class ChatRetryTests(unittest.TestCase):
    """R5：可重试错误退避重试，不可重试错误立即失败。"""

    MODEL = {"id": "stub", "url": "http://127.0.0.1:1/v1", "apiKey": "k"}

    def _run(self, script, retries=2):
        fake = _FakeOpener(script)
        with patch.object(T.urllib.request, "build_opener", lambda *a, **k: fake), \
                patch.object(T.time, "sleep", lambda *_: None):
            return T._chat(self.MODEL, [{"type": "text", "text": "hi"}],
                           max_tokens=100, retries=retries), fake.calls

    def _http_error(self, code):
        return urllib.error.HTTPError("http://x", code, "err", {},
                                      io.BytesIO(b'{"error":"boom"}'))

    def test_429_then_success(self):
        text, calls = self._run([self._http_error(429), _resp("hello")])
        self.assertEqual(text, "hello")
        self.assertEqual(calls, 2, "429 必须重试")

    def test_503_exhausted_reports_code(self):
        with self.assertRaises(T.ReverseError) as ctx:
            self._run([self._http_error(503) for _ in range(3)])
        self.assertIn("503", str(ctx.exception))

    def test_400_is_not_retried(self):
        fake = _FakeOpener([self._http_error(400), _resp("should-not-reach")])
        with patch.object(T.urllib.request, "build_opener", lambda *a, **k: fake), \
                patch.object(T.time, "sleep", lambda *_: None):
            with self.assertRaises(T.ReverseError) as ctx:
                T._chat(self.MODEL, [{"type": "text", "text": "hi"}], max_tokens=100)
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(fake.calls, 1, "400 是参数错误，不该重试")

    def test_timeout_retried_then_raises(self):
        import socket
        with self.assertRaises(T.ReverseError) as ctx:
            self._run([socket.timeout("timed out") for _ in range(3)])
        self.assertIn("已重试", str(ctx.exception))

    def test_non_json_200_is_retried(self):
        fake = _FakeOpener([_resp("ignored")])

        def opener_open(req, timeout=None):
            fake.calls += 1
            if fake.calls == 1:
                return io.BytesIO(b"<html>gateway error</html>")
            return io.BytesIO(json.dumps(_resp("recovered")).encode("utf-8"))

        fake.open = opener_open
        with patch.object(T.urllib.request, "build_opener", lambda *a, **k: fake), \
                patch.object(T.time, "sleep", lambda *_: None):
            text = T._chat(self.MODEL, [{"type": "text", "text": "hi"}], max_tokens=100)
        self.assertEqual(text, "recovered")
        self.assertEqual(fake.calls, 2, "200+非 JSON 也应重试")

    def test_empty_body_doubles_budget(self):
        budgets = []
        fake = _FakeOpener([{"choices": [{"message": {"content": ""}}]}, _resp("second")])
        orig_open = fake.open

        def open_spy(req, timeout=None):
            budgets.append(json.loads(req.data.decode("utf-8"))["max_tokens"])
            return orig_open(req, timeout)

        fake.open = open_spy
        with patch.object(T.urllib.request, "build_opener", lambda *a, **k: fake), \
                patch.object(T.time, "sleep", lambda *_: None):
            text = T._chat(self.MODEL, [{"type": "text", "text": "hi"}], max_tokens=100)
        self.assertEqual(text, "second")
        self.assertEqual(budgets, [100, 200], "空正文后应加倍预算")


class AdStructureTests(unittest.TestCase):
    """R6：ad 分镜容错解析 + 报告不再炸。"""

    def _shots(self, **over):
        base = {"idx": 1, "name": "开场", "t_start": 0, "t_end": 1.5,
                "shot_size": "中景", "action": "走过来", "info": "注意"}
        return dict(base, **over)

    def test_parses_human_written_numbers(self):
        val = T.validate_ad_structure({"shots": [
            self._shots(idx="S1", t_start="0s", t_end="1.5s"),
            self._shots(idx="第2段", t_start="1.5s", t_end="3.0s"),
        ]}, 3.2)
        self.assertEqual([s["idx"] for s in val["shots"]], [1, 2])
        self.assertEqual([(s["t_start"], s["t_end"]) for s in val["shots"]],
                         [(0.0, 1.5), (1.5, 3.0)])

    def test_duplicate_idx_is_renumbered(self):
        val = T.validate_ad_structure({"shots": [
            self._shots(idx=1), self._shots(idx=1), self._shots(idx=1)]}, 5.0)
        self.assertEqual([s["idx"] for s in val["shots"]], [1, 2, 3])

    def test_out_of_range_is_clamped_not_rejected(self):
        val = T.validate_ad_structure({"shots": [
            self._shots(t_start=0, t_end=99), self._shots(idx=2, t_start=99, t_end=120)]}, 4.0)
        self.assertLessEqual(val["shots"][0]["t_end"], 4.0)
        self.assertLess(val["shots"][1]["t_start"], 4.0)
        self.assertLessEqual(val["shots"][1]["t_end"], 4.0)

    def test_reversed_times_are_fixed(self):
        val = T.validate_ad_structure({"shots": [self._shots(t_start=3, t_end=1)]}, 10.0)
        shot = val["shots"][0]
        self.assertLess(shot["t_start"], shot["t_end"])

    def test_bad_payloads_raise_readable_errors(self):
        for payload, needle in [({}, "没有返回分镜"), ({"shots": []}, "没有返回分镜"),
                                ({"shots": [1, 2]}, "不是对象"),
                                ({"shots": [self._shots()] * 65}, "数量异常")]:
            with self.assertRaises(T.ReverseError) as ctx:
                T.validate_ad_structure(payload, 5.0)
            self.assertIn(needle, str(ctx.exception))

    def test_render_report_survives_model_style_garbage(self):
        """旧版这里会 int("S1") 抛 ValueError，导致 93% 处整单失败。"""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "reverse"
        root.mkdir()
        with patch.object(T, "REVERSE_DIR", root):
            with T._LOCK:
                T._STATE.clear()
            T._save("888", stage="done", has_report=True)
            ai = {
                "shots": [self._shots(idx="S1", t_end="1.5s"), self._shots(idx=2, t_start=None)],
                "prompts": [{"idx": "S1", "text": "竖屏 9:16 test"}],
                "ratio": {"hook": "30", "selling": 30, "proof": 20, "ending": 20},
                "why": [], "narration": {}, "compliance": [], "template": [],
            }
            html = T._render_report("888", {"model": "stub"}, {"author": "a"}, {"duration": 3.2}, ai)
        tmp.cleanup()
        self.assertIn("竖屏 9:16 test", html)
        self.assertIn("S1", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
