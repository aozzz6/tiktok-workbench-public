# -*- coding: utf-8 -*-
r"""qa/T-26/run.py —— T-26「读者文档：README / README-zh / CHANGELOG / DEPLOY_GUIDE」的 QA 用例

用例清单：`.dsh-crew/jobs/portable-package/M3-qa-plan.md` 的 T-26 段（C-01 ~ C-08，一条一条照写）。
DoD 来源：本作业 `docs/tasks/tasks.md` 里 T-26 的 DoD 第 1~7 条。

跑法（清单指定的解释器）：
    cd D:\tiktok-workbench
    pipeline\.venv\Scripts\python.exe qa\T-26\run.py

边界（硬约束）：
  · 只读四份文档 + 跑文档里写的两条命令；**不修改产品代码、不改这四份文档**；
  · 文档断言全部由本文件独立重算（不拿 `webapp\tests\readme_portable.test.py` 的结论当唯一证据）；
  · 命令那条在仓库根真跑；`make_zips.py` 允许加 `--flavor slim` 省时间；ZIP 输出改到临时目录
    （`--out`），避免覆盖 `dist\` 里 T-22 的 full/slim ZIP 与 SHA256SUMS；
  · 不碰 8789 上用户的工作台（本文件自身不启动任何服务；packaging 测试若起服务由它们自己负责）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── 控制台/管道编码：被管道接走时用 UTF-8 ───────────────────────────────────────
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:                        # noqa: BLE001
        pass

# ── 路径：全部按本文件位置推，与 cwd 无关 ────────────────────────────────────────
QA_DIR = Path(__file__).resolve().parent                 # ...\qa\T-26
REPO = QA_DIR.parents[1]                                 # 仓库根
README_ZH = REPO / "README-zh.md"
README_EN = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"
DEPLOY = REPO / "DEPLOY_GUIDE.md"
VERSION_FILE = REPO / "VERSION"
UNIT = REPO / "webapp" / "tests" / "readme_portable.test.py"
WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"

BUILD_CMD = r"webapp\.venv\Scripts\python.exe packaging\make_zips.py"
TESTS_CMD_PREFIX = "for %f in (packaging\\tests\\*.test.py)"
ISO_DATE = "2026-11-05"                                  # DoD 原文的日期，按原文判（不是跑今天的日期）
FFMPEG_CANDIDATES = [
    REPO / "pipeline" / "ffmpeg_bin",
    Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin"),
]

_ok = 0
_fail = 0
_failed_ids: list[str] = []


# ═════════════════════════════ 小工具 ═════════════════════════════
def assert_(cond, msg="断言失败"):
    if not cond:
        raise AssertionError(msg)


def _emit(text):
    for line in (text or "").splitlines():
        if line.strip():
            print("        " + line.rstrip())


def check(cid, name, fn):
    global _ok, _fail
    try:
        ev = fn() or []
    except AssertionError as exc:
        print("FAIL " + cid + " " + name + " -> " + str(exc))
        _fail += 1
        _failed_ids.append(cid)
    except Exception as exc:                 # noqa: BLE001
        print("FAIL " + cid + " " + name + " -> " + type(exc).__name__ + ": " + str(exc))
        _fail += 1
        _failed_ids.append(cid)
    else:
        print("ok " + cid + " " + name)
        _emit("\n".join(ev) if isinstance(ev, list) else str(ev))
        _ok += 1


def read(path: Path) -> str:
    assert_(path.is_file(), "找不到文件：%s" % path)
    return path.read_text(encoding="utf-8", errors="replace")


def clean_env(**extra):
    """剥掉 WB_* / HF_* / PYTHON*（父会话可能残留），只留本用例显式给的。"""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("WB_", "HF_", "PYTHON"))}
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def run(cmd, timeout, cwd=REPO, env=None):
    """跑一条命令（argv 列表），返回 (退出码, 合并输出, 用时)。超时也转成「有原因的红」。"""
    started = time.time()
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                              env=env or clean_env(), capture_output=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError("命令 %.0f 秒没跑完（超时）：%s"
                             % (timeout, " ".join(str(c) for c in cmd)))
    except FileNotFoundError as exc:
        raise AssertionError("命令不可用：%s（%s）" % (cmd[0], exc))
    out = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
    return proc.returncode, out, time.time() - started


def run_cmdline(cmdline: str, timeout, cwd=REPO, env=None):
    """把 README 里的 cmd 行**原样**交给 cmd.exe（用字符串而不是 argv 列表：
    argv 列表会让 subprocess 把内层引号转义成 \\"，cmd 再把引号当文件名的一部分，
    实测 `for %f ... "%f"` 会变成 python 打不开 `"packaging\\...py"`）。"""
    started = time.time()
    try:
        proc = subprocess.run("cmd /c " + cmdline, cwd=str(cwd), env=env or clean_env(),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError("命令 %.0f 秒没跑完（超时）：cmd /c %s" % (timeout, cmdline))
    out = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
    return proc.returncode, out, time.time() - started


def tail(text, n=6):
    got = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(got[-n:])


def exact_lines(text: str, target: str):
    return [ln.strip() for ln in text.splitlines() if ln.strip() == target]


def bullet(text: str, anchor: str):
    """取以 anchor（strip 后）开头的那条 markdown `- ` 项目符号及其续行。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(anchor):
            out = [ln]
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s.startswith("- ") or s.startswith("## ") or not s:
                    break
                out.append(nxt)
            return "\n".join(out)
    return ""


