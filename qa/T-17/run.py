# -*- coding: utf-8 -*-
r"""qa/T-17/run.py —— T-17「首次运行初始化 first_run.py --portable」的独立 QA 用例

用例清单来源：`.dsh-crew/jobs/portable-package/M1-qa-plan.md` 的 T-17 段（C-01 ~ C-11，逐条照写）。
DoD 来源：`docs/tasks/tasks.md` 里 T-17 的 6 条（v2）。

隔离与安全边界（Lead 硬约束）：
  · 本脚本只写 `qa\T-17\` 与 tempfile 目录；**绝不改产品代码**（first_run.py 只读，结束时校验 sha256 未变）；
  · 所有「台账 / accounts.json 初始化」用例都把 first_run.py **逐字节复制**到临时包根，再在临时目录里跑，
    不碰仓库的 `ledger\` 与 `pipeline\accounts.json`；
  · 跑完（C-10 第二半）对仓库 `ledger\*.xlsx`、`pipeline\accounts.json`、`first_run.py` 做
    sha256 + mtime 断言，证明它们一个字节都没被这次 QA 碰过；
  · 不联网、不起服务、不占 8789；代理一律指向 `http://127.0.0.1:9`（不打任何真实请求）。

跑法（仓库根）：
    pipeline\.venv\Scripts\python.exe qa\T-17\run.py
全通过退出码 0；任何一条不通过退出码 1（不 SKIP，没有「跳过算过」的分支）。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

# ── 路径：全部按本文件位置推，与 cwd 无关 ─────────────────────────────────
HERE = Path(__file__).resolve()
QA_DIR = HERE.parent                      # ...\qa\T-17
REPO = QA_DIR.parents[1]                  # 仓库根
WEBAPP = REPO / "webapp"
FR = REPO / "first_run.py"                # 产品文件：只读
LEDGER = REPO / "ledger"
ACCOUNTS = REPO / "pipeline" / "accounts.json"
PY = sys.executable                       # 本脚本自身解释器（pipeline venv）
WEBAPP_PY = WEBAPP / ".venv" / "Scripts" / "python.exe"

TMP_ROOT = Path(tempfile.mkdtemp(prefix="qa_t17_"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass

_ok = 0
_fail = 0
W: dict = {}                              # 用例之间共享的工作区（C-02 → C-03/C-04/C-05）

_KEEP_ENV = (
    "SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "USERNAME", "USERDOMAIN", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "ProgramData", "ProgramFiles", "ProgramFiles(x86)",
    "CommonProgramFiles", "CommonProgramFiles(x86)", "OS", "PATH",
)


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stat_of(path: Path):
    """(sha256, mtime_ns, size)；文件不存在返回 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return {"kind": "not-a-file"}
    return {"sha": sha256(path), "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def repo_snapshot() -> dict:
    """真实仓库里「本轮绝不该被碰」的东西：账号、台账、产品文件。"""
    snap = {"files": {}, "ledger_names": []}
    snap["files"]["pipeline\\accounts.json"] = stat_of(ACCOUNTS)
    snap["files"]["first_run.py"] = stat_of(FR)
    for p in sorted(LEDGER.glob("*.xlsx")):
        snap["files"]["ledger\\" + p.name] = stat_of(p)
    try:
        snap["ledger_names"] = sorted(x.name for x in LEDGER.iterdir())
    except OSError:
        snap["ledger_names"] = []
    return snap


def _short_sha(s) -> str:
    if not isinstance(s, dict):
        return "缺失"
    sha = str(s.get("sha") or "")
    return (sha[:12] + "…") if sha else "非文件"


def _min_path() -> str:
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    return sysroot + r"\system32;" + sysroot


def clean_env(extra=None, path=None) -> dict:
    """干净子进程环境：清掉 WB_*/HF_*/XDG/PYTHON*，PATH 可控（默认剥到 system32）。"""
    env = {k: v for k, v in os.environ.items() if k in _KEEP_ENV}
    for k in list(env):
        ku = k.upper()
        if ku.startswith(("WB_", "HF_")) or ku in ("XDG_CACHE_HOME", "PYTHONPATH", "PYTHONHOME"):
            env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = path if path is not None else _min_path()
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def run_fr(pkg: Path, args, env, timeout: int = 240):
    """跑临时包里的 first_run.py（副本，不是仓库原件）。返回 (rc, 输出, 秒)。"""
    cmd = [PY, str(pkg / "first_run.py")] + [str(a) for a in args]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(pkg), capture_output=True, timeout=timeout, env=env)
    elapsed = time.perf_counter() - t0
    out = ((p.stdout or b"").decode("utf-8", "replace")
           + (p.stderr or b"").decode("utf-8", "replace"))
    return p.returncode, out, elapsed


