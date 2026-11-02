# -*- coding: utf-8 -*-
"""子进程任务管理器：后台跑 pipeline / daily_batch / 脚本，
逐行捕获 stdout+stderr，支持实时订阅（SSE）与终止。"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import config
import store

MAX_LINES = 4000

# ────────────────────── 共享写者资源锁（2026-10-24 R1）──────────────────────
# 问题（独立验收 R1）：修复启动时只**检查**已有 pipeline/daily/ledger，但
#   · 「待接管 → 重跑全部」走 pipeline_ops.start_retry → MANAGER.start(kind="daily")
#     完全不看修复会话，修复与日更会同时写同一本台账；
#   · 两个入口各自「先检查后启动」，同时点就是竞态。
# 现在把互斥下沉到共享执行层：谁要写台账，就必须在 `RunManager.start` 里
# 原子地拿到 `ledger` 资源租约。检查与获取在同一把锁内完成，没有窗口。
#
# 约束：**不能**简单封死所有 runner.start —— 修复会话自己的子任务（kind=repair）
# 必须能跑。做法是租约令牌（lease）：会话先持有一个令牌，它的子任务用
# `owner=<令牌>` 加入同一租约；其它入口拿不到令牌就会被拒绝。
LEDGER = "ledger"

# kind → 需要的共享写者资源。只列真正会写台账/回执的入口；
# 爆品监控/脚本/逆向等读写各自产物，不参与台账互斥。
KIND_RESOURCES: dict[str, tuple[str, ...]] = {
    "pipeline": (LEDGER,),
    "daily": (LEDGER,),
    "ledger": (LEDGER,),
    "repair": (LEDGER,),
}


class ResourceBusy(ValueError):
    """共享写者被占用。继承 ValueError，路由层既可 400 也可单独识别。"""

    def __init__(self, message: str, holders: list[dict] | None = None):
        super().__init__(message)
        self.holders = holders or []


class ResourceLock:
    """共享资源租约表（线程安全）。一个资源同时只允许一个租约持有者。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leases: dict[str, dict] = {}      # token -> {resources, kind, title, started_at}
        self._owners: dict[str, str] = {}       # resource -> token
        self._release_hooks: list = []

    def on_release(self, fn) -> None:
        """租约释放后的回调（用于把被挡下的排队任务接上，避免静默丢失）。"""
        with self._lock:
            self._release_hooks.append(fn)

    def _holder_rows(self, resources: tuple[str, ...]) -> list[dict]:
        rows = []
        for res in resources:
            token = self._owners.get(res)
            lease = self._leases.get(token or "")
            if not lease:
                continue
            rows.append({"resource": res, "kind": lease.get("kind") or "",
                         "title": lease.get("title") or "",
                         "since": lease.get("started_at") or 0})
        return rows

    def acquire(self, resources, *, owner: str = "", kind: str = "",
                title: str = "") -> str:
        """原子获取一组资源；被占用抛 ResourceBusy。

        owner 已持有全部资源时直接返回该令牌（修复内部的预期子任务）。
        """
        resources = tuple(sorted({str(r) for r in (resources or ()) if r}))
        if not resources:
            return ""
        with self._lock:
            if owner:
                if all(self._owners.get(r) == owner for r in resources):
                    return owner
                raise ResourceBusy(
                    f"租约 {owner} 不持有资源 {','.join(resources)}（内部子任务必须继承会话租约）",
                    self._holder_rows(resources))
            if any(r in self._owners for r in resources):
                rows = self._holder_rows(resources)
                names = "、".join(f"{r.get('kind') or '任务'}「{r.get('title') or ''}」"
                                  for r in rows)
                raise ResourceBusy(f"有任务正在写同一本台账（{names}），"
                                   f"请等它结束再启动", rows)
            token = uuid.uuid4().hex[:12]
            self._leases[token] = {"resources": resources, "kind": kind,
                                   "title": title, "started_at": time.time()}
            for r in resources:
                self._owners[r] = token
            return token

    def owns(self, token: str, resources) -> bool:
        resources = [str(r) for r in (resources or ()) if r]
        if not resources:
            return True
        with self._lock:
            return bool(token) and all(self._owners.get(r) == token for r in resources)

    def holders(self, resources=(LEDGER,)) -> list[dict]:
        with self._lock:
            return self._holder_rows(tuple(resources))

    def release(self, token: str) -> bool:
        """释放租约；释放后触发 on_release 回调（在锁外，允许回调再取租约）。"""
        if not token:
            return False
        with self._lock:
            lease = self._leases.pop(token, None)
            if not lease:
                return False
            for r in lease["resources"]:
                if self._owners.get(r) == token:
                    self._owners.pop(r, None)
            hooks = list(self._release_hooks)
        for fn in hooks:
            try:
                fn(lease)
            except Exception:                # noqa: BLE001  回调不该影响释放本身
                pass
        return True


