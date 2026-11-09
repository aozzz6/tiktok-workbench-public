#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qa/T-24/run.py — T-24「使用者说明书」的独立验收。

用例清单：`.dsh-crew/jobs/portable-package/M3-qa-plan.md` 的 T-24 段（1..9 条）。
DoD 来源：docs/tasks/tasks.md 的 T-24（DoD 7 真人抽检只能人工）。

跑法（按清单）：
    cd D:\\tiktok-workbench
    pipeline\\.venv\\Scripts\\python.exe qa\\T-24\\run.py

判据来源（独立于 packaging/tests/manual.test.py）：
  * 8 个主题（二级标题）；
  * 4 个具体事实（换端口、升级保留数据、日志位置、版本号）；
  * 3 个已知限制（yt-dlp/本地视频、API key/联网/models.json、代理/离线）；
  * ≥5 条常见故障（杀软/python.exe、端口、模型下载+HF_ENDPOINT、VC 运行库、中文/空格路径）；
  * 禁项（作者绝对路径、D:\\tiktok-workbench、C:\\Users、教用户双击 python\\python.exe、pip install）；
  * 反引号里的包内路径在**新构建的 slim 包**里真实存在（占位/通配/运行时文件按白名单单列）。
  * 第 8 条（真人 5 分钟抽检）只能人工：打印 DEFERRED-HUMAN，不算失败。

构建输出到私有的 `build\\portable\\t24-qa3`，不覆盖 `build\\portable\\slim`，也不与别的
角色可能占用的 `build\\portable\\t24` 抢目录。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QA_DIR = Path(__file__).resolve().parent
PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
BUILD_PY = REPO / "packaging" / "build_portable.py"
BUILD_OUT = REPO / "build" / "portable" / "t24-qa3"
PKG = BUILD_OUT / "slim"
FFMPEG_DIR = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin")
MANUAL = REPO / "packaging" / "templates" / "使用说明.md"
MANUAL_TEST = REPO / "packaging" / "tests" / "manual.test.py"
VERSION_FILE = REPO / "VERSION"

TOPICS = ["解除锁定", "解压", "启动", "首次使用", "更新", "备份", "常见故障", "版本与日志"]
KEEP_ON_UPGRADE = ["ledger", "pipeline\\output", "models", "webapp\\data",
                   "workbuddy", "_setenv.local.bat"]

# 两个"用户自己才会有/首次使用后才生成"的文件：包里没有是对的，单列不参与断言。
LOCAL_ONLY = {
    "_setenv.local.bat": "用户自建的本地覆盖文件（可选），包里没有",
    "workbuddy\\models.json": "用户自己填 API key 的文件，首次用 AI 功能后才出现",
}
# 启动后才生成的日志：只查目录。
DIR_ONLY = {"logs\\_server_run.log": "logs"}

BUILD_RC: object = None
BUILD_TEXT = ""

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class CaseFail(AssertionError):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


class CaseDeferred(Exception):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


RESULTS: list[tuple[str, int, str]] = []