def make_pkg(root: Path, with_example: bool = True, webapp_as_file: bool = False) -> Path:
    """在 temp 里造一个最小「包根」：first_run.py 副本 + pipeline/ + webapp/。

    副本与仓库原件逐字节一致（sha256 断言），所以测的是同一份代码，但 `_HERE` 落在 temp，
    仓库的 ledger/accounts 从路径上就够不着。
    """
    pkg = root / "pkg"
    (pkg / "pipeline").mkdir(parents=True)
    shutil.copy2(FR, pkg / "first_run.py")
    assert_(sha256(pkg / "first_run.py") == sha256(FR),
            "复制出来的 first_run.py 与仓库原件不一致")
    if with_example:
        shutil.copy2(REPO / "pipeline" / "accounts.example.json",
                     pkg / "pipeline" / "accounts.example.json")
    if webapp_as_file:
        (pkg / "webapp").write_text("这不是目录", encoding="utf-8")
    else:
        (pkg / "webapp").mkdir()
    return pkg


def load_fr_module(path: Path, name: str):
    """按文件路径 import 一份 first_run（用于进程内打桩，不写 sys.modules 的真实名）。"""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def expected_cols() -> list:
    """从 webapp\\config.py 的源码里取 EXPECTED_COLS（AST 解析，不 import —— import 会做代理探测/mkdir）。"""
    tree = ast.parse((WEBAPP / "config.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "EXPECTED_COLS" for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError("webapp\\config.py 里找不到 EXPECTED_COLS 赋值")


def fail_lines(out: str) -> list:
    return [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]


def _tail(out: str, n: int = 900) -> str:
    return out[-n:]


# ══════════════════════════════════════════════════════════════════════
# C-01 首次运行单测通过（复用工程师单测，DoD 1 的入口）
# ══════════════════════════════════════════════════════════════════════
def c01():
    assert_(WEBAPP_PY.is_file(), f"找不到 webapp 解释器：{WEBAPP_PY}")
    # DoD 1 的命令就是在仓库里按当前环境跑它；不剥环境（剥了反而可能踩系统 Winsock 的抖动）
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.perf_counter()
    p = subprocess.run([str(WEBAPP_PY), str(WEBAPP / "tests" / "first_run_portable.test.py")],
                       cwd=str(REPO), capture_output=True, timeout=900, env=env)
    elapsed = time.perf_counter() - t0
    out = ((p.stdout or b"").decode("utf-8", "replace")
           + (p.stderr or b"").decode("utf-8", "replace"))
    assert_(p.returncode == 0, f"退出码 {p.returncode}（期望 0）；输出尾：{_tail(out)}")
    assert_("Traceback" not in out, f"输出里有 Traceback：{_tail(out)}")
    summ = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("通过 ")]
    assert_(summ, f"没找到「通过 N / M」汇总行；输出尾：{_tail(out)}")
    ev = [f"exit=0，{elapsed:.1f}s", f"单测汇总：{summ[-1]}"]
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("✓") or s.startswith("✗"):
            ev.append(s[:150])
    return ev


# ══════════════════════════════════════════════════════════════════════
# C-02 14 列台账模板，表头逐字等于 config.EXPECTED_COLS（DoD 1(a)）
# ══════════════════════════════════════════════════════════════════════
def c02():
    t = TMP_ROOT / "c02"
    t.mkdir()
    pkg = make_pkg(t)
    pipe, ledger = pkg / "pipeline", pkg / "ledger"
    env = clean_env(extra={"WB_LOG_DIR": t / "logs"})
    rc, out, elapsed = run_fr(
        pkg, ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger], env)
    W["c02"] = {"pkg": pkg, "pipe": pipe, "ledger": ledger, "rc": rc, "out": out, "env": env}
    assert_(rc == 0, f"退出码 {rc}（期望 0）；输出：{_tail(out)}")
    assert_("[FAIL]" not in out, f"--init-only 不许出现 [FAIL]：{_tail(out)}")

    xlsx = ledger / "tiktok_对标.xlsx"
    assert_(xlsx.is_file(), f"没生成台账：{xlsx}（输出：{_tail(out)}）")

    import openpyxl                    # 只有这里需要；pipeline venv 里 3.1.5
    wb = openpyxl.load_workbook(str(xlsx))
    try:
        ws = wb.active
        header = [c.value for c in ws[1]][:14]
        sheets = list(wb.sheetnames)
    finally:
        wb.close()

    exp = expected_cols()
    assert_(len(header) == 14, f"表头不是 14 列，实得 {len(header)}：{header}")
    assert_(header == exp,
            "表头与 webapp\\config.py:EXPECTED_COLS 不一致：\n"
            f"  实得：{header}\n  期望：{exp}")
    return [f"exit=0，{elapsed:.2f}s",
            f"台账：{xlsx.name}（{xlsx.stat().st_size} B）",
            f"sheet：{sheets}",
            f"表头（{len(header)} 列）：{header}"]


