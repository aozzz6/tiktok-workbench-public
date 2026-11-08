# -*- coding: utf-8 -*-
"""T-20 `batch_transcribe_fw.py` 后端单测：只走 faster-whisper，模型走 `model_cache`。

覆盖 T-20 DoD 1~4：
  (a) `--backend openai` → 退出 2、stderr 含「只带 faster-whisper」、无 Traceback/ModuleNotFoundError；
  (b) 无模型 + `WB_MODEL_DOWNLOAD=0` → 退出 2、含「模型」「HF_ENDPOINT」、无 Traceback、不去联网；
  (c) `ctranslate2` 导入失败（PYTHONPATH 里放一个抛 ImportError 的桩）→ 退出 2、
      一句可读中文含 `ctranslate2` 与「这台机器」、无 Traceback；
  (d) 空音频目录 → 退出 0、stdout 末行 JSON `total==0`（不需要模型）；
  (e) `--backend auto` 与 `faster` 都落到 faster 分支（打印行以 `backend=faster` 开头；本用例把
      ctranslate2 打桩成"没有 CUDA"，所以整行必须一致、`device=cpu`），且**不 import torch/whisper**
      （PYTHONPATH 里放毒桩：真被 import 就会带着「simulated …」出现在 stderr）；
      `choose_runtime()` 纯函数表：auto 只有"有 CUDA 设备 **且** cuDNN 可用"才走 GPU，faster 固定 CPU；
  静态：没有 `setdefault("HF_HUB_OFFLINE", "1")`；`import model_cache` 与 `ensure_model(...)`
        在 `import faster_whisper` 之前；没有 `^ *import (whisper|torch)`；
        docstring 写明「只带 faster-whisper」与 requirements / requirements-portable 的关系。

全部用子进程跑真脚本；**不联网、不加载真模型**（`--model-dir` 指向一个只有 1 GB 稀疏
`model.bin` 的桩目录）。`ctranslate2` 的失败在 import faster_whisper 时就会发生，所以 (c)/(e)
不用真的加载模型。

跑法（仓库根）：`pipeline\\.venv\\Scripts\\python.exe pipeline\\tests\\test_transcribe_backend.py`
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # pipeline/tests
PIPE = HERE.parent                               # pipeline
SCRIPT = Path(os.environ.get("WB_T20_SCRIPT",
                             str(PIPE / "scripts" / "batch_transcribe_fw.py")))
PY = sys.executable
MIN_MODEL_BYTES = 1_000_000_000

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
    except Exception as exc:                    # noqa: BLE001  失败要一句话说清
        _fail += 1
        print(f"  FAIL {name} -> {exc}")
        return
    _ok += 1
    print(f"  ok   {name}")


def assert_(cond, message=""):
    if not cond:
        raise AssertionError(message or "断言失败")


def dec(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw or ""


def base_env(**over):
    """剥掉 HF_*/WB_*/PYTHON*，只留系统变量；None 表示显式清空。"""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("HF_", "WB_", "PYTHON"))}
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in over.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def run_script(args, env=None, timeout=180):
    proc = subprocess.run([PY, str(SCRIPT)] + [str(a) for a in args],
                          cwd=str(PIPE), env=env or base_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc


def make_audio(root, name="acc1_v1.mp3"):
    audio = Path(root) / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    (audio / name).write_bytes(b"fake-audio-bytes")
    return audio


def make_empty_audio(root):
    audio = Path(root) / "audio_empty"
    audio.mkdir(parents=True, exist_ok=True)
    return audio


def make_model_dir(root):
    """`--model-dir` 的桩：只要一个 >= 1 GB 的稀疏 model.bin（秒建、不占盘）。"""
    model = Path(root) / "model"
    model.mkdir(parents=True, exist_ok=True)
    with open(model / "model.bin", "wb") as fh:
        fh.truncate(MIN_MODEL_BYTES)
    return model


def make_stub(root, package, message):
    """PYTHONPATH 桩：import 该包就抛 ImportError(message)。"""
    stub = Path(root) / ("stub_" + package)
    (stub / package).mkdir(parents=True, exist_ok=True)
    (stub / package / "__init__.py").write_text(
        'raise ImportError("%s")\n' % message, encoding="utf-8")
    return stub


def no_traceback(text, extra_tokens=()):
    low = dec(text).lower()
    assert_("traceback" not in low, f"不该出现 Traceback：{dec(text)[-700:]!r}")
    for token in extra_tokens:
        assert_(token.lower() not in low, f"不该出现 {token}：{dec(text)[-700:]!r}")


# ───────────────────────────── (a) --backend openai ─────────────────────────────
def case_openai_gone():
    tmp = Path(tempfile.mkdtemp(prefix="wb_t20_a_"))
    try:
        p = run_script(["--audio-dir", make_audio(tmp), "--backend", "openai"],
                       env=base_env(XDG_CACHE_HOME=None, HF_HOME=None))
        assert_(p.returncode == 2, f"退出码 {p.returncode}（期望 2）；stderr={dec(p.stderr)[-700:]!r}")
        assert_("只带 faster-whisper" in dec(p.stderr),
                f"stderr 没有「只带 faster-whisper」：{dec(p.stderr)[-700:]!r}")
        no_traceback(p.stderr, ("ModuleNotFoundError",))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────── (b) 无模型 + 禁止下载 → 退 2 ────────────────────────
def case_missing_model_no_download():
    tmp = Path(tempfile.mkdtemp(prefix="wb_t20_b_"))
    try:
        env = base_env(HF_HOME=str(tmp / "hf_empty"), WB_MODEL_DOWNLOAD="0",
                       XDG_CACHE_HOME=None, HF_HUB_CACHE=None,
                       HUGGINGFACE_HUB_CACHE=None, HF_HUB_OFFLINE=None)
        p = run_script(["--audio-dir", make_audio(tmp), "--backend", "faster"], env=env)
        err = dec(p.stderr)
        assert_(p.returncode == 2, f"退出码 {p.returncode}（期望 2）；stderr={err[-700:]!r}")
        assert_("模型" in err, f"stderr 没有「模型」：{err[-700:]!r}")
        assert_("HF_ENDPOINT" in err, f"stderr 没有「HF_ENDPOINT」：{err[-700:]!r}")
        assert_("正在下载" not in err, f"禁止下载时不该出现联网下载：{err[-700:]!r}")
        no_traceback(p.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────── (c) ctranslate2 导入失败 → 退 2 ───────────────────────
def case_ctranslate2_import_failure():
    tmp = Path(tempfile.mkdtemp(prefix="wb_t20_c_"))
    try:
        stub = make_stub(tmp, "ctranslate2", "simulated: ctranslate2 unavailable on this machine")
        env = base_env(PYTHONPATH=str(stub), XDG_CACHE_HOME=None, HF_HOME=None)
        p = run_script(["--audio-dir", make_audio(tmp), "--backend", "faster",
                        "--model-dir", make_model_dir(tmp)], env=env)
        err = dec(p.stderr)
        assert_(p.returncode == 2, f"退出码 {p.returncode}（期望 2）；stderr={err[-700:]!r}")
        assert_("ctranslate2" in err, f"stderr 没提 ctranslate2：{err[-700:]!r}")
        assert_("这台机器" in err, f"stderr 不是一句可读中文（缺「这台机器」）：{err[-700:]!r}")
        no_traceback(p.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────── (d) 空音频目录 → 退 0 + total 0 ────────────────────────
def case_empty_audio_dir():
    tmp = Path(tempfile.mkdtemp(prefix="wb_t20_d_"))
    try:
        env = base_env(HF_HOME=str(tmp / "hf_empty"), WB_MODEL_DOWNLOAD="0",
                       XDG_CACHE_HOME=None, HF_HUB_CACHE=None,
                       HUGGINGFACE_HUB_CACHE=None, HF_HUB_OFFLINE=None)
        p = run_script(["--audio-dir", make_empty_audio(tmp), "--backend", "faster"], env=env)
        out = dec(p.stdout).strip().splitlines()
        assert_(p.returncode == 0, f"退出码 {p.returncode}（期望 0）；stderr={dec(p.stderr)[-700:]!r}")
        assert_(out, "stdout 为空（应有末行 JSON 摘要）")
        summary = json.loads(out[-1])
        assert_(summary.get("total") == 0, f"末行 JSON 的 total 应为 0：{summary!r}")
        assert_("正在下载" not in dec(p.stderr), "空目录不该碰模型/下载")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ────────────── (e) --backend auto vs faster；且不 import torch/whisper ──────────────
def case_auto_equals_faster():
    tmp = Path(tempfile.mkdtemp(prefix="wb_t20_e_"))
    try:
        audio = make_audio(tmp)
        model = make_model_dir(tmp)
        ct2 = make_stub(tmp, "ctranslate2", "simulated: ctranslate2 unavailable")
        poison = [str(ct2), str(make_stub(tmp, "torch", "simulated torch import")),
                  str(make_stub(tmp, "whisper", "simulated whisper import"))]
        env = base_env(PYTHONPATH=os.pathsep.join(poison), XDG_CACHE_HOME=None, HF_HOME=None)
        auto = run_script(["--audio-dir", audio, "--backend", "auto", "--model-dir", model], env=env)
        fast = run_script(["--audio-dir", audio, "--backend", "faster", "--model-dir", model], env=env)

        def backend_line(proc):
            for line in dec(proc.stderr).splitlines():
                if "backend=" in line:
                    return line[line.index("backend="):].strip()
            return None

        line_auto, line_fast = backend_line(auto), backend_line(fast)
        assert_(line_auto is not None, f"auto 没打印 backend= 行：{dec(auto.stderr)[-700:]!r}")
        assert_(line_fast is not None, f"faster 没打印 backend= 行：{dec(fast.stderr)[-700:]!r}")
        # 只比 `backend=faster` 前缀：真 GPU 机器上 auto 可能是 device=cuda，而 faster 固定 CPU；
        # 本用例把 ctranslate2 打桩了（= 没有 CUDA），所以整行也必须一致。
        assert_(line_auto.startswith("backend=faster"), f"auto 的 backend 行不是 faster：{line_auto!r}")
        assert_(line_fast.startswith("backend=faster"), f"faster 的 backend 行不是 faster：{line_fast!r}")
        assert_(line_auto == line_fast,
                f"无 CUDA 环境里 auto 与 faster 必须整行一致：{line_auto!r} vs {line_fast!r}")
        assert_("device=cpu" in line_auto, f"无 CUDA 环境应选 CPU：{line_auto!r}")
        for proc, label in ((auto, "auto"), (fast, "faster")):
            err = dec(proc.stderr).lower()
            assert_("simulated torch" not in err, f"{label} 走通了却 import 了 torch")
            assert_("simulated whisper" not in err, f"{label} 走通了却 import 了 whisper")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_choose_runtime():
    """纯函数表：auto 只有在"有 CUDA 设备 **且** cuDNN 可用"时才走 GPU；faster 固定 CPU。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bt_t20", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    table = [
        # (backend, device, compute_type, gpu_compute_type, cuda_devices, cudnn_ok) -> (device, ctype)
        (("auto", "auto", "int8", "float16", 1, True), ("cuda", "float16")),
        (("auto", "auto", "int8", "int8_float16", 1, True), ("cuda", "int8_float16")),
        (("auto", "auto", "int8", "float16", 0, True), ("cpu", "int8")),
        (("auto", "auto", "int8", "float16", 1, False), ("cpu", "int8")),   # 有卡但 cuDNN 不可用
        (("faster", "auto", "int8", "float16", 1, True), ("cpu", "int8")),  # 显式 faster 不跳 GPU
        (("auto", "cpu", "int8", "float16", 1, True), ("cpu", "int8")),     # --device cpu 优先
        (("auto", "cuda", "int8", "float16", 0, True), ("cuda", "float16")),  # --device cuda 优先
    ]
    for args, want in table:
        got = tuple(module.choose_runtime(*args))
        assert_(got == want, f"choose_runtime{args} = {got}，期望 {want}")


