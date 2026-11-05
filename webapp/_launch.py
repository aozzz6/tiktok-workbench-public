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
PKG_ROOT = os.path.dirname(BASE)
VENV = os.path.join(BASE, ".venv", "Scripts")
VENV_PY = os.path.join(VENV, "python.exe")
ENV_PY = os.environ.get("WB_PYTHON") or ""
BOOT = os.path.join(BASE, "_boot.py")


def resolve_python(env_py: str, venv_py: str):
    """(解释器, 找过的候选)：WB_PYTHON 存在就用它，否则回落开发检出里的 venv。"""
    tried = []
    if env_py:
        tried.append(env_py)
        if os.path.exists(env_py):
            return env_py, tried
    if venv_py not in tried:
        tried.append(venv_py)
    if os.path.exists(venv_py):
        return venv_py, tried
    return None, tried


PY, PY_TRIED = resolve_python(ENV_PY, VENV_PY)
# pythonw 优先 WB_PYTHONW；缺省推导 PY 同目录的 pythonw.exe（包内 python\ 就是这么放的）。
PYW = os.environ.get("WB_PYTHONW") or os.path.join(
    os.path.dirname(PY or ENV_PY or VENV_PY), "pythonw.exe")
# 日志：WB_LOG_DIR 决定目录（没设仍是开发检出的 webapp\）；文件名固定，与 _boot.py 对齐。
LOG = os.path.join(os.environ.get("WB_LOG_DIR") or BASE, "_server_run.log")


def resolve_restart_bat(pkg_root: str, webapp_dir: str) -> str:
    """包根有 重启工作台.bat 就用它；否则用 webapp 目录里的（开发检出）。"""
    pkg_bat = os.path.join(pkg_root, "重启工作台.bat")
    if os.path.exists(pkg_bat):
        return pkg_bat
    return os.path.join(webapp_dir, "重启工作台.bat")


RESTART_BAT = resolve_restart_bat(PKG_ROOT, BASE)
# 8787 放最后：本机 D:\skilltext\reporter.js 会常驻抢它（杀完会被 dsh runtime 拉起），
# 而 8790 是「token余额监控」app.py、8788 是它的 monitor.py —— 都不能当成工作台。
PORTS = [8789, 8791, 8792, 8793, 8787]
READY_TIMEOUT = 45.0
# 等就绪时的轮询间隔。0.6s 会让启动最坏白等 0.6 秒（M1 指标：首次 ≤8 s / 稳态 ≤3 s，CRD-0005）；
# health 请求本身只有毫秒级开销，0.15s 足够便宜。
READY_POLL = 0.15


def _system32(exe: str) -> str:
    """System32 下系统工具的**绝对路径**。

    安全评审：Windows 的 CreateProcess 会先找 exe 所在目录和当前目录（双击时=包根，
    用户可写），包根放一个同名 exe 就能劫持 netstat/taskkill。所以一律走绝对路径。
    """
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:\\Windows"
    return os.path.join(root, "System32", exe)


NETSTAT = _system32("netstat.exe")
TASKKILL = _system32("taskkill.exe")

# 工作台 health 的指纹：ok + ts + diagnostics(pipeline_dir/ledger_files)。
# 只认 "ok": true 会把 token余额监控（返回 db/upstream + ok）当成工作台。
_HEALTH_KEYS = ("ts", "diagnostics")
_DIAG_KEYS = ("pipeline_dir", "ledger_files")

# ★ 独立验收 R6：启动器以前只看 health 指纹，遇到旧进程就直接复用，于是
#   「磁盘已是新代码、进程还是旧代码」永远不自愈，而页面提示却叫人双击这个启动器。
#   现在 health 带 api_version/features；低于要求就明确提示用真正的重启入口。
REQUIRED_API_VERSION = 2
REQUIRED_FEATURES = ("repair", "ai")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _listening(port: int, timeout: float = 0.25) -> bool:
    """端口上有没有东西在听。

    超时故意压到 0.25s：在某些 Windows 配置下，连接一个已关闭的本机端口不会立刻
    refused，而是等满超时。probe() 每轮要扫 5 个端口，1.5s 的超时会拖成 7.5s
    （启动指标：首次 ≤8 s / 稳态 ≤3 s，CRD-0005）。真正有人监听时 connect 是毫秒级返回，不受影响。
    """
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
    """端口三态。先试 bind（空端口毫秒级返回），绑不上再连一下看是谁在听。

    顺序很重要：反过来的话，每个空端口都要等满 _listening 的超时，
    5 个端口就是 5 倍等待（实测 7.5s，直接把启动拖过 M1 的 5 秒线）。
    """
    if _bindable(port):
        return "free"
    if _listening(port):
        return "workbench" if health(port) else "foreign"
    return "foreign"


def wants_restart(argv) -> bool:
    """命令行是否要求重启（--restart [PORT]）。"""
    return restart_target(argv)[0]