def first_line_with(text: str, *needles):
    for lineno, line in enumerate(text.splitlines(), 1):
        if all(n in line for n in needles):
            return lineno, line.strip()
    return None, None


# ═════════════════════════════ C-01 ═════════════════════════════
# 清单 1 / DoD 1：工程师的读者文档单测退出 0。不只看退出码，还要求汇总为「通过 N / N」且 N>=10。
def case_01_unit_test():
    assert_(UNIT.is_file(), "找不到工程师单测：%s" % UNIT)
    rc, out, elapsed = run([str(WEBAPP_PY), r"webapp\tests\readme_portable.test.py"], timeout=300)
    summary = [ln.strip() for ln in out.splitlines() if re.match(r"^通过\s+\d+\s*/\s*\d+", ln.strip())]
    assert_(rc == 0, "单测退出码 %d；输出尾部：\n%s" % (rc, tail(out)))
    assert_(summary, "单测输出里没有「通过 N / M」汇总行；尾部：\n%s" % tail(out))
    m = re.search(r"通过\s+(\d+)\s*/\s*(\d+)", summary[-1])
    got, total = int(m.group(1)), int(m.group(2))
    assert_(got == total, "单测没有全绿：%d / %d" % (got, total))
    assert_(total >= 10, "单测只有 %d 条检查（工程师声明 10 条），像是检查被删/被跳过" % total)
    assert_("FAIL" not in out, "单测输出里出现 FAIL 行（退出码却是 0，自相矛盾）")
    return ["证据：cd 仓库根 && webapp\\tests\\readme_portable.test.py -> exit=%d，通过 %d / %d（%.2fs）"
            % (rc, got, total, elapsed)]


