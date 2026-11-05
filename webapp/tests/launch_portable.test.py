# -*- coding: utf-8 -*-
"""T-14 便携启动器契约单测（`webapp\\_launch.py`）。

只做 import 与纯函数断言：不起服务、不联网、不打开浏览器。
覆盖 T-14 DoD 1：
  - `WB_PYTHON` 优先于 `webapp\\.venv\\Scripts\\python.exe`；两个都不存在时 main() 返回 1 并打印找过的两个路径
  - `WB_PYTHONW` 优先；缺省时推导 `PY` 同目录的 `pythonw.exe`
  - `WB_LOG_DIR` 决定 LOG；未设仍是 `webapp\\_server_run.log`
  - 包根有 `重启工作台.bat` 时 RESTART_BAT 取包根那份，否则取 `webapp\\` 那份
  - `--restart` 的"该不该杀"纯函数：三种端口状态下只有"本工作台"产生 kill，命令里只有一个 pid

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe webapp\\tests\\launch_portable.test.py`
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parent
LAUNCH = WEBAPP / "_launch.py"
VENV_PY = WEBAPP / ".venv" / "Scripts" / "python.exe"
VENV_PYW = WEBAPP / ".venv" / "Scripts" / "pythonw.exe"

ENV_KEYS = ("WB_PYTHON", "WB_PYTHONW", "WB_LOG_DIR")

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


def load_with(env):
    """在给定 WB_* 环境下重新加载 `_launch.py`，返回模块。"""
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in env.items():
            os.environ[key] = str(value)
        spec = importlib.util.spec_from_file_location("_launch_t14", str(LAUNCH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_stub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")                # 只要"存在"即可，绝不执行
    return path


print("T-14 启动器契约（launch_portable）")

# ── 1. 解释器解析 ───────────────────────────────────────────────────
print("[1] 解释器：WB_PYTHON 优先，缺省回落到 webapp\\.venv")


def _default_python():
    mod = load_with({})
    assert_(mod.PY == str(VENV_PY), f"PY={mod.PY!r}，期望 {str(VENV_PY)!r}")


check("不设 WB_PYTHON 时 PY = webapp\\.venv\\Scripts\\python.exe", _default_python)


def _env_python_wins():
    tmp = Path(tempfile.mkdtemp(prefix="t14_py_"))
    stub = write_stub(tmp / "python.exe")
    mod = load_with({"WB_PYTHON": str(stub)})
    assert_(mod.PY == str(stub), f"PY={mod.PY!r}，期望 {str(stub)!r}")


check("WB_PYTHON 指向存在的解释器时优先于 venv", _env_python_wins)


def _env_missing_falls_back():
    mod = load_with({})
    missing = str(Path(tempfile.mkdtemp(prefix="t14_py_")) / "nope" / "python.exe")
    py, tried = mod.resolve_python(missing, str(VENV_PY))
    assert_(py == str(VENV_PY), f"resolve={py!r}")
    assert_(tried == [missing, str(VENV_PY)], f"tried={tried!r}")


check("WB_PYTHON 指定但不存在时回落到 venv（记下两个候选）", _env_missing_falls_back)


# ── 2. pythonw 解析 ─────────────────────────────────────────────────
print("[2] pythonw：WB_PYTHONW 优先，缺省推导 PY 同目录")


def _env_pythonw_wins():
    tmp = Path(tempfile.mkdtemp(prefix="t14_pyw_"))
    stub = write_stub(tmp / "pythonw.exe")
    mod = load_with({"WB_PYTHONW": str(stub)})
    assert_(mod.PYW == str(stub), f"PYW={mod.PYW!r}，期望 {str(stub)!r}")


check("WB_PYTHONW 优先", _env_pythonw_wins)


def _derive_pythonw_from_py():
    tmp = Path(tempfile.mkdtemp(prefix="t14_pyw_"))
    py = write_stub(tmp / "python.exe")
    mod = load_with({"WB_PYTHON": str(py)})
    want = str(tmp / "pythonw.exe")
    assert_(mod.PYW == want, f"PYW={mod.PYW!r}，期望 {want!r}")


check("缺省 WB_PYTHONW 时推导 PY 同目录的 pythonw.exe", _derive_pythonw_from_py)


def _default_pythonw():
    mod = load_with({})
    assert_(mod.PYW == str(VENV_PYW), f"PYW={mod.PYW!r}，期望 {str(VENV_PYW)!r}")


check("全缺省时 PYW = webapp\\.venv\\Scripts\\pythonw.exe", _default_pythonw)


# ── 3. 日志路径 ─────────────────────────────────────────────────────
print("[3] 日志：WB_LOG_DIR 决定 LOG，未设仍是 webapp\\_server_run.log")


def _log_default():
    mod = load_with({})
    assert_(mod.LOG == str(WEBAPP / "_server_run.log"), f"LOG={mod.LOG!r}")


check("未设 WB_LOG_DIR 时 LOG = webapp\\_server_run.log", _log_default)


def _log_from_env():
    logs = Path(tempfile.mkdtemp(prefix="t14_logs_"))
    mod = load_with({"WB_LOG_DIR": str(logs)})
    assert_(mod.LOG == str(logs / "_server_run.log"), f"LOG={mod.LOG!r}")
    assert_(str(WEBAPP) not in mod.LOG, f"LOG 仍指向 webapp：{mod.LOG!r}")


check("设了 WB_LOG_DIR 时 LOG = <WB_LOG_DIR>\\_server_run.log", _log_from_env)


# ── 4. 重启入口定位 ─────────────────────────────────────────────────
print("[4] 重启入口：包根优先，否则 webapp\\")


def _restart_bat_pkg_first():
    pkg = Path(tempfile.mkdtemp(prefix="t14_pkg_"))
    webapp = Path(tempfile.mkdtemp(prefix="t14_web_"))
    (pkg / "重启工作台.bat").write_bytes(b"@echo off\r\n")
    got = mod_resolve().resolve_restart_bat(str(pkg), str(webapp))
    assert_(got == str(pkg / "重启工作台.bat"), f"got={got!r}")


def mod_resolve():
    return load_with({})


check("包根有 重启工作台.bat 时取包根那份", _restart_bat_pkg_first)


def _restart_bat_webapp_fallback():
    pkg = Path(tempfile.mkdtemp(prefix="t14_pkg_"))
    webapp = Path(tempfile.mkdtemp(prefix="t14_web_"))
    got = mod_resolve().resolve_restart_bat(str(pkg), str(webapp))
    assert_(got == str(webapp / "重启工作台.bat"), f"got={got!r}")


check("包根没有时退回 webapp\\重启工作台.bat", _restart_bat_webapp_fallback)


def _restart_bat_module_value():
    mod = load_with({})
    assert_(mod.PKG_ROOT == str(WEBAPP.parent), f"PKG_ROOT={mod.PKG_ROOT!r}")
    assert_(mod.RESTART_BAT == mod.resolve_restart_bat(mod.PKG_ROOT, mod.BASE),
            f"RESTART_BAT={mod.RESTART_BAT!r} 与解析函数不一致")


check("模块级 RESTART_BAT 与解析函数一致", _restart_bat_module_value)


# ── 5. 两个解释器都不存在：main() 退出码 1 ──────────────────────────
print("[5] 两个解释器都不存在：main() 返回 1，打印找过的两个路径")


def _main_missing_both():
    mod = load_with({})
    tmp = Path(tempfile.mkdtemp(prefix="t14_none_"))
    missing_env = str(tmp / "env_python.exe")
    missing_venv = str(tmp / "venv_python.exe")
    mod.ENV_PY = missing_env
    mod.VENV_PY = missing_venv
    # 双保险：即使实现有 bug 也不会真的去探测/拉起服务；所有端口都当"别人的程序"。
    mod.probe = lambda port: "foreign"
    mod.spawn = lambda port: (_ for _ in ()).throw(AssertionError("解释器缺失时不应走到 spawn"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    out = buf.getvalue()
    assert_(rc == 1, f"rc={rc}，期望 1；输出：{out!r}")
    assert_(missing_env in out, f"输出里没有 WB_PYTHON 候选 {missing_env!r}：{out!r}")
    assert_(missing_venv in out, f"输出里没有 venv 候选 {missing_venv!r}：{out!r}")


check("两个解释器都不存在 → main() == 1 且打印两个路径", _main_missing_both)


# ── 6. --restart 的"该不该杀"纯函数 ────────────────────────────────
print("[6] --restart：只有'本工作台'产生 kill，命令里只有一个 pid")


def _plan_free_foreign():
    mod = load_with({})
    for state in ("free", "foreign"):
        plan = mod.restart_plan(8789, state, 4321)
        assert_(plan["kill"] is False, f"{state}: plan={plan!r}")
        assert_(plan["command"] is None, f"{state}: command={plan['command']!r}")


check("free / foreign 不杀（哪怕拿到了 pid）", _plan_free_foreign)


def _plan_workbench():
    mod = load_with({})
    plan = mod.restart_plan(8791, "workbench", 4321)
    assert_(plan["kill"] is True, f"plan={plan!r}")
    assert_(plan["pid"] == 4321, f"pid={plan['pid']!r}")
    cmd = plan["command"]
    assert_(isinstance(cmd, list) and cmd, f"command={cmd!r}")
    assert_(cmd[0].lower() == "taskkill", f"command[0]={cmd[0]!r}")
    pids = [token for token in cmd if str(token).isdigit()]
    assert_(pids == ["4321"], f"命令里的 pid 应恰好一个 4321，实得 {pids!r}（command={cmd!r}）")
    assert_("/IM" not in cmd, "不许按进程名批量杀")
    assert_("/PID" in cmd, "应按 pid 杀")


check("workbench → kill，taskkill 只带一个 pid、不按名字批量杀", _plan_workbench)


def _plan_workbench_no_pid():
    mod = load_with({})
    plan = mod.restart_plan(8789, "workbench", None)
    assert_(plan["kill"] is False, f"拿不到 pid 时不许杀：{plan!r}")


check("workbench 但拿不到 pid → 不杀", _plan_workbench_no_pid)


# ── 7. --restart 参数识别 ───────────────────────────────────────────
print("[7] --restart 参数识别")


def _restart_flag():
    mod = load_with({})
    assert_(mod.wants_restart([]) is False, "空参数不应重启")
    assert_(mod.wants_restart(["--restart"]) is True, "--restart 应识别")
    assert_(mod.wants_restart(["-x", "--restart"]) is True, "混在其它参数里也应识别")


check("wants_restart 只认 --restart", _restart_flag)


# ── 8. probe() 性能与健康判据（M1 DoD 1 / T-15 DoD 8：双击到 ready ≤5 秒） ──
print("[8] probe()：空端口要快，有人监听仍判 workbench/foreign")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _listening_is_fast():
    mod = load_with({})
    port = _free_port()
    t0 = time.perf_counter()
    listening = mod._listening(port)
    elapsed = time.perf_counter() - t0
    assert_(listening is False, f"空端口不应判为有人在听：{port}")
    assert_(elapsed < 0.5,
            f"_listening(空端口) 耗时 {elapsed:.2f}s，应 <0.5s（默认超时要降到 0.2~0.3s）")


check("_listening(空端口) 默认超时下 < 0.5 秒", _listening_is_fast)


def _probe_five_free_ports_fast():
    mod = load_with({})
    ports = list(mod.PORTS)
    if not all(_port_free(p) for p in ports):
        # 机器上已有本工作台/别的程序占着候选端口：用同样数量的临时空端口做同一判据。
        ports = [_free_port() for _ in range(len(mod.PORTS))]
    t0 = time.perf_counter()
    states = [(p, mod.probe(p)) for p in ports]
    elapsed = time.perf_counter() - t0
    assert_(all(state == "free" for _, state in states),
            f"空端口应判 free，实得 {states!r}")
    assert_(elapsed < 1.0, f"5 个空端口 probe 一轮耗时 {elapsed:.2f}s，应 <1.0s")


check("5 个空端口 probe 一轮 < 1.0 秒且全判 free", _probe_five_free_ports_fast)


def _probe_foreign_raw_socket():
    mod = load_with({})
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert_(mod.probe(port) == "foreign",
                f"有人在听但不是本工作台时 {port} 应判 foreign")
    finally:
        srv.close()


check("有人在听但不是 HTTP 工作台 → foreign", _probe_foreign_raw_socket)


class _HealthHandler(BaseHTTPRequestHandler):
    payload = {}

    def do_GET(self):
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _http_probe(payload, want):
    class Handler(_HealthHandler):
        pass

    Handler.payload = payload
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        mod = load_with({})
        got = mod.probe(port)
        assert_(got == want, f"health={payload!r} 时 probe({port})={got!r}，期望 {want!r}")
    finally:
        srv.shutdown()
        srv.server_close()


def _probe_workbench_http():
    _http_probe({
        "ok": True, "ts": 1.0, "api_version": 4, "features": ["repair", "ai"],
        "diagnostics": {"pipeline_dir": "x", "ledger_files": []},
    }, "workbench")


check("HTTP 返回本工作台 health 指纹 → workbench", _probe_workbench_http)


def _probe_foreign_http():
    _http_probe({"ok": True, "ts": 1.0}, "foreign")


check("HTTP 返回但不是本工作台指纹（缺 diagnostics）→ foreign", _probe_foreign_http)


# ── 9. wait_ready 轮询间隔（冷启动 5 秒预算） ──────────────────────
print("[9] wait_ready：轮询间隔 ≤0.2s（冷启动时最坏不能白等 0.6s）")


def _wait_ready_polls_fast():
    mod = load_with({})
    stamps = []

    def fake_health(port, timeout=2.5):
        stamps.append(time.perf_counter())
        return None

    class FakeProc:
        def poll(self):
            return None

    mod.health = fake_health
    mod._bindable = lambda p: False      # 模拟"服务已绑上端口"，health 才会被调用
    mod.READY_TIMEOUT = 1.0          # 只跑 1 秒，别等满 45 秒
    ok = mod.wait_ready(8789, FakeProc())
    assert_(ok is False, f"一直没 ready 时应返回 False，实得 {ok!r}")
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    worst = max(gaps) if gaps else 99.0
    assert_(worst <= 0.2,
            f"wait_ready 轮询间隔 {worst:.2f}s > 0.2s（每次最坏白等这么久，冷启动 5 秒预算吃不住）")
    assert_(len(stamps) >= 3, f"1 秒内只轮询了 {len(stamps)} 次（间隔过大）")


check("wait_ready 轮询间隔 ≤ 0.2 秒", _wait_ready_polls_fast)


def _wait_ready_skips_health_while_port_free():
    """端口还没被服务绑上时，不许发 HTTP：某些 Windows 配置下 connect 未监听端口
    会阻塞到超时（本机实测 health() 白等 2.04s），而 _bindable() 是毫秒级。"""
    mod = load_with({})
    calls = []
    mod._bindable = lambda p: True                  # 端口仍空闲
    mod.health = lambda p, timeout=2.5: (calls.append(p), None)[1]
    mod.READY_TIMEOUT = 0.5
    mod.READY_POLL = 0.05

    class FakeProc:
        def poll(self):
            return None

    t0 = time.perf_counter()
    ok = mod.wait_ready(8789, FakeProc())
    elapsed = time.perf_counter() - t0
    assert_(ok is False, f"一直没 ready 时应返回 False，实得 {ok!r}")
    assert_(calls == [], f"端口还空着时不应发 health（会阻塞），实得要把 {calls!r}")
    assert_(elapsed < 0.9, f"0.5s 窗口实际用了 {elapsed:.2f}s（说明有阻塞调用）")


check("wait_ready 在端口空闲时不发 HTTP（避免 connect 阻塞）", _wait_ready_skips_health_while_port_free)


# ── 10. --restart [PORT]：带端口必须回到那个端口 ───────────────────
print("[10] --restart [PORT]：带端口回原端口；foreign 不杀；不带端口行为不变")


def _restart_target_parse():
    mod = load_with({})
    assert_(mod.restart_target(["--restart", "8792"]) == (True, 8792),
            f"带端口解析错：{mod.restart_target(['--restart', '8792'])!r}")
    assert_(mod.restart_target(["--restart=8792"]) == (True, 8792),
            "--restart=8792 也要认")
    assert_(mod.restart_target(["--restart"]) == (True, None), "不带端口应为 (True, None)")
    assert_(mod.restart_target([]) == (False, None), "不是 restart 应为 (False, None)")
    assert_(mod.restart_target(["--restart", "abc"]) == (True, None), "端口不是数字时按不带端口处理")


check("restart_target：--restart [PORT] / --restart=PORT 解析", _restart_target_parse)


def _restart_port_plan():
    mod = load_with({})
    is_restart, port = mod.restart_target(["--restart", "8792"])
    assert_(is_restart is True and port == 8792, f"target={(is_restart, port)!r}")
    plan = mod.restart_plan(port, "workbench", 4321)
    assert_(plan["port"] == 8792, f"plan.port={plan['port']!r}（必须是 8792）")
    assert_(plan["kill"] is True, f"plan={plan!r}")
    assert_(plan["pid"] == 4321, f"plan.pid={plan['pid']!r}")
    pids = [t for t in plan["command"] if str(t).isdigit()]
    assert_(pids == ["4321"], f"命令里应恰好一个 pid 4321：{plan['command']!r}")
    foreign = mod.restart_plan(8792, "foreign", 4321)
    assert_(foreign["kill"] is False and foreign["command"] is None,
            f"foreign 不许杀：{foreign!r}")


check("--restart 8792 + 本工作台 → kill 的是 8792 的监听 pid、端口 8792", _restart_port_plan)


def _restart_port_workbench_restarts_there():
    mod = load_with({})
    spawned, commands = [], []

    class FakeProc:
        pid = 1

        def poll(self):
            return None

    mod.probe = lambda p: "workbench" if p == 8792 else "free"
    mod.listener_pid = lambda p: 4321 if p == 8792 else None
    mod.subprocess.run = lambda *a, **k: commands.append(a[0] if a else k)
    mod.wait_released = lambda p, timeout=15.0: True
    mod.spawn = lambda p: (spawned.append(p), FakeProc())[1]
    mod.wait_ready = lambda p, proc: True
    mod.open_page = lambda url: None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--restart", "8792"])
    out = buf.getvalue()
    assert_(rc == 0, f"rc={rc}；输出：{out!r}")
    assert_(spawned == [8792], f"必须在 8792 重启，实得 spawn={spawned!r}")
    assert_(commands, "应调用一次 taskkill")
    cmd = commands[-1]
    pids = [t for t in cmd if str(t).isdigit()]
    assert_(pids == ["4321"], f"只许杀监听 pid 4321，实得 {pids!r}（cmd={cmd!r}）")
    assert_("8792" in out, f"输出里应出现 8792：{out!r}")


check("--restart 8792 + 本工作台 → 杀掉 8792 的 pid 并起在 8792", _restart_port_workbench_restarts_there)


def _restart_port_foreign_exit2():
    mod = load_with({})
    spawned, commands = [], []
    mod.probe = lambda p: "foreign" if p == 8792 else "free"
    mod.subprocess.run = lambda *a, **k: commands.append(a)
    mod.spawn = lambda p: (spawned.append(p), None)[1]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--restart", "8792"])
    out = buf.getvalue()
    assert_(rc == 2, f"foreign 应退出 2，实得 rc={rc}；输出：{out!r}")
    assert_(spawned == [], f"foreign 不许启动，实得 spawn={spawned!r}")
    assert_(commands == [], f"foreign 不许杀任何进程，实得 {commands!r}")
    assert_("8792" in out and "别的程序" in out, f"输出应说明端口被别的程序占用：{out!r}")


check("--restart 8792 + foreign → 不杀、不起、退出 2", _restart_port_foreign_exit2)


def _restart_port_free_starts_there():
    mod = load_with({})
    spawned = []

    class FakeProc:
        pid = 1

        def poll(self):
            return None

    mod.probe = lambda p: "free"
    mod.spawn = lambda p: (spawned.append(p), FakeProc())[1]
    mod.wait_ready = lambda p, proc: True
    mod.open_page = lambda url: None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--restart", "8792"])
    out = buf.getvalue()
    assert_(rc == 0, f"rc={rc}；输出：{out!r}")
    assert_(spawned == [8792], f"空端口应起在 8792，实得 {spawned!r}")
    assert_("8792" in out, f"输出里应出现 8792：{out!r}")


check("--restart 8792 + 空闲 → 起在 8792", _restart_port_free_starts_there)


def _restart_no_port_unchanged():
    mod = load_with({})
    spawned = []

    class FakeProc:
        pid = 1

        def poll(self):
            return None

    # 没有任何实例：照常启动，退出 0
    mod.probe = lambda p: "free"
    mod.spawn = lambda p: (spawned.append(p), FakeProc())[1]
    mod.wait_ready = lambda p, proc: True
    mod.open_page = lambda url: None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--restart"])
    out = buf.getvalue()
    assert_(rc == 0, f"无实例时应照常启动并退出 0，rc={rc}；输出：{out!r}")
    assert_(spawned == [mod.PORTS[0]], f"应扫描候选起在 {mod.PORTS[0]}，实得 {spawned!r}")


check("不带端口的 --restart：无实例时照常启动（行为不变）", _restart_no_port_unchanged)


print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
