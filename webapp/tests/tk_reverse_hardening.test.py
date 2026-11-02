# -*- coding: utf-8 -*-
"""2026-10-23 审查整改（R1–R17）的回归测试。

只覆盖可隔离的纯函数与状态层：不调真实模型、不碰线上 reverse 目录
（REVERSE_DIR 全部改到 temp，_bg 显式桩掉）。
跑法：webapp/.venv/Scripts/python.exe tests/tk_reverse_hardening.test.py
"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reverse_restore as R
import tk_reverse as T


class _FakeOpener:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return io.BytesIO(json.dumps(action).encode("utf-8"))


def _http_error(code):
    return urllib.error.HTTPError("http://model.test", code, "err", {},
                                  io.BytesIO(b'{"error":"x"}'))


class ChatRetryTests(unittest.TestCase):
    """R5：429 可重试、401 不重试、网络错误重试后给可读错误。"""

    MODEL = {"id": "m", "url": "http://model.test", "apiKey": "k"}

    def test_429_is_retried_then_succeeds(self):
        opener = _FakeOpener([_http_error(429),
                              {"choices": [{"message": {"content": "ok"}}]}])
        with patch.object(T.urllib.request, "build_opener", return_value=opener), \
                patch.object(T.time, "sleep"):
            got = T._chat(self.MODEL, [{"type": "text", "text": "p"}])
        self.assertEqual(got, "ok")
        self.assertEqual(opener.calls, 2)

    def test_401_is_not_retried(self):
        opener = _FakeOpener([_http_error(401)])
        with patch.object(T.urllib.request, "build_opener", return_value=opener), \
                patch.object(T.time, "sleep"):
            with self.assertRaises(T.ReverseError):
                T._chat(self.MODEL, [{"type": "text", "text": "p"}])
        self.assertEqual(opener.calls, 1)

    def test_network_error_retries_then_readable_error(self):
        opener = _FakeOpener([urllib.error.URLError("down")] * 3)
        with patch.object(T.urllib.request, "build_opener", return_value=opener), \
                patch.object(T.time, "sleep"):
            with self.assertRaises(T.ReverseError) as ctx:
                T._chat(self.MODEL, [])
        self.assertIn("已重试 2 次", str(ctx.exception))
        self.assertEqual(opener.calls, 3)


class AdStructureTests(unittest.TestCase):
    """R6：模型写 "S1"/"2.0s" 时不能在导出首帧才炸。"""

    def test_tolerates_s1_and_seconds_suffix(self):
        value = {"shots": [
            {"idx": "S1", "t_start": "0", "t_end": "2.0s"},
            {"idx": "S2", "t_start": "1.5", "t_end": 3},
        ]}
        out = T.validate_ad_structure(value, 10)
        self.assertEqual([s["idx"] for s in out["shots"]], [1, 2])
        self.assertEqual(out["shots"][0]["t_end"], 2.0)
        self.assertEqual(out["shots"][1]["t_start"], 1.5)

    def test_duplicate_idx_is_renumbered(self):
        out = T.validate_ad_structure({"shots": [{"idx": 1}, {"idx": 1}]}, 5)
        self.assertEqual([s["idx"] for s in out["shots"]], [1, 2])

    def test_time_is_clamped_to_duration(self):
        out = T.validate_ad_structure(
            {"shots": [{"idx": 1, "t_start": 9, "t_end": 99}]}, 10)
        shot = out["shots"][0]
        self.assertLessEqual(shot["t_end"], 10)
        self.assertGreater(shot["t_end"], shot["t_start"])

    def test_empty_shots_rejected(self):
        with self.assertRaises(T.ReverseError):
            T.validate_ad_structure({"shots": []}, 5)

    def test_numeric_helpers(self):
        self.assertEqual(T._as_int("S12"), 12)
        self.assertEqual(T._as_int(None, 7), 7)
        self.assertEqual(T._as_float("2.5s"), 2.5)
        self.assertEqual(T._as_float("1.5–2.0s"), 1.5)


class ChooseIndicesTests(unittest.TestCase):
    """R11：切点帧优先，不能被均匀抽样挤掉。"""

    def test_all_cut_frames_survive_under_limit(self):
        times = [round(i * 0.5, 3) for i in range(120)]      # 60s @2fps
        scenes = [float(i * 3) for i in range(20)]           # 61 个 critical 帧
        gallery, ai = R.choose_indices(times, 60.0, scenes)
        self.assertLessEqual(len(gallery), R.MAX_GALLERY_FRAMES)
        self.assertGreaterEqual(len(gallery), 60)
        self.assertIn(0, gallery)
        self.assertTrue(set(ai) <= set(gallery))
        self.assertLessEqual(len(ai), R.MAX_AI_FRAMES)

    def test_huge_cut_set_still_capped(self):
        times = [round(i * 0.15, 3) for i in range(400)]
        scenes = [i * 0.3 for i in range(200)]
        gallery, ai = R.choose_indices(times, 60.0, scenes)
        self.assertEqual(len(gallery), R.MAX_GALLERY_FRAMES)
        self.assertLessEqual(len(ai), R.MAX_AI_FRAMES)


class ReportModeCacheTests(unittest.TestCase):
    """R16：_mode 按 (mtime, size) 缓存，文件一变就失效。"""

    def test_cache_hits_and_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "report.json").write_text('{"_mode":"restore"}', encoding="utf-8")
            with patch.object(T, "_REPORT_MODE_CACHE", {}):
                self.assertEqual(T._report_mode(d, "1", "ad"), "restore")
                self.assertIn("1", T._REPORT_MODE_CACHE)
                (d / "report.json").write_text('{"_mode":"ad","x":1}', encoding="utf-8")
                self.assertEqual(T._report_mode(d, "1", "restore"), "ad")


class _TempReverse:
    """把 REVERSE_DIR / 状态层换到 temp 目录的夹具。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reverse"
        self.root.mkdir()
        self.patches = [
            patch.object(T, "REVERSE_DIR", self.root),
            patch.object(T, "_STATE", {}),
            patch.object(T, "_WORKERS", {}),
            patch.object(T, "_CANCEL", {}),
            patch.object(T, "_REPORT_MODE_CACHE", {}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class SaveTests(_TempReverse, unittest.TestCase):
    """R1：随机临时名写盘，成功后不留 .tmp。"""

    def test_save_leaves_no_tmp_and_roundtrips(self):
        vid = "123456789012345"
        T._save(vid, stage="ready", frames=3)
        d = self.root / vid
        self.assertTrue((d / "status.json").is_file())
        self.assertEqual(list(d.glob("*.tmp")), [])
        self.assertEqual(list(d.glob(".status-*")), [])
        st = json.loads((d / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["frames"], 3)


class CreateReuseTests(_TempReverse, unittest.TestCase):
    """R13：命中素材已存在时必须带 reused/notice，且不起后台线程。"""

    def test_reuse_returns_notice_and_does_not_start_worker(self):
        vid = "123456789012345"
        T._dir(vid).mkdir(parents=True)
        T._save(vid, stage="ready", frames=5, url="", meta={"author": "a"})
        with patch.object(T, "_bg", side_effect=AssertionError("不应起线程")):
            out = T.create(f"https://www.tiktok.com/@a/video/{vid}")
        self.assertTrue(out.get("reused"))
        self.assertIn("素材已存在", out.get("notice") or "")


class CancelTests(_TempReverse, unittest.TestCase):
    """R3：没有在跑的任务时 cancel 给可读错误。"""

    def test_cancel_without_worker_is_readable_error(self):
        with self.assertRaises(T.ReverseError) as ctx:
            T.cancel("123456789012345")
        self.assertIn("没有在跑", str(ctx.exception))


class DownloadGuardTests(_TempReverse, unittest.TestCase):
    """R9：fresh 下载失败时旧原片必须还在。"""

    def test_failed_fresh_download_keeps_old_src(self):
        vid = "123456789012345"
        d = T._dir(vid)
        d.mkdir(parents=True)
        old = d / "src.mp4"
        old.write_bytes(b"x" * 20_000)
        with patch.object(T, "_run", return_value=(1, "network down")), \
                patch.object(T, "_log"):
            with self.assertRaises(T.ReverseError):
                T._download(vid, "https://example.invalid/v", fresh=True)
        self.assertTrue(old.is_file())
        self.assertGreater(old.stat().st_size, 10_000)


class StructureValidationTests(unittest.TestCase):
    """R7 附带：clamp 后不能产出零长度镜头（否则 97% 导出参考片段才炸）。"""

    def _shot(self, **kw):
        base = {"idx": 1, "name": "s", "t_start": 0, "t_end": 1,
                "initial_state": "a", "action_steps": ["x"],
                "ending_state": "b", "camera_motion": "fixed"}
        base.update(kw)
        return base

    def test_zero_length_after_clamp_rejected(self):
        value = {"shots": [self._shot(t_start=0, t_end=10),
                           self._shot(idx=2, t_start=10.1, t_end=10.12)]}
        with self.assertRaises(T.ReverseError):
            R.validate_structure(value, 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
