# -*- coding: utf-8 -*-
"""测试隔离：把运行 journal 指到临时文件（2026-10-24 总览诊断 P0-2）。

为什么需要它
    单测会真的起 run（桩账号 acc1/acc3），以前直接写生产 `data/runs.jsonl`。
    实测当天 91 条记录里 58 条是测试写进去的，直接把总览「今日执行」算歪，
    而这些假记录还会出现在 /api/runs、待接管统计、最近运行列表里。

怎么用
    在任何 `import config / runner / repair / app` **之前**加一行：
        import _isolate  # noqa: F401
    子进程也会继承 WB_RUNS_FILE（config.run_env 复制 os.environ），所以
    「测试里起的子 run」同样落到临时目录，不会漏回生产文件。

生产不受影响
    只在显式 import 时生效；若调用方已经设了 WB_RUNS_FILE 则不覆盖（便于指定固定路径）。
"""
from __future__ import annotations

import os
import pathlib
import tempfile

if not os.environ.get("WB_RUNS_FILE"):
    _dir = pathlib.Path(tempfile.mkdtemp(prefix="wb-test-runs-"))
    os.environ["WB_RUNS_FILE"] = str(_dir / "runs.jsonl")

try:                                    # config 可能已被更早的 import 拉起来
    import config
    config.RUNS_JSONL = pathlib.Path(os.environ["WB_RUNS_FILE"])
    config.RUNS_JSONL.parent.mkdir(parents=True, exist_ok=True)
except Exception:                       # pragma: no cover - 隔离失败不该让测试崩
    pass
