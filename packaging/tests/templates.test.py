# -*- coding: utf-8 -*-
"""T-14 启动模板契约单测（`packaging\\templates\\*.bat`）。

覆盖 T-14 DoD 2 与 DoD 3：
  (a) `_setenv.bat` 12 条设值 + 6 条清空 = 18 行
  (b) `WB_HOME` 指向 `%WB_ROOT%workbuddy`（PRD v3 M1-(b)：LLM 配置只落包内）
  (c) 无条件覆盖：不出现 `if not defined`，每个 set 都是裸赋值
  (d) `PATH` 的精确值与顺序：python 在 ffmpeg\\bin 之前，两者都在 %PATH% 之前
  (e) 入口 `.bat` 都 `call "%~dp0_setenv.bat"`
  (f) `_setenv.bat` 与入口 `.bat` 每个字节 < 0x80
  (g) `_setenv.bat` 不含 `[A-Za-z]:\\` / `C:\\Users` / `/home/`
  (h) 结尾有 `if exist "%~dp0_setenv.local.bat" call ...`
  外加：真正的 `cmd /c` 反例——先塞用户机残留的 WB_HOME / WB_LEDGER_DIR / WB_REVERSE_DIR，
  再 call 模板，三个变量必须被包内值无条件覆盖（DoD 3）。
  外加：入口不含 `runas` / `net session` / `Start-Process -Verb`（PRD 需求 1）。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe packaging\\tests\\templates.test.py`
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent            # packaging/tests
PKG = HERE.parent                                  # packaging
TPL = PKG / "templates"
SETENV = TPL / "_setenv.bat"
LAUNCH = TPL / "启动工作台.bat"
START = TPL / "start.bat"

SET_NAMES = [
    "WB_ROOT", "PY", "WB_PYTHON", "WB_PYTHONW", "WB_PIPELINE_DIR", "WB_HOME",
    "WB_LEDGER_DIR", "WB_REVERSE_DIR", "WB_LOG_DIR", "HF_HOME", "PATH",
    "PYTHONIOENCODING",
]
CLEAR_NAMES = [
    "PYTHONPATH", "PYTHONHOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
    "XDG_CACHE_HOME", "HF_HUB_OFFLINE",
]
SET_RE = re.compile(r'^set "([A-Za-z_][A-Za-z0-9_]*)=(.*)"$')
ABS_PATH_RE = re.compile(r"[A-Za-z]:\\")

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  PASS {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:                                   # noqa: BLE001
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def read_text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8", errors="strict")


def parse_sets(text: str):
    """返回 (非空 set 字典, 清空变量列表, 非法 set 行列表)。"""
    sets, clears, bad = {}, [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("rem ") or line.startswith("::"):
            continue
        if re.match(r"(?i)^set\b", line):
            m = SET_RE.match(line)
            if not m:
                bad.append(line)
                continue
            name, value = m.group(1), m.group(2)
            if value == "":
                clears.append(name)
            else:
                sets[name] = value
    return sets, clears, bad


print("T-14 启动模板契约（templates）")

# ── (a) 18 行 + 名字齐全 ────────────────────────────────────────────
print("[a] _setenv.bat：12 设值 + 6 清空 = 18 行")


def _setenv_18_lines():
    sets, clears, bad = parse_sets(read_text(SETENV))
    assert_(not bad, f"有不是裸赋值的 set 行：{bad!r}")
    assert_(sorted(sets) == sorted(SET_NAMES),
            f"设值变量应恰好 {SET_NAMES}，实得 {sorted(sets)}")
    assert_(sorted(clears) == sorted(CLEAR_NAMES),
            f"清空变量应恰好 {CLEAR_NAMES}，实得 {sorted(clears)}")
    assert_(len(sets) + len(clears) == 18,
            f"应 12+6=18 行，实得 {len(sets)}+{len(clears)}")


check("12 条设值 + 6 条清空，变量名一个不多一个不少", _setenv_18_lines)


# ── 各变量取值 ──────────────────────────────────────────────────────
def _setenv_values():
    sets, _, _ = parse_sets(read_text(SETENV))
    want = {
        "WB_ROOT": "%~dp0",
        "PY": "%WB_ROOT%python\\python.exe",
        "WB_PYTHON": "%WB_ROOT%python\\python.exe",
        "WB_PYTHONW": "%WB_ROOT%python\\pythonw.exe",
        "WB_PIPELINE_DIR": "%WB_ROOT%pipeline",
        "WB_HOME": "%WB_ROOT%workbuddy",
        "WB_LEDGER_DIR": "%WB_ROOT%ledger",
        "WB_REVERSE_DIR": "%WB_ROOT%reverse",
        "WB_LOG_DIR": "%WB_ROOT%logs",
        "HF_HOME": "%WB_ROOT%models\\hf",
        "PYTHONIOENCODING": "utf-8",
    }
    for name, value in want.items():
        assert_(sets.get(name) == value, f"{name}={sets.get(name)!r}，期望 {value!r}")


check("各变量都指向包内（WB_ROOT / PY / WB_* / HF_HOME / 编码）", _setenv_values)


# ── (b) WB_HOME 落在包内 ────────────────────────────────────────────
def _wb_home_in_pkg():
    sets, _, _ = parse_sets(read_text(SETENV))
    value = sets.get("WB_HOME")
    assert_(value == "%WB_ROOT%workbuddy",
            f"WB_HOME={value!r}，必须由 %WB_ROOT% 推出（PRD v3 M1-(b)）")


check("(b) WB_HOME = %WB_ROOT%workbuddy", _wb_home_in_pkg)


# ── (c) 无条件覆盖 ──────────────────────────────────────────────────
def _unconditional():
    text = read_text(SETENV)
    assert_("if not defined" not in text.lower(), "出现 if not defined（无条件覆盖被破坏）")
    _, _, bad = parse_sets(text)
    assert_(not bad, f"有不是裸赋值的 set 行：{bad!r}")


check("(c) 无 if not defined，每个 set 都是裸赋值", _unconditional)


# ── (d) PATH 顺序 ───────────────────────────────────────────────────
def _path_order():
    sets, _, _ = parse_sets(read_text(SETENV))
    parts = sets.get("PATH", "").split(";")
    assert_(parts == ["%WB_ROOT%python", "%WB_ROOT%ffmpeg\\bin", "%PATH%"],
            f"PATH 应为 python;ffmpeg\\bin;%PATH%，实得 {parts!r}")


check("(d) PATH = python;ffmpeg\\bin;%PATH%（顺序不可换）", _path_order)


# ── (e) 入口 call _setenv.bat（T-18 两个存在时也查） ────────────────
def _entries_call_setenv():
    checked = []
    for path in (LAUNCH, START):
        text = read_text(path)
        assert_('call "%~dp0_setenv.bat"' in text, f"{path.name} 没有 call _setenv.bat")
        checked.append(path.name)
    for name in ("重启工作台.bat", "检查环境.bat"):
        path = TPL / name
        if path.is_file():                     # T-18 的文件，存在才查
            text = read_text(path)
            assert_('call "%~dp0_setenv.bat"' in text, f"{name} 没有 call _setenv.bat")
            checked.append(name)
    assert_(len(checked) >= 2, f"检查到的入口不足：{checked!r}")


check("(e) 入口 .bat 都 call \"%~dp0_setenv.bat\"", _entries_call_setenv)


# ── (f) 纯 ASCII（逐字节） ─────────────────────────────────────────
def _ascii_bytes():
    for path in (SETENV, LAUNCH, START):
        raw = path.read_bytes()
        bad = [(i, b) for i, b in enumerate(raw) if b >= 0x80]
        assert_(not bad, f"{path.name} 有非 ASCII 字节：{bad[:5]!r}")


check("(f) _setenv.bat 与三个入口 .bat 每字节 < 0x80", _ascii_bytes)


# ── (g) 不含作者机/构建机绝对路径 ──────────────────────────────────
def _no_abs_paths():
    text = read_text(SETENV)
    assert_(not ABS_PATH_RE.search(text), f"出现盘符绝对路径：{ABS_PATH_RE.findall(text)!r}")
    assert_("C:\\Users" not in text, "出现 C:\\Users")
    assert_("/home/" not in text, "出现 /home/")


check("(g) _setenv.bat 不含盘符绝对路径 / C:\\Users / /home/", _no_abs_paths)


# ── (h) 结尾的本地覆盖 ──────────────────────────────────────────────
def _local_override_tail():
    lines = [ln.strip() for ln in read_text(SETENV).splitlines() if ln.strip()]
    assert_(lines, "文件为空")
    want = 'if exist "%~dp0_setenv.local.bat" call "%~dp0_setenv.local.bat"'
    assert_(lines[-1] == want, f"最后一行={lines[-1]!r}，期望 {want!r}")


check("(h) 结尾 call _setenv.local.bat（存在才 call）", _local_override_tail)


# ── 入口内容：初始化 + 启动 + 不要管理员 ───────────────────────────
def _entry_commands():
    text = read_text(LAUNCH)
    assert_("first_run.py" in text, "入口没有 first_run.py")
    assert_("--portable" in text and "--init-only" in text, "入口没有 first_run --portable --init-only")
    assert_("webapp\\_launch.py" in text, "入口没有 webapp\\_launch.py")
    assert_(read_text(START) == text, "start.bat 必须与 启动工作台.bat 同内容")


check("入口先 first_run --portable --init-only 再 webapp\\_launch.py；start.bat 同内容", _entry_commands)


def _no_admin():
    forbidden = ("runas", "net session", "start-process -verb")
    for path in (LAUNCH, START):
        text = read_text(path).lower()
        for token in forbidden:
            assert_(token not in text, f"{path.name} 含 {token!r}（PRD 需求 1：不要管理员权限）")


check("入口不含 runas / net session / Start-Process -Verb", _no_admin)


# ── DoD 3：cmd /c 反例（用户机残留必须被覆盖） ─────────────────────
print("[反例] cmd 里先塞用户机残留，再 call 模板，三个变量必须被覆盖")


def _counter_example():
    # 临时目录故意放在 D:\ 根下：DoD 3 要求"输出里没有 C:\Users"，若用 %TEMP%
    # （C:\Users\<user>\AppData\...）会让这条断言因路径本身而失败，失去判别力。
    base = "D:\\" if os.path.isdir("D:\\") else tempfile.gettempdir()
    tmp = Path(tempfile.mkdtemp(prefix="t14_setenv_", dir=base))
    try:
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
        proc = subprocess.run(["cmd", "/c", str(wrapper)], cwd=str(tmp),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        out = proc.stdout.replace("\r", "")
        assert_(proc.returncode == 0, f"cmd 退出码 {proc.returncode}；stdout={out!r} stderr={proc.stderr!r}")
        lines = [ln.strip() for ln in out.splitlines()]
        expected = {
            f"[HOME]={tmp}\\workbuddy": "[HOME]",
            f"[LEDGER]={tmp}\\ledger": "[LEDGER]",
            f"[REVERSE]={tmp}\\reverse": "[REVERSE]",
            "[PYTHONPATH]=": "[PYTHONPATH] 清空",
            "[HF_HUB_CACHE]=": "[HF_HUB_CACHE] 清空",
            "[XDG_CACHE_HOME]=": "[XDG_CACHE_HOME] 清空",
            f"[ROOT]={tmp}\\": "[ROOT]",
        }
        for want, label in expected.items():
            assert_(want in lines, f"{label} 不是 {want!r}；实际行：{lines!r}")
        assert_("C:\\Users" not in out, f"输出里残留 C:\\Users：{out!r}")
        assert_("D:\\old" not in out, f"输出里残留 D:\\old：{out!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("(DoD 3) 用户机残留的 WB_HOME / WB_LEDGER_DIR / WB_REVERSE_DIR 被包内值覆盖", _counter_example)


print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