# ═════════════════════════════ C-02 ═════════════════════════════
# 清单 2 / DoD 1：两份 README 的 portable 一节 —— 同一条构建命令、开发者检出流程不变、
# packaging 测试的跑法都在。
def case_02_readme_portable_sections():
    zh, en = read(README_ZH), read(README_EN)

    assert_(any(ln.startswith("## ") and "绿色版" in ln for ln in zh.splitlines()),
            "README-zh.md 没有「绿色版」一节（## 标题里找不到）")
    assert_(any(ln.startswith("## ") and "Portable package" in ln for ln in en.splitlines()),
            "README.md 没有「Portable package」一节（## 标题里找不到）")

    zh_cmd = exact_lines(zh, BUILD_CMD)
    en_cmd = exact_lines(en, BUILD_CMD)
    assert_(len(zh_cmd) == 1,
            "README-zh.md 里「%s」应恰好 1 行，实得 %d 行" % (BUILD_CMD, len(zh_cmd)))
    assert_(len(en_cmd) == 1,
            "README.md 里「%s」应恰好 1 行，实得 %d 行" % (BUILD_CMD, len(en_cmd)))
    assert_(zh_cmd == en_cmd, "两份 README 的构建命令行不一致：zh=%r / en=%r" % (zh_cmd, en_cmd))

    for label, text, needles in (
        ("README-zh.md", zh, ["开发者检出流程不变", "两个 venv", "install.bat"]),
        ("README.md", en, ["developer checkout flow is unchanged", "two venvs", "install.bat"]),
    ):
        low = text.lower()
        for needle in needles:
            assert_(needle.lower() in low, "%s 没有说明开发者检出流程不变：缺「%s」" % (label, needle))

    assert_("packaging\\tests\\*.test.py" in zh,
            "README-zh.md 没提 packaging\\tests\\*.test.py 的跑法")
    assert_("packaging\\tests\\*.test.py" in en,
            "README.md 没提 packaging\\tests\\*.test.py 的跑法")
    zh_for = [ln.strip() for ln in zh.splitlines() if ln.strip().startswith(TESTS_CMD_PREFIX)]
    en_for = [ln.strip() for ln in en.splitlines() if ln.strip().startswith(TESTS_CMD_PREFIX)]
    assert_(len(zh_for) == 1 and len(en_for) == 1,
            "两份 README 都要有且只有 1 条 `for %%f in (packaging\\tests\\*.test.py)` 命令行"
            "（zh=%d / en=%d）" % (len(zh_for), len(en_for)))
    assert_(zh_for == en_for, "两份 README 的 packaging 测试命令行不一致：zh=%r / en=%r"
            % (zh_for, en_for))

    for label, text in (("README-zh.md", zh), ("README.md", en)):
        assert_("webapp\\.venv" in text and "pipeline\\.venv" in text,
                "%s 应同时点名 webapp\\.venv 与 pipeline\\.venv（两个 venv）" % label)

    # 2026-11-09 修正后又加了三条事实断言（Lead 要求）：包里带 yt-dlp、离线/联网两栏、反推在联网栏。
    assert_("WB_YTDLP" in zh and "WB_YTDLP" in en,
            "两份 README 都应写「工作台只认 WB_YTDLP」")
    zh_off = bullet(zh, "- **离线可用**")
    zh_on = bullet(zh, "- **需要联网")
    en_off = bullet(en, "- **Works offline:**")
    en_on = bullet(en, "- **Needs internet")
    assert_(zh_off and zh_on, "README-zh.md 应有「离线可用」与「需要联网」两条列表项")
    assert_(en_off and en_on, "README.md 应有「Works offline」与「Needs internet」两条列表项")
    assert_("反推" not in zh_off, "README-zh.md 把「反推」列进了离线可用（反推要远程视觉模型）")
    assert_("反推" in zh_on, "README-zh.md 的「需要联网」那条没有点名反推")
    assert_("reverse-engineering" not in en_off.lower(),
            "README.md 把 reverse-engineering 列进了离线可用（反推要远程视觉模型）")
    assert_("reverse-engineering" in en_on.lower(),
            "README.md 的「Needs internet」那条没有点名 reverse-engineering")
    assert_("yt-dlp.exe" in zh and "yt-dlp.exe" in en,
            "两份 README 都应说明包里的 yt-dlp.exe 位置（python\\Lib\\site-packages\\bin\\yt-dlp.exe）")
    assert_("包里没有 yt-dlp.exe，粘贴" not in zh,
            "README-zh.md 还在说「包里没有 yt-dlp.exe」（包内实际带了）")
    assert_("ships no `yt-dlp.exe`" not in en,
            "README.md 还在说「the package ships no yt-dlp.exe」（包内实际带了）")

    return ["证据：README-zh.md 节标题「%s」" % next(ln.strip() for ln in zh.splitlines()
                                                    if ln.startswith("## ") and "绿色版" in ln),
            "证据：README.md 节标题「%s」" % next(ln.strip() for ln in en.splitlines()
                                                 if ln.startswith("## ") and "Portable package" in ln),
            "证据：两份的构建命令行逐字相同 -> %s" % BUILD_CMD,
            "证据：两份都写「开发者检出流程不变（两个 venv + install.bat）」；packaging 测试命令逐字相同 -> %s"
            % zh_for[0],
            "证据：两份都写 WB_YTDLP；离线栏只含台账/对标/转写，联网栏点名反推（zh 反推 / en reverse-engineering）"]