# ══════════════════════════════════════════════════════════════════════
# C-03 accounts.json 生成正确（DoD 1(b)）
# ══════════════════════════════════════════════════════════════════════
def c03():
    st = W.get("c02")
    assert_(st, "C-02 没跑成，无法验 accounts.json")
    pipe, ledger = st["pipe"], st["ledger"]
    acc_path = pipe / "accounts.json"
    assert_(acc_path.is_file(), f"没生成 accounts.json：{acc_path}")
    cfg = json.loads(acc_path.read_text(encoding="utf-8"))
    accs = cfg.get("accounts") or []
    assert_(accs, f"accounts.json 里没有示例账号：{cfg.keys()}")
    assert_(all(a.get("active") is False for a in accs),
            f"示例账号必须全部 active=false，实得 {[a.get('active') for a in accs]}")
    xlsx = Path(str(cfg.get("xlsx") or ""))
    assert_(xlsx.resolve() == (ledger / "tiktok_对标.xlsx").resolve(),
            f"xlsx 没指向本次台账：{cfg.get('xlsx')!r} != {str(ledger / 'tiktok_对标.xlsx')!r}")
    assert_(str(cfg.get("metrics_file") or "").startswith(str(pipe)),
            f"metrics_file 没落在本次 pipeline 目录：{cfg.get('metrics_file')!r}")
    assert_(str(cfg.get("reports_root") or "").startswith(str(pipe)),
            f"reports_root 没落在本次 pipeline 目录：{cfg.get('reports_root')!r}")
    assert_(all(Path(str(a.get("out_dir") or "")).resolve() == (pipe / "output").resolve()
                for a in accs),
            f"示例账号的 out_dir 没指向本次 pipeline\\output：{[a.get('out_dir') for a in accs]}")
    ev = [f"账号数：{len(accs)}，全部 active=false",
          f"xlsx：{cfg.get('xlsx')}",
          f"metrics_file：{cfg.get('metrics_file')}",
          f"reports_root：{cfg.get('reports_root')}"]
    for a in accs[:3]:
        ev.append(f"  ({a.get('account')!r}, active={a.get('active')}, "
                  f"out_dir={a.get('out_dir')!r})")
    return ev


# ══════════════════════════════════════════════════════════════════════
# C-04 二次运行不覆盖（DoD 1(c)）
# ══════════════════════════════════════════════════════════════════════
def c04():
    st = W.get("c02")
    assert_(st, "C-02 没跑成，无法验二次运行")
    pkg, pipe, ledger, env = st["pkg"], st["pipe"], st["ledger"], st["env"]
    xlsx = ledger / "tiktok_对标.xlsx"
    acc_path = pipe / "accounts.json"

    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx))
    try:
        wb.active["A2"] = "QA 手改的单元格"
        wb.save(str(xlsx))
    finally:
        wb.close()

    cfg = json.loads(acc_path.read_text(encoding="utf-8"))
    cfg.setdefault("accounts", []).append({
        "account": "qa_user_added", "lang": "en", "active": True,
        "out_dir": str(pipe / "output"),
        "audio_dir": str(pipe / "output" / "qa_user_added_audio"),
    })
    acc_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_m, acc_m = xlsx.stat().st_mtime_ns, acc_path.stat().st_mtime_ns
    # 强制第二次走「完整路径」而不是快路径：删掉一个快路径前置目录（temp 副本里的），
    # 否则 _package_ready() 会直接返回 0，根本不会执行 init_package 的「不覆盖」逻辑。
    shutil.rmtree(pkg / "webapp" / "data", ignore_errors=True)
    rc, out, elapsed = run_fr(
        pkg, ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger], env)
    W["c04"] = {"rc": rc, "out": out, "elapsed": elapsed}
    assert_(rc == 0, f"二次运行退出码 {rc}（期望 0）；输出：{_tail(out)}")

    wb = openpyxl.load_workbook(str(xlsx))
    try:
        kept = wb.active["A2"].value
    finally:
        wb.close()
    assert_(kept == "QA 手改的单元格", f"台账被覆盖了：A2 = {kept!r}")

    cfg2 = json.loads(acc_path.read_text(encoding="utf-8"))
    names = [a.get("account") for a in (cfg2.get("accounts") or [])]
    assert_("qa_user_added" in names, f"accounts.json 被覆盖了（找不到自加账号）：{names}")
    assert_(xlsx.stat().st_mtime_ns == xlsx_m, "台账 mtime 变了：二次运行不该动已有文件")
    assert_(acc_path.stat().st_mtime_ns == acc_m, "accounts.json mtime 变了：二次运行不该动已有文件")
    return [f"二次运行 exit=0，{elapsed:.2f}s",
            "手改单元格 A2 仍在；自加账号 qa_user_added 仍在",
            f"台账/accounts.json mtime 未变（{xlsx_m} / {acc_m}）"]


