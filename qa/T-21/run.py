#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qa/T-21/run.py — T-21「流水线不再钉离线开关，前置校验对便携环境说真话」的独立验收。

用例清单：`.dsh-crew/jobs/portable-package/M2-qa-plan.md` 的 T-21 段（1..10 条）。
DoD 来源：docs/tasks/tasks.md 的 T-21（DoD 4 已按 CRD-0006 放宽）。

跑法（按清单）：
    cd D:\\tiktok-workbench
    pipeline\\.venv\\Scripts\\python.exe qa\\T-21\\run.py

Lead 的顺序要求（2026-11-08）：
  * 先跑 `pipeline\\tests\\test_orchestrator.py`，必须退 0。
  * `pipeline\\tests\\test_acceptance_regressions.py` 现在红 1 条
    `test_cpu_backend_explicitly_uses_cpu`，是 CRD-0006 的**已知前提**，另一个 QA 正在改。
    本文件先跑一次记录状态；**如果红的只有这一条**，标
    `WAIT-CRD-0006（待另一 QA 更新）` 且不算失败；红别的就是真回归，判 FAIL。
    最后（本文件第 10 条之后）再跑一次：那时如果绿了，就按绿判（第 9 条升级为 PASS）。
  * 纯打桩，不起服务；除上面那一条 WAIT 外没有 SKIP。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
QA_DIR = Path(__file__).resolve().parent
PIPELINE_PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
PIPELINE_DIR = REPO / "pipeline"
PIPELINE_SRC = PIPELINE_DIR / "pipeline.py"
TEST_PORTABLE = PIPELINE_DIR / "tests" / "test_pipeline_portable_env.py"
TEST_ORCH = PIPELINE_DIR / "tests" / "test_orchestrator.py"
TEST_ACCEPT = PIPELINE_DIR / "tests" / "test_acceptance_regressions.py"
KNOWN_RED = "test_cpu_backend_explicitly_uses_cpu"

# 本机控制台是 GBK，而这些测试会打印 ✓/✗；统一用 UTF-8 跑子进程。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class CaseFail(AssertionError):
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


class CaseWait(Exception):
    """已知的、被别人任务修掉的暂时红：不算失败，但也不是 PASS。"""
    def __init__(self, msg: str, evidence: list[str] | None = None):
        super().__init__(msg)
        self.evidence = evidence or []


RESULTS: list[tuple[str, int, str]] = []


