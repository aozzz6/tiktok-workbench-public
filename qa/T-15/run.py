#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qa/T-15/run.py — T-15「构建脚本：把仓库装进一个双击就能启动的文件夹」的独立验收用例。

用例清单：`.dsh-crew/jobs/portable-package/M1-qa-plan.md` 的 T-15 段（1..29 条）。
DoD 来源：docs/tasks/tasks.md 里 T-15 的 DoD 1..19。

跑法（按清单）：
    cd D:\\tiktok-workbench
    pipeline\\.venv\\Scripts\\python.exe qa\\T-15\\run.py

Lead 的裁决（2026-11-05/06）：
  1. 第 15 条 `id="appVersion"` 由 M3 的 T-25 落地，M1 阶段打印
     `DEFERRED-M3: id="appVersion"（T-25）`，不计入失败，但必须出现在输出里。
  2. 第 18 条（DoD 11 连续 10 次）每次都从启动器 stdout 正则抓
     `http://127.0.0.1:(\\d+)/` 的实际端口，用抓到的端口判 health，不写死 8789。
  3. DoD 8/9/10/13/14 相关用例先重跑构建，但输出到 **单独的** `build\\portable\\qa`
     （不覆盖 `build\\portable\\slim`）；本文件所有「包内」检查都读这份新构建
     `build\\portable\\qa\\slim`，再复制到 `%TEMP%` 下、路径里仍带中文和空格的
     `<临时目录>\\测试 目录\\qa\\工作台` 做剥环境验收。
  4. DoD 8 按 CRD-0005 两条判据：首次冷启动 ≤8s、同一副本再启 ≤3s。

纪律：
  * 只写 `qa\\T-15\\`、`%TEMP%`（含其中的 `测试 目录` 沙箱）、`build\\portable\\qa`；
    **绝不碰** `D:\\测试 目录*`（HLD §9 的手动验收位置），不碰产品代码、
    不碰 `build\\portable\\slim`。
  * 只停本进程自己起的 pid（`_OUR_PIDS` 里登记的）；8789 被别人占着时
    记 BLOCKED 或换端口，**不按路径前缀判归属、不杀别人**。
  * 所有 rmtree 都受 `_safe_to_delete` 约束：只允许删本次 `<TEMP>\\qa15_*` 沙箱
    和本次 `qa_t15_*` 临时目录。
  * 剥环境：PATH=%SystemRoot%\\system32;%SystemRoot%、独立空 TEMP/TMP、清空
    WB_*/HF_*/PYTHON*、HTTP(S)_PROXY 指向黑洞；`.bat` 用绝对路径（本机
    NoDefaultCurrentDirectoryInExePath=1，不能靠 cwd 找）。
  * JSON 一律读原始字节按 UTF-8 解（本机控制台是 GBK）。
  * 每条用例都真的可能失败，没有 SKIP。
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 本机控制台/重定向默认是 GBK；本文件所有输出统一 UTF-8，避免证据里的 U+FFFD 把打印打崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─────────────────────────── 路径与常量 ───────────────────────────
REPO = Path(__file__).resolve().parents[2]
QA_DIR = Path(__file__).resolve().parent
PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
BUILD_PY = REPO / "packaging" / "build_portable.py"
BUILD_OUT = REPO / "build" / "portable" / "qa"
PKG = BUILD_OUT / "slim"
# 所有测试副本都放本次唯一沙箱（%TEMP%\qa15_sandbox_*）下，路径仍带中文和空格；
# 绝不使用 HLD §9 的手动验收位置（仓库外那个既有目录）。
_SANDBOX = Path(tempfile.mkdtemp(prefix="qa15_sandbox_"))
COPY1 = _SANDBOX / "测试 目录" / "qa" / "工作台"
COPY2 = _SANDBOX / "测试 目录 二" / "工作台"
FFMPEG_DIR = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin")
PORTS = [8789, 8791, 8792, 8793, 8787]
BROWSER = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1248" / "chrome-win64" / "chrome.exe"
PLAYWRIGHT = REPO / "webapp" / "node_modules" / "playwright-core"

SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
CMD_EXE = str(SYSTEM32 / "cmd.exe")
NETSTAT_EXE = str(SYSTEM32 / "netstat.exe")
TASKKILL_EXE = str(SYSTEM32 / "taskkill.exe")
WHERE_EXE = str(SYSTEM32 / "where.exe")
NODE_EXE = shutil.which("node") or "node"

VERSION_EXPECTED = "0.1.0"
DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
        "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
REQUIRED_FILES = (
    "python/python.exe",
    "python/Lib/site-packages/fastapi",
    "ffmpeg/bin/ffmpeg.exe",
    "ffmpeg/bin/ffprobe.exe",
    "webapp/app.py",
    "webapp/static/index.html",
    "pipeline/pipeline.py",
    "pipeline/accounts.example.json",
    "pipeline/scripts/batch_transcribe_fw.py",
    "VERSION",
    "build-info.json",
    "_setenv.bat",
    "启动工作台.bat",
    "start.bat",
    "first_run.py",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "licenses/PYTHON-PACKAGES.txt",
)
DATA_DIRS = ("ledger", "reverse", "logs", "workbuddy", "webapp/data",
             "pipeline/output", "pipeline/reports")

METRICS: dict[str, object] = {}
_OUR_PIDS: dict[int, Path] = {}      # pid -> 它应该归属的复制目录（只停这些）
_OUR_TEMPS: list[Path] = []          # 每次剥环境启动用的独立 TEMP
_PROBE_SHEET_FILE = ""
_PROBE_SHEET_NAME = ""


# ─────────────────────────── 用例框架 ───────────────────────────
class CaseFail(AssertionError):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


class CaseDeferred(Exception):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


class CaseBlocked(Exception):
    """环境被别人占住、且按纪律不能动它：不算产品失败，但也不能算通过。"""
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


RESULTS: list[tuple[str, int, str]] = []   # (status, number, desc)


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
            except CaseBlocked as e:
                print("BLOCKED %d. %s -> %s" % (num, desc, e))
                for line in e.evidence:
                    print("    " + line)
                RESULTS.append(("BLOCKED", num, desc))
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