# ══════════════════════════════════════════════════════════════════════
# C-05 --init-only 两次运行退出码都是 0（DoD 1(d)）
# ══════════════════════════════════════════════════════════════════════
def c05():
    # 独立跑两次（不借用 C-02/C-04 的记录，否则这条只是把别处的断言再念一遍）
    t = TMP_ROOT / "c05"
    t.mkdir()
    pkg = make_pkg(t)
    pipe, ledger = pkg / "pipeline", pkg / "ledger"
    env = clean_env(extra={"WB_LOG_DIR": t / "logs"})
    args = ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger]
    rcs = []
    for i in (1, 2):
        rc, out, elapsed = run_fr(pkg, args, env)
        rcs.append(rc)
        assert_(rc == 0, f"第 {i} 次 --init-only 退出码 {rc}（期望 0）；输出：{_tail(out)}")
    return [f"第一次 exit={rcs[0]}，{elapsed:.2f}s；第二次 exit={rcs[1]}（两次都是 0）"]


# ══════════════════════════════════════════════════════════════════════
# C-06 单次 < 2 秒（DoD 1(e)）
# ══════════════════════════════════════════════════════════════════════
def c06():
    t = TMP_ROOT / "c06"
    t.mkdir()
    pkg = make_pkg(t)
    pipe, ledger = pkg / "pipeline", pkg / "ledger"
    env = clean_env(extra={"WB_LOG_DIR": t / "logs"})
    args = ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger]

    rc, out, cold = run_fr(pkg, args, env)
    assert_(rc == 0, f"首次 --init-only 退出码 {rc}（期望 0）；输出：{_tail(out)}")

    times = []
    for i in range(3):
        rc, out, elapsed = run_fr(pkg, args, env)
        assert_(rc == 0, f"第 {i + 1} 次 --init-only 退出码 {rc}（期望 0）；输出：{_tail(out)}")
        assert_(out.strip() == "", f"第 {i + 1} 次快路径不该有输出，实得 {out[:200]!r}")
        times.append(elapsed)
    judged = max(times)
    assert_(judged < 2.0, f"连续 3 次 --init-only 的最大值 {judged:.3f}s ≥ 2.0s：{times}")
    return [f"首次（建台账，不计判据）：{cold:.3f}s",
            "连续 3 次（判据取最大值）：" + "，".join(f"{x:.3f}s" for x in times),
            f"判定值 max={judged:.3f}s < 2.0s"]


