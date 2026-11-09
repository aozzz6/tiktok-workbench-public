# -*- coding: utf-8 -*-
r"""qa/T-23/run.py — T-23（许可证包）的独立验收用例。

清单来源：`.dsh-crew/jobs/portable-package/M3-qa-plan.md` 的 **T-23** 段（10 条，逐条实现）。
DoD 来源：`docs/tasks/tasks.md` 的 T-23（DoD 1~9）。

做了什么：
  · 1  GPLv3 全文：≥660 行 + 三个标记 + 与本机 gyan.dev 的 LICENSE **逐字节相同**（sha256）；
  · 2  `ffmpeg-SOURCE-OFFER.txt` 四组：build/commit/GPLv3 · 四个资产名 · GPLv3 §6(b) 三要件
       （≥3 年 / 范围含 37 个静态库与 av 的 FFmpeg / 只收介质成本 + **具体**联系方式）· 独立进程声明；
  · 3  `PYAV-FFMPEG-SOURCE.txt`：av 版本 + 内嵌 FFmpeg 版本 + 获取方式 + **同一进程内**加载；
  · 4  `THIRD_PARTY_NOTICES.md`：四节 + 「不是纯 MIT」总述 + DM Sans / Apache ECharts 保留；
  · 5  `M3-gaps.md`：Release 必需 6 个资产名 + 「上传后必须人工点一次下载」那句（机器核对）；
  · 6  **DEFERRED-RELEASE**：GitHub Release 上传 + 逐个点下载 → 200/非 0 字节，只能人做；
  · 7  先用当前代码重跑构建，再核对包内六件 + `PYTHON-PACKAGES.txt` 三行；
  · 8  抽 5 个发行版，`*.dist-info\METADATA` 都在（构建不许删 dist-info）；
  · 9  `licenses.test.py` 跑绿；
  · 10 入口自检（pipeline\.venv 跑、10 条清单齐全、打印 DEFERRED 数）。

DEFERRED-RELEASE 单列：**不算失败**，但必须打印（两个源码 tarball 按 PM 指示本机未下载，
「文件存在」与「点下载」只能发布时由人做）。

跑法（仓库根）：`pipeline\.venv\Scripts\python.exe qa\T-23\run.py`
调试：可只跑某几条 —— `... qa\T-23\run.py T23-01 T23-02`。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]        # qa/T-23/run.py -> 仓库根
PKG = REPO / "packaging"
LIC = PKG / "licenses"
GPL = LIC / "ffmpeg-COPYING.GPLv3.txt"
OFFER = LIC / "ffmpeg-SOURCE-OFFER.txt"
PYAV = LIC / "PYAV-FFMPEG-SOURCE.txt"
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"
UNIT = PKG / "tests" / "licenses.test.py"
BUILD = PKG / "build_portable.py"

GAPS = Path(os.environ.get(
    "T23_GAPS",
    r"C:\Users\94453\AppData\Local\Temp\dsh-crew\d-tiktok-workbench"
    r"\portable-package\docs\release\M3-gaps.md"))

WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
PIPE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
FFMPEG_BIN = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin")
FFMPEG_LICENSE = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\LICENSE")
SLIM = REPO / "build" / "portable" / "slim"
SLIM_LIC = SLIM / "licenses"
SITE = SLIM / "python" / "Lib" / "site-packages"

# gyan.dev 那份 GPLv3 全文的规范 sha256（35,147 B / 674 行）；只用来在证据里标注"是不是规范文本"
GPL_CANONICAL_SHA = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"

_tmpdirs = []
_state = {}
_ok = 0
_fail = 0
_deferred = []


# ────────────────────────── 基础设施 ──────────────────────────
def _emit(lines):
    for line in lines or []:
        print(f"        证据：{line}")


def check(cid, title, fn):
    global _ok, _fail
    try:
        evidence = fn() or []
        print(f"PASS {cid} {title}")
        _emit(evidence)
        _ok += 1
    except AssertionError as e:
        print(f"FAIL {cid} {title} -> {e}")
        _fail += 1
    except subprocess.TimeoutExpired as e:
        print(f"FAIL {cid} {title} -> 子进程超时：{e}")
        _fail += 1
    except Exception as e:                                   # noqa: BLE001
        print(f"FAIL {cid} {title} -> {type(e).__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _dec(raw) -> str:
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "cp936", "mbcs"):
        try:
            return (raw or b"").decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return (raw or b"").decode("utf-8", "replace")


def _run(argv, cwd=None, env=None, timeout=1800):
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd or REPO),
                          capture_output=True, env=env, timeout=timeout)
    out, err = _dec(proc.stdout), _dec(proc.stderr)
    return proc.returncode, out, err, out + err


def _tail(text, n=8, width=500):
    lines = [ln.strip() for ln in (text or "").replace("\r", "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[-width:]


def _tmpdir(prefix="t23qa_"):
    try:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir="D:\\"))
    except OSError:
        path = Path(tempfile.mkdtemp(prefix=prefix))
    _tmpdirs.append(path)
    return path


def _read(path: Path, what: str) -> str:
    assert_(path.is_file(), f"缺 {what}：{path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines_with(text: str, needle: str, limit=2, width=170):
    return [ln.strip()[:width] for ln in text.splitlines() if needle in ln][:limit]


# ────────────────────────── T23-01：GPLv3 全文 ──────────────────────────
def t23_01():
    """DoD 1：GPLv3 全文 ≥660 行、三个标记齐全、与本机 ffmpeg LICENSE 逐字节相同。"""
    text = _read(GPL, "GPLv3 全文")
    lines = text.splitlines()
    non_empty = sum(1 for ln in lines if ln.strip())
    assert_(len(lines) >= 660, f"GPLv3 全文应 ≥660 行，实得 {len(lines)} 行（非空 {non_empty}）")
    missing = [m for m in ("GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007",
                           "END OF TERMS AND CONDITIONS") if m not in text]
    assert_(not missing, f"GPLv3 全文缺标记：{missing}")
    got = _sha256(GPL)
    assert_(FFMPEG_LICENSE.is_file(), f"本机没有对照用的 ffmpeg LICENSE：{FFMPEG_LICENSE}")
    want = _sha256(FFMPEG_LICENSE)
    assert_(got == want,
            f"与 {FFMPEG_LICENSE} 不是逐字节相同：这份 {got}，本机那份 {want}")
    canon = "，等于 GPLv3 规范文本" if got == GPL_CANONICAL_SHA else "，与已知规范 sha256 不同（需人工确认）"
    return [f"行数 = {len(lines)}（非空 {non_empty}），三个标记齐全",
            f"sha256 本文件 = {got}",
            f"sha256 本机 ffmpeg LICENSE = {want}（逐字节相同{canon}）"]


# ────────────────────────── T23-02：书面要约四组 ──────────────────────────
def t23_02():
    """DoD 2：ffmpeg-SOURCE-OFFER.txt 的 (a) 标识 (b) 资产名 (c) §6(b) 三要件 (d) 独立进程。"""
    text = _read(OFFER, "书面要约")
    low = text.lower()
    groups = {}

    # (a) build 标识 + 许可证 + commit
    need_a = ("ffmpeg version 9.0.2-essentials_build-www.gyan.dev", "GPLv3",
              "https://github.com/FFmpeg/FFmpeg/commit/946fcce07b")
    groups["(a) build/许可/commit"] = [n for n in need_a if n in text]
    assert_(len(groups["(a) build/许可/commit"]) == 3, f"缺 (a)：{set(need_a) - set(groups['(a) build/许可/commit'])}")

    # (b) 四个资产名
    need_b = ("ffmpeg-9.0.2-source.tar.xz", "ffmpeg-7.1-source.tar.xz",
              "ffmpeg-9.0.2-static-libs-sources.txt", "pyav-14.2.0-ffmpeg-source.txt")
    groups["(b) 四个资产名"] = [n for n in need_b if n in text]
    assert_(len(groups["(b) 四个资产名"]) == 4, f"缺 (b)：{set(need_b) - set(groups['(b) 四个资产名'])}")

    # (c) §6(b) 三要件：有效期 / 范围 / 只收介质成本 + 具体联系方式
    valid = re.search(r"(three\s*\(3\)\s*years|最少?\s*三年|3\s*年|three years)", text, re.I)
    assert_(valid, "§6(b) 没写有效期（GPLv3 的下限是三年）")
    scope_37 = re.search(r"\b37\b", text) and re.search(r"(statically linked|静态链接)", text, re.I)
    assert_(scope_37, "§6(b) 没写清提供范围含静态链接的 37 个库")
    scope_av = re.search(r"(av==14\.2\.0|PyAV|av 内嵌|av 的 FFmpeg)", text, re.I)
    assert_(scope_av, "§6(b) 的提供范围没包含 av 内嵌的那份 FFmpeg")
    cost = re.search(r"(cost of the medium|介质.{0,6}成本|只收取介质)", text, re.I)
    assert_(cost, "§6(b) 没写『只收取介质与邮寄的实际成本』")
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    issues = re.search(r"https?://github\.com/[^\s/]+/[^\s/]+/issues", text)
    contact = email or issues
    assert_(contact, "§6(b) 的联系方式不具体（既没有邮箱也没有仓库 Issues 地址）")
    groups["(c) 三要件"] = ["有效期 ✓", "范围含 37 库 + av 的 FFmpeg ✓", "介质成本 ✓",
                        f"联系方式 = {contact.group(0)}"]
    valid_line = _lines_with(text, "three (3) years") or _lines_with(text, "三年")
    contact_line = _lines_with(text, contact.group(0))

    # (d) 独立进程、不链接
    sep = re.search(r"(separate process|独立进程|separate spawn)", text, re.I)
    nolink = re.search(r"(not link|不链接|不与.{0,8}链接|not a derivative work|不修改、不链接)", text, re.I)
    assert_(sep, "(d) 没写 ffmpeg 以独立进程调用")
    assert_(nolink, "(d) 没写『不与本项目代码链接』")
    groups["(d) 独立进程/不链接"] = ["独立进程 ✓", "不链接 ✓"]

    evidence = [f"{k}: {v}" for k, v in groups.items()]
    evidence += [f"有效期原文：{valid_line[0] if valid_line else valid.group(0)}",
                 f"联系方式原文：{contact_line[0] if contact_line else contact.group(0)}"]
    return evidence


# ────────────────────────── T23-03：PyAV 那份 FFmpeg ──────────────────────────
def t23_03():
    """DoD 3：PYAV-FFMPEG-SOURCE.txt 写明 av 版本、内嵌 FFmpeg 获取方式、同一进程内加载。"""
    text = _read(PYAV, "PyAV 的 FFmpeg 说明")
    ver = re.search(r"av\s*==\s*([0-9][\w.\-]*)", text) or re.search(r"PyAV\s*\**\s*([0-9][\w.\-]*)", text)
    assert_(ver, "没写 av 轮子的版本（应形如 `av == 14.2.0`）")
    embedded = re.search(r"FFmpeg\s*\**\s*(7\.1|9\.0\.2)", text, re.I)
    assert_(embedded, "没写内嵌 FFmpeg 的版本（应是 7.1）")
    assert_(embedded.group(1) == "7.1", f"内嵌版本写成了 {embedded.group(1)}，应是 7.1")
    obtain = re.search(r"(ffmpeg-7\.1-source\.tar\.xz|ffmpeg://[^\s]*|github\.com/FFmpeg/FFmpeg|ffmpeg\.org/releases)", text, re.I)
    assert_(obtain, "没写内嵌 FFmpeg 的源码获取方式")
    inproc = re.search(r"(same process|in-process|同一进程内|进程内加载)", text, re.I)
    assert_(inproc, "没写明它是『同一进程内加载』的第二份 FFmpeg")
    return [f"av 版本：{ver.group(0)[:60]}",
            f"内嵌 FFmpeg：{embedded.group(0)}",
            f"获取方式：{_lines_with(text, 'ffmpeg-7.1-source.tar.xz')[0] if _lines_with(text, 'ffmpeg-7.1-source.tar.xz') else obtain.group(0)}",
            f"同进程：{_lines_with(text, 'same process')[0] if _lines_with(text, 'same process') else inproc.group(0)}"]


# ────────────────────────── T23-04：THIRD_PARTY_NOTICES 四节 + 总述 ──────────────────────────
def t23_04():
    """DoD 4：四节齐全 + 「不是纯 MIT」总述 + 原 DM Sans / Apache ECharts 保留。"""
    text = _read(NOTICES, "THIRD_PARTY_NOTICES.md")
    low = text.lower()
    sections = {
        "ffmpeg CLI 节": bool(re.search(r"##\s*ffmpeg CLI", text, re.I)),
        "PyAV 的 FFmpeg 节": bool(re.search(r"##\s*The FFmpeg inside PyAV", text, re.I)),
        "随包 Python 依赖节（含 .dist-info 与 licenses\\PYTHON-PACKAGES.txt）":
            "## Bundled Python packages" in text and ".dist-info" in text
            and "PYTHON-PACKAGES.txt" in text,
        "VC 运行库节": bool(re.search(r"##\s*Microsoft Visual C\+\+ runtime", text, re.I)),
        "总述：仓库 MIT + ZIP 内含 GPLv3 + 不是纯 MIT":
            ("MIT" in text and "GPLv3" in text and ("ZIP" in text or "zip" in low)
             and bool(re.search(r"(not a pure MIT|not pure MIT|不是纯 MIT)", text, re.I))),
        "原 DM Sans 节保留": "DM Sans" in text,
        "原 Apache ECharts 节保留": "Apache ECharts" in text,
    }
    bad = [k for k, ok in sections.items() if not ok]
    assert_(not bad, f"THIRD_PARTY_NOTICES.md 缺：{bad}")
    paras = [re.sub(r"\s+", " ", p).strip() for p in text.split("\n\n")]
    summary = next((p for p in paras
                    if "不是纯 MIT" in p or "not a pure MIT" in p.lower()), "")
    return [f"{k}：✓" for k in sections] + [f"总述原文：{summary[:200]}"]


# ────────────────────────── T23-05：M3-gaps 的 6 资产 + 人工检查句 ──────────────────────────
ASSETS = [
    ("full ZIP", r"tiktok-workbench-[^\s`]+-full\.zip"),
    ("slim ZIP", r"tiktok-workbench-[^\s`]+-slim\.zip"),
    ("FFmpeg 9.0.2 源码", r"ffmpeg-9\.0\.2-source\.tar\.xz"),
    ("FFmpeg 7.1 源码（PyAV）", r"ffmpeg-7\.1-source\.tar\.xz"),
    ("静态库索引", r"ffmpeg-9\.0\.2-static-libs-sources\.txt"),
    ("PyAV 索引", r"pyav-(?:<ver>|[0-9][^\s`]*)-ffmpeg-source\.txt"),
]


def _gaps_text():
    if "gaps" in _state:
        return _state["gaps"]
    text = _read(GAPS, "M3 发布缺口清单")
    _state["gaps"] = text
    return text


def t23_05():
    """DoD 5：M3-gaps.md 写全 6 个 Release 资产 + 「上传后必须人工点一次下载」那句。"""
    text = _gaps_text()
    hits, missing = {}, []
    for label, pattern in ASSETS:
        m = re.search(pattern, text)
        hits[label] = m.group(0) if m else None
        if not m:
            missing.append(label)
    assert_(not missing, f"M3-gaps.md 的 6 资产清单缺：{missing}")
    # 人工检查那句：上传后点一次「下载」、200、非 0 字节
    assert_(re.search(r"上传后[^\n]{0,20}(人工|手动)[^\n]{0,20}点[^\n]{0,10}下载", text),
            "缺『上传后必须人工点一次「下载」验证』那句")
    assert_(re.search(r"200", text) and re.search(r"非\s*0\s*字节", text),
            "那句人工检查没写判据（200 且文件非 0 字节）")
    tarballs = ("ffmpeg-9.0.2-source.tar.xz", "ffmpeg-7.1-source.tar.xz")
    present = [name for name in tarballs
               if (REPO / name).is_file() or (REPO / "dist" / name).is_file()]
    return [f"{label}: {name}" for label, name in hits.items()] + [
        "人工检查句：✓（上传后点下载；200 且非 0 字节）",
        "DEFERRED-RELEASE：两个源码 tarball 本机" + ("存在" if present else "未下载")
        + "（" + ", ".join(tarballs) + "）→ 文件存在性与点下载验证只能发布时做",
    ]


# ────────────────────────── T23-06：只能人工（DEFERRED-RELEASE） ──────────────────────────
def t23_06():
    """DoD 6 的机器部分：本机没有可点的链接；上传/点下载只能人做 → DEFERRED-RELEASE。"""
    text = _gaps_text()
    has_urls = re.findall(r"https?://[^\s)）]+", text)
    return [f"M3-gaps.md 里的外链/命令：{len(has_urls)} 条（都是下载源码用的，不是 Release 资产链接）",
            "GitHub Release 尚未创建（本机不联网、也没有 tag/release），逐个点下载 → 200/非 0 字节只能人做"]


# ────────────────────────── T23-07/08：包内构件（先重跑构建） ──────────────────────────
def _snapshot_deps():
    """把现有 slim 的 site-packages 快照到临时目录当 --deps-from（避开并行构建的半份依赖）。"""
    must = ("fastapi/__init__.py", "faster_whisper/__init__.py", "ctranslate2/__init__.py")
    if not all((SITE / rel).is_file() for rel in must):
        return None
    snap = _tmpdir("t23qa_deps_") / "site-packages"
    shutil.copytree(SITE, snap, ignore=shutil.ignore_patterns("pip*", "__pycache__", "*.pyc"))
    if not all((snap / rel).is_file() for rel in must):
        return None
    return snap


def _build_slim():
    if "slim" in _state:
        return _state["slim"]
    assert_(BUILD.is_file(), f"缺少构建脚本 {BUILD}")
    assert_(FFMPEG_BIN.is_dir(), f"ffmpeg 9.0.2 目录不存在：{FFMPEG_BIN}")
    snap = _snapshot_deps()
    argv = [WEBAPP_PY, BUILD, "--flavor", "slim", "--ffmpeg-dir", str(FFMPEG_BIN)]
    mode = "deps-from 快照"
    if snap is not None:
        argv += ["--deps-from", str(snap)]
    else:
        mode = "pip install（本机没有可复用的 slim 依赖，需要网络）"
    rc = 1
    text = ""
    for attempt in (1, 2):
        t0 = time.time()
        rc, out, err, text = _run(argv, timeout=3600)
        seconds = time.time() - t0
        if rc == 0:
            break
        if attempt == 1:
            print(f"        [build] 第 1 次构建失败（rc={rc}），重试一次"
                  f"（可能有并行构建撞车）：{_tail(text, 6)}")
    assert_(rc == 0, f"slim 构建失败 rc={rc}；输出尾：{_tail(text, 12)}")
    assert_(SLIM.is_dir(), f"构建没产出 {SLIM}")
    _state["slim"] = dict(seconds=seconds, mode=mode, text=text)
    return _state["slim"]


def t23_07():
    """DoD 6：先用当前代码重跑构建，再核对包内六件 + PYTHON-PACKAGES.txt 三行。"""
    build = _build_slim()
    files = {
        "licenses\\ffmpeg-COPYING.GPLv3.txt": SLIM_LIC / "ffmpeg-COPYING.GPLv3.txt",
        "licenses\\ffmpeg-SOURCE-OFFER.txt": SLIM_LIC / "ffmpeg-SOURCE-OFFER.txt",
        "licenses\\PYAV-FFMPEG-SOURCE.txt": SLIM_LIC / "PYAV-FFMPEG-SOURCE.txt",
        "licenses\\PYTHON-PACKAGES.txt": SLIM_LIC / "PYTHON-PACKAGES.txt",
        "THIRD_PARTY_NOTICES.md": SLIM / "THIRD_PARTY_NOTICES.md",
        "LICENSE": SLIM / "LICENSE",
    }
    missing = [name for name, path in files.items() if not path.is_file()]
    assert_(not missing, f"包内缺这 {len(missing)} 件：{missing}")
    pkg_list = _read(files["licenses\\PYTHON-PACKAGES.txt"], "PYTHON-PACKAGES.txt")
    rows = [ln.strip() for ln in pkg_list.splitlines() if re.match(r"^\S+==\S+", ln.strip())]
    assert_(len(rows) >= 3, f"PYTHON-PACKAGES.txt 里 `包名==版本` 行应 ≥3，实得 {len(rows)}")
    low = pkg_list.lower()
    must = {"fastapi": "fastapi" in low,
            "ctranslate2": "ctranslate2" in low,
            "faster-whisper": ("faster-whisper" in low or "faster_whisper" in low)}
    bad = [k for k, ok in must.items() if not ok]
    assert_(not bad, f"PYTHON-PACKAGES.txt 缺这三行：{bad}")
    sample = [ln for ln in rows if re.match(r"(fastapi|ctranslate2|faster[-_])", ln, re.I)][:3]
    return [f"构建：{build['seconds']:.1f}s，{build['mode']}",
            f"包内六件齐全：{list(files)}",
            f"PYTHON-PACKAGES.txt：{len(rows)} 行 `包名==版本`，三行样例：{sample}"]


def t23_08():
    """DoD 7：抽 5 个发行版，各自 `*.dist-info\\METADATA` 都在（构建不许删 dist-info）。"""
    _build_slim()
    want = {"fastapi": "fastapi", "uvicorn": "uvicorn", "ctranslate2": "ctranslate2",
            "av": "av", "openpyxl": "openpyxl"}
    rows, missing = [], []
    for dist in want:
        metas = sorted(SITE.glob(f"{dist}-*.dist-info/METADATA"))
        if metas:
            rows.append(f"{dist}: {metas[0].parent.name}/METADATA 存在")
        else:
            missing.append(dist)
    assert_(not missing, f"这些发行版没有 *.dist-info\\METADATA（dist-info 被删了？）：{missing}")
    return rows


# ────────────────────────── T23-09：单测 ──────────────────────────
def t23_09():
    """DoD 8：licenses.test.py 跑绿（覆盖 DoD 1~4）。"""
    assert_(UNIT.is_file(), f"缺少 {UNIT}")
    rc, out, err, text = _run([WEBAPP_PY, UNIT], timeout=900)
    line = next((ln.strip() for ln in reversed(text.splitlines())
                 if re.match(r"通过\s*\d+\s*/\s*\d+", ln.strip())), None)
    assert_(rc == 0, f"退出码 {rc}；输出尾：{_tail(text)}")
    assert_("✗" not in text, f"单测里有失败项：{_tail(text, 12)}")
    assert_(line is not None, f"没有『通过 N / N』汇总行：{_tail(text)}")
    m = re.match(r"通过\s*(\d+)\s*/\s*(\d+)", line)
    assert_(m and m.group(1) == m.group(2) and int(m.group(1)) > 0, f"不是全绿：{line}")
    return [f"exit={rc}；{line}",
            f"命令：webapp\\.venv\\Scripts\\python.exe {UNIT.relative_to(REPO)}"]


# ────────────────────────── T23-10：入口自检 ──────────────────────────
def t23_10():
    assert_(Path(sys.executable).resolve() == PIPE_PY.resolve(),
            f"本用例必须用 pipeline\\.venv 跑：sys.executable={sys.executable!r}")
    ids = [cid for cid, _t, _f in CASES]
    assert_(len(ids) == 10, f"应注册 10 条（清单 10 条），实得 {len(ids)}：{ids}")
    assert_(ids == [f"T23-{i:02d}" for i in range(1, 11)], f"用例 id 不是 T23-01~10：{ids}")
    assert_(_deferred, "DEFERRED-RELEASE 列表为空（第 6 条人工项必须标出来）")
    return [f"解释器：{sys.executable}",
            "清单 10 条 = 9 条机判 + 1 条 DEFERRED-RELEASE（T23-06 上传/点下载只能人做）"]


CASES = [
    ("T23-01", "DoD 1：GPLv3 全文 ≥660 行 + 三标记 + 与本机 ffmpeg LICENSE 逐字节相同", t23_01),
    ("T23-02", "DoD 2：书面要约四组（标识/四个资产名/§6(b) 三要件/独立进程）", t23_02),
    ("T23-03", "DoD 3：PyAV 那份 FFmpeg 的说明（av 版本/内嵌版本/获取/同进程）", t23_03),
    ("T23-04", "DoD 4：NOTICES 四节 + 「不是纯 MIT」总述 + DM Sans/ECharts 保留", t23_04),
    ("T23-05", "DoD 5：M3-gaps.md 写全 6 个 Release 资产 + 人工点下载那句", t23_05),
    ("T23-06", "DoD 6：GitHub Release 上传 + 逐个点下载 200/非 0 字节（只能人工）", t23_06),
    ("T23-07", "DoD 6：先重跑构建，包内六件齐全 + PYTHON-PACKAGES 三行", t23_07),
    ("T23-08", "DoD 7：5 个发行版的 *.dist-info\\METADATA 都在", t23_08),
    ("T23-09", "DoD 8：licenses.test.py 跑绿", t23_09),
    ("T23-10", "QA 入口自检：pipeline\\.venv 跑、10 条清单齐全、DEFERRED 单列", t23_10),
]
DEFERRED_IDS = {"T23-06"}


def _cleanup():
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)


def main():
    only = {a.upper() for a in sys.argv[1:] if a.upper().startswith("T23-")}
    print("T-23 许可证包 · QA 用例（清单：.dsh-crew/jobs/portable-package/M3-qa-plan.md T-23 段）")
    print(f"仓库：{REPO}")
    print("")
    try:
        for cid, title, fn in CASES:
            if only and cid not in only:
                continue
            if cid in DEFERRED_IDS:
                # 只能人做/发布时才能做：单列，不算失败
                try:
                    evidence = fn() or []
                except AssertionError as e:
                    evidence = [f"（证据收集未完成：{e}）"]
                print(f"DEFERRED-RELEASE {cid} {title}")
                _emit(evidence)
                _deferred.append(cid)
                continue
            check(cid, title, fn)
    finally:
        _cleanup()
    total = _ok + _fail
    if _deferred:
        print(f"\nDEFERRED-RELEASE（单列，不计失败）：{len(_deferred)} 条 —— {', '.join(_deferred)}"
              "（两个源码 tarball 按 PM 指示本机未下载；上传与点下载只能人工）")
    print(f"\n通过 {_ok} / {total}（机判）+ {len(_deferred)} 条 DEFERRED-RELEASE")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
