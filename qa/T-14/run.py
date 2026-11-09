# -*- coding: utf-8 -*-
"""qa/T-14/run.py — T-14（便携启动契约）的独立验收用例。

清单来源：`.dsh-crew/jobs/portable-package/M1-qa-plan.md` 的 **T-14** 段（18 条，一条一条照写）。
DoD 来源：`docs/tasks/tasks.md` v2 的 T-14（DoD 1~6）。

做了什么：
  · 复用工程师的两个单测（T14-01/04/05），并**独立**核对"单测没有缩水"（T14-02）；
  · 独立探针：两个解释器都不存在 → `main()` 返回 1 且打印找过的路径（T14-03，把 `_launch.py`
    复制到没有 `.venv` 的临时目录再跑——在 webapp 目录里跑会回落到真 venv，验不到这一条）；
  · 独立静态核对 `_setenv.bat` 的 13+7=20 行、WB_HOME 单反斜杠、无条件覆盖、PATH 顺序、
    入口 call 关系、纯 ASCII、无绝对路径（T14-06~12）——**不读工程师的解析函数**；
  · 独立 `cmd /c` 反例：用户机残留被无条件覆盖（T14-13）；
  · 契约表 ↔ bat 逐行核对（T14-14，§2 表转录在下面，文档在时再与文档核对一次）；
  · 起停：`--restart` 起一个真实服务，抓子进程命令行证明不设 `WB_PYTHON` 时用 venv 解释器
    （T14-16）；8789 上已有本工作台时 `_launch.py` 打印"已在 N 运行"且退出 0（T14-15）；
  · 既有回归两条（T14-17）+ 入口自检（T14-18）。
  · 只杀本用例自己起的进程（`finally` 里按记录的 pid 停），只删自己建的临时目录。

跑法（仓库根，必须用这个解释器）：
    pipeline\\.venv\\Scripts\\python.exe qa\\T-14\\run.py

判绿：每条 `PASS`，最后 `通过 N / M`，全过退出 0；任一条 `FAIL`/`DEFERRED` 退出 1。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # qa/T-14/run.py -> 仓库根
WEBAPP = REPO / "webapp"
PKG = REPO / "packaging"
TPL = PKG / "templates"
WEBAPP_PY = WEBAPP / ".venv" / "Scripts" / "python.exe"
WEBAPP_PYW = WEBAPP / ".venv" / "Scripts" / "pythonw.exe"
PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"

SETENV = TPL / "_setenv.bat"
ENTRIES = [
    ("启动工作台.bat", TPL / "启动工作台.bat"),
    ("start.bat", TPL / "start.bat"),
    ("重启工作台.bat", TPL / "重启工作台.bat"),
    ("检查环境.bat", TPL / "检查环境.bat"),
]
LAUNCH = WEBAPP / "_launch.py"
LAUNCH_TEST = WEBAPP / "tests" / "launch_portable.test.py"
TPL_TEST = PKG / "tests" / "templates.test.py"
DOC_LAUNCHER = Path(r"C:\Users\94453\AppData\Local\Temp\dsh-crew\d-tiktok-workbench"
                    r"\portable-package\docs\design\api\launcher-runtime.md")

# 契约的 20 行（2026-11-05 从 launcher-runtime.md 转录：变量 → "set 值" / "清空"；
# 安全修复后新增 PYTHONNOUSERSITE=1、PYTHONUSERBASE=空）。
# 文档在场时以**文档现表**为准（_doc_contract_rows 全文件扫）；这份常量只是文档家被删后的兜底，
# 这样作业结束后本用例仍然能核对 bat 有没有漂。
DOC_CONTRACT = {
    "WB_ROOT": ("set", "%~dp0"),
    "PY": ("set", "%WB_ROOT%python\\python.exe"),
    "WB_PYTHON": ("name", ""),          # 文档 §2 只写"由 _setenv.bat 设置"，没写具体值
    "WB_PYTHONW": ("set", "%WB_ROOT%python\\pythonw.exe"),
    "WB_PIPELINE_DIR": ("set", "%WB_ROOT%pipeline"),
    "WB_HOME": ("set", "%WB_ROOT%workbuddy"),
    "WB_LEDGER_DIR": ("set", "%WB_ROOT%ledger"),
    "WB_REVERSE_DIR": ("set", "%WB_ROOT%reverse"),
    "WB_LOG_DIR": ("set", "%WB_ROOT%logs"),
    "HF_HOME": ("set", "%WB_ROOT%models\\hf"),
    "PATH": ("set", "%WB_ROOT%python;%WB_ROOT%ffmpeg\\bin;%PATH%"),
    "PYTHONIOENCODING": ("set", "utf-8"),
    "PYTHONNOUSERSITE": ("set", "1"),   # 安全修复：不 import 用户 site-packages
    "PYTHONPATH": ("clear", ""),
    "PYTHONHOME": ("clear", ""),
    "PYTHONUSERBASE": ("clear", ""),
    "HF_HUB_CACHE": ("clear", ""),
    "HUGGINGFACE_HUB_CACHE": ("clear", ""),
    "XDG_CACHE_HOME": ("clear", ""),
    "HF_HUB_OFFLINE": ("clear", ""),
}

DEFAULT_CANDIDATE_PORTS = (8789, 8791, 8792, 8793, 8787)
_tmpdirs = []
_started = {}          # {"port": int, "pid": int} —— 本用例自己起的服务
_cache = {}            # 单测只跑一次

_ok = 0
_fail = 0


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


def _dec(raw: bytes) -> str:
    for enc in ("utf-8", "cp936", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _env(extra=None, drop=()):
    env = dict(os.environ)
    for key in drop:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (extra or {}).items():
        env[key] = str(value)
    return env


def _run(argv, cwd=None, extra=None, drop=(), timeout=180):
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd or REPO),
                          capture_output=True, env=_env(extra, drop), timeout=timeout)
    out, err = _dec(proc.stdout), _dec(proc.stderr)
    return proc.returncode, out, err, out + err


def _ps(script, timeout=90):
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                timeout=timeout)


def _tail(text, n=6, width=400):
    lines = [ln.strip() for ln in (text or "").replace("\r", "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[-width:]


def _tmpdir(prefix="t14qa_"):
    """临时目录：优先 D:\\ 根（DoD 3 要求"输出里没有 C:\\Users"，放 %TEMP% 会自带 C:\\Users）。"""
    try:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir="D:\\"))
    except OSError:
        path = Path(tempfile.mkdtemp(prefix=prefix))
    _tmpdirs.append(path)
    return path


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _health(port, timeout=3.0):
    try:
        with _opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


def _is_workbench(data) -> bool:
    return (isinstance(data, dict) and data.get("ok") is True and "ts" in data
            and isinstance(data.get("diagnostics"), dict))


def _port_free(port) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _listener_pid(port):
    rc, out, err, _ = _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                          f"-ErrorAction SilentlyContinue | Select-Object -First 1 "
                          f"-ExpandProperty OwningProcess)")
    text = out.strip()
    return int(text) if text.isdigit() else None


def _boot_processes(port):
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*_boot.py --port {port}*' }} | "
              "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    rc, out, err, _ = _ps(script)
    text = out.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return [item for item in data if isinstance(item, dict)]


def _boot_process(port):
    procs = _boot_processes(port)
    return procs[0] if procs else None


def _kill(pid):
    if not pid:
        return
    _ps(f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue")


def _wait_port_free(port, timeout=20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_free(port):
            return True
        time.sleep(0.2)
    return _port_free(port)


def _start_instance():
    """用当前代码在本机起一个真实工作台（不设 WB_PYTHON），返回端口；失败抛 AssertionError。"""
    if _started.get("port"):
        return _started["port"]
    port = next((p for p in DEFAULT_CANDIDATE_PORTS if _port_free(p)), None)
    assert_(port is not None,
            f"候选端口 {list(DEFAULT_CANDIDATE_PORTS)} 全被占用，无法起自测实例")
    rc, out, err, text = _run([str(WEBAPP_PY), str(LAUNCH), "--restart", str(port)],
                              drop=("WB_PYTHON", "WB_PYTHONW", "WB_LOG_DIR"), timeout=180)
    assert_(rc == 0, f"起实例失败 rc={rc}（端口 {port}）；输出尾：{_tail(text)}")
    assert_(f"127.0.0.1:{port}" in text, f"启动器没有打印端口 {port} 的地址：{_tail(text)}")
    data = _health(port)
    assert_(_is_workbench(data), f"端口 {port} 上的 /api/health 不是本工作台：{data!r}")
    procs = _boot_processes(port)
    assert_(procs, f"端口 {port} 上找不到 _boot.py 子进程（无法记录 pid 用于清理）")
    pids = {int(p["ProcessId"]) for p in procs if p.get("ProcessId")}
    listener = _listener_pid(port)
    if listener:
        pids.add(int(listener))
    _started.update(port=port, pids=sorted(pids))
    return port


# ────────────────────────── T14-01 / 04 / 05：复用单测 ──────────────────────────
def _launcher_test():
    if "launcher" not in _cache:
        assert_(LAUNCH_TEST.is_file(), f"缺少 {LAUNCH_TEST}")
        _cache["launcher"] = _run([str(WEBAPP_PY), str(LAUNCH_TEST)], timeout=300)
    return _cache["launcher"]


def _summary_line(text):
    for line in reversed([ln.strip() for ln in text.replace("\r", "").splitlines() if ln.strip()]):
        m = re.match(r"通过\s*(\d+)\s*/\s*(\d+)", line)
        if m:
            return line, int(m.group(1)), int(m.group(2))
    return None, None, None


def t14_01():
    rc, out, err, text = _launcher_test()
    line, ok, total = _summary_line(text)
    assert_(rc == 0, f"退出码 {rc}；输出尾：{_tail(text)}")
    assert_("Traceback" not in text, f"输出里有 Traceback：{_tail(text)}")
    assert_(line is not None, f"没有汇总行『通过 N / N』：{_tail(text)}")
    assert_(ok == total and total > 0, f"汇总行不是全绿：{line}")
    return [f"exit={rc}；{line}", f"命令：webapp\\.venv\\Scripts\\python.exe {LAUNCH_TEST.relative_to(REPO)}"]


def t14_02():
    """六组 grep：单测里六个行为各至少一条断言/调用（防缩水）。"""
    text = LAUNCH_TEST.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    groups = [
        ("WB_PYTHON 优先", lambda ln: "WB_PYTHON" in ln and "优先" in ln),
        ("WB_PYTHONW / pythonw.exe", lambda ln: "WB_PYTHONW" in ln),
        ("WB_LOG_DIR", lambda ln: "WB_LOG_DIR" in ln),
        ("RESTART_BAT", lambda ln: "RESTART_BAT" in ln),
        ("--restart kill/pid（restart_plan）", lambda ln: "restart_plan" in ln),
        ("双解释器缺失 → rc 1 + 两路径", lambda ln: ("missing_env" in ln or "missing_venv" in ln)
                                          or ("rc == 1" in ln and "main()" in ln)),
    ]
    evidence, missing = [], []
    for label, pred in groups:
        hits = [i + 1 for i, ln in enumerate(lines) if pred(ln)]
        if hits:
            evidence.append(f"{label}: 命中 {len(hits)} 行，行号 {hits[:4]}{'…' if len(hits) > 4 else ''}")
        else:
            missing.append(label)
    # 这六组之外，还要求 kill 命令本身带 /PID 且按 pid 杀（plan 第 4 条）
    pid_ok = any(("/PID" in ln or "taskkill" in ln) for ln in lines)
    assert_(not missing, f"单测里缺少这些覆盖（用例被删/缩水）：{missing}")
    assert_(pid_ok, "单测里没有 taskkill//PID 相关断言（--restart 只杀一个 pid 的点没了）")
    return evidence + ["另：找得到 taskkill//PID 断言 ✓"]


def t14_03():
    """独立探针：两个解释器都不存在 → main() 返回 1 并打印找过的两个路径。"""
    tmp = _tmpdir("t14qa_nolpy_")
    shutil.copyfile(LAUNCH, tmp / "_launch.py")
    env_py = tmp / "missing_env_python.exe"
    env_pyw = tmp / "missing_env_pythonw.exe"
    rc, out, err, text = _run([str(WEBAPP_PY), str(tmp / "_launch.py")],
                              cwd=tmp,
                              extra={"WB_PYTHON": str(env_py), "WB_PYTHONW": str(env_pyw)},
                              timeout=120)
    venv_candidate = tmp / ".venv" / "Scripts" / "python.exe"      # 副本旁边没有 .venv
    assert_(rc == 1, f"期望退出码 1，实得 {rc}；输出：{_tail(text)}")
    assert_("找不到 Python 解释器" in text, f"没有打印『找不到 Python 解释器』：{_tail(text)}")
    assert_(str(env_py) in text, f"输出里没有 WB_PYTHON 候选 {env_py}：{_tail(text)}")
    assert_(str(venv_candidate) in text, f"输出里没有 venv 候选 {venv_candidate}：{_tail(text)}")
    assert_("[launcher] 在" not in text, f"解释器缺失时仍试图起服务：{_tail(text)}")
    return [f"exit={rc}（副本目录无 .venv，所以两个候选都不存在）",
            f"输出含两个候选：{env_py.name} / {venv_candidate}"]


def t14_04():
    """--restart 的"该不该杀"：三种状态只有本工作台 kill，且命令只带一个 pid（复用单测断言）。"""
    rc, out, err, text = _launcher_test()
    want = [
        "free / foreign 不杀（哪怕拿到了 pid）",
        "workbench → kill，taskkill 只带一个 pid、不按名字批量杀",
        "workbench 但拿不到 pid → 不杀",
    ]
    missing = [w for w in want if f"PASS {w}" not in text]
    assert_(rc == 0, f"单测退出码 {rc}：{_tail(text)}")
    assert_(not missing, f"单测里这些断言没有 PASS 行（被改名或删掉）：{missing}")
    test_text = LAUNCH_TEST.read_text(encoding="utf-8", errors="replace")
    hits = [i + 1 for i, ln in enumerate(test_text.splitlines()) if "restart_plan" in ln]
    return [f"三条断言均 PASS：{want}", f"测试文件 restart_plan 行号：{hits[:6]}"]


def t14_05():
    assert_(TPL_TEST.is_file(), f"缺少 {TPL_TEST}")
    rc, out, err, text = _run([str(WEBAPP_PY), str(TPL_TEST)], timeout=300)
    line, ok, total = _summary_line(text)
    assert_(rc == 0, f"退出码 {rc}；输出尾：{_tail(text)}")
    assert_(line is not None and ok == total and total > 0, f"汇总行不是全绿：{line!r}")
    return [f"exit={rc}；{line}"]


# ────────────────────────── T14-06 ~ 12：独立静态核对 ──────────────────────────
SET_NAMES = ["WB_ROOT", "PY", "WB_PYTHON", "WB_PYTHONW", "WB_PIPELINE_DIR", "WB_HOME",
             "WB_LEDGER_DIR", "WB_REVERSE_DIR", "WB_LOG_DIR", "HF_HOME", "PATH",
             "PYTHONIOENCODING", "PYTHONNOUSERSITE"]
CLEAR_NAMES = ["PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "HF_HUB_CACHE",
               "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME", "HF_HUB_OFFLINE"]
SET_RE = re.compile(r'^set "([A-Za-z_][A-Za-z0-9_]*)=(.*)"$')


def _read(path: Path) -> str:
    assert_(path.is_file(), f"缺少文件 {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_setenv(text):
    """(设值 dict, 清空 list, 非法行 list)，行号一并返回。"""
    sets, clears, bad = {}, [], []
    for idx, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.lower().startswith("rem ") or line.startswith("::") or line == "@echo off":
            continue
        if line.lower().startswith("set"):
            m = SET_RE.match(line)
            if not m:
                bad.append((idx, line))
                continue
            name, value = m.group(1), m.group(2)
            if value == "":
                clears.append((idx, name))
            else:
                sets[name] = (idx, value)
    return sets, clears, bad


def t14_06():
    """DoD 2(a)：恰好 13 条设值 + 7 条清空 = 20 行，名字一个不多一个不少、无重复。"""
    text = _read(SETENV)
    sets, clears, bad = _parse_setenv(text)
    assert_(not bad, f"有不是裸赋值的 set 行：{bad}")
    assert_(sorted(sets) == sorted(SET_NAMES),
            f"设值变量不符：多 {sorted(set(sets) - set(SET_NAMES))}，缺 {sorted(set(SET_NAMES) - set(sets))}")
    assert_(sorted(n for _, n in clears) == sorted(CLEAR_NAMES),
            f"清空变量不符：实得 {sorted(n for _, n in clears)}")
    total_lines = len(sets) + len(clears)
    assert_(len(sets) == len(SET_NAMES) and len(clears) == len(CLEAR_NAMES) and total_lines == 20,
            f"应 13+7=20 行，实得 {len(sets)}+{len(clears)}={total_lines}")
    # 安全修复的取值也要钉住：PYTHONNOUSERSITE=1（不 import 用户 site-packages）
    assert_(sets.get("PYTHONNOUSERSITE", (0, ""))[1] == "1",
            f"PYTHONNOUSERSITE 设值应为 '1'，实得 {sets.get('PYTHONNOUSERSITE')!r}")
    rows = [f"set  {n} = {sets[n][1]}  (L{sets[n][0]})" for n in SET_NAMES]
    rows += [f"clear {n}  (L{idx})" for idx, n in clears]
    return [f"13 设值 + 7 清空 = {total_lines} 行，无重复、无多余（PYTHONNOUSERSITE=1）"] + rows


def t14_07():
    """DoD 2(b)：WB_HOME 值逐字是 %WB_ROOT%workbuddy（只有一个反斜杠）。"""
    sets, _, _ = _parse_setenv(_read(SETENV))
    assert_("WB_HOME" in sets, "没有 WB_HOME")
    idx, value = sets["WB_HOME"]
    assert_(value == "%WB_ROOT%workbuddy",
            f"WB_HOME 值={value!r}，必须恰好是 '%WB_ROOT%workbuddy'")
    # WB_ROOT 的值是 %~dp0，自带结尾反斜杠；所以值里不该再有任何**字面**反斜杠。
    # 写成 %WB_ROOT%\workbuddy 会变成两个（双反斜杠）——这就是判红条件。
    assert_("\\" not in value, f"值里出现字面反斜杠：{value!r}（写成 %WB_ROOT%\\workbuddy 会变双反斜杠）")
    assert_("%WB_ROOT%\\workbuddy" not in value, f"出现双反斜杠写法：{value!r}")
    return [f"L{idx}: WB_HOME = {value}",
            "值里字面反斜杠 = 0；分隔用的那个反斜杠由 WB_ROOT(%~dp0) 自带，没有写成 %WB_ROOT%\\workbuddy"]


def t14_08():
    """DoD 2(c)：无 `if not defined`，每个 set 都是裸赋值（不在任何条件块里）。"""
    text = _read(SETENV)
    low = text.lower().replace("\r", "")
    assert_("if not defined" not in low, "全文出现 'if not defined'（无条件覆盖被破坏）")
    # 括号深度扫描：任何 set 行都必须在深度 0（不在 if 块里）
    depth = 0
    offenders = []
    for idx, raw in enumerate(text.splitlines(), 1):
        line = raw.split("rem ", 1)[0] if raw.strip().lower().startswith("rem ") else raw
        line = re.sub(r'"[^"]*"', '""', line)          # 去掉引号内的括号干扰
        before = depth
        depth += line.count("(") - line.count(")")
        if re.match(r"^\s*set\s", raw, re.IGNORECASE) and before != 0:
            offenders.append((idx, raw.strip()))
    assert_(not offenders, f"这些 set 在条件块里（不是裸赋值）：{offenders}")
    sets, clears, bad = _parse_setenv(text)
    assert_(not bad, f"有不是裸赋值的 set 行：{bad}")
    return ["'if not defined' 命中 0 行", f"深度 0 的裸 set：{len(sets)} 条设值 + {len(clears)} 条清空",
            "全文没有把 set 包进任何 ( ) 块"]


def t14_09():
    """DoD 2(d)：PATH = python;ffmpeg\\bin;%PATH%，三个片段的下标严格递增。"""
    sets, _, _ = _parse_setenv(_read(SETENV))
    assert_("PATH" in sets, "没有 PATH")
    idx, value = sets["PATH"]
    pos_py = value.find("%WB_ROOT%python")
    pos_ff = value.find("%WB_ROOT%ffmpeg\\bin")
    pos_old = value.find("%PATH%")
    assert_(min(pos_py, pos_ff, pos_old) >= 0,
            f"PATH 缺少片段：python={pos_py} ffmpeg={pos_ff} %PATH%={pos_old}；值={value!r}")
    assert_(pos_py < pos_ff < pos_old,
            f"PATH 顺序不对：python@{pos_py} 应 < ffmpeg@{pos_ff} 应 < %PATH%@{pos_old}")
    parts = value.split(";")
    assert_(parts == ["%WB_ROOT%python", "%WB_ROOT%ffmpeg\\bin", "%PATH%"],
            f"PATH 片段应恰好 3 段且顺序固定，实得 {parts!r}")
    return [f"L{idx}: {value}", f"下标：python={pos_py} < ffmpeg={pos_ff} < %PATH%={pos_old}"]


def t14_10():
    """DoD 2(e)：四个入口都 call _setenv.bat；_setenv.bat 不自 call、只带守卫 call local，且在最后。"""
    evidence = []
    for name, path in ENTRIES:
        text = _read(path)
        idx = next((i + 1 for i, ln in enumerate(text.splitlines())
                    if 'call "%~dp0_setenv.bat"' in ln), None)
        assert_(idx is not None, f"{name} 没有 call \"%~dp0_setenv.bat\"")
        evidence.append(f"{name}: L{idx} call \"%~dp0_setenv.bat\"")
    text = _read(SETENV)
    self_calls = [i + 1 for i, ln in enumerate(text.splitlines())
                  if re.search(r'call\s+"%~dp0_setenv\.bat"', ln)]
    assert_(not self_calls, f"_setenv.bat 里出现对自己的 call（会无限递归）：行 {self_calls}")
    guard = [i + 1 for i, ln in enumerate(text.splitlines())
             if re.match(r'^\s*if\s+exist\s+"%~dp0_setenv\.local\.bat"\s+call\s+"%~dp0_setenv\.local\.bat"',
                         ln, re.IGNORECASE)]
    assert_(guard, "_setenv.bat 里没有带 if exist 守卫的 call _setenv.local.bat")
    effective = [(i + 1, ln.strip()) for i, ln in enumerate(text.splitlines())
                 if ln.strip() and not ln.strip().lower().startswith("rem ")
                 and not ln.strip().startswith("::") and ln.strip().lower() != "@echo off"]
    assert_(len(effective) >= 2, f"_setenv.bat 有效语句太少：{effective}")
    last_no, last_line = effective[-1]
    prev_no, prev_line = effective[-2]
    assert_(last_line.lower() == "exit /b 0",
            f"最后一条有效语句必须是 'exit /b 0'（把调用前的旧 errorlevel 清成 0，"
            f"否则入口 bat 会误报加载失败），实得 L{last_no}: {last_line!r}")
    assert_(re.match(r'^if\s+exist\s+"%~dp0_setenv\.local\.bat"\s+call\s+"%~dp0_setenv\.local\.bat"$',
                     prev_line, re.IGNORECASE),
            f"'exit /b 0' 之前必须紧跟带 if exist 守卫的 call _setenv.local.bat，"
            f"实得 L{prev_no}: {prev_line!r}")
    evidence.append(f"_setenv.bat: L{prev_no} 守卫 call local → L{last_no} exit /b 0（最后一条有效语句）")
    evidence.append(f"_setenv.bat 对自己 call 的行数 = 0")
    return evidence


def t14_11():
    """DoD 2(f)：五个 .bat 每个字节 < 0x80（文件名中文不影响内容）。"""
    evidence = []
    for name, path in [("_setenv.bat", SETENV)] + ENTRIES:
        assert_(path.is_file(), f"缺少 {name}")
        raw = path.read_bytes()
        bad = [(i, b) for i, b in enumerate(raw) if b >= 0x80]
        assert_(not bad, f"{name} 有非 ASCII 字节（前 5 个）：{bad[:5]}")
        evidence.append(f"{name}: {len(raw)} 字节，最大字节值 {max(raw):#04x}（< 0x80）")
    return evidence


def t14_12():
    """DoD 2(g)：_setenv.bat 不含构建机/作者机绝对路径。"""
    text = _read(SETENV)
    hits = []
    for pattern, label in ((r"[A-Za-z]:\\", "盘符绝对路径"), (r"C:\\Users", "C:\\Users"),
                           (r"/home/", "/home/")):
        found = re.findall(pattern, text)
        if found:
            hits.append((label, found[:3]))
    assert_(not hits, f"_setenv.bat 里出现绝对路径：{hits}")
    return ["[A-Za-z]:\\ 命中 0；C:\\Users 命中 0；/home/ 命中 0"]


# ────────────────────────── T14-13：cmd /c 反例 ──────────────────────────
def t14_13():
    tmp = _tmpdir("t14qa_setenv_")
    shutil.copyfile(SETENV, tmp / "_setenv.bat")
    wrapper = tmp / "_call.bat"
    wrapper.write_text(
        "@echo off\r\n"
        'set "WB_HOME=C:\\Users\\x\\.workbuddy"\r\n'
        'set "WB_LEDGER_DIR=D:\\old\\ledger"\r\n'
        'set "WB_REVERSE_DIR=D:\\old\\reverse"\r\n'
        'set "PYTHONPATH=C:\\Users\\x\\pylib"\r\n'
        'set "HF_HUB_CACHE=C:\\Users\\x\\.cache\\hub"\r\n'
        'set "XDG_CACHE_HOME=D:\\old\\xdg"\r\n'
        'call "%~dp0_setenv.bat"\r\n'
        "echo [HOME]=%WB_HOME%\r\n"
        "echo [LEDGER]=%WB_LEDGER_DIR%\r\n"
        "echo [REVERSE]=%WB_REVERSE_DIR%\r\n"
        "echo [PYTHONPATH]=%PYTHONPATH%\r\n"
        "echo [HF_HUB_CACHE]=%HF_HUB_CACHE%\r\n"
        "echo [XDG_CACHE_HOME]=%XDG_CACHE_HOME%\r\n"
        "echo [ROOT]=%WB_ROOT%\r\n",
        encoding="ascii", newline="")
    rc, out, err, text = _run(["cmd", "/c", str(wrapper)], cwd=tmp, timeout=90)
    clean = text.replace("\r", "")
    assert_(rc == 0, f"cmd 退出码 {rc}；输出：{_tail(clean)}")
    lines = [ln.strip() for ln in clean.splitlines()]
    expect = {
        f"[HOME]={tmp}\\workbuddy": "[HOME] 被包内值覆盖",
        f"[LEDGER]={tmp}\\ledger": "[LEDGER] 被包内值覆盖",
        f"[REVERSE]={tmp}\\reverse": "[REVERSE] 被包内值覆盖",
        "[PYTHONPATH]=": "[PYTHONPATH] 被清空",
        "[HF_HUB_CACHE]=": "[HF_HUB_CACHE] 被清空",
        "[XDG_CACHE_HOME]=": "[XDG_CACHE_HOME] 被清空",
        f"[ROOT]={tmp}\\": "[ROOT] = bat 所在目录",
    }
    for want, label in expect.items():
        assert_(want in lines, f"{label} 不对，期望行 {want!r}；实得 {lines}")
    if str(tmp).upper().startswith("D:\\"):
        assert_("C:\\Users" not in clean, f"输出里残留 C:\\Users：{_tail(clean)}")
    assert_("D:\\old" not in clean, f"输出里残留 D:\\old：{_tail(clean)}")
    return [f"预设 C:\\Users\\x\\.workbuddy / D:\\old\\ledger / D:\\old\\reverse 全部被覆盖"] + \
           [ln for ln in lines if ln.startswith("[")][:4]


# ────────────────────────── T14-14：契约表 ↔ bat ──────────────────────────
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _doc_contract_rows(text):
    """从契约文档里抽 (变量 → (kind, value, 所在小节))，并返回 (rows, notes)。

    全文件扫表格行、不只看 §2：本轮文档把新增的两行粘到了文件最开头（连 H1 标题都被吃进
    第 3 行），§2 表里反而没有。内容是契约的一部分，位置异常必须**报出来**，但不能因此漏掉
    真实契约行。§3 的入口表（首列是 `.bat` 名）靠"首列必须是环境变量名"筛掉。
    """
    section = "（文件开头，标题之前）"
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("|") or line.startswith("|---") or "变量" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        name = cells[0].strip("`").strip()
        if not ENV_NAME_RE.match(name):
            continue
        who = cells[1]
        if "_setenv.bat" not in who:
            continue
        m = re.search(r"[=＝：:]\s*(`([^`]+)`|空)", who)
        if not m:
            kind, value = "name", ""
        elif m.group(1) == "空":
            kind, value = "clear", ""
        else:
            kind, value = "set", m.group(2).strip()
        found.append((section, name, kind, value))

    rows, notes = {}, []
    for section, name, kind, value in found:
        in_section2 = section.startswith("2.")
        if name in rows:
            if rows[name][:2] != (kind, value):
                notes.append(f"CONFLICT {name}: 文档里两处写法不一致（{rows[name][:2]!r} vs "
                             f"{(kind, value)!r}，小节「{section}」）")
            else:
                notes.append(f"{name}: 在「{section}」有重复行（与已收录的一致，不影响判据）")
            continue
        rows[name] = (kind, value, section)
        if not in_section2:
            notes.append(f"{name}: 契约行不在 §2，而在「{section}」")
    return rows, notes


def t14_14():
    """DoD 4：契约表里"谁设置=_setenv.bat"的每一行与 bat 实际一致（文档在场以文档现表为准）。"""
    sets, clears, _ = _parse_setenv(_read(SETENV))
    clear_names = {n for _, n in clears}
    notes = []
    if DOC_LAUNCHER.is_file():
        rows, notes = _doc_contract_rows(_read(DOC_LAUNCHER))
        source = f"文档现表 launcher-runtime.md（{len(rows)} 行）"
        assert_(rows, f"文档里解析不到任何『谁设置=_setenv.bat』的行：{DOC_LAUNCHER}")
        assert_(not [n for n in notes if n.startswith("CONFLICT")],
                f"文档自身前后矛盾：{notes}")
    else:
        rows = {name: (kind, value, "run.py 内转录")
                for name, (kind, value) in DOC_CONTRACT.items()}
        source = f"run.py 内转录的契约（文档家不在，{len(rows)} 行）"
    evidence = [f"契约来源：{source}"]
    mismatches = []
    for name, (kind, doc_value, _section) in rows.items():
        if name in sets:
            actual = ("set", sets[name][1])
        elif name in clear_names:
            actual = ("clear", "")
        else:
            actual = ("missing", "")
        if kind == "name":
            ok = name in sets or name in clear_names
            doc_text = "由 _setenv.bat 设置（文档未写具体值）"
        else:
            ok = actual[0] == kind and (kind != "set" or actual[1] == doc_value)
            doc_text = kind + (f"={doc_value}" if doc_value else "")
        evidence.append(f"{name}: 契约 {doc_text} / bat {actual[0]}"
                        f"{'=' + actual[1] if actual[1] else ''} / {'一致' if ok else '不一致'}")
        if not ok:
            mismatches.append(name)
    assert_(not mismatches, f"契约表与 bat 不一致：{mismatches}")
    extra = sorted((set(sets) | clear_names) - set(rows))
    assert_(not extra, f"bat 里有契约表没写的变量：{extra}")
    missing = sorted(set(rows) - (set(sets) | clear_names))
    assert_(not missing, f"契约表里有 bat 没写的变量：{missing}")
    n_set = sum(1 for k, _v, _s in rows.values() if k in ("set", "name"))
    n_clear = sum(1 for k, _v, _s in rows.values() if k == "clear")
    evidence += [f"NOTE：{n}" for n in notes]
    evidence.append(f"契约合计 {len(rows)} 行（{n_set} 设值 + {n_clear} 清空），与 bat 全部一致")
    return evidence


# ────────────────────────── T14-15 / 16：起停 ──────────────────────────
def t14_15():
    """DoD 5 前半：8789 已被本工作台占用时，_launch.py 打印"已在 N 运行"且退出 0。"""
    if _is_workbench(_health(8789)):
        port, source = 8789, "8789 上已有的工作台（本用例没有起也没有停）"
    else:
        port, source = _start_instance(), "本用例自起的实例"
    pid = _listener_pid(port)
    assert_(pid is not None, f"端口 {port} 上没有监听进程（{source}）")
    rc, out, err, text = _run([str(WEBAPP_PY), str(LAUNCH)],
                              drop=("WB_PYTHON", "WB_PYTHONW", "WB_LOG_DIR"), timeout=120)
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；输出：{_tail(text)}")
    m = re.search(r"\[launcher\] 工作台已在 (\d+) 运行", text)
    assert_(m, f"没有打印『[launcher] 工作台已在 <port> 运行』：{_tail(text)}")
    assert_(int(m.group(1)) == port, f"打印的端口 {m.group(1)} 与真实监听端口 {port} 不一致")
    say = next((ln.strip() for ln in text.splitlines() if "工作台已在" in ln), "")
    return [f"来源：{source}；监听 pid={pid}", f"exit={rc}；{say}",
            "NOTE：浏览器是否弹出只能人工看（本条判退出码与打印行）"]


def t14_16():
    """DoD 5 后半：不设 WB_PYTHON 时仍用 webapp\\.venv 的解释器（解析 + 真实子进程命令行）。"""
    code = ("import sys; sys.path.insert(0, r'%s'); import _launch; "
            "print('PY=' + str(_launch.PY)); print('PYW=' + str(_launch.PYW))" % WEBAPP)
    rc, out, err, text = _run([str(WEBAPP_PY), "-c", code],
                              drop=("WB_PYTHON", "WB_PYTHONW"), timeout=120)
    assert_(rc == 0, f"解析探针退出码 {rc}：{_tail(text)}")
    py = next((ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("PY=")), "")
    pyw = next((ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("PYW=")), "")
    assert_(py == str(WEBAPP_PY), f"不设 WB_PYTHON 时 PY={py!r}，期望 {str(WEBAPP_PY)!r}")
    assert_(pyw == str(WEBAPP_PYW), f"不设 WB_PYTHONW 时 PYW={pyw!r}，期望 {str(WEBAPP_PYW)!r}")

    port = _start_instance()
    info = _boot_process(port)
    assert_(info is not None, f"端口 {port} 上找不到 _boot.py 子进程")
    cmdline = str(info.get("CommandLine") or "")
    exe = str(info.get("ExecutablePath") or "")
    venv_scripts = str(WEBAPP / ".venv" / "Scripts").lower()
    assert_("_boot.py" in cmdline and f"--port {port}" in cmdline,
            f"子进程命令行不是 _boot.py --port {port}：{cmdline!r}")
    assert_(venv_scripts in cmdline.lower() or venv_scripts in exe.lower(),
            f"子进程不是 webapp\\.venv 的解释器：exe={exe!r} cmdline={cmdline!r}")
    assert_("pathlib" not in cmdline.lower(), "（内部）命令行异常")
    launched_by = os.path.basename(exe) or os.path.basename(cmdline.split('"')[1] if '"' in cmdline else "")
    return [f"解析探针（未设 WB_PYTHON）：PY={py}；PYW={pyw}",
            f"真实子进程：pid={info['ProcessId']} exe={exe}",
            f"命令行：{cmdline[:220]}",
            f"说明：spawn 用的是 PY 同目录的 {launched_by}（T-14 DoD：pythonw 优先/推导），不是 python.exe"]


# ────────────────────────── T14-17：既有回归 ──────────────────────────
def t14_17():
    evidence = []
    for name in ("launch_version.test.py", "port_override.test.py"):
        path = WEBAPP / "tests" / name
        assert_(path.is_file(), f"缺少既有用例 {path}")
        rc, out, err, text = _run([str(WEBAPP_PY), str(path)], timeout=300)
        line, ok, total = _summary_line(text)
        assert_(rc == 0, f"{name} 退出码 {rc}：{_tail(text)}")
        evidence.append(f"{name}: exit={rc}" + (f"；{line}" if line else f"；{_tail(text, 2)}"))
    return evidence


# ────────────────────────── T14-18：入口自检 ──────────────────────────
def t14_18():
    assert_(Path(__file__).is_file(), f"缺少 {__file__}")
    assert_(Path(sys.executable).resolve() == PIPELINE_PY.resolve(),
            f"本用例必须用 pipeline\\.venv 跑：sys.executable={sys.executable!r}，"
            f"期望 {str(PIPELINE_PY)!r}")
    ids = [cid for cid, _, _ in CASES]
    assert_(len(ids) == 18, f"应注册 18 条用例，实得 {len(ids)}：{ids}")
    assert_(len(set(ids)) == 18, f"用例 id 有重复：{ids}")
    assert_(ids == [f"T14-{i:02d}" for i in range(1, 19)], f"用例 id 不是 T14-01~18：{ids}")
    return [f"解释器：{sys.executable}", f"用例注册：{len(ids)} 条（T14-01 ~ T14-18），无重复"]


CASES = [
    ("T14-01", "launcher 单测通过（launch_portable.test.py 退出 0、无 Traceback）", t14_01),
    ("T14-02", "单测覆盖 DoD 1 的六个行为（防缩水，六组 grep）", t14_02),
    ("T14-03", "两个解释器都不存在 → main() 返回 1 且打印找过的两个路径（独立探针）", t14_03),
    ("T14-04", "--restart 的该不该杀：三状态只有本工作台 kill、命令只带一个 pid", t14_04),
    ("T14-05", "templates 单测通过（templates.test.py 退出 0）", t14_05),
    ("T14-06", "_setenv.bat 恰好 13 条设值 + 7 条清空 = 20 行，名字一个不多一个不少", t14_06),
    ("T14-07", "WB_HOME 逐字是 %WB_ROOT%workbuddy（单反斜杠）", t14_07),
    ("T14-08", "无条件覆盖：无 if not defined，每个 set 都是裸赋值", t14_08),
    ("T14-09", "PATH 精确值/顺序：python < ffmpeg\\bin < %PATH%", t14_09),
    ("T14-10", "四个入口都 call _setenv.bat；它不自 call、守卫 call local 之后紧跟 exit /b 0", t14_10),
    ("T14-11", "五个 .bat 每字节 < 0x80", t14_11),
    ("T14-12", "_setenv.bat 不含盘符绝对路径 / C:\\Users / /home/", t14_12),
    ("T14-13", "cmd /c 反例：用户机残留的 WB_HOME/WB_LEDGER_DIR/WB_REVERSE_DIR 被包内值覆盖", t14_13),
    ("T14-14", "环境变量契约（launcher-runtime.md §2）与 bat 逐行一致", t14_14),
    ("T14-15", "8789 已被本工作台占用时：打印『已在 N 运行』且退出 0，端口与真实监听一致", t14_15),
    ("T14-16", "不设 WB_PYTHON 时仍用 webapp\\.venv 的解释器（解析 + 真实子进程命令行）", t14_16),
    ("T14-17", "既有回归仍绿：launch_version.test.py / port_override.test.py", t14_17),
    ("T14-18", "QA 入口自检：用 pipeline\\.venv 跑、18 条用例齐全", t14_18),
]


def _cleanup():
    port = _started.get("port")
    pids = [int(p) for p in _started.get("pids", []) if p]
    for pid in pids:
        _kill(pid)
    if port:
        freed = _wait_port_free(port, timeout=15.0)
        if not freed:
            # 只停"本用例起的那个实例"：端口上的监听者必须真的是 _boot.py --port <port>。
            listener = _listener_pid(port)
            ours = {int(p["ProcessId"]) for p in _boot_processes(port) if p.get("ProcessId")}
            if listener and int(listener) in ours:
                print(f"        [cleanup] 端口 {port} 仍被本用例起的 _boot.py（pid={listener}）占着，停掉它")
                _kill(listener)
                freed = _wait_port_free(port, timeout=15.0)
        print(f"        [cleanup] 自起实例 pid={pids}；端口 {port} "
              f"{'已释放' if freed else '仍被占用（请人工检查）'}")
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)


def main():
    print("T-14 便携启动契约 · QA 用例（清单：.dsh-crew/jobs/portable-package/M1-qa-plan.md T-14 段）")
    print(f"仓库：{REPO}")
    print("")
    try:
        for cid, title, fn in CASES:
            check(cid, title, fn)
    finally:
        _cleanup()
    total = _ok + _fail
    print(f"\n通过 {_ok} / {total}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