RESOURCES = ResourceLock()

# ────────────────────── 子进程 Job Object（2026-10-21）──────────────────────
# P1-2 的根因之一：webapp 用普通 Popen，服务被强杀时 pipeline 子进程会变成孤儿，
# 继续往台账/抓取产物里写，而新进程看到的 journal 只是「interrupted」。
# 这里给所有由本服务拉起的子进程挂一个 kill-on-close 的 Job：
#   · 服务正常退出或崩溃 → 最后一个句柄关闭 → 整棵子进程树被系统回收
#   · 保留 BREAKAWAY_OK → 显式传 CREATE_BREAKAWAY_FROM_JOB 的子进程（daily_batch --detach）
#     仍能按设计脱离父进程组
#
# ★ 2026-10-21 独立验收 P1-R2「先启动、后入 Job 的窗口」：
#   旧顺序是 Popen(立即运行) → 落 pid → 建 Job → Assign。子进程在入 Job 前已经能创建
#   孙进程，这些孙进程不会随后自动补入 Job，服务被杀后它们变成孤儿，而 journal 只记父
#   pid（已退出）→ daily_batch 判定「可以重试」→ 同账号两个 run 并行。
#   现在的顺序是 Popen(CREATE_SUSPENDED) → 建**本轮命名** Job → Assign → NtResumeProcess：
#   业务代码的第一条指令发生在「已经在 Job 里」之后，后代（含孙进程）必然同属一棵树。
#   命名 Job（WB_RUN_<run_id>）同时让 daily_batch 能跨进程查「这棵树还有几个活进程」
#   作为重试门禁（见 pipeline/scripts/daily_batch._job_active_processes）。
# 创建失败（非 Windows / 权限受限 / 命名冲突）一律退化回普通 Popen，不影响任务本身。
_JOB_HANDLE = None
_JOB_TRIED = False
_JOB_LOCK = threading.Lock()
RUN_JOB_PREFIX = "WB_RUN_"          # Local 命名空间：同会话内可被其它进程打开查询
CREATE_SUSPENDED = 0x00000004


def _create_kill_on_close_job(name: str | None = None):
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimit),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD)
    job = k32.CreateJobObjectW(None, name)
    if not job:
        return None
    info = _ExtLimit()
    KILL_ON_JOB_CLOSE = 0x00002000
    BREAKAWAY_OK = 0x00000800
    info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return job


def _job_handle():
    """惰性创建（并缓存）kill-on-close Job；不可用时返回 None。"""
    global _JOB_HANDLE, _JOB_TRIED
    if os.name != "nt" or os.environ.get("WB_RUNNER_NO_JOB") == "1":
        return None
    with _JOB_LOCK:
        if not _JOB_TRIED:
            _JOB_TRIED = True
            try:
                _JOB_HANDLE = _create_kill_on_close_job()
            except Exception:            # noqa: BLE001  Job 不可用就退化为普通 Popen
                _JOB_HANDLE = None
        return _JOB_HANDLE


def _run_job(run_id: str):
    """为本轮创建命名 Job：返回 (handle, name)。命名失败则退回共享无名 Job。"""
    if os.name != "nt" or os.environ.get("WB_RUNNER_NO_JOB") == "1":
        return None, None
    name = f"{RUN_JOB_PREFIX}{run_id}"
    try:
        job = _create_kill_on_close_job(name)
    except Exception:                    # noqa: BLE001
        job = None
    if job:
        return job, name
    return _job_handle(), None