def case(num: int, desc: str):
    def deco(fn):
        def wrapper():
            try:
                ev = fn() or []
            except CaseWait as e:
                print("WAIT %d. %s -> %s" % (num, desc, e))
                for line in e.evidence:
                    print("    " + line)
                RESULTS.append(("WAIT", num, desc))
            except CaseFail as e:
                print("FAIL %d. %s -> %s" % (num, desc, e))
                for line in e.evidence:
                    print("    " + line)
                RESULTS.append(("FAIL", num, desc))
            except Exception as e:  # noqa: BLE001
                print("FAIL %d. %s -> %s: %s" % (num, desc, type(e).__name__, e))
                RESULTS.append(("FAIL", num, desc))
            else:
                print("PASS %d. %s" % (num, desc))
                for line in ev:
                    print("    " + line)
                RESULTS.append(("PASS", num, desc))
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def fail(msg: str, evidence: list[str] | None = None):
    raise CaseFail(msg, evidence)


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def run_cmd(args, timeout: int = 600) -> tuple[object, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([str(a) for a in args], cwd=str(REPO), env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, _decode_bytes(p.stdout)
    except subprocess.TimeoutExpired as e:
        return "TIMEOUT", _decode_bytes(e.stdout or b"")


def tail(text: str, n: int = 12) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-n:]


def load_pipeline():
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    import pipeline as pl  # noqa: E402
    return pl


def capture_step5():
    """打桩 _subprocess_step，跑一次 step5_transcribe，返回 (ok, detail, env, cmd)。"""
    pl = load_pipeline()
    audio = Path(tempfile.mkdtemp(prefix="t21_audio_")) / "acc1_audio"
    ns = SimpleNamespace(account="acc1", lang="en", backend="faster",
                         audio_dir=str(audio), media_type="video")
    cap: dict = {}
    real = pl._subprocess_step

    def fake_sub(step, cmd, env=None):
        cap.update(step=step, cmd=list(cmd), env=env)
        return True, '{"total": 1, "done": ["v1"], "suspect_hallucination": []}'

    pl._subprocess_step = fake_sub
    try:
        ok, detail = pl.step5_transcribe(ns)
    finally:
        pl._subprocess_step = real
    return ok, detail, cap.get("env"), cap.get("cmd") or [], audio


def _opt(cmd: list[str], name: str):
    if name not in cmd:
        return None
    i = cmd.index(name)
    return cmd[i + 1] if i + 1 < len(cmd) else None


# ═════════════════════════════ 用例 1..10 ═════════════════════════════

@case(1, "test_pipeline_portable_env.py 退出 0，输出无 Traceback")
def c01():
    if not TEST_PORTABLE.is_file():
        fail("单测不存在：%s" % TEST_PORTABLE)
    rc, out = run_cmd([PIPELINE_PY, TEST_PORTABLE], timeout=300)
    ev = ["%s -> exit=%s" % (TEST_PORTABLE, rc)] + tail(out, 8)
    if rc != 0:
        fail("test_pipeline_portable_env.py 退出码 %s" % rc, ev)
    if "Traceback" in out:
        fail("输出里出现 Traceback", ev)
    return ev


@case(2, "step5 传给 _subprocess_step 的 env 里没有 HF_HUB_OFFLINE")
def c02():
    ok, detail, env, cmd, audio = capture_step5()
    ev = ["step5_transcribe 返回 ok=%r，detail=%r" % (ok, detail),
          "捕获到的 env=%r" % (env,),
          "env 键集合=%r" % (sorted(env) if isinstance(env, dict) else [])]
    if ok is not True:
        fail("step5_transcribe 应返回成功", ev)
    if isinstance(env, dict) and "HF_HUB_OFFLINE" in env:
        fail("传给子进程的 env 里仍有 HF_HUB_OFFLINE（值为空字符串也算红）", ev)
    if env is None:
        ev.append("env=None：step5 没有显式覆盖任何变量（子进程继承父环境）")
    return ev


@case(3, "step5 命令行仍含 --backend faster / --model medium / --compute-type int8 / --lang en")
def c03():
    ok, detail, env, cmd, audio = capture_step5()
    pl = load_pipeline()
    ev = ["命令原文：%s" % " ".join(cmd)]
    if ok is not True:
        fail("step5_transcribe 应返回成功", ev)
    if len(cmd) < 2 or cmd[0] != pl.CFG["python"] or cmd[1] != pl.CFG["transcribe"]:
        fail("命令前两项不是解释器/转写脚本", ev)
    for flag, want in (("--backend", "faster"), ("--model", "medium"),
                       ("--compute-type", "int8"), ("--lang", "en")):
        got = _opt(cmd, flag)
        ev.append("%s=%r（期望 %r）" % (flag, got, want))
        if got != want:
            fail("%s 期望 %r，实得 %r" % (flag, want, got), ev)
    if str(audio) not in cmd:
        fail("命令里没有 audio_dir=%s" % audio, ev)
    return ev


@case(4, "pipeline.py 任何地方都不设置 HF_HUB_OFFLINE（只在注释，且写明由 model_cache 决定）")
def c04():
    if not PIPELINE_SRC.is_file():
        fail("源码不存在：%s" % PIPELINE_SRC)
    src = PIPELINE_SRC.read_text(encoding="utf-8")
    hits = [(i + 1, ln) for i, ln in enumerate(src.splitlines()) if "HF_HUB_OFFLINE" in ln]
    ev = ["命中 %d 行：" % len(hits)] + ["  %d: %s" % (n, ln.strip()) for n, ln in hits]
    if not hits:
        fail("一条注释都没有：后来的人会再把离线开关加回去", ev)
    bad = [(n, ln.strip()) for n, ln in hits if not ln.strip().startswith("#")]
    if bad:
        fail("HF_HUB_OFFLINE 出现在代码/文档字符串里（只允许 # 注释）：%r" % bad, ev)
    if not any(("model_cache" in ln) and ("决定" in ln or "由" in ln) for _n, ln in hits):
        fail("注释没有写明由 model_cache 决定", ev)
    return ev


@case(5, "step0_check 在便携环境成立（which 有值 + CFG['python'] 真实存在 → True）")
def c05():
    pl = load_pipeline()
    a = SimpleNamespace(account="acc1", media_type="video")
    saved = pl.CFG.get("python")
    real_which = shutil.which
    try:
        pl.CFG["python"] = sys.executable
        shutil.which = lambda _name: r"C:\x\ffmpeg.exe"
        ok, detail = pl.step0_check(a)
    finally:
        shutil.which = real_which
        pl.CFG["python"] = saved
    ev = ["返回 ok=%r，detail=%r" % (ok, detail),
          "CFG['python']=%r 存在=%s" % (sys.executable, Path(sys.executable).exists())]
    if ok is not True:
        fail("便携环境（解释器真实存在 + which 有 ffmpeg）应判 True", ev)
    return ev


@case(6, "step0_check 缺 ffmpeg 时失败且消息含 ffmpeg")
def c06():
    pl = load_pipeline()
    a = SimpleNamespace(account="acc1", media_type="video")
    saved = pl.CFG.get("python")
    real_which = shutil.which
    try:
        pl.CFG["python"] = sys.executable
        shutil.which = lambda _name: None
        ok, detail = pl.step0_check(a)
    finally:
        shutil.which = real_which
        pl.CFG["python"] = saved
    ev = ["返回 ok=%r，detail=%r" % (ok, detail)]
    if ok is not False:
        fail("which 返回 None 时应判 False", ev)
    if "ffmpeg" not in detail:
        fail("失败消息没有点名 ffmpeg", ev)
    return ev


@case(7, "step0_check detail 不含“venv python”（改成解释器/python 措辞）")
def c07():
    pl = load_pipeline()
    a = SimpleNamespace(account="acc1", media_type="video")
    saved = pl.CFG.get("python")
    real_which = shutil.which
    details = []
    try:
        pl.CFG["python"] = sys.executable
        shutil.which = lambda _name: r"C:\x\ffmpeg.exe"
        details.append(pl.step0_check(a)[1])
        shutil.which = lambda _name: None
        details.append(pl.step0_check(a)[1])
    finally:
        shutil.which = real_which
        pl.CFG["python"] = saved
    ev = ["detail（通过）：%r" % details[0], "detail（缺 ffmpeg）：%r" % details[1]]
    bad = [d for d in details if "venv python" in d]
    if bad:
        fail("detail 里仍有“venv python”：%r" % bad, ev)
    if "解释器" not in details[0]:
        fail("通过分支应说“解释器”", ev)
    return ev


@case(8, "回归 test_orchestrator.py 退出 0")
def c08():
    if not TEST_ORCH.is_file():
        fail("单测不存在：%s" % TEST_ORCH)
    rc, out = run_cmd([PIPELINE_PY, TEST_ORCH], timeout=600)
    ev = ["%s -> exit=%s" % (TEST_ORCH, rc)] + tail(out, 8)
    if rc != 0:
        fail("test_orchestrator.py 退出码 %s" % rc, ev)
    if "Traceback" in out:
        fail("输出里出现 Traceback", ev)
    return ev


def run_acceptance() -> tuple[object, str, dict]:
    rc, out = run_cmd([PIPELINE_PY, TEST_ACCEPT], timeout=900)
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    failures = re.findall(r"^(?:FAIL|ERROR): (test_\w+)", out, re.M)
    info = {
        "rc": rc,
        "ran": int(ran.group(1)) if ran else None,
        "failures": failures,
        "known_only": (bool(failures) and set(failures) == {KNOWN_RED}),
        "ok": (rc == 0 and "OK" in out),
        "out": out,
    }
    return rc, out, info


@case(9, "回归 test_acceptance_regressions.py（CRD-0006 已知红 1 条 → WAIT）")
def c09():
    rc, out, info = run_acceptance()
    ev = ["%s -> exit=%s，Ran=%s，失败/错误=%r"
          % (TEST_ACCEPT, rc, info["ran"], info["failures"])] + tail(out, 10)
    if info["ran"] is None:
        fail("没有解析到 unittest 的 Ran N tests 汇总", ev)
    if info["ok"]:
        return ev
    if info["known_only"]:
        raise CaseWait("WAIT-CRD-0006（待另一 QA 更新）：只有 %s，其余 %d 条通过"
                       % (KNOWN_RED, info["ran"] - 1), ev)
    fail("test_acceptance_regressions.py 有非 CRD-0006 的失败：%r" % (info["failures"],), ev)


@case(10, "QA 入口：pipeline 解释器跑 qa\\T-21\\run.py；最后再跑一次 acceptance")
def c10():
    actual = Path(sys.executable).resolve()
    expect = PIPELINE_PY.resolve()
    ev = ["sys.executable=%s" % actual, "期望=%s" % expect,
          "脚本=%s" % Path(__file__).resolve()]
    if actual != expect:
        fail("不是用清单规定的解释器在跑（%s）" % actual, ev)
    if Path(__file__).resolve() != (QA_DIR / "run.py").resolve():
        fail("入口不是 qa\\T-21\\run.py", ev)

    # Lead 要求：最后再跑一次；如果已经绿了，第 9 条按绿判。
    rc, out, info = run_acceptance()
    ev += ["最终重跑：exit=%s，Ran=%s，失败/错误=%r"
           % (rc, info["ran"], info["failures"])] + tail(out, 6)
    if info["ran"] is None:
        fail("最终重跑没有解析到 Ran N tests", ev)
    final_status = "PASS" if info["ok"] else ("WAIT" if info["known_only"] else "FAIL")
    for i, (status, num, desc) in enumerate(RESULTS):
        if num == 9:
            RESULTS[i] = (final_status, num, desc)
    if final_status == "PASS":
        ev.append("第 9 条最终重跑已绿：按绿判 PASS（不再是 WAIT）")
    elif final_status == "WAIT":
        ev.append("第 9 条最终重跑仍只红 %s：保持 WAIT-CRD-0006（待另一 QA 更新）" % KNOWN_RED)
    else:
        ev.append("第 9 条最终重跑出现非 CRD-0006 的失败：按 FAIL 判 %r" % (info["failures"],))
    return ev


# ═══════════════════════════════ main ═══════════════════════════════
def main() -> int:
    print("qa/T-21/run.py — T-21 独立验收（M2-qa-plan T-21 1..10）")
    print("仓库：%s" % REPO)
    print("解释器：%s" % sys.executable)
    print("")
    for fn in (c01, c02, c03, c04, c05, c06, c07, c08, c09, c10):
        fn()

    total = len(RESULTS)
    passed = sum(1 for s, _n, _d in RESULTS if s == "PASS")
    wait = [r for r in RESULTS if r[0] == "WAIT"]
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("")
    if wait:
        print("WAIT-CRD-0006（待另一 QA 更新）：%s"
              % ", ".join("%d.%s" % (n, d) for _s, n, d in wait))
    print("通过 %d / %d（FAIL %d，WAIT %d）%s"
          % (passed, total, len(failed), len(wait),
             "；失败：%s" % ", ".join("%d.%s" % (n, d) for _s, n, d in failed) if failed else ""))
    if failed:
        print("T-21 有 FAIL，退出码 1")
        return 1
    if wait:
        print("T-21 无 FAIL；1 条 WAIT-CRD-0006 不计失败（待另一 QA 更新后复跑应变绿），退出码 0")
        return 0
    print("T-21 全部通过，退出码 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
