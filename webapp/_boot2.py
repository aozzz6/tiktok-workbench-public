"""Boot wrapper（v2）：完全脱离父进程 stdio 的启动方式。

v1 的问题（2026-10-21 实测）：`_boot.py` 用 `os.dup2(log.fileno(), 1/2)` 复用了父进程的
fd 1/2；父 shell（Agent 工具会话）退出时把控制台/管道句柄关掉，进程下次写日志就抛
`OSError(9, '句柄无效。')` + `lost sys.stderr` 直接崩。日志尾部能看到这两行。

v2 做法：用 Python 级别重定向（不再 dup2），并把 stdin 置为 /dev/null，
配合调用方的 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + stdio=DEVNULL，
让进程不持有任何来自父会话的句柄。

日志写到哪（T-16，2026-11-05）：与 `_boot.py` 同一规则 —— `WB_LOG_DIR` 决定目录，没设
仍是 `webapp\\_server_run.log`；打开日志前先建目录（`logs\\` 被删掉也不许静默丢日志）。
本模块 **import 时没有副作用**，执行逻辑在 `main()`，由 `__main__` 调用。
v2 **不接命令行参数**：端口只认 `WB_PORT`（由启动器写进环境），不要给它加端口命令行开关。
"""
import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_NAME = "_server_run.log"


def log_path(environ=None):
    """日志文件：WB_LOG_DIR 决定目录；未设回落到 webapp\\（与 _boot.py / _launch.py 同口径）。"""
    env = os.environ if environ is None else environ
    return os.path.join(env.get("WB_LOG_DIR") or BASE, LOG_NAME)


# 模块级只读快照；main() 仍用 log_path() 现算，import 之后改环境也不会写去旧路径。
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
        _warn(f"[boot2] 无法创建日志目录 {directory}：{exc}")
        return False


def _warn(message):
    """写 stderr；detached 启动时句柄可能无效，失败也不许把启动带崩。"""
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:                      # noqa: BLE001  句柄坏了也只能算了
        pass


def _open_log(target):
    """打开日志；WB_LOG_DIR 真建不出来时退回 webapp\\ 下的同名文件（至少不丢日志）。"""
    ensure_log_dir(target)
    try:
        return open(target, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        fallback = os.path.join(BASE, LOG_NAME)
        if os.path.abspath(fallback) == os.path.abspath(target):
            raise
        _warn(f"[boot2] 打不开日志 {target}：{exc}（退回 {fallback}）")
        return open(fallback, "a", encoding="utf-8", buffering=1)


def main():
    """启动入口（不接参数；端口只认 WB_PORT）：重定向 stdio → 跑 app.py。"""
    os.chdir(BASE)
    log = _open_log(log_path())
    sys.stdout = log
    sys.stderr = log
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    sys.argv = [os.path.join(BASE, "app.py")]
    runpy.run_path(os.path.join(BASE, "app.py"), run_name="__main__")


if __name__ == "__main__":
    main()
