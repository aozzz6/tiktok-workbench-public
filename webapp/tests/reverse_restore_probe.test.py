# -*- coding: utf-8 -*-
"""提示词反推 · 2026-10-23 验收修订回归（A1 / A2 / A5 / A6）。

对应用户退回的验收问题：
  A1  WebM/MKV 时长只在容器层 → _probe 必须回退 format/duration，不能读成 0；
      原片还原的时长闸门要区分「读不到时长」和「超过 60 秒」。
  A2  文本改名 .mp4 必须在建任务前被拒，且不留任务目录。
  A5  极短镜头只有 1 张不同源帧时，返回 1 张 + references_note，不复制重复帧凑数。
  A6  补导边界帧后 restore_frames 与图册一致（计数在 merge 之后写）。

全部用真实 ffmpeg 现场造样本，隔离到临时目录，不碰线上 reverse 目录，不调用模型。
跑法：webapp/.venv/Scripts/python.exe tests/reverse_restore_probe.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T
import reverse_restore as R

HAVE_FFMPEG = bool(T.FFMPEG and T.FFPROBE)


def _make(path: Path, args: list[str]) -> bool:
    rc, _ = T._run([T.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=160x120:rate=10:duration=1"] + args + [str(path), "-y"], timeout=180)
    return rc == 0 and path.is_file() and path.stat().st_size > 0


class ProbeDurationTests(unittest.TestCase):
    """A1：时长取值必须能从容器层回退。"""

    def setUp(self):
        if not HAVE_FFMPEG:
            self.skipTest("没有 ffmpeg/ffprobe")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_webm_duration_comes_from_container(self):
        webm = self.dir / "a.webm"
        self.assertTrue(_make(webm, ["-c:v", "libvpx-vp9", "-b:v", "200k"]), "造不出 VP9 WebM")
        info = T._probe(webm)
        # 这个样本的时长只写在 format 层；旧实现返回 0.0，被当成超限
        self.assertGreater(info["duration"], 0.5, info)
        self.assertAlmostEqual(info["duration"], 1.0, delta=0.2)
        self.assertEqual(info["duration_source"], "format", info)
        self.assertTrue(info["has_video"], info)

    def test_mp4_duration_stays_on_stream(self):
        mp4 = self.dir / "a.mp4"
        self.assertTrue(_make(mp4, ["-c:v", "libx264", "-pix_fmt", "yuv420p"]), "造不出 H.264 MP4")
        info = T._probe(mp4)
        self.assertAlmostEqual(info["duration"], 1.0, delta=0.2)
        self.assertEqual(info["duration_source"], "stream", info)
        self.assertTrue(info["has_video"], info)

    def test_text_file_has_no_video(self):
        fake = self.dir / "fake.mp4"
        fake.write_text("this is not a video", encoding="utf-8")
        info = T._probe(fake)
        self.assertFalse(info["has_video"], info)
        self.assertEqual(info["duration"], 0)


class ImportGuardTests(unittest.TestCase):
    """A2：先验证真实视频流，再建任务。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reverse"
        self.root.mkdir()
        self.patch_dir = patch.object(T, "REVERSE_DIR", self.root)
        self.patch_dir.start()
        self.patch_bg = patch.object(T, "_bg", lambda *a, **k: None)   # 不启后台线程
        self.patch_bg.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()

    def tearDown(self):
        self.patch_bg.stop()
        self.patch_dir.stop()
        self.tmp.cleanup()

    def _task_dirs(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.isdigit())

    def test_text_renamed_mp4_is_rejected_before_task_created(self):
        fake = self.root / "upload.mp4"
        fake.write_bytes(b"definitely not a video" * 100)
        with self.assertRaises(T.ReverseError) as ctx:
            T.import_local(fake, "clip.mp4")
        self.assertIn("没有视频流", str(ctx.exception))
        self.assertEqual(self._task_dirs(), [], "拒收时不能留下任务目录")

    def test_empty_file_is_rejected(self):
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        with self.assertRaises(T.ReverseError):
            T.import_local(empty, "empty.mp4")
        self.assertEqual(self._task_dirs(), [])

    @unittest.skipUnless(HAVE_FFMPEG, "没有 ffmpeg")
    def test_real_mp4_is_accepted_and_probe_is_stored(self):
        good = self.root / "good.mp4"
        self.assertTrue(_make(good, ["-c:v", "libx264", "-pix_fmt", "yuv420p"]))
        out = T.import_local(good, "good.mp4")
        dirs = self._task_dirs()
        self.assertEqual(len(dirs), 1, "通过校验应建 1 个任务")
        task = self.root / dirs[0]
        self.assertTrue((task / "src.mp4").is_file())
        self.assertEqual(out["stage"], "preparing")
        # 导入即带 probe，卡片不用等第二次探测就能显示分辨率/时长
        self.assertGreater(out["probe"]["duration"], 0.5)
        self.assertEqual(out["meta"]["origin"], "local")
        self.assertEqual(out["steps"][0]["label"], "读取原片")