# ═════════════════════════════ C-03 ═════════════════════════════
# 清单 3 / DoD 1：CHANGELOG 新节标题含 VERSION 与 2026-11-05；正文含三处事实。
def case_03_changelog_entry():
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert_(version, "VERSION 文件是空的")
    text = read(CHANGELOG)
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert_(heads, "CHANGELOG.md 里没有任何 `## ` 小节")
    first = heads[0]
    heading = lines[first].strip()
    assert_(version in heading, "CHANGELOG 第一个小节标题里没有 VERSION=%r：%r" % (version, heading))
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", heading)
    assert_(dates, "CHANGELOG 第一个小节标题里没有日期：%r" % heading)
    assert_(ISO_DATE in heading,
            "CHANGELOG 新节标题的日期是 %s，DoD 原文要求 %s（今天是 %s，但判据按 DoD 原文）"
            % (", ".join(dates), ISO_DATE, time.strftime("%Y-%m-%d")))
    end = heads[1] if len(heads) > 1 else len(lines)
    body = "\n".join(lines[first + 1:end])
    for needle in ("两个 ZIP", "（完整", "精简"):
        assert_(needle in body, "CHANGELOG 新节正文缺「两个 ZIP（完整/精简）」的一部分：缺 %r" % needle)
    assert_("包内不自带 LLM key" in body, "CHANGELOG 新节正文缺「包内不自带 LLM key」")
    assert_("包内不含 PyTorch" in body, "CHANGELOG 新节正文缺「包内不含 PyTorch」")
    # 2026-11-09：前言从「按日期编、不编号」改成「自 0.1.0 起也带 VERSION 号」，断言别再退回旧话术。
    preamble = "\n".join(lines[:6])
    assert_("0.1.0" in preamble and "VERSION" in preamble,
            "CHANGELOG.md 前言没有说明「自 0.1.0 起带 VERSION 号」：%r" % preamble)
    assert_("dated instead of numbered" not in preamble,
            "CHANGELOG.md 前言还是旧的「entries are dated instead of numbered」")
    return ["证据：CHANGELOG 首节标题 -> %s" % heading,
            "证据：正文命中「两个 ZIP（完整…精简…）」「包内不自带 LLM key」「包内不含 PyTorch」",
            "证据：前言已更新 -> %s" % next(ln.strip() for ln in lines[:6] if "VERSION" in ln)]


# ═════════════════════════════ C-04 ═════════════════════════════
# 清单 4 / DoD 2：许可表述 —— 仓库源码 MIT；ZIP 内含 GPLv3 的 ffmpeg 与 PyAV 的 FFmpeg；
# 见 THIRD_PARTY_NOTICES.md；不许再说“整个包/整个项目是 MIT”。
def _paragraph(text: str, anchor: str, span: int = 7):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if anchor in ln:
            return "\n".join(lines[i:i + span])
    return ""