# ─────────────────────────── 进程/端口工具 ───────────────────────────
def _decode_bytes(data: bytes) -> str:
    """子进程输出解码：先 UTF-8，再 GBK（本机 pip/cmd 可能写 GBK），最后容错。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace").replace("\ufffd", "?")


def run_cmd(args, cwd: Path | None = None, timeout: int = 600,
            env: dict[str, str] | None = None) -> tuple[object, str]:
    try:
        p = subprocess.run([str(a) for a in args],
                           cwd=str(cwd) if cwd else None,
                           env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return p.returncode, _decode_bytes(p.stdout)
    except subprocess.TimeoutExpired as e:
        return "TIMEOUT", _decode_bytes(e.stdout or b"")


def listener_pid(port: int) -> int | None:
    code, out = run_cmd([NETSTAT_EXE, "-ano"], timeout=30)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" \
                and parts[1].endswith(":%d" % port) and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def proc_image(pid: int) -> str:
    """QueryFullProcessImageNameW；拿不到就返回空串。"""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(2048)
            size = wt.DWORD(2048)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def image_under(image: str, root: Path) -> bool:
    if not image:
        return False
    try:
        return Path(image).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def port_listening(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def wait_port_free(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_listening(port):
            return True
        time.sleep(0.2)
    return not port_listening(port)


def stop_pid(pid: int, expected_root: Path) -> bool:
    """只停镜像路径在 expected_root 下的 pid；返回是否已不在监听（无法判断时 False）。"""
    if not pid:
        return True
    image = proc_image(pid)
    if not image_under(image, expected_root):
        return False
    subprocess.run([TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
                   capture_output=True, timeout=30)
    for _ in range(40):
        if proc_image(pid) == "":
            return True
        time.sleep(0.15)
    return proc_image(pid) == ""


def stop_our_instances():
    for pid, root in list(_OUR_PIDS.items()):
        try:
            stop_pid(pid, root)
        except Exception:
            pass
        _OUR_PIDS.pop(pid, None)


def ensure_8789_free(evidence: list[str]) -> bool:
    """确保 8789 空闲。只允许停 `_OUR_PIDS` 里登记的本进程实例；
    别的进程一律不动（不按路径前缀判归属）。返回 True=空闲，False=被别人占着。"""
    if not port_listening(8789):
        return True
    pid = listener_pid(8789)
    if pid and pid in _OUR_PIDS:
        evidence.append("8789 是本进程之前起的 pid=%s，停掉" % pid)
        stop_pid(pid, _OUR_PIDS[pid])
        if wait_port_free(8789, 15):
            return True
        evidence.append("本进程 pid=%s 停掉后 8789 仍未释放" % pid)
        return False
    evidence.append("8789 被非本进程 pid=%s 占用；按纪律不杀" % (pid or "?"))
    return False


def foreign_workbenches() -> list[tuple[int, int]]:
    """候选端口上正在跑的、不属于本进程的工作台（pid 不在 _OUR_PIDS）。"""
    rows = []
    for p in PORTS:
        data = health(p, 0.3)
        if not data:
            continue
        pid = listener_pid(p)
        if pid and pid not in _OUR_PIDS:
            rows.append((p, pid))
    return rows


def wait_foreign_ports_clear(evidence: list[str], timeout: float = 60.0) -> bool:
    """启动器会复用任何已在运行的工作台；若那是不属于本进程的实例，按纪律不能杀，
    只能等它自己停。等不到就返回 False（调用方记 BLOCKED）。"""
    deadline = time.time() + timeout
    while True:
        rows = foreign_workbenches()
        if not rows:
            return True
        if time.time() >= deadline:
            evidence.append("候选端口上仍有别的工作台在跑（不杀）：%s" % (rows,))
            return False
        time.sleep(1.0)


# ─────────────────────────── HTTP ───────────────────────────
def http_get(port: int, path: str, timeout: float = 10.0) -> tuple[int | None, bytes]:
    try:
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return None, b""


def http_json(port: int, path: str, timeout: float = 10.0):
    status, raw = http_get(port, path, timeout)
    if status != 200:
        return status, None, raw
    try:
        return status, json.loads(raw.decode("utf-8")), raw
    except Exception:
        return status, None, raw


def http_post_json(port: int, path: str, obj, timeout: float = 15.0) -> tuple[int | None, bytes]:
    try:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return None, b""


def health(port: int, timeout: float = 3.0):
    status, data, _ = http_json(port, "/api/health", timeout)
    return data if status == 200 and isinstance(data, dict) else None


def wait_health(port: int, timeout: float = 20.0, proc: subprocess.Popen | None = None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = health(port, 2.0)
        if data and data.get("ok") is True:
            return data
        if proc is not None and proc.poll() is not None:
            # 启动器进程退出后再给 3s：服务是 detached 的，可能还在起
            for _ in range(30):
                data = health(port, 1.0)
                if data and data.get("ok") is True:
                    return data
                time.sleep(0.1)
            return None
        time.sleep(0.15)
    return None


# ─────────────────────────── 剥环境启动 ───────────────────────────
def strip_env(temp_dir: Path) -> dict[str, str]:
    keep = ("SystemRoot", "SystemDrive", "windir", "COMSPEC",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "USERNAME",
            "HOMEDRIVE", "HOMEPATH", "OS", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "PATHEXT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    root = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = root + r"\system32;" + root
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = ""
    return env


def launch_package(pkg: Path, timeout: float = 30.0) -> dict:
    """用剥环境 + 绝对路径跑 <pkg>\\启动工作台.bat；返回 port/pid/elapsed/rc/out。"""
    bat = pkg / "启动工作台.bat"
    if not bat.is_file():
        fail("包里没有启动器：%s" % bat)
    tmp = Path(tempfile.mkdtemp(prefix="qa_t15_tmp_"))
    _OUR_TEMPS.append(tmp)
    env = strip_env(tmp)
    proc = subprocess.Popen([CMD_EXE, "/c", "call", str(bat)],
                            cwd=str(pkg), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks: list[bytes] = []
    marks: dict[str, float] = {}

    def _reader():
        try:
            for raw in proc.stdout:
                chunks.append(raw)
                line = _decode_bytes(raw)
                if "launcher" in line and "启动工作台" in line:
                    marks.setdefault("launcher_start", time.time())
                if "就绪" in line:
                    marks.setdefault("ready", time.time())
        except Exception:
            pass

    def _text() -> str:
        data = b"".join(chunks)
        for enc in ("utf-8", "gbk"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "replace")

    threading.Thread(target=_reader, daemon=True).start()
    t0 = time.time()
    port = None
    elapsed = None
    while time.time() - t0 < timeout:
        text = _text()
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", text)
        if m:
            port = int(m.group(1))
        candidates = ([port] if port else []) + [p for p in PORTS if p != port]
        for cand in candidates:
            # 短超时：app 已 bind 但还没开始服务时，一次长超时的 health 会把启动耗时
            # 多算整整一个 timeout（本机实测 1.0s 超时会把 4.6s 量成 5.4s）。
            data = health(cand, 0.25)
            if data and data.get("ok") is True:
                pid = listener_pid(cand)
                if pid and image_under(proc_image(pid), pkg):
                    if elapsed is None:
                        elapsed = time.time() - t0
                    port = cand
                    break
        if elapsed is not None:
            break
        if proc.poll() is not None and not port:
            # 启动器失败：再多等 1s 收集输出
            time.sleep(0.3)
            break
        time.sleep(0.1)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    out = _text()
    if elapsed is None:
        # 再兜底 5s：服务可能比启动器晚就绪
        for cand in PORTS:
            data = health(cand, 1.0)
            if data and data.get("ok") is True:
                pid = listener_pid(cand)
                if pid and image_under(proc_image(pid), pkg):
                    port, elapsed = cand, time.time() - t0
                    break
    pid = listener_pid(port) if port else None
    # 启动器打印「已在 … 运行」时，只有该 pid 本来就是本进程起的才认领；
    # 否则绝不接管/停别人的实例。
    if pid and "已在" in out and pid not in _OUR_PIDS:
        port, elapsed = None, None
    elif pid:
        _OUR_PIDS[pid] = pkg
    return {"rc": proc.returncode, "out": out, "port": port, "pid": pid,
            "elapsed": elapsed, "tmp": tmp, "marks": marks, "t0": t0}


def tail(text: str, n: int = 12) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-n:]


# ─────────────────────────── 其它工具 ───────────────────────────
def dir_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def mb(n: int) -> str:
    return "%.1f MB" % (n / (1024 * 1024))


def snapshot_tree(base: Path):
    """返回 (存在性, [(相对路径, 字节数), ...])；用 os.walk，86k 文件级也要跑得动。"""
    if not base.exists():
        return ("missing", [])
    rows = []
    for root, dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base).replace("\\", "/")
            try:
                rows.append((rel, os.stat(full).st_size))
            except OSError:
                rows.append((rel, -1))
    rows.sort()
    return ("present", rows)


def _safe_to_delete(path: Path) -> bool:
    """只有本次 %TEMP%\\qa15_* 沙箱、以及本次 qa_t15_* 临时目录可以删。
    `D:\\测试 目录*`（HLD §9 手动验收位置）永远不在允许范围里。"""
    try:
        rp = Path(path).resolve()
    except OSError:
        return False
    sandbox = _SANDBOX.resolve()
    if rp == sandbox or rp.is_relative_to(sandbox):
        return True
    tmp = Path(tempfile.gettempdir()).resolve()
    return (rp != tmp and rp.is_relative_to(tmp)
            and (rp.name.startswith("qa15_") or rp.name.startswith("qa_t15_")))


def copy_tree(src: Path, dst: Path) -> float:
    rp, sp = Path(dst).resolve(), Path(src).resolve()
    if not _safe_to_delete(rp):
        raise RuntimeError("拒绝复制到本次沙箱之外：%s" % dst)
    if rp == sp or rp.is_relative_to(sp) or sp.is_relative_to(rp):
        raise RuntimeError("源/目标重叠：%s -> %s" % (src, dst))
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    shutil.copytree(src, dst)
    return time.time() - t0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rmtree(path: Path):
    if not _safe_to_delete(path):
        return
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=False)
    except Exception:
        pass


def make_probe_xlsx(folder: Path, filename: str, sheet_name: str) -> Path:
    import openpyxl
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["记录日期", "对标账号", "视频发布时间", "视频标题", "话题标签",
               "视频链接/ID", "商品链接", "播放量", "点赞数", "评论数",
               "转发数", "收藏数", "音频", "翻译"])
    ws.append(["2026-11-05", "qa15_probe", "", "qa probe row", "", "", "", 0, 0, 0, 0, 0, "", ""])
    wb.save(target)
    return target


# ═══════════════════════════ 用例 1..12（离线/静态） ═══════════════════════════

@case(1, "slim 构建命令原样可跑（按裁决用 --out build\\portable\\qa，不覆盖 slim）")
def c01():
    if not WEBAPP_PY.is_file():
        fail("找不到 webapp 解释器 %s" % WEBAPP_PY)
    if BUILD_OUT.exists():
        shutil.rmtree(BUILD_OUT)          # 不碰 build\portable\slim
    cmd = [WEBAPP_PY, BUILD_PY, "--flavor", "slim",
           "--out", r"build\portable\qa",
           "--ffmpeg-dir", str(FFMPEG_DIR)]
    t0 = time.time()
    rc, out = run_cmd(cmd, cwd=REPO, timeout=7200)
    secs = time.time() - t0
    ev = ["命令：%s" % " ".join(str(c) for c in cmd),
          "exit=%s，耗时 %.1fs" % (rc, secs)]
    METRICS["build_seconds"] = round(secs, 1)
    if rc != 0:
        ev += tail(out, 15)
        fail("构建失败（exit=%s）" % rc, ev)
    if not PKG.is_dir():
        fail("构建 exit=0 但目录不存在：%s" % PKG, ev)
    ev.append("产物：%s（%s）" % (PKG, mb(dir_bytes(PKG))))
    return ev


@case(2, "DoD 1 必须存在的 18 个路径逐个存在")
def c02():
    if not PKG.is_dir():
        fail("包不存在（先看第 1 条）：%s" % PKG)
    missing = [p for p in REQUIRED_FILES if not (PKG / p).exists()]
    ev = ["%s: %s" % (p, "OK" if (PKG / p).exists() else "MISSING") for p in REQUIRED_FILES]
    if missing:
        fail("缺 %d 个：%s" % (len(missing), ", ".join(missing)), ev)
    return ev


@case(3, "licenses\\PYTHON-PACKAGES.txt ≥3 行 包名==版本")
def c03():
    f = PKG / "licenses" / "PYTHON-PACKAGES.txt"
    if not f.is_file():
        fail("文件不存在：%s" % f)
    text = f.read_text(encoding="utf-8", errors="replace")
    rows = [ln.strip() for ln in text.splitlines()
            if re.match(r"^\S+==\S+", ln.strip())]
    ev = ["文件：%s" % f, "匹配行数：%d" % len(rows), "前 10 行："]
    ev += ["  " + ln for ln in text.splitlines()[:10]]
    if len(rows) < 3:
        fail("只有 %d 行 包名==版本（要求 ≥3）" % len(rows), ev)
    return ev


@case(4, "包内依赖可 import（fastapi/uvicorn/openpyxl/ctranslate2/faster_whisper）")
def c04():
    py = PKG / "python" / "python.exe"
    if not py.is_file():
        fail("包内解释器不存在：%s" % py)
    rc, out = run_cmd([py, "-c", "import fastapi, uvicorn, openpyxl, ctranslate2, faster_whisper"],
                      timeout=300)
    ev = ["exit=%s" % rc] + tail(out, 6)
    if rc != 0:
        fail("导入失败", ev)
    return ev


@case(5, "PyTorch 不在（import torch 退出码非 0）")
def c05():
    py = PKG / "python" / "python.exe"
    if not py.is_file():
        fail("包内解释器不存在：%s" % py)
    rc, out = run_cmd([py, "-c", "import torch"], timeout=300)
    ev = ["exit=%s（期望非 0）" % rc] + tail(out, 4)
    if rc == 0:
        fail("包里能 import torch（ADR-0002 禁止）", ev)
    return ev


@case(6, "6 个 VC 运行库 DLL 都在 python\\ 下")
def c06():
    missing = [d for d in DLLS if not (PKG / "python" / d).is_file()]
    ev = ["%s: %s" % (d, "OK" if (PKG / "python" / d).is_file() else "MISSING") for d in DLLS]
    if missing:
        fail("缺：%s" % ", ".join(missing), ev)
    return ev


@case(7, "缺一个 DLL 时构建失败并点名（--vcruntime-dir 反例）")
def c07():
    tmp = Path(tempfile.mkdtemp(prefix="qa_t15_baddll_"))
    try:
        bad_vc = tmp / "vc"
        bad_vc.mkdir()
        missing = "msvcp140_2.dll"
        for name in DLLS:
            if name == missing:
                continue
            shutil.copy2(SYSTEM32 / name, bad_vc / name)
        fake_py = tmp / "pyhome"
        (fake_py / "Lib").mkdir(parents=True)
        for name in ("python.exe", "pythonw.exe", "python3.dll", "python313.dll"):
            (fake_py / name).write_bytes(b"stub")
        cmd = [WEBAPP_PY, BUILD_PY, "--flavor", "slim",
               "--out", str(tmp / "out"),
               "--python-home", str(fake_py),
               "--skip-deps",
               "--ffmpeg-dir", str(FFMPEG_DIR),
               "--vcruntime-dir", str(bad_vc)]
        rc, out = run_cmd(cmd, cwd=REPO, timeout=600)
        ev = ["exit=%s（期望 1）" % rc, "缺的是：%s" % missing] + tail(out, 6)
        if rc != 1:
            fail("构建应 exit=1，实得 %s" % rc, ev)
        if missing not in out:
            fail("失败消息没有点名缺少的 %s" % missing, ev)
        return ev
    finally:
        safe_rmtree(tmp)


@case(8, "包内没有 venv（无 pyvenv.cfg / 无 .venv）")
def c08():
    if not PKG.is_dir():
        fail("包不存在（先看第 1 条）：%s" % PKG)
    pyvenv = list(PKG.rglob("pyvenv.cfg"))
    venvs = [p for p in PKG.rglob(".venv") if p.is_dir()]
    ev = ["pyvenv.cfg 命中：%d" % len(pyvenv), ".venv 目录命中：%d" % len(venvs)]
    if pyvenv or venvs:
        fail("包里出现 venv 痕迹：%s" % (pyvenv + venvs)[:3], ev)
    return ev


@case(9, "体积护栏：build-info 两值 ≤ 上限且与实测偏差 <1%")
def c09():
    info = json.loads((PKG / "build-info.json").read_text(encoding="utf-8"))
    deps = info["deps"]
    site_recorded = deps["site_packages_bytes"]
    unpacked_recorded = deps["unpacked_bytes"]
    site_actual = dir_bytes(PKG / "python" / "Lib" / "site-packages")
    unpacked_actual = dir_bytes(PKG)
    ev = ["上限：site ≤ 419430400，unpacked ≤ 734003200",
          "site_packages_bytes：记录 %d / 实测 %d（%.3f%%）"
          % (site_recorded, site_actual, abs(site_actual - site_recorded) / max(1, site_actual) * 100),
          "unpacked_bytes：记录 %d / 实测 %d（%.3f%%）"
          % (unpacked_recorded, unpacked_actual, abs(unpacked_actual - unpacked_recorded) / max(1, unpacked_actual) * 100)]
    if site_recorded > 419430400 or unpacked_recorded > 734003200:
        fail("build-info 记录值超过上限", ev)
    if abs(site_actual - site_recorded) / max(1, site_actual) >= 0.01:
        fail("site_packages 记录与实测偏差 ≥1%", ev)
    if abs(unpacked_actual - unpacked_recorded) / max(1, unpacked_actual) >= 0.01:
        fail("unpacked 记录与实测偏差 ≥1%", ev)
    METRICS["site_bytes"] = site_actual
    METRICS["unpacked_bytes"] = unpacked_actual
    return ev


@case(10, "超限时构建退出 1 并打印实际值与上限（等价反例：把整包上限压到 1 字节）")
def c10():
    sys.path.insert(0, str(REPO / "packaging"))
    import build_portable as bp  # noqa: E402
    tmp = Path(tempfile.mkdtemp(prefix="qa_t15_overlimit_"))
    old = dict(bp.UNPACKED_LIMITS)
    try:
        fake_py = tmp / "pyhome"
        (fake_py / "Lib").mkdir(parents=True)
        for name in ("python.exe", "pythonw.exe", "python3.dll", "python313.dll"):
            (fake_py / name).write_bytes(b"stub")
        bp.UNPACKED_LIMITS = dict(old)
        bp.UNPACKED_LIMITS["slim"] = 1
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.main(["--flavor", "slim", "--out", str(tmp / "out"),
                          "--python-home", str(fake_py), "--skip-deps",
                          "--ffmpeg-dir", str(FFMPEG_DIR)])
        out = buf.getvalue()
        ev = ["exit=%s（期望 1）" % rc] + tail(out, 8)
        if rc != 1:
            fail("压小上限后构建应 exit=1，实得 %s" % rc, ev)
        if "超过上限" not in out:
            fail("失败消息没有『超过上限』：%s" % " / ".join(tail(out, 3)), ev)
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*MB", out)
        if len(nums) < 2:
            fail("失败消息没有同时给出实际值与上限（只看到 %s）" % nums, ev)
        ev.append("消息里的 MB 数字：%s（期望：实际值 + 上限）" % ", ".join(nums[-2:]))
        return ev
    finally:
        bp.UNPACKED_LIMITS = old
        safe_rmtree(tmp)


@case(11, "排除清单成立（DoD 6 的 15 类路径/模式 0 命中）")
def c11():
    checks = [
        ("webapp/.venv", PKG / "webapp" / ".venv"),
        ("webapp/tests", PKG / "webapp" / "tests"),
        ("webapp/data/runs.jsonl", PKG / "webapp" / "data" / "runs.jsonl"),
        ("webapp/package.json", PKG / "webapp" / "package.json"),
        ("webapp/install.bat", PKG / "webapp" / "install.bat"),
        ("webapp/start_workbench.*", next(PKG.glob("webapp/start_workbench.*"), None)),
        ("webapp/_serve*.bat", next(iter(PKG.glob("webapp/_serve*.bat")), None)),
        ("pipeline/.venv", PKG / "pipeline" / ".venv"),
        ("pipeline/tests", PKG / "pipeline" / "tests"),
        ("pipeline/accounts.json", PKG / "pipeline" / "accounts.json"),
        ("pipeline/cc_cookie.txt", PKG / "pipeline" / "cc_cookie.txt"),
        ("pipeline/ffmpeg_bin", PKG / "pipeline" / "ffmpeg_bin"),
        (".git", next(PKG.rglob(".git"), None)),
        ("qa\\", next(PKG.rglob("qa"), None)),
    ]
    xlsx = list(PKG.rglob("*.xlsx"))
    zips = list(PKG.rglob("*.zip"))
    checks += [("*.xlsx", xlsx[0] if xlsx else None), ("*.zip", zips[0] if zips else None)]
    bad = []
    ev = []
    for name, hit in checks:
        present = bool(hit) and Path(hit).exists()
        ev.append("%-30s %s" % (name, "FOUND -> %s" % hit if present else "absent"))
        if present:
            bad.append(name)
    if bad:
        fail("排除清单被破坏：%s" % ", ".join(bad), ev)
    return ev


@case(12, "数据目录形状（7 个目录存在、空、含 README-data.txt；ledger 无 xlsx）")
def c12():
    ev = []
    bad = []
    for rel in DATA_DIRS:
        d = PKG / rel
        if not d.is_dir():
            bad.append("%s 不存在" % rel)
            ev.append("%s: MISSING" % rel)
            continue
        files = [p for p in d.iterdir() if p.is_file()]
        names = sorted(p.name for p in files)
        readme = (d / "README-data.txt").is_file()
        ok = readme and names == ["README-data.txt"]
        ev.append("%-16s files=%s README=%s" % (rel, names, readme))
        if not ok:
            bad.append("%s 不是只含 README-data.txt" % rel)
    ledger_xlsx = list((PKG / "ledger").glob("*.xlsx"))
    ev.append("ledger\\*.xlsx 数量：%d（期望 0）" % len(ledger_xlsx))
    if ledger_xlsx:
        bad.append("ledger 里有 xlsx")
    if bad:
        fail("；".join(bad), ev)
    return ev


# ═══════════════════════════ 用例 13..21（真包/起停） ═══════════════════════════

@case(13, "DoD 8（CRD-0005）：首次冷启动 ≤8s、同副本再启 ≤3s（8789），health/diagnostics/where 全对")
def c13():
    if not PKG.is_dir():
        fail("包不存在（先看第 1 条）：%s" % PKG)
    ev = []
    if not ensure_8789_free(ev):
        raise CaseBlocked("8789 被非本进程占用；按纪律不杀别人，DoD 8 的 8789 判据无法验证", ev)
    if not wait_foreign_ports_clear(ev):
        raise CaseBlocked("候选端口上有别的副本的工作台在跑（不杀），无法保证落在 8789", ev)
    t_copy = copy_tree(PKG, COPY1)
    ev.append("复制 %s -> %s（%.1fs，%s）" % (PKG, COPY1, t_copy, mb(dir_bytes(COPY1))))
    launch = launch_package(COPY1, timeout=40)
    ev.append("首次冷启动（判据 ≤8s）：启动器 exit=%s，端口=%s，耗时 %.2fs"
              % (launch["rc"], launch["port"], launch["elapsed"] or -1))
    ev += tail(launch["out"], 6)
    # 即使不达标，也把实际端口记下来，让后面 14/16/27/28/29 仍能取证；
    # 但本条的 FAIL 在最后统一抛出。
    problems = []
    if launch["port"] != 8789:
        problems.append("首次启动没有落在 8789（实际 %s）" % launch["port"])
    if launch["elapsed"] is None or launch["elapsed"] > 8.0:
        problems.append("首次冷启动 %.2fs > 8s（CRD-0005）" % (launch["elapsed"] or -1))
    port = launch["port"]
    if port:
        METRICS["cold_start_s"] = round(launch["elapsed"] or -1, 2)
        METRICS["c1_port"] = port
        METRICS["c1_pid"] = launch["pid"]
    data = health(port) if port else None
    if not data:
        fail("启动器起不来，%s /api/health 不是 200" % port, ev)
    pipe = data.get("diagnostics", {}).get("pipeline_dir", "")
    try:
        pipe_ok = Path(pipe).resolve() == (COPY1 / "pipeline").resolve()
    except OSError:
        pipe_ok = False
    ev.append("diagnostics.pipeline_dir=%r（期望 %s，一致=%s）" % (pipe, COPY1 / "pipeline", pipe_ok))
    if not pipe_ok:
        problems.append("diagnostics.pipeline_dir 不指向本次复制目录")
    env = strip_env(Path(tempfile.mkdtemp(prefix="qa_t15_where_")))
    for exe in ("python", "ffmpeg"):
        wdir = Path(tempfile.mkdtemp(prefix="qa_t15_where_"))
        _OUR_TEMPS.append(wdir)
        rc, out = run_cmd([WHERE_EXE, exe], cwd=wdir, env=env)
        ev.append("where %s -> exit=%s（期望非 0）%s" % (exe, rc, out.strip()[:80]))
        if rc == 0:
            problems.append("剥环境会话里 where %s 仍然命中" % exe)
    ev.append("首次 health ok=True，api_version=%s" % data.get("api_version"))

    # CRD-0005 第二条：同一副本再次启动 ≤3s。停掉刚才那次，立刻用同一副本再启。
    stop_our_instances()
    if not ensure_8789_free(ev):
        raise CaseBlocked("首次实例停止后 8789 被别的进程占住；按纪律不杀别人，无法做『同副本再启』", ev)
    if not wait_foreign_ports_clear(ev):
        raise CaseBlocked("再启前候选端口上有别的工作台在跑（不杀），无法验证 ≤3s", ev)
    released = wait_port_free(8789, 15)
    second = launch_package(COPY1, timeout=40)
    ev.append("同副本再启（判据 ≤3s）：启动器 exit=%s，端口=%s，耗时 %.2fs"
              % (second["rc"], second["port"], second["elapsed"] or -1))
    ev += tail(second["out"], 4)
    if not released:
        problems.append("首次实例停止后 8789 未在 15s 内释放")
    if second["port"] != 8789:
        problems.append("同副本再启没有落在 8789（实际 %s）" % second["port"])
    if second["elapsed"] is None or second["elapsed"] > 3.0:
        problems.append("同副本再启 %.2fs > 3s（CRD-0005）" % (second["elapsed"] or -1))
    data2 = health(second["port"]) if second["port"] else None
    if not data2:
        problems.append("同副本再启后 /api/health 不是 200")
    else:
        ev.append("再启 health ok=True，api_version=%s" % data2.get("api_version"))
    if second["port"]:
        METRICS["steady_start_s"] = round(second["elapsed"] or -1, 2)
        METRICS["c1_port"] = second["port"]
        METRICS["c1_pid"] = second["pid"]
    if problems:
        fail("；".join(problems), ev)
    return ev


@case(14, "DoD 9：四个页面/接口 HTTP 可用（/、overview、accounts、benchmark、runs）")
def c14():
    port = METRICS.get("c1_port")
    if not port:
        fail("没有正在运行的实例（第 13 条未成功）")
    ev = []
    st, raw = http_get(port, "/", 15)
    ev.append("/ -> %s，HTML %d 字节" % (st, len(raw)))
    if st != 200 or len(raw) < 500:
        fail("/ 不是 200 或 HTML 太小", ev)
    results = {}
    for path, need in (("/api/overview", ("accounts",)),
                       ("/api/accounts", ("accounts",)),
                       ("/api/config", ("accounts_detail",)),
                       ("/api/benchmark", ("accounts",)),
                       ("/api/runs", ("runs",))):
        st, data, raw = http_json(port, path, 20)
        keys = list(data) if isinstance(data, dict) else type(data).__name__
        ev.append("%s -> %s，keys=%s" % (path, st, keys))
        if st != 200:
            fail("%s 不是 200" % path, ev)
        if isinstance(data, dict) and need:
            missing = [k for k in need if k not in data]
            if missing:
                ev.append("  note：响应缺字段 %s" % missing)
                if path in ("/api/config", "/api/runs"):
                    fail("%s 响应缺 %s" % (path, missing), ev)
        results[path] = data
    return ev


@case(15, "/ 的 HTML 含 id=\"appVersion\"（M3 T-25；按裁决 DEFERRED）")
def c15():
    port = METRICS.get("c1_port")
    if not port:
        raise CaseDeferred("DEFERRED-M3: id=\"appVersion\"（T-25）", ["第 13 条未成功，无法取 HTML"])
    st, raw = http_get(port, "/", 15)
    html = raw.decode("utf-8", "replace")
    if 'id="appVersion"' in html:
        return ["/ 的 HTML 已含 id=\"appVersion\"（T-25 已提前落地）"]
    raise CaseDeferred("DEFERRED-M3: id=\"appVersion\"（T-25）",
                       ["/ 的 HTML 里没搜到 id=\"appVersion\"（M1 阶段预期；由 M3 T-25 落地）",
                        "HTML 大小 %d 字节" % len(html)])


@case(16, "日志无 Traceback（logs\\_server_run.log）")
def c16():
    log = COPY1 / "logs" / "_server_run.log"
    if not log.is_file():
        fail("日志不存在：%s" % log)
    text = log.read_text(encoding="utf-8", errors="replace")
    hits = [ln for ln in text.splitlines() if "Traceback" in ln]
    ev = ["日志：%s（%d 字节）" % (log, log.stat().st_size),
          "Traceback 命中：%d" % len(hits)]
    if hits:
        ev += ["  " + h for h in hits[:5]]
        fail("日志里有 Traceback", ev)
    return ev


@case(17, "DoD 10：不写用户目录（三个定点路径前后完全一致）")
def c17():
    if not COPY1.is_dir():
        fail("复制目录不存在：%s" % COPY1)
    stop_our_instances()
    wait_port_free(8789, 15)
    ev = []
    if not wait_foreign_ports_clear(ev):
        raise CaseBlocked("候选端口上有别的工作台在跑（不杀），无法保证启动的是本次副本", ev)
    paths = [Path(os.environ.get("USERPROFILE", "")) / ".workbuddy",
             Path(os.environ.get("USERPROFILE", "")) / ".cache" / "huggingface",
             Path(os.environ.get("APPDATA", "")) / "tiktok-workbench"]
    before = {str(p): snapshot_tree(p) for p in paths}
    ev.append("操作前：")
    ev += ["  %s -> %s（%d 个文件）" % (p, s[0], len(s[1])) for p, s in before.items()]
    launch = launch_package(COPY1, timeout=40)
    ev.append("启动器 exit=%s，端口=%s，冷启动 %.2fs"
              % (launch["rc"], launch["port"], launch["elapsed"] or -1))
    if not launch["port"]:
        fail("未能启动实例", ev)
    port = launch["port"]
    for path in ("/", "/api/overview", "/api/accounts", "/api/benchmark", "/api/runs"):
        st, _ = http_get(port, path, 20)
        if st != 200:
            ev.append("%s -> %s（非 200，记录但不单独判红；第 14 条已判）" % (path, st))
    probe = "qa15_probe_%d" % int(time.time())
    st, raw = http_post_json(port, "/api/accounts", {"account": probe})
    ev.append("POST /api/accounts（%s）-> %s" % (probe, st))
    if st != 200:
        fail("探针账号 POST 失败", ev)
    after = {str(p): snapshot_tree(p) for p in paths}
    diffs = []
    for p in paths:
        key = str(p)
        if before[key] != after[key]:
            diffs.append("%s：%s -> %s" % (p, before[key][0], after[key][0]))
            if before[key][0] == "present" and after[key][0] == "present":
                added = sorted(set(x[0] for x in after[key][1]) - set(x[0] for x in before[key][1]))[:10]
                removed = sorted(set(x[0] for x in before[key][1]) - set(x[0] for x in after[key][1]))[:10]
                diffs += ["  新增 %s" % added, "  消失 %s" % removed]
    ev.append("操作后对比：%s" % ("一致" if not diffs else "有差异"))
    ev += diffs
    METRICS["user_dir_diff"] = len(diffs)
    stop_our_instances()
    wait_port_free(port, 15)
    if diffs:
        fail("三个用户目录有变化", ev)
    return ev


@case(18, "DoD 11：连续 10 次启动（每次抓启动器 stdout 的实际端口判 health）")
def c18():
    if not COPY1.is_dir():
        fail("复制目录不存在：%s" % COPY1)
    ev = []
    ports, times = [], []
    for i in range(1, 11):
        stop_our_instances()
        for p in PORTS:
            wait_port_free(p, 10)
        if not wait_foreign_ports_clear(ev):
            raise CaseBlocked("第 %d 次启动前，候选端口上有别的工作台在跑（不杀）；10 次连续性无法验证" % i, ev)
        launch = launch_package(COPY1, timeout=40)
        port = launch["port"]
        ok = False
        if port:
            data = health(port)
            ok = bool(data and data.get("ok") is True)
        ev.append("第 %2d 次：端口=%s，health=%s，启动 %.2fs，rc=%s"
                  % (i, port, "200" if ok else "FAIL", launch["elapsed"] or -1, launch["rc"]))
        if not ok:
            ev += tail(launch["out"], 6)
            stop_our_instances()
            fail("第 %d 次启动没有 health 200（端口=%s）" % (i, port), ev)
        ports.append(port)
        times.append(round(launch["elapsed"] or -1, 2))
    stop_our_instances()
    for p in PORTS:
        wait_port_free(p, 10)
    METRICS["ten_start_ports"] = ports
    METRICS["ten_start_seconds"] = times
    ev.append("10/10 通过；端口序列：%s" % ports)
    return ev


@case(19, "DoD 12：first_run.py 失败不阻断启动（替换→启动→恢复，sha256 一致）")
def c19():
    first = COPY1 / "first_run.py"
    if not first.is_file():
        fail("复制目录里没有 first_run.py：%s" % first)
    stop_our_instances()
    for p in PORTS:
        wait_port_free(p, 10)
    before = sha256(first)
    original = first.read_bytes()
    ev = ["替换前 sha256=%s" % before]
    if not wait_foreign_ports_clear(ev):
        first.write_bytes(original)
        stop_our_instances()
        raise CaseBlocked("候选端口上有别的工作台在跑（不杀），无法验证 first_run 失败不阻断", ev)
    try:
        first.write_bytes(b"import sys\nsys.exit(1)\n")
        launch = launch_package(COPY1, timeout=40)
        ok = bool(launch["port"]) and bool(health(launch["port"]))
        ev.append("first_run 桩 exit=1 后：启动器 rc=%s，端口=%s，health=%s，%.2fs"
                  % (launch["rc"], launch["port"], "200" if ok else "FAIL",
                     launch["elapsed"] or -1))
        if not ok:
            ev += tail(launch["out"], 6)
            fail("first_run 失败后工作台没起来", ev)
    finally:
        first.write_bytes(original)
        stop_our_instances()
        for p in PORTS:
            wait_port_free(p, 10)
    after = sha256(first)
    ev.append("恢复后 sha256=%s（一致=%s）" % (after, before == after))
    if before != after:
        fail("first_run.py 没恢复到原字节", ev)
    return ev


@case(20, "DoD 13：8789 被别的程序占住 → 自动换端口并打印实际地址")
def c20():
    if not COPY1.is_dir():
        fail("复制目录不存在：%s" % COPY1)
    ev = []
    stop_our_instances()
    if not ensure_8789_free(ev):
        raise CaseBlocked("8789 被非本进程占用，无法构造『8789 被占』场景；按纪律不杀别人", ev)
    if not wait_foreign_ports_clear(ev):
        raise CaseBlocked("候选端口上有别的工作台在跑（不杀），无法保证换端口结果可判", ev)
    wait_port_free(8789, 15)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", 8789))
        srv.listen(8)
    except OSError as e:
        fail("无法占住 8789：%s" % e)
    stop_flag = {"stop": False}

    def _acceptor():
        while not stop_flag["stop"]:
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                break
            except Exception:
                break

    threading.Thread(target=_acceptor, daemon=True).start()
    ev.append("占位监听：127.0.0.1:8789（python socket，不是工作台，health 不响应）")
    try:
        launch = launch_package(COPY1, timeout=40)
        port = launch["port"]
        ev.append("启动器 rc=%s，抓到的实际端口=%s，启动 %.2fs"
                  % (launch["rc"], port, launch["elapsed"] or -1))
        ev += tail(launch["out"], 6)
        if port not in (8791, 8792, 8793, 8787):
            fail("没有换到候选端口（实际 %s）" % port, ev)
        data = health(port)
        if not data:
            fail("换端口后 %s 的 health 不是 200" % port, ev)
        METRICS["switch_port"] = port
        ev.append("换端口后 %s health 200（DoD 13 通过）" % port)
        return ev
    finally:
        stop_flag["stop"] = True
        try:
            srv.close()
        except OSError:
            pass
        stop_our_instances()
        for p in PORTS:
            wait_port_free(p, 10)


@case(21, "DoD 14：换路径后仍可用（pipeline_dir 指向新路径、ledger_files 一致）")
def c21():
    if not COPY1.is_dir():
        fail("复制目录不存在：%s" % COPY1)
    stop_our_instances()
    for p in PORTS:
        wait_port_free(p, 10)
    ev = []
    if not wait_foreign_ports_clear(ev):
        raise CaseBlocked("候选端口上有别的工作台在跑（不杀），无法验证换路径后启动的是新副本", ev)
    t_copy = copy_tree(COPY1, COPY2)
    ev.append("复制 %s -> %s（%.1fs，%s）" % (COPY1, COPY2, t_copy, mb(dir_bytes(COPY2))))
    old_health = health(8789) if METRICS.get("c1_port") == 8789 else None
    old_files = (old_health or {}).get("diagnostics", {}).get("ledger_files")
    if old_files is None:
        old_files = METRICS.get("c1_ledger_files")
    launch = launch_package(COPY2, timeout=40)
    port = launch["port"]
    ev.append("新路径启动：端口=%s，启动 %.2fs" % (port, launch["elapsed"] or -1))
    if not port:
        ev += tail(launch["out"], 6)
        fail("新路径启动失败", ev)
    data = health(port)
    if not data:
        fail("新路径 health 不是 200", ev)
    pipe = data.get("diagnostics", {}).get("pipeline_dir", "")
    try:
        pipe_ok = Path(pipe).resolve() == (COPY2 / "pipeline").resolve()
    except OSError:
        pipe_ok = False
    new_files = data.get("diagnostics", {}).get("ledger_files")
    ev.append("diagnostics.pipeline_dir=%r（指向新路径=%s）" % (pipe, pipe_ok))
    ev.append("旧路径 ledger_files=%r" % (old_files,))
    ev.append("新路径 ledger_files=%r" % (new_files,))
    if not pipe_ok:
        fail("pipeline_dir 没有指向新路径", ev)
    if old_files is not None and new_files != old_files:
        fail("ledger_files 与旧路径不一致", ev)
    stop_our_instances()
    for p in PORTS:
        wait_port_free(p, 10)
    safe_rmtree(COPY2)
    METRICS["path_change_ok"] = True
    return ev


# ═══════════════════════════ 用例 22..29 ═══════════════════════════

@case(22, "build-info.json 干净、版本/ffmpeg 正确")
def c22():
    f = PKG / "build-info.json"
    raw = f.read_text(encoding="utf-8", errors="replace")
    info = json.loads(raw)
    version_expected = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    ev = []
    hits = re.findall(r"[A-Za-z]:\\|C:/Users|/home/", raw)
    ev.append("绝对路径命中：%d（期望 0）%s" % (len(hits), hits[:3]))
    if hits:
        fail("build-info.json 里出现构建机绝对路径", ev)
    ev.append("version=%r（期望 %r）" % (info.get("version"), version_expected))
    if info.get("version") != version_expected:
        fail("version 与 VERSION 不一致", ev)
    ff = info.get("ffmpeg", {})
    ev.append("ffmpeg.version_line=%r" % (ff.get("version_line", "")[:80],))
    if "9.0.2" not in str(ff.get("version_line", "")):
        fail("ffmpeg.version_line 不含 9.0.2", ev)
    dlls = list((PKG / "ffmpeg" / "bin").glob("*.dll"))
    expect = "shared" if dlls else "static"
    ev.append("ffmpeg.build=%r，ffmpeg\\bin 里 DLL 数=%d（期望 %r）"
              % (ff.get("build"), len(dlls), expect))
    if ff.get("build") != expect:
        fail("ffmpeg.build 与 ffmpeg\\bin 里有没有 DLL 不一致", ev)
    return ev


@case(23, "构建产物不进 git（git status 无 build/、dist/）")
def c23():
    rc, out = run_cmd(["git", "status", "--short"], cwd=REPO, timeout=60)
    if rc != 0:
        fail("git status 失败（exit=%s）：%s" % (rc, tail(out, 3)))
    bad = [ln for ln in out.splitlines()
           if re.search(r"(^|[\s/])(build|dist)/", ln)]
    ev = ["git status 行数：%d" % len(out.splitlines())]
    if bad:
        ev += bad[:10]
        fail("git status 里出现 build/ 或 dist/ 行", ev)
    return ev


@case(24, "build_portable.test.py 通过（含 full 缺 hf-cache / 空模型 / 排除清单等分支）")
def c24():
    f = REPO / "packaging" / "tests" / "build_portable.test.py"
    if not f.is_file():
        fail("单测不存在：%s" % f)
    # 该单测自己用 encoding="utf-8" 解 build_portable 的输出（见 run_raw/run_build）。
    # 本机默认控制台是 GBK，不显式对齐时新加的（安全 4）会因中文变 ? 而误判红；
    # 这里只给这条单测的子进程环境设 UTF-8，不改产品代码，也不改判据。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    rc, out = run_cmd([WEBAPP_PY, f], cwd=REPO, timeout=900, env=env)
    ev = ["%s -> exit=%s（子进程 PYTHONIOENCODING=utf-8，对齐单测自身的 UTF-8 解码）" % (f, rc)] + tail(out, 8)
    src = f.read_text(encoding="utf-8", errors="replace")
    for token in ("--hf-cache", "model.bin", "--skip-deps", "排除"):
        ev.append("单测源码含 %-12r：%s" % (token, token in src))
    if rc != 0:
        fail("build_portable.test.py 退出码 %s" % rc, ev)
    if "model.bin" not in src or "--hf-cache" not in src:
        fail("单测里没有 hf-cache/model.bin 分支（DoD 17）", ev)
    return ev


@case(25, "CI 覆盖 packaging；本机 compileall 退出 0")
def c25():
    yml = REPO / ".github" / "workflows" / "ci.yml"
    if not yml.is_file():
        fail("CI 文件不存在：%s" % yml)
    text = yml.read_text(encoding="utf-8", errors="replace")
    cand = [ln.strip() for ln in text.splitlines() if "compileall" in ln]
    # `- name: ...` 那行只是标题；真正的命令在下一行 `run: python -m compileall ...`
    line = next((ln for ln in cand if ln.lower().startswith("run:")),
                next((ln for ln in cand if "packaging" in ln), cand[-1] if cand else ""))
    ev = ["compileall 命中行：%s" % " | ".join(cand)]
    if "packaging" not in line:
        fail("compileall 命令没有覆盖 packaging（取到：%s）" % line, ev)
    rc, out = run_cmd([PIPELINE_PY, "-m", "compileall", "-q",
                       "pipeline", "webapp", "packaging", "first_run.py"],
                      cwd=REPO, timeout=600)
    ev.append("本机 compileall exit=%s" % rc)
    if rc != 0:
        ev += tail(out, 6)
        fail("本机 compileall 失败", ev)
    return ev


@case(26, "QA 入口：用 pipeline 解释器跑 qa\\T-15\\run.py，并打印实测指标")
def c26():
    actual = Path(sys.executable).resolve()
    expect = PIPELINE_PY.resolve()
    ev = ["sys.executable=%s" % actual, "期望=%s" % expect,
          "脚本=%s" % Path(__file__).resolve()]
    if actual != expect:
        fail("不是用清单规定的解释器在跑（%s）" % actual, ev)
    if Path(__file__).resolve() != (QA_DIR / "run.py").resolve():
        fail("入口不是 qa\\T-15\\run.py", ev)
    need = ("cold_start_s", "steady_start_s", "ten_start_ports", "switch_port", "user_dir_diff")
    missing = [k for k in need if k not in METRICS]
    ev.append("实测指标：%s" % json.dumps({k: METRICS.get(k) for k in need}, ensure_ascii=False))
    if missing:
        # 某条运行时用例 FAIL/BLOCKED 时它可能没产出指标；上游已经报红，这里不重复判红。
        ev.append("note：缺指标 %s（由上游用例报红/阻塞，不在此重复判红）" % missing)
    return ev


@case(27, "M1-1：包内真实台账被 health 看见")
def c27():
    port = METRICS.get("c1_port")
    if not port or not COPY1.is_dir():
        fail("没有正在运行的实例（第 13 条未成功）")
    name = "qa15_probe_%d.xlsx" % int(time.time())
    sheet = "对标记录-qa15"
    f = make_probe_xlsx(COPY1 / "ledger", name, sheet)
    ev = ["写入台账：%s（%.1f KB，sheet=%s）" % (f, f.stat().st_size / 1024, sheet)]
    data = health(port)
    if not data:
        fail("health 不是 200", ev)
    files = data.get("diagnostics", {}).get("ledger_files") or []
    ev.append("health diagnostics.ledger_files=%r" % (files,))
    if name not in files:
        fail("health 的 ledger_files 没有包含新表 %s" % name, ev)
    METRICS["c1_health"] = data
    METRICS["c1_ledger_files"] = files
    global _PROBE_SHEET_FILE, _PROBE_SHEET_NAME
    _PROBE_SHEET_FILE, _PROBE_SHEET_NAME = name, sheet
    return ev


@case(28, "M1-1：/api/ledger 能取到该表")
def c28():
    port = METRICS.get("c1_port")
    if not port:
        fail("没有正在运行的实例（第 13 条未成功）")
    st, data, raw = http_json(port, "/api/ledger", 30)
    ev = ["/api/ledger -> %s" % st]
    if st != 200 or not isinstance(data, dict):
        ev.append(raw[:200].decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
        fail("/api/ledger 不是 200/JSON", ev)
    files = data.get("files") or []
    sheets = data.get("sheets") or []
    ev.append("files=%r" % (files,))
    ev.append("sheets=%r" % ([(s.get("file"), s.get("name")) for s in sheets][:8],))
    if _PROBE_SHEET_FILE not in files:
        fail("files 里没有探针表 %s" % _PROBE_SHEET_FILE, ev)
    if not any(s.get("name") == _PROBE_SHEET_NAME and s.get("file") == _PROBE_SHEET_FILE
               for s in sheets):
        fail("sheets 里没有 %s / %s" % (_PROBE_SHEET_FILE, _PROBE_SHEET_NAME), ev)
    return ev


@case(29, "M1-(a)：Playwright 四个页面不白屏、无未捕获 pageerror")
def c29():
    port = METRICS.get("c1_port")
    if not port:
        fail("没有正在运行的实例（第 13 条未成功）")
    if not PLAYWRIGHT.is_dir():
        fail("找不到 playwright-core：%s" % PLAYWRIGHT)
    if not BROWSER.is_file():
        fail("找不到 Chromium：%s" % BROWSER)
    sheet_file = _PROBE_SHEET_FILE
    sheet_name = _PROBE_SHEET_NAME
    if not sheet_file:
        st, data, _ = http_json(port, "/api/ledger", 30)
        if not isinstance(data, dict) or not data.get("sheets"):
            fail("台账没有可打开的 sheet，无法验『台账页』", ["/api/ledger -> %s" % st])
        first = data["sheets"][0]
        sheet_file, sheet_name = first["file"], first["name"]
    base = "http://127.0.0.1:%d" % port
    script = Path(tempfile.mkdtemp(prefix="qa_t15_pw_")) / "probe.js"
    js = r"""
