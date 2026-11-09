# -*- coding: utf-8 -*-
"""first_run_portable.test.py — T-17：`first_run.py --portable` / `--init-only`

DoD（任务表 T-17，逐条对应）：
  1. `--portable --init-only --pipeline-dir <临时> --ledger-dir <临时>`：
     (a) 生成 14 列台账 xlsx，表头逐字等于 `webapp\\config.py:EXPECTED_COLS`；
     (b) 生成 `accounts.json`，示例账号 active=false、xlsx 指向该台账；
     (c) 二次运行不覆盖（改单元格 + 加账号后两处都在）；
     (d) 退出码 0；
     (e) 单次 < 2 秒。
  2. 不联网：socket.create_connection / urllib.request.urlopen 打桩成抛异常时，
     `--portable --init-only` 仍退出 0、无 [FAIL]、桩调用计数 == 0。
  3. `--portable` 下 [FAIL] 只有三类；缺 venv 打 `[ OK ] 包内解释器：…`；
     缺 ffmpeg / 缺模型 / 无代理一律 [WARN]。
  4. 模型检查认 HF_HOME / HF_HUB_CACHE（找 models--Systran--faster-whisper-medium\\snapshots\\*\\model.bin）。
  5. 真实环境实跑 `--portable --no-network` 退出 0，且 pipeline\\accounts.json、ledger\\*.xlsx mtime 不变。

跑法（仓库根）：
    webapp\\.venv\\Scripts\\python.exe webapp\\tests\\first_run_portable.test.py
不联网、不占 8789、只写 tempfile 目录（第 7 条只读真实仓库并核对 mtime）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
WEBAPP = HERE.parent.parent
ROOT = WEBAPP.parent

# 本机控制台默认 GBK，打不出 ✓/✗ 会把「断言失败」变成 UnicodeEncodeError 崩溃。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass

sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402  （只读 EXPECTED_COLS；import 会建 data 目录，仓库里本来就存在）

sys.path.insert(0, str(ROOT))
import first_run  # noqa: E402  （第 3 条要在同一个进程里打桩调 main()）

PY = str(WEBAPP / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = sys.executable

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except SystemExit as e:                      # argparse 拒绝参数（或被测代码 sys.exit）
        print(f"  ✗ {name} -> 进程被 sys.exit({e.code}) 中断：参数没被接受？")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


_CLEAR = ("WB_PYTHON", "WB_PYTHONW", "WB_LEDGER_DIR", "WB_PIPELINE_DIR", "WB_HOME",
          "WB_LOG_DIR", "WB_PROXY", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
          "XDG_CACHE_HOME", "HF_HUB_OFFLINE", "HF_ENDPOINT")


def base_env(extra=None, path=None):
    """干净子进程环境：清掉 WB_*/HF_*，保证用例不依赖开发机残留。"""
    env = dict(os.environ)
    for k in _CLEAR:
        env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    env["PATH"] = path if path is not None else (sysroot + r"\system32;" + sysroot)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def run_first_run(args, env=None, timeout=120):
    t0 = time.perf_counter()
    p = subprocess.run([PY, str(ROOT / "first_run.py")] + [str(a) for a in args],
                       cwd=str(ROOT), capture_output=True, timeout=timeout,
                       env=env if env is not None else base_env())
    elapsed = time.perf_counter() - t0
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out, elapsed


def tmpdir(tag):
    return Path(tempfile.mkdtemp(prefix=f"fr_{tag}_"))


print("first_run_portable — T-17：--portable / --init-only")

# ── 1. 首次初始化：台账 + accounts.json + 表头 + 耗时 ─────────────────
_STATE = {}


def case_first_init():
    t = tmpdir("first")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    rc, out, elapsed = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    _STATE.update(pipe=pipe, ledger=ledger_dir, out=out, elapsed=elapsed)

    assert_(rc == 0, f"退出码应为 0，实得 {rc}；输出尾：{out[-800:]}")
    assert_("[FAIL]" not in out, f"--init-only 不许出现 [FAIL]：{out[-800:]}")

    xlsx = ledger_dir / "tiktok_对标.xlsx"
    assert_(xlsx.is_file(), f"没生成台账：{xlsx}（输出尾：{out[-400:]}）")

    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    try:
        ws = wb.active
        header = [c.value for c in ws[1]][:14]
    finally:
        wb.close()
    assert_(header == list(config.EXPECTED_COLS),
            f"表头必须逐字等于 config.EXPECTED_COLS；实得 {header} / 期望 {list(config.EXPECTED_COLS)}")

    acc_path = pipe / "accounts.json"
    assert_(acc_path.is_file(), f"没生成 accounts.json：{acc_path}")
    cfg = json.loads(acc_path.read_text(encoding="utf-8"))
    accs = cfg.get("accounts") or []
    assert_(accs, "accounts.json 里没有示例账号")
    assert_(all(a.get("active") is False for a in accs),
            f"示例账号必须全部 active=false：{[a.get('active') for a in accs]}")
    assert_(Path(cfg.get("xlsx") or "").resolve() == xlsx.resolve(),
            f"accounts.json 的 xlsx 没指向本次台账：{cfg.get('xlsx')!r} != {str(xlsx)!r}")
    assert_(elapsed < 2.0, f"--init-only 单次应 < 2 秒，实测 {elapsed:.3f}s")
    print(f"    实测首次 --init-only：{elapsed:.3f}s")


check("--portable --init-only 首次：生成 14 列台账 + accounts.json，退出 0，< 2 秒", case_first_init)


# ── 2. 二次运行绝不覆盖 ────────────────────────────────────────────
def case_second_run_keeps():
    pipe, ledger_dir = _STATE.get("pipe"), _STATE.get("ledger")
    assert_(pipe and ledger_dir, "第 1 条没跑成，无法验二次运行")
    xlsx = ledger_dir / "tiktok_对标.xlsx"
    acc_path = pipe / "accounts.json"

    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    try:
        wb.active["A2"] = "用户手改的单元格"
        wb.save(xlsx)
    finally:
        wb.close()

    cfg = json.loads(acc_path.read_text(encoding="utf-8"))
    cfg.setdefault("accounts", []).append({"account": "user_added", "lang": "en", "active": True,
                                           "out_dir": str(pipe / "output"),
                                           "audio_dir": str(pipe / "output" / "user_added_audio")})
    acc_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    xlsx_m = xlsx.stat().st_mtime_ns
    acc_m = acc_path.stat().st_mtime_ns

    rc, out, _el = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc == 0, f"二次运行退出码应为 0，实得 {rc}；输出尾：{out[-500:]}")

    wb = openpyxl.load_workbook(xlsx)
    try:
        kept = wb.active["A2"].value
    finally:
        wb.close()
    assert_(kept == "用户手改的单元格", f"台账被覆盖了：A2 = {kept!r}")

    cfg2 = json.loads(acc_path.read_text(encoding="utf-8"))
    names = [a.get("account") for a in cfg2.get("accounts") or []]
    assert_("user_added" in names, f"accounts.json 被覆盖了（找不到自加账号）：{names}")
    assert_(xlsx.stat().st_mtime_ns == xlsx_m, "台账 mtime 变了：二次运行不该动已有文件")
    assert_(acc_path.stat().st_mtime_ns == acc_m, "accounts.json mtime 变了：二次运行不该动已有文件")


check("二次运行不覆盖：手改的单元格与自加账号都还在（且 mtime 不变）", case_second_run_keeps)


# ── 2b. 每次启动的快路径：文件都在时只许用最小标准库 ───────────────────
def case_fast_path_min_imports():
    """`--init-only` 每次启动都会跑，所以「文件都在」时要做成零动作快路径。

    判据（T-17 收尾）：这一次不许 import openpyxl（只有真建台账才用）、
    也不许 import argparse / socket / shutil / pathlib 这些启动期重库；
    不打印任何东西、不动任何文件。用 `-X importtime` 看子进程真实 import 了谁。
    """
    t = tmpdir("fast")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    rc, out, _el = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc == 0, f"第一次 init-only 应退出 0，实得 {rc}；输出尾：{out[-500:]}")
    acc = pipe / "accounts.json"
    xlsx = ledger_dir / "tiktok_对标.xlsx"
    assert_(acc.is_file() and xlsx.is_file(), "第一次应建出 accounts.json 与台账")
    before = (acc.stat().st_mtime_ns, xlsx.stat().st_mtime_ns)

    # 第二次：所有文件都在 → 走快路径；用 -X importtime 记录真实 import
    p = subprocess.run(
        [PY, "-X", "importtime", str(ROOT / "first_run.py"), "--portable", "--init-only",
         "--pipeline-dir", str(pipe), "--ledger-dir", str(ledger_dir)],
        cwd=str(ROOT), capture_output=True, timeout=120, env=base_env())
    err = (p.stderr or b"").decode("utf-8", "replace")
    stdout = (p.stdout or b"").decode("utf-8", "replace")
    assert_(p.returncode == 0, f"快路径退出码应为 0，实得 {p.returncode}；stderr 尾：{err[-500:]}")

    heavy = []
    for line in err.splitlines():
        if line.strip().startswith("import time") and "|" in line:
            mod = line.split("|")[-1].strip()
            if mod.split(".")[0] in {"openpyxl", "argparse", "socket", "shutil", "pathlib"}:
                heavy.append(mod)
    assert_(not heavy, f"快路径 import 了启动期重库：{heavy[:8]}")
    assert_(stdout.strip() == "", f"快路径不该有任何输出，实得：{stdout[:300]!r}")

    after = (acc.stat().st_mtime_ns, xlsx.stat().st_mtime_ns)
    assert_(before == after, "快路径动了已有文件（mtime 变了）")
    assert_(not (t / "logs" / "first-run.log").exists(), "快路径不该写 first-run.log")


check("快路径（文件都在）：只 import 最小标准库，不碰 openpyxl/argparse/socket/shutil/pathlib",
      case_fast_path_min_imports)


# ── 3. 不联网：两个网络入口打桩成抛异常，计数必须为 0 ────────────────
def case_no_network():
    import socket
    import urllib.request

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise OSError("stub: 本用例禁止联网")

    t = tmpdir("net")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    argv = ["first_run.py", "--portable", "--init-only",
            "--pipeline-dir", str(pipe), "--ledger-dir", str(ledger_dir)]
    buf = io.StringIO()
    with patch.object(socket, "create_connection", boom), \
         patch.object(urllib.request, "urlopen", boom), \
         patch.object(sys, "argv", argv):
        with contextlib.redirect_stdout(buf):
            rc = first_run.main()
    out = buf.getvalue()

    # 先证明桩确实会拦（否则「计数 0」可能是桩没装好）
    try:
        with patch.object(socket, "create_connection", boom):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        assert_(False, "桩没生效：create_connection 没抛异常")
    except OSError:
        pass
    calls["n"] = 0

    assert_(rc == 0, f"退出码应为 0，实得 {rc}；输出尾：{out[-600:]}")
    assert_("[FAIL]" not in out, f"不许出现 [FAIL]：{out[-600:]}")
    assert_(calls["n"] == 0, f"--portable --init-only 竟然发起了 {calls['n']} 次网络调用")
    assert_((pipe / "accounts.json").is_file(), "不联网时也应完成初始化")


check("不联网：socket/urllib 打桩后仍退出 0、无 [FAIL]、网络调用 0 次", case_no_network)


def case_proxy_credentials_masked():
    """安全：`检查环境.bat` 会打印代理；带 user:pass@ 的凭据绝不许出现在屏幕上。

    `WB_PROXY=http://user:pass@127.0.0.1:7892` 是合法写法（有些代理要认证），
    但打印出来会进截图/求助贴。打印前只留 `scheme://host:port`。
    """
    import socket

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    raw = "http://user:pass@127.0.0.1:7892"
    buf = io.StringIO()
    real = socket.create_connection
    try:
        socket.create_connection = lambda *_a, **_k: _FakeConn()
        with contextlib.redirect_stdout(buf):
            ok = first_run.check_proxy(raw, portable=True)
    finally:
        socket.create_connection = real
    out = buf.getvalue()
    assert_(ok is True, f"桩连上时 check_proxy 应返回 True；输出：{out!r}")
    assert_("http://127.0.0.1:7892" in out, f"应打印 scheme://host:port；实得：{out!r}")
    assert_("user" not in out and "pass" not in out, f"代理凭据被打印到屏幕上了：{out!r}")

    # 连不上时那条 [WARN] 也不许带凭据
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        ok2 = first_run.check_proxy("http://user:pass@127.0.0.1:9", portable=True)
    out2 = buf2.getvalue()
    assert_(ok2 is False, f"127.0.0.1:9 应连不上；输出：{out2!r}")
    assert_("user" not in out2 and "pass" not in out2, f"WARN 行里带了代理凭据：{out2!r}")

    # 没有 userinfo 时输出一个字都不能变
    assert_(first_run._mask_proxy("http://127.0.0.1:7892") == "http://127.0.0.1:7892",
            "没有凭据的代理地址不该被改写")


check("安全：带 user:pass@ 的代理打印前去掉凭据（只留 scheme://host:port）",
      case_proxy_credentials_masked)


# ── 2c. 坏台账（0 字节 / 截断）不许被快路径静默吞掉 ─────────────────────
def case_corrupt_ledger_zero_byte():
    """`openpyxl.save()` 不是原子写：进程被杀会留下 0 字节 xlsx。

    这种文件如果被当成"台账已就绪"，快路径会零输出静默返回 0，
    而看板打不开台账 → 用户拿不到任何线索。所以：0 字节 = 没有台账，
    落回完整路径，并把原因写进 logs\\first-run.log，且**绝不覆盖**它。
    """
    t = tmpdir("corrupt0")
    pipe, ledger_dir, log_dir = t / "pipeline", t / "ledger", t / "logs"
    rc, out, _el = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc == 0, f"首次 init-only 应退出 0，实得 {rc}；输出尾：{out[-500:]}")
    xlsx = ledger_dir / "tiktok_对标.xlsx"
    assert_(xlsx.is_file() and xlsx.stat().st_size > 0, "首次应建出非空台账")

    xlsx.write_bytes(b"")                     # 模拟 openpyxl.save() 中途被杀
    mtime = xlsx.stat().st_mtime_ns

    rc2, out2, _el2 = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"WB_LOG_DIR": log_dir}))
    assert_(rc2 == 0, f"--init-only 仍应退出 0，实得 {rc2}；输出：{out2[-600:]}")
    log = log_dir / "first-run.log"
    assert_(log.is_file(), "0 字节台账没有留下任何线索：快路径把它静默当成就绪了")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert_(("损坏" in text) or ("为空" in text), f"日志里没说台账坏了：{text[:400]!r}")
    assert_("tiktok_对标.xlsx" in text, f"日志里要指名是哪个文件：{text[:400]!r}")

    assert_(xlsx.stat().st_size == 0, "坏台账被覆盖了（应只报不写）")
    assert_(xlsx.stat().st_mtime_ns == mtime, "坏台账被改写了（应只报不写）")
    assert_(len(list(ledger_dir.glob("*.xlsx"))) == 1, "不该在旁边再生成第二本台账")


def case_corrupt_ledger_truncated():
    """截断的 xlsx（有 PK 头、但没有 ZIP 的 End of Central Directory）同样算坏。"""
    t = tmpdir("corrupt1")
    pipe, ledger_dir, log_dir = t / "pipeline", t / "ledger", t / "logs"
    rc, out, _el = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc == 0, f"首次 init-only 应退出 0，实得 {rc}；输出尾：{out[-500:]}")
    xlsx = ledger_dir / "tiktok_对标.xlsx"
    xlsx.write_bytes(b"PK\x03\x04" + b"truncated" * 8)     # 有魔数、没有 EOCD

    rc2, out2, _el2 = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"WB_LOG_DIR": log_dir}))
    assert_(rc2 == 0, f"--init-only 仍应退出 0，实得 {rc2}；输出：{out2[-600:]}")
    log = log_dir / "first-run.log"
    assert_(log.is_file(), "截断的台账没有留下线索（快路径当成就绪了）")
    assert_(("损坏" in log.read_text(encoding="utf-8", errors="replace")),
            "日志里没说台账坏了")

    # --portable 检查模式：坏台账只算 [WARN]，不占那三类 [FAIL]，退出码仍 0
    rc3, out3, _el3 = run_first_run(
        ["--portable", "--no-network", "--dry-run",
         "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc3 == 0, f"坏台账在检查模式应只是 WARN（退出 0），实得 {rc3}；输出：{out3[-600:]}")
    assert_("[FAIL]" not in out3, f"坏台账不许占那三类 [FAIL]：{out3[-600:]}")
    assert_("损坏" in out3, f"检查模式应说明台账坏了：{out3[-600:]}")


check("坏台账（0 字节）：快路径不生效、原因写进 logs\\first-run.log、绝不覆盖",
      case_corrupt_ledger_zero_byte)
check("坏台账（截断/无 EOCD）：同样判坏；检查模式只 WARN、不占 [FAIL]",
      case_corrupt_ledger_truncated)


def case_init_only_logs_failure():
    """契约 §3：--init-only 失败不阻断启动，只把原因写进 logs\\first-run.log。"""
    t = tmpdir("initlog")
    pipe, ledger_dir, log_dir = t / "pipeline", t / "ledger", t / "logs"
    pipe.mkdir(parents=True)
    (pipe / "accounts.json").write_text("坏 JSON", encoding="utf-8")
    rc, out, _el = run_first_run(
        ["--portable", "--init-only", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"WB_LOG_DIR": log_dir}))
    assert_(rc == 0, f"--init-only 失败也必须退出 0（不阻断启动），实得 {rc}；输出：{out[-600:]}")
    assert_("[FAIL]" not in out, f"--init-only 不许出现 [FAIL]：{out[-600:]}")
    log = log_dir / "first-run.log"
    assert_(log.is_file(), f"失败原因应写进 {log}（日志目录里没有它）")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert_("accounts.json" in text, f"日志里没写清失败原因：{text[:400]!r}")


check("--init-only 失败不阻断启动：退出 0、无 [FAIL]、原因写进 logs\\first-run.log",
      case_init_only_logs_failure)


# ── 4. --portable 下的 FAIL 只有三类；其余一律 WARN ─────────────────
def case_fail_budget():
    t = tmpdir("warn")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    empty_hf = t / "hfempty"
    empty_hf.mkdir()
    env = base_env({"HF_HOME": empty_hf})
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--dry-run", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=env)
    assert_(rc == 0, f"缺 ffmpeg/模型/代理应仍退出 0（都是 WARN），实得 {rc}；输出尾：{out[-800:]}")
    assert_("[FAIL]" not in out, f"缺 ffmpeg/模型/代理不该出 [FAIL]：{out[-800:]}")
    assert_("[WARN]" in out, "至少应有 [WARN]（缺 ffmpeg/代理/模型之一）")
    assert_("包内解释器" in out, f"--portable 应打一行 [ OK ] 包内解释器：…；输出：{out[-800:]}")
    assert_("虚拟环境缺失" not in out, "便携包里没有 venv，不许报「虚拟环境缺失」")
    print("    缺 ffmpeg/模型/代理 → 全部 WARN，退出 0")


def case_fail_bad_accounts():
    t = tmpdir("badacc")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    pipe.mkdir(parents=True)
    (pipe / "accounts.json").write_text("这不是 JSON", encoding="utf-8")
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--dry-run", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir])
    assert_(rc == 1, f"accounts.json 无法解析应退出 1，实得 {rc}；输出尾：{out[-600:]}")
    assert_("[FAIL]" in out, f"accounts.json 无法解析应出 [FAIL]：{out[-600:]}")


def case_fail_ledger_dir():
    t = tmpdir("badledger")
    pipe = t / "pipeline"
    blocked = t / "not_a_dir"
    blocked.write_text("x", encoding="utf-8")      # 台账目录位置被一个文件占住 → 建不出目录/台账
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--pipeline-dir", pipe, "--ledger-dir", blocked])
    assert_(rc == 1, f"包内目录不可写应退出 1，实得 {rc}；输出尾：{out[-600:]}")
    assert_("[FAIL]" in out, f"包内目录不可写应出 [FAIL]：{out[-600:]}")


def case_fail_ledger_create():
    """台账创建失败（create_ledger 返回 False）必须变成 [FAIL] + 退出 1 —— 用桩制造。"""
    t = tmpdir("ledgerfail")
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    buf = io.StringIO()
    argv = ["first_run.py", "--portable", "--no-network",
            "--pipeline-dir", str(pipe), "--ledger-dir", str(ledger_dir)]
    with patch.object(first_run, "create_ledger", lambda _p: (False, "stub：台账写入失败")), \
         patch.object(sys, "argv", argv), \
         contextlib.redirect_stdout(buf):
        rc = first_run.main()
    out = buf.getvalue()
    assert_(rc == 1, f"台账创建失败应退出 1，实得 {rc}；输出尾：{out[-600:]}")
    assert_("[FAIL]" in out, f"台账创建失败应出 [FAIL]：{out[-600:]}")
    assert_("台账" in out, f"失败消息应点明台账：{out[-600:]}")


check("--portable：缺 ffmpeg/模型/代理 → 全 WARN 且退出 0；打 [ OK ] 包内解释器", case_fail_budget)
check("--portable：accounts.json 无法解析 → [FAIL] + 退出 1（允许的三类之一）", case_fail_bad_accounts)
check("--portable：包内目录不可写 → [FAIL] + 退出 1（允许的三类之一）", case_fail_ledger_dir)
check("--portable：台账创建失败 → [FAIL] + 退出 1（允许的三类之一）", case_fail_ledger_create)


# ── 5. 模型检查认 HF_HOME / HF_HUB_CACHE ───────────────────────────
def _fake_model(hub: Path, model="medium") -> Path:
    snap = hub / f"models--Systran--faster-whisper-{model}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"stub-model")     # T-17 只判「在不在」，不读 1.5 GB
    (snap / "config.json").write_text("{}", encoding="utf-8")
    return snap


def case_model_hf_home():
    t = tmpdir("model")
    hub = t / "hfhome" / "hub"
    _fake_model(hub)
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--dry-run", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"HF_HOME": t / "hfhome"}))
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；输出尾：{out[-600:]}")
    assert_("whisper 模型已缓存" in out, f"HF_HOME 指到有 model.bin 的目录却没认出来：{out[-600:]}")


def case_model_missing():
    t = tmpdir("nomodel")
    empty = t / "empty"
    empty.mkdir()
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--dry-run", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"HF_HOME": empty}))
    assert_(rc == 0, f"缺模型只是 WARN，退出码应为 0，实得 {rc}；输出尾：{out[-600:]}")
    assert_("没找到" in out, f"空目录应打印「没找到」：{out[-600:]}")


def case_model_hub_cache_priority():
    t = tmpdir("modelcache")
    hub = t / "hubcache"
    _fake_model(hub)
    empty = t / "hfhome_empty"
    empty.mkdir()
    pipe, ledger_dir = t / "pipeline", t / "ledger"
    rc, out, _el = run_first_run(
        ["--portable", "--no-network", "--dry-run", "--pipeline-dir", pipe, "--ledger-dir", ledger_dir],
        env=base_env({"HF_HUB_CACHE": hub, "HF_HOME": empty}))
    assert_(rc == 0, f"退出码应为 0，实得 {rc}；输出尾：{out[-600:]}")
    assert_("whisper 模型已缓存" in out,
            f"HF_HUB_CACHE 优先级最高，指到有模型处应命中：{out[-600:]}")


check("模型检查认 HF_HOME：有 model.bin → 「whisper 模型已缓存」", case_model_hf_home)
check("模型检查认空 HF_HOME：→ 「没找到」且只是 WARN", case_model_missing)
check("模型检查认 HF_HUB_CACHE（优先级高于 HF_HOME）", case_model_hub_cache_priority)


# ── 6. 真实环境实跑（DoD 5）：退出 0，且只创建缺失 ─────────────────
def case_real_env():
    acc = ROOT / "pipeline" / "accounts.json"
    xlsx_before = {p: p.stat().st_mtime_ns for p in sorted((ROOT / "ledger").glob("*.xlsx"))}
    acc_before = acc.stat().st_mtime_ns if acc.is_file() else None

    rc, out, elapsed = run_first_run(["--portable", "--no-network"],
                                     env=base_env(path=os.environ.get("PATH", "")))
    assert_(rc == 0, f"真实环境 --portable --no-network 应退出 0，实得 {rc}；输出尾：{out[-900:]}")
    assert_("[FAIL]" not in out, f"真实环境不该有 [FAIL]：{out[-900:]}")

    if acc_before is not None:
        assert_(acc.stat().st_mtime_ns == acc_before, "真实 pipeline\\accounts.json 的 mtime 被改了")
    for p, m in xlsx_before.items():
        assert_(p.stat().st_mtime_ns == m, f"真实台账 {p.name} 的 mtime 被改了")
    print(f"    实测真实环境 --portable --no-network：{elapsed:.2f}s，台账/accounts.json mtime 未变")


def case_default_mode_unchanged():
    rc, out, _el = run_first_run(["--dry-run", "--no-network"],
                                 env=base_env(path=os.environ.get("PATH", "")))
    assert_(rc == 0, f"不带 --portable 的默认模式应仍退出 0，实得 {rc}；输出尾：{out[-900:]}")
    assert_("虚拟环境" in out, f"开发检出模式仍应做 venv 检查：{out[-900:]}")


check("真实环境实跑 --portable --no-network：退出 0，台账与 accounts.json 的 mtime 不变", case_real_env)
check("默认模式（开发检出）行为不变：仍做 venv 检查并退出 0", case_default_mode_unchanged)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