def case_04_license_wording():
    zh, en = read(README_ZH), read(README_EN)
    zhp = _paragraph(zh, "仓库源码")
    assert_(zhp, "README-zh.md 的许可段找不到（没有含「仓库源码」的那段）")
    enp = _paragraph(en, "Licensing.")
    assert_(enp, "README.md 的许可段找不到（没有含「Licensing.」的那段）")
    for label, para, needles in (
        ("README-zh.md", zhp, ["MIT", "GPL", "THIRD_PARTY_NOTICES", "ffmpeg", "PyAV"]),
        ("README.md", enp, ["MIT", "GPL", "THIRD_PARTY_NOTICES", "ffmpeg", "PyAV"]),
    ):
        low = para.lower()
        for needle in needles:
            assert_(needle.lower() in low, "%s 许可段缺「%s」" % (label, needle))

    wrong = re.compile(r"(整个|整体)[^。\n]{0,30}MIT|(whole|entire)[^.\n]{0,40}MIT", re.IGNORECASE)
    for label, text in (("README-zh.md", zh), ("README.md", en)):
        hits = [ln.strip() for ln in text.splitlines() if wrong.search(ln)]
        assert_(not hits, "%s 还在说「整个包/整个项目是 MIT」：%s" % (label, hits[:3]))
    return ["证据：README-zh.md 许可段含 MIT / GPLv3 / ffmpeg / PyAV / THIRD_PARTY_NOTICES",
            "证据：README.md 许可段含 MIT / GPLv3 / ffmpeg / PyAV / THIRD_PARTY_NOTICES",
            "证据：「整个…MIT / whole|entire…MIT」命中 0 行"]


# ═════════════════════════════ C-05 ═════════════════════════════
# 清单 5 / DoD 3：README-zh.md 保留 `**Linux / macOS 完全没验证过。**` 原句；
# 新增 run-all 恒红那句。
def case_05_known_gaps_kept():
    zh = read(README_ZH)
    assert_("**Linux / macOS 完全没验证过。**" in zh,
            "README-zh.md 里 `**Linux / macOS 完全没验证过。**` 原句不在了（被改写/删掉）")
    lineno, line = first_line_with(zh, "run-all.ps1", "恒红", "#21")
    assert_(line is not None,
            "README-zh.md 里没有「qa\\run-all.ps1 在本仓库恒红…（qa\\gaps.md #21）」这句")
    assert_("qa\\gaps.md" in line or "qa/gaps.md" in line,
            "run-all 恒红那句没有指向 qa\\gaps.md：%r" % line)
    return ["证据：README-zh.md:%d 原句仍在 -> %r" % (
        next(i for i, ln in enumerate(zh.splitlines(), 1)
             if ln.strip() == "构建）。**Linux / macOS 完全没验证过。**"
             or "**Linux / macOS 完全没验证过。**" in ln),
        "**Linux / macOS 完全没验证过。**"),
            "证据：README-zh.md:%d 新增句 -> %s" % (lineno, line)]


# ═════════════════════════════ C-06 ═════════════════════════════
# 清单 6 / DoD 4：从 README-zh.md 抽出的两条命令在仓库根原样执行，都退出 0。
#   · 构建命令加 `--flavor slim`（DoD 允许省时间）+ `--out` 指到临时目录
#     （只改 ZIP 落点，避免覆盖 dist\ 里 T-22 的产物；stage 仍真实重建）；
#   · ffmpeg 用 README rem 里写明的 `WB_FFMPEG_DIR` 形式给（本机没有 pipeline\ffmpeg_bin）。
def _ffmpeg_dir():
    for cand in FFMPEG_CANDIDATES:
        if (cand / "ffmpeg.exe").is_file() and (cand / "ffprobe.exe").is_file():
            return cand
    return None