# ══════════════════════════════════════════════════════════════════════
# C-07 不联网：socket / urlopen 打桩成抛异常，仍退出 0、无 [FAIL]、调用 0 次（DoD 2）
# ══════════════════════════════════════════════════════════════════════
def c07():
    t = TMP_ROOT / "c07"
    t.mkdir()
    pkg = make_pkg(t)
    pipe, ledger = pkg / "pipeline", pkg / "ledger"
    mod = load_fr_module(pkg / "first_run.py", "qa_t17_fr_c07")

    counts = {"sock": 0, "url": 0}

    def boom_sock(*_a, **_k):
        counts["sock"] += 1
        raise OSError("stub: 本用例禁止联网")

    def boom_url(*_a, **_k):
        counts["url"] += 1
        raise OSError("stub: 本用例禁止联网")

    # 1) 先证明两个桩真的会拦（否则「计数 0」可能只是桩没装上）—— 证明完把计数清零，
    #    这样后面统计到的才是被测代码自己发的调用。
    with patch.object(socket, "create_connection", boom_sock), \
         patch.object(urllib.request, "urlopen", boom_url):
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        except OSError:
            pass
        else:
            raise AssertionError("桩没生效：socket.create_connection 没抛异常")
        try:
            urllib.request.urlopen("http://127.0.0.1:9")
        except OSError:
            pass
        else:
            raise AssertionError("桩没生效：urllib.request.urlopen 没抛异常")
    counts["sock"] = counts["url"] = 0

    # 2) 再跑被测代码
    buf = io.StringIO()
    with patch.object(socket, "create_connection", boom_sock), \
         patch.object(urllib.request, "urlopen", boom_url), \
         contextlib.redirect_stdout(buf):
        rc = mod.main(["--portable", "--init-only",
                       "--pipeline-dir", str(pipe), "--ledger-dir", str(ledger)])
    out = buf.getvalue()

    assert_(rc == 0, f"退出码 {rc}（期望 0）；输出：{_tail(out)}")
    assert_("[FAIL]" not in out, f"不许出现 [FAIL]：{_tail(out)}")
    assert_(counts["sock"] == 0, f"--init-only 竟调用 socket.create_connection {counts['sock']} 次")
    assert_(counts["url"] == 0, f"--init-only 竟调用 urllib.request.urlopen {counts['url']} 次")
    assert_((pipe / "accounts.json").is_file(), "不联网时也应完成初始化（没生成 accounts.json）")
    assert_((ledger / "tiktok_对标.xlsx").is_file(), "不联网时也应生成台账")
    return [f"exit={rc}；socket 调用 {counts['sock']} 次；urlopen 调用 {counts['url']} 次",
            "输出里 0 条 [FAIL]；台账与 accounts.json 都已生成"]