def _assign_to_job(job, proc) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        return bool(k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))))
    except Exception:                    # noqa: BLE001
        return False


def _resume_process(proc) -> bool:
    """把 CREATE_SUSPENDED 创建的子进程恢复执行（Popen 不暴露主线程句柄，用整进程恢复）。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        status = ntdll.NtResumeProcess(wintypes.HANDLE(int(proc._handle)))
        return status == 0
    except Exception:                    # noqa: BLE001
        return False


def _terminate_job(job) -> bool:
    """整棵树终止（比 taskkill /T 更严：Job 里所有后代，含父进程已退出后的遗留孙进程）。"""
    if not job:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        return bool(k32.TerminateJobObject(wintypes.HANDLE(job), 1))
    except Exception:                    # noqa: BLE001
        return False


def _close_handle(handle) -> None:
    if not handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:                    # noqa: BLE001
        pass


@dataclass
class Run:
    id: str
    kind: str
    title: str
    cmd: list[str]
    cwd: str
    account: str = ""
    status: str = "running"          # running | done | failed | stopped
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    lines: list[dict] = field(default_factory=list)
    line_count: int = 0
    subscribers: list[queue.Queue] = field(default_factory=list)
    proc: subprocess.Popen | None = None
    job: object = None               # 本轮命名 Job 句柄（P1-R2）
    job_name: str | None = None      # 落进 journal，daily_batch 用它查整棵树
    job_assigned: bool = False
    lease: str = ""                  # 共享写者租约令牌（R1）
    owns_lease: bool = False         # True = 本 run 拿到的租约，终态时由它释放
    state_lock: object = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "cmd": " ".join(self.cmd),
            "cwd": self.cwd,
            "account": self.account,
            "status": self.status,
            "exit_code": self.exit_code,
            # 2026-10-31 修复 P2：/api/runs 列表一直有 pid（来自 runs.jsonl），
            # 但详情/启动响应没有，排障时看不到「这个 run 对应哪个进程」。
            "pid": (self.proc.pid if self.proc is not None else None),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round((self.ended_at or time.time()) - self.started_at, 1),
            "line_count": self.line_count,
            "tail": [l["text"] for l in self.lines[-20:]],
        }


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        # 运行终态钩子（2026-10-15）：跑完自动入台账等后续动作在此挂接
        self._finish_hooks: list = []

    def add_finish_hook(self, fn) -> None:
        """注册终态钩子：每次运行进入终态后调用 fn(run)；单个钩子异常不影响其它钩子。"""
        self._finish_hooks.append(fn)

    # ── 查询 ────────────────────────────────────────────────────────
    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self, limit: int = 50) -> list[dict]:
        rows = sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)
        return [r.snapshot() for r in rows[:limit]]

    def running(self) -> list[dict]:
        """全部运行中任务 —— 不受历史窗口限制。

        ★ 2026-10-20 验收 P2-2：list(limit=N) 是「先按启动时间取最新 N 条，再过滤
          running」。一条早起的长任务会被后续新增的 done 记录挤出窗口，导致
          overview 把仍在抓取的账号判成 running=false。运行态必须用本方法。
        """
        rows = sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)
        return [r.snapshot() for r in rows if r.status == "running"]

    def all(self) -> list[dict]:
        """本进程内全部任务快照（不再被 limit 截断），/api/runs 合并历史时用。"""
        rows = sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)
        return [r.snapshot() for r in rows]

    def lines(self, run_id: str, since: int = 0) -> list[dict]:
        r = self._runs.get(run_id)
        return [line for line in r.lines if line["i"] >= since] if r else []

    # ── 启动 ────────────────────────────────────────────────────────
    def start(self, kind: str, title: str, cmd: list[str], cwd: str | None = None,
              account: str = "", owner: str = "") -> Run:
        """启动子进程任务。

        写台账的 kind（pipeline/daily/ledger/repair）必须原子拿到 `ledger` 租约，
        否则抛 ResourceBusy（路由层转 400）。owner 用于修复会话把自己的子任务
        接进同一租约 —— 既保证「唯一写者」，又不会把修复内部步骤挡在外面。
        """
        resources = KIND_RESOURCES.get(kind, ())
        lease, owns_lease = "", False
        if resources:
            lease = RESOURCES.acquire(resources, owner=owner, kind=kind, title=title)
            owns_lease = not owner
        run = Run(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            title=title,
            cmd=cmd,
            cwd=cwd or str(config.PIPELINE_DIR),
            account=account,
            lease=lease,
            owns_lease=owns_lease,
        )
        try:
            with self._lock:
                self._runs[run.id] = run
            store.append({
                "id": run.id, "kind": kind, "title": title,
                "account": account, "cmd": " ".join(cmd),
                "started_at": run.started_at, "status": "running",
            })
        except Exception:
            # 登记失败不能把租约留在手里，否则整个台账写入被永久挡住
            if owns_lease:
                RESOURCES.release(lease)
            raise
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        return run

    def _child_env(self, run: "Run") -> dict:
        """子进程环境：在 config.run_env() 基础上处理租约下传。

        ★ 2026-10-24（B）：修复会话的内部子任务（owner=租约 → owns_lease=False）必须把
          租约令牌下传给子进程树。因为 collect 项的 cmd 是 `daily_batch.py`，它会在
          **子进程里再 POST /api/run**；不带令牌时那次申请会被修复自己持有的租约挡回
          （实测 daily_batch 直接拿到 HTTP 500，2.9s 退出，一键修复的 collect 项永远起不来）。

        ★ 2026-10-31：顶层 run 同样下传**自己的**租约令牌。「待接管重跑」（pipeline_ops
          .start_retry）以顶层 daily 启动并自己持有 ledger 租约，daily_batch 在子进程里
          逐账号 POST /api/run kind=pipeline —— 不带令牌时被**自己父任务**的租约挡回
          （实测 400「有任务正在写同一本台账」，daily_batch 每次尝试死等 1200s，重试 2 次
          共 ~40 分钟后放弃，10-30 22:53 example_account_022 实录）。顶层 run 持有的只可能是它
          自己 acquire 的租约，下传自己的令牌不构成越权；无租约的 run（script/reverse 等）
          仍然 pop 掉继承来的脏值，防伪造语义不变。
        """
        env = config.run_env()
        if run.lease:
            env[config.LEASE_ENV] = run.lease
        else:
            env.pop(config.LEASE_ENV, None)
        return env

    def _pump(self, run: Run) -> None:
        try:
            with run.state_lock:
                if run.status != "running":
                    return  # 启动线程尚未执行时，用户已取消。
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt":
                    # ★ P1-R2：先挂起创建，入 Job 成功后再恢复执行（见文件头说明）
                    flags |= CREATE_SUSPENDED
                run.proc = subprocess.Popen(
                    run.cmd, cwd=run.cwd, env=self._child_env(run),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=flags,
                )
        except Exception as e:  # 启动即失败（解释器/脚本不存在）
            self._emit(run, f"[启动失败] {e}")
            self._finish(run, "failed", -1)
            return

        # ★ 2026-10-21 独立验收 P1-R2：入 Job 在恢复执行之前完成；命名 Job 名落进 journal，
        #   让 daily_batch 能跨进程核实「这棵树是否真的没进程了」再决定要不要重试。
        job, job_name = _run_job(run.id)
        run.job, run.job_name = job, job_name
        run.job_assigned = bool(job) and _assign_to_job(job, run.proc)
        try:
            store.update(run.id, {"pid": run.proc.pid, "job_name": job_name,
                                  "job_assigned": run.job_assigned})
        except Exception:                    # 记账失败不能影响任务本身
            pass
        if not run.job_assigned:
            self._emit(run, "[warn] 子进程未能加入 Job，进程树不会随服务退出")

        if os.name == "nt" and not _resume_process(run.proc):
            # 恢复不了就等于任务永远跑不起来：回收后按启动失败处理，不留挂起进程
            self._emit(run, "[启动失败] 子进程无法从挂起态恢复，已回收")
            self._kill_tree(run)
            run.job_assigned = False
            try:
                if run.proc.stdout is not None:
                    run.proc.stdout.close()
            except Exception:                # noqa: BLE001
                pass
            self._finish(run, "failed", -1)
            return

        assert run.proc.stdout is not None
        with run.proc.stdout:
            for line in run.proc.stdout:
                self._emit(run, line.rstrip("\r\n"))
        code = run.proc.wait()
        self._emit(run, f"[进程退出] exit_code={code}")
        self._finish(run, "done" if code == 0 else "failed", code)

    def _kill_tree(self, run: Run) -> None:
        """终止整棵树：先 Job（覆盖父进程已退出的遗留孙进程），再 taskkill /T 兜底。"""
        if run.job and run.job_assigned and _terminate_job(run.job):
            deadline = time.time() + 5
            while time.time() < deadline:
                if run.proc is None or run.proc.poll() is not None:
                    return
                time.sleep(0.1)
        if run.proc is not None and run.proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(run.proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:                # noqa: BLE001
                pass

    def _emit(self, run: Run, text: str) -> None:
        item = {"i": run.line_count, "t": round(time.time() - run.started_at, 1), "text": text}
        run.line_count += 1
        run.lines.append(item)
        if len(run.lines) > MAX_LINES:
            del run.lines[: len(run.lines) - MAX_LINES]
        for q in list(run.subscribers):
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _finish(self, run: Run, status: str, code: int) -> None:
        owned = ""
        with run.state_lock:
            if run.status != "running":
                return  # 幂等：stop() 已置 stopped 时，_pump 的进程退出回调不再覆盖
            run.status = status
            run.exit_code = code
            run.ended_at = time.time()
            if run.owns_lease:
                owned, run.owns_lease = run.lease, False
        # ★ R1：进程已经退出（_pump 在 wait() 之后才调到这里），先释放写者租约，
        #   随后的终态钩子（自动入账 / 自动翻译）才能拿租约启动后续任务。
        if owned:
            RESOURCES.release(owned)
        for q in list(run.subscribers):
            try:
                q.put_nowait({"done": True, "status": status, "exit_code": code})
            except queue.Full:
                pass
        store.update(run.id, {
            "status": status, "exit_code": code,
            "ended_at": run.ended_at,
            "duration_s": round((run.ended_at or 0) - run.started_at, 1),
        })
        for fn in list(self._finish_hooks):
            try:
                fn(run)
            except Exception as e:  # 钩子失败不能吞掉运行记录本身
                try:
                    self._emit(run, f"[钩子异常] {type(e).__name__}: {e}")
                except Exception:
                    pass
        # 命名 Job 句柄随本轮结束关闭：句柄关闭 = kill-on-close 生效，
        # 本轮遗留的任何后代（含孙进程）会被系统一并回收，不会跨轮存活。
        if run.job_name and run.job:
            _close_handle(run.job)
            run.job = None
            run.job_assigned = False

    # ── 终止 ────────────────────────────────────────────────────────
    def stop(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if not run:
            return False
        with run.state_lock:
            if run.status != "running":
                return False
            if run.proc:
                if run.proc.poll() is not None:
                    return False
                # ★ P1-R2：Job 里可能还有父进程已退出后遗留的孙进程，taskkill /T 沿着
                #   父链找不到它们；先整 Job 终止，再退回 taskkill /T。
                self._kill_tree(run)
                if run.proc.poll() is None:
                    self._emit(run, "[终止失败] 进程树未能终止，请重试")
                    return False
            self._emit(run, "[用户终止]")
            self._finish(run, "stopped", -9)
            return True

    def subscribe(self, run_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10000)
        run = self._runs.get(run_id)
        if run:
            run.subscribers.append(q)
        return q

    def unsubscribe(self, run_id: str, q: queue.Queue) -> None:
        run = self._runs.get(run_id)
        if run and q in run.subscribers:
            run.subscribers.remove(q)


MANAGER = RunManager()