def case_06_commands_run():
    zh = read(README_ZH)
    build_lines = exact_lines(zh, BUILD_CMD)
    assert_(len(build_lines) == 1, "README-zh.md 里构建命令行数量异常（%d 行）" % len(build_lines))
    tests_lines = [ln.strip() for ln in zh.splitlines() if ln.strip().startswith(TESTS_CMD_PREFIX)]
    assert_(len(tests_lines) == 1, "README-zh.md 里 packaging 测试命令行数量异常（%d 行）" % len(tests_lines))
    tests_line = tests_lines[0]

    ffmpeg = _ffmpeg_dir()
    assert_(ffmpeg is not None,
            "README 里给的三条 ffmpeg 线索都不成立：pipeline\\ffmpeg_bin 不存在，"
            "WB_FFMPEG_DIR 没设，%s 也不存在 —— 构建命令无法执行" % FFMPEG_CANDIDATES[1])

    out_dir = Path(tempfile.mkdtemp(prefix="qa_t26_dist_"))
    try:
        # ① 构建 slim（README 的构建命令 + --flavor slim + --out 临时目录）
        cmd1 = [str(WEBAPP_PY), r"packaging\make_zips.py", "--flavor", "slim", "--out", str(out_dir)]
        env1 = clean_env(WB_FFMPEG_DIR=str(ffmpeg))
        rc1, out1, sec1 = run(cmd1, timeout=1800, env=env1)
        zips = sorted(out_dir.glob("*.zip"))
        assert_(rc1 == 0, "构建命令退出码 %d（读 README-zh.md 那条 + --flavor slim）；尾部：\n%s"
                          % (rc1, tail(out1, 8)))
        assert_("zip_bytes=" in out1, "构建命令退出 0 但输出里没有 zip_bytes= 汇总：\n%s" % tail(out1))
        assert_(zips, "构建命令退出 0 但 %s 里没有 ZIP" % out_dir)
        build_ev = ("证据①：%s --flavor slim --out <临时>（WB_FFMPEG_DIR=%s）-> exit=%d（%.0fs）；%s"
                    % (BUILD_CMD, ffmpeg, rc1, sec1,
                       "；".join("%s=%d B" % (z.name, z.stat().st_size) for z in zips)))

        # ② packaging 测试（README-zh.md 里那条 `for %f ...`，原样交给 cmd /c 执行）。
        #    README 在同一个小节下面还有一条 note：包上用例按 `build\portable\t18` 找包，
        #    「没有就先建一次」——launchers.test.py 要 `build\portable\t18\slim`。缺了就按
        #    note 里的命令原样建一次（这是文档自己写的步骤），再跑测试命令。
        t18 = REPO / "build" / "portable" / "t18" / "slim"
        pre_ev = []
        if not t18.is_dir():
            cmd_t18 = [str(WEBAPP_PY), r"packaging\build_portable.py", "--flavor", "slim",
                       "--out", r"build\portable\t18", "--ffmpeg-dir", str(ffmpeg)]
            rc0, out0, sec0 = run(cmd_t18, timeout=1800, env=clean_env())
            assert_(rc0 == 0, "README note 里的 t18 前置构建退出码 %d；尾部：\n%s"
                              % (rc0, tail(out0, 8)))
            pre_ev.append("证据①.5：build\\portable\\t18\\slim 缺失，按 README note 原样构建 "
                          "`packaging\\build_portable.py --flavor slim --out build\\portable\\t18 "
                          "--ffmpeg-dir <%s>` -> exit=%d（%.0fs）" % (ffmpeg, rc0, sec0))
        rc2, out2, sec2 = run_cmdline(tests_line, timeout=2400)
        assert_(rc2 == 0,
                "packaging 测试命令退出码 %d；最后几行：\n%s" % (rc2, tail(out2, 10)))
        test_ev = ("证据②：%s -> exit=%d（%.0fs）；尾部：%s"
                   % (tests_line, rc2, sec2, tail(out2, 3).replace("\n", " | ")))
        return [build_ev] + pre_ev + [test_ev]
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ═════════════════════════════ C-07 ═════════════════════════════
# 清单 7 / DoD 5+6：DEPLOY_GUIDE 的过期点（8787、15 列、作者路径）与顶部「绿色版见 README」。
def case_07_deploy_guide():
    text = read(DEPLOY)
    assert_("8787" not in text, "DEPLOY_GUIDE.md 里还有旧端口 8787（%d 处）" % text.count("8787"))
    assert_("8789" in text, "DEPLOY_GUIDE.md 里没有新端口 8789")
    assert_("14 列" in text, "DEPLOY_GUIDE.md 里没有「14 列」")
    assert_("15 列" not in text, "DEPLOY_GUIDE.md 里还有「15 列」")
    for bad in ("D:/1/", "D:\\1\\", "workbuddy_project", "C:\\Users", "Administrator", "/home/"):
        assert_(bad.lower() not in text.lower(),
                "DEPLOY_GUIDE.md 里还有作者机器路径/主机名：%r" % bad)
    assert_("ledger\\" in text, "DEPLOY_GUIDE.md 没有指向包内 ledger\\")
    top = "\n".join(text.splitlines()[:20])
    assert_("绿色版" in top and "README" in top and ("Portable" in top or "portable" in top),
            "DEPLOY_GUIDE.md 顶部没有「给使用者的绿色版见 README 的 portable 一节」那段")
    # 2026-11-09：第 4 步从「裸跑 app.py 却写 8789」改成两条明路（双击 bat / set WB_PORT=8789）。
    sec4 = _paragraph(text, "### 4. 启动与验证", span=14)
    assert_(sec4, "DEPLOY_GUIDE.md 找不到「### 4. 启动与验证」一节")
    assert_("启动工作台.bat" in sec4,
            "DEPLOY_GUIDE.md 第 4 步没有给「双击 启动工作台.bat」这条推荐路")
    assert_("WB_PORT=8789" in sec4,
            "DEPLOY_GUIDE.md 第 4 步手动起服务没有 `set WB_PORT=8789`（app.py 默认不是 8789）")
    return ["证据：8787 命中 0；8789 命中 %d；「14 列」命中 %d；「15 列」命中 0"
            % (text.count("8789"), text.count("14 列")),
            "证据：作者路径（D:/1/、workbuddy_project、C:\\Users、Administrator、/home/）命中 0；ledger\\ 命中 %d"
            % text.count("ledger\\"),
            "证据：顶部第 3-5 行 -> %s" % " / ".join(ln.strip() for ln in text.splitlines()[2:5] if ln.strip())[:200],
            "证据：第 4 步含「启动工作台.bat」与「set WB_PORT=8789」"]


