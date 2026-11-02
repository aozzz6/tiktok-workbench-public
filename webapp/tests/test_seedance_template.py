# -*- coding: utf-8 -*-
"""Seedance 2.0 模板拼装与审计的纯规则测试（不联网、不调模型）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seedance_template as S

SECTIONS = {
    "subject": "年轻女性，米白针织上衣，黑色长裤",
    "scene": "窗边卧室，上午自然光",
    "action": "先低头整理袖口，接着抬头看向镜头，最后缓慢转身",
    "camera": "中近景，固定机位，浅景深",
    "light": "左侧窗光，暖白色调",
    "style": "35mm 胶片颗粒，自然光，纪实感",
    "audio": "环境声：室内安静底噪；音效：衣物摩擦；台词：无；BGM：无",
}


class ComposeTests(unittest.TestCase):
    def test_chinese_order_and_tail(self):
        text = S.compose(SECTIONS, title="竖屏 9:16｜T2｜S1", keep="保持原图构图不变")
        labels = ["【主体】", "【场景环境】", "【动作表演】", "【运镜方式】",
                  "【光影氛围】", "【风格质感】", "【保持不变】", "【音频】", "【负向】"]
        positions = [text.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(S.NEGATIVE, text)
        self.assertTrue(text.startswith("竖屏 9:16"))

    def test_missing_element_raises(self):
        broken = dict(SECTIONS)
        broken.pop("camera")
        with self.assertRaises(ValueError) as ctx:
            S.compose(broken)
        self.assertIn("运镜方式", str(ctx.exception))

    def test_missing_audio_raises(self):
        broken = dict(SECTIONS)
        broken.pop("audio")
        with self.assertRaises(ValueError):
            S.compose(broken)

    def test_english_labels(self):
        text = S.compose(SECTIONS, lang="en")
        for label in ["[Subject]", "[Scene]", "[Action]", "[Camera]", "[Lighting]",
                      "[Style]", "[Audio]", "[Negative]"]:
            self.assertIn(label, text)

    def test_abstract_words_stripped(self):
        text = S.compose(dict(SECTIONS, style="完美的高质量面料，纪实感"))
        self.assertNotIn("完美", text)
        self.assertNotIn("高质量", text)
        self.assertIn("纪实感", text)


class AuditTests(unittest.TestCase):
    def test_good_prompt_passes(self):
        self.assertEqual(S.audit(S.compose(SECTIONS, keep="保持原图构图不变")), [])

    def test_missing_negative_flagged(self):
        text = S.compose(SECTIONS).replace(S.NEGATIVE, "")
        self.assertTrue(any("负向" in issue for issue in S.audit(text)))

    def test_wrong_order_flagged(self):
        text = S.compose(SECTIONS)
        body = text.split("\n")
        body[2], body[3] = body[3], body[2]
        self.assertTrue(any("顺序" in issue for issue in S.audit("\n".join(body))))

    def test_vague_audio_flagged(self):
        text = S.compose(SECTIONS).replace(SECTIONS["audio"], "有声音")
        self.assertTrue(any("音频段" in issue for issue in S.audit(text)))

    def test_camera_without_term_flagged(self):
        text = S.compose(dict(SECTIONS, camera="镜头很好看"))
        self.assertTrue(any("运镜方式" in issue for issue in S.audit(text)))

    def test_check_fields(self):
        self.assertEqual(S.check_fields(SECTIONS), [])
        self.assertTrue(S.check_fields({**SECTIONS, "audio": ""}))
        multi = dict(SECTIONS, refs="")
        self.assertTrue(S.check_fields(multi, need_refs=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
