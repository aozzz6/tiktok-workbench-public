# -*- coding: utf-8 -*-
"""desc_split.py — 台账列结构常量 + 文案拆分（单一事实源）

列结构（14 列 A–N，2026-10-16 用户定稿表头：M=音频、N=翻译）：
  A 记录日期   B 对标账号   C 视频发布时间   D 视频标题   E 话题标签
  F 视频链接/ID   G 商品链接   H 播放量   I 点赞数   J 评论数   K 转发数   L 收藏数
  M 音频（音频转写原文）   N 翻译（中文翻译）

D/E 口径（2026-10-14 变更；变更前为 D=desc[:120]、E=desc 全量）：
  D 视频标题 = desc 去掉全部 #话题标签 后的正文（首尾去空白、内部空白折叠为单空格）
  E 话题标签 = desc 中按出现顺序的 # 标签，单空格连接；无标签则为空串

相对 15 列旧结构的差异：删除「视频出单量」（旧 G）与「风格标签」（旧 O）。
"""
import re

# 话题标签：一个及以上 '#' + 单词字符（兼容 #40ozTumbler 数字开头、##musthav 双井号手误）
TAG_RE = re.compile(r"#+[\w]+")

HEADER_14 = ["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签",
             "视频链接/ID", "商品链接", "播放量", "点赞数", "评论数", "转发数", "收藏数",
             "音频", "翻译"]

# 兼容旧名（历史代码 import HEADER_13）
HEADER_13 = HEADER_14

COL_COUNT = len(HEADER_14)   # 14
COL_TITLE = 4                # D 视频标题
COL_TAGS = 5                 # E 话题标签
COL_LINK = 6                 # F 视频链接/ID
COL_PRODUCT = 7              # G 商品链接（挂车商品链接或账号 bio 商品链接）
COL_PLAY = 8                 # H 播放量（其后为 赞/评/转/藏）
COL_NOTE = 13                # M 音频（转写原文）
COL_NOTE_ZH = 14             # N 翻译（中文翻译）


def split_desc(desc: str) -> tuple[str, str]:
    """把 TikTok 文案拆成 (正文, 话题标签串)。

    >>> split_desc("Nice cup! 😂🥤#ProCarryTumbler #DrinkwareGoals ")
    ('Nice cup! 😂🥤', '#ProCarryTumbler #DrinkwareGoals')
    >>> split_desc("hi ##musthav #gadget")
    ('hi', '##musthav #gadget')
    >>> split_desc("")
    ('', '')
    """
    if not desc:
        return "", ""
    tags = TAG_RE.findall(desc)
    body = TAG_RE.sub(" ", desc)          # 标签位置留空格，防相邻词粘连
    body = re.sub(r"\s+", " ", body).strip()
    return body, " ".join(tags)
