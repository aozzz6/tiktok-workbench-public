# -*- coding: utf-8 -*-
"""pipeline_backend.test.py — 防回归：转写后端必须走 openai

背景
----
本机 AVX-512 CPU 上 faster-whisper/ctranslate2 构造即崩（exit=3221225477 / 0xC0000005）。
2026-10-09 一天内踩了两次同款坑：
  ① `app.py::run_account`（界面「运行」按钮）不传 --backend → 崩
  ② `daily_update.py`（「一键运行 → 每日增量更新」）不传 --backend → 崩
     （实测新账号 example_account_047：s5_transcribe=False / pipeline rc=1 / alert_level=WARN）

2026-10-21 更新：转写侧改成了 `--backend auto`（有 CUDA 才走 faster），
不变量换成「默认/注入值只能是 auto 或 openai，且 auto 在无 CUDA 时必须回落 openai」——
等价地保证本机 CPU 上永远不会落到 faster。

本测试锁定：
  · `pipeline.py` 的 `--backend` 默认值 ∈ {auto, openai}；若为 auto，必须有 CPU 回落分支
  · `daily_update.py` 在 DAILY_STEPS 含转写时必须**显式**注入 backend，值 ∈ {auto, openai}
  · 台账表头与 `COL_COUNT` 自洽（不再钉死历史列数）

运行：python tests/pipeline_backend.test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
PIPE = WEBAPP.parent / "pipeline"
sys.path.insert(0, str(PIPE / "scripts"))

_ok = 0
_fail = 0


def check(name: str, fn) -> None:
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name}: {e}")
        _fail += 1
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {name}: {type(e).__name__}: {e}")
        _fail += 1


assert_ = lambda c, m="": (_ for _ in ()).throw(AssertionError(m)) if not c else None

SRC_PIPELINE = (PIPE / "pipeline.py").read_text(encoding="utf-8")
SRC_DAILY = (PIPE / "daily_update.py").read_text(encoding="utf-8")
SRC_TRANSCRIBE = (PIPE / "scripts" / "batch_transcribe_fw.py").read_text(encoding="utf-8")

print("== 用例 1：pipeline.py 的 --backend 默认值 ==")


def t1():
    m = re.search(r'"--backend"\s*,\s*default="(\w+)"', SRC_PIPELINE)
    assert_(m, "pipeline.py 未找到 --backend 的 default")
    v = m.group(1)
    assert_(v in ("auto", "openai"), f"默认值应为 auto 或 openai（faster 在本机必崩），实为 {v}")
    if v == "auto":
        # auto 必须「无 CUDA 就 openai」，否则等于把默认值偷偷放回 faster
        assert_(re.search(r"use_cuda\s*=\s*\(n_gpu\s*>\s*0", SRC_TRANSCRIBE),
                "auto 模式必须先用 n_gpu>0 判定 CUDA")
        assert_(re.search(r'if\s+a\.backend\s*==\s*"auto"\s+and\s+not\s+use_cuda\s*:\s*\n\s*backend\s*=\s*"openai"',
                          SRC_TRANSCRIBE),
                "auto 模式缺「无 CUDA → openai」的回落分支")


check("pipeline.py --backend 默认值 = openai", t1)


def t2():
    m = re.search(r'"--backend"[^)]*choices=\[([^\]]+)\]', SRC_PIPELINE)
    assert_(m, "未找到 --backend 的 choices")
    assert_('"faster"' in m.group(1) and '"openai"' in m.group(1),
            f"choices 应同时保留两者（兼容 CPU 可用 faster 提速）: {m.group(1)}")


check("pipeline.py --backend choices 含 faster/openai", t2)

print("== 用例 2：daily_update.py 必须显式传后端 ==")


def t3():
    m = re.search(r"DAILY_STEPS\s*=\s*\[([^\]]+)\]", SRC_DAILY)
    assert_(m, "未找到 DAILY_STEPS 定义")
    assert_("s5_transcribe" in m.group(1),
            f"DAILY_STEPS 应含转写步骤（否则本测试前提不成立）: {m.group(1)}")


check("daily_update.py DAILY_STEPS 含 s5_transcribe", t3)


def t4():
    m = re.search(r'if\s+"s5_transcribe"\s+in\s+DAILY_STEPS\s*:\s*\n((?:[ \t]+.+\n)+)', SRC_DAILY)
    assert_(m, "daily_update.py 未在「含转写」分支显式注入 backend")
    body = m.group(1)
    val = re.search(r'"--backend"\s*,\s*"(\w+)"', body)
    assert_(val, f"含转写时必须显式传 --backend（不依赖 CLI 默认值），实际分支：{body.strip()!r}")
    assert_(val.group(1) in ("auto", "openai"),
            f"注入值应为 auto（无 CUDA 自动回落 openai）或 openai，实为 {val.group(1)}")


check("daily_update.py 显式传 --backend openai", t4)


def t5():
    m = re.search(r'if\s+"s5_transcribe"\s+in\s+DAILY_STEPS\s*:', SRC_DAILY)
    assert_(m, "缺少「含转写才注入 backend」的条件判断")


check("daily_update.py 按需注入（条件判断在）", t5)

print("== 用例 3：台账建表工具 style_label ==")


def t6():
    import add_account_sheet as m

    assert_(m.style_label(False, 5, "x" * 100) == "未标注", "无音频 → 未标注")
    assert_(m.style_label(True, 0, "") == "（纯BGM，无口播）", "nseg=0 → 纯BGM")
    assert_(m.style_label(True, 5, "short") == "（纯BGM，无口播）", "<30字符 → 纯BGM")
    assert_(m.style_label(True, 7, "x" * 100) == "转写7句", "正常 → 转写N句")


check("add_account_sheet.style_label 四分支", t6)


def t7():
    import add_account_sheet as m

    assert_(callable(m.read_transcript), "缺 read_transcript")
    assert_(callable(m.build_rows), "缺 build_rows")
    assert_(m.HEADER_EXPECT[0] == "记录日期", "HEADER_EXPECT 首列应为「记录日期」")
    assert_(len(m.HEADER_EXPECT) == m.COL_COUNT,
            f"HEADER_EXPECT 列数应等于 COL_COUNT（{m.COL_COUNT}），实为 {len(m.HEADER_EXPECT)}")
    assert_(len(set(m.HEADER_EXPECT)) == len(m.HEADER_EXPECT), "表头不应有重复列名")


check("add_account_sheet 关键接口与 15 列表头", t7)

print("== 用例 4：scrape --ids 必须配 --merge（防不可逆覆盖）==")


def t8():
    """守卫：--ids 表示只抓一部分，此时整体覆盖会不可逆丢失历史条目。"""
    src = (PIPE / "scripts" / "scrape_tiktok_video.py").read_text(encoding="utf-8")
    assert_("--allow-overwrite" in src, "缺 --allow-overwrite 逃生口")
    assert_("if a.ids and not a.merge and not a.allow_overwrite:" in src,
            "缺 fail-fast 守卫（--ids 且无 --merge 时应拒绝）")
    # 守卫块含中文提示，到 return 2 约 330 字符，窗口给足
    block = src[src.index("if a.ids and not a.merge"):][:600]
    assert_("return 2" in block,
            "守卫分支应 return 2（非 0 非 1，便于区分参数误用）")


check("scrape 脚本含 --ids/--merge fail-fast 守卫", t8)


def t9():
    """实跑验证：--ids 无 --merge 必须被拒绝（退出码 2）。"""
    import subprocess
    import tempfile

    script = PIPE / "scripts" / "scrape_tiktok_video.py"
    with tempfile.TemporaryDirectory() as td:
        p = subprocess.run(
            [sys.executable, str(script), "--account", "probe", "--out-dir", td,
             "--ids", "3333333333333333333"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert_(p.returncode == 2,
                f"应退出码 2，实得 {p.returncode}")
        assert_("--merge" in (p.stderr or ""),
                "错误信息应提示 --merge 用法")


check("实跑：--ids 无 --merge → 退出码 2", t9)


def t10():
    """正常用法（--ids + --merge）不应被守卫拦截。"""
    src = (PIPE / "scripts" / "scrape_tiktok_video.py").read_text(encoding="utf-8")
    guard = src.split("if a.ids and not a.merge")[1].split("return 2")[0]
    assert_("not a.allow_overwrite" in guard,
            "守卫条件应允许 --allow-overwrite 显式放行（逃生口）")
    assert_("--merge" in src, "脚本应支持 --merge")


check("守卫不误伤正常用法（--merge / 全量）", t10)

print()
print(f"结果: {_ok} 通过 / {_fail} 失败")
sys.exit(1 if _fail else 0)
