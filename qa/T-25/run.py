# -*- coding: utf-8 -*-
"""qa/T-25/run.py —— T-25「页面能看到版本号」（M3 DoD (e)）的独立验收用例。

跑法（仓库根）：
    pipeline\\.venv\\Scripts\\python.exe qa\\T-25\\run.py

退出码：0 = 机器可判的用例全通过；1 = 有用例不通过；2 = 验收包不存在（不算通过，禁止 SKIP 成 0）。

被测对象：`webapp\\app.py` 的 `/api/health` + `webapp\\static\\index.html` 的 `appVersion`，
以及 `build\\portable\\slim` 上真实跑起来的那一份。视觉位置（右上角）只能人工，单列打印
`DEFERRED-HUMAN`，不计入通过/失败。

六条机器用例（M3 QA 清单 T-25 的 1–4、6 条，加上独立复核）：
    C-01 webapp\\tests\\app_version.test.py 退出 0（复用单测）
    C-02 独立复核：/api/health 的 version == 仓库根 VERSION（子进程 import app，不复制单测实现）
    C-03 独立复核：VERSION 缺失 → "0.0.0"；VERSION=9.9.9 → "9.9.9"（打桩 config.WEBAPP_DIR，不动仓库文件）
    C-04 既有字段不变 + launch_version.test.py / acceptance_api.test.py 仍退出 0
    C-05 包上：先重跑构建 → 879x 起 `_boot.py --port` → version == 包根 VERSION == 仓库 VERSION
    C-06 index.html 有 appVersion，且页面脚本请求 /api/health 把 version 填进去
    C-07 QA 入口：能被 run-all 发现，并能在仓库根用文档指定解释器跑起来
   （另有 DEFERRED-HUMAN：页面右上角视觉位置，只能人工）

纪律：剥环境起包；只用空闲 879x 端口；只杀自己起的 pid（按可执行文件归属）；
8789 上用户的工作台绝不动；结束时删自己的临时目录。
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
import traceback
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[2]
PKG = Path(os.environ.get("QA25_PKG") or (REPO / "build" / "portable" / "slim"))
PY = PKG / "python" / "python.exe"
SETENV = PKG / "_setenv.bat"
BOOT = PKG / "webapp" / "_boot.py"
WEBAPP_VENV_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
VERSION_FILE = REPO / "VERSION"

FIXED_8789 = 8789
PORT_CANDIDATES = (8796, 8797, 8798, 8799, 8795)  # 只用 879x

BUILD_CMD = (
    "webapp\\.venv\\Scripts\\python.exe packaging\\build_portable.py "
    "--flavor slim "
    '--ffmpeg-dir "D:\\ffmpeg\\build\\ffmpeg-9.0.2-essentials_build\\bin"'
)
# 包必须是「当前代码」构建的：这几份逐字节比对（T-25 的落点都在前两项里）。
FRESH_FILES = (
    ("webapp/app.py", "webapp/app.py"),
    ("webapp/static/index.html", "webapp/static/index.html"),
    ("webapp/static/app.js", "webapp/static/app.js"),
    ("VERSION", "VERSION"),
    ("packaging/templates/_setenv.bat", "_setenv.bat"),
)

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CASES = []
_STATE = {"tmp": None, "port": None}


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
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd, cwd=None, env=None, timeout=300):
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=env, timeout=timeout)
        code, raw = proc.returncode, (proc.stdout or b"")
    except subprocess.TimeoutExpired as exc:
        code, raw = 124, (exc.stdout or b"")
    except OSError as exc:
        return 127, "无法执行 %r：%s" % (cmd, exc), time.perf_counter() - t0
    return code, raw.decode("utf-8", "replace"), time.perf_counter() - t0


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
    """独立取监听 pid（Win32 API；不抄产品的 netstat 文本解析）。"""
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


def _probe_script() -> str:
    return (
        "import json, sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import config, app\n"
        "out = {}\n"
        "h = app.health()\n"
        "out['health'] = {k: h.get(k) for k in ('ok','ts','api_version','version','features','diagnostics')}\n"
        "out['keys'] = sorted(h.keys())\n"
        "config.WEBAPP_DIR = Path(sys.argv[2])\n"
        "out['missing'] = app.health().get('version')\n"
        "config.WEBAPP_DIR = Path(sys.argv[3])\n"
        "out['stubbed'] = app.health().get('version')\n"
        "print('QA25JSON:' + json.dumps(out, ensure_ascii=False, default=str))\n"
    )


_PROBE = {"done": False, "data": None}


def health_probe() -> dict:
    """子进程里 import app 取 health（正常 / VERSION 缺失 / VERSION=9.9.9 三档）。"""
    if _PROBE["done"]:
        return _PROBE["data"]
    tmp = Path(_STATE["tmp"])
    none_root = tmp / "no-version"
    stub_root = tmp / "stub-version"
    (none_root / "webapp").mkdir(parents=True, exist_ok=True)
    (stub_root / "webapp").mkdir(parents=True, exist_ok=True)
    (stub_root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    code, out, secs = _run(
        [str(WEBAPP_VENV_PY), "-c", _probe_script(),
         str(REPO / "webapp"), str(none_root / "webapp"), str(stub_root / "webapp")],
        cwd=REPO, env=env, timeout=300)
    line = next((ln for ln in out.splitlines() if ln.startswith("QA25JSON:")), "")
    assert code == 0, "健康探针子进程退出码 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-10:]))
    assert line, "探针没有打印 QA25JSON：%s" % "\n".join(out.splitlines()[-10:])
    _PROBE["data"] = json.loads(line[len("QA25JSON:"):])
    _PROBE["done"] = True
    return _PROBE["data"]


def cleanup_service(why: str) -> list:
    ev = []
    port = _STATE.get("port")
    if not port:
        return ev
    pid = listener_pid(port)
    if not pid:
        return ev
    exe = pid_exe(pid)
    if exe and str(PKG).lower() not in exe.lower():
        ev.append("%s：pid %d 的可执行文件是 %s，不在包里 → 不杀" % (why, pid, exe))
        return ev
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
    deadline = time.time() + 10
    while time.time() < deadline and listener_pid(port):
        time.sleep(0.2)
    ev.append("%s：已结束 pid %d（端口 %d %s）"
              % (why, pid, port, "已释放" if not listener_pid(port) else "仍有监听"))
    return ev


def stale_files() -> list:
    bad = []
    for repo_rel, pkg_rel in FRESH_FILES:
        src, dst = REPO / repo_rel, PKG / pkg_rel
        if not src.is_file():
            bad.append("%s：仓库里没有" % rel(src))
        elif not dst.is_file():
            bad.append("%s：包里没有" % rel(dst))
        elif sha(src) != sha(dst):
            bad.append("%s：与 %s 不一致" % (rel(dst), rel(src)))
    return bad


# ────────────────────────────── 用例 ──────────────────────────────
@case("C-01", "webapp\\tests\\app_version.test.py 退出 0（复用单测）")
def c01():
    test = REPO / "webapp" / "tests" / "app_version.test.py"
    assert WEBAPP_VENV_PY.is_file(), "缺解释器 %s" % rel(WEBAPP_VENV_PY)
    assert test.is_file(), "缺单测 %s" % rel(test)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    code, out, secs = _run([str(WEBAPP_VENV_PY), str(test)], cwd=REPO, env=env, timeout=300)
    tail = [ln.strip() for ln in out.splitlines() if ln.strip()][-1:] or ["(无输出)"]
    assert code == 0, "app_version.test.py 退出码 %d（%.1fs），末行：%s" % (code, secs, tail[0])
    assert "Traceback" not in out, "单测输出里有 Traceback"
    return ["退出码 0（%.1fs）" % secs, "末行：%s" % tail[0]]


@case("C-02", "独立复核：/api/health 的 version == 仓库根 VERSION")
def c02():
    data = health_probe()
    expected = VERSION_FILE.read_text(encoding="utf-8").strip()
    got = data["health"].get("version")
    assert got == expected, "health.version=%r 与 VERSION=%r 不一致" % (got, expected)
    return ["VERSION（仓库根）：%r" % expected, "health.version：%r（相等）" % got]


@case("C-03", "独立复核：VERSION 缺失 → 0.0.0；VERSION=9.9.9 → 跟着变")
def c03():
    data = health_probe()
    assert data.get("missing") == "0.0.0", \
        "VERSION 缺失时 version=%r，应为 \"0.0.0\"" % (data.get("missing"),)
    assert data.get("stubbed") == "9.9.9", \
        "打桩 VERSION=9.9.9 时 version=%r，没跟着变" % (data.get("stubbed"),)
    return ["VERSION 不存在：version=%r" % data["missing"],
            "VERSION=9.9.9：version=%r" % data["stubbed"]]


@case("C-04", "既有字段不变 + launch_version / acceptance_api 仍退出 0")
def c04():
    data = health_probe()
    keys = set(data.get("keys") or [])
    need = ("ok", "ts", "api_version", "features", "diagnostics")
    missing = [k for k in need if k not in keys]
    assert not missing, "health 缺字段：%s（实际键：%s）" % (missing, sorted(keys))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    results = []
    for name in ("launch_version.test.py", "acceptance_api.test.py"):
        test = REPO / "webapp" / "tests" / name
        assert test.is_file(), "缺用例 %s" % rel(test)
        code, out, secs = _run([str(WEBAPP_VENV_PY), str(test)], cwd=REPO, env=env, timeout=300)
        assert code == 0, "%s 退出码 %d（%.1fs）：%s" % (
            name, code, secs, "\n".join(out.splitlines()[-8:]))
        results.append("%s → 退出码 0（%.1fs）" % (name, secs))
    return ["五个字段都在：%s" % ", ".join(need),
            "api_version=%r，features 有 %d 项"
            % (data["health"].get("api_version"), len(data["health"].get("features") or []))] + results


@case("C-05", "包上：_boot.py --port 起服务 → version == 包根 VERSION == 仓库 VERSION")
def c05():
    assert SETENV.is_file(), "包里没有 %s" % rel(SETENV)
    assert BOOT.is_file(), "包里没有 %s" % rel(BOOT)
    assert PY.is_file(), "包里没有 %s" % rel(PY)
    stale = stale_files()
    assert not stale, "包不是用当前代码构建的：%s；先重建：%s" % ("；".join(stale), BUILD_CMD)
    pkg_version = (PKG / "VERSION").read_text(encoding="utf-8").strip()
    repo_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert pkg_version == repo_version, "包根 VERSION=%r 与仓库 VERSION=%r 不一致（旧包？）" % (
        pkg_version, repo_version)
    port = pick_port()
    assert port is not None, "879x 候选端口全被占用（%s）" % (PORT_CANDIDATES,)
    _STATE["port"] = port
    env = stripped_env(Path(_STATE["tmp"]))
    log = Path(_STATE["tmp"]) / "pkg-boot.log"
    cmd = ["cmd", "/d", "/c", "call", str(SETENV), "&&", str(PY), str(BOOT),
           "--port", str(port)]
    with open(log, "wb") as fh:
        proc = subprocess.Popen(cmd, cwd=str(PKG), env=env, stdin=subprocess.DEVNULL,
                                stdout=fh, stderr=subprocess.STDOUT)
    ev = []
    try:
        data = wait_health(port, 40.0)
        assert data is not None, "包起在 %d 上后 health 拿不到；日志尾部：\n%s" % (
            port, "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]))
        got = data.get("version")
        assert got == pkg_version, "包上 health.version=%r 与包根 VERSION=%r 不一致" % (got, pkg_version)
        pid = listener_pid(port)
        assert pid, "端口 %d 上没有监听进程" % port
        diag = data.get("diagnostics") or {}
        pipe = str(diag.get("pipeline_dir") or "")
        assert str(PKG).lower() in pipe.lower(), "diagnostics.pipeline_dir=%r 不指向这个包" % pipe
        ev = ["包：%s（%d 个文件与当前代码逐字节一致）" % (rel(PKG), len(FRESH_FILES)),
              "剥环境起包：port=%d pid=%d，api_version=%r"
              % (port, pid, data.get("api_version")),
              "包根 VERSION=%r = 仓库 VERSION=%r = health.version=%r"
              % (pkg_version, repo_version, got)]
    finally:
        ev += cleanup_service("清理")
        if proc.poll() is None:
            proc.terminate()
    return ev


@case("C-06", "index.html 有 id=\"appVersion\"，且页面脚本请求 /api/health 把 version 填进去")
def c06():
    html_path = REPO / "webapp" / "static" / "index.html"
    assert html_path.is_file(), "缺 %s" % rel(html_path)
    html = html_path.read_text(encoding="utf-8", errors="replace")
    assert 'id="appVersion"' in html, "index.html 里没有 id=\"appVersion\" 元素"
    assert "/api/health" in html, "index.html 里没有请求 /api/health"
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    hit = [s for s in scripts
           if "/api/health" in s and "appVersion" in s and "version" in s]
    assert hit, ("没有一个 <script> 同时做三件事：请求 /api/health、拿到 appVersion 元素、"
                 "写 version（两处各写一半不算）")
    snippet = " ".join(hit[0].split())[:160]
    return ["index.html 里有 id=\"appVersion\" 与 /api/health",
            "填数脚本：%s…" % snippet]


@case("C-07", "QA 入口：能被 run-all 发现，并能在仓库根用文档指定解释器跑起来")
def c07():
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
    env["QA25_NESTED"] = "1"
    code, out, secs = _run([str(py), str(entry), "--entry-probe"], cwd=REPO, env=env, timeout=180)
    assert code == 0, "入口探针退出码 %d（%.1fs）：%s" % (
        code, secs, "\n".join(out.splitlines()[-6:]))
    assert "ENTRY-PROBE OK" in out, "入口探针没有打印 ENTRY-PROBE OK"
    return ["run-all.ps1 含 run.py ✓；--entry-probe → 退出码 0（仓库根）"]


# ────────────────────────────── 主流程 ──────────────────────────────
def _missing_report() -> int:
    print("[FAIL] 缺少 %s，先跑 packaging\\build_portable.py" % rel(PKG))
    print("       构建命令（本机实测可用）：%s" % BUILD_CMD)
    return 2


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print("T-25 验收：%s" % rel(PKG))
    if not PKG.is_dir():
        return _missing_report()
    if "--entry-probe" in args:
        print("ENTRY-PROBE OK %s" % rel(PKG))
        return 0
    if "--missing-probe" in args:
        print("[FAIL] 包存在，--missing-probe 不该被这样调用")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="qa25-", dir="D:\\"))
    _STATE["tmp"] = tmp
    print("临时目录：%s" % tmp)
    stale = stale_files()
    print("包新鲜度（与当前源码逐字节比对）：%s" % ("一致" if not stale else "不一致 → " + "；".join(stale)))

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
        if _STATE.get("port"):
            for line in cleanup_service("收尾"):
                print("     %s" % line)
        if tmp.is_dir():
            if fail == 0:
                shutil.rmtree(tmp, ignore_errors=True)
                print("     收尾：临时目录已删除（%s）" % tmp)
            else:
                print("     临时目录保留（失败现场）：%s" % tmp)

    # 只能人工判的那条，单列，不混进通过/失败。
    print("DEFERRED-HUMAN 页面右上角视觉位置（需人工/截图确认，机器判不了）")
    print("通过 %d / %d（另有 1 条 DEFERRED-HUMAN，不计入通过/失败）" % (ok, ok + fail))
    print("T-25 RESULT: %d/%d ok, 1 deferred" % (ok, ok + fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
