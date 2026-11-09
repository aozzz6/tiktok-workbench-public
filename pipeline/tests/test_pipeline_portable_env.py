# -*- coding: utf-8 -*-
"""test_pipeline_portable_env.py — T-21：流水线步骤对便携环境说真话

背景（ADR-0008 三处强制离线表）：`HF_HUB_OFFLINE` 由 `pipeline/scripts/model_cache.py`
按"本机有没有模型"决定；`pipeline.py` 再给子进程钉死 1，精简包首次下载就必挂。

DoD（任务表 T-21）：
  1. `step5_transcribe(ns)` 调 `_subprocess_step` 时传入的 `env` 里**没有** `HF_HUB_OFFLINE`；
     命令里仍含 `--backend faster` / `--model medium` / `--compute-type int8` / `--lang en`。
  2. `pipeline.py` 任何地方都**不再设置** `HF_HUB_OFFLINE`（只在 `#` 注释里出现，
     且注释写明"由 model_cache 决定"）。
  3. 便携环境的前置校验：`shutil.which` 有值 + `CFG["python"]` 真实存在 → `(True, …)`；
     `which` 返回 None → `(False, …)` 且消息含 `ffmpeg`；
     `CFG["python"] == sys.executable` 时 detail **不含** `venv python`（说"解释器"）。

运行：`pipeline\\.venv\\Scripts\\python.exe pipeline\\tests\\test_pipeline_portable_env.py`
只打桩：不真的起转写、不联网（`_subprocess_step` / `shutil.which` 全程替换成假的）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))
import pipeline as pl  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass

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
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _opt(cmd: list[str], name: str):
    """取 `--flag value` 的 value（没有就 None）。"""
    if name not in cmd:
        return None
    i = cmd.index(name)
    return cmd[i + 1] if i + 1 < len(cmd) else None


print("test_pipeline_portable_env — T-21：便携环境说真话")

# ── 1. step5 不再把 HF_HUB_OFFLINE 钉给子进程 ────────────────────────
def case_step5_no_offline():
    audio = Path(tempfile.mkdtemp(prefix="t21_audio_")) / "acc1_audio"
    ns = SimpleNamespace(account="acc1", lang="en", backend="faster",
                         audio_dir=str(audio), media_type="video")
    cap: dict = {}

    def fake_sub(step, cmd, env=None):
        cap.update(step=step, cmd=list(cmd), env=env)
        return True, '{"total": 1, "done": ["v1"], "suspect_hallucination": []}'

    real = pl._subprocess_step
    pl._subprocess_step = fake_sub
    try:
        ok, detail = pl.step5_transcribe(ns)
    finally:
        pl._subprocess_step = real

    assert_(ok is True, f"step5_transcribe 应返回成功；detail={detail!r}")
    assert_(cap.get("step") == "s5", f"应走 s5；实得 {cap.get('step')!r}")
    env = cap.get("env")
    assert_(not env or "HF_HUB_OFFLINE" not in env,
            f"子进程 env 里不许再钉 HF_HUB_OFFLINE（应由 model_cache 决定）：{env!r}")

    cmd = cap.get("cmd") or []
    assert_(cmd[0] == pl.CFG["python"], f"命令第一个参数应是解释器：{cmd[:1]!r}")
    assert_(cmd[1] == pl.CFG["transcribe"], f"第二个参数应是转写脚本：{cmd[1:2]!r}")
    for flag, want in (("--backend", "faster"), ("--model", "medium"),
                       ("--compute-type", "int8"), ("--lang", "en")):
        assert_(_opt(cmd, flag) == want, f"{flag} 应为 {want}，实得 {_opt(cmd, flag)!r}；cmd={cmd}")
    assert_(str(audio) in cmd, f"命令里应带 audio_dir：{audio}")


check("1 step5_transcribe：env 里没有 HF_HUB_OFFLINE，四个参数仍在", case_step5_no_offline)


# ── 2. 源码里不再设置 HF_HUB_OFFLINE（只允许 # 注释） ────────────────
def case_source_no_offline_assignment():
    src = (PIPE / "pipeline.py").read_text(encoding="utf-8")
    hits = [(i + 1, ln) for i, ln in enumerate(src.splitlines()) if "HF_HUB_OFFLINE" in ln]
    assert_(hits, "至少要留一条注释说明「离线开关由 model_cache 决定」，否则后来的人会再加回去")

    bad = [(n, ln.strip()) for n, ln in hits if not ln.strip().startswith("#")]
    assert_(not bad,
            f"HF_HUB_OFFLINE 还出现在代码或文档字符串里（只允许 # 注释）：{bad}")

    assert_(any("model_cache" in ln for _n, ln in hits),
            f"注释里要写明由 model_cache 决定：{[ln.strip() for _n, ln in hits]}")


check("2 pipeline.py 不再设置 HF_HUB_OFFLINE（只在 # 注释里，且指向 model_cache）",
      case_source_no_offline_assignment)


# ── 3. 前置校验对便携环境说真话 ──────────────────────────────────────
def case_step0_portable():
    import shutil

    a = SimpleNamespace(account="acc1", media_type="video")
    saved_py = pl.CFG.get("python")
    real_which = shutil.which
    try:
        pl.CFG["python"] = sys.executable        # 便携包：解释器就是自己
        shutil.which = lambda _name: r"C:\x\ffmpeg.exe"
        ok, detail = pl.step0_check(a)
        assert_(ok is True, f"ffmpeg + 解释器都在应判通过；detail={detail!r}")
        assert_("venv python" not in detail, f"便携环境不该说「venv python」：{detail!r}")
        assert_("解释器" in detail, f"应改成「解释器」措辞：{detail!r}")

        shutil.which = lambda _name: None
        ok2, detail2 = pl.step0_check(a)
        assert_(ok2 is False, f"缺 ffmpeg 应判失败；detail={detail2!r}")
        assert_("ffmpeg" in detail2, f"失败消息要点明 ffmpeg：{detail2!r}")
        assert_("venv python" not in detail2, f"失败消息也不该说「venv python」：{detail2!r}")
    finally:
        shutil.which = real_which
        pl.CFG["python"] = saved_py


check("3 step0_check：便携环境（无 venv）说「解释器」；缺 ffmpeg 时判失败并点名",
      case_step0_portable)


# ── 4. 子进程环境：掐掉宿主的 Python 注入，保留 HF_* ─────────────────
def case_child_env_strips_python_injection():
    """安全评审：转写子进程不许带宿主的 `PYTHONPATH`/`PYTHONHOME`/`PYTHONSTARTUP`。

    包里靠 `_setenv.bat` 清这些；开发检出、从终端或计划任务启动时没人清，
    宿主的 `PYTHONPATH` 会进子进程，可能遮蔽包内的 `huggingface_hub`/`faster_whisper`。
    反过来 `HF_*`（`HF_ENDPOINT`/`HF_HOME`/`HF_HUB_OFFLINE`）是设计要传给子进程的，必须留。
    """
    import os
    import subprocess as _sp

    capture: dict = {}

    class _Proc:
        returncode = 0
        stdout = '{"total": 0, "done": [], "suspect_hallucination": []}'
        stderr = ""

    def fake_run(_cmd, **_kw):
        capture.update(kw=_kw)
        return _Proc()

    keys = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
            "HF_ENDPOINT", "HF_HOME", "HF_HUB_OFFLINE")
    saved = {k: os.environ.get(k) for k in keys}
    real_run, real_log = _sp.run, pl.log
    try:
        os.environ["PYTHONPATH"] = r"C:\evil"
        os.environ["PYTHONHOME"] = r"C:\evil2"
        os.environ["PYTHONSTARTUP"] = r"C:\evil3.py"
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ["HF_HOME"] = r"C:\hf-home"
        os.environ["HF_HUB_OFFLINE"] = "0"      # 用户显式设的：要如实转发，不删不改写
        _sp.run = fake_run
        pl.log = lambda *_a, **_k: None          # 别往仓库的 pipeline_run.log 里写
        ok, _detail = pl._subprocess_step("s5", ["x"])
    finally:
        _sp.run, pl.log = real_run, real_log
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert_(ok is True, "打桩后应判成功")
    env = capture.get("kw", {}).get("env")
    assert_(env is not None, "子进程必须拿到一份显式环境（不能再把 env=None 传下去，那样会全量继承宿主）")
    for bad in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        assert_(bad not in env, f"子进程环境里还带着宿主的 {bad}={env.get(bad)!r}")
    assert_(env.get("HF_ENDPOINT") == "https://hf-mirror.com",
            f"HF_ENDPOINT 是设计要传的，不该被删：{env.get('HF_ENDPOINT')!r}")
    assert_(env.get("HF_HOME") == r"C:\hf-home",
            f"HF_HOME 是设计要传的，不该被删：{env.get('HF_HOME')!r}")
    assert_(env.get("HF_HUB_OFFLINE") == "0",
            f"父环境里的离线开关要如实转发（不删、也不改写成 1）：{env.get('HF_HUB_OFFLINE')!r}")
    assert_("PATH" in env, "父环境的普通变量仍要继承（PATH 是子进程找可执行文件用的）")


check("4 子进程环境：删掉 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP，保留 HF_ENDPOINT",
      case_child_env_strips_python_injection)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
