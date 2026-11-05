# -*- coding: utf-8 -*-
"""T-18：两个入口 `.bat`（`重启工作台.bat` / `检查环境.bat`）—— 静态契约 + 包上实跑。

DoD 1/2（静态，任何机器可跑）：
  - 两个文件存在，且每个字节 < 0x80（ADR-0006：cmd 解析到的一律 ASCII）；
  - 都 `call "%~dp0_setenv.bat"`；
  - `重启工作台.bat` 含 `webapp\\_launch.py` 与 `--restart`，并把端口参数 `%~1` 传给 launcher；
  - `检查环境.bat` 含 `first_run.py` / `--portable` / `--no-network` / `pause` / `exit /b`；
  - 都不含 `[A-Za-z]:\\` / `C:\\Users` / `/home/`，也不含 runas 之类要管理员的写法；
  - 「杀进程」不在入口里自己做（交给 `_launch.py` 的 restart_plan，只杀一个 pid）；
  - 引用的 `webapp\\_launch.py`、`first_run.py` 真实存在。

DoD 3/4（包上实跑，需要 build\\portable\\t18）：
  - 在包上起一个 8792 实例 → `cmd /c 重启工作台.bat 8792` → 5 秒内 health 200、端口不变、pid 变了；
  - `cmd /c 检查环境.bat`（喂一个回车过掉 pause）→ 退出码 0、解释器/ffmpeg/模型三类各一行、无 `[FAIL]`。

包不存在时退出码 2 并打印怎么构建（不假装通过）。

跑法（仓库根）：
  webapp\\.venv\\Scripts\\python.exe packaging\\tests\\launchers.test.py
前置：
  webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py --flavor slim --out build\\portable\\t18 \
      --ffmpeg-dir "<本机 ffmpeg 9.0.2 目录>"
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent            # packaging/tests
REPO = HERE.parent.parent                          # repo root
TPL = REPO / "packaging" / "templates"
RESTART = TPL / "重启工作台.bat"
CHECK_ENV = TPL / "检查环境.bat"
PKG = REPO / "build" / "portable" / "t18" / "slim"   # --out 的下一级才是包根（<out>\<flavor>）

LIVE_PORT = 8792
PEER_PORTS = (8789, 8791, 8793)                    # 用来判"这次实跑有没有动别人家的端口"
PY = sys.executable

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
    except Exception as exc:                      # noqa: BLE001  失败要一句话说清
        _fail += 1
        print(f"  失败 {name} -> {exc}")
        return
    _ok += 1
    print(f"  ok {name}")


def assert_(cond, message=""):
    if not cond:
        raise AssertionError(message or "断言失败")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ═══════════════════════ 1. 静态契约（DoD 1 / DoD 2） ═══════════════════════
def _both_exist():
    for path in (RESTART, CHECK_ENV):
        assert_(path.is_file(), f"{path.name} 不存在：{path}")


def _ascii_only():
    for path in (RESTART, CHECK_ENV):
        raw = path.read_bytes()
        bad = [(i, b) for i, b in enumerate(raw) if b >= 0x80]
        assert_(not bad, f"{path.name} 有非 ASCII 字节：{bad[:5]!r}")


def _calls_setenv():
    for path in (RESTART, CHECK_ENV):
        assert_('call "%~dp0_setenv.bat"' in read_text(path),
                f"{path.name} 没有 call \"%~dp0_setenv.bat\"")


def _no_abs_paths():
    for path in (RESTART, CHECK_ENV):
        text = read_text(path)
        assert_("C:\\Users" not in text, f"{path.name} 出现 C:\\Users")
        assert_("/home/" not in text, f"{path.name} 出现 /home/")
        for i, ch in enumerate(text):
            if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" and text[i:i + 3][1:] == ":\\":
                raise AssertionError(f"{path.name} 出现盘符绝对路径：{text[i:i + 12]!r}")


def _no_admin():
    for path in (RESTART, CHECK_ENV):
        low = read_text(path).lower()
        for token in ("runas", "net session", "start-process -verb"):
            assert_(token not in low, f"{path.name} 含 {token!r}（PRD 需求 1：不要管理员权限）")


def _restart_tokens():
    text = read_text(RESTART)
    assert_("webapp\\_launch.py" in text, "重启工作台.bat 没有 webapp\\_launch.py")
    assert_("--restart" in text, "重启工作台.bat 没有 --restart")
    assert_("%~1" in text, "重启工作台.bat 没有接收端口参数 %~1")
    launch = [ln for ln in text.splitlines() if "_launch.py" in ln]
    assert_(len(launch) == 1, f"重启工作台.bat 里 _launch.py 出现 {len(launch)} 次（应 1 次）")
    assert_("--restart" in launch[0], f"启动行没有 --restart：{launch[0]!r}")
    assert_("%~1" in launch[0], f"启动行没有把端口 %~1 传给 launcher：{launch[0]!r}")


def _restart_delegates_kill():
    text = read_text(RESTART)
    assert_("taskkill" not in text.lower(),
            "重启工作台.bat 不该自己 taskkill —— 杀进程由 _launch.py 的 restart_plan 负责（只杀一个 pid）")


def _checkenv_tokens():
    text = read_text(CHECK_ENV)
    for token in ("first_run.py", "--portable", "--no-network", "pause", "exit /b"):
        assert_(token in text, f"检查环境.bat 缺 {token!r}")


def _checkenv_exit_code():
    lines = [ln.strip() for ln in read_text(CHECK_ENV).splitlines() if ln.strip()]
    idx = next((i for i, ln in enumerate(lines)
                if "first_run.py" in ln and not ln.lower().startswith("rem")), None)
    assert_(idx is not None, "检查环境.bat 里没有真正执行 first_run.py 的那一行")
    assert_(idx + 1 < len(lines), "first_run 后面没有内容")
    assert_(lines[idx + 1].lower() == 'set "rc=%errorlevel%"',
            f"first_run 之后没有立刻记 RC：{lines[idx + 1]!r}")
    assert_(lines[-1].lower() == "exit /b %rc%", f"最后一行应为 exit /b %RC%，实得 {lines[-1]!r}")
    assert_(lines[-2].lower() == "pause",
            f"倒数第二行必须是（无条件）pause，实得 {lines[-2]!r}")


def _referenced_scripts_exist():
    assert_((REPO / "webapp" / "_launch.py").is_file(), "仓库里没有 webapp\\_launch.py")
    assert_((REPO / "first_run.py").is_file(), "仓库里没有 first_run.py")


print("[1] 静态契约（DoD 1 / DoD 2）")
check("两个入口 .bat 都存在", _both_exist)
check("两个入口每字节 < 0x80（纯 ASCII）", _ascii_only)
check('两个入口都 call "%~dp0_setenv.bat"', _calls_setenv)
check("四个文件都不含盘符绝对路径 / C:\\Users / /home/", _no_abs_paths)
check("两个入口都不含 runas / net session / Start-Process -Verb", _no_admin)
check("重启工作台.bat 含 webapp\\_launch.py + --restart，并把 %~1 传给 launcher", _restart_tokens)
check("重启工作台.bat 不自己 taskkill（杀进程交给 _launch.py）", _restart_delegates_kill)
check("检查环境.bat 含 first_run.py / --portable / --no-network / pause / exit /b", _checkenv_tokens)
check("检查环境.bat：first_run → 记 RC → 无条件 pause → exit /b %RC%", _checkenv_exit_code)
check("webapp\\_launch.py 与 first_run.py 真实存在", _referenced_scripts_exist)


# ═══════════════════════ 2. 包上实跑（DoD 3 / DoD 4） ═══════════════════════
def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _wait_health(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with _OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:                          # noqa: BLE001  还没起来
            pass
        time.sleep(0.2)
    return False


def _listener_pid(port: int):
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                         text=True, encoding="mbcs", errors="replace").stdout or ""
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(":" + str(port))
                and parts[3].upper() == "LISTENING" and parts[4].isdigit()):
            return int(parts[4])
    return None


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True, encoding="mbcs", errors="replace").stdout or ""
    return str(pid) in out


def _kill_tree(pid) -> None:
    if not pid:
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)


def _package_ready() -> bool:
    return ((PKG / "python" / "python.exe").is_file()
            and (PKG / "webapp" / "_launch.py").is_file()
            and (PKG / "first_run.py").is_file()
            and (PKG / "_setenv.bat").is_file())


def _package_env() -> dict:
    """用包内 `_setenv.bat` 现算环境（与入口同一个来源，不把变量表抄一份在用例里）。

    注意两点（实测踩过）：
    - 必须用**字符串**形式交给 cmd；列表形式会把内层引号转义成 `\\"`，cmd 认不出路径。
    - `cmd /c <bat>` 依赖「当前目录可执行」；本机设了 `NoDefaultCurrentDirectoryInExePath=1`，
      所以下面调用包内入口时一律用**绝对路径**（在普通用户机上等价于在该目录里双击）。
    """
    proc = subprocess.run('cmd /c call "{}" && set'.format(PKG / "_setenv.bat"),
                          cwd=str(PKG), capture_output=True)
    env = {}
    for line in (proc.stdout or b"").decode("mbcs", "replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


def _start_instance(env: dict):
    return subprocess.Popen(
        [str(PKG / "python" / "python.exe"), str(PKG / "webapp" / "_boot.py"),
         "--port", str(LIVE_PORT)],
        cwd=str(PKG / "webapp"), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x00000200,        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    )


def _decode(raw: bytes) -> str:
    """入口的 echo 是控制台代码页，first_run/launcher 的 print 是 UTF-8（PYTHONIOENCODING）。
    以 UTF-8 解，坏字节替换掉即可 —— 断言要用到的 token 全是 ASCII 或 UTF-8 中文。"""
    return (raw or b"").decode("utf-8", "replace")


print("[2] 包上实跑（DoD 3 / DoD 4）")

if not _package_ready():
    print(f"[FAIL] 缺少 {PKG.relative_to(REPO)}，先跑 packaging\\build_portable.py "
          f"（--flavor slim --out {PKG.relative_to(REPO)}）")
    print(f"\n通过 {_ok} / {_ok + _fail}")
    sys.exit(2)

assert_(_port_free(LIVE_PORT), f"端口 {LIVE_PORT} 已被占用，无法实跑（换时间重跑）")
for peer in PEER_PORTS:
    if not _port_free(peer):
        print(f"  注意：{peer} 上有服务（不是本用例起的），本用例只碰 {LIVE_PORT}")


def _restart_on_package():
    """/T-18 DoD 3/：起 8792 → 重启工作台.bat 8792 → 5 秒内 health 200、端口不变、pid 变了。

    "5 秒" 量的是**从发出重启命令到新实例 health 200**（不是 cmd 自己跑了多久），
    并用 `pid != old_pid` 确认 200 是新进程给的（旧进程刚被杀，端口会短暂不通）。
    """
    env = _package_env()
    assert_(env.get("WB_ROOT"), "包内 _setenv.bat 没给出 WB_ROOT")
    proc = _start_instance(env)
    old_pid = new_pid = None
    try:
        assert_(_wait_health(LIVE_PORT, 15.0),
                f"15 秒内 http://127.0.0.1:{LIVE_PORT}/api/health 没返回 200")
        old_pid = _listener_pid(LIVE_PORT)
        assert_(old_pid, f"拿不到 {LIVE_PORT} 的监听 pid")

        started = time.time()
        run = subprocess.Popen('cmd /c "{}" {}'.format(PKG / "重启工作台.bat", LIVE_PORT),
                               cwd=str(PKG), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        health_at = None
        while time.time() - started < 20.0:
            pid = _listener_pid(LIVE_PORT)
            if pid and pid != old_pid and _wait_health(LIVE_PORT, 0.5):
                new_pid = pid
                health_at = time.time() - started
                break
            time.sleep(0.2)
        out = _decode(run.stdout.read()) if run.stdout else ""
        run.wait(timeout=120)
        assert_(health_at is not None,
                f"20 秒内 {LIVE_PORT} 没回到「health 200 且 pid 变了」"
                f"（old_pid={old_pid}）；launcher 输出末尾：{out[-900:]!r}")
        assert_(run.returncode == 0,
                f"重启退出码 {run.returncode}（期望 0）；launcher 输出末尾：{out[-900:]!r}")
        assert_(new_pid != old_pid, f"pid 没变（old={old_pid} new={new_pid}）")
        assert_(not _pid_alive(old_pid), f"旧 pid {old_pid} 还活着")
        print(f"    [DoD 3] port={LIVE_PORT} old_pid={old_pid} -> new_pid={new_pid} "
              f"health_back_after={health_at:.2f}s  cmd_rc={run.returncode}")
        assert_(health_at <= 5.0, f"重启后 health 200 用了 {health_at:.2f}s，超过 DoD 的 5s")
    finally:
        _kill_tree(_listener_pid(LIVE_PORT))
        if proc.poll() is None:
            _kill_tree(proc.pid)
        deadline = time.time() + 10
        while time.time() < deadline and not _port_free(LIVE_PORT):
            time.sleep(0.2)


check("包上重启：端口不变、pid 变了、5 秒内 health 200", _restart_on_package)


def _checkenv_on_package():
    """T-18 DoD 4：cmd /c 检查环境.bat → 退出码 0、三类各一行、无 [FAIL]。

    用绝对路径调用（理由见 `_package_env` 的 `NoDefaultCurrentDirectoryInExePath` 说明），
    `input=b"\\n"` 等价于 DoD 里的 `echo.` 过掉无条件的 pause。
    """
    run = subprocess.run('cmd /c "{}"'.format(PKG / "检查环境.bat"), cwd=str(PKG), input=b"\n",
                         capture_output=True, timeout=180)
    out = _decode(run.stdout) + _decode(run.stderr)
    assert_(run.returncode == 0,
            f"检查环境 退出码 {run.returncode}（期望 0）；输出末尾：{out[-1200:]!r}")
    for marker in ("包内解释器", "ffmpeg", "whisper 模型"):
        line = next((ln for ln in out.splitlines() if marker in ln), None)
        assert_(line is not None, f"输出里没有 {marker!r} 那一行；输出末尾：{out[-1200:]!r}")
    assert_("[FAIL]" not in out, f"输出里有 [FAIL]：{out[-1200:]!r}")
    model_line = next((ln for ln in out.splitlines() if "whisper 模型" in ln), "")
    print(f"    [DoD 4] rc={run.returncode} model_line={model_line.strip()!r}")


check("包上检查环境：退出码 0、三类各一行、无 [FAIL]", _checkenv_on_package)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
