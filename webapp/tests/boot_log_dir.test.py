# -*- coding: utf-8 -*-
"""T-16：`_boot.py` / `_boot2.py` 的日志目录读 `WB_LOG_DIR`（未设仍是 `webapp\\`）。

为什么要有这条用例（2026-11-05）：便携包里日志应落在包根 `logs\\`，但两个 boot wrapper 把
路径写死成 `webapp\\_server_run.log`，而 `webapp/_launch.py` 已经按 `WB_LOG_DIR` 算 LOG。
两边不是同一个文件时，启动失败会"看不到日志"（tail 读空文件）。本用例钉住两边一致。

覆盖 T-16 DoD 1~4：
  1. import `_boot` / `_boot2` **无副作用**（不建日志文件、不起服务、不改 cwd）；
     `WB_LOG_DIR=<临时>` → 两者日志路径都是 `<临时>\\_server_run.log`；
     未设 → 两者都是 `webapp\\_server_run.log`。
  2. 顶层只剩常量与函数定义，执行逻辑在 `if __name__ == "__main__": main()` 下；
     `_boot.py` 接 `--port`；`_boot2.py` 不接参数（只认 `WB_PORT`）。
  3. 实跑 `_boot.py --port 8792`（`WB_LOG_DIR` 含空格与中文）→ 日志落在该目录、health 200、
     且本次运行没有再往 `webapp\\_server_run.log` 写。
  4. 不设 `WB_LOG_DIR` 实跑 → 日志继续追加到 `webapp\\_server_run.log`（今天的行为不变）。

跑法：webapp/.venv/Scripts/python.exe webapp/tests/boot_log_dir.test.py
"""
from __future__ import annotations

import ast
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1]
BOOT = WEBAPP / "_boot.py"
BOOT2 = WEBAPP / "_boot2.py"
LOG_NAME = "_server_run.log"
DEFAULT_LOG = WEBAPP / LOG_NAME
LIVE_PORT = 8792                      # T-16 DoD 3 指定的实跑端口
PY = sys.executable

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
    except Exception as exc:                  # noqa: BLE001  用例失败要一句话说清
        _fail += 1
        print(f"  失败 {name} -> {exc}")
        return
    _ok += 1
    print(f"  ok {name}")


def assert_(cond, message=""):
    if not cond:
        raise AssertionError(message or "断言失败")