# ══════════════════════════════════════════════════════════════════════
# C-08 --portable 的 [FAIL] 预算：只有三类，且各恰好一条（DoD 3 的另一半）
# ══════════════════════════════════════════════════════════════════════
def c08():
    ev = []

    def warn_env(t: Path):
        empty = t / "hf_empty"
        home = t / "home_empty"
        empty.mkdir()
        home.mkdir()
        return clean_env(path=_min_path(), extra={
            "WB_PROXY": "http://127.0.0.1:9",   # 显式代理但连不上 → 一条确定的 WARN，不做端口探测
            "HF_HOME": empty, "USERPROFILE": home,
        })

    # 8a 正常便携运行：0 条 [FAIL]；缺 ffmpeg / 模型 / 代理各自 [WARN]；[ OK ] 包内解释器
    t = TMP_ROOT / "c08a"
    t.mkdir()
    pkg = make_pkg(t)
    (pkg / "pipeline" / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "pipeline" / "scripts" / "proxy_detect.py",
                 pkg / "pipeline" / "scripts" / "proxy_detect.py")
    rc, out, elapsed = run_fr(pkg, ["--portable", "--no-network"], warn_env(t))
    assert_(rc == 0, f"8a 正常便携运行退出码 {rc}（缺件应全是 WARN）；输出：{_tail(out)}")
    assert_(not fail_lines(out), f"8a 出现 [FAIL]：{fail_lines(out)}")
    assert_("[ OK ] 包内解释器" in out, f"8a 没有「[ OK ] 包内解释器：…」行：{_tail(out)}")
    assert_("虚拟环境缺失" not in out, "8a 便携模式不许报「虚拟环境缺失」")
    assert_("ffmpeg 不在 PATH" in out, f"8a 缺 ffmpeg 没出 WARN：{_tail(out)}")
    assert_("没找到 faster-whisper 模型" in out, f"8a 缺模型没出 WARN：{_tail(out)}")
    assert_(("端口连不上" in out) or ("没探测到本机代理" in out),
            f"8a 代理不可用没出 WARN：{_tail(out)}")
    ev.append(f"8a 正常便携运行 exit=0，{elapsed:.2f}s，0 条 [FAIL]；"
              f"WARN {len([ln for ln in out.splitlines() if ln.startswith('[WARN]')])} 条")

    # 8b accounts.json 无法解析 → 恰好 1 条 [FAIL]
    t = TMP_ROOT / "c08b"
    t.mkdir()
    pkg = make_pkg(t)
    (pkg / "pipeline" / "accounts.json").write_text("这不是 JSON", encoding="utf-8")
    rc, out, _el = run_fr(pkg, ["--portable", "--no-network"], warn_env(t))
    fl = fail_lines(out)
    assert_(rc == 1, f"8b 退出码 {rc}（期望 1）；输出：{_tail(out)}")
    assert_(len(fl) == 1, f"8b 应恰好 1 条 [FAIL]，实得 {len(fl)}：{fl}")
    assert_("accounts.json 无法解析" in fl[0], f"8b [FAIL] 没点明解析失败：{fl[0]}")
    ev.append(f"8b accounts.json 坏 JSON → exit=1，1 条 [FAIL]：{fl[0][:120]}")

    # 8b2 accounts.json 无法生成（缺 accounts.example.json）→ 恰好 1 条 [FAIL]
    t = TMP_ROOT / "c08b2"
    t.mkdir()
    pkg = make_pkg(t, with_example=False)
    rc, out, _el = run_fr(pkg, ["--portable", "--no-network"], warn_env(t))
    fl = fail_lines(out)
    assert_(rc == 1, f"8b2 退出码 {rc}（期望 1）；输出：{_tail(out)}")
    assert_(len(fl) == 1, f"8b2 应恰好 1 条 [FAIL]，实得 {len(fl)}：{fl}")
    assert_("accounts.example.json" in fl[0], f"8b2 [FAIL] 没点明缺示例文件：{fl[0]}")
    ev.append(f"8b2 缺 accounts.example.json → exit=1，1 条 [FAIL]：{fl[0][:120]}")

    # 8c 包内目录不可写（webapp 位置被文件占住）→ 恰好 1 条 [FAIL]
    t = TMP_ROOT / "c08c"
    t.mkdir()
    pkg = make_pkg(t, webapp_as_file=True)
    rc, out, _el = run_fr(pkg, ["--portable", "--no-network"], warn_env(t))
    fl = fail_lines(out)
    assert_(rc == 1, f"8c 退出码 {rc}（期望 1）；输出：{_tail(out)}")
    assert_(len(fl) == 1, f"8c 应恰好 1 条 [FAIL]，实得 {len(fl)}：{fl}")
    assert_("包内目录不可写" in fl[0], f"8c [FAIL] 没点明目录不可写：{fl[0]}")
    ev.append(f"8c 包内目录不可写 → exit=1，1 条 [FAIL]：{fl[0][:120]}")

    # 8d 台账创建失败 → 恰好 1 条 [FAIL]（桩：真实「磁盘拒绝写」在 Windows 上要靠 ACL，
    #    这里只验「create_ledger 返回失败时调用方是否恰好转成一条 [FAIL] + exit 1」这一条契约）
    t = TMP_ROOT / "c08d"
    t.mkdir()
    pkg = make_pkg(t)
    mod = load_fr_module(pkg / "first_run.py", "qa_t17_fr_c08d")
    buf = io.StringIO()
    env_guard = {"WB_PROXY": "http://127.0.0.1:9",
                 "HF_HOME": str(t), "USERPROFILE": str(t)}
    with patch.dict(os.environ, env_guard), \
         patch.object(mod, "create_ledger",
                      lambda _p: (False, "桩：台账写入失败（模拟磁盘拒绝）")), \
         contextlib.redirect_stdout(buf):
        rc = mod.main(["--portable", "--no-network",
                       "--pipeline-dir", str(pkg / "pipeline"),
                       "--ledger-dir", str(pkg / "ledger")])
    out = buf.getvalue()
    fl = fail_lines(out)
    assert_(rc == 1, f"8d 退出码 {rc}（期望 1）；输出：{_tail(out)}")
    assert_(len(fl) == 1, f"8d 应恰好 1 条 [FAIL]，实得 {len(fl)}：{fl}")
    assert_("台账" in fl[0], f"8d [FAIL] 没点明台账：{fl[0]}")
    ev.append(f"8d 台账创建失败（桩）→ exit=1，1 条 [FAIL]：{fl[0][:120]}")
    return ev


# ══════════════════════════════════════════════════════════════════════
# C-09 模型检查认 HF_HOME / HF_HUB_CACHE（DoD 4）
# ══════════════════════════════════════════════════════════════════════
def _fake_model(hub: Path) -> Path:
    snap = hub / "models--Systran--faster-whisper-medium" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"stub-model")   # T-17 只判「在不在」，不读 1.5 GB
    (snap / "config.json").write_text("{}", encoding="utf-8")
    return snap


def _model_probe(tag: str, extra_env: dict, expect: str):
    t = TMP_ROOT / tag
    t.mkdir()
    pkg = make_pkg(t)
    env = clean_env(path=_min_path(), extra={
        "WB_PROXY": "http://127.0.0.1:9", "HF_HOME": t / "hf_empty",
        "USERPROFILE": t / "home_empty",
    })
    (t / "hf_empty").mkdir()
    (t / "home_empty").mkdir()
    env.update({k: str(v) for k, v in extra_env.items()})
    rc, out, _el = run_fr(
        pkg, ["--portable", "--no-network", "--dry-run",
              "--pipeline-dir", pkg / "pipeline", "--ledger-dir", pkg / "ledger"], env)
    assert_(rc == 0, f"{tag} 退出码 {rc}（期望 0）；输出：{_tail(out)}")
    assert_(expect in out, f"{tag} 输出里没有「{expect}」：{_tail(out)}")
    hit = [ln.strip() for ln in out.splitlines() if expect in ln]
    return hit[0] if hit else expect


