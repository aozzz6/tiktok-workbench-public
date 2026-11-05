# -*- coding: utf-8 -*-
"""llm_endpoint_url.test.py — `_endpoint()` 不重复拼接完整地址（T-12）

背景：用户 ~/.workbuddy/models.json 里的 url 常写**完整**端点
（例如 https://api.deepseek.com/v1/chat/completions）。旧实现无条件追加
"/chat/completions"，请求变成 .../chat/completions/chat/completions → HTTP 404，
设置页「测试连接」永远失败。

约定（本文件就是验收标准）：
  · 已经是完整端点 → 原样返回，不再追加
      openai    .../chat/completions
      anthropic .../messages
      gemini    ...:generateContent
  · 基础地址 → 拼接规则与以前完全一致（不改变既有行为）
      openai    .../v1            → .../v1/chat/completions
      openai    https://x.com     → https://x.com/chat/completions
      anthropic .../v1            → .../messages
      anthropic https://x.com     → https://x.com/v1/messages
      gemini    https://x.com     → https://x.com/v1beta/models/<id>:generateContent
  · 结尾多余的 "/" 先去掉，再判断

运行：webapp/.venv/Scripts/python.exe tests/llm_endpoint_url.test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import llm  # noqa: E402

# Windows 控制台默认 GBK 编码打不出 ✓/✗，会把「断言失败」变成 UnicodeEncodeError 崩溃。
# 本文件自带的输出约定是 UTF-8（与 tests/ai_llm.test.py 等既有用例一致）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                           # noqa: BLE001
        pass

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def ep(style, url, mid="m1"):
    return llm._endpoint(style, {"id": mid, "url": url})


print("llm_endpoint_url — 完整端点不再重复拼接")

# ── 1. 完整端点：原样返回 ────────────────────────────────────────
check("openai：完整 .../chat/completions 原样返回，只有一个 /chat/completions", lambda: (
    assert_(ep("openai", "https://api.deepseek.com/v1/chat/completions")
            == "https://api.deepseek.com/v1/chat/completions",
            ep("openai", "https://api.deepseek.com/v1/chat/completions")),
    assert_(ep("openai", "https://api.deepseek.com/v1/chat/completions").count("/chat/completions") == 1,
            "出现了两次 /chat/completions：" + ep("openai", "https://api.deepseek.com/v1/chat/completions")),
))

check("openai：完整端点带结尾斜杠也只返回一个 /chat/completions", lambda: (
    assert_(ep("openai", "https://api.deepseek.com/v1/chat/completions/")
            == "https://api.deepseek.com/v1/chat/completions",
            ep("openai", "https://api.deepseek.com/v1/chat/completions/")),
))

check("anthropic：完整 .../messages 原样返回", lambda: (
    assert_(ep("anthropic", "https://api.anthropic.com/v1/messages")
            == "https://api.anthropic.com/v1/messages",
            ep("anthropic", "https://api.anthropic.com/v1/messages")),
    assert_(ep("anthropic", "https://api.anthropic.com/v1/messages").count("/messages") == 1,
            ep("anthropic", "https://api.anthropic.com/v1/messages")),
))

check("gemini：完整 ...:generateContent 原样返回（不再套一层 /models/）", lambda: (
    assert_(ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
               mid="gemini-2.0-flash")
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
               mid="gemini-2.0-flash")),
    assert_(ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
               mid="gemini-2.0-flash").count(":generateContent") == 1,
            ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
               mid="gemini-2.0-flash")),
))

# ── 2. 基础地址：拼接规则与既有行为一致 ──────────────────────────
check("openai：基础 .../v1 仍拼成 .../v1/chat/completions", lambda: (
    assert_(ep("openai", "https://api.deepseek.com/v1")
            == "https://api.deepseek.com/v1/chat/completions",
            ep("openai", "https://api.deepseek.com/v1")),
))

check("openai：无路径基础地址仍拼成 .../chat/completions", lambda: (
    assert_(ep("openai", "https://api.openai.com") == "https://api.openai.com/chat/completions",
            ep("openai", "https://api.openai.com")),
    assert_(ep("openai", "http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/chat/completions",
            ep("openai", "http://127.0.0.1:11434/v1")),
))

check("anthropic：基础 .../v1 仍拼成 .../messages（不是 /v1/v1/messages）", lambda: (
    assert_(ep("anthropic", "https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages",
            ep("anthropic", "https://api.anthropic.com/v1")),
    assert_(ep("anthropic", "https://api.anthropic.com") == "https://api.anthropic.com/v1/messages",
            ep("anthropic", "https://api.anthropic.com")),
))

check("gemini：基础地址仍拼成 .../v1beta/models/<id>:generateContent", lambda: (
    assert_(ep("gemini", "https://generativelanguage.googleapis.com", mid="gemini-x")
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent",
            ep("gemini", "https://generativelanguage.googleapis.com", mid="gemini-x")),
    assert_(ep("gemini", "https://generativelanguage.googleapis.com/v1beta", mid="gemini-x")
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent",
            ep("gemini", "https://generativelanguage.googleapis.com/v1beta", mid="gemini-x")),
))

# ── 3. 失败症状：任何风格都不该出现两段路径 ──────────────────────
check("三种风格：任何输入都不会出现重复的路径段", lambda: (
    assert_("/chat/completions/chat/completions"
            not in ep("openai", "https://api.deepseek.com/v1/chat/completions"),
            ep("openai", "https://api.deepseek.com/v1/chat/completions")),
    assert_("/messages/messages"
            not in ep("anthropic", "https://api.anthropic.com/v1/messages"),
            ep("anthropic", "https://api.anthropic.com/v1/messages")),
    assert_(":generateContent:generateContent"
            not in ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/g:generateContent",
                      mid="g"),
            ep("gemini", "https://generativelanguage.googleapis.com/v1beta/models/g:generateContent",
               mid="g")),
))

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