# ────────────────────────── 公共夹具 ──────────────────────────
def clean_env():
    """剥掉 WB_* / PYTHON* / HF_*：本用例只关心自己显式给的那几个变量。"""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("WB_", "PYTHON", "HF_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"      # 别用 __pycache__ 干扰"import 不建文件"
    return env


def busy_port():
    """本进程占住一个端口：万一被 import 的模块真去起服务，app.py 会因端口被占立刻退出，
    不会真的把服务跑起来（红的时候也快、也不会污染 8789 / 8792）。"""
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    guard.bind(("127.0.0.1", 0))
    guard.listen(1)
    return guard, guard.getsockname()[1]


def port_is_free(port=LIVE_PORT):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def wait_health(port=LIVE_PORT, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with _OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:                     # noqa: BLE001  还没起来
            pass
        time.sleep(0.2)
    return False


def wait_released(port=LIVE_PORT, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_free(port):
            return True
        time.sleep(0.2)
    return port_is_free(port)


def probe_import(module, log_dir):
    """在子进程里 import 模块，打印 log_path() / 模块级 LOG / cwd；返回 (proc, cwd, 秒数)。"""
    guard, port = busy_port()
    cwd = Path(tempfile.mkdtemp(prefix="wb_probe_cwd_"))
    try:
        env = clean_env()
        env["WB_PORT"] = str(port)
        if log_dir is not None:
            env["WB_LOG_DIR"] = str(log_dir)
        code = (
            "import os, sys\n"
            f"sys.path.insert(0, r'{WEBAPP}')\n"
            f"import {module} as m\n"
            "print('LOG=' + m.log_path())\n"
            "print('CONST=' + str(getattr(m, 'LOG', '')))\n"
            "print('CWD=' + os.getcwd())\n"
        )
        started = time.time()
        proc = subprocess.run([PY, "-c", code], cwd=str(cwd), env=env,
                              capture_output=True, text=True, timeout=45)
        return proc, cwd, time.time() - started
    finally:
        guard.close()


def parse_probe(proc):
    values = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def expect_import(module, log_dir, expected):
    root = Path(tempfile.mkdtemp(prefix="wb_blog_"))
    cwd = None
    try:
        target = root / "测试 目录" / "logs" if log_dir else None
        want = expected(target)
        proc, cwd, elapsed = probe_import(module, target)
        assert_(proc.returncode == 0,
                f"import {module} 退出码 {proc.returncode}；stderr 末尾={proc.stderr[-700:]!r}")
        got = parse_probe(proc)
        assert_(got.get("LOG") == want, f"log_path()={got.get('LOG')!r}，期望 {want!r}")
        assert_(got.get("CONST") in ("", want), f"模块级 LOG={got.get('CONST')!r}，期望 {want!r}")
        assert_(got.get("CWD") == str(cwd), f"import 改了 cwd：{got.get('CWD')!r} != {cwd!r}")
        assert_(elapsed < 20, f"import 花了 {elapsed:.1f}s，看着像把服务也起起来了")
        if target is not None:
            assert_(not (target / LOG_NAME).exists(), f"import 就写了 {target / LOG_NAME}")
        assert_(not list(cwd.iterdir()), f"import 在 cwd 建了文件：{list(cwd.iterdir())}")
    finally:
        if cwd is not None:
            shutil.rmtree(cwd, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def style_expected(kind):
    if kind == "wb_log_dir":
        return lambda root: str(root / LOG_NAME)
    return lambda _root: str(DEFAULT_LOG)


# ─────────────────── 1. import 无副作用 + 路径规则（DoD 1） ───────────────────
print("[1] import 无副作用；WB_LOG_DIR 决定日志路径")


def _boot_uses_wb_log_dir():
    expect_import("_boot", True, style_expected("wb_log_dir"))


def _boot2_uses_wb_log_dir():
    expect_import("_boot2", True, style_expected("wb_log_dir"))


def _boot_default_dir():
    expect_import("_boot", False, style_expected("default"))


def _boot2_default_dir():
    expect_import("_boot2", False, style_expected("default"))


check("import _boot 无副作用，日志路径 = <WB_LOG_DIR>\\_server_run.log", _boot_uses_wb_log_dir)
check("import _boot2 无副作用，日志路径 = <WB_LOG_DIR>\\_server_run.log", _boot2_uses_wb_log_dir)
check("未设 WB_LOG_DIR：_boot 回落到 webapp\\_server_run.log", _boot_default_dir)
check("未设 WB_LOG_DIR：_boot2 回落到 webapp\\_server_run.log", _boot2_default_dir)


# ─────────────── 2. 顶层形状 / --port 归属（DoD 2） ───────────────
print("[2] 顶层只有常量与函数；执行逻辑在 __main__ 下")


def is_main_guard(node):
    test = getattr(node, "test", None)
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name) and test.left.id == "__name__"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def assert_top_level_shape(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
               ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    bad, guards = [], []
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue                          # 模块 docstring
        if isinstance(node, allowed):
            continue
        if isinstance(node, ast.If) and is_main_guard(node):
            guards.append(node)
            continue
        bad.append(f"{type(node).__name__}@L{getattr(node, 'lineno', '?')}")
    assert_(not bad, f"{path.name} 顶层有 import 就会被执行的东西：{', '.join(bad)}")
    assert_(len(guards) == 1,
            f"{path.name} 要求有且只有一个 if __name__ == \"__main__\" 块，实得 {len(guards)}")
    calls_main = any(
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name) and n.value.func.id == "main"
        for n in guards[0].body
    )
    assert_(calls_main, f"{path.name} 的 __main__ 块里没有调用 main()")


def _boot_shape():
    assert_top_level_shape(BOOT)
    assert_("--port" in BOOT.read_text(encoding="utf-8"), "_boot.py 应支持 --port")


def _boot2_shape():
    assert_top_level_shape(BOOT2)
    text = BOOT2.read_text(encoding="utf-8")
    tree = ast.parse(text)
    main_fn = next((n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert_(main_fn is not None, "_boot2.py 应有 main()")
    assert_(not main_fn.args.args,
            f"_boot2.py 的 main() 不该接参数：{[a.arg for a in main_fn.args.args]}")
    assert_("_port_arg" not in text, "_boot2.py 不该有端口参数解析器（--port 只属于 _boot.py）")
    reads_argv = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
        and isinstance(n.value.value, ast.Name) and n.value.value.id == "sys"
        and n.value.attr == "argv"
    ]
    assert_(not reads_argv, "_boot2.py 不该读 sys.argv（它不接参数，端口只认 WB_PORT）")


check("_boot.py 顶层只有常量/函数，执行在 __main__ 下，且支持 --port", _boot_shape)
check("_boot2.py 顶层只有常量/函数，执行在 __main__ 下，且不接 --port", _boot2_shape)


# ───────────────── 3./4. 实跑（DoD 3 / DoD 4） ─────────────────
def start_boot(log_dir, port=LIVE_PORT):
    env = clean_env()
    if log_dir is not None:
        env["WB_LOG_DIR"] = str(log_dir)
    return subprocess.Popen(
        [PY, str(BOOT), "--port", str(port)],
        cwd=str(WEBAPP), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_boot(proc):
    """只杀自己起的那个 pid（连同子进程），绝不按名字批量杀。"""
    try:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, text=True)
        proc.wait(timeout=15)
    except Exception:                         # noqa: BLE001  清理失败也别把用例挂在半空
        if proc.poll() is None:
            proc.kill()


def read_appended(path, offset):
    if not path.exists() or path.stat().st_size <= offset:
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        return fh.read()


print("[3] 实跑 _boot.py --port 8792（WB_LOG_DIR 含空格与中文）")


def _live_with_wb_log_dir():
    assert_(port_is_free(LIVE_PORT), f"端口 {LIVE_PORT} 已被占用，无法实跑（换时间重跑）")
    root = Path(tempfile.mkdtemp(prefix="wb_live_"))
    log_dir = root / "测试 目录" / "logs"
    before = DEFAULT_LOG.stat().st_size if DEFAULT_LOG.exists() else 0
    proc = start_boot(log_dir)
    try:
        assert_(wait_health(LIVE_PORT, 10.0),
                f"10 秒内 http://127.0.0.1:{LIVE_PORT}/api/health 没有返回 200")
        log_file = log_dir / LOG_NAME
        assert_(log_file.exists(), f"日志没有出现在 WB_LOG_DIR：{log_file}")
        text = log_file.read_text(encoding="utf-8", errors="replace")
        assert_(text.strip(), f"日志是空的：{log_file}")
        assert_("端口 8792" in text, f"日志里没有本进程的启动行：{text[-400:]!r}")
        appended = read_appended(DEFAULT_LOG, before)
        assert_("端口 8792" not in appended,
                "设了 WB_LOG_DIR，日志却仍写到 webapp\\_server_run.log")
    finally:
        stop_boot(proc)
        shutil.rmtree(root, ignore_errors=True)
    assert_(wait_released(LIVE_PORT), f"清理后 {LIVE_PORT} 仍被占用")


check("实跑：日志写进 WB_LOG_DIR、health 200、没再写 webapp\\_server_run.log", _live_with_wb_log_dir)

print("[4] 不设 WB_LOG_DIR 实跑：日志继续追加到 webapp\\_server_run.log")


def _live_default_dir():
    assert_(port_is_free(LIVE_PORT), f"端口 {LIVE_PORT} 已被占用，无法实跑（换时间重跑）")
    before = DEFAULT_LOG.stat().st_size if DEFAULT_LOG.exists() else 0
    proc = start_boot(None)
    try:
        assert_(wait_health(LIVE_PORT, 10.0),
                f"10 秒内 http://127.0.0.1:{LIVE_PORT}/api/health 没有返回 200")
        appended = read_appended(DEFAULT_LOG, before)
        assert_(appended, "webapp\\_server_run.log 没有被追加")
        assert_("端口 8792" in appended, f"追加内容里没有本进程的启动行：{appended[-400:]!r}")
    finally:
        stop_boot(proc)
    assert_(wait_released(LIVE_PORT), f"清理后 {LIVE_PORT} 仍被占用")


check("不设 WB_LOG_DIR：日志仍追加到 webapp\\_server_run.log", _live_default_dir)


print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