def c09():
    t = TMP_ROOT / "c09"
    t.mkdir()
    hfhome = t / "hfhome"
    _fake_model(hfhome / "hub")

    ev = []
    ev.append("HF_HOME 有 model.bin -> " + _model_probe(
        "c09a", {"HF_HOME": hfhome}, "whisper 模型已缓存"))
    ev.append("HF_HOME 空 + 空 home -> " + _model_probe(
        "c09b", {}, "没找到"))

    hubcache = t / "hubcache"
    _fake_model(hubcache)
    ev.append("HF_HUB_CACHE 优先（HF_HOME 为空）-> " + _model_probe(
        "c09c", {"HF_HUB_CACHE": hubcache}, "whisper 模型已缓存"))
    return ev


# ══════════════════════════════════════════════════════════════════════
# C-10 真实环境「只创建缺失、不改既有」+ 仓库未被碰（DoD 5，按 Lead 约束用等价副本）
# ══════════════════════════════════════════════════════════════════════
def c10():
    ev = []
    t = TMP_ROOT / "c10"
    t.mkdir()
    replica = t / "replica"
    (replica / "pipeline").mkdir(parents=True)
    (replica / "ledger").mkdir()
    (replica / "webapp").mkdir()
    shutil.copy2(FR, replica / "first_run.py")
    assert_(sha256(replica / "first_run.py") == sha256(FR), "副本 first_run.py 与仓库原件不一致")

    # 用仓库真实文件做副本（只读复制；副本里把 xlsx 指到副本台账，避免读到真实路径）
    src_xlsx = LEDGER / "tiktok_对标.xlsx"
    assert_(src_xlsx.is_file(), f"仓库台账不存在：{src_xlsx}（T-17 的真实验收前提）")
    shutil.copy2(src_xlsx, replica / "ledger" / "tiktok_对标.xlsx")
    shutil.copy2(ACCOUNTS, replica / "pipeline" / "accounts.json")
    cfg = json.loads((replica / "pipeline" / "accounts.json").read_text(encoding="utf-8"))
    cfg["xlsx"] = str(replica / "ledger" / "tiktok_对标.xlsx")
    cfg["metrics_file"] = str(replica / "pipeline" / "reports" / "daily_metrics.jsonl")
    cfg["reports_root"] = str(replica / "pipeline" / "reports")
    (replica / "pipeline" / "accounts.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    r_acc = replica / "pipeline" / "accounts.json"
    r_xlsx = replica / "ledger" / "tiktok_对标.xlsx"
    before = {"accounts.json": stat_of(r_acc), "xlsx": stat_of(r_xlsx)}
    before_names = sorted(p.name for p in (replica / "ledger").iterdir())

    env = clean_env(path=_min_path(), extra={
        "WB_PROXY": "http://127.0.0.1:9",
        "HF_HOME": t / "hf_empty", "USERPROFILE": t / "home_empty",
    })
    (t / "hf_empty").mkdir()
    (t / "home_empty").mkdir()

    for i in (1, 2):
        rc, out, elapsed = run_fr(replica, ["--portable", "--no-network"], env)
        assert_(rc == 0, f"副本第 {i} 次 --portable --no-network 退出码 {rc}（期望 0）；输出：{_tail(out)}")
        assert_(not fail_lines(out), f"副本第 {i} 次出现 [FAIL]：{fail_lines(out)}")
        ev.append(f"副本第 {i} 次：exit=0，{elapsed:.2f}s，0 条 [FAIL]")

    after = {"accounts.json": stat_of(r_acc), "xlsx": stat_of(r_xlsx)}
    assert_(after == before,
            "副本里已有的 accounts.json / 台账被改动了：\n"
            f"  前：{ {k: _short_sha(v) for k, v in before.items()} }\n"
            f"  后：{ {k: _short_sha(v) for k, v in after.items()} }")
    after_names = sorted(p.name for p in (replica / "ledger").iterdir())
    assert_(after_names == before_names,
            f"副本台账目录出现了新文件/少了文件：前 {before_names} / 后 {after_names}")
    ev.append("副本里既有台账与 accounts.json：sha256 + mtime 两次运行后完全不变；无新增文件")

    # ── 第二半：真实仓库绝不被碰（Lead 硬约束的断言）──
    end = repo_snapshot()
    assert_(end == START_SNAP,
            "真实仓库的受保护文件被本次 QA 改动了（或运行期间被别的进程改动）：\n" +
            "\n".join(_snap_diff(START_SNAP, end)))
    ev.append("真实仓库未被碰（逐项核对）：")
    for name in sorted(START_SNAP["files"]):
        s = START_SNAP["files"][name]
        ev.append(f"  {name}：sha256={_short_sha(s)}，mtime_ns={s.get('mtime_ns') if isinstance(s, dict) else '缺失'}，未变")
    return ev


def _snap_diff(a: dict, b: dict) -> list:
    lines = []
    for name in sorted(set(a["files"]) | set(b["files"])):
        sa, sb = a["files"].get(name), b["files"].get(name)
        if sa != sb:
            lines.append(f"  {name}：前 {_short_sha(sa)} / 后 {_short_sha(sb)}")
    if a["ledger_names"] != b["ledger_names"]:
        lines.append(f"  ledger 目录清单变化：前 {a['ledger_names']} / 后 {b['ledger_names']}")
    if not lines:
        lines.append("  （无差异）")
    return lines


CASES = [
    ("C-01", "首次运行单测通过（DoD 1 入口）", c01),
    ("C-02", "14 列台账模板，表头逐字等于 config.EXPECTED_COLS（DoD 1a）", c02),
    ("C-03", "accounts.json 生成正确：示例账号 active=false、xlsx 指向该台账（DoD 1b）", c03),
    ("C-04", "二次运行不覆盖：手改单元格 + 自加账号都还在（DoD 1c）", c04),
    ("C-05", "两次 --init-only 退出码都是 0（DoD 1d）", c05),
    ("C-06", "单次 --init-only < 2 秒（3 次取最大值，DoD 1e）", c06),
    ("C-07", "不联网：socket/urlopen 打桩后仍退出 0、0 条 [FAIL]、调用 0 次（DoD 2）", c07),
    ("C-08", "--portable 的 [FAIL] 预算：三类故障各恰好 1 条（DoD 3 的另一半）", c08),
    ("C-09", "模型检查认 HF_HOME / HF_HUB_CACHE（DoD 4）", c09),
    ("C-10", "真实环境只创建缺失、不改既有 + 仓库未被碰（DoD 5）", c10),
]


def main() -> int:
    global _ok, _fail, START_SNAP
    print("qa/T-17 —— T-17 首次运行初始化（first_run.py --portable / --init-only）")
    print(f"仓库：{REPO}")
    print(f"隔离：所有初始化都在 {TMP_ROOT} 下的临时包副本里跑；仓库 ledger/accounts 只读")
    if not FR.is_file():
        print("[FAIL] 找不到 first_run.py：T-17 的被测文件不存在")
        return 1
    START_SNAP = repo_snapshot()
    print("开跑前真实仓库快照：" + "、".join(
        f"{k}({_short_sha(v)})" for k, v in sorted(START_SNAP["files"].items())))
    print()

    for cid, title, fn in CASES:
        try:
            ev = fn() or []
            print(f"ok {cid} {title}")
            for line in ev:
                print("     " + line)
            _ok += 1
        except AssertionError as e:
            print(f"FAIL {cid} {title} -> {e}")
            _fail += 1
        except Exception as e:                       # noqa: BLE001
            print(f"FAIL {cid} {title} -> {type(e).__name__}: {e}")
            _fail += 1
        print()

    # C-11 是入口契约本身：本脚本退出码 0 + 打印「通过 N / M」；任何一条红它必然红
    try:
        assert_(_fail == 0, f"有 {_fail} 条用例不通过（入口不可能退出 0）")
        assert_(len(CASES) == 10, f"用例数应为 10 + C-11，实得 {len(CASES)}")
        _ok += 1
        print("ok C-11 QA 入口：本脚本退出码 0，且有「通过 N / M」汇总")
    except AssertionError as e:
        _fail += 1
        print(f"FAIL C-11 QA 入口：本脚本退出码 0，且有「通过 N / M」汇总 -> {e}")

    total = _ok + _fail
    print(f"\nDoD 对照：DoD1(a) C-02；DoD1(b) C-03；DoD1(c) C-04；DoD1(d) C-05；DoD1(e) C-06；"
          f"DoD2 C-07；DoD3 C-08；DoD4 C-09；DoD5 C-10；DoD6 C-11（本入口）")
    print(f"\n通过 {_ok} / {total}")
    return 0 if _fail == 0 else 1


START_SNAP: dict = {}

if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
    sys.exit(code)