# ─────────────────────────────── 静态断言（DoD 2/3/4） ───────────────────────────────
SRC = SCRIPT.read_text(encoding="utf-8")


def case_no_setdefault_offline():
    assert_('setdefault("HF_HUB_OFFLINE"' not in SRC, '仍有 setdefault("HF_HUB_OFFLINE", ...)')
    assert_("setdefault('HF_HUB_OFFLINE'" not in SRC, "仍有 setdefault('HF_HUB_OFFLINE', ...)")


def line_of(pattern, start=1):
    rx = re.compile(pattern)
    for no, line in enumerate(SRC.splitlines(), 1):
        if no >= start and rx.search(line):
            return no
    return None


def case_model_cache_order():
    i_import = line_of(r"^\s*import\s+model_cache\b")
    assert_(i_import is not None, "顶层没有 import model_cache")
    i_ensure = line_of(r"\bensure_model\s*\(", start=i_import)
    assert_(i_ensure is not None, "import model_cache 之后没有 ensure_model(...)")
    i_fw = line_of(r"^\s*(from\s+faster_whisper\s+import|import\s+faster_whisper)\b")
    assert_(i_fw is not None, "没有 import faster_whisper")
    assert_(i_import < i_ensure < i_fw,
            f"顺序必须是 import model_cache({i_import}) < ensure_model({i_ensure}) < faster_whisper({i_fw})")


