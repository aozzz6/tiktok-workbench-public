# -*- coding: utf-8 -*-
"""qa/T-28/run.py —— T-28「反推抽帧用包内 ffmpeg」的独立验收用例。

跑法（仓库根）：
    pipeline\\.venv\\Scripts\\python.exe qa\\T-28\\run.py

退出码：0 = 全部通过；1 = 有用例不通过；2 = 验收包不存在（不算通过，禁止 SKIP 成 0）。

被测对象：`build\\portable\\slim`（本轮构建产物；不存在时退出码 2 并打印构建命令）。
这不是工程师用例的复刻：本文件自己按 DoD 重跑一遍关键路径（自己的 fixture、自己的
HTTP 客户端、自己的剥环境启动、自己的端口探针），工程师的
`packaging\\tests\\reverse_ffmpeg_package.test.py` 只作为 C-01 的一条证据被 subprocess 调用。

八条用例（M2 QA 清单 T-28 的 1–8 条）：
    C-01 工程师用例 packaging\\tests\\reverse_ffmpeg_package.test.py 退出 0
    C-02 包不存在 → 退出码 2 并打印构建命令（临时改名 → 子进程 → finally 恢复并校验）
    C-03 用**包内** ffmpeg 造 6 s fixture，包内 ffprobe 读回 6.0 s / 640x360
    C-04 剥环境起服务（879x 空闲端口，绝不用 8789）→ POST /api/reverse/import
         → 轮询到 stage=ready、frames>=1，且 <pkg>\\reverse\\<vid>\\frames\\f_001.jpg 在
    C-05 把 <pkg>\\ffmpeg\\bin\\ffmpeg.exe 改名 + **重启** → 同一个 import 必须失败，
         error / detail 含 ffmpeg
    C-06 恢复文件名 + 重启 → 再成功一次（证明 C-05 不是偶然）
    C-07 清理：停自己起的进程、删 <pkg>\\reverse\\<vid>\\、包目录文件清单复原
    C-08 QA 入口：能被 run-all 发现，并能在仓库根用文档指定解释器跑起来

纪律：剥环境（PATH 只留 system32、独立空 TEMP/TMP、清空 WB_*/HF_*/PYTHON*、代理黑洞）；
端口只挑空闲的 879x；只杀自己能证明归属的 pid；不改产品代码；结束时把 fixture 的
任务目录与改名后的文件全部复原。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[2]
PKG = Path(os.environ.get("QA28_PKG") or (REPO / "build" / "portable" / "slim"))
PY = PKG / "python" / "python.exe"
FFMPEG = PKG / "ffmpeg" / "bin" / "ffmpeg.exe"
FFMPEG_BAK = PKG / "ffmpeg" / "bin" / "ffmpeg.exe.bak"
FFPROBE = PKG / "ffmpeg" / "bin" / "ffprobe.exe"
REVERSE = PKG / "reverse"
RESTART_BAT = PKG / "重启工作台.bat"

FIXED_8789 = 8789
PORT_CANDIDATES = (8796, 8797, 8798, 8799, 8795, 8794)  # 只用 879x

BUILD_CMD = (
    "webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py "
    "--flavor slim "
    '--ffmpeg-dir "D:\\ffmpeg\\build\\ffmpeg-9.0.2-essentials_build\\bin"'
)
# 「本轮构建产物」的判据：包里这几份必须与仓库当前源码逐字节一致。
# （左边是仓库里的源文件，右边是包里的落点；入口模板在 packaging\templates\ 下。）
FRESH_FILES = (
    ("webapp/app.py", "webapp/app.py"),
    ("webapp/tk_reverse.py", "webapp/tk_reverse.py"),
    ("webapp/_boot.py", "webapp/_boot.py"),
    ("webapp/_launch.py", "webapp/_launch.py"),
    ("packaging/templates/_setenv.bat", "_setenv.bat"),
    ("packaging/templates/start.bat", "start.bat"),
    ("packaging/templates/启动工作台.bat", "启动工作台.bat"),
    ("first_run.py", "first_run.py"),
)
# 运行时允许变化的目录（T-28 DoD 5 的：「不许把素材留在包里」只针对反推任务目录）
RUNTIME_IGNORE_DIRS = ("logs/", "pipeline/output/", "webapp/data/")

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
    kwargs = dict(cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, env=env, timeout=timeout)
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


def _run_bat(bat: Path, args=(), env=None, timeout=180):
    """绝对路径调 .bat；参数交给 list2cmdline 补引号，不手写引号（含空格路径会坏）。"""
    cmd = ["cmd", "/d", "/c", "call", str(bat)] + [str(a) for a in args]
    return _run(cmd, env=env, timeout=timeout)


def _ps(script: str):
    ps = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


def listener_pid(port: int):
    """独立取监听 pid（Win32 API，不抄产品的 netstat 解析，也不会把 TIME_WAIT 当监听）。"""
    _rc, out, _err = _ps(
        "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue "
        "| Select-Object -First 1 -ExpandProperty OwningProcess)" % port)
    text = out.strip()
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def pid_exe(pid: int) -> str:
    _rc, out, _err = _ps("(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').ExecutablePath" % pid)
    return out.strip()


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
        if port_free(port):
            return port
    return None


def stripped_env(base: Path) -> dict:
    """剥环境：PATH 只留 system32；独立空 TEMP/TMP；清空 WB_*/HF_*/PYTHON*；代理黑洞。"""
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    temp = base / "child-temp"
    tmp = base / "child-tmp"
    temp.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    keep = ("SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
            "USERNAME", "USERDOMAIN", "ProgramData", "ProgramFiles",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = sysroot + r"\system32;" + sysroot
    env["TEMP"] = str(temp)
    env["TMP"] = str(tmp)
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    return env


def health(port: int, timeout: float = 2.0):
    try:
        with _opener.open("http://127.0.0.1:%d/api/health" % port, timeout=timeout) as resp:
            data = json.loads(resp.read(65536).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) and data.get("ok") is True else None


def wait_health(port: int, timeout: float = 40.0, stable: int = 2, gap: float = 0.4):
    """等 health 连续 stable 次成功：刚重启完偶发一次连不上时，不把它当“已就绪”。"""
    deadline = time.time() + timeout
    hits = 0
    last = None
    while time.time() < deadline:
        data = health(port)
        if data is not None:
            hits += 1
            last = data
            if hits >= stable:
                return last
            time.sleep(gap)
        else:
            hits = 0
            time.sleep(0.2)
    return last if hits else None


def http_json(method: str, url: str, body: bytes | None = None, timeout: float = 180.0,
              retries: int = 5):
    """返回 (HTTP 状态码, JSON 或原文)。不发送 Origin / Sec-Fetch-*（本地写守卫）。

    只在“连接被拒绝”时重试：那种情况请求根本没到服务端，重发不会产生第二次导入；
    超时/其它错误不重试（避免重复 POST）。
    """
    last_exc = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "video/mp4")
        try:
            with _opener.open(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
            break
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, (exc.read() or b"")
            break
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            refused = isinstance(reason, (ConnectionRefusedError, ConnectionAbortedError)) or \
                (isinstance(reason, OSError) and getattr(reason, "winerror", None) in (10061, 10054))
            last_exc = exc
            if not refused or attempt == retries - 1:
                raise
            time.sleep(0.5)
    else:  # pragma: no cover - 循环必有 break 或 raise
        raise last_exc  # type: ignore[misc]
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


# ────────────────────────────── 包与运行态 ──────────────────────────────
def assets_are_stale() -> list:
    stale = []
    for repo_rel, pkg_rel in FRESH_FILES:
        src = REPO / repo_rel
        dst = PKG / pkg_rel
        if not dst.is_file():
            stale.append("%s：包里没有" % rel(dst))
        elif not src.is_file():
            stale.append("%s：仓库里没有" % rel(src))
        elif sha(src) != sha(dst):
            stale.append("%s：与 %s 不一致" % (rel(dst), rel(src)))
    return stale


def repair_package() -> list:
    """把上一次被打断的运行留下的 `ffmpeg.exe.bak` 复原（否则整包是坏的）。"""
    ev = []
    if FFMPEG_BAK.exists() and not FFMPEG.exists():
        FFMPEG_BAK.rename(FFMPEG)
        ev.append("修复：上一次运行留下的 ffmpeg.exe.bak 已复原为 ffmpeg.exe")
    elif FFMPEG_BAK.exists() and FFMPEG.exists():
        ev.append("注意：包里同时有 ffmpeg.exe 与 ffmpeg.exe.bak（残留，本次不动）")
    return ev


def kill_pkg_processes(why: str) -> list:
    """结束“可执行文件在本包内”的进程（目录改名会因它们失败）。只认包内路径。"""
    ev = []
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '%s*' } "
              "| Select-Object -ExpandProperty ProcessId" % str(PKG).replace("'", "''"))
    _rc, out, _err = _ps(script)
    for token in out.split():
        if not token.isdigit():
            continue
        subprocess.run(["taskkill", "/PID", token, "/T", "/F"], capture_output=True, timeout=30)
        ev.append("%s：结束包内进程 pid %s" % (why, token))
    return ev


_STATE = {"port": None, "env": None, "tmp": None, "vid": [], "baseline": None}


def start_pkg(port: int, env: dict, timeout: float = 180.0):
    """用包装里的 `重启工作台.bat <port>` 起/重启服务（它会 call _setenv.bat，见 CRD-0007）。"""
    code, out, secs = _run_bat(RESTART_BAT, [port], env=env, timeout=timeout)
    data = wait_health(port, 40.0)
    return code, out, secs, data, listener_pid(port)


def cleanup_service(why: str) -> list:
    ev = []
    port = _STATE.get("port")
    if not port:
        return ev
    pid = listener_pid(port)
    if pid:
        exe = pid_exe(pid)
        if exe and str(PKG).lower() not in exe.lower():
            ev.append("%s：pid %d 的可执行文件是 %s，不在包里 → 不杀" % (why, pid, exe))
        else:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30)
            deadline = time.time() + 10
            while time.time() < deadline and listener_pid(port):
                time.sleep(0.2)
            ev.append("%s：已结束 pid %d（端口 %d 现在 %s）"
                      % (why, pid, port, "已释放" if not listener_pid(port) else "仍有监听"))
    return ev


def tree_snapshot() -> dict:
    """包目录快照（相对路径 → 文件大小）；排除运行时允许变化的目录。"""
    snap = {}
    for path in PKG.rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(PKG).as_posix()
        if any(relpath.startswith(d) for d in RUNTIME_IGNORE_DIRS):
            continue
        if relpath.startswith("reverse/_uploads/"):
            continue
        snap[relpath] = path.stat().st_size
    return snap


# ────────────────────────────── 用例 ──────────────────────────────
@case("C-01", "工程师用例 packaging\\tests\\reverse_ffmpeg_package.test.py 退出 0 且自己清理干净")
def c01():
    py = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
    test = REPO / "packaging" / "tests" / "reverse_ffmpeg_package.test.py"
    assert py.is_file(), "缺解释器 %s" % rel(py)
    assert test.is_file(), "缺工程师用例 %s" % rel(test)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    before = tree_snapshot()
    code, out, secs = _run([str(py), str(test)], cwd=REPO, env=env, timeout=900)
    after = tree_snapshot()
    assert code == 0, "工程师用例退出码 %d（%.1fs），尾部：%s" % (
        code, secs, "\n".join(out.splitlines()[-12:]))
    diff = sorted(set(before) ^ set(after)) + sorted(
        k for k in set(before) & set(after) if before[k] != after[k])
    assert not diff, "工程师用例通过了，但结束时包目录没复原（多/少/大小变了）：%s" % (diff[:8],)
    keep = [ln.strip() for ln in out.splitlines()
            if any(k in ln for k in ("vid", "帧", "耗时", "PASS"))]
    return ["退出码 0（%.1fs）" % secs] + keep[-4:] + ["结束时包目录与用例前一致"]


@case("C-02", "包不存在 → 退出码 2 并打印构建命令（临时改名 → 恢复）")
def c02():
    for line in kill_pkg_listeners("改名前置") + kill_pkg_processes("改名前置"):
        print("     %s" % line)
    before = sha(FFMPEG)
    hidden = PKG.with_name(PKG.name + ".qa28-hidden")
    if hidden.exists():
        shutil.rmtree(hidden)
    renamed = False
    for attempt in range(20):
        try:
            PKG.rename(hidden)
            renamed = True
            break
        except PermissionError as exc:
            if attempt == 19:
                raise AssertionError("包目录无法改名（还有进程开着里面的文件）：%s" % exc) from exc
            kill_pkg_processes("改名重试")
            time.sleep(0.5)
    assert renamed, "包目录改名失败"
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["QA28_NESTED"] = "1"
        code, out, secs = _run([sys.executable, str(Path(__file__).resolve()), "--missing-probe"],
                               cwd=REPO, env=env, timeout=180)
    finally:
        hidden.rename(PKG)
    assert code == 2, "包缺失时退出码应为 2，实得 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-6:]))
    assert "[FAIL] 缺少" in out, "包缺失时没有打印 [FAIL] 缺少 …：%s" % out[-300:]
    assert "build_portable.py" in out, "包缺失时没有打印构建命令"
    assert sha(FFMPEG) == before, "改名再恢复后 ffmpeg.exe 的 sha256 变了"
    line = next((ln.strip() for ln in out.splitlines() if "[FAIL] 缺少" in ln), "")
    return ["缺包时退出码 2：%s" % line, "恢复后 %s sha256 一致" % rel(FFMPEG)]


@case("C-03", "包内 ffmpeg 造 6 s fixture，包内 ffprobe 读回 6.0 s / 640x360")
def c03():
    for line in repair_package():
        print("     %s" % line)
    assert FFMPEG.is_file(), "包里没有 %s" % rel(FFMPEG)
    assert FFPROBE.is_file(), "包里没有 %s" % rel(FFPROBE)
    fixture = Path(_STATE["tmp"]) / "fixture.mp4"
    code, out, secs = _run(
        [str(FFMPEG), "-hide_banner", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=640x360:rate=25", "-t", "6", "-pix_fmt", "yuv420p", str(fixture)],
        timeout=120)
    assert code == 0, "包内 ffmpeg 造素材失败（exit=%d）：%s" % (
        code, "\n".join(out.splitlines()[-6:]))
    assert fixture.is_file() and fixture.stat().st_size > 0, "fixture 没生成"
    pcode, pout, _psecs = _run(
        [str(FFPROBE), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration:format=duration",
         "-of", "json", str(fixture)], timeout=60)
    assert pcode == 0, "包内 ffprobe 读不出 fixture：%s" % "\n".join(pout.splitlines()[-6:])
    data = json.loads(pout)
    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or stream.get("duration") or 0)
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    assert abs(duration - 6.0) <= 0.5, "fixture 时长 %.2fs 不是 6s" % duration
    assert (width, height) == (640, 360), "fixture 分辨率 %sx%s 不是 640x360" % (width, height)
    return ["包内 ffmpeg 造素材：exit=0 %.1fs，%.0f KB" % (secs, fixture.stat().st_size / 1024.0),
            "包内 ffprobe：duration=%.2fs, %dx%d" % (duration, width, height)]


@case("C-04", "剥环境起服务 + POST import → ready、frames>=1、f_001.jpg 在")
def c04():
    assert RESTART_BAT.is_file(), "包里没有 %s" % rel(RESTART_BAT)
    stale = assets_are_stale()
    assert not stale, "包不是用当前代码构建的：%s；先重建：%s" % ("；".join(stale), BUILD_CMD)
    port = pick_port()
    assert port is not None, "879x 候选端口全被占用（%s）" % (PORT_CANDIDATES,)
    _STATE["port"] = port
    env = _STATE["env"] = stripped_env(Path(_STATE["tmp"]))
    pid_8789_before = listener_pid(FIXED_8789)
    code, out, secs, data, pid = start_pkg(port, env)
    assert code == 0, "启动退出码 %d（%.1fs）：%s" % (code, secs, "\n".join(out.splitlines()[-8:]))
    assert data is not None, "启动后 %d 上 health 拿不到：%s" % (port, "\n".join(out.splitlines()[-8:]))
    assert pid, "启动后 %d 上没有监听进程" % port
    pipe = str((data.get("diagnostics") or {}).get("pipeline_dir") or "")
    assert str(PKG).lower() in pipe.lower(), "health.pipeline_dir=%r 不指向这个包" % pipe
    fixture = Path(_STATE["tmp"]) / "fixture.mp4"
    body = fixture.read_bytes()
    status, payload = http_json("POST", "http://127.0.0.1:%d/api/reverse/import?filename=fixture.mp4"
                                % port, body=body)
    assert status == 200, "POST import 返回 %d：%s" % (status, str(payload)[:300])
    assert isinstance(payload, dict), "POST import 响应不是 JSON：%r" % (payload,)
    vid = str(payload.get("video_id") or payload.get("vid") or "")
    assert vid, "POST import 没给 video_id：%s" % str(payload)[:300]
    _STATE["vid"].append(vid)
    final = _poll(port, vid, 180.0)
    frames = int(final.get("frames") or 0)
    assert final.get("stage") == "ready", "终态不是 ready：stage=%r error=%r" % (
        final.get("stage"), final.get("error"))
    assert frames >= 1, "frames=%d，没有抽到帧" % frames
    first = REVERSE / vid / "frames" / "f_001.jpg"
    assert first.is_file(), "缺帧文件 %s" % rel(first)
    assert first.stat().st_size > 0, "帧文件是空的：%s" % rel(first)
    assert listener_pid(FIXED_8789) == pid_8789_before, "8789 的监听 pid 被本用例改动了"
    ev_rows = sorted(p.name for p in (REVERSE / vid / "frames").glob("f_*.jpg"))
    return ["剥环境启动：exit=0 %.1fs，port=%d pid=%d" % (secs, port, pid),
            "POST import：200，vid=%s" % vid,
            "终态：stage=ready，frames=%d，帧文件=%s（%d 个 f_*.jpg）"
            % (frames, rel(first), len(ev_rows)),
            "8789 监听 pid 前后一致（%s）" % (pid_8789_before or "无")]


@case("C-05", "改名包内 ffmpeg + 重启 → 同一个 import 必须失败（error/detail 含 ffmpeg）")
def c05():
    port = _STATE["port"]
    env = _STATE["env"]
    assert port and env, "C-04 没把服务起起来，无法做这一条"
    assert FFMPEG.is_file(), "改名前的 %s 不在了" % rel(FFMPEG)
    if FFMPEG_BAK.exists():
        FFMPEG_BAK.unlink()
    FFMPEG.rename(FFMPEG_BAK)
    try:
        code, out, secs, data, pid = start_pkg(port, env)
        assert code == 0, "改名后重启退出码 %d（%.1fs）：%s" % (
            code, secs, "\n".join(out.splitlines()[-8:]))
        assert data is not None, "改名后 %d 上 health 拿不到" % port
        assert pid, "改名后 %d 上没有监听进程" % port
        fixture = Path(_STATE["tmp"]) / "fixture.mp4"
        status, payload = http_json("POST", "http://127.0.0.1:%d/api/reverse/import?filename=fixture.mp4"
                                    % port, body=fixture.read_bytes())
        text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        assert "ffmpeg" in text, "失败信息里没有 ffmpeg：HTTP %d %s" % (status, text[:400])
        if status == 400:
            return ["改名 ffmpeg.exe → ffmpeg.exe.bak + 重启（%.1fs）" % secs,
                    "POST import：400（detail 含 ffmpeg）：%s" % text[:220]]
        assert status == 200, "既不是 400 也不是 200，而是 %d：%s" % (status, text[:300])
        vid = str(payload.get("video_id") or payload.get("vid") or "")
        assert vid, "失败路径没给 video_id：%s" % text[:300]
        _STATE["vid"].append(vid)
        final = _poll(port, vid, 180.0)
        assert final.get("stage") == "failed", "终态不是 failed：%r" % (final.get("stage"),)
        error = str(final.get("error") or "")
        assert "ffmpeg" in error, "error 里没有 ffmpeg：%r" % error
        return ["改名 ffmpeg.exe → ffmpeg.exe.bak + 重启（%.1fs）" % secs,
                "POST import：200，vid=%s → stage=failed" % vid,
                "error 原文：%s" % error[:220]]
    finally:
        if FFMPEG_BAK.exists() and not FFMPEG.exists():
            FFMPEG_BAK.rename(FFMPEG)


@case("C-06", "恢复 ffmpeg + 重启 → 再成功一次（证明 C-05 不是偶然）")
def c06():
    port = _STATE["port"]
    env = _STATE["env"]
    assert port and env, "C-04 没把服务起起来，无法做这一条"
    assert FFMPEG.is_file(), "恢复后的 %s 不在" % rel(FFMPEG)
    code, out, secs, data, pid = start_pkg(port, env)
    assert code == 0, "恢复后重启退出码 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-8:]))
    assert data is not None, "恢复后 %d 上 health 拿不到" % port
    assert pid, "恢复后 %d 上没有监听进程" % port
    fixture = Path(_STATE["tmp"]) / "fixture.mp4"
    status, payload = http_json("POST", "http://127.0.0.1:%d/api/reverse/import?filename=fixture.mp4"
                                % port, body=fixture.read_bytes())
    assert status == 200, "恢复后 POST import 返回 %d：%s" % (status, str(payload)[:300])
    vid = str((payload or {}).get("video_id") or (payload or {}).get("vid") or "")
    assert vid, "恢复后 POST import 没给 video_id：%s" % str(payload)[:300]
    _STATE["vid"].append(vid)
    final = _poll(port, vid, 180.0)
    frames = int(final.get("frames") or 0)
    assert final.get("stage") == "ready", "恢复后终态不是 ready：%r" % (final.get("stage"),)
    assert frames >= 1, "恢复后 frames=%d" % frames
    first = REVERSE / vid / "frames" / "f_001.jpg"
    assert first.is_file(), "恢复后缺帧文件 %s" % rel(first)
    return ["恢复 + 重启：exit=0 %.1fs，port=%d pid=%d" % (secs, port, pid),
            "再 POST：200，vid=%s → stage=ready，frames=%d" % (vid, frames)]


@case("C-07", "清理：停自起进程、删 <pkg>\\reverse\\<vid>\\、包文件清单复原")
def c07():
    ev = []
    ev += cleanup_service("清理")
    port = _STATE["port"]
    assert port is None or not listener_pid(port), "端口 %s 上还有监听进程" % port
    # 删掉本轮创建的所有反推任务目录（按基线之外的新目录删，不碰别人的）
    deleted = []
    baseline_dirs = set((_STATE.get("baseline") or {}).get("__dirs__") or ())
    for child in sorted(REVERSE.iterdir()) if REVERSE.is_dir() else []:
        if not child.is_dir() or child.name == "_uploads":
            continue
        if child.name not in baseline_dirs:
            shutil.rmtree(child, ignore_errors=True)
            deleted.append(child.name)
    # 反推根下 stash 的素材：_uploads 里本轮新出现的文件也清掉
    removed_uploads = 0
    uploads = REVERSE / "_uploads"
    base_uploads = set((_STATE.get("baseline") or {}).get("__uploads__") or ())
    if uploads.is_dir():
        for item in uploads.iterdir():
            if item.is_file() and item.name not in base_uploads:
                item.unlink(missing_ok=True)
                removed_uploads += 1
    ev.append("已删反推任务目录：%s；_uploads 清理 %d 个残留文件"
              % (", ".join(deleted) or "无", removed_uploads))
    # 包目录复原（排除 logs/ pipeline/output/ webapp/data/ 与 reverse/_uploads）
    after = tree_snapshot()
    before = (_STATE.get("baseline") or {}).get("files") or {}
    changed = sorted(set(before) ^ set(after))
    size_changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    assert not changed, "包目录文件清单变了（多/少）：%s" % (changed[:8],)
    assert not size_changed, "包目录有文件大小变了：%s" % (size_changed[:8],)
    ev.append("包目录文件清单与用例前一致（%d 个文件，排除 logs/、pipeline/output/、webapp/data/）"
              % len(after))
    assert not FFMPEG_BAK.exists(), "ffmpeg.exe.bak 还在（没复原）"
    if port:
        assert not listener_pid(port), "自己用的端口 %d 上还有监听进程" % port
        ev.append("ffmpeg.exe 已复原；端口 %d 无残留监听" % port)
    return ev


@case("C-08", "QA 入口：能被 run-all 发现，并能在仓库根用文档指定解释器跑起来")
def c08():
    entry = Path(__file__).resolve()
    assert entry.name == "run.py", "入口文件名必须是 run.py"
    runall = REPO / "qa" / "run-all.ps1"
    assert runall.is_file(), "缺共享总跑器 qa\\run-all.ps1"
    text = runall.read_text(encoding="utf-8", errors="replace")
    assert "run.py" in text, "qa\\run-all.ps1 的入口顺序里没有 run.py"
    py = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
    assert py.is_file(), "缺解释器 %s" % rel(py)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QA28_NESTED"] = "1"
    code, out, secs = _run([str(py), str(entry), "--entry-probe"], cwd=REPO, env=env, timeout=180)
    assert code == 0, "入口探针退出码 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-6:]))
    assert "ENTRY-PROBE OK" in out, "入口探针没有打印 ENTRY-PROBE OK"
    return ["run-all.ps1 含 run.py ✓；--entry-probe → 退出码 0（仓库根）"]


def _poll(port: int, vid: str, timeout: float) -> dict:
    url = "http://127.0.0.1:%d/api/reverse/status/%s" % (port, vid)
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        status, payload = http_json("GET", url, timeout=30)
        if status == 200 and isinstance(payload, dict):
            last = payload
            if payload.get("stage") in ("ready", "failed", "stopped"):
                return payload
        time.sleep(0.4)
    raise AssertionError("轮询 %ds 后仍未到终态，最后状态：%s"
                         % (int(timeout), json.dumps(last, ensure_ascii=False)[:300]))


def kill_pkg_listeners(why: str) -> list:
    """把“可执行文件在本包内”的监听进程收掉（只收自己的包，绝不碰 8789 上别人的进程）。"""
    ev = []
    for port in PORT_CANDIDATES:
        pid = listener_pid(port)
        if not pid:
            continue
        exe = pid_exe(pid)
        if exe and str(PKG).lower() in exe.lower():
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30)
            ev.append("%s：包内进程 pid %d（端口 %d）已结束" % (why, pid, port))
    return ev


# ────────────────────────────── 主流程 ──────────────────────────────
def _missing_report() -> int:
    print("[FAIL] 缺少 %s，先跑 packaging\\build_portable.py" % rel(PKG))
    print("       构建命令（本机实测可用）：%s" % BUILD_CMD)
    return 2


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print("T-28 验收：%s" % rel(PKG))
    if not PKG.is_dir():
        return _missing_report()
    if "--entry-probe" in args:
        print("ENTRY-PROBE OK %s" % rel(PKG))
        return 0
    if "--missing-probe" in args:
        print("[FAIL] 包存在，--missing-probe 不该被这样调用")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="qa28-", dir="D:\\"))
    _STATE["tmp"] = tmp
    print("临时目录：%s" % tmp)
    for line in repair_package() + kill_pkg_processes("启动前清理"):
        print("     %s" % line)
    stale = assets_are_stale()
    print("包新鲜度（与当前源码逐字节比对）：%s" % ("一致" if not stale else "不一致 → " + "；".join(stale)))
    if REVERSE.is_dir():
        _STATE["baseline"] = {
            "files": tree_snapshot(),
            "__dirs__": [p.name for p in REVERSE.iterdir() if p.is_dir()],
            "__uploads__": [p.name for p in (REVERSE / "_uploads").iterdir()] if (REVERSE / "_uploads").is_dir() else [],
        }

    ok = fail = 0
    try:
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
                for line in traceback.format_exc().strip().splitlines()[-4:]:
                    print("     %s" % line.strip())
    finally:
        # 无论如何：停自己起的服务、复原被改名的 ffmpeg、清掉自己开的临时目录。
        if _STATE.get("port"):
            for line in cleanup_service("收尾"):
                print("     %s" % line)
        if FFMPEG_BAK.exists() and not FFMPEG.exists():
            FFMPEG_BAK.rename(FFMPEG)
            print("     收尾：ffmpeg.exe 已复原")
        if tmp.is_dir():
            if fail == 0:
                shutil.rmtree(tmp, ignore_errors=True)
                print("     收尾：临时目录已删除（%s）" % tmp)
            else:
                print("     临时目录保留（失败现场）：%s" % tmp)

    print("通过 %d / %d" % (ok, ok + fail))
    print("T-28 RESULT: %d/%d ok" % (ok, ok + fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
