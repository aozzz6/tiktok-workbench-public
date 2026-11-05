# -*- coding: utf-8 -*-
"""原片还原 · 端到端桩测试（2026-10-23 验收修订，免费跑）

为什么用桩模型：这一步要验证的是「文件流 / 时间轴 / 计数 / 打包」这些确定性逻辑，
不是模型语义质量。桩掉 _chat_json 后可以在 temp 目录里真跑一遍完整 run()：
  · 真 WebM（VP9，时长只在容器层）→ import_local 真实导入路径（A1 + A2）
  · 真 ffprobe 源帧时间 → 真抽图 → 真拆镜校验 → 真合并补导帧 → 真打包 zip
断言：
  A6  restore_frames == 报告图册张数 == ZIP 内 images 张数
  A5  极短镜头 1 张参考图必须带 references_note
  A3  bundle 解压后 report.html 相对依赖全部可解析（离线可看）
  A1  WebM 的 probe.duration > 0 且 duration_source == 'format'

跑法：webapp/.venv/Scripts/python.exe tests/reverse_restore_e2e.test.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T
import reverse_restore as R

DURATION = 3.2


def _make_webm(path: Path) -> bool:
    rc, _ = T._run([T.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=320x240:rate=10:duration={DURATION}",
                    "-c:v", "libvpx-vp9", "-b:v", "300k", str(path), "-y"], timeout=240)
    return rc == 0 and path.is_file() and path.stat().st_size > 0


class RestoreEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (T.FFMPEG and T.FFPROBE):
            raise unittest.SkipTest("没有 ffmpeg/ffprobe")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.rev = root / "reverse"
        cls.rev.mkdir()
        cls.webm = root / "clip.webm"
        if not _make_webm(cls.webm):
            raise unittest.SkipTest("造不出 VP9 WebM")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # ★ 2026-10-23 泄漏事故：曾有一次跑测试时 import_local 起的后台 prepare 线程活过了
        #   tearDown（REVERSE_DIR 已 un-patch），把 320×240 的测试图卡写进了**正式** reverse 目录，
        #   给用户列表塞了 3 条 failed 垃圾任务。这里同时做两件事：桩掉 _bg + 记下正式目录快照。
        self.real_before = self._real_dirs()
        self.patch_dir = patch.object(T, "REVERSE_DIR", self.rev)
        self.patch_dir.start()
        # import_local 会自己起后台 prepare；本测试要同步跑一遍，否则两个线程抢 frames 目录
        self.patch_bg = patch.object(T, "_bg", lambda *a, **k: None)
        self.patch_bg.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()
            T._WORK_START.clear()

    def tearDown(self):
        self.patch_bg.stop()
        self.patch_dir.stop()

    @staticmethod
    def _real_dirs():
        root = Path(__file__).resolve().parents[2] / "reverse"
        return {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()

    def test_zz_no_leak_into_real_reverse_dir(self):
        """测试绝不能往正式 reverse 目录写东西（名字以 zz 开头，保证最后跑）。"""
        leaked = sorted(self._real_dirs() - self.real_before)
        self.assertEqual(leaked, [], "测试把任务泄漏到了正式 reverse 目录")

    def _structure(self, *_a, **_k):
        return {
            "product_summary": "白色瓷砖样张", "person_summary": "无人物", "scene_summary": "测试图卡",
            "uncertainties": [], "shots": [
                {"idx": 1, "name": "镜头一", "t_start": 0, "t_end": 1.6,
                 "initial_state": "彩条静止", "action_steps": ["彩条左移"], "ending_state": "彩条居中",
                 "camera_motion": "固定机位", "boundary_evidence": "硬切"},
                {"idx": 2, "name": "镜头二", "t_start": 1.6, "t_end": DURATION,
                 "initial_state": "彩条居中", "action_steps": ["彩条右移"], "ending_state": "彩条右移",
                 "camera_motion": "固定机位", "boundary_evidence": "硬切"},
            ],
        }

    def _prompts(self, model, content, **_k):
        text = content[0]["text"]
        idxs = [int(m) for m in re.findall(r'"idx":\s*(\d+)', text)] or [1, 2]
        rows = []
        for i in sorted(set(idxs)):
            zh = {"subject": f"镜头{i}主体", "scene": "测试场景", "action": "彩条左移",
                  "camera": "固定机位", "light": "棚拍白光", "style": "测试图卡质感",
                  "keep": "保持原图构图不变",
                  "audio": "环境声：电流底噪；音效：无；台词：原片台词后期回挂，本段不新增；BGM：无"}
            en = {"subject": f"shot {i} subject", "scene": "test scene", "action": "bars move left",
                  "camera": "static camera", "light": "studio white light", "style": "test chart look",
                  "keep": "keep the original framing",
                  "audio": "Ambient: electrical hum; SFX: none; Dialogue: original track reattached later; BGM: none"}
            multi = dict(zh, refs="图片1作为起始构图与动作参考")
            rows.append({"idx": i, "text": zh, "english": en, "multi": multi, "notes": "无"})
        return {"prompts": rows}

    def _stub(self, model, content, **kw):
        text = content[0]["text"]
        if "S" in text and "Seedance" in text:
            return self._prompts(model, content, **kw)
        return self._structure()

    def test_full_run_with_webm(self):
        out = T.import_local(self.webm, "clip.webm")
        vid = out["video_id"]
        self.assertGreater(out["probe"]["duration"], 0.5, out["probe"])
        self.assertEqual(out["probe"]["duration_source"], "format", out["probe"])

        # 真实素材阶段（抽帧/场景/音轨），后台线程同步跑完
        T._prepare(vid, "", out["meta"])
        self.assertEqual(T.status(vid)["stage"], "ready")

        with patch.object(T, "_chat_json", self._stub):
            (T.task_dir(vid) / "voice.json").write_text(
                json.dumps({"state": "spoken", "language": "德语",
                            "source": "user_confirmed", "updated_at": 1}, ensure_ascii=False),
                encoding="utf-8")
            R.run(vid, {"id": "stub-model", "name": "stub"})

        st = T.status(vid)
        rep = json.loads((T.task_dir(vid) / "report.json").read_text(encoding="utf-8"))
        gallery = rep.get("gallery") or []
        self.assertEqual(st["stage"], "done")
        self.assertEqual(len(rep["shots"]), 2)
        # A6：三处计数一致
        self.assertEqual(st["restore_frames"], len(gallery), "状态计数必须等于报告图册张数")
        self.assertGreater(len(gallery), 0)

        assets = T.task_dir(vid) / rep["_assets"]
        bundle = assets / "bundle.zip"
        self.assertTrue(bundle.is_file())
        names = zipfile.ZipFile(bundle).namelist()
        images = [n for n in names if n.startswith("images/")]
        self.assertEqual(len(images), len(gallery), "ZIP 内图片数必须等于图册张数")

        # A5：参考图数量与例外说明必须自洽
        for shot in rep["shots"]:
            refs = shot["references"]
            note = str(shot.get("references_note") or "").strip()
            self.assertTrue(len(refs) >= 2 or (len(refs) == 1 and note),
                            f"S{shot['idx']} 只有 {len(refs)} 张却没有例外说明")
            self.assertEqual(len({r["id"] for r in refs}), len(refs), "参考图不能重复同一帧")

        # A3：解压后离线可看
        with tempfile.TemporaryDirectory() as tmp:
            zipfile.ZipFile(bundle).extractall(tmp)
            report = Path(tmp) / "report.html"
            self.assertTrue(report.is_file())
            text = report.read_text(encoding="utf-8", errors="ignore")
            refs = set(re.findall(r'(?:src|href)="([^"#?]+)"', text))
            local = [r for r in refs if not r.startswith(("http://", "https://", "/tk_reverse/", "data:"))]
            missing = [r for r in local if not (Path(tmp) / r).is_file()]
            self.assertEqual(missing, [], "便携报告存在解析不到的相对依赖")
            self.assertNotIn("/tk_reverse/", text)
            self.assertNotIn("reverse_voice_report.js", text, "便携报告不应依赖在线注入脚本")

        # F16：口播快照必须在报告 JSON / HTML / Markdown 三处一致（用户确认，不是音频识别）
        self.assertEqual(rep.get("voice", {}).get("state"), "spoken", "report.json 缺口播快照")
        self.assertEqual(rep["voice"]["language"], "德语")
        self.assertEqual(rep["voice"]["source"], "user_confirmed")
        md = (assets / "prompts.md").read_text(encoding="utf-8")
        self.assertIn("原片口播语言：德语（用户确认）", md, "prompts.md 未写入口播快照")
        self.assertIn("【主体】", md, "还原提示词未按 Seedance 2.0 模板分段")
        self.assertIn(T.seedance.NEGATIVE, md, "还原提示词缺少模板负向词")
        top_html = (T.task_dir(vid) / f"TK反推报告_{vid}.html").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("原片口播语言：德语（用户确认，非自动音频识别）", top_html, "HTML 未写入口播快照")
        self.assertIn("data-source-voice", top_html)

        # 顶层报告仍是服务端前缀版本
        top = top_html
        self.assertIn("/tk_reverse/", top)


if __name__ == "__main__":
    unittest.main(verbosity=2)