class DurationGateTests(unittest.TestCase):
    """A1：读不到时长 与 超过 60 秒，必须给不同提示。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reverse"
        self.root.mkdir()
        self.patch_dir = patch.object(T, "REVERSE_DIR", self.root)
        self.patch_dir.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()

    def tearDown(self):
        self.patch_dir.stop()
        self.tmp.cleanup()

    def _seed(self, vid: str, duration: float):
        d = T.task_dir(vid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps({"meta": {}, "probe": {"duration": duration}, "scenes": []}), encoding="utf-8")
        (d / "src.mp4").write_bytes(b"stub")

    def test_zero_duration_says_cannot_read_not_over_limit(self):
        self._seed("901", 0)
        with self.assertRaises(T.ReverseError) as ctx:
            R.run("901", {"id": "stub"})
        msg = str(ctx.exception)
        self.assertIn("读不到", msg)
        self.assertNotIn("超过原片还原上限", msg)

    def test_too_long_reports_actual_duration(self):
        self._seed("902", 90.0)
        with self.assertRaises(T.ReverseError) as ctx:
            R.run("902", {"id": "stub"})
        msg = str(ctx.exception)
        self.assertIn("90.0", msg)
        self.assertIn("超过原片还原上限", msg)


class ReferenceTests(unittest.TestCase):
    """A5：短镜头参考图例外必须写明，不复制重复帧。"""

    @staticmethod
    def _gallery(step: float, count: int):
        return [{"id": f"F{i:03d}", "source_index": i, "t": round(i * step, 3),
                 "path": f"images/f_{i:03d}.jpg", "analysis_path": f"analysis/f_{i:03d}.jpg"}
                for i in range(count)]

    def test_short_shot_returns_one_ref_with_note(self):
        gallery = self._gallery(0.5, 6)              # 2fps 抽帧
        shot = {"t_start": 0.0, "t_end": 0.4}
        refs = R.shot_references(shot, gallery)
        self.assertEqual(len(refs), 1)
        self.assertEqual(len({r["id"] for r in refs}), 1, "不能复制同一帧充数")
        self.assertIn("只有 1 张不同源帧", shot["references_note"])

    def test_normal_shot_gets_three_refs_without_note(self):
        gallery = self._gallery(0.5, 6)
        shot = {"t_start": 0.0, "t_end": 2.0}
        refs = R.shot_references(shot, gallery)
        self.assertEqual(len(refs), 3)
        self.assertEqual(len({r["id"] for r in refs}), 3)
        self.assertEqual(shot["references_note"], "")

    def test_shot_shorter_than_frame_gap_still_one_ref(self):
        gallery = self._gallery(0.5, 4)
        shot = {"t_start": 0.62, "t_end": 0.71}      # 完全落在两帧之间
        refs = R.shot_references(shot, gallery)
        self.assertEqual(len(refs), 1)
        self.assertTrue(shot["references_note"])


class CountConsistencyTests(unittest.TestCase):
    """A6：补导边界帧后计数与图册一致。"""

    def test_merge_extra_frames_keeps_count_in_sync(self):
        base = [{"id": "F001", "source_index": 0, "t": 0.0},
                {"id": "F002", "source_index": 10, "t": 5.0}]
        extra = [{"id": "X001", "source_index": 4, "t": 2.0},
                 {"id": "X002", "source_index": 14, "t": 7.0}]
        merged = R.merge_extra_frames(base, extra)
        self.assertEqual(len(merged), 4)
        self.assertEqual([f["t"] for f in merged], [0.0, 2.0, 5.0, 7.0], "必须按源时间重排")
        self.assertIs(R.merge_extra_frames(merged, []), merged)

    def test_run_writes_frames_count_after_merge(self):
        src = (Path(__file__).resolve().parents[1] / "reverse_restore.py").read_text(encoding="utf-8")
        merge_at = src.index("merge_extra_frames(gallery, export_frames")
        save_at = src.index("T._save(vid, restore_frames=len(gallery)")
        self.assertLess(merge_at, save_at, "restore_frames 必须在合并补导帧之后写")


if __name__ == "__main__":
    unittest.main(verbosity=2)
