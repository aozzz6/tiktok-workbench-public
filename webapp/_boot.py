"""Boot wrapper: chdir to webapp, redirect stdout/stderr to the run log, run app.py.

用法：pythonw _boot.py [--port 8789]
--port 会写进 WB_PORT（app.py 的 _listen_port() 读它）；不给就沿用外部 WB_PORT/PORT。

日志写到哪（T-16，2026-11-05）：由 `WB_LOG_DIR` 决定 —— 设了写
`<WB_LOG_DIR>\\_server_run.log`，没设仍是 `webapp\\_server_run.log`；与 `webapp/_launch.py`
的 `LOG` 是同一个文件（启动超时时 launcher 的 tail_log() 才读得到）。打开日志前先把目录
建出来：用户把 `logs\\` 删了也要照写，不许静默丢日志。
本模块 **import 时没有副作用**：顶层只放常量与函数，执行逻辑在 `main()`，由 `__main__` 调用。

★ 2026-10-23：用 `start /min` 或 pythonw 拉起时没有有效控制台，`os.dup2` 会抛
  OSError(9, 句柄无效)，旧版直接崩在重定向这一步（日志里只有 "lost sys.stderr"）。
  现在逐个 fd 兜底，失败就把 sys.stdout/sys.stderr 换成交付的日志对象。
"""
import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_NAME = "_server_run.log"


def log_path(environ=None):
    """日志文件：WB_LOG_DIR 决定目录；未设回落到 webapp\\（与 _launch.py 同口径）。"""
    env = os.environ if environ is None else environ
    return os.path.join(env.get("WB_LOG_DIR") or BASE, LOG_NAME)


# 模块级只读快照，方便调用方/用例直接看；main() 仍用 log_path() 现算，
# 这样 import 之后再改环境也不会写去旧路径。
LOG = log_path()


def ensure_log_dir(target):
    """打开日志前先把目录建出来；建不出来返回 False（并喊一声，不静默丢日志）。"""
    directory = os.path.dirname(target)
    if not directory:
        return True
    try:
        os.makedirs(directory, exist_ok=True)
        return True
    except OSError as exc:
        _warn(f"[boot] 无法创建日志目录 {directory}：{exc}")
        return False


def _warn(message):
    """写 stderr；detached 启动时句柄可能无效，失败也不许把启动带崩。"""
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:                      # noqa: BLE001  句柄坏了也只能算了
        pass


def _port_arg(argv):
    """从 --port 8789 / --port=8789 取端口；没有就返回空串。"""
    for i, item in enumerate(argv):
        if item == "--port" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if item.startswith("--port="):
            return item.split("=", 1)[1].strip()
    return ""


def _redirect(target):
    """把 1/2 号 fd 指到日志；没有有效控制台时退化为替换 sys.stdout/sys.stderr。"""
    try:
        log = open(target, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        _warn(f"[boot] 打不开日志 {target}：{exc}")
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


def main(argv=None):
    """启动入口：解析 --port → 建日志目录 → 重定向 → 跑 app.py。"""
    args = list(sys.argv[1:] if argv is None else argv)
    port = _port_arg(args)
    if port:
        os.environ["WB_PORT"] = port
    os.chdir(BASE)
    target = log_path()
    ensure_log_dir(target)
    log = _redirect(target)
    if log is not None:                    # 打不开日志时 stdout 可能无效，别在这儿再崩一次
        try:
            print(f"[boot] 启动，端口 {os.environ.get('WB_PORT') or os.environ.get('PORT') or 8787}",
                  flush=True)
        except OSError:
            pass
    sys.argv = [os.path.join(BASE, "app.py")]
    runpy.run_path(os.path.join(BASE, "app.py"), run_name="__main__")


if __name__ == "__main__":
    main()
