# -*- coding: utf-8 -*-
"""Seedance 2.0 通用提示词模板（唯一事实源）。

模板出处：工作台根目录 `Seedance2.0通用提示词模板.md`（2026-10-30）。
「提示词反推」与「提示词优化」都从这里取要素顺序、负向词和审计规则，避免两套写法。

模板公式（六要素 + 音频）：
    主体 + 场景环境 + 动作表演 + 运镜方式 + 光影氛围 + 风格质感（+ 保持不变 + 音频 + 负向）

为什么由程序拼段落而不是让模型自由写：
  · 六要素必须按固定顺序出现，模型经常把光线/风格混进动作里；
  · 图生视频必须写「保持不变」，否则首帧人物和构图会被改写；
  · 2.0 原生音画同步，音频必须单独成句；
  · 负向词必须每段都有，不能靠模型自觉。
模型只负责填字段，顺序、标题、负向词由 `compose()` 统一生成。
"""
from __future__ import annotations

import re

VERSION = "Seedance 2.0 通用模板 v1（2026-10-30）"
SOURCE = "Seedance2.0通用提示词模板.md"

# 第三节「负向提示」的通用负向词。每段提示词末尾必须原样出现。
NEGATIVE = "画面撕裂，肢体扭曲，多余手指，字幕水印，闪烁，噪点，卡通感"
NEGATIVE_EN = "torn frames, distorted limbs, extra fingers, subtitles or watermarks, flicker, noise, cartoon look"

# 六要素 + 两个 2.0 必需段落。顺序即输出顺序，不得调整。
ELEMENTS = ("主体", "场景环境", "动作表演", "运镜方式", "光影氛围", "风格质感")
TAIL = ("保持不变", "音频", "负向")
ELEMENT_KEYS = ("subject", "scene", "action", "camera", "light", "style")
REQUIRED_KEYS = ELEMENT_KEYS + ("audio",)

LABELS_ZH = {
    "subject": "主体", "scene": "场景环境", "action": "动作表演",
    "camera": "运镜方式", "light": "光影氛围", "style": "风格质感",
    "keep": "保持不变", "audio": "音频", "negative": "负向", "refs": "参考素材",
}
LABELS_EN = {
    "subject": "Subject", "scene": "Scene", "action": "Action",
    "camera": "Camera", "light": "Lighting", "style": "Style",
    "keep": "Keep", "audio": "Audio", "negative": "Negative", "refs": "References",
}

# 运镜专业术语（模板第一节第 2 条）。审计时运镜段落至少要命中一个。
CAMERA_TERMS_ZH = (
    "推", "拉", "摇", "移", "跟", "环绕", "orbit", "俯冲", "crane", "升降",
    "手持", "handheld", "浅景深", "跟焦", "固定", "微距", "低机位", "高机位",
    "过肩", "主观镜头", "变焦", "横移", "甩镜", "俯拍", "仰拍", "航拍",
    "特写", "近景", "中景", "全景", "远景", "中近景", "半身",
)
CAMERA_TERMS_EN = (
    "push", "pull", "pan", "tilt", "track", "follow", "orbit", "crane", "rise",
    "handheld", "shallow depth", "rack focus", "static", "macro", "low angle",
    "high angle", "over-the-shoulder", "zoom", "whip", "aerial", "close-up",
    "medium", "wide", "dolly",
)

# 模板第三节避坑：抽象形容词不如具体描述。这些词会被自动删掉并在审计里提示。
ABSTRACT_TERMS = ("高质量", "绝美", "震撼", "完美", "无瑕", "唯美", "精美", "顶级", "极致", "大片感")

_AUDIO_HINTS_ZH = ("环境声", "音效", "台词", "BGM", "音乐", "静音")
_AUDIO_HINTS_EN = ("ambient", "sound", "sfx", "dialogue", "voice", "music", "bgm", "silent")


def _is_en(lang: str) -> bool:
    return str(lang).lower().startswith("en")


def _labels(lang: str) -> dict:
    return LABELS_EN if _is_en(lang) else LABELS_ZH


def _label(lang: str, key: str) -> str:
    return f"[{LABELS_EN[key]}] " if _is_en(lang) else f"【{LABELS_ZH[key]}】"


def _text(value) -> str:
    return "" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def strip_abstract(value) -> str:
    """删掉模板明确点名的空泛形容词，保留具体描述。

    「完美贴合」→「贴合」；「高质量面料」→「面料」。只在词本身出现时删，
    不碰「电影感」「纪实感」这类模板认可的质感词。
    """
    text = _text(value)
    for word in ABSTRACT_TERMS:
        text = re.sub(re.escape(word) + r"的?", "", text)
    text = re.sub(r"[，,、；;]\s*(?=[，,、；;])", "", text)
    return re.sub(r"^[，,、；;\s]+|[，,、；;\s]+$", "", text)


def _add_clause(text: str, clause: str, sep: str) -> str:
    base = text.rstrip("。；;，, \t.")
    return (base + sep + clause) if base else clause


