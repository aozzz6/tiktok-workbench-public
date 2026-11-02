"""Boot wrapper: chdir to webapp, redirect stdout/stderr to _server_run.log, run app.py.

用法：pythonw _boot.py [--port 8789]
--port 会写进 WB_PORT（app.py 的 _listen_port() 读它）；不给就沿用外部 WB_PORT/PORT。

★ 2026-10-23：用 `start /min` 或 pythonw 拉起时没有有效控制台，`os.dup2` 会抛
  OSError(9, 句柄无效)，旧版直接崩在重定向这一步（日志里只有 "lost sys.stderr"）。
  现在逐个 fd 兜底，失败就把 sys.stdout/sys.stderr 换成交付的日志对象。
"""
import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def _port_arg(argv):
    """从 --port 8789 / --port=8789 取端口；没有就返回空串。"""
    for i, item in enumerate(argv):
        if item == "--port" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if item.startswith("--port="):
            return item.split("=", 1)[1].strip()
    return ""


def _redirect(log_path):
    """把 1/2 号 fd 指到日志；没有有效控制台时退化为替换 sys.stdout/sys.stderr。"""
    try:
        log = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    duped = 0
    for fd in (1, 2):
        try:
            os.dup2(log.fileno(), fd)
            duped += 1
        except OSError:
            pass
    # 无论 dup2 成没成，Python 层的 stdout/stderr 一律换成日志对象：
    # detached 启动时句柄可能是无效的，uvicorn/print 一写就 OSError(9)。
    try:
        sys.stdout = log
        sys.stderr = log
    except Exception:              # noqa: BLE001  极端情况下也不值得阻断启动
        pass
    return log


_port = _port_arg(sys.argv[1:])
if _port:
    os.environ["WB_PORT"] = _port

os.chdir(BASE)
_redirect(os.path.join(BASE, "_server_run.log"))
print(f"[boot] 启动，端口 {os.environ.get('WB_PORT') or os.environ.get('PORT') or 8787}", flush=True)
sys.argv = [os.path.join(BASE, "app.py")]
runpy.run_path(os.path.join(BASE, "app.py"), run_name="__main__")