const { chromium } = require(process.argv[2]);
(async () => {
  const chrome = process.argv[3];
  const base = process.argv[4];
  const sheetHash = process.argv[5];
  const browser = await chromium.launch({ headless: true, executablePath: chrome });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  let errors = [];
  page.on('pageerror', (e) => errors.push(String(e && e.message ? e.message : e)));
  const out = [];
  async function grab(name) {
    const info = await page.evaluate((n) => {
      const el = document.querySelector('#view-' + n);
      const host = (n === 'sheet') ? document.querySelector('#sheet-host') : null;
      let txt = el ? (el.innerText || '') : '';
      // 对标分析页在 Shadow DOM 里（#bench-s096）；只收文本节点，排除 <style>/<script>，
      // 否则 CSS 源码会让「空页」也显得有内容。
      if (n === 'bench') {
        const h = document.querySelector('#bench-s096');
        if (h && h.shadowRoot) {
          let t = '';
          const walk = (node) => {
            for (const c of node.childNodes) {
              if (c.nodeType === 3) { t += c.nodeValue; }
              else if (c.nodeType === 1) {
                const tag = c.tagName.toLowerCase();
                if (tag === 'style' || tag === 'script' || tag === 'link') continue;
                walk(c);
              }
            }
          };
          walk(h.shadowRoot);
          txt += '\n' + t;
        }
      }
      return { len: txt.trim().length, head: txt.trim().slice(0, 80),
               active: !!(el && el.classList.contains('active')),
               hostLen: host ? ((host.innerText || '').trim().length) : 0 };
    }, name);
    out.push({ name, len: info.len, head: info.head, active: info.active,
               hostLen: info.hostLen, errors: errors.slice() });
    errors = [];
  }
  await page.goto(base + '/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(2000);
  await page.click('a[data-view="overview"]').catch(() => {});
  await page.waitForTimeout(1200);
  await grab('overview');
  await page.click('a[data-view="bench"]');
  await page.waitForTimeout(2500);
  await grab('bench');
  await page.click('a[data-view="run"]');
  await page.waitForTimeout(1800);
  await grab('run');
  await page.evaluate((h) => { location.hash = h; }, sheetHash);
  await page.waitForFunction(() => {
    const h = document.querySelector('#sheet-host');
    return h && (h.innerText || '').trim().length > 0;
  }, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await grab('sheet');
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.error(e && e.stack ? e.stack : String(e)); process.exit(1); });
"""
    script.write_text(js, encoding="utf-8", newline="\n")
    sheet_hash = ("#sheet?file=" + urllib.parse.quote(sheet_file)
                  + "&sheet=" + urllib.parse.quote(sheet_name))
    rc, out = run_cmd([NODE_EXE, script, str(PLAYWRIGHT), str(BROWSER), base, sheet_hash],
                      cwd=REPO, timeout=300)
    ev = ["node exit=%s" % rc] + tail(out, 6)
    if rc != 0:
        fail("Playwright 探针失败", ev)
    data = None
    for line in reversed([ln for ln in out.splitlines() if ln.strip()]):
        try:
            data = json.loads(line)
            break
        except Exception:
            continue
    if not isinstance(data, list):
        fail("Playwright 探针没有输出合法 JSON", ev)
    bad = []
    for item in data:
        ev.append("%-8s active=%s text=%3d host=%3d errors=%d head=%r"
                  % (item["name"], item["active"], item["len"], item["hostLen"],
                     len(item["errors"]), item["head"]))
        if not item["active"]:
            bad.append("%s 视图没有激活（导航没生效）" % item["name"])
        if item["len"] <= 0:
            bad.append("%s 主内容为空" % item["name"])
        if item["errors"]:
            bad.append("%s pageerror: %s" % (item["name"], item["errors"][:1]))
        if item["name"] == "sheet" and item["hostLen"] <= 0:
            bad.append("台账页 #sheet-host 为空（表没渲染）")
    if bad:
        ev += ["  " + b for b in bad]
        fail("；".join(bad), ev)
    METRICS["browser_pages"] = [i["name"] for i in data]
    return ev


# ═══════════════════════════════ main ═══════════════════════════════
ORDER = [c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12,
         c22, c23, c24, c25,
         c13, c14, c15, c16, c27, c28, c29, c17, c18, c19, c20, c21, c26]


def main() -> int:
    print("qa/T-15/run.py —— T-15 独立验收（M1-qa-plan T-15 1..29）")
    print("仓库：%s" % REPO)
    print("被测包：%s（按 Lead 裁决用 --out build\\portable\\qa 重建）" % PKG)
    print("")
    try:
        for fn in ORDER:
            fn()
    finally:
        stop_our_instances()
        for p in PORTS:
            wait_port_free(p, 5)
        for tmp in _OUR_TEMPS:
            safe_rmtree(tmp)
        safe_rmtree(COPY1)
        safe_rmtree(COPY2)
        safe_rmtree(_SANDBOX)
    total = len(RESULTS)
    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    deferred = [r for r in RESULTS if r[0] == "DEFERRED"]
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    blocked = [r for r in RESULTS if r[0] == "BLOCKED"]
    print("")
    print("实测指标：")
    for key in ("build_seconds", "cold_start_s", "steady_start_s", "ten_start_ports",
                "ten_start_seconds", "switch_port", "user_dir_diff",
                "site_bytes", "unpacked_bytes", "browser_pages"):
        if key in METRICS:
            print("  %-18s %s" % (key, METRICS[key]))
    print("")
    if deferred:
        for _, num, desc in deferred:
            print("DEFERRED-M3: id=\"appVersion\"（T-25）" if num == 15 else "DEFERRED %d. %s" % (num, desc))
    print("通过 %d / %d（FAIL %d，DEFERRED %d，BLOCKED %d）%s"
          % (passed, total, len(failed), len(deferred), len(blocked),
             ("；失败：%s" % ", ".join("%d.%s" % (n, d) for _, n, d in failed) if failed else "")
             + ("；阻塞：%s" % ", ".join("%d.%s" % (n, d) for _, n, d in blocked) if blocked else "")))
    if failed or blocked:
        print("T-15 有 FAIL 或 BLOCKED，退出码 1")
        return 1
    print("T-15 全部通过（1 条 DEFERRED-M3 不计入失败：id=\"appVersion\"（T-25））")
    return 0


if __name__ == "__main__":
    sys.exit(main())
