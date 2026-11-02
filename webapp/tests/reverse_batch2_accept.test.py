# -*- coding: utf-8 -*-
"""第二批验收（2026-10-23）：R3 取消 / R9 重建保原片 / R10 删除闸门。

真机部分（浏览器 + 8787）见 `_rv_batch2_20261023/accept.js`，这里只锁后端契约：
1. R9 —— 重建时下载失败，旧 src.mp4 必须还在（旧实现先 unlink 再下载，失败即丢原片）。
2. R3 —— 取消后落 `stopped` 而不是 `failed`；没在跑时取消要报错；取消信号在批次边界抛。
3. R10 —— 有后台线程时（即使 stage 已写成 ready）也拒删。

跑法：webapp/.venv/Scripts/python.exe tests/reverse_batch2_accept.test.py
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rvb2_")) / "reverse"
        self.tmp.mkdir(parents=True)
        self.patch_dir = patch.object(T, "REVERSE_DIR", self.tmp)
        self.patch_dir.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()
            T._WORK_START.clear()
            T._CANCEL.clear()

    def tearDown(self):
        self.patch_dir.stop()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()
            T._WORK_START.clear()
            T._CANCEL.clear()

    def _task(self, vid="123456789012345678", *, origin="link", url="https://www.tiktok.com/@a/video/123456789012345678", frames=12):
        d = T._dir(vid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "src.mp4").write_bytes(b"ORIGINAL-SOURCE" * 100)
        (d / "manifest.json").write_text(json.dumps({"frames": [], "probe": {"duration": 5.0}}), encoding="utf-8")
        T._save(vid, stage="ready", stage_label="素材就绪", progress=100, busy="",
                url=url, meta={"origin": origin, "author": "t"}, frames=frames,
                ai_frames=4, shots=0, has_report=False)
        return vid


class RebuildKeepsSourceTests(Base):
    def test_download_failure_keeps_src(self):
        vid = self._task()
        src = T._dir(vid) / "src.mp4"
        before = src.read_bytes()

        started = {}
        self.patch_bg = patch.object(T, "_bg", lambda *a, **k: started.setdefault("args", a))
        with self.patch_bg, patch.object(T, "_yt_json", side_effect=RuntimeError("network down")):
            T.rebuild(vid)
        self.assertIn("args", started, "rebuild 应起后台 prepare")
        self.assertTrue(src.is_file(), "重建只清派生文件，原片必须留到新片下载成功")
        self.assertEqual(before, src.read_bytes(), "原片内容不能被改动")
        st = T._load(vid)
        self.assertEqual(st["frames"], 0, "派生物计数应清零等重算")
        self.assertFalse(st.get("has_report"), "旧报告标记应清掉")

        # 真正跑一次 prepare：下载失败 → 任务 failed，但原片仍在
        def boom(*a, **k):
            raise T.ReverseError("下载失败：测试桩")

        with patch.object(T, "_download", boom):
            with self.assertRaises(T.ReverseError):
                T._prepare(vid, "https://www.tiktok.com/@a/video/123456789012345678", {"author": "t"})
        self.assertTrue(src.is_file(), "下载失败后原片不应消失（旧实现就是这里丢的）")
        self.assertEqual(before, src.read_bytes())

    def test_local_rebuild_needs_no_url(self):
        vid = self._task(origin="local", url="")
        with patch.object(T, "_bg", lambda *a, **k: None):
            out = T.rebuild(vid)
        self.assertEqual("preparing", out["stage"])
        self.assertTrue((T._dir(vid) / "src.mp4").is_file())
        self.assertIsNotNone(out.get("frames"))


class CancelTests(Base):
    def test_cancel_marks_stopped_not_failed(self):
        vid = self._task()
        holder = {}

        def slow(vid_, *a, cancel=None, **k):
            holder["ev"] = cancel
            while not cancel.is_set():
                cancel.wait(0.02)
            T._check_cancel(cancel)          # 批次边界检查点

        T._bg(vid, "prepare", slow)
        for _ in range(200):
            if holder.get("ev"):
                break
            threading.Event().wait(0.01)
        T.cancel(vid)
        for _ in range(300):
            if T._load(vid)["stage"] == "stopped":
                break
            threading.Event().wait(0.01)
        st = T._load(vid)
        self.assertEqual("stopped", st["stage"], st)
        self.assertEqual("已取消", st["stage_label"])
        self.assertEqual("", st["error"], "用户主动取消不该留错误文案")
        with T._LOCK:
            self.assertNotIn(vid, T._WORKERS)
            self.assertNotIn(vid, T._CANCEL)

    def test_cancel_when_idle_raises(self):
        vid = self._task()
        with self.assertRaises(T.ReverseError) as ctx:
            T.cancel(vid)
        self.assertIn("没有在跑", str(ctx.exception))

    def test_check_cancel_raises_readable(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(T.ReverseError) as ctx:
            T._check_cancel(ev)
        self.assertIn("已取消", str(ctx.exception))


class RemoveGateTests(Base):
    def test_remove_refused_while_worker_alive(self):
        vid = self._task()
        blocker = threading.Event()
        T._bg(vid, "prepare", lambda v, *a, cancel=None, **k: blocker.wait(3))
        try:
            # stage 故意写成 ready：旧实现只看 stage 就会放行，然后把正在写的目录删掉
            T._save(vid, stage="ready", stage_label="素材就绪")
            with self.assertRaises(T.ReverseError) as ctx:
                T.remove(vid)
            self.assertIn("仍在运行", str(ctx.exception))
            self.assertTrue(T._dir(vid).is_dir(), "拒删时目录必须还在")
        finally:
            blocker.set()
            for _ in range(300):
                with T._LOCK:
                    if vid not in T._WORKERS:
                        break
                threading.Event().wait(0.01)

    def test_remove_after_stop_cleans_everything(self):
        vid = self._task()
        out = T.remove(vid)
        self.assertTrue(out.get("removed"))
        self.assertFalse(T._dir(vid).exists())
        with T._LOCK:
            self.assertNotIn(vid, T._STATE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