def case_no_torch_whisper_import():
    m = re.search(r"(?m)^\s*import\s+(whisper|torch)\b", SRC)
    assert_(m is None, f"仍有兜底 import：{m.group(0).strip() if m else ''}")


def case_docstring():
    assert_("只带 faster-whisper" in SRC, "docstring 没写「只带 faster-whisper」")
    assert_("requirements-portable.txt" in SRC, "docstring 没提 requirements-portable.txt")
    assert_("requirements.txt" in SRC, "docstring 没提 requirements.txt")


print("[1] DoD 1：失败面与空目录")
check("(a) --backend openai → 退 2、含「只带 faster-whisper」、无 Traceback", case_openai_gone)
check("(b) 无模型 + WB_MODEL_DOWNLOAD=0 → 退 2、含「模型」「HF_ENDPOINT」、不联网", case_missing_model_no_download)
check("(c) ctranslate2 导入失败 → 退 2、含 ctranslate2 与「这台机器」、无 Traceback", case_ctranslate2_import_failure)
check("(d) 空音频目录 → 退 0、末行 JSON total==0", case_empty_audio_dir)
check("(e) --backend auto 与 faster 同走 faster 分支（无 CUDA 时整行一致）、不 import torch/whisper", case_auto_equals_faster)

print("[2] DoD 2/3/4：静态契约")
check('(DoD 2) 没有 setdefault("HF_HUB_OFFLINE", "1")', case_no_setdefault_offline)
check("(DoD 2) import model_cache < ensure_model(...) < import faster_whisper", case_model_cache_order)
check("(DoD 3) 没有 `^ *import (whisper|torch)`", case_no_torch_whisper_import)
check("(DoD 3) choose_runtime：auto 只在「有 CUDA 设备 + cuDNN 可用」时走 GPU；faster 固定 CPU",
      case_choose_runtime)
check("(DoD 4) docstring：只带 faster-whisper + requirements 两者关系", case_docstring)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(0 if _fail == 0 else 1)
