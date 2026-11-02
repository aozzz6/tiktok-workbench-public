# -*- coding: utf-8 -*-
"""提示词反推（tk_reverse）纯逻辑测试。

不联网、不下载、不调模型：只覆盖可判定的解析/抽样/容错/渲染逻辑。
跑法：webapp/.venv/Scripts/python.exe tests/tk_reverse.test.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tk_reverse as T


class ParseUrlTests(unittest.TestCase):
    def test_standard_video_url(self):
        url, vid = T.parse_url("https://www.tiktok.com/@example_account_030/video/7698814547133517078")
        self.assertEqual(vid, "7698814547133517078")
        self.assertIn("/video/7698814547133517078", url)

    def test_short_link_defers_to_ytdlp(self):
        url, vid = T.parse_url("https://vm.tiktok.com/ZMabcDEFg/")
        self.assertEqual(vid, "")           # 短链的 id 只能由 yt-dlp 跟随跳转拿到
        self.assertIn("vm.tiktok.com", url)

    def test_bare_video_id(self):
        url, vid = T.parse_url("  7698814547133517078 \n")
        self.assertEqual(vid, "7698814547133517078")
        self.assertIn("tiktok.com", url)

    def test_query_string_is_kept(self):
        _, vid = T.parse_url("https://www.tiktok.com/@a/video/7698814547133517078?is_from_webapp=1")
        self.assertEqual(vid, "7698814547133517078")

    def test_rejects_non_tiktok_and_garbage(self):
        for bad in ("", "https://youtube.com/watch?v=abc", "https://www.tiktok.com/@a"):
            with self.subTest(bad=bad), self.assertRaises(T.ReverseError):
                T.parse_url(bad)


class NormMetaTests(unittest.TestCase):
    def _raw(self, **kw):
        base = {
            "id": "123456789012345", "description": "Zu kalt für normale Hosen?\n\n#Schneehose #TikTokShopDE #WinterStyle",
            "uploader": "someone", "channel": "Anna", "duration": 11, "width": 720, "height": 1280,
            "view_count": 632, "like_count": 3, "upload_date": "20261020",
        }
        base.update(kw)
        return base

    def test_extracts_hook_hashtags_and_shop_tag(self):
        m = T._norm_meta(self._raw())
        self.assertEqual(m["hook_line"], "Zu kalt für normale Hosen?")
        self.assertIn("Schneehose", m["hashtags"])
        self.assertNotIn("#", m["hashtags"][0])
        self.assertTrue(m["has_shop_tag"])

    def test_no_shop_tag_when_absent(self):
        m = T._norm_meta(self._raw(description="#fashion #ootd"))
        self.assertFalse(m["has_shop_tag"])

    def test_upload_date_only_does_not_fake_midnight(self):
        m = T._norm_meta(self._raw())
        self.assertEqual(m["upload_at"], "2026-10-20")     # 不许写成 00:00

    def test_create_time_wins_when_present(self):
        m = T._norm_meta(self._raw(create_time=1792549913))
        self.assertRegex(m["upload_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_language_guess_prefers_german(self):
        self.assertEqual(T._norm_meta(self._raw())["language_guess"], "de")
        self.assertEqual(T._norm_meta(self._raw(description="the best pants and this is it"))["language_guess"], "en")


class PickFramesTests(unittest.TestCase):
    def _frames(self, n):
        return [{"n": i + 1, "t": round(i / T.FPS_SAMPLE, 2), "path": f"f_{i + 1:03d}.jpg"} for i in range(n)]

    def test_short_clip_uses_every_frame(self):
        fr = self._frames(10)                     # 5.0s
        self.assertEqual(len(T._pick_frames(fr, 5.0, [])), 10)

    def test_long_clip_samples_every_two_seconds(self):
        fr = self._frames(40)                     # 20.0s
        picked = T._pick_frames(fr, 20.0, [])
        self.assertLess(len(picked), len(fr))
        self.assertLessEqual(len(picked), T.MAX_FRAME_FOR_AI)
        self.assertEqual(picked[-1]["n"], 40)     # 结尾定格必须留

    def test_scene_cut_adds_nearest_frame(self):
        fr = self._frames(40)
        picked = T._pick_frames(fr, 20.0, [5.7])
        self.assertTrue(any(abs(p["t"] - 5.7) <= 0.5 for p in picked))

    def test_cap_is_respected(self):
        fr = self._frames(100)                    # 50s，每 2 秒 1 张仍有 25 张
        self.assertLessEqual(len(T._pick_frames(fr, 50.0, [])), T.MAX_FRAME_FOR_AI)

    def test_empty_input(self):
        self.assertEqual(T._pick_frames([], 0, []), [])


class JloadTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(T._jload('{"a":1}')["a"], 1)

    def test_fenced_json(self):
        self.assertEqual(T._jload('```json\n[{"x":2}]\n```')[0]["x"], 2)

    def test_json_with_leading_prose(self):
        self.assertEqual(T._jload('好的，结果如下：\n{"ok":true}\n以上。')["ok"], True)

    def test_garbage_raises(self):
        with self.assertRaises(T.ReverseError):
            T._jload("模型今天不想干活")


class JloadRepairTests(unittest.TestCase):
    """2026-10-21 真实故障：读帧输出被 max_tokens 截断，括号未闭合 → 整条反推失败。"""

    TRUNCATED = ('[{"t":0.0,"person":"青年女性；棕色低盘发","product":"浅灰卡其色翻领衬衫"},'
                 '{"t":0.5,"person":"青年女性；侧脸","product":"衬衫与浅蓝色直筒牛仔裤"},'
                 '{"t":1.0,"person":"青年女')

    def test_truncated_raises_without_partial(self):
        with self.assertRaises(T.ReverseError) as cm:
            T._jload(self.TRUNCATED)
        self.assertIn("截断", str(cm.exception))

    def test_truncated_partial_returns_head_objects(self):
        got = T._jload(self.TRUNCATED, partial=True)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["t"], 0.0)
        self.assertIn("牛仔裤", got[1]["product"])

    def test_concatenated_objects_become_list(self):
        got = T._jload('{"t":0,"person":"A"}\n{"t":0.5,"person":"B"}')
        self.assertIsInstance(got, list)
        self.assertEqual([g["person"] for g in got], ["A", "B"])

    def test_chinese_quotes_repaired(self):
        got = T._jload('〔示例〕\u201cok\u201d：\u201c{"a":1,"b":"x"}\u201d')
        self.assertEqual(got["a"], 1)

    def test_trailing_comma_repaired(self):
        self.assertEqual(T._jload('{"a":1,"b":[1,2,],}')["b"], [1, 2])

    def test_fenced_with_language_and_prose(self):
        self.assertEqual(T._jload("结果如下\n```JSON\n{\"k\":3}\n```\n完")["k"], 3)

    def test_brace_inside_string_not_confused(self):
        got = T._jload('注意 {"a":"含 } 和 ] 的字符串","b":2} 结束')
        self.assertEqual(got["b"], 2)

    def test_empty_text_message(self):
        with self.assertRaises(T.ReverseError) as cm:
            T._jload("   ")
        self.assertIn("空内容", str(cm.exception))


class ChatJsonRetryTests(unittest.TestCase):
    """解析失败要自动再问一次（加「只输出 JSON」指令），不是直接失败。"""

    def test_retries_once_then_succeeds(self):
        replies = ["抱歉，我先说说画面：一个女性站在摊位前", '{"shots":[{"idx":1}]}']
        with patch.object(T, "_chat", side_effect=replies) as m:
            got = T._chat_json({"id": "m"}, [{"type": "text", "text": "p"}], max_tokens=100)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(got["shots"][0]["idx"], 1)
        self.assertIn("只输出一个合法 JSON", m.call_args_list[1][0][1][-1]["text"])

    def test_retries_disabled_raises_first_error(self):
        with patch.object(T, "_chat", return_value="不是 JSON"):
            with self.assertRaises(T.ReverseError):
                T._chat_json({"id": "m"}, [{"type": "text", "text": "p"}], retries=0)


class DescribeFramesRecoveryTests(unittest.TestCase):
    """批量输出被截断 → 抢救头部 + 逐帧补读，不整条失败。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "frames").mkdir()
        for i in range(1, 4):
            (root / "frames" / f"f_{i:03d}.jpg").write_bytes(b"x")
        self.vid = "7698863537006939394"
        self._p1 = patch.object(T, "_dir", return_value=root)
        self._p2 = patch.object(T, "_log", return_value=None)
        self._p3 = patch.object(T, "_img_part", return_value={"type": "text", "text": "img"})
        for p in (self._p1, self._p2, self._p3):
            p.start()
        self.frames = [{"n": i, "t": i - 1.0, "path": f"f_{i:03d}.jpg"} for i in range(1, 4)]

    def tearDown(self):
        for p in (self._p1, self._p2, self._p3):
            p.stop()
        self.tmp.cleanup()

    def test_missing_frames_are_refilled_one_by_one(self):
        calls = []

        def fake_chat_json(model, content, max_tokens=6000, partial=False, retries=1):
            calls.append(partial)
            if content[0]["text"].find("第 1/1 批") >= 0:
                return [{"person": "A"}, {"person": "B"}]      # 被截断，只回 2 帧
            return {"person": "C"}                            # 单帧补读

        with patch.object(T, "_chat_json", side_effect=fake_chat_json):
            got = T._describe_frames("v", {"id": "m"}, self.frames, 5, 45)
        self.assertEqual([g["person"] for g in got], ["A", "B", "C"])
        self.assertEqual([g["t"] for g in got], [0.0, 1.0, 2.0])
        self.assertTrue(calls[0])                             # 批量调用走 partial

    def test_empty_result_raises_readable_error(self):
        with patch.object(T, "_chat_json", return_value=[]):
            with self.assertRaises(T.ReverseError) as cm:
                T._describe_frames("v", {"id": "m"}, self.frames, 5, 45)
        self.assertIn("读帧结果为空", str(cm.exception))

    def test_invalid_middle_slot_is_refilled_in_place(self):
        """P2-1：[帧1, null, 帧3] 不能过滤成两项后按新位置配对（否则帧3 绑到帧2 的时间点）。"""
        with patch.object(T, "_chat_json",
                          return_value=[{"person": "A"}, None, {"person": "C"}]), \
                patch.object(T, "_describe_one", return_value={"person": "FILL2"}):
            got = T._describe_frames("v", {"id": "m"}, self.frames, 5, 45)
        self.assertEqual([g["person"] for g in got], ["A", "FILL2", "C"])
        self.assertEqual([g["n"] for g in got], [1, 2, 3])
        self.assertEqual([g["t"] for g in got], [0.0, 1.0, 2.0])

    def test_trailing_gap_still_refilled_in_order(self):
        with patch.object(T, "_chat_json", return_value=[{"person": "A"}]), \
                patch.object(T, "_describe_one", side_effect=[{"person": "B"}, {"person": "C"}]):
            got = T._describe_frames("v", {"id": "m"}, self.frames, 5, 45)
        self.assertEqual([g["person"] for g in got], ["A", "B", "C"])


