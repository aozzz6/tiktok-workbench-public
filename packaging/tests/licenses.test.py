# -*- coding: utf-8 -*-
"""T-23 许可证资产单测（`packaging\\licenses\\*` + `THIRD_PARTY_NOTICES.md`）。

覆盖 T-23 DoD 1~4（DoD 5~7 是"发布检查/构建产物"层面，由构建与 QA 用例验）：
  1. `ffmpeg-COPYING.GPLv3.txt` 是 GPLv3 全文（≥660 行、三个标记），且与本机
     `D:\\ffmpeg\\build\\ffmpeg-9.0.2-essentials_build\\LICENSE` 逐字节相同
     （换成 sha256 常量断言，别的机器上也能验；源文件在时再比一次）。
  2. `ffmpeg-SOURCE-OFFER.txt`：build 标识 + commit + GPLv3 + Release 资产文件名 +
     GPLv3 §6(b) 书面要约三要件（≥3 年 / 范围含 37 个静态库 / 只收介质成本 + 具体联系方式）+
     "独立进程调用、不与本项目代码链接"的声明。
  3. `PYAV-FFMPEG-SOURCE.txt`：av 版本 + 内嵌 FFmpeg 版本（7.1）+ 获取方式 +
     明确它在**同一进程内**加载。
  4. `THIRD_PARTY_NOTICES.md`：ffmpeg CLI / PyAV 的 FFmpeg / 随包 Python 依赖 / VC 运行库
     四节都在，且有"仓库源码 MIT、ZIP 内含 GPLv3 二进制、整体不是纯 MIT"的总述；
     原 DM Sans / Apache ECharts 两节保留。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe packaging\\tests\\licenses.test.py`
不联网。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # packaging/tests
PKG = HERE.parent                                  # packaging
ROOT = PKG.parent                                  # 仓库根
LIC_DIR = PKG / "licenses"

GPL = LIC_DIR / "ffmpeg-COPYING.GPLv3.txt"
OFFER = LIC_DIR / "ffmpeg-SOURCE-OFFER.txt"
PYAV = LIC_DIR / "PYAV-FFMPEG-SOURCE.txt"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"

# gyan.dev 那份 GPLv3 全文（35147 字节，674 行）。常量断言让"逐字节相同"在别的机器上也成立。
FFMPEG_SRC_LICENSE = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\LICENSE")
GPL_SHA256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"

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


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


print("licenses — T-23 许可证与第三方声明")

# ── 1. GPLv3 全文 ───────────────────────────────────────────────────
def case_gpl():
    assert_(GPL.is_file(), f"缺 GPLv3 全文：{GPL}")
    text = read(GPL)
    lines = text.splitlines()
    assert_(len(lines) >= 660, f"GPLv3 全文应该 ≥660 行，实得 {len(lines)} 行")
    for marker in ("GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007",
                   "END OF TERMS AND CONDITIONS"):
        assert_(marker in text, f"GPLv3 全文里找不到标记：{marker!r}")
    got = sha256(GPL)
    assert_(got == GPL_SHA256,
            f"GPLv3 全文与 gyan.dev 那份不逐字节相同：sha256={got}（期望 {GPL_SHA256}）")
    print(f"    行数={len(lines)} sha256={got}")
    if FFMPEG_SRC_LICENSE.is_file():
        assert_(sha256(FFMPEG_SRC_LICENSE) == GPL_SHA256,
                "本机源 LICENSE 与下面的常量对不上：常量该跟着换")
        print(f"    与本机 {FFMPEG_SRC_LICENSE} 逐字节相同（源文件在场，已复核）")
    else:
        print(f"    （本机没有 {FFMPEG_SRC_LICENSE}：跳过现场比对，只按 sha256 常量验）")


check("1 ffmpeg-COPYING.GPLv3.txt：674 行 GPLv3 全文，与 gyan.dev 那份逐字节相同", case_gpl)


# ── 2. 书面要约（GPLv3 §6(b) 三要件） ──────────────────────────────
def case_offer():
    assert_(OFFER.is_file(), f"缺书面要约：{OFFER}")
    text = read(OFFER)
    low = text.lower()

    # (a) build 标识 + 许可证 + commit
    for needle in ("ffmpeg version 9.0.2-essentials_build-www.gyan.dev",
                   "GPLv3",
                   "https://github.com/FFmpeg/FFmpeg/commit/946fcce07b"):
        assert_(needle in text, f"书面要约缺 {needle!r}")

    # (b) Release 资产文件名清单（两个源码 tarball + 两个索引）
    for name in ("ffmpeg-9.0.2-source.tar.xz", "ffmpeg-7.1-source.tar.xz",
                 "ffmpeg-9.0.2-static-libs-sources.txt",
                 "pyav-14.2.0-ffmpeg-source.txt"):
        assert_(name in text, f"书面要约的资产清单里缺 {name}")

    # (c) §6(b) 三要件：有效期 / 范围 / 只收介质成本 + 具体联系方式
    assert_("three (3) years" in low or "3 年" in text,
            "书面要约没写有效期（GPLv3 §6(b) 的下限是三年）")
    assert_("37" in text and ("statically linked" in low or "静态链接" in text),
            "书面要约没写清提供范围含静态链接的 37 个库")
    assert_(("cost of the medium" in low) or ("介质" in text and "成本" in text),
            "书面要约没写'只收取介质与邮寄的实际成本'")
    assert_("github.com/aozzz6/tiktok-workbench-public" in text,
            "书面要约没有具体的请求渠道（仓库 Issues 地址）")

    # (d) 独立进程调用、不链接
    assert_("separate process" in low or "独立进程" in text,
            "书面要约没写 ffmpeg 是以独立进程调用的")
    assert_(("not link" in low) or ("不链接" in text) or ("不与" in text and "链接" in text),
            "书面要约没写'不与本项目代码链接'")


check("2 ffmpeg-SOURCE-OFFER.txt：build/commit/GPLv3 + 四个资产名 + §6(b) 三要件 + 独立进程声明",
      case_offer)


# ── 3. PyAV 那份 FFmpeg ─────────────────────────────────────────────
def case_pyav():
    assert_(PYAV.is_file(), f"缺 PyAV 的 FFmpeg 说明：{PYAV}")
    text = read(PYAV)
    low = text.lower()
    assert_("14.2.0" in text, "没写 av 轮子的版本（应为 14.2.0）")
    assert_("7.1" in text, "没写内嵌 FFmpeg 的版本（应为 7.1）")
    assert_(("same process" in low) or ("in-process" in low) or ("同一进程" in text),
            "没写明它在同一进程内加载（与 CLI 那份不同）")
    assert_(("ffmpeg-7.1-source.tar.xz" in text) or ("github.com/FFmpeg/FFmpeg" in text),
            "没写内嵌 FFmpeg 的源码获取方式")


check("3 PYAV-FFMPEG-SOURCE.txt：av 14.2.0 + 内嵌 FFmpeg 7.1 + 源码获取 + 同一进程内加载",
      case_pyav)


# ── 4. THIRD_PARTY_NOTICES.md 四节 + 总述 + 原有两节 ─────────────────
def case_notices():
    assert_(NOTICES.is_file(), f"缺 {NOTICES}")
    text = read(NOTICES)
    low = text.lower()

    # 总述：仓库源码 MIT；ZIP 内含 GPLv3 二进制；整体不是纯 MIT
    assert_("MIT" in text, "总述里没提 MIT")
    assert_("GPLv3" in text, "总述里没提 GPLv3")
    assert_(("not a pure mit" in low) or ("not pure mit" in low)
            or ("不是纯 MIT" in text), "总述没写「整体不是纯 MIT 包」")
    assert_("ZIP" in text or "zip" in low, "总述没点明'发布出的 ZIP'")

    # 四节
    assert_("ffmpeg" in low and ("CLI" in text or "独立进程" in text or "separate process" in low),
            "缺 ffmpeg CLI 那一节")
    assert_("pyav" in low, "缺 PyAV 的 FFmpeg 那一节")
    assert_("PYTHON-PACKAGES.txt" in text, "缺随包 Python 依赖那一节（应指向 licenses\\PYTHON-PACKAGES.txt）")
    assert_(("Visual C++" in text) or ("VC" in text and "运行库" in text) or ("msvcp140" in low),
            "缺 VC 运行库那一节")

    # 原有两节保留
    assert_("DM Sans" in text, "把原有的 DM Sans 那一节弄丢了")
    assert_("Apache ECharts" in text, "把原有的 Apache ECharts 那一节弄丢了")


check("4 THIRD_PARTY_NOTICES.md：四节齐全 + “不是纯 MIT”的总述 + DM Sans/ECharts 保留",
      case_notices)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