def case(num: int, desc: str):
    def deco(fn):
        def wrapper():
            try:
                ev = fn() or []
            except CaseDeferred as e:
                print("DEFERRED %d. %s -> %s" % (num, desc, e))
                for line in e.evidence:
                    print("    " + line)
                RESULTS.append(("DEFERRED", num, desc))
            except CaseFail as e:
                print("FAIL %d. %s -> %s" % (num, desc, e))
                for line in e.evidence:
                    print("    " + line)
                RESULTS.append(("FAIL", num, desc))
            except Exception as e:  # noqa: BLE001
                print("FAIL %d. %s -> %s: %s" % (num, desc, type(e).__name__, e))
                RESULTS.append(("FAIL", num, desc))
            else:
                print("PASS %d. %s" % (num, desc))
                for line in ev:
                    print("    " + line)
                RESULTS.append(("PASS", num, desc))
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def fail(msg: str, evidence: list[str] | None = None):
    raise CaseFail(msg, evidence)


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def run_cmd(args, timeout: int = 900, extra_env: dict | None = None) -> tuple[object, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    try:
        p = subprocess.run([str(a) for a in args], cwd=str(REPO), env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, _decode_bytes(p.stdout)
    except subprocess.TimeoutExpired as e:
        return "TIMEOUT", _decode_bytes(e.stdout or b"")


def tail(text: str, n: int = 12) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-n:]


def read_manual() -> tuple[str, list[str]]:
    if not MANUAL.is_file():
        fail("说明书不存在：%s" % MANUAL)
    text = MANUAL.read_text(encoding="utf-8")
    return text, text.splitlines()


def section(lines: list[str], heading: str, next_prefix: str = "## ") -> list[str]:
    """取 heading 开头到下一个同级标题前的行。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(heading):
            start = i
            break
    if start is None:
        return []
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith(next_prefix):
            break
        out.append(ln)
    return out


def build_package():
    global BUILD_RC, BUILD_TEXT
    if not WEBAPP_PY.is_file():
        BUILD_RC, BUILD_TEXT = "NO_WEBAPP_PY", "找不到 %s" % WEBAPP_PY
        return
    if not BUILD_PY.is_file():
        BUILD_RC, BUILD_TEXT = "NO_BUILD_PY", "找不到 %s" % BUILD_PY
        return
    if BUILD_OUT.exists():
        shutil.rmtree(BUILD_OUT)  # 只删私有的 build\portable\t24-qa3
    cmd = [WEBAPP_PY, BUILD_PY, "--flavor", "slim",
           "--out", str(BUILD_OUT), "--ffmpeg-dir", str(FFMPEG_DIR)]
    print("[build] %s" % " ".join(str(c) for c in cmd), flush=True)
    BUILD_RC, BUILD_TEXT = run_cmd(cmd, timeout=3600)
    print("[build] exit=%s" % BUILD_RC, flush=True)
    for ln in tail(BUILD_TEXT, 6):
        print("    " + ln, flush=True)


# ═════════════════════════════ 用例 1..9 ═════════════════════════════

@case(1, "manual.test.py 退出 0（说明书单测）")
def c01():
    global BUILD_RC, BUILD_TEXT
    if BUILD_RC != 0:
        fail("先行构建未成功（exit=%r），无法验证说明书里的包内路径" % BUILD_RC,
             tail(str(BUILD_TEXT), 10))
    if not MANUAL_TEST.is_file():
        fail("单测不存在：%s" % MANUAL_TEST)
    rc, out = run_cmd([WEBAPP_PY, MANUAL_TEST], timeout=300,
                       extra_env={"WB_MANUAL_PKG": str(PKG)})
    ev = ["%s -> exit=%s（WB_MANUAL_PKG=%s）" % (MANUAL_TEST, rc, PKG)] + tail(out, 10)
    if rc != 0:
        fail("manual.test.py 退出码 %s" % rc, ev)
    if "通过 15 / 15" not in out:
        ev.append("注意：没有看到「通过 15 / 15」汇总")
    return ev


@case(2, "八个主题都在二级标题里")
def c02():
    text, lines = read_manual()
    h2 = [ln for ln in lines if ln.startswith("## ")]
    missing = [t for t in TOPICS if not any(t in h for h in h2)]
    ev = ["二级标题："] + ["  " + h for h in h2]
    if missing:
        fail("缺主题：%r" % missing, ev)
    return ev


@case(3, "四个具体事实：换端口、升级保留数据、日志位置、版本号")
def c03():
    text, lines = read_manual()
    problems = []
    ev = []

    # (a) 端口被占换 8791/8792/8793，打开正确地址
    for port in ("8789", "8791", "8792", "8793"):
        if port not in text:
            problems.append("没写端口 %s" % port)
    if not any(w in text for w in ("127.0.0.1", "浏览器", "地址")):
        problems.append("没写会打开哪个地址")
    if not any(w in text for w in ("占用", "被占", "被占用")):
        problems.append("没说清端口被占会换")
    ev.append("(a) 端口：8789/8791/8792/8793 + 地址/浏览器 + 占用 关键词命中")

    # (b) 升级 = 复制全部内容、选替换、不要删六类数据
    for keep in KEEP_ON_UPGRADE:
        if keep not in text:
            problems.append("升级没写保留 %s" % keep)
    for word in ("复制", "替换", "不要删"):
        if word not in text:
            problems.append("升级没写「%s」" % word)
    ev.append("(b) 升级：复制 + 替换 + 不要删 %s" % "/".join(KEEP_ON_UPGRADE))

    # (c) 日志位置
    if "logs\\_server_run.log" not in text:
        problems.append("没写日志在 logs\\_server_run.log")
    ev.append("(c) 日志：logs\\_server_run.log 命中")

    # (d) 版本号
    if "VERSION" not in text or "版本" not in text:
        problems.append("没写版本号看 VERSION/版本")
    ev.append("(d) 版本：VERSION + 版本 命中")

    if problems:
        fail("；".join(problems), ev)
    return ev


@case(4, "三个已知限制各写一段")
def c04():
    text, lines = read_manual()
    groups = [
        ("① 粘贴 TikTok 链接不可用 / 只能用本地视频", ("yt-dlp", "链接", "本地视频")),
        ("② AI 翻译/对话要联网 + 自己填 API key（models.json）",
         ("API key", "联网", "models.json")),
        ("③ 采集需要自己的代理；其余可离线", ("代理", "离线")),
    ]
    missing = []
    ev = []
    for name, words in groups:
        hit = [w for w in words if w in text]
        ev.append("%s：命中 %r" % (name, hit))
        if len(hit) != len(words):
            missing.append("%s 缺 %r" % (name, [w for w in words if w not in text]))
    if missing:
        fail("；".join(missing), ev)
    return ev


@case(5, "常见故障至少五条（杀软/python.exe、端口、模型+HF_ENDPOINT、VC、中文/空格）")
def c05():
    text, lines = read_manual()
    faults = section(lines, "## 七、常见故障")
    if not faults:
        fail("找不到「## 七、常见故障」章节")
    body = "\n".join(faults)
    pairs = [
        ("杀软拦截 python.exe", ("杀软", "安全中心", "杀毒"), ("python.exe",)),
        ("端口被占", ("端口",), ("占",)),
        ("模型下载失败 + HF_ENDPOINT", ("模型",), ("HF_ENDPOINT",)),
        ("缺 VC 运行库", ("VC", "运行库"), ("msvcp140", "0xc000007b", "0xC000007B")),
        ("中文/空格路径", ("中文",), ("空格",)),
    ]
    missing = []
    ev = []
    for name, left, right in pairs:
        ok_l = any(w in body for w in left)
        ok_r = any(w in body for w in right)
        ev.append("%s：左=%s 右=%s" % (name, ok_l, ok_r))
        if not (ok_l and ok_r):
            missing.append(name)
    if missing:
        fail("常见故障缺：%r" % missing, ev)
    ev.append("常见故障章节行数：%d" % len(faults))
    return ev


@case(6, "禁项：作者绝对路径 / 教双击 python.exe / pip install")
def c06():
    text, lines = read_manual()
    problems = []
    ev = []

    bad_paths = ["D:\\tiktok-workbench", "C:\\Users", "C:/Users", "/home/"]
    hits = [b for b in bad_paths if b in text]
    ev.append("作者路径命中：%r（应为空）" % hits)
    if hits:
        problems.append("出现作者机器路径：%r" % hits)

    # 明确禁止的是"教用户双击 python\python.exe"；附录里"双击它没用，别动"是警示，允许。
    literal = "python\\python.exe"
    ev.append("字面量 python\\python.exe 命中：%s" % (literal in text))
    if literal in text:
        problems.append("出现 python\\python.exe")
    positives = []
    allowed_notes = []
    for ln in lines:
        low = ln.lower()
        if "双击" in ln and "python" in low:
            if any(neg in ln for neg in ("没用", "别动", "不要", "不能", "不是", "别双击")):
                allowed_notes.append(ln.strip())
            else:
                positives.append(ln.strip())
    ev.append("含「双击+python」的负向警示：%r" % allowed_notes)
    ev.append("含「双击+python」的正向建议：%r（应为空）" % positives)
    if positives:
        problems.append("发现教用户双击 python 的句子：%r" % positives)

    low = text.lower()
    pip_hits = [w for w in ("pip install", "python -m pip") if w in low]
    ev.append("pip 建议命中：%r（应为空）" % pip_hits)
    if pip_hits:
        problems.append("出现 pip 建议：%r" % pip_hits)

    if problems:
        fail("；".join(problems), ev)
    return ev


@case(7, "反引号里的包内路径在新构建的 slim 包里真实存在")
def c07():
    global BUILD_RC, BUILD_TEXT
    if BUILD_RC != 0:
        fail("构建失败（exit=%r），无法验证包内路径" % BUILD_RC, tail(str(BUILD_TEXT), 10))
    if not PKG.is_dir():
        fail("包不存在：%s" % PKG)
    text, _lines = read_manual()
    tokens = [t.strip() for t in re.findall(r"`([^`\n]+)`", text)]

    def is_pkg_path(t: str) -> bool:
        if not t or " " in t:
            return False
        if t.lower().startswith(("http://", "https://")):
            return False
        if any(ch in t for ch in ("<", ">", "*", "…")) or "..." in t:
            return False
        if t in LOCAL_ONLY or t in DIR_ONLY:
            return True
        if "\\" in t or "/" in t:
            return True
        return t in {"VERSION"} or t.endswith((".bat", ".md", ".exe", ".log", ".json", ".txt"))

    paths = [t for t in tokens if is_pkg_path(t)]
    checked = []
    skipped = []
    missing = []
    for t in paths:
        if t in LOCAL_ONLY:
            skipped.append("%s（%s）" % (t, LOCAL_ONLY[t]))
            continue
        if t in DIR_ONLY:
            d = DIR_ONLY[t]
            ok = (PKG / d).is_dir()
            checked.append("%s -> 目录 %s\\ = %s" % (t, d, ok))
            if not ok:
                missing.append("%s（目录 %s）" % (t, d))
            continue
        rel = t.replace("/", "\\").rstrip("\\")
        ok = (PKG / rel).exists()
        checked.append("%s -> %s" % (t, ok))
        if not ok:
            missing.append(t)

    ev = ["从反引号抽到路径 %d 个，检查 %d 个；占位/通配已排除" %
          (len(paths), len(paths) - len(skipped))]
    ev += ["  " + c for c in checked]
    if skipped:
        ev += ["白名单/例外（不参与断言）："] + ["  " + s for s in skipped]
    if missing:
        fail("这些路径在包里不存在：%r" % missing, ev)
    return ev


@case(8, "用户视角 5 分钟抽检（只能人工）")
def c08():
    raise CaseDeferred("DEFERRED-HUMAN", [
        "DEFERRED-HUMAN: 需要一个没参与开发的人按说明书从「下载 ZIP」读到「打开页面」，全程不问问题；QA 无法代替。",
        "请人工记下：抽检人、卡在哪一步、是否要问问题。",
    ])


@case(9, "QA 入口：pipeline 解释器跑 qa\\T-24\\run.py")
def c09():
    actual = Path(sys.executable).resolve()
    expect = PIPELINE_PY.resolve()
    ev = ["sys.executable=%s" % actual, "期望=%s" % expect,
          "脚本=%s" % Path(__file__).resolve(),
          "构建：exit=%r，包=%s（%s）" % (BUILD_RC, PKG,
                                       "存在" if PKG.is_dir() else "不存在")]
    if actual != expect:
        fail("不是用清单规定的解释器在跑（%s）" % actual, ev)
    if Path(__file__).resolve() != (QA_DIR / "run.py").resolve():
        fail("入口不是 qa\\T-24\\run.py", ev)
    return ev


# ═══════════════════════════════ main ═══════════════════════════════
def main() -> int:
    print("qa/T-24/run.py — T-24 独立验收（M3-qa-plan T-24 1..9）")
    print("仓库：%s" % REPO)
    print("说明书：%s" % MANUAL)
    print("")
    build_package()
    for fn in (c01, c02, c03, c04, c05, c06, c07, c08, c09):
        fn()

    total = len(RESULTS)
    passed = sum(1 for s, _n, _d in RESULTS if s == "PASS")
    deferred = [r for r in RESULTS if r[0] == "DEFERRED"]
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("")
    if deferred:
        for _s, num, _desc in deferred:
            print("DEFERRED-HUMAN: 第 %d 条（T-24 DoD 7 真人 5 分钟抽检）" % num)
    print("通过 %d / %d（FAIL %d，DEFERRED %d）%s"
          % (passed, total, len(failed), len(deferred),
             "；失败：%s" % ", ".join("%d.%s" % (n, d) for _s, n, d in failed) if failed else ""))
    if failed:
        print("T-24 有 FAIL，退出码 1")
        return 1
    print("T-24 通过（1 条 DEFERRED-HUMAN 不计入失败：真人 5 分钟抽检），退出码 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