class VideoIdBoundaryTests(unittest.TestCase):
    """P1-1：按 vid 访问的入口共用数字 ID + 根目录内校验；删除不得越界。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reverse"
        self.root.mkdir()
        self._p = patch.object(T, "REVERSE_DIR", self.root)
        self._p.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    def test_rejects_non_numeric_and_traversal_ids(self):
        for bad in ("..", "../webapp", "..\\webapp", "/abs/path", "C:\\Windows", "",
                    "12ab", "１２３", "a/b", "."):
            with self.subTest(bad=bad), self.assertRaises(T.ReverseError):
                T._dir(bad)

    def test_valid_id_maps_to_single_subdir(self):
        d = T._dir("7698863537006939394")
        self.assertEqual(d.parent, self.root.resolve())
        self.assertEqual(d.name, "7698863537006939394")
        self.assertEqual(T.task_dir("7698863537006939394"), d)

    def test_delete_cannot_escape_root(self):
        sibling = self.root.parent / "sibling_business_data.txt"
        sibling.write_text("keep me", encoding="utf-8")
        victim = self.root / "123456789012345"
        victim.mkdir()
        (victim / "status.json").write_text('{"stage":"done"}', encoding="utf-8")
        for bad in ("..", "../sibling_business_data.txt", "."):
            with self.subTest(bad=bad), self.assertRaises(T.ReverseError):
                T.remove(bad)
        self.assertTrue(sibling.exists())
        self.assertTrue(self.root.exists())
        self.assertTrue(victim.exists())
        self.assertEqual(T.remove("123456789012345"), {"ok": True, "removed": True})
        self.assertFalse(victim.exists())
        self.assertTrue(sibling.exists())

    def test_delete_failure_is_not_reported_as_success(self):
        victim = self.root / "2223334445"
        victim.mkdir()
        with patch.object(T.shutil, "rmtree", side_effect=OSError("locked")):
            with self.assertRaises(T.ReverseError):
                T.remove("2223334445")
        self.assertTrue(victim.exists())

    def test_list_all_ignores_non_numeric_dirs(self):
        good = self.root / "3334445556"
        good.mkdir()
        (good / "status.json").write_text('{"stage":"done"}', encoding="utf-8")
        bad = self.root / "not-a-task"
        bad.mkdir()
        (bad / "status.json").write_text('{"stage":"done"}', encoding="utf-8")
        self.assertEqual([i["video_id"] for i in T.list_all()], ["3334445556"])


class RealismTests(unittest.TestCase):
    def test_appends_english_spell_when_missing(self):
        out = T._ensure_realism("竖屏 9:16。人物：女性。", {"action": "手扶拉链", "shot_size": "特写"})
        self.assertIn("realistic human", out)
        self.assertIn("no uncanny AI look", out)

    def test_leg_word_triggers_limb_clause(self):
        out = T._ensure_realism("竖屏 9:16。", {"action": "抬脚亮靴底积雪"})
        self.assertIn("correct leg anatomy", out)

    def test_idempotent(self):
        once = T._ensure_realism("竖屏 9:16。", {"action": "微笑"})
        self.assertEqual(once, T._ensure_realism(once, {"action": "微笑"}))

    def test_legs_added_even_if_model_wrote_human_module(self):
        # 模型自己写了 human 版但漏了 leg 子句 → 只补 leg，不重复整段
        text = "竖屏 9:16。" + T.REALISM_HUMAN
        out = T._ensure_realism(text, {"action": "雪地行走，抬脚亮靴底"})
        self.assertTrue(out.endswith(T.REALISM_LEGS))
        self.assertEqual(out.count("realistic human"), 1)

    def test_no_legs_clause_for_static_upper_body(self):
        out = T._ensure_realism("竖屏 9:16。" + T.REALISM_HUMAN, {"action": "双手插入口袋，看镜头微笑"})
        self.assertNotIn("correct leg anatomy", out)

    def test_hand_in_product_shot_upgrades_to_human(self):
        # 模型把有手的近景写成产品版 → 必须换成 human 版（手畸形是高频失败项）
        text = "竖屏 9:16 裤装特写。" + T.REALISM_PRODUCT
        out = T._ensure_realism(text, {"action": "右手插右侧口袋，展示腰头与拉链"})
        self.assertIn("realistic human", out)
        self.assertIn("correct finger count", out)
        self.assertNotIn("realistic fabric texture", out)

    def test_product_only_version(self):
        out = T._ensure_realism("竖屏 9:16 商品特写。", {"action": "纯商品特写，无人脸"})
        self.assertIn("realistic fabric texture", out)
        self.assertNotIn("realistic human", out)


class PromptTemplateTests(unittest.TestCase):
    """广告拆解提示词必须按 Seedance 2.0 模板拼装，模型只填字段。"""

    struct = {"product_summary": "米白翻领针织上衣", "person_summary": "年轻成年女性",
              "scene_summary": "窗边日光卧室",
              "shots": [{"idx": 1, "name": "窗边展示", "t_start": 0, "t_end": 2,
                         "shot_size": "中近景", "action": "人物正面对镜头整理袖口，嘴部可见",
                         "info": "展示上衣"}]}
    probe = {"width": 1080, "height": 1920, "fps": 30, "duration": 2}

    def _row(self, **over):
        row = {"idx": 1, "subject": "年轻女性，米白针织上衣", "scene": "窗边卧室，上午自然光",
               "action": "先低头整理袖口，接着抬头看向镜头，最后缓慢转身",
               "camera": "中景固定机位，浅景深", "light": "左侧窗光，暖白色调",
               "style": "完美的高质量面料，纪实感",
               "audio": "环境声：室内安静底噪；音效：衣物摩擦；台词：无；BGM：无",
               "keep": "保持原图人物长相、服装、构图和光影关系不变"}
        row.update(over)
        return row

    def _run(self, row):
        with patch.object(T, "_chat_json", return_value={"prompts": [row]}), \
                patch.object(T, "_log", lambda *a, **k: None):
            return T._write_prompts("123456789012345", {"id": "stub"}, self.struct, {}, self.probe)

    def test_composes_template_and_strips_abstract(self):
        out = self._run(self._row())
        self.assertEqual(len(out), 1)
        text = out[0]["text"]
        for label in ["【主体】", "【场景环境】", "【动作表演】", "【运镜方式】",
                      "【光影氛围】", "【风格质感】", "【保持不变】", "【音频】", "【负向】"]:
            self.assertIn(label, text)
        self.assertIn(T.seedance.NEGATIVE, text)
        self.assertNotIn("完美", text)
        self.assertNotIn("高质量", text)
        self.assertIn("realistic human", text)
        self.assertEqual(T.seedance.audit(text), [])

    def test_missing_audio_is_missing_not_faked(self):
        row = self._row()
        row.pop("audio")
        self.assertEqual(self._run(row), [])

    def test_string_prompt_is_rejected(self):
        # 旧版自由文本不再是合法输出；避免模型绕过模板。
        row = self._row()
        row.pop("subject")
        row["text"] = "竖屏 9:16 一段自由文本"
        self.assertEqual(self._run(row), [])

    def test_keep_strips_original_params(self):
        keep = T._clean_keep("原片参数：1080×1920、30fps、2.5s；保持原图构图不变")
        self.assertEqual(keep, "保持原图构图不变")
        self.assertEqual(T._clean_keep(""), T.DEFAULT_KEEP)


class SlugTests(unittest.TestCase):
    def test_known_function_names(self):
        self.assertEqual(T._slug("场景锚定", 1), "scene")
        self.assertEqual(T._slug("物理证明", 5), "proof")

    def test_strips_timeline_prefix(self):
        self.assertNotEqual(T._slug("腰头抽绳中景 0.0–2.0s", 2), "0_2s")

    def test_unknown_chinese_falls_back_to_index(self):
        self.assertEqual(T._slug("某个模型的临时命名", 4), "shot4")

    def test_ascii_name_kept(self):
        self.assertEqual(T._slug("walk away", 3), "walk_away")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(T, "REVERSE_DIR", Path(self.tmp.name))
        self.patch.start()
        with T._LOCK:
            T._STATE.clear()
            T._WORKERS.clear()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_save_and_reload_roundtrip(self):
        T._save("111", stage="ready", frames=23, shots=7)
        with T._LOCK:
            T._STATE.clear()                      # 模拟服务重启
        st = T._load("111")
        self.assertEqual(st["stage"], "ready")
        self.assertEqual(st["frames"], 23)

    def test_interrupted_state_falls_back_to_ready(self):
        T._save("222", stage="analyzing", frames=23)
        with T._LOCK:
            T._STATE.clear()
        st = T._load("222")
        self.assertEqual(st["stage"], "ready")     # 崩溃遗留的 running 态不可信
        self.assertIn("中断", st["stage_label"])

    def test_interrupted_without_frames_is_failed(self):
        T._save("333", stage="preparing", frames=0)
        with T._LOCK:
            T._STATE.clear()
        self.assertEqual(T._load("333")["stage"], "failed")

    def test_status_reports_report_url_only_when_file_exists(self):
        T._save("444", stage="ready", frames=5)
        self.assertEqual(T.status("444")["report"], "")
        d = T._dir("444")
        (d / "TK反推报告_444.html").write_text("<html></html>", encoding="utf-8")
        st = T.status("444")
        self.assertTrue(st["has_report"])
        self.assertTrue(st["report"].endswith("TK反推报告_444.html"))

    def test_list_all_reads_disk(self):
        T._save("555", stage="done", frames=9, shots=3, has_report=True)
        with T._LOCK:
            T._STATE.clear()
        items = T.list_all()
        self.assertEqual([i["video_id"] for i in items], ["555"])

    def test_handoff_requires_material(self):
        T._save("666", stage="pending", frames=0)
        with self.assertRaises(T.ReverseError):
            T.build_handoff("666")


class RenderReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(T, "REVERSE_DIR", Path(self.tmp.name))
        self.patch.start()
        T._dir("777").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _ai(self, with_prompts=True):
        return {
            "product_summary": "深炭灰抓绒雪裤",
            "person_summary": "20 岁欧洲白人女性",
            "scene_summary": "雪地木屋前",
            "hook_line": "Zu kalt?",
            "phys_proof": "有：靴底积雪",
            "why": [{"point": "疑问句钩子", "basis": "首帧文案"}],
            "ratio": {"hook": 17, "selling": 41, "proof": 28, "ending": 14},
            "shots": [
                {"idx": 1, "name": "场景锚定", "t_start": 0.0, "t_end": 2.0,
                 "shot_size": "中全景·低机位", "action": "走出木屋", "info": "场景+版型"},
                {"idx": 2, "name": "物理证明", "t_start": 8.0, "t_end": 10.0,
                 "shot_size": "低角度", "action": "抬脚亮靴底", "info": "物理证明"},
            ],
            "prompts": ([{"idx": 1, "text": "竖屏 9:16 第一段"}] if with_prompts else []),
            "narration": {"a": "Frau tritt in den Schnee.", "b": "Wasserdicht ⚠", "a_words": 5, "b_words": 3},
            "compliance": [{"rule": "功能词无据", "risk": "wasserdicht 需供应商参数"}],
            "template": [{"seg": "S1", "func": "场景锚定", "formula": "极端环境+出口走出", "pct": "15–18%"}],
        }

    def _html(self, ai):
        meta = {"author": "someone", "nickname": "Anna", "desc": "文案", "hashtags": ["a"],
                "view": 1, "like": 2, "upload_at": "2026-10-20", "duration": 11.0}
        probe = {"width": 720, "height": 1280, "fps": 30.0, "duration": 11.53, "has_audio": True,
                 "audio_codec": "aac"}
        return T._render_report("777", {"model": "m"}, meta, probe, ai)

    def test_seven_sections_present(self):
        html = self._html(self._ai())
        for title in ("一、这条片子为什么能跑", "二、分镜反推表", "三、可直接使用的提示词",
                      "四、首帧参考图", "五、旁白与字幕", "六、合规与实操", "七、换品复用模板"):
            self.assertIn(title, html)

    def test_uses_probe_duration_not_rounded_meta(self):
        html = self._html(self._ai())
        self.assertIn("11.53s", html)
        self.assertIn("参考上限 27 词", html)      # 11.53 × 2.3 = 26.52 → 27

    def test_missing_prompt_is_flagged_not_faked(self):
        html = self._html(self._ai(with_prompts=False))
        self.assertIn("本段提示词缺失", html)
        self.assertNotIn("竖屏 9:16 第一段", html)

    def test_images_use_relative_paths(self):
        html = self._html(self._ai())
        self.assertNotIn('src="D:/', html)
        self.assertNotIn("src=\"/tk_reverse", html)

    def test_audio_note_never_claims_transcript(self):
        html = self._html(self._ai())
        self.assertIn("未做 ASR", html)
        self.assertNotIn("转写结果：", html)

    def test_ratio_bar_sums_shown(self):
        html = self._html(self._ai())
        self.assertIn("合计 100%", html)

    def test_shot_name_time_suffix_cleaned(self):
        ai = self._ai()
        ai["shots"][0]["name"] = "场景锚定·痛点钩子（切点±0.2s）"
        html = self._html(ai)
        self.assertNotIn("切点±0.2s", html)
        self.assertIn("场景锚定·痛点钩子", html)

    def test_code_fence_and_copy_button(self):
        html = self._html(self._ai())
        self.assertIn("function cp(", html)
        self.assertIn('id="p1"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
