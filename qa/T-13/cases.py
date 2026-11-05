# -*- coding: utf-8 -*-
r"""qa/T-13/cases.py —— T-13「启动脚本、测试、README 不再依赖旧仓库路径」的 QA 用例

用例清单来源：`.dsh-crew/jobs/fix-check-findings/T-13-plan.md`（C-01 ~ C-10，一条一条实现）。
DoD 来源：docs/tasks/tasks.md 里 T-13 的 DoD 第 1~5 条，以及 DoD 2 的「更正 1」（2026-11-05）。

三处与清单字面不同、必须写在最前面的地方（都是清单跟不上更正/DoD 造成的）：

  1. **C-04**：清单写的是「11 条 Node 用例各自退出码 0」，但 DoD 2 的更正 1 已把这一条
     改成 (a)(b)(c)(d)：7 条历史红用例（bench_products / bench_render / bench_url /
     fail_analysis / refresh_active_view / reverse_progress_ui / run_progress）改前就红，
     本任务不重写它们。所以 C-04 按更正后的版本判：(a) 4 条改后已绿的 Node 用例必须退出码 0；
     (b) 7 条历史红的输出里不得再出现旧路径症状（ENOENT / workbuddy_project）。
     它们的真实失败原因（旧 DOM / STEP_META）只作为证据打印，不判红。

  2. **C-07**：清单写的是「两份 README 里 workbuddy_project 无输出」，比 DoD 4 更严。
     DoD 4 只禁止 **命令行** 里的 `cd D:\workbuddy_project\tiktok` 与旧 `PY=` 路径，并要求
     中英文两版命令一致。两版 README 的 `WB_REVERSE_DIR` 说明行还写着旧仓库路径，那是
     「作者机器上的默认路径」的 **说明文字**，不是要照抄的命令（README 的
     “not portable” 一节就是这么讲的，`webapp/tk_reverse.py` 的默认值也确实还是它）。
     所以 C-07 按 DoD 4 判：命令行干净 + 两版命令一致；非命令行的旧路径出现作为 note
     打印出来，不算失败。判据宁可说清楚，也不把说明文字当成缺陷。

  3. **C-10**：清单要求执行 `qa\run-all.ps1` 且退出码 0。run-all 会枚举 `qa\` 下所有目录，
     包括本目录，于是形成递归。这里用三个 QA 目录共用的环境变量 `QA_RUNALL_NESTED`
     （并保留本目录旧的 `T13_NESTED_RUNALL` 作兼容）打断：外层（人跑的 run.ps1）的 C-10
     真跑一次 run-all，并在子进程环境里设 `QA_RUNALL_NESTED=1`；run-all 调进来的内层 T-13
     跑 C-01~C-09，C-10 直接打印
     `ok C-10 (nested run-all: 跳过自身回归检查，由外层负责)` 并通过，不再起第二个 run-all。
     另外全套退出码当前不可能是 0（T-04 / T-11 等别的任务自己的旧红与本任务无关），
     所以 C-10 按清单第二句「失败项若涉及 T-13 拥有的文件判红」判：
     失败目录不许提到 T-13 拥有的文件，T-13 自己的用例也不许红；真实退出码原样打印。

边界：本文件只读仓库、只跑命令；不写 `webapp/`、不改产品代码/产品测试、不启停 8789 上的服务。
跑法：powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-13\run.ps1
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

# 控制台/管道编码可能不是 UTF-8：真控制台保留默认，重定向时用 UTF-8（与 qa/T-04/cases.py 同做法）
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:
        pass

_HERE = Path(__file__).resolve().parent          # ...\qa\T-13
QA_ROOT = _HERE.parent                            # ...\qa
REPO = _HERE.parents[1]                           # 仓库根
WEBAPP = REPO / "webapp"
PYTHON = WEBAPP / ".venv" / "Scripts" / "python.exe"
JOB = REPO / ".dsh-crew" / "jobs" / "fix-check-findings"

OLD = "workbuddy_project"

# 启动/服务脚本：DoD 1 写的是 start_workbench.vbs / start_workbench.bat / `_serve*.bat`；
# `_serve*.bat` 是通配，所以把 _serve_port.bat 也算进来（更正 1 表里也是这个理由）。
def launcher_files() -> list[Path]:
    files = [WEBAPP / "start_workbench.vbs", WEBAPP / "start_workbench.bat"]
    files += sorted(WEBAPP.glob("_serve*.bat"))
    return files


_DRIVE = re.compile(r"[A-Za-z]:\\")

# C-04 的名单（DoD 2 更正 1 的 (a)/(b)）
NODE_GREEN = ["fold_blocks", "pipe_fold", "run_add_account", "smoke_no_ledger"]
NODE_LEGACY_RED = [
    "bench_products",
    "bench_render",
    "bench_url",
    "fail_analysis",
    "refresh_active_view",
    "reverse_progress_ui",
    "run_progress",
]

# T-13 拥有、且一旦出现在别的目录的失败输出里就说明被本任务弄坏的文件
T13_OWNED_TOKENS = [
    "webapp/start_workbench.vbs",
    "webapp/start_workbench.bat",
    "webapp/_serve",
    "webapp/install.bat",
    "webapp/static/app.js",
    "webapp/tests/bench_products.test.js",
    "webapp/tests/bench_render.test.js",
    "webapp/tests/bench_url.test.js",
    "webapp/tests/fail_analysis.test.js",
    "webapp/tests/fold_blocks.test.js",
    "webapp/tests/pipe_fold.test.js",
    "webapp/tests/product_scan_acceptance.js",
    "webapp/tests/refresh_active_view.test.js",
    "webapp/tests/reverse_progress_ui.test.js",
    "webapp/tests/run_add_account.test.js",
    "webapp/tests/run_progress.test.js",
    "webapp/tests/smoke_no_ledger.test.js",
    "webapp/tests/port_override.test.py",
    "webapp/tests/repo_paths.test.py",
    "webapp/tests/post_update_port.test.py",
    "webapp/tests/reverse_restore_e2e.test.py",
    "webapp/tests/reverse_voice_browser.js",
    "webapp/tests/reverse_voice_origin.test.py",
    "README.md",
    "README-zh.md",
]

_ok = 0
_fail = 0
_evidence: list[str] = []
# 用例自己已经打印过 ok 行（嵌套 run-all 场景）时返回它，包装器只计数、不再重复打印
_SILENT_OK = object()


class CaseFail(AssertionError):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


def fail(msg: str, evidence: list[str] | None = None):
    raise CaseFail(msg, evidence)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def lines_of(path: Path) -> list[str]:
    return read(path).splitlines()


def run(args, cwd: Path | None = None, timeout: int = 300, env_extra: dict[str, str] | None = None):
    """跑子进程，返回 (退出码, stdout+stderr 文本)。子进程一律 UTF-8 输出。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # 本机控制台是 GBK，子进程里中文测试按 UTF-8 输出
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(
            [str(a) for a in args],
            cwd=str(cwd or WEBAPP),
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        fail("命令不可用：%s（%s）" % (args[0], e))
    except subprocess.TimeoutExpired:
        fail("命令超时 %ss：%s" % (timeout, " ".join(str(a) for a in args)))
    out = (p.stdout + b"\n" + p.stderr).decode("utf-8", "replace")
    return p.returncode, out


def tail_lines(text: str, n: int = 4) -> list[str]:
    got = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return got[-n:]


def first_cause(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if re.search(r"(TypeError|ReferenceError|AssertionError|Cannot read|Cannot set|assertion failed)", s):
            return s
    got = tail_lines(text, 3)
    return got[-1] if got else "(无输出)"


def port_listening(port: int) -> bool:
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def check(cid: str, name: str):
    """装饰器：把用例函数包成「跑、打印 ok/FAIL、累加计数」，导入时不执行。"""
    def deco(fn):
        def wrapper():
            global _ok, _fail
            try:
                ev = fn()
            except CaseFail as e:
                print("FAIL %s %s -> %s" % (cid, name, e))
                for line in e.evidence:
                    print("    " + line)
                _fail += 1
            except Exception as e:  # noqa: BLE001 - 用例自己说清失败原因，不能整段崩
                print("FAIL %s %s -> %s: %s" % (cid, name, type(e).__name__, e))
                _fail += 1
            else:
                if ev is _SILENT_OK:
                    # 用例自己已经打印过 ok 行（嵌套 run-all 场景），这里只计数，不重复打印
                    _ok += 1
                    return
                ev = ev or []
                print("ok %s %s" % (cid, name))
                for line in ev:
                    print("    " + line)
                _ok += 1
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


# ---------------------------------------------------------------- C-01

@check("C-01", "DoD 1 前半：启动/服务脚本里没有旧仓库名 workbuddy_project")
def c01():
    files = launcher_files()
    missing = [rel(p) for p in files if not p.exists()]
    if missing:
        fail("脚本不存在：" + ", ".join(missing))
    hits = []
    for p in files:
        for no, line in enumerate(lines_of(p), 1):
            if OLD in line.lower():
                hits.append("%s:%d: %s" % (rel(p), no, line.strip()))
    ev = ["扫描 %d 个启动/服务脚本：%s" % (len(files), ", ".join(rel(p) for p in files))]
    if hits:
        fail("这些脚本仍含旧仓库名（%s）：%s" % (OLD, "; ".join(hits[:6]),), ev)
    ev.append("命中 %d 处（期望 0）" % len(hits))
    return ev


# ---------------------------------------------------------------- C-02

@check("C-02", "DoD 1 中段：端口统一 8789，旧端口 8787 不再出现")
def c02():
    files = launcher_files()
    hits8787, assign8789 = [], []
    port_assign = re.compile(r"(?i)(\bPORT\b\s*=|WB_PORT|--port|\bPORT\b\s*:=)")
    for p in files:
        for no, line in enumerate(lines_of(p), 1):
            if "8787" in line:
                hits8787.append("%s:%d: %s" % (rel(p), no, line.strip()))
            if "8789" in line and port_assign.search(line):
                assign8789.append("%s:%d: %s" % (rel(p), no, line.strip()))
    ev = ["8787 命中 %d 处（期望 0）" % len(hits8787)]
    if hits8787:
        fail("旧端口 8787 仍出现在启动脚本：%s" % "; ".join(hits8787[:6]), ev)
    if not assign8789:
        fail("没有任何一行把 8789 真正赋给端口/启动参数（只出现在注释/echo 不算）", ev)
    ev.append("8789 真正赋值 %d 行：" % len(assign8789))
    for line in assign8789:
        ev.append(line)
    ev.append("注意：_serve_port.bat 可由调用方传端口（默认 8789），端口由调用方给出；其余脚本自己设 8789")
    return ev


# ---------------------------------------------------------------- C-03

@check("C-03", "DoD 1 后半：路径按脚本自身位置推导，不写死机器绝对路径")
def c03():
    files = launcher_files()
    drive_hits = []
    for p in files:
        for no, line in enumerate(lines_of(p), 1):
            if _DRIVE.search(line):
                drive_hits.append("%s:%d: %s" % (rel(p), no, line.strip()))
    ev = ["扫描 %d 个脚本，盘符路径命中 %d 处（期望 0）" % (len(files), len(drive_hits))]
    if drive_hits:
        fail("脚本里出现写死的盘符路径（%s）：%s" % (r"[A-Za-z]:\\", "; ".join(drive_hits[:6])), ev)

    bat = [p for p in files if p.suffix.lower() == ".bat"]
    dp0 = [p for p in bat if "%~dp0" in read(p)]
    ev.append("含 %~dp0 的 .bat：" + (", ".join(rel(p) for p in dp0) or "无"))
    if not dp0:
        fail("没有一个 .bat 用 %~dp0 按自身位置推导路径", ev)
    for p in bat:
        if p not in dp0:
            ev.append("note：" + rel(p) + " 不含 %~dp0（只是转调别的脚本/不含路径的 wrapper，按清单不要求）")

    vbs = WEBAPP / "start_workbench.vbs"
    if "ScriptFullName" not in read(vbs):
        fail("%s 里没有 ScriptFullName，没有按自身位置推导路径" % rel(vbs), ev)
    ev.append("start_workbench.vbs 使用 ScriptFullName 取自身目录")

    # 「从任意当前目录也能起服务」只有真跑一次才能证明。8789 已经由用户的服务监听，
    # 此时 start_workbench.vbs 必须幂等退出 0；没监听就按 PRD 不动用户的服务，跳过。
    if port_listening(8789):
        code, out = run(["cscript", "//nologo", "start_workbench.vbs"], cwd=WEBAPP, timeout=120)
        ev.append("8789 已监听 -> cscript //nologo start_workbench.vbs exit=%d（期望 0，幂等退出）" % code)
        if code != 0:
            fail("8789 已监听时 cscript start_workbench.vbs 应幂等退出 0，实得 exit=%d：%s"
                 % (code, " / ".join(tail_lines(out, 3))), ev)
    else:
        ev.append("note：8789 当前没有监听，跳过实跑启动脚本（PRD 不许动用户正在跑的服务）")
    return ev


# ---------------------------------------------------------------- C-04

@check("C-04", "DoD 2(a)(b)：4 条必需绿的 Node 用例退出码 0；7 条历史红不再有旧路径症状")
def c04():
    ev = []
    for name in NODE_GREEN:
        code, out = run(["node", "tests\\%s.test.js" % name], cwd=WEBAPP, timeout=300)
        ev.append("node tests\\%s.test.js -> exit=%d（期望 0）%s"
                  % (name, code, "；末行：" + (tail_lines(out, 1)[0] if tail_lines(out, 1) else "")))
        if code != 0:
            fail("必需绿的用例 %s 退出码 %d（期望 0）：%s"
                 % (name, code, " / ".join(tail_lines(out, 4))), ev)

    for name in NODE_LEGACY_RED:
        code, out = run(["node", "tests\\%s.test.js" % name], cwd=WEBAPP, timeout=300)
        symptom = "ENOENT" in out.upper() or OLD in out.lower()
        ev.append("历史红 node tests\\%s.test.js -> exit=%d（改前就红，允许）；真实原因：%s"
                  % (name, code, first_cause(out)))
        if symptom:
            bad = [ln.strip() for ln in out.splitlines() if "ENOENT" in ln.upper() or OLD in ln.lower()]
            fail("历史红用例 %s 的输出里仍有旧路径症状（ENOENT/workbuddy_project）：%s"
                 % (name, " / ".join(bad[:3]),), ev)
    ev.append("11 条 Node 用例：4 条必需绿全绿；7 条历史红均为旧 DOM/STEP_META，无旧路径症状")
    return ev


# ---------------------------------------------------------------- C-05

@check("C-05", "DoD 2(a) Python 部分：port_override.test.py 退出码 0")
def c05():
    if not PYTHON.exists():
        fail("找不到解释器 %s" % PYTHON)
    code, out = run([PYTHON, "tests\\port_override.test.py"], cwd=WEBAPP, timeout=180)
    ev = ["cd webapp; .venv\\Scripts\\python.exe tests\\port_override.test.py -> exit=%d（期望 0）" % code,
          "末行：" + (tail_lines(out, 1)[0] if tail_lines(out, 1) else "(无输出)")]
    if code != 0:
        fail("port_override.test.py 退出码 %d（期望 0）：%s" % (code, " / ".join(tail_lines(out, 5))), ev)
    return ev


# ---------------------------------------------------------------- C-06

@check("C-06", "DoD 3：product_scan_acceptance.js 的 playwright-core 引用与页面地址不写死旧仓库")
def c06():
    p = WEBAPP / "tests" / "product_scan_acceptance.js"
    if not p.exists():
        fail("文件不存在：%s" % rel(p))
    text = read(p)
    old_hits = [("%s:%d: %s" % (rel(p), no, ln.strip())) for no, ln in enumerate(text.splitlines(), 1)
                if OLD in ln.lower() or ":8787" in ln]
    drive_hits = [("%s:%d: %s" % (rel(p), no, ln.strip())) for no, ln in enumerate(text.splitlines(), 1)
                  if _DRIVE.search(ln)]
    pw = [ln for ln in text.splitlines() if "playwright-core" in ln]
    page_literal = "127.0.0.1:8789" in text
    ev = ["旧仓库名/旧端口命中 %d 处（期望 0）" % len(old_hits),
          "盘符写死路径命中 %d 处（期望 0）" % len(drive_hits),
          "playwright-core 引用 %d 行（期望 >=1）%s" % (len(pw), ("：" + pw[0].strip()) if pw else "")]
    if old_hits:
        fail("product_scan_acceptance.js 仍有旧仓库名或旧端口：%s" % "; ".join(old_hits[:5]), ev)
    if drive_hits:
        fail("product_scan_acceptance.js 仍有写死的盘符路径：%s" % "; ".join(drive_hits[:5]), ev)
    if not pw:
        fail("product_scan_acceptance.js 里找不到 playwright-core 引用（DoD 3 要求它仍能跑页面）", ev)
    if page_literal:
        ev.append("页面地址直接写 127.0.0.1:8789（DoD 3 满足）")
    else:
        ev.append("note：页面地址不是字面量 8789，而由脚本内变量给出；前两条旧路径检查无命中，按清单算通过")
    return ev


# ---------------------------------------------------------------- C-07

def _code_block_lines(text: str) -> list[str]:
    out, infence = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            infence = not infence
            continue
        if infence and s:
            out.append(s)
    return out


def _norm_cmd(line: str) -> str:
    line = re.sub(r"\s+#.*$", "", line)      # 行尾注释（两版是翻译的，不该参与比较）
    line = re.sub(r"<[^>]*>", "<X>", line)   # <账号> / <account> 这类占位符
    return line.strip()


@check("C-07", "DoD 4：两份 README 无旧路径命令行，中英两版命令一致")
def c07():
    en = REPO / "README.md"
    zh = REPO / "README-zh.md"
    if not en.exists() or not zh.exists():
        fail("README 缺失：%s / %s" % (rel(en), rel(zh)))
    ev = []
    texts = {rel(en): read(en), rel(zh): read(zh)}

    # (1) 命令行里不许出现旧仓库路径/旧 PY 路径
    bad = []
    for rp, text in texts.items():
        for no, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if OLD in line.lower() and (s.startswith("cd ") or s.lower().startswith("set py=")):
                bad.append("%s:%d: %s" % (rp, no, s))
            if re.search(r"cd\s+D:\\%s\\tiktok" % OLD, line, re.I):
                bad.append("%s:%d: %s" % (rp, no, s))
    if bad:
        fail("README 命令行里仍有旧仓库路径：%s" % "; ".join(bad[:6]), ev)
    ev.append("命令行（cd / set PY=）里旧仓库路径命中 0 处")

    # (2) 两版 cd 行逐条一致
    cd = {}
    for rp, text in texts.items():
        cd[rp] = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("cd ")]
    if not cd[rel(en)] or not cd[rel(zh)]:
        fail("两版 README 都必须有 cd 命令行", ev)
    if cd[rel(en)] != cd[rel(zh)]:
        fail("两版 README 的 cd 命令行不一致：en=%s / zh=%s" % (cd[rel(en)], cd[rel(zh)]), ev)
    ev.append("两版 cd 命令行一致（%d 条）：%s" % (len(cd[rel(en)]), cd[rel(en)]))

    # (3) 两版 set PY= 一致，且路径在仓库根下
    py_paths = {}
    for rp, text in texts.items():
        got = [ln.strip() for ln in text.splitlines() if re.match(r"\s*set\s+PY\s*=", ln, re.I)]
        py_paths[rp] = got
    if len(py_paths[rel(en)]) != 1 or len(py_paths[rel(zh)]) != 1:
        fail("两版 README 里 set PY= 应各 1 条，实得 en=%d / zh=%d"
             % (len(py_paths[rel(en)]), len(py_paths[rel(zh)]),), ev)
    if py_paths[rel(en)] != py_paths[rel(zh)]:
        fail("两版 README 的 set PY= 不一致：en=%s / zh=%s"
             % (py_paths[rel(en)], py_paths[rel(zh)],), ev)
    py_line = py_paths[rel(en)][0]
    py_value = py_line.split("=", 1)[1].strip()
    root_str = str(REPO).replace("/", "\\").rstrip("\\").lower()
    if OLD in py_value.lower() or not py_value.lower().startswith(root_str + "\\"):
        fail("README 的 PY 路径不在仓库根 %s 下（或还是旧路径）：%s" % (REPO, py_value), ev)
    ev.append("两版 set PY= 一致且在仓库根下：%s" % py_value)

    # (4) 代码块里的命令序列一致（去掉注释行/行尾注释、占位符统一后逐条比较）
    cmds = {}
    for rp, text in texts.items():
        seq = []
        for line in _code_block_lines(text):
            if line.startswith("#") or line.lower().startswith("rem "):
                continue
            n = _norm_cmd(line)
            if n:
                seq.append(n)
        cmds[rp] = seq
    if cmds[rel(en)] != cmds[rel(zh)]:
        only_en = [x for x in cmds[rel(en)] if x not in cmds[rel(zh)]]
        only_zh = [x for x in cmds[rel(zh)] if x not in cmds[rel(en)]]
        fail("两版 README 的代码块命令序列不一致（en %d 条 / zh %d 条）：只在 en=%s ；只在 zh=%s"
             % (len(cmds[rel(en)]), len(cmds[rel(zh)]), only_en[:3], only_zh[:3],), ev)
    ev.append("两版代码块命令序列一致（%d 条，占位符与行尾注释已归一）" % len(cmds[rel(en)]))

    # (5) note：非命令行的旧路径说明文字（DoD 4 不禁止；只打印，不判红）
    for rp, text in texts.items():
        for no, line in enumerate(text.splitlines(), 1):
            if OLD in line.lower() and not line.strip().startswith("cd ") \
                    and not line.strip().lower().startswith("set py="):
                ev.append("note：%s:%d 出现旧仓库路径，但那是默认值/限制说明文字，不是命令：%s"
                          % (rp, no, line.strip()[:110]))
    return ev


# ---------------------------------------------------------------- C-08

@check("C-08", "DoD 5 前半：新用例 repo_paths.test.py 退出码 0")
def c08():
    p = WEBAPP / "tests" / "repo_paths.test.py"
    if not p.exists():
        fail("新用例不存在：%s" % rel(p))
    if not PYTHON.exists():
        fail("找不到解释器 %s" % PYTHON)
    code, out = run([PYTHON, "tests\\repo_paths.test.py"], cwd=WEBAPP, timeout=180)
    ev = ["cd webapp; .venv\\Scripts\\python.exe tests\\repo_paths.test.py -> exit=%d（期望 0）" % code,
          "末行：" + (tail_lines(out, 1)[0] if tail_lines(out, 1) else "(无输出)")]
    if code != 0:
        fail("repo_paths.test.py 退出码 %d（期望 0）：%s" % (code, " / ".join(tail_lines(out, 5))), ev)

    # 顺手核对它的扫描范围真的覆盖 DoD 5 说的三处：tests/*.js、tests/*.py、启动脚本
    src = read(p)
    missing = [tok for tok in ("*.js", "*.py", "*.bat", "*.vbs") if tok not in src]
    if missing:
        fail("repo_paths.test.py 的扫描范围少了 %s（DoD 5 要求扫 tests/*.js、tests/*.py 与启动脚本）"
             % ", ".join(missing), ev)
    m = re.search(r"扫描\s+(\d+)\s+个文件", out)
    ev.append("扫描范围含 tests/*.js、tests/*.py、*.bat、*.vbs；扫描文件数 %s" % (m.group(1) if m else "(未解析)"))
    return ev


# ---------------------------------------------------------------- C-09

def _fenced_blocks(text: str) -> list[str]:
    blocks, cur, infence = [], [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if infence:
                blocks.append("\n".join(cur))
                cur, infence = [], False
            else:
                cur, infence = [], True
            continue
        if infence:
            cur.append(line)
    return blocks


_NONZERO = re.compile(r"(?i)(exit\s*(?:code)?\s*[=:]\s*[1-9]|退出码\s*[:：=]?\s*[1-9]|returncode\s*=\s*[1-9]|\bFAILED\b)")
_ZERO = re.compile(r"(?i)(exit\s*(?:code)?\s*[=:]\s*0|退出码\s*[:：=]?\s*0|returncode\s*=\s*0)")
_HIT_LINE = re.compile(r":\d+:")


def _segments(text: str):
    """找 repo_paths.test.py 的「改前红」「改后绿」两段真实输出，返回 (红段摘要, 绿段摘要)。

    两个都只在 **代码块** 里找，避免把「应该长这样」的说明文字当成证据：
      红段 = 一个代码块里同时有 FAIL / 旧仓库名 / 形如 `文件名:行号:` 的命中行；
      绿段 = 一个代码块里有 PASS 未发现硬编码旧仓库路径。
    """
    red = green = None
    for block in _fenced_blocks(text):
        low = block.lower()
        if red is None and "fail" in low and OLD in low:
            hit_lines = [ln.strip() for ln in block.splitlines()
                         if _HIT_LINE.search(ln) and OLD in ln.lower()]
            if hit_lines:
                red = hit_lines[0]
        if green is None:
            m = re.search(r"(?m)^\s*.*PASS\s*未发现硬编码旧仓库路径.*$", block)
            if m:
                green = m.group(0).strip()
    return red, green


def _first_marker(text: str, pattern) -> str:
    m = pattern.search(text)
    if not m:
        return "(无)"
    line_start = text.rfind("\n", 0, m.start()) + 1
    line_end = text.find("\n", m.end())
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()


@check("C-09", "DoD 5 后半：repo_paths.test.py 的改前红 / 改后绿两段真实输出")
def c09():
    report = _HERE / "engineer-report.md"
    candidates: list[tuple[str, str]] = []
    if report.exists():
        candidates.append((rel(report), read(report)))
    for p in sorted(_HERE.glob("*report*.md")):
        if p != report and p.is_file():
            candidates.append((rel(p), read(p)))
    if JOB.exists():
        for p in sorted(JOB.glob("*report*.md")):
            if p.is_file():
                candidates.append((rel(p), read(p)))
        inbox = JOB / "inbox"
        if inbox.exists():
            for p in sorted(inbox.glob("*.md")):
                if p.is_file():
                    candidates.append((rel(p), read(p)))

    # 提交信息也算耐用证据（DoD 5 优先要求报告里贴出，提交信息是第二条路）
    commit_hits: list[str] = []
    code, log_out = run(["git", "log", "--all", "--format=%H%x09%s%x0a%b%x0a===COMMIT===="],
                        cwd=REPO, timeout=120)
    if code == 0:
        for block in log_out.split("===COMMIT===="):
            if "T-13" not in block and "repo_paths" not in block:
                continue
            red, green = _segments(block)
            first = (block.strip().splitlines() or ["(空提交)"])[0]
            if red and green and _NONZERO.search(block) and _ZERO.search(block):
                commit_hits.append(first)
            else:
                commit_hits.append(first + "（未见完整两段）")

    ev = ["搜索报告：" + (", ".join(rp for rp, _ in candidates) or "无（qa/T-13/*report*.md、job 报告、inbox 都没有）")]
    if commit_hits:
        ev.append("git 历史里提到 T-13/repo_paths 的提交：" + "; ".join(commit_hits[:3]))
    else:
        ev.append("git 历史里没有任何提到 T-13 / repo_paths 的提交（T-13 的改动还没提交）")

    # 报告优先：一个来源里必须同时找到红段、绿段和非 0 / 0 的退出码标记
    best = None
    for rp, text in candidates:
        red, green = _segments(text)
        if red and green and _NONZERO.search(text) and _ZERO.search(text):
            best = (rp, text, red, green)
            break
    if best:
        rp, text, red, green = best
        ev.append("证据来源：%s（改前红 + 改后绿两段都在）" % rp)
        ev.append("改前红：%s；退出标记 %s" % (red, _first_marker(text, _NONZERO)))
        ev.append("改后绿：%s；退出标记 %s" % (green, _first_marker(text, _ZERO)))
        return ev

    if commit_hits and "（未见完整两段）" not in commit_hits[0]:
        ev.append("提交信息里含两段输出；但 DoD 5 要求「报告贴改前红、改后绿」，报告 %s 里证据不完整"
                  % (rel(report) if report.exists() else "(未落盘)"))
        return ev

    # 没找到完整证据：把缺什么说清楚（缺红段 / 缺绿段 / 两者都缺）
    parts = []
    for rp, text in candidates:
        red, green = _segments(text)
        miss = []
        if not red:
            miss.append("改前红（代码块里 FAIL + 命中的文件:行号 指名旧路径）")
        if not green:
            miss.append("改后绿（PASS 未发现硬编码旧仓库路径）")
        if not _NONZERO.search(text):
            miss.append("非 0 退出码标记")
        if not _ZERO.search(text):
            miss.append("0 退出码标记")
        if miss:
            parts.append("%s 缺：%s" % (rp, "；".join(miss)))
    detail = "；".join(parts) if parts else "没有找到任何报告文件（qa/T-13/engineer-report.md 不存在）"
    fail("repo_paths.test.py 的改前红 / 改后绿两段证据不完整：%s。按 T-02/T-03/T-04 的惯例，"
         "工程师原文应逐字落盘到 qa/T-13/engineer-report.md（改前：退出码非 0 且列出命中的旧路径文件；"
         "改后：退出码 0）。补齐后重跑 powershell -NoProfile -ExecutionPolicy Bypass -File qa\\T-13\\run.ps1"
         % detail, ev)


# ---------------------------------------------------------------- C-10

def _split_sections(text: str) -> dict[str, str]:
    """把 qa\\run-all.ps1 的输出切成 {任务 id: 该目录的正文}；同名段落只取第一次出现。"""
    parts = re.split(r"(?m)^=== (T-\d+) -> .*? ===[ \t]*\r?$", text)
    secs: dict[str, str] = {}
    it = iter(parts[1:])
    for tid, body in zip(it, it):
        if tid not in secs:
            secs[tid] = body
    return secs


@check("C-10", "回归（非 DoD）：全套 QA 没被本任务弄红")
def c10():
    # 递归护栏：qa\run-all.ps1 会枚举 qa\ 下所有目录并调回本目录。
    # QA_RUNALL_NESTED 是三个 QA 目录共用的标记（T-11 的 QA_T11_NESTED、T-12 的 QA_T12_NESTED
    # 也一起认这个），谁在 run-all 的子里跑谁就不再起新的 run-all，否则互相不认会叠出多层 run-all。
    # T13_NESTED_RUNALL 保留兼容旧入口，两个有一个为真就按嵌套处理。
    if os.environ.get("QA_RUNALL_NESTED") or os.environ.get("T13_NESTED_RUNALL") == "1":
        print("ok C-10 (nested run-all: 跳过自身回归检查，由外层负责)")
        return _SILENT_OK

    runner = QA_ROOT / "run-all.ps1"
    if not runner.exists():
        fail("找不到共享总跑器 %s" % rel(runner))
    code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", runner],
                    cwd=REPO, timeout=2400,
                    env_extra={"QA_RUNALL_NESTED": "1", "T13_NESTED_RUNALL": "1"})

    secs = _split_sections(out)
    if not secs:
        fail("qa\\run-all.ps1 exit=%d 但输出里没有任何任务段落，无法确认总跑器真的跑完：%s"
             % (code, " / ".join(tail_lines(out, 4))))

    fail_dirs = re.findall(r"(?m)^\[FAIL\] (T-\d+) exit=(\d+)", out)
    failed = [tid for tid, _ in fail_dirs]
    no_entry = re.findall(r"(?m)^\[WARN\] (T-\d+)：没有入口文件", out)
    ev = ["qa\\run-all.ps1 exit=%d" % code,
          "失败目录：" + (", ".join("%s(exit=%s)" % d for d in fail_dirs) or "无")]
    if no_entry:
        ev.append("无入口目录（不算通过）：" + ", ".join(no_entry))
    for tid in failed:
        if tid == "T-13":
            continue
        cs = re.findall(r"(?m)^\s*FAIL (C-\d+) ", secs.get(tid, ""))
        if cs:
            ev.append("  %s 的失败用例：%s（原因归该目录自己的报告，与 T-13 无关才不判红）"
                      % (tid, ", ".join(cs[:10])))

    # T-13 自己的用例：任何一条红都算本任务回归（C-09 是报告证据，也在 C-09 里单独判）
    t13_fail_cases = re.findall(r"(?m)^\s*FAIL (C-\d+) ", secs.get("T-13", ""))
    ev.append("T-13 自身失败用例：" + (", ".join(t13_fail_cases) or "无"))
    if t13_fail_cases:
        fail("T-13 自己的用例 %s 红了（见上面各条的失败原因）" % ", ".join(t13_fail_cases), ev)

    # 别的目录的失败输出里不许出现 T-13 拥有的文件
    hits = []
    for tid, body in secs.items():
        if tid == "T-13" or tid not in failed:
            continue
        body_fwd = body.replace("\\", "/")
        for tok in T13_OWNED_TOKENS:
            if tok in body_fwd:
                hits.append("%s 的失败输出提到 %s" % (tid, tok))
    if hits:
        fail("失败项涉及 T-13 拥有的文件：%s" % "; ".join(hits[:6]), ev)
    ev.append("其余目录的失败输出里，T-13 拥有的文件命中 0 处")

    if code != 0:
        ev.append("note：全套 exit=%d 不是 0；红的是 %s，都是别的任务自己的 QA 问题（上面列了它们的失败用例），"
                  "没有任何一项指向 T-13 拥有的文件。本条按清单第二句判：失败项不涉及 T-13 的文件。"
                  % (code, ", ".join(failed) or "无"))
    return ev


# ---------------------------------------------------------------- main

def main() -> int:
    print("qa/T-13 cases —— 任务 T-13（fix-check-findings），用例清单 .dsh-crew/jobs/fix-check-findings/T-13-plan.md")
    print("仓库：%s" % REPO)
    print("解释器：%s" % PYTHON)
    print("")
    global _ok, _fail
    for fn in (c01, c02, c03, c04, c05, c06, c07, c08, c09, c10):
        fn()
    print("")
    print("T-13 RESULT: %d/%d ok, exit %d" % (_ok, _ok + _fail, 0 if _fail == 0 else 1))
    if _fail:
        print("T-13 有用例不通过 (exit=1)")
        return 1
    print("T-13 用例全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
