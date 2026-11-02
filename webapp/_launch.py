"""一键启动工作台：挑端口 -> 拉起服务 -> 等就绪 -> 开浏览器。

为什么需要这个（2026-10-24）：
  桌面入口（现统一放 `Desktop\\work\\启动工作台.bat`）的旧版只 netstat 看 8787 是否 LISTENING，本机
  D:\\skilltext\\reporter.js 会抢 8787 并返回 1x1 GIF，于是旧版判定
  "已在运行"，浏览器打开一片空白。这里只认 /api/health 的 JSON。

端口三态：
  workbench  已有本工作台在跑（health 返回 {"ok": true, ...}）
  foreign    别的程序占着（能 connect，但 health 不是我们的 JSON）
  free       空闲（connect 拒绝且 bind 成功）

扫描顺序 8787 -> 8789 -> 8790 -> 8791：优先复用已在跑的工作台，
否则用第一个空闲端口启动，并把 URL 打到屏幕上。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(BASE, ".venv", "Scripts")
PY = os.path.join(VENV, "python.exe")
PYW = os.path.join(VENV, "pythonw.exe")
BOOT = os.path.join(BASE, "_boot.py")
LOG = os.path.join(BASE, "_server_run.log")
# 8787 放最后：本机 D:\skilltext\reporter.js 会常驻抢它（杀完会被 dsh runtime 拉起），
# 而 8790 是「token余额监控」app.py、8788 是它的 monitor.py —— 都不能当成工作台。
PORTS = [8789, 8791, 8792, 8793, 8787]
READY_TIMEOUT = 45.0

# 工作台 health 的指纹：ok + ts + diagnostics(pipeline_dir/ledger_files)。
# 只认 "ok": true 会把 token余额监控（返回 db/upstream + ok）当成工作台。
_HEALTH_KEYS = ("ts", "diagnostics")
_DIAG_KEYS = ("pipeline_dir", "ledger_files")

# ★ 独立验收 R6：启动器以前只看 health 指纹，遇到旧进程就直接复用，于是
#   「磁盘已是新代码、进程还是旧代码」永远不自愈，而页面提示却叫人双击这个启动器。
#   现在 health 带 api_version/features；低于要求就明确提示用真正的重启入口。
REQUIRED_API_VERSION = 2
REQUIRED_FEATURES = ("repair", "ai")
RESTART_BAT = os.path.join(BASE, "重启工作台.bat")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _listening(port: int, timeout: float = 1.5) -> bool:
    """端口上有没有东西在听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _bindable(port: int) -> bool:
    """能不能绑（和 app.py 的 _port_free 同口径）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_workbench(data) -> bool:
    if not isinstance(data, dict) or data.get("ok") is not True:
        return False
    if any(key not in data for key in _HEALTH_KEYS):
        return False
    diag = data.get("diagnostics")
    return isinstance(diag, dict) and all(key in diag for key in _DIAG_KEYS)


def api_status(data) -> tuple[bool, str]:
    """运行中实例的接口版本是否够新；返回 (是否够新, 一句说明)。"""
    data = data if isinstance(data, dict) else {}
    try:
        version = int(data.get("api_version") or 0)
    except (TypeError, ValueError):
        version = 0
    features = {str(x) for x in (data.get("features") or [])}
    missing = [f for f in REQUIRED_FEATURES if f not in features]
    if version >= REQUIRED_API_VERSION and not missing:
        return True, f"api_version={version}"
    detail = f"api_version={version}（需要 ≥{REQUIRED_API_VERSION}）"
    if missing:
        detail += f"，缺接口：{'、'.join('/api/' + f + '/*' for f in missing)}"
    return False, detail


def print_upgrade_hint(port: int, detail: str) -> None:
    """旧进程明确提示升级：给出真正的重启入口，而不是让人重复双击启动器。"""
    print(f"[launcher] 检测到 {port} 上运行的是**旧版工作台**：{detail}")
    print("[launcher] 磁盘代码已更新，但运行中的进程还是旧的，所以 /api/repair/* 或 /api/ai/* 仍会 404。")
    print("[launcher] 请运行下面这个真正的重启入口（会重启该端口的 python 进程，不影响别的程序）：")
    print(f'[launcher]     "{RESTART_BAT}" {port}')
    print("[launcher] 或双击桌面 work 目录下的「重启工作台.bat」。重启后再点一键修复即可。")


def health(port: int, timeout: float = 2.5):
    """拿 health JSON；拿不到或不是本工作台就返回 None。"""
    try:
        with _opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            raw = resp.read(8192)
    except Exception:                      # noqa: BLE001  超时/重置/非 HTTP 都算不可用
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if _is_workbench(data) else None


def probe(port: int) -> str:
    if _listening(port):
        return "workbench" if health(port) else "foreign"
    return "free" if _bindable(port) else "foreign"


def spawn(port: int) -> subprocess.Popen:
    exe = PYW if os.path.exists(PYW) else PY
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    return subprocess.Popen(
        [exe, BOOT, "--port", str(port)],
        cwd=BASE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def wait_ready(port: int, proc) -> bool:
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if health(port):
            return True
        if proc.poll() is not None and not health(port):
            # 进程退了：再给它 1 秒落日志，然后判失败
            time.sleep(1.0)
            return health(port) is not None
        time.sleep(0.6)
    return False


def open_page(url: str) -> None:
    try:
        os.startfile(url)                  # noqa: S606  桌面环境默认浏览器
    except OSError:
        subprocess.Popen(["cmd", "/c", "start", "", url])


def tail_log(lines: int = 15) -> str:
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).rstrip()
    except OSError:
        return "(读不到日志)"


def main() -> int:
    if not os.path.exists(PY):
        print(f"[ERROR] 找不到 venv python：{PY}")
        return 1

    states = [(port, probe(port)) for port in PORTS]

    for port, state in states:
        if state == "workbench":
            data = health(port)
            ok, detail = api_status(data)
            if not ok:
                # R6：旧进程不能复用 —— 复用等于继续 404，必须明确提示走重启入口
                print_upgrade_hint(port, detail)
                return 2
            url = f"http://127.0.0.1:{port}/"
            print(f"[launcher] 工作台已在 {port} 运行（{detail}），直接打开：{url}")
            open_page(url)
            return 0

    for port, state in states:
        if state != "free":
            print(f"[launcher] 端口 {port} 被别的程序占用，跳过")
            continue
        url = f"http://127.0.0.1:{port}/"
        print(f"[launcher] 在 {port} 启动工作台 ...")
        proc = spawn(port)
        if wait_ready(port, proc):
            print(f"[launcher] 就绪：{url}")
            open_page(url)
            return 0
        print(f"[launcher] 端口 {port} 启动失败（pid {proc.pid}，exit={proc.poll()}）")
        print("[launcher] 日志末尾：")
        print(tail_log())

    print("[launcher] 全部候选端口（" + ", ".join(map(str, PORTS)) + "）都不可用。")
    print("[launcher] 排查：netstat -ano | findstr :8787  看占用 pid，再 tasklist /FI \"PID eq <pid>\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
