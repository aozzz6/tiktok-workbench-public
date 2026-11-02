"""Boot wrapper（v2）：完全脱离父进程 stdio 的启动方式。

v1 的问题（2026-10-21 实测）：`_boot.py` 用 `os.dup2(log.fileno(), 1/2)` 复用了父进程的
fd 1/2；父 shell（Agent 工具会话）退出时把控制台/管道句柄关掉，进程下次写日志就抛
`OSError(9, '句柄无效。')` + `lost sys.stderr` 直接崩。日志尾部能看到这两行。

v2 做法：用 Python 级别重定向（不再 dup2），并把 stdin 置为 /dev/null，
配合调用方的 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + stdio=DEVNULL，
让进程不持有任何来自父会话的句柄。
"""
import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

_log = open(os.path.join(BASE, "_server_run.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log
sys.stdin = open(os.devnull, "r", encoding="utf-8")

sys.argv = [os.path.join(BASE, "app.py")]
runpy.run_path(os.path.join(BASE, "app.py"), run_name="__main__")
