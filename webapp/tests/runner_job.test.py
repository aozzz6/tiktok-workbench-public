# -*- coding: utf-8 -*-
"""runner 的 Job Object 回归：服务被强杀时，子进程**整棵树**（含孙进程）必须一起回收。

背景：
  · 2026-10-21 独立验收 P1-2：runner 用普通 Popen 拉 pipeline，webapp 被强杀后子进程变
    孤儿，重启后的 journal 只剩 interrupted，于是同一账号可能被第二轮并行跑。
  · 2026-10-21 独立验收 P1-R2：旧顺序「Popen 启动 → 落 pid → 建 Job → Assign」有窗口 ——
    子进程入 Job 前就能创建孙进程，这些孙进程不会随后自动补入 Job，服务被杀后仍存活，
    而 journal 只记父 pid（已退出）→ daily_batch 判定「可以重试」→ 同账号两轮并行。

做法：起一个最小宿主（_job_drill_harness.py），它用真实 runner 拉起子任务：
  · child 模式：子进程只是长睡（父子两代）
  · tree 模式：子进程启动后**立刻创建孙进程**（P1-R2 要求补的回归形态）
宿主打印 pid 与「谁在 Job 里」，测试**强杀宿主**（模拟崩溃），再检查谁还活着。
对照组 WB_RUNNER_NO_JOB=1：不挂 Job 时应当有进程存活 —— 证明演练有区分力。

跑法：webapp/.venv/Scripts/python.exe tests/runner_job.test.py
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HARNESS = TESTS / "_job_drill_harness.py"


def _alive(pid: int) -> bool:
    """只读探测：不向目标进程发信号。"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259            # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _taskkill(pid: int, tree: bool = True) -> None:
    argv = ["taskkill", "/F", "/PID", str(pid)]
    if tree:
        argv.insert(1, "/T")
    subprocess.run(argv, capture_output=True)


