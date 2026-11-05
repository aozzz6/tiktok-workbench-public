# -*- coding: utf-8 -*-
"""qa/T-18/run.py —— T-18「入口补齐：重启工作台.bat 与 检查环境.bat」的独立验收用例。

跑法（仓库根）：
    pipeline\\.venv\\Scripts\\python.exe qa\\T-18\\run.py

退出码：0 = 全部通过；1 = 有用例不通过；2 = 验收包不存在（不算通过，也不算失败，禁止 SKIP 成 0）。

被测对象：`build\\portable\\qa18\\slim`（用当前代码新构建的包；PM 指定单独的 `--out`，
避免拿 T-15 的旧包验新代码）。包缺失时的构建命令见 BUILD_CMD。

七条用例对应 M1 QA 清单 T-18 的 1–7 条：
    C-01 packaging\\tests\\launchers.test.py 退出 0（复用单测，只作弱证据）
    C-02 两个 .bat 的静态契约（独立核对：字节、call、关键字、无绝对路径）
    C-03 引用可解析（webapp\\_launch.py / first_run.py 仓库与包内都存在）
    C-04 包上实跑 重启工作台.bat：<5 s、端口不变、pid 变了（真重启）
    C-05 包上实跑 检查环境.bat：退出 0、三类各一行、无 [FAIL]（slim 无模型应为 [WARN]）
    C-06 包不存在时退出码 2 并打印构建命令（临时改名 → 子进程 → finally 恢复并校验）
    C-07 入口可被 run-all 发现、且能在仓库根用文档指定的解释器跑起来

纪律（见 M1 QA 清单「全局约定」）：
  * 剥环境：PATH 只留 system32，独立空 TEMP/TMP，清空 WB_*/HF_*/PYTHON*，
    HTTP(S)_PROXY 指向黑洞，包复制到「中文 + 空格」路径下再跑。
  * 起停：只碰自己选的 879x 端口；只杀自己能证明归属的 pid；**绝不动 8789 上
    用户可能在跑的工作台**（用例里还断言 8789 的监听 pid 前后不变）。
  * `NoDefaultCurrentDirectoryInExePath=1`：所有 .bat 都用绝对路径 + `cmd /c call "..."`。
  * 每条用例都真的能失败：断言的是包内真实产物与真实进程/端口/退出码，不是文本存在性。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

# 控制台可能是 GBK：中文输出统一走 UTF-8，避免 UnicodeEncodeError 把用例打成假红。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[2]
PKG_ROOT = Path(os.environ.get("QA18_PKG_ROOT") or (REPO / "build" / "portable" / "qa18"))
PKG = Path(os.environ.get("QA18_PKG") or (PKG_ROOT / "slim"))

TEMPLATES = REPO / "packaging" / "templates"
RESTART_BAT = "重启工作台.bat"
CHECK_BAT = "检查环境.bat"
FIXED_8789 = 8789
PORT_CANDIDATES = (8795, 8796, 8797, 8798, 8799, 8794)  # 只用 879x；8789 永不作为本用例的端口

BUILD_CMD = (
    "webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py "
    "--flavor slim --out build\\portable\\qa18 "
    '--ffmpeg-dir "D:\\ffmpeg\\build\\ffmpeg-9.0.2-essentials_build\\bin"'
)

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CASES = []


def case(cid, title):
    def deco(fn):
        CASES.append((cid, title, fn))
        return fn

    return deco


# ────────────────────────────── 小工具 ──────────────────────────────
def rel(path) -> str:
    try:
        return str(Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd, cwd=None, env=None, timeout=180, stdin_data=None):
    """跑子进程，返回 (退出码, 合并输出文本, 秒)。超时算 124。"""
    kwargs = dict(
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=timeout,
    )
    if stdin_data is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin_data
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, **kwargs)
        code, raw = proc.returncode, (proc.stdout or b"")
    except subprocess.TimeoutExpired as exc:
        code, raw = 124, (exc.stdout or b"")
    except OSError as exc:
        return 127, "无法执行 %r：%s" % (cmd, exc), time.perf_counter() - t0
    return code, raw.decode("utf-8", "replace"), time.perf_counter() - t0


def _run_bat(bat: Path, args=(), env=None, timeout=180, stdin_data=None):
    """绝对路径调 .bat（本机 NoDefaultCurrentDirectoryInExePath=1，不能靠 cwd 找）。

    拼参数时不要自己加引号：Windows 的 list2cmdline 会给含空格的路径补引号，
    再手写一层引号会让 cmd 把整条命令当成一个文件名（实测报 "…bat" is not recognized）。
    """
    cmd = ["cmd", "/d", "/c", "call", str(bat)] + [str(a) for a in args]
    return _run(cmd, env=env, timeout=timeout, stdin_data=stdin_data)


def stripped_env(tmp_base: Path) -> dict:
    """M1 QA 清单的剥环境口径：不留 Python/代理/WB_*/HF_* 的宿主痕迹。"""
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    child_temp = tmp_base / "child-temp"
    child_tmp = tmp_base / "child-tmp"
    child_temp.mkdir(parents=True, exist_ok=True)
    child_tmp.mkdir(parents=True, exist_ok=True)
    keep = (
        "SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
        "USERNAME", "USERDOMAIN", "ProgramData", "ProgramFiles",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    )
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = sysroot + r"\system32;" + sysroot
    env["TEMP"] = str(child_temp)
    env["TMP"] = str(child_tmp)
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    return env


def health(port: int, timeout: float = 2.5):
    """拿 /api/health；不是本工作台的 JSON 就返回 None。"""
    try:
        with _opener.open("http://127.0.0.1:%d/api/health" % port, timeout=timeout) as resp:
            raw = resp.read(65536)
    except Exception:  # noqa: BLE001
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("ok") is True else None


def wait_health(port: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = health(port)
        if data is not None:
            return data
        time.sleep(0.15)
    return None


def listener_pid(port: int):
    """监听该端口的 pid（与 _launch.listener_pid 同口径）。"""
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(":" + str(port)) and parts[-1].isdigit()):
            return int(parts[-1])
    return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port():
    for port in PORT_CANDIDATES:
        if port_free(port) and listener_pid(port) is None:
            return port
    return None


def pid_exe(pid: int) -> str:
    """pid 的可执行文件路径；读不到返回空串（不因此拒绝清理，见 cleanup）。"""
    ps = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').ExecutablePath" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cleanup(port: int, copy_root: Path, why: str) -> list:
    """只杀能证明属于自己的进程：端口是我选的、pid 是该端口现在的监听者、
    可执行文件（读得到时）在复制的包目录下。"""
    ev = []
    pid = listener_pid(port)
    if not pid:
        return ["%s：端口 %d 已无监听进程" % (why, port)]
    exe = pid_exe(pid)
    if exe and str(copy_root).lower() not in exe.lower():
        ev.append("%s：pid %d 的可执行文件是 %s，不在复制包里 → 不杀" % (why, pid, exe))
        return ev
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
    deadline = time.time() + 10
    while time.time() < deadline and listener_pid(port):
        time.sleep(0.2)
    ev.append("%s：已结束 pid %d（端口 %d 现在 %s）"
              % (why, pid, port, "已释放" if not listener_pid(port) else "仍有监听"))
    return ev


# ────────────────────────────── 包与复制 ──────────────────────────────
class PkgMissing(RuntimeError):
    pass


def require_package() -> None:
    if not PKG.is_dir():
        raise PkgMissing(str(PKG))


_COPY = {"path": None, "base": None}


def package_copy() -> Path:
    """把验收包复制到「中文 + 空格」路径下再跑；只复制一次。"""
    if _COPY["path"] is not None:
        return _COPY["path"]
    base = Path(tempfile.mkdtemp(prefix="qa18-", dir="D:\\")) if Path("D:\\").is_dir() \
        else Path(tempfile.mkdtemp(prefix="qa18-"))
    dest = base / "测试 目录 工作台" / "工作台"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PKG, dest)
    _COPY["base"] = base
    _COPY["path"] = dest
    return dest


def drop_copy() -> None:
    if _COPY["base"] is not None:
        shutil.rmtree(_COPY["base"], ignore_errors=True)


def assets_are_fresh(pkg: Path) -> list:
    """包必须是用当前代码/模板构建的：逐字节比对入口与两个 Python 入口。"""
    pairs = [
        (TEMPLATES / RESTART_BAT, pkg / RESTART_BAT),
        (TEMPLATES / CHECK_BAT, pkg / CHECK_BAT),
        (TEMPLATES / "_setenv.bat", pkg / "_setenv.bat"),
        (REPO / "webapp" / "_launch.py", pkg / "webapp" / "_launch.py"),
        (REPO / "first_run.py", pkg / "first_run.py"),
    ]
    stale = []
    for src, dst in pairs:
        if not dst.is_file():
            stale.append("%s：包里没有" % rel(dst))
        elif sha(src) != sha(dst):
            stale.append("%s：与 %s 不一致" % (rel(dst), rel(src)))
    return stale


# ────────────────────────────── 用例 ──────────────────────────────
@case("C-01", "入口单测 packaging\\tests\\launchers.test.py 退出码 0")
def c01():
    py = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
    test = REPO / "packaging" / "tests" / "launchers.test.py"
    assert py.is_file(), "缺解释器 %s" % rel(py)
    assert test.is_file(), "缺单测 %s" % rel(test)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # 单测打印 ✓/✗，GBK 控制台会崩成假红
    code, out, secs = _run([str(py), str(test)], cwd=REPO, env=env, timeout=300)
    tail = [ln.strip() for ln in out.splitlines() if ln.strip()][-1:] or ["(无输出)"]
    assert code == 0, "launchers.test.py 退出码 %d（%.1fs），末行：%s" % (code, secs, tail[0])
    assert "Traceback" not in out, "单测输出里有 Traceback"
    return ["退出码 0（%.1fs）" % secs, "末行：%s" % tail[0]]


@case("C-02", "两个 .bat 的静态契约（独立核对）")
def c02():
    ev = []
    for name in (RESTART_BAT, CHECK_BAT):
        path = TEMPLATES / name
        assert path.is_file(), "缺模板 %s" % rel(path)
        raw = path.read_bytes()
        high = [b for b in raw if b >= 0x80]
        assert not high, "%s 有 %d 个字节 >= 0x80" % (name, len(high))
        text = raw.decode("ascii")
        assert 'call "%~dp0_setenv.bat"' in text, "%s 没有 call \"%%~dp0_setenv.bat\"" % name
        drive = re.findall(r"[A-Za-z]:\\", text)
        assert not drive, "%s 里有绝对路径 %r" % (name, drive[:3])
        assert "C:\\Users" not in text, "%s 里出现 C:\\Users" % name
        ev.append("%s：ASCII ✓ 最大字节 0x%02X call ✓ 无盘符路径 ✓"
                  % (name, max(raw) if raw else 0))

    restart = (TEMPLATES / RESTART_BAT).read_text(encoding="ascii")
    assert "webapp\\_launch.py" in restart, "重启工作台.bat 没有引用 webapp\\_launch.py"
    assert "--restart" in restart, "重启工作台.bat 没有 --restart"
    check = (TEMPLATES / CHECK_BAT).read_text(encoding="ascii")
    for kw in ("first_run.py", "--portable", "--no-network", "pause", "exit /b"):
        assert kw in check, "检查环境.bat 缺关键字 %r" % kw
    ev.append("重启：webapp\\_launch.py + --restart ✓；检查：first_run.py/--portable/"
              "--no-network/pause/exit /b ✓")

    for name in (RESTART_BAT, CHECK_BAT):   # 包里的副本必须就是这份模板
        assert sha(PKG / name) == sha(TEMPLATES / name), \
            "包内 %s 与模板不一致（包是旧的，先重建）" % name
    ev.append("包内两份 .bat 与 packaging\\templates\\ 逐字节一致")
    return ev


@case("C-03", "引用可解析：webapp\\_launch.py 与 first_run.py")
def c03():
    ev = []
    for relpath in ("webapp/_launch.py", "first_run.py"):
        repo_path = REPO / relpath
        pkg_path = PKG / relpath
        assert repo_path.is_file(), "仓库里没有 %s" % rel(repo_path)
        assert pkg_path.is_file(), "包里没有 %s" % rel(pkg_path)
        ev.append("%s：仓库 ✓ 包内 ✓" % rel(repo_path).replace("/", "\\"))
    return ev


@case("C-04", "包上实跑 重启工作台.bat：<5 s、端口不变、pid 变了")
def c04():
    ev = []
    stale = assets_are_fresh(PKG)
    assert not stale, "包不是用当前代码构建的：%s；先重建：%s" % ("；".join(stale), BUILD_CMD)
    ev.append("包与当前代码/模板一致：%s" % rel(PKG))

    pid_8789_before = listener_pid(FIXED_8789)
    ev.append("8789 上现有监听 pid（用户的工作台，不动它）：%s" % (pid_8789_before or "无"))

    port = pick_port()
    assert port is not None, "879x 候选端口全被占用（%s），无法起独立实例" % (PORT_CANDIDATES,)
    ev.append("选用端口：%d（启动前空闲）" % port)

    copy = package_copy()
    ev.append("复制到包外路径：%s（剥环境运行）" % copy)
    env = stripped_env(_COPY["base"])

    bat = copy / RESTART_BAT
    try:
        code1, out1, secs1 = _run_bat(bat, [port], env=env, timeout=180)
        assert code1 == 0, "第一次启动退出码 %d（%.1fs），输出尾部：%s" % (
            code1, secs1, "\n".join(out1.splitlines()[-6:]))
        data1 = wait_health(port, 20.0)
        pid1 = listener_pid(port)
        assert data1 is not None, "第一次启动后 %d 上 health 拿不到：%s" % (
            port, "\n".join(out1.splitlines()[-6:]))
        assert pid1, "第一次启动后 %d 上没有监听进程" % port
        pipe1 = str((data1.get("diagnostics") or {}).get("pipeline_dir") or "")
        assert pipe1 and str(copy).lower() in pipe1.lower(), \
            "health.diagnostics.pipeline_dir=%r 不指向复制的包" % pipe1
        ev.append("第一次启动：exit=0 %.1fs，port=%d pid=%d，pipeline_dir=%s"
                  % (secs1, port, pid1, pipe1))

        code2, out2, secs2 = _run_bat(bat, [port], env=env, timeout=180)
        data2 = wait_health(port, 10.0)
        pid2 = listener_pid(port)
        assert code2 == 0, "重启退出码 %d（%.1fs），输出尾部：%s" % (
            code2, secs2, "\n".join(out2.splitlines()[-8:]))
        assert data2 is not None, "重启后 %d 上 health 拿不到（%.1fs）" % (port, secs2)
        assert pid2, "重启后 %d 上没有监听进程" % port
        assert pid2 != pid1, "重启前后 pid 相同（%d）——没有真重启" % pid1
        assert secs2 <= 5.0, "重启耗时 %.2fs 超过 5 秒（DoD 3）" % secs2
        pipe2 = str((data2.get("diagnostics") or {}).get("pipeline_dir") or "")
        assert str(copy).lower() in pipe2.lower(), \
            "重启后 pipeline_dir=%r 不指向复制的包" % pipe2
        ev.append("重启：exit=0 %.2fs，port=%d（不变），pid %d → %d（变了），health 200"
                  % (secs2, port, pid1, pid2))
    finally:
        # 无论断言在哪一步炸，都把自己起的那个端口的进程收掉（只按 pid 归属清理）。
        ev += cleanup(port, copy, "清理")

    pid_8789_after = listener_pid(FIXED_8789)
    assert pid_8789_after == pid_8789_before, \
        "8789 的监听 pid 从 %s 变成 %s —— 本用例动了用户的工作台" % (
            pid_8789_before, pid_8789_after)
    ev.append("8789 监听 pid 前后一致（%s）" % (pid_8789_after or "无"))
    return ev


@case("C-05", "包上实跑 检查环境.bat：退出 0、三类各一行、无 [FAIL]")
def c05():
    ev = []
    stale = assets_are_fresh(PKG)
    assert not stale, "包不是用当前代码构建的：%s；先重建：%s" % ("；".join(stale), BUILD_CMD)
    copy = package_copy()
    env = stripped_env(_COPY["base"])
    bat = copy / CHECK_BAT
    # DoD 4 的口径是 `echo.|cmd /c 检查环境.bat`：这里喂三个回车过掉 pause。
    code, out, secs = _run_bat(bat, [], env=env, timeout=300, stdin_data=b"\r\n\r\n\r\n")
    assert code == 0, "检查环境.bat 退出码 %d（%.1fs），输出尾部：%s" % (
        code, secs, "\n".join(out.splitlines()[-10:]))
    lines = out.splitlines()
    for kw in ("包内解释器", "ffmpeg", "whisper 模型"):
        hit = [ln.strip() for ln in lines if kw in ln]
        assert hit, "输出里没有含 %r 的行；输出的 [OK]/[WARN]/[FAIL] 行：%s" % (
            kw, "\n".join(ln for ln in lines if ln.strip().startswith("[")) or "(无)")
        ev.append("%s：%s" % (kw, hit[0][:110]))
    fails = [ln.strip() for ln in lines if "[FAIL]" in ln]
    assert not fails, "slim 包不该有 [FAIL]：%s" % fails
    warns = [ln.strip() for ln in lines if "[WARN]" in ln]
    assert warns, "slim 包没有模型，应至少有一条 [WARN]（否则「无模型」没有被验到）"
    ev.append("exit=0（%.1fs），[FAIL] 0 条，[WARN] %d 条：%s"
              % (secs, len(warns), warns[0][:110]))
    return ev


@case("C-06", "包不存在时退出码 2 并打印构建命令（临时改名 → 恢复）")
def c06():
    ev = []
    # 只隐藏真正被验收的那份包（默认 build\portable\qa18\slim；QA18_PKG 覆盖时也成立）。
    hidden = PKG.with_name(PKG.name + ".qa-hidden")
    before_sha = sha(PKG / RESTART_BAT)
    if hidden.exists():
        shutil.rmtree(hidden)
    PKG.rename(hidden)
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["QA18_NESTED"] = "1"
        code, out, secs = _run(
            [sys.executable, str(Path(__file__).resolve()), "--missing-probe"],
            cwd=REPO, env=env, timeout=180)
    finally:
        hidden.rename(PKG)
    assert code == 2, "包缺失时退出码应为 2，实得 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-6:]))
    assert "[FAIL] 缺少" in out, "包缺失时没有打印 [FAIL] 缺少 …：%s" % out[-300:]
    assert "build_portable.py" in out, "包缺失时没有打印构建命令"
    assert sha(PKG / RESTART_BAT) == before_sha, "改名再恢复后包内容变了（sha 不一致）"
    line = next((ln.strip() for ln in out.splitlines() if "[FAIL] 缺少" in ln), "")
    ev.append("缺包时退出码 2：%s" % line)
    ev.append("恢复后 %s sha256 与改名前一致，目录回到 %s" % (RESTART_BAT, rel(PKG)))
    return ev


@case("C-07", "入口：能被 run-all 发现，并能在仓库根用文档指定解释器跑起来")
def c07():
    ev = []
    entry = Path(__file__).resolve()
    assert entry.name == "run.py", "入口文件名必须是 run.py，实为 %s" % entry.name
    runall = REPO / "qa" / "run-all.ps1"
    assert runall.is_file(), "缺共享总跑器 qa\\run-all.ps1"
    text = runall.read_text(encoding="utf-8", errors="replace")
    assert "run.py" in text, "qa\\run-all.ps1 的入口顺序里没有 run.py"
    py = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
    assert py.is_file(), "缺解释器 %s" % rel(py)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QA18_NESTED"] = "1"
    code, out, secs = _run([str(py), str(entry), "--entry-probe"], cwd=REPO, env=env, timeout=180)
    assert code == 0, "入口探针退出码 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-6:]))
    assert "ENTRY-PROBE OK" in out, "入口探针没有打印 ENTRY-PROBE OK"
    ev.append("run-all.ps1 含 run.py ✓；%s --entry-probe → 退出码 0（仓库根，%.1fs）"
              % (rel(entry), secs))
    return ev


# ────────────────────────────── 主流程 ──────────────────────────────
def _missing_report() -> int:
    print("[FAIL] 缺少 %s，先跑 packaging\\build_portable.py" % rel(PKG))
    print("       构建命令（本机实测可用）：%s" % BUILD_CMD)
    print("       （T-18 DoD 5 的原文以 build\\portable\\slim 为准；本作业按 PM 指定用单独的 --out，")
    print("         不拿 T-15 的旧包验新代码，所以路径是 %s）" % rel(PKG))
    return 2


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print("T-18 验收：%s" % rel(PKG))
    if not PKG.is_dir():
        return _missing_report()
    if "--entry-probe" in args:
        print("ENTRY-PROBE OK %s" % rel(PKG))
        return 0
    if "--missing-probe" in args:
        # 只有在包缺失时才该走到这里；包在 = 探针失效。
        print("[FAIL] 包存在，--missing-probe 不该被这样调用")
        return 1

    ok = fail = 0
    for cid, title, fn in CASES:
        try:
            evidence = fn() or []
            ok += 1
            print("ok %s %s" % (cid, title))
            for line in evidence:
                print("     %s" % line)
        except AssertionError as exc:
            fail += 1
            print("FAIL %s %s -> %s" % (cid, title, exc))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print("FAIL %s %s -> %s: %s" % (cid, title, type(exc).__name__, exc))
            tb = traceback.format_exc().strip().splitlines()[-4:]
            for line in tb:
                print("     %s" % line.strip())

    # 起停清理的兜底：只要我复制过的包还在，就再确认一次没有遗留进程占着 879x。
    leftover = [p for p in PORT_CANDIDATES if listener_pid(p)]
    if leftover:
        print("     注意：%s 上仍有监听进程（本用例在 C-04 里按 pid 归属清理）" % leftover)

    if fail:
        keep = _COPY["path"]
        if keep is not None:
            print("     复制包保留在：%s（失败现场，可查 logs）" % keep)
        else:
            drop_copy()
    else:
        drop_copy()

    print("通过 %d / %d" % (ok, ok + fail))
    print("T-18 RESULT: %d/%d ok" % (ok, ok + fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