# ═════════════════════════════ C-08 ═════════════════════════════
# 清单 8：QA 入口 —— 清单指定的解释器/路径，用例表齐全，前面全绿（退出码 0）。
def case_08_qa_entry():
    assert_((QA_DIR / "run.py").resolve() == Path(__file__).resolve(),
            "本文件不在清单位置 qa\\T-26\\run.py：%s" % Path(__file__).resolve())
    assert_(Path(sys.executable).resolve() == PIPELINE_PY.resolve(),
            "本入口必须用清单指定的解释器跑：期望 %s，实得 %s" % (PIPELINE_PY, sys.executable))
    ids = [cid for cid, _name, _fn in CASES]
    expected = ["C-01", "C-02", "C-03", "C-04", "C-05", "C-06", "C-07", "C-08"]
    assert_(ids == expected, "用例表不是清单的 C-01~C-08：%s" % ids)
    if _failed_ids:
        raise AssertionError("前面有用例失败，QA 入口不能算绿：%s" % ", ".join(_failed_ids))
    return ["证据：qa\\T-26\\run.py + %s（清单指定解释器）" % sys.executable,
            "证据：本次进程退出码 0（C-01~C-07 全绿，见上面的 ok 行与汇总）"]


# ═════════════════════════════ 附注（不是 DoD 判据） ═════════════════════════════
def print_notes():
    """只打印、不判红：把文档 2026-11-09 修正后的三条说法与产品实际正向对一遍。"""
    notes = []
    for stage in ("slim", "full"):
        exe = (REPO / "build" / "portable" / stage / "python" / "Lib" / "site-packages"
               / "bin" / "yt-dlp.exe")
        if exe.is_file():
            notes.append("文档说「包里带了 yt-dlp.exe」—— 实测确实在：%s；与 "
                         "webapp\\tk_reverse.py:50 只认 WB_YTDLP 的说法一致。" % exe)
            break
    tk = (REPO / "webapp" / "tk_reverse.py")
    if tk.is_file():
        src = tk.read_text(encoding="utf-8", errors="replace")
        if "supportsImages" in src and "MODELS_JSON" in src:
            notes.append("文档说「反推要远程视觉模型、不能离线」—— webapp\\tk_reverse.py 确实要求 "
                         "models.json 里 supportsImages=true 的条目，一致。")
    app = (REPO / "webapp" / "app.py")
    if app.is_file() and "def _listen_port(default: int = 8787)" in app.read_text(encoding="utf-8", errors="replace"):
        notes.append("文档说「app.py 的默认端口不是 8789」—— webapp\\app.py:3410 默认 8787；"
                     "DEPLOY_GUIDE 第 4 步已要求 set WB_PORT=8789，一致。")
    if notes:
        print("")
        print("附注（不是 DoD 判据，正向核对文档新说法与产品）：")
        for note in notes:
            print("  [note] " + note)


