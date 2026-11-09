# -*- coding: utf-8 -*-
"""T-28 · 反推抽帧用包内 ffmpeg 的端到端验收（可重跑，不是一次性命令）。

前置：`build\\portable\\slim\\` 必须存在、且是**当前代码**构建的；否则退出码 2 并
打印构建命令（**不 SKIP 成 0**）。

流程：
  1. 用**包内** ffmpeg 造 6 秒 testsrc2 素材；
  2. 剥环境（PATH 只留 %SystemRoot%\\system32；WB_*/HF_*/PYTHON* 全清）启动包内服务
     （端口从 879x 里挑空的，绝不用 8789）；
  3. `POST /api/reverse/import?filename=fixture.mp4`（不带 Origin）→ 轮询 status 到
     `stage == "ready"` 且 `frames >= 1`，且 `<pkg>\\reverse\\<vid>\\frames\\f_001.jpg` 存在；
  4. 把 `<pkg>\\ffmpeg\\bin\\ffmpeg.exe` 改名 → **重启服务** → 再 POST：必须失败
     （POST 400 且 detail 含 ffmpeg，或 status failed 且 error 含 ffmpeg）；
     恢复文件名并重启 → 再成功一次；
  5. 结束时停掉自己起的进程、删 `<pkg>\\reverse\\<vid>\\`、恢复 ffmpeg 文件名、删临时目录。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe packaging\\tests\\reverse_ffmpeg_package.test.py`
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
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 默认就是 DoD 指定的构建产物；T28_PKG_DIR 只用于演示"缺包 → 退出码 2"（不 SKIP）。
PKG = Path(os.environ.get("T28_PKG_DIR") or (REPO / "build" / "portable" / "slim"))
PY = PKG / "python" / "python.exe"
BOOT = PKG / "webapp" / "_boot.py"
FFMPEG = PKG / "ffmpeg" / "bin" / "ffmpeg.exe"
FFMPEG_BAK = PKG / "ffmpeg" / "bin" / "ffmpeg.exe.bak"
BUILD_INFO = PKG / "build-info.json"
REVERSE = PKG / "reverse"
SYSTEM_ROOT = Path(os.environ.get("SystemRoot") or r"C:\Windows")
TASKKILL = SYSTEM_ROOT / "System32" / "taskkill.exe"
BUILD_CMD = (
    'webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py '
    '--flavor slim --ffmpeg-dir "D:\\ffmpeg\\build\\ffmpeg-9.0.2-essentials_build\\bin"'
)
FRESH_FILES = (
    ("webapp/app.py", "webapp/app.py"),
    ("webapp/tk_reverse.py", "webapp/tk_reverse.py"),
    ("webapp/_boot.py", "webapp/_boot.py"),
    ("webapp/_launch.py", "webapp/_launch.py"),
    ("_setenv.bat", "packaging/templates/_setenv.bat"),
    ("start.bat", "packaging/templates/start.bat"),
    ("启动工作台.bat", "packaging/templates/启动工作台.bat"),
)
READY_TIMEOUT = 180.0
START_TIMEOUT = 40.0
# 本用例会临时改名包内 ffmpeg.exe，两个实例同时跑必然互相踩；用锁串行化
# （QA 的 qa\T-28\run.py 也会调用它）。
LOCK = Path(tempfile.gettempdir()) / (
    "t28_reverse_%s.lock" % hashlib.sha1(str(PKG).lower().encode("utf-8")).hexdigest()[:12])
LOCK_TIMEOUT = 300.0


def say(text: str) -> None:
    print(text, flush=True)


def fail(text: str) -> int:
    say("[FAIL] " + text)
    return 1


# ────────────────────────── 前置检查 ──────────────────────────
def acquire_lock() -> bool:
    deadline = time.time() + LOCK_TIMEOUT
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime > 3600:
                    LOCK.unlink(missing_ok=True)      # 崩溃留下的旧锁
                    continue
            except OSError:
                pass
            time.sleep(2.0)
    return False


def release_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def heal_ffmpeg() -> None:
    """上一次崩溃可能把 ffmpeg.exe 留在 .bak：先自愈，否则连成功路径都跑不了。"""
    if FFMPEG_BAK.exists() and not FFMPEG.exists():
        os.replace(str(FFMPEG_BAK), str(FFMPEG))
        say("[T-28] 自愈：把残留的 ffmpeg.exe.bak 恢复为 ffmpeg.exe")


def rename_ffmpeg_away(tries: int = 6) -> None:
    """把包内 ffmpeg.exe 改名；容忍另一个（旧版、没锁的）实例正在恢复它。"""
    last = ""
    for _ in range(tries):
        if FFMPEG_BAK.exists() and not FFMPEG.exists():
            return
        if FFMPEG.exists() and not FFMPEG_BAK.exists():
            try:
                os.replace(str(FFMPEG), str(FFMPEG_BAK))
                return
            except OSError as exc:                                  # noqa: BLE001
                last = repr(exc)
        else:
            last = "exe=%s bak=%s" % (FFMPEG.exists(), FFMPEG_BAK.exists())
        time.sleep(2.0)
    raise RuntimeError("包内 ffmpeg.exe 改名失败（疑似有并发实例在恢复它）：" + last)


def restore_ffmpeg(strict: bool) -> None:
    """把 ffmpeg.exe 从 .bak 恢复；若并发实例已经恢复过，就不再报错。"""
    if FFMPEG_BAK.exists():
        os.replace(str(FFMPEG_BAK), str(FFMPEG))
        return
    if FFMPEG.exists():
        say("[T-28] 注意：ffmpeg.exe 已被并发实例恢复（.bak 不在了），继续")
        return
    if strict:
        raise RuntimeError("包内 ffmpeg.exe 与 ffmpeg.exe.bak 都不见了")


def check_preconditions() -> bool:
    missing = [str(p) for p in (PKG, PY, BOOT, FFMPEG, BUILD_INFO, PKG / "webapp" / "app.py")
               if not p.exists()]
    if missing:
        say("[FAIL] 缺少构建产物（不许 SKIP）：")
        for item in missing:
            say("  " + item)
        say("先跑：\n  " + BUILD_CMD)
        return False
    stale = []
    for packaged_rel, source_rel in FRESH_FILES:
        source = REPO / source_rel
        packaged = PKG / packaged_rel
        if not packaged.is_file():
            stale.append(packaged_rel + "（包内缺失）")
        elif not source.is_file() or source.read_bytes() != packaged.read_bytes():
            stale.append(packaged_rel + " vs " + source_rel)
    if stale:
        say("[FAIL] 构建产物不是当前代码（与仓库不一致）：" + ", ".join(stale))
        say("先跑：\n  " + BUILD_CMD)
        return False
    return True


# ────────────────────────── 环境剥离 / 起停服务 ──────────────────────────
def stripped_base(tmpdir: Path) -> dict:
    """剥环境：按 HLD §9 —— 清 PATH / WB_* / HF_* / PYTHON*，PATH 只留 System32；
    其余宿主变量（USERPROFILE 等）按真实双击场景保留（config.py 会 Path.home()）。"""
    base = {key: value for key, value in os.environ.items()
            if not key.upper().startswith(("WB_", "HF_", "PYTHON"))
            and key.lower() != "nodefaultcurrentdirectoryinexepath"}
    base["PATH"] = str(SYSTEM_ROOT / "System32")
    base["TEMP"] = str(tmpdir)
    base["TMP"] = str(tmpdir)
    return base


def package_env(tmpdir: Path) -> dict:
    """在剥环境里 `call _setenv.bat && set`，把整份环境读回来给包内服务用。"""
    dump = tmpdir / "_env_dump.cmd"
    dump.write_text(
        "@echo off\r\n"
        'cd /d "%s"\r\n'
        'call "%s"\r\n'
        "set\r\n" % (PKG, PKG / "_setenv.bat"),
        encoding="ascii", newline="")
    proc = subprocess.run(["cmd", "/c", str(dump)], cwd=str(tmpdir),
                          env=stripped_base(tmpdir), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("读取包环境失败（_setenv.bat）：%r %r" % (proc.stdout, proc.stderr))
    env = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
            env[name] = value
    if env.get("WB_PIPELINE_DIR") != str(PKG / "pipeline"):
        raise RuntimeError("_setenv.bat 没把 WB_PIPELINE_DIR 指到包内：%r" % (env.get("WB_PIPELINE_DIR"),))
    if str(PKG / "ffmpeg" / "bin").lower() not in (env.get("PATH") or "").lower():
        raise RuntimeError("_setenv.bat 的 PATH 里没有包内 ffmpeg\\bin：%r" % (env.get("PATH"),))
    env["TEMP"] = str(tmpdir)
    env["TMP"] = str(tmpdir)
    return env


def pick_port() -> int:
    for port in range(8794, 8804):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8794-8803 都被占用，找不到空闲端口")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def http_json(url: str, method: str = "GET", data: bytes | None = None, timeout: float = 8.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Content-Type": "video/mp4"} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with opener.open(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except ValueError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def wait_health(port: int, timeout: float = START_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, body = http_json("http://127.0.0.1:%d/api/health" % port, timeout=3.0)
        except Exception as exc:                                    # noqa: BLE001
            last = repr(exc)
            time.sleep(0.3)
            continue
        if code == 200 and isinstance(body, dict) and body.get("ok") is True:
            return body
        last = "%s %r" % (code, body)
        time.sleep(0.3)
    raise RuntimeError("服务在 %.0fs 内没就绪：%r" % (timeout, last))


def start_service(port: int, env: dict) -> subprocess.Popen:
    proc = subprocess.Popen(
        [str(PY), str(BOOT), "--port", str(port)],
        cwd=str(PKG / "webapp"),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc


def stop_service(proc, port: int) -> None:
    if proc is not None and proc.poll() is None:
        subprocess.run([str(TASKKILL), "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=30)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.time() + 20
    while time.time() < deadline and not port_free(port):
        time.sleep(0.2)


# ────────────────────────── 反推流程 ──────────────────────────
def _vid_of(body):
    return str((body or {}).get("video_id") or (body or {}).get("id") or "")


def post_import(port: int, fixture: Path):
    with fixture.open("rb") as fh:
        data = fh.read()
    return http_json(
        "http://127.0.0.1:%d/api/reverse/import?filename=fixture.mp4" % port,
        method="POST", data=data, timeout=120.0)


def wait_status(port: int, vid: str, timeout: float = READY_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, body = http_json("http://127.0.0.1:%d/api/reverse/status/%s" % (port, vid))
        if code == 200 and isinstance(body, dict):
            last = body
            stage = str(body.get("stage") or "")
            frames = int(body.get("frames") or 0)
            if stage == "ready" and frames >= 1:
                return body
            if stage in ("failed", "stopped"):
                return body
        time.sleep(0.3)
    return last


def run_success(port: int, fixture: Path, label: str, created: list) -> dict:
    """POST + 轮询到 ready，断言 frames≥1 与 f_001.jpg；返回证据 dict。"""
    started = time.time()
    code, body = post_import(port, fixture)
    vid = _vid_of(body)
    if code != 200 or not vid:
        raise AssertionError("%s：POST 失败 HTTP %s：%r" % (label, code, body))
    created.append(vid)                     # 一拿到 vid 就登记，后面断言失败也能被清理
    status = wait_status(port, vid)
    elapsed = time.time() - started
    stage = str(status.get("stage") or "")
    frames = int(status.get("frames") or 0)
    error = str(status.get("error") or "")
    frame = REVERSE / vid / "frames" / "f_001.jpg"
    if stage != "ready" or frames < 1:
        raise AssertionError("%s：终态不是 ready/frames≥1（stage=%r frames=%r error=%r）"
                             % (label, stage, frames, error))
    if error:
        raise AssertionError("%s：ready 但 error 非空：%r" % (label, error))
    if not frame.is_file():
        raise AssertionError("%s：缺少 %s" % (label, frame))
    say("[T-28] %s：vid=%s frames=%d stage=%s elapsed=%.2fs f_001=%s"
        % (label, vid, frames, stage, elapsed, frame.is_file()))
    return {"vid": vid, "frames": frames, "elapsed": elapsed, "stage": stage}


def run_expected_failure(port: int, fixture: Path, created: list) -> dict:
    """改名后的包再导入：POST 400 且 detail 含 ffmpeg，或 status failed 且 error 含 ffmpeg。"""
    started = time.time()
    code, body = post_import(port, fixture)
    if code == 400:
        detail = body.get("detail") if isinstance(body, dict) else body
        text = str(detail)
        if "ffmpeg" not in text.lower():
            raise AssertionError("POST 400 但 detail 不含 ffmpeg：%r" % (text,))
        say("[T-28] 改名后：POST HTTP 400 detail=%r elapsed=%.2fs" % (text, time.time() - started))
        return {"mode": "post400", "detail": text, "elapsed": time.time() - started}
    if code != 200 or not _vid_of(body):
        raise AssertionError("改名后 POST 既不是 400 也不是 200：HTTP %s %r" % (code, body))
    vid = _vid_of(body)
    created.append(vid)
    status = wait_status(port, vid)
    elapsed = time.time() - started
    stage = str(status.get("stage") or "")
    error = str(status.get("error") or "")
    if stage != "failed" or "ffmpeg" not in error.lower():
        raise AssertionError("改名后应失败：stage=%r error=%r" % (stage, error))
    say("[T-28] 改名后：vid=%s stage=failed error=%r elapsed=%.2fs" % (vid, error, elapsed))
    return {"mode": "status", "vid": vid, "error": error, "elapsed": elapsed}


# ────────────────────────── 主流程 ──────────────────────────
def main() -> int:
    if not acquire_lock():
        say("[FAIL] 另一个 T-28 验收正在跑（锁 %s），本次不并发运行。" % LOCK)
        return 2
    tmpdir = None
    port = 8794
    proc = None
    created_vids = []
    try:
        heal_ffmpeg()
        if not check_preconditions():
            return 2
        say("[T-28] 包：%s" % PKG)
        tmpdir = Path(tempfile.mkdtemp(prefix="t28_reverse_"))
        port = pick_port()
        say("[T-28] 端口：%d（避开 8789）" % port)
        fixture = tmpdir / "fixture.mp4"
        # 1) 用包内 ffmpeg 造素材
        made = subprocess.run(
            [str(FFMPEG), "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
             "-t", "6", "-pix_fmt", "yuv420p", str(fixture)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if made.returncode != 0 or not fixture.is_file() or fixture.stat().st_size <= 0:
            raise AssertionError("包内 ffmpeg 造素材失败：rc=%s stderr=%r"
                                 % (made.returncode, made.stderr[-400:]))
        say("[T-28] 素材：%s（%d bytes，6s，包内 ffmpeg 生成）" % (fixture, fixture.stat().st_size))

        env = package_env(tmpdir)
        if "ffmpeg" not in env.get("PATH", "").lower():
            raise AssertionError("PATH 里没有包内 ffmpeg：%r" % (env.get("PATH"),))

        # 2) 剥环境起包 + 第一次成功
        proc = start_service(port, env)
        health = wait_health(port)
        pkg_pipeline = (health.get("diagnostics") or {}).get("pipeline_dir") or ""
        if str(PKG / "pipeline").lower() not in str(pkg_pipeline).lower():
            raise AssertionError("服务不在包内运行：diagnostics.pipeline_dir=%r" % (pkg_pipeline,))
        say("[T-28] 服务就绪：port=%d pipeline_dir=%r" % (port, pkg_pipeline))
        first = run_success(port, fixture, "包内 ffmpeg 成功", created_vids)

        # 3) 改名 + 重启 → 必须失败
        stop_service(proc, port)
        proc = None
        rename_ffmpeg_away()
        say("[T-28] 已把包内 ffmpeg.exe 改名为 ffmpeg.exe.bak，重启服务……")
        proc = start_service(port, env)
        wait_health(port)
        failure = run_expected_failure(port, fixture, created_vids)

        # 4) 恢复 + 重启 → 再成功一次
        stop_service(proc, port)
        proc = None
        restore_ffmpeg(strict=True)
        say("[T-28] 已恢复包内 ffmpeg.exe，重启服务……")
        proc = start_service(port, env)
        wait_health(port)
        second = run_success(port, fixture, "恢复后再次成功", created_vids)

        say("[T-28] PASS：包内 ffmpeg 成功两次；改名后按预期失败（%s）" % failure.get("mode"))
        return 0
    except Exception as exc:                                        # noqa: BLE001
        say("[FAIL] %s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        stop_service(proc, port)
        # 无论从哪一步失败，都不许把包里的 ffmpeg.exe 留在 .bak（哪怕是被别的进程改名）
        if FFMPEG_BAK.exists():
            restore_ffmpeg(strict=False)
            say("[T-28] 清理：已恢复包内 ffmpeg.exe")
        for vid in created_vids:
            target = REVERSE / str(vid)
            shutil.rmtree(target, ignore_errors=True)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
        say("[T-28] 清理：进程已停、端口 %d 空闲=%s、删除 %d 个任务目录"
            % (port, port_free(port), len(created_vids)))
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