def _complete_audio(sound: str, lang: str) -> str:
    """补齐音频段缺的台词/BGM 说明；不凭空新增环境声或音效。"""
    text = _text(sound)
    if _is_en(lang):
        low = text.lower()
        if "dialogue" not in low and "voice" not in low:
            text = _add_clause(text, "Dialogue: none", "; ")
        if "bgm" not in low and "music" not in low:
            text = _add_clause(text, "BGM: none", "; ")
    else:
        if "台词" not in text:
            text = _add_clause(text, "台词：无", "；")
        if "BGM" not in text and "音乐" not in text:
            text = _add_clause(text, "BGM：无", "；")
    return text


def compose(sections: dict, *, title: str = "", lang: str = "zh", refs: str = "",
            keep: str = "", audio: str = "", negative: str = NEGATIVE,
            require_audio: bool = True) -> str:
    """把六要素字段拼成可直接复制的 Seedance 2.0 提示词。

    sections 必须含 subject/scene/action/camera/light/style；audio 可单独传。
    缺字段直接抛 ValueError——宁可不生成，也不让模型用一句话糊过去。
    """
    if not isinstance(sections, dict):
        raise ValueError("提示词要素必须是对象")
    labels = _labels(lang)
    if _is_en(lang) and negative == NEGATIVE:
        negative = NEGATIVE_EN
    values = {key: strip_abstract(sections.get(key)) for key in ELEMENT_KEYS}
    missing = [labels[key] for key, value in values.items() if not value]
    sound = strip_abstract(audio or sections.get("audio"))
    if sound:
        sound = _complete_audio(sound, lang)
    if require_audio and not sound:
        missing.append(labels["audio"])
    if missing:
        raise ValueError("缺少提示词要素：" + "、".join(missing))

    lines = []
    if title:
        lines.append(_text(title))
    if refs:
        lines.append(f"{_label(lang, 'refs')}{strip_abstract(refs)}")
    for key in ELEMENT_KEYS:
        lines.append(f"{_label(lang, key)}{values[key]}")
    if keep or sections.get("keep"):
        value = strip_abstract(keep or sections.get("keep"))
        lines.append(f"{_label(lang, 'keep')}{value}")
    lines.append(f"{_label(lang, 'audio')}{sound}")
    lines.append(f"{_label(lang, 'negative')}{_text(negative)}")
    return "\n".join(lines)


def _label_positions(text: str, lang: str) -> list[tuple[str, int]]:
    found = []
    for key in ("refs", *ELEMENT_KEYS, "keep", "audio", "negative"):
        pos = text.find(_label(lang, key))
        if pos >= 0:
            found.append((key, pos))
    return sorted(found, key=lambda item: item[1])


def audit(text: str, *, lang: str = "zh") -> list[str]:
    """按模板检查一段提示词，返回可读问题列表；空列表=已覆盖规则。

    这是本地规则审计，不是成片质量判断。它只回答「模板要求的段落有没有、
    顺序对不对、负向词在不在、有没有模板点名的空泛形容词」。
    """
    raw = _text(text)
    issues: list[str] = []
    if not raw:
        return ["提示词为空"]

    positions = _label_positions(raw, lang)
    present = [key for key, _ in positions]
    required = [*ELEMENT_KEYS, "audio", "negative"]
    for key in required:
        if key not in present:
            issues.append(f"缺少{_label(lang, key)}")
    order = [key for key, _ in positions if key in required]
    expected = [key for key in required if key in present]
    if order != expected:
        issues.append("六要素/音频/负向顺序与模板不一致")

    if NEGATIVE not in raw and (NEGATIVE_EN if _is_en(lang) else NEGATIVE) not in raw:
        issues.append("负向词未按模板原样写入")

    hits = [word for word in ABSTRACT_TERMS if word in raw]
    if hits:
        issues.append("含模板避坑的抽象形容词：" + "、".join(hits))

    # 运镜段落必须用专业术语，不能只写「镜头很好看」。
    start = raw.find(_label(lang, "camera"))
    if start >= 0:
        end = raw.find(_label(lang, "light"))
        camera = raw[start:end if end > start else start + 400]
        terms = CAMERA_TERMS_EN if _is_en(lang) else CAMERA_TERMS_ZH
        if not any(term in camera for term in terms):
            issues.append("运镜方式缺少专业术语（推拉摇移跟/环绕/俯冲/手持/浅景深等）")

    # 音频段要分别说明环境声/音效/台词/音乐，不能只写一句「有声音」。
    start = raw.find(_label(lang, "audio"))
    if start >= 0:
        sound = raw[start:]
        hints = _AUDIO_HINTS_EN if _is_en(lang) else _AUDIO_HINTS_ZH
        if sum(1 for hint in hints if hint.lower() in sound.lower()) < 2:
            issues.append("音频段过于笼统，至少分别写明环境声/音效/台词/音乐中的两项")
    return issues


def check_fields(sections: dict, *, lang: str = "zh", need_refs: bool = False) -> list[str]:
    """检查模型返回的分段字段是否齐全（compose 前的硬门槛）。"""
    labels = _labels(lang)
    if not isinstance(sections, dict):
        return ["分段字段不是对象"]
    issues = [f"缺少【{labels[key]}】" for key in REQUIRED_KEYS
              if not _text(sections.get(key))]
    if need_refs and not _text(sections.get("refs")):
        issues.append("多图版缺少【参考素材】用途说明")
    return issues