# ═════════════════════════════ main ═════════════════════════════
CASES = [
    ("C-01", "工程师的读者文档单测通过（通过 N/N，N>=10）", case_01_unit_test),
    ("C-02", "两份 README 的 portable 一节（同一构建命令 / 检出流程不变 / packaging 测试跑法 / "
             "WB_YTDLP + 离线/联网两栏且反推在联网栏）", case_02_readme_portable_sections),
    ("C-03", "CHANGELOG 新节（VERSION + 2026-11-05 + 三处事实 + 前言已更新）", case_03_changelog_entry),
    ("C-04", "许可表述（源码 MIT / ZIP 含 GPLv3 ffmpeg+PyAV / THIRD_PARTY_NOTICES / 无「整个包是 MIT」）",
     case_04_license_wording),
    ("C-05", "README-zh 保留 Linux/macOS 原句 + 新增 run-all 恒红那句", case_05_known_gaps_kept),
    ("C-06", "从 README-zh 抽出的两条命令在仓库根真跑，退出码 0", case_06_commands_run),
    ("C-07", "DEPLOY_GUIDE 过期点（8787 / 15 列 / 作者路径）+ 顶部指向 + 第 4 步启动工作台.bat/WB_PORT=8789",
     case_07_deploy_guide),
    ("C-08", "QA 入口（清单解释器 + 用例表齐全 + 全绿）", case_08_qa_entry),
]


def main():
    print("T-26 用例（qa/T-26/run.py，清单见 .dsh-crew/jobs/portable-package/M3-qa-plan.md 的 T-26 段）：")
    print("仓库=%s" % REPO)
    print("解释器=%s" % sys.executable)
    print("DoD 日期判据=%s（按 DoD 原文，不按今天）" % ISO_DATE)
    print("")
    started = time.time()
    for cid, name, fn in CASES:
        check(cid, name, fn)
    print_notes()
    total = _ok + _fail
    print("")
    print("结果：通过 %d / %d（失败用例：%s）"
          % (_ok, total, ", ".join(_failed_ids) if _failed_ids else "无"))
    print("T-26 RESULT: %d/%d ok, exit %d（用时 %d 秒）"
          % (_ok, total, 0 if _fail == 0 else 1, round(time.time() - started)))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