class RunnerJobObjectTests(unittest.TestCase):
    def _drill(self, disable_job: bool, mode: str = "child",
               env_extra: dict | None = None) -> dict:
        """起宿主 → 读 pid → 强杀宿主 → 等进程消失。返回 info + 存活状态。

        返回 dict：{info, child_alive, grandchild_alive, tmp}
        """
        tmp = tempfile.mkdtemp(prefix="job_drill_")
        env = dict(os.environ)
        if disable_job:
            env["WB_RUNNER_NO_JOB"] = "1"
        else:
            env.pop("WB_RUNNER_NO_JOB", None)
        env.update(env_extra or {})
        host = subprocess.Popen([sys.executable, str(HARNESS), tmp, mode],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", env=env)
        child = 0
        grandchild = 0
        try:
            line = (host.stdout.readline() or "").strip()
            if not line:                     # 只在失败时读 stderr，否则会阻塞在未 EOF 的管道上
                self.fail("宿主没有输出 pid：\n" + (host.stderr.read() or "")[:800])
            info = json.loads(line)
            child = int(info["child"])
            grandchild = int(info.get("grandchild") or 0)
            self.assertEqual(info["job"], not disable_job, "Job 句柄创建状态与预期不符")
            if not disable_job:
                self.assertTrue(info["assigned"], "子进程没有被加入 Job")
                self.assertTrue(info["in_job"], "子进程不在 Job 里")
                self.assertTrue(info.get("job_name"), "命名 Job 名应落进 journal 供重试门禁查询")
            self.assertTrue(_alive(child), "宿主存活期间子进程本应在跑")
            if mode == "tree":
                self.assertTrue(grandchild, "孙进程没被创建（夹具前提不成立）")
                self.assertTrue(_alive(grandchild), "宿主存活期间孙进程本应在跑")
                if not disable_job:
                    self.assertTrue(info["grandchild_in_job"],
                                    f"孙进程 {grandchild} 不在 Job 里 → 仍会变成孤儿")
            # venv 的 python.exe 只是启动器，真正跑 harness 的是它拉起的 base python；
            # 只强杀真正持有 Job 句柄的那个进程，才等价于「webapp 崩溃」。
            real_host = int(info["host"])
            _taskkill(real_host, tree=False)
            host.wait(timeout=15)
            child_alive = True
            grandchild_alive = True
            for _ in range(100):             # 最多等 10s
                if not _alive(child):
                    child_alive = False
                if not grandchild or not _alive(grandchild):
                    grandchild_alive = False
                if not child_alive and not grandchild_alive:
                    break
                time.sleep(0.1)
            return {"info": info, "child_alive": child_alive,
                    "grandchild_alive": grandchild_alive, "tmp": tmp}
        finally:
            if host.poll() is None:
                host.kill()
            for stream in (host.stdout, host.stderr):
                try:
                    stream.close()
                except Exception:            # noqa: BLE001
                    pass
            if child and _alive(child):
                _taskkill(child)             # 兜底，别把孤儿留在机器上
            if grandchild and _alive(grandchild):
                _taskkill(grandchild, tree=False)

    # ── 父子两代（原有回归，保留）────────────────────────────────
    def test_child_tree_dies_with_host(self):
        r = self._drill(disable_job=False, mode="child")
        self.assertFalse(r["child_alive"],
                         f"宿主被杀后子进程 {r['info']['child']} 仍存活 → Job 没起作用")

    def test_control_without_job_child_survives(self):
        r = self._drill(disable_job=True, mode="child")
        self.assertTrue(r["child_alive"],
                        "对照组：不挂 Job 时子进程应当存活，否则本演练没有区分力")

    # ── P1-R2：孙进程 ─────────────────────────────────────────────
    def test_grandchild_is_in_job(self):
        """子任务快速创建孙进程：孙进程必须在同一棵 Job 里（P1-R2 的核心）。"""
        r = self._drill(disable_job=False, mode="tree")
        self.assertTrue(r["info"]["grandchild_in_job"],
                        "孙进程没有进入 Job，服务被杀后会变成孤儿")

    def test_grandchild_tree_dies_with_host(self):
        """整棵树回收：宿主被杀后子进程与孙进程都不得存活。"""
        r = self._drill(disable_job=False, mode="tree")
        self.assertFalse(r["child_alive"], "宿主被杀后子进程仍存活")
        self.assertFalse(r["grandchild_alive"],
                         f"宿主被杀后孙进程 {r['info']['grandchild']} 仍存活 → P1-R2 未闭环")

    def test_control_without_job_grandchild_survives(self):
        """对照组：不挂 Job 时孙进程确实会存活 —— 证明「整棵树回收」这条断言有区分力。"""
        r = self._drill(disable_job=True, mode="tree")
        self.assertTrue(r["grandchild_alive"],
                        "对照组：不挂 Job 时孙进程应当存活，否则本演练没有区分力")

    def test_business_code_does_not_run_before_job_assign(self):
        """挂起创建 → 入 Job → 恢复：入 Job 之前子进程一行业务代码都不能执行。

        夹具：宿主把第一次 journal 写入拖慢 1.5s（WB_HARNESS_SLOW_STORE）。旧顺序
        Popen→update→Assign 会把这 1.5s 变成「子进程已经在跑」的窗口；新顺序
        Popen(挂起)→Assign→update→恢复，同一段时间里 marker 不会出现。
        """
        r = self._drill(disable_job=False, mode="tree",
                        env_extra={"WB_HARNESS_SLOW_STORE": "1.5"})
        self.assertIs(r["info"]["marker_during_window"], False,
                      "入 Job 之前子进程就已经执行了业务代码（挂起创建未生效）")
        self.assertTrue(r["info"]["child_started"], "恢复执行后子进程应正常开跑")
        self.assertFalse(r["child_alive"], "宿主被杀后子进程仍存活")
        self.assertFalse(r["grandchild_alive"], "宿主被杀后孙进程仍存活")


if __name__ == "__main__":
    unittest.main(verbosity=2)