def restart_target(argv):
    """解析 `--restart [PORT]`：返回 (是否重启, 端口或 None)。

    `--restart 8792` / `--restart=8792` → (True, 8792)；`--restart` → (True, None)。
    端口必须是 0 < PORT < 65536；越界/非数字按"不带端口"处理（照旧扫描候选），
    否则 `_bindable(99999)` 会抛 OverflowError。
    """
    def _valid(value: str):
        if value.isdigit():
            port = int(value)
            if 0 < port < 65536:
                return port
        return None

    args = list(argv)
    for index, token in enumerate(args):
        token = str(token)
        if token == "--restart":
            if index + 1 < len(args):
                port = _valid(str(args[index + 1]))
                if port is not None:
                    return True, port
            return True, None
        if token.startswith("--restart="):
            return True, _valid(token.split("=", 1)[1])
    return False, None


def listener_pid(port: int):
    """监听在该端口的进程 pid；读不到就返回 None。只认 LISTENING 行，只按端口取。"""
    try:
        proc = subprocess.run(
            [NETSTAT, "-ano", "-p", "tcp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        # 行格式：TCP  local  foreign  STATE  PID。只认 LISTENING，
        # 否则 TIME_WAIT 的 pid=0 会被当成结果。
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(":" + str(port))
                and any(part.upper() == "LISTENING" for part in parts[3:-1])
                and parts[-1].isdigit()):
            return int(parts[-1])
    return None


def restart_plan(port: int, state: str, pid) -> dict:
    """纯函数：--restart 在这个端口上该不该杀、杀谁。

    state 是 probe() 的三态。只有"本工作台"且拿得到监听 pid 才产生 kill；
    命令里只有一个 pid，绝不按进程名批量杀。工具走 System32 绝对路径。
    """
    if state != "workbench" or not pid:
        return {"port": port, "kill": False, "pid": None, "command": None}
    return {
        "port": port,
        "kill": True,
        "pid": int(pid),
        "command": [TASKKILL, "/PID", str(int(pid)), "/T", "/F"],
    }


def wait_released(port: int, timeout: float = 15.0) -> bool:
    """等端口不再被监听；超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _listening(port):
            return True
        time.sleep(0.3)
    return not _listening(port)


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
        # 端口还没被服务绑上时先别发 HTTP：某些 Windows 配置下 connect 一个未监听端口
        # 会一直阻塞到超时（本机 health() 实测白等 2.04s），而 _bindable() 是毫秒级。
        if not _bindable(port) and health(port):
            return True
        if proc.poll() is not None:
            # 进程退了：再给它 1 秒落日志，然后判失败
            time.sleep(1.0)
            return False if _bindable(port) else health(port) is not None
        time.sleep(READY_POLL)
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


def _start_on_port(port: int) -> int:
    """在指定端口拉起服务、等就绪、开浏览器。0 成功，1 失败。"""
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
    return 1


def _restart_on_port(port: int) -> int:
    """`--restart <PORT>`：只处理这一个端口，且重启必须回到这个端口（契约 §5.6）。

    - 本工作台 → 杀该端口的监听 pid（只这一个）、等释放、**在这个端口**重新启动；
    - 别人的程序 → 一律不杀，退出 2；
    - 空闲 → 直接在这个端口启动。
    """
    state = probe(port)
    if state == "foreign":
        print(f"[launcher] 端口 {port} 被别的程序占用，未做任何操作。")
        return 2
    if state == "workbench":
        pid = listener_pid(port)
        plan = restart_plan(port, "workbench", pid)
        if not plan["kill"]:
            print(f"[launcher] 在 {port} 找到工作台，但拿不到可杀的监听 pid；请手动结束。")
            return 2
        print(f"[launcher] 重启：结束 {port} 上的工作台进程（pid {pid}）")
        subprocess.run(plan["command"], capture_output=True)
        if not wait_released(port):
            print(f"[launcher] 端口 {port} 没能释放（pid {pid} 仍在监听）。")
            return 2
    return _start_on_port(port)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    py, tried = resolve_python(ENV_PY, VENV_PY)
    if not py:
        print("[ERROR] 找不到 Python 解释器，找过：")
        for path in tried:
            print(f"  {path}")
        return 1

    is_restart, restart_port = restart_target(args)
    if is_restart and restart_port is not None:
        # 带端口：只碰这一个端口，重启回到这个端口。
        return _restart_on_port(restart_port)

    if is_restart:
        # 不带端口：保持既有行为（扫描候选，命中本工作台就重启，都没有就照常启动）。
        for port in PORTS:
            if probe(port) != "workbench":
                continue
            pid = listener_pid(port)
            plan = restart_plan(port, "workbench", pid)
            if not plan["kill"]:
                print(f"[launcher] 在 {port} 找到工作台，但拿不到可杀的监听 pid；请手动结束。")
                return 2
            print(f"[launcher] 重启：结束 {port} 上的工作台进程（pid {pid}）")
            subprocess.run(plan["command"], capture_output=True)
            if not wait_released(port):
                print(f"[launcher] 端口 {port} 没能释放（pid {pid} 仍在监听）。")
                return 2
            break

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
        if _start_on_port(port) == 0:
            return 0

    print("[launcher] 全部候选端口（" + ", ".join(map(str, PORTS)) + "）都不可用。")
    print("[launcher] 排查：netstat -ano | findstr :8787  看占用 pid，再 tasklist /FI \"PID eq <pid>\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
