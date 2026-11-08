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

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
