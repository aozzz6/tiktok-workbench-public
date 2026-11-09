# -*- coding: utf-8 -*-
r"""qa/T-20/run.py — T-20（转写只走 faster-whisper）的独立验收用例。

清单来源：`.dsh-crew/jobs/portable-package/M2-qa-plan.md` 的 **T-20** 段（14 条，逐条实现）。
DoD 来源：`docs/tasks/tasks.md` 的 T-20（DoD 1~7，含 (a2)）；执行方式按 CRD-0006 / CRD-0007。

做了什么：
  · 1~6：不加载真模型、不联网，用子进程跑**真脚本**验失败面与空目录（openai 退役 / 缺模型 /
        ctranslate2 导入失败 / 空音频目录 total=0 / auto==faster 且不 import torch）；
  · 7：CRD-0006 授权给 QA 的两处旧断言更新（产品测试文件 `test_acceptance_regressions.py`）——
        静态核对改法 + 跑一遍必须 `Ran 27 tests ... OK`；
  · 8~10：DoD 2/3/4 的静态契约（无 setdefault 离线开关、import 行号顺序、无兜底 import、docstring）；
  · 11~13：**先用当前代码重建 full 包** → 复制到「中文+空格」临时目录 → 剥环境、按 CRD-0007
        先 `call _setenv.bat` 再用包内 `python\python.exe` 断网转写 45 s 英文语音；
        同一段音频再在开发机 `pipeline\.venv` 跑一次，比较两次秒数（包里 ≤ 开发机 ×1.5）；
  · 14：入口自检（必须用 pipeline\.venv 跑、14 条齐全、打印 11/13 的实测秒数）。

只杀自己起的进程（本用例不常驻服务，只有子进程，全部 subprocess 等它结束）；
只写/删自己建的临时目录（`build\portable\qa20` 是构建产物，保留供人工查看，跑完会说明）。

跑法（仓库根）：`pipeline\.venv\Scripts\python.exe qa\T-20\run.py`
调试：可只跑某几条 —— `... qa\T-20\run.py T20-08 T20-10`。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]        # qa/T-20/run.py -> 仓库根
PIPE = REPO / "pipeline"
SCRIPTS = PIPE / "scripts"
SCRIPT = SCRIPTS / "batch_transcribe_fw.py"
PIPE_PY = PIPE / ".venv" / "Scripts" / "python.exe"
WEBAPP_PY = REPO / "webapp" / ".venv" / "Scripts" / "python.exe"
UNIT_TEST = PIPE / "tests" / "test_transcribe_backend.py"
ACCEPT_TEST = PIPE / "tests" / "test_acceptance_regressions.py"
BUILD = REPO / "packaging" / "build_portable.py"

FFMPEG_BIN = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin")
HF_HUB = Path(r"D:\devcache\xdg-cache\huggingface\hub")
SLIM_SITE = REPO / "build" / "portable" / "slim" / "python" / "Lib" / "site-packages"
PKG_ROOT = REPO / "build" / "portable" / "qa20"     # 构建输出（--out）
PKG = PKG_ROOT / "full"
PKG_COPY = Path(r"D:\测试 目录 二\qa20 验证\包")     # 中文+空格，跑完删

VID = "7000000000000000001"
STEM = f"acc1_{VID}"
NET_TOKENS = ("正在下载", "超时", "timeout", "timed out", "Retrying", "retry",
              "URLError", "HTTPError", "ConnectionError", "hf-mirror", "huggingface.co")

_tmpdirs = []
_state = {}
_ok = 0
_fail = 0


# ────────────────────────── 基础设施 ──────────────────────────
def _emit(lines):
    for line in lines or []:
        print(f"        证据：{line}")


def check(cid, title, fn):
    global _ok, _fail
    try:
        evidence = fn() or []
        print(f"PASS {cid} {title}")
        _emit(evidence)
        _ok += 1
    except AssertionError as e:
        print(f"FAIL {cid} {title} -> {e}")
        _fail += 1
    except subprocess.TimeoutExpired as e:
        print(f"FAIL {cid} {title} -> 子进程超时：{e}")
        _fail += 1
    except Exception as e:                                   # noqa: BLE001
        print(f"FAIL {cid} {title} -> {type(e).__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _dec(raw) -> str:
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "cp936", "mbcs"):
        try:
            return (raw or b"").decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return (raw or b"").decode("utf-8", "replace")


def _tmpdir(prefix="t20qa_"):
    try:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir="D:\\"))
    except OSError:
        path = Path(tempfile.mkdtemp(prefix=prefix))
    _tmpdirs.append(path)
    return path


def _clean_env(**over):
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


def _run(argv, cwd=None, env=None, timeout=900):
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd or REPO),
                          capture_output=True, env=env, timeout=timeout)
    out, err = _dec(proc.stdout), _dec(proc.stderr)
    return proc.returncode, out, err, out + err


def _run_script(args, env=None, timeout=900, extra_flags=()):
    """用 pipeline venv 跑真脚本：python [-X importtime] <script> <args>。"""
    return _run([PIPE_PY, *extra_flags, SCRIPT, *args], cwd=PIPE, env=env or _clean_env(),
                timeout=timeout)


def _tail(text, n=8, width=500):
    lines = [ln.strip() for ln in (text or "").replace("\r", "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[-width:]


def _no_traceback(text, extra=()):
    low = text.lower()
    assert_("traceback" not in low, f"不该出现 Traceback：{_tail(text)}")
    for token in extra:
        assert_(token.lower() not in low, f"不该出现 {token}：{_tail(text)}")


def _make_audio(root, name=f"{STEM}.mp3", empty=False):
    audio = Path(root) / ("audio_empty" if empty else "audio")
    audio.mkdir(parents=True, exist_ok=True)
    if not empty:
        (audio / name).write_bytes(b"fake-audio-bytes")
    return audio


def _make_model_dir(root):
    """`--model-dir` 桩：一个 ≥1 GB 的稀疏 model.bin（秒建、几乎不占盘）。"""
    model = Path(root) / "model"
    model.mkdir(parents=True, exist_ok=True)
    with open(model / "model.bin", "wb") as fh:
        fh.truncate(1_000_000_000)
    return model


def _make_stub(root, package, message):
    """PYTHONPATH 毒桩：import 该包就抛 ImportError(message)。"""
    stub = Path(root) / ("stub_" + package)
    (stub / package).mkdir(parents=True, exist_ok=True)
    (stub / package / "__init__.py").write_text(
        'raise ImportError("%s")\n' % message, encoding="utf-8")
    return stub


def _wav_seconds(path):
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def _reset_transcripts(audio_dir):
    """重跑前清掉上一轮的 transcripts/（含断点状态），否则脚本会判「全部已完成」只跑 1 秒。"""
    tr = Path(audio_dir) / "transcripts"
    if tr.is_dir():
        shutil.rmtree(tr, ignore_errors=True)


# ────────────────────────── T20-01 ~ 06：失败面 ──────────────────────────
def t20_01():
    """T-20 单测跑绿（工程师的主证据；QA 只判它真跑了、没崩、全过）。"""
    assert_(UNIT_TEST.is_file(), f"缺少 {UNIT_TEST}")
    rc, out, err, text = _run([PIPE_PY, UNIT_TEST], env=_clean_env(), timeout=900)
    line = next((ln.strip() for ln in reversed(text.splitlines())
                 if re.match(r"通过\s*\d+\s*/\s*\d+", ln.strip())), None)
    assert_(rc == 0, f"退出码 {rc}；输出尾：{_tail(text)}")
    # 注意：单测的 check 名字里就有「无 Traceback」字样，这里只抓真正的 traceback 头
    assert_(not re.search(r"(?m)^Traceback \\(most recent call last\\)", text),
            f"输出里有真 Traceback：{_tail(text)}")
    assert_(line is not None, f"没有『通过 N / N』汇总行：{_tail(text)}")
    m = re.match(r"通过\s*(\d+)\s*/\s*(\d+)", line)
    assert_(m and m.group(1) == m.group(2) and int(m.group(1)) > 0, f"不是全绿：{line}")
    return [f"exit={rc}；{line}", f"命令：pipeline\\.venv\\Scripts\\python.exe {UNIT_TEST.relative_to(REPO)}"]


def t20_02():
    """DoD 1(a)：--backend openai 已退役 → 退出码 2，stderr 含「只带 faster-whisper」，无 Traceback。"""
    tmp = _tmpdir()
    audio = _make_audio(tmp, empty=True)
    rc, out, err, text = _run_script(["--audio-dir", audio, "--backend", "openai"])
    assert_(rc == 2, f"期望退出码 2，实得 {rc}；stderr={_tail(err)}")
    assert_("只带 faster-whisper" in err, f"stderr 没有「只带 faster-whisper」：{_tail(err)}")
    _no_traceback(err, ("ModuleNotFoundError",))
    line = next((ln.strip() for ln in err.splitlines() if "只带 faster-whisper" in ln), "")
    return [f"exit={rc}", f"stderr：{line[:160]}"]


def t20_03():
    """DoD 1(b)：本地没模型 + WB_MODEL_DOWNLOAD=0 → 退出码 2，stderr 含「模型」与「HF_ENDPOINT」。"""
    tmp = _tmpdir()
    audio = _make_audio(tmp)
    env = _clean_env(HF_HOME=str(tmp / "hf_empty"), WB_MODEL_DOWNLOAD="0",
                     XDG_CACHE_HOME=None, HF_HUB_CACHE=None, HUGGINGFACE_HUB_CACHE=None,
                     HF_HUB_OFFLINE=None)
    rc, out, err, text = _run_script(["--audio-dir", audio, "--backend", "faster"], env=env)
    assert_(rc == 2, f"期望退出码 2，实得 {rc}；stderr={_tail(err)}")
    assert_("模型" in err, f"stderr 没提「模型」：{_tail(err)}")
    assert_("HF_ENDPOINT" in err, f"stderr 没提「HF_ENDPOINT」：{_tail(err)}")
    assert_("正在下载" not in err, f"禁止下载时不该出现「正在下载」：{_tail(err)}")
    _no_traceback(err)
    lines = [ln.strip() for ln in err.splitlines() if ln.strip()][:2]
    return [f"exit={rc}"] + [f"stderr：{ln[:170]}" for ln in lines]


def t20_04():
    """DoD 1(c)：ctranslate2 导入失败 → 退出码 2，一句可读中文（含 ctranslate2 与「这台机器」）。"""
    tmp = _tmpdir()
    audio = _make_audio(tmp)
    stub = _make_stub(tmp, "ctranslate2", "simulated: ctranslate2 unavailable on this machine")
    env = _clean_env(PYTHONPATH=str(stub), XDG_CACHE_HOME=None, HF_HOME=None)
    rc, out, err, text = _run_script(["--audio-dir", audio, "--backend", "faster",
                                     "--model-dir", _make_model_dir(tmp)], env=env)
    assert_(rc == 2, f"期望退出码 2，实得 {rc}；stderr={_tail(err)}")
    assert_("ctranslate2" in err, f"stderr 没提 ctranslate2：{_tail(err)}")
    assert_("这台机器" in err, f"不是一句可读中文（缺「这台机器」）：{_tail(err)}")
    _no_traceback(err)
    line = next((ln.strip() for ln in err.splitlines() if "这台机器" in ln), "")
    return [f"exit={rc}", f"stderr：{line[:170]}"]


def t20_05():
    """DoD 1(d)：空音频目录 → 退出码 0，stdout 末行合法 JSON 且 total == 0（不需要模型）。"""
    tmp = _tmpdir()
    audio = _make_audio(tmp, empty=True)
    env = _clean_env(HF_HOME=str(tmp / "hf_empty"), WB_MODEL_DOWNLOAD="0",
                     XDG_CACHE_HOME=None, HF_HUB_CACHE=None, HUGGINGFACE_HUB_CACHE=None)
    rc, out, err, text = _run_script(["--audio-dir", audio, "--backend", "faster"], env=env)
    lines = [ln for ln in out.replace("\r", "").splitlines() if ln.strip()]
    assert_(rc == 0, f"期望退出码 0，实得 {rc}；stderr={_tail(err)}")
    assert_(lines, f"stdout 为空（应有末行 JSON 摘要）：{_tail(text)}")
    try:
        summary = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout 末行不是合法 JSON（{e}）：{lines[-1][:200]!r}") from None
    assert_(summary.get("total") == 0, f"total 应为 0：{summary!r}")
    assert_("正在下载" not in err, f"空目录不该碰模型/下载：{_tail(err)}")
    return [f"exit={rc}；末行 JSON：{lines[-1][:150]}", f"total = {summary.get('total')}"]


def t20_06():
    """DoD 1(e)：--backend auto 与 faster 同走 faster 分支，且都不 import torch/whisper。

    2026-11-06（安全评审后）：产品日志把「有没有真的探测 CUDA」写出来了 ——
    `auto` 探测 → `probe=done cuda_devices=0 cudnn=False`；`faster` 不探 → `probe=skipped`
    （不再谎报 0/False）。所以两行**只在 `probe=` 段不同是对的**：本条只比
    `backend=/device=/compute_type=/model=` 四段，并额外要求 faster 那行是 `probe=skipped`。
    """
    tmp = _tmpdir()
    audio = _make_audio(tmp)
    model = _make_model_dir(tmp)
    poison = [str(_make_stub(tmp, "ctranslate2", "simulated: ctranslate2 unavailable")),
              str(_make_stub(tmp, "torch", "simulated torch import")),
              str(_make_stub(tmp, "whisper", "simulated whisper import"))]
    env = _clean_env(PYTHONPATH=os.pathsep.join(poison), XDG_CACHE_HOME=None, HF_HOME=None)
    auto = _run_script(["--audio-dir", audio, "--backend", "auto", "--model-dir", model], env=env)
    fast = _run_script(["--audio-dir", audio, "--backend", "faster", "--model-dir", model], env=env)

    def backend_line(text):
        for line in text.splitlines():
            if "backend=" in line:
                return line[line.index("backend="):].strip()
        return None

    def segs(line, keys=("backend", "device", "compute_type", "model", "probe")):
        out = {}
        for key in keys:
            m = re.search(rf"(?:^|\s){key}=(\S+)", line or "")
            out[key] = m.group(1) if m else None
        return out

    la, lf = backend_line(auto[2]), backend_line(fast[2])
    assert_(la is not None, f"auto 没打印 backend= 行：{_tail(auto[3])}")
    assert_(lf is not None, f"faster 没打印 backend= 行：{_tail(fast[3])}")
    sa, sf = segs(la), segs(lf)
    same_keys = ("backend", "device", "compute_type", "model")
    missing = [k for k in same_keys if sa.get(k) is None or sf.get(k) is None]
    assert_(not missing, f"两行缺少这些段 {missing}：auto={la!r} faster={lf!r}")
    diff = {k: (sa[k], sf[k]) for k in same_keys if sa[k] != sf[k]}
    assert_(not diff, f"auto 与 faster 的 {list(same_keys)} 必须一致，实得差异 {diff}")
    assert_(sa["backend"] == "faster", f"auto 的 backend 段不是 faster：{la!r}")
    assert_("device=cpu" in la, f"无 CUDA 环境应选 CPU：{la!r}")
    # 唯一允许不同的就是 probe=：auto 真探测、faster 明确跳过
    assert_(sa.get("probe") == "done",
            f"auto 应当真的探测（probe=done …），实得 {la!r}")
    assert_(sf.get("probe") == "skipped",
            f"faster 那行必须是 probe=skipped（不再谎报 0/False），实得 {lf!r}")
    for proc, label in ((auto, "auto"), (fast, "faster")):
        err = proc[2]
        assert_("simulated torch" not in err, f"{label} 分支 import 了 torch")
        assert_("simulated whisper" not in err, f"{label} 分支 import 了 whisper")

    # 另用 -X importtime 静态复核：模块级 import 里不该出现 torch/whisper 模块
    it = _run([PIPE_PY, "-X", "importtime", SCRIPT, "--audio-dir", _make_audio(tmp, empty=True),
               "--backend", "auto"],
              cwd=PIPE, env=_clean_env(HF_HOME=str(tmp / "hf2"), WB_MODEL_DOWNLOAD="0",
                                       XDG_CACHE_HOME=None), timeout=300)
    bad = [ln for ln in it[3].splitlines()
           if re.match(r"^import time:.*\|\s*(torch|whisper)(\.|\s|$)", ln.strip())]
    assert_(not bad, f"importtime 里出现 torch/whisper 导入：{bad[:3]}")
    return [f"auto  ：{la}",
            f"faster：{lf}",
            f"四段一致：{ {k: sa[k] for k in same_keys} }（probe= 段允许不同："
            f"auto={sa.get('probe')} / faster={sf.get('probe')}）",
            "importtime：torch/whisper 命中 0 行"]


# ────────────────────────── T20-07：CRD-0006 的 QA 改动 ──────────────────────────
FASTER_TESTS = ("test_stale_done_does_not_skip_new_audio",
                "test_missing_transcript_is_regenerated",
                "test_successful_retry_clears_old_error",
                "test_transcription_failure_returns_nonzero",
                "test_japanese_language_alias_matches_whisper")


def t20_07():
    """DoD 1(a2)：QA 已按 CRD-0006 改旧断言 + 给 5 条 faster 用例打桩，文件整体 Ran 27 tests OK。"""
    assert_(ACCEPT_TEST.is_file(), f"缺少 {ACCEPT_TEST}")
    src = ACCEPT_TEST.read_text(encoding="utf-8", errors="replace")

    # 1) 被改写的用例：新断言是"退 2 + 可读提示"，旧断言（whisper.load_model 被调用）必须消失
    m = re.search(r"(?ms)^    def test_cpu_backend_explicitly_uses_cpu\(self\):(.*?)(?=^    def |\Z)", src)
    assert_(m, "找不到 test_cpu_backend_explicitly_uses_cpu")
    block = m.group(1)
    assert_("assertEqual(rc,2" in block.replace(" ", ""),
            f"新断言里没有『退出码 2』：{_tail(block, 6)}")
    assert_("只带 faster-whisper" in block, "新断言里没有『只带 faster-whisper』")
    assert_("Traceback" in block, "新断言里没有『不含 Traceback』")
    code = re.sub(r'""".*?"""', "", block, flags=re.S)   # 去掉说明性 docstring，只看可执行代码
    stale = [tok for tok in ("load_model", "assert_called_once", "Mock(", "unittest.mock import Mock") if tok in code]
    assert_(not stale, f"旧断言（whisper.load_model 被调用）还在代码里：{stale}")
    assert_("CRD-0006" in block, "没写明这是 CRD-0006 授权的改写")

    # 2) 5 条 faster 用例共用的 helper 里把 ensure_model 打桩
    hm = re.search(r"(?ms)^    def transcribe_run\(self.*?(?=^    def |\Z)", src)
    assert_(hm, "找不到 transcribe_run helper")
    helper = hm.group(0)
    assert_("ensure_model" in helper, f"transcribe_run 里没有 ensure_model 打桩：{_tail(helper, 8)}")
    assert_("bt.model_cache" in helper or "model_cache" in helper, "打桩点不是 bt.model_cache")
    called = len(re.findall(r"self\.transcribe_run\(", src))
    assert_(called == 5, f"用 transcribe_run 的用例应恰好 5 条，实得 {called}")
    missing = [n for n in FASTER_TESTS if f"def {n}(self)" not in src]
    assert_(not missing, f"这 5 条 faster 用例缺了：{missing}")

    # 3) 整体必须绿，且测试数不能少
    rc, out, err, text = _run([PIPE_PY, ACCEPT_TEST], env=_clean_env(), timeout=900)
    ran = next((ln.strip() for ln in text.splitlines() if re.match(r"Ran\s+\d+\s+tests", ln.strip())), None)
    assert_(rc == 0, f"退出码 {rc}；输出尾：{_tail(text)}")
    assert_(ran is not None, f"没有『Ran N tests』行：{_tail(text)}")
    assert_("OK" in text and "FAILED" not in text, f"不是 OK：{ran}")
    assert_(not re.search(r"(?m)^(ERROR|FAIL):", text), f"有失败/错误用例：{_tail(text, 11)}")
    assert_("Ran 27 tests" in ran, f"测试数变了（应 27）：{ran}")
    return [f"test_cpu_backend_explicitly_uses_cpu：新断言 = 退 2 + 「只带 faster-whisper」+ 无 Traceback（旧 load_model 断言已删）",
            f"transcribe_run 里 patch.object(bt.model_cache,'ensure_model',...)；用它的是 5 条：{', '.join(FASTER_TESTS)}",
            f"exit={rc}；{ran}；OK"]


# ────────────────────────── T20-08 ~ 10：静态契约 ──────────────────────────
def _script_src():
    assert_(SCRIPT.is_file(), f"缺少 {SCRIPT}")
    return SCRIPT.read_text(encoding="utf-8", errors="replace")


def t20_08():
    """DoD 2：模块级不再 setdefault("HF_HUB_OFFLINE")；import model_cache < ensure_model < faster_whisper。"""
    src = _script_src()
    hits = [pat for pat in ('setdefault("HF_HUB_OFFLINE"', "setdefault('HF_HUB_OFFLINE'") if pat in src]
    assert_(not hits, f"文件里仍有离线 setdefault：{hits}")

    def line_of(pattern, start=1):
        rx = re.compile(pattern)
        for no, line in enumerate(src.splitlines(), 1):
            if no >= start and rx.search(line):
                return no
        return None

    i_import = line_of(r"^\s*import\s+model_cache\b")
    i_ensure = line_of(r"\bensure_model\s*\(", start=i_import or 1)
    i_fw = line_of(r"^\s*(from\s+faster_whisper\s+import|import\s+faster_whisper)\b")
    assert_(i_import is not None, "顶层没有 import model_cache")
    assert_(i_ensure is not None, "import model_cache 之后没有 ensure_model(...)")
    assert_(i_fw is not None, "没有 import faster_whisper")
    assert_(i_import < i_ensure < i_fw,
            f"顺序必须是 import model_cache({i_import}) < ensure_model({i_ensure}) < faster_whisper({i_fw})")
    return ["setdefault(\"HF_HUB_OFFLINE\", ...) 命中 0 行",
            f"行号：import model_cache L{i_import} < ensure_model L{i_ensure} < from faster_whisper L{i_fw}"]


def t20_09():
    """DoD 3：没有兜底 `import whisper` / `import torch`；CUDA 探测只能用 find_spec。"""
    src = _script_src()
    bad = [m.group(0).strip() for m in re.finditer(r"(?m)^\s*import\s+(whisper|torch)\b", src)]
    assert_(not bad, f"仍有兜底 import：{bad}")
    finds = [i + 1 for i, ln in enumerate(src.splitlines()) if "find_spec" in ln]
    return ["`^ *import (whisper|torch)` 命中 0 行",
            f"find_spec 命中行：{finds or '无（本文件连 CUDA 探测都不需要 import）'}"]


def t20_10():
    """DoD 4：头部注释写明只带 faster-whisper / CPU int8 / openai 已不可用(退 2) / 两份 requirements 关系。"""
    src = _script_src()
    head = "\n".join(src.splitlines()[:40])
    checks = {
        "只带 faster-whisper": "只带 faster-whisper" in head,
        "CPU int8": "CPU int8" in head,
        "openai 后端已不可用（退出码 2）": "openai 后端已不可用" in head and "退出码 2" in head,
        "requirements.txt（开发用，带 torch）": "requirements.txt" in head and "开发" in head,
        "requirements-portable.txt（随包，不带）": "requirements-portable.txt" in head and "随包" in head,
    }
    bad = [k for k, ok in checks.items() if not ok]
    assert_(not bad, f"头部注释缺：{bad}")
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()][:6]
    return [f"五项齐全：{list(checks)}"] + [f"头部：{ln[:150]}" for ln in lines[:3]]


# ────────────────────────── T20-11 ~ 13：full 包断网转写 ──────────────────────────
PASSAGE = (
    "The portable workbench records benchmark accounts and keeps a ledger. "
    "Today we check that transcription works without any network connection. "
    "The model is stored inside the package, so the speech engine reads it from the local disk. "
    "This sentence is only here to make the recording long enough for the acceptance test. "
    "We speak slowly and clearly so the transcript has several timestamped lines. "
    "If you can read this transcript, the offline transcription path is working as expected. "
    "The quick brown fox jumps over the lazy dog near the river bank. "
    "Engineers measure the elapsed time for the same audio on the development machine and inside the package."
)


def _make_speech(target_s=45.0):
    """SAPI 合成一段 30~60 s 的英文 wav（HLD §9 的做法），返回 (path, seconds)。"""
    if "speech" in _state:
        return _state["speech"]
    tmp = _tmpdir("t20qa_speech_")
    wav = tmp / "speech45.wav"
    text_file = tmp / "speech.txt"
    words = PASSAGE.split()
    for _attempt in range(4):
        text_file.write_text(" ".join(words), encoding="utf-8")
        ps = (
            "$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Speech;"
            f"$t = Get-Content -Raw -LiteralPath '{text_file}';"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$s.Rate = 0;"
            f"$s.SetOutputToWaveFile('{wav}');"
            "$s.Speak($t);"
            "$s.Dispose();"
        )
        rc, out, err, text = _run(["powershell", "-NoProfile", "-Command", ps], timeout=180)
        assert_(rc == 0 and wav.is_file(), f"SAPI 合成失败 rc={rc}：{_tail(text)}")
        seconds = _wav_seconds(wav)
        if 30.0 <= seconds <= 60.0:
            _state["speech"] = (wav, seconds)
            return wav, seconds
        if seconds <= 0:
            break
        words = words[: max(25, int(len(words) * target_s / seconds))]
    raise AssertionError(f"合成不出 30~60 s 的音频（最后 {seconds:.1f}s）")


def _stripped_env(tmp):
    """剥环境口径（HLD §9）：PATH 只留系统、独立空 TEMP、代理指向黑洞。"""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    fresh = Path(tmp) / "temp_empty"
    fresh.mkdir(parents=True, exist_ok=True)
    env = {
        "SystemRoot": root,
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "windir": root,
        "ComSpec": os.path.join(root, "system32", "cmd.exe"),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "PATH": os.path.join(root, "system32") + os.pathsep + root,
        "TEMP": str(fresh), "TMP": str(fresh),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"),
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    }
    return env


def _env_snapshot(env):
    return {k: env.get(k, "<unset>") for k in ("PATH", "TEMP", "HTTP_PROXY", "HTTPS_PROXY",
                                                "HF_HOME", "WB_ROOT", "PYTHONPATH")}


def _snapshot_deps():
    """把 `build\\portable\\slim` 的 site-packages 先快照到临时目录再交给构建。

    为什么要快照：`--deps-from` 直接指 `build\\portable\\slim` 时，如果有别人正在并行重建
    `build\\portable`（PM/别的 agent），复制会拿到半份依赖 → 包内 `import fastapi` 直接
    `ModuleNotFoundError`（2026-11-06 实测撞过一次）。快照 + 关键包校验把这一条变成
    「拿到稳定副本才开始」，而不是把并行构建的噪声算成产品缺陷。
    """
    if "deps" in _state:
        return _state["deps"]
    assert_(SLIM_SITE.is_dir(), f"没有可复用的已装依赖：{SLIM_SITE}（先按 M1 建一次 slim）")
    must = ("fastapi/__init__.py", "faster_whisper/__init__.py", "ctranslate2/__init__.py")
    tmp = _tmpdir("t20qa_deps_")
    snap = tmp / "site-packages"
    deadline = time.time() + 300
    last = ""
    while time.time() < deadline:
        ready = all((SLIM_SITE / rel).is_file() for rel in must)
        if ready:
            shutil.rmtree(snap, ignore_errors=True)
            shutil.copytree(SLIM_SITE, snap,
                            ignore=shutil.ignore_patterns("pip*", "__pycache__", "*.pyc"))
            if all((snap / rel).is_file() for rel in must):
                _state["deps"] = snap
                return snap
            last = "复制后的快照缺关键包（大概率撞上并行重建）"
        else:
            last = f"{SLIM_SITE} 里还没有 fastapi / faster_whisper / ctranslate2"
        time.sleep(3)
    raise AssertionError(f"拿不到稳定的依赖快照（{last}）——可能有并行构建正在重建 build\\portable，稍后重跑")


def _prepare_package():
    """重建 full 包 → 复制到中文+空格临时目录 → 造 45 s 音频；返回 (包路径, 音频目录, 构建秒数, 复制秒数)。"""
    if "pkgdir" in _state:
        return _state["pkgdir"], _state["audio"], _state["build_s"], _state["copy_s"]
    assert_(BUILD.is_file(), f"缺少构建脚本 {BUILD}")
    assert_(FFMPEG_BIN.is_dir(), f"ffmpeg 9.0.2 目录不存在：{FFMPEG_BIN}")
    assert_(HF_HUB.is_dir(), f"HF 缓存不存在：{HF_HUB}")
    deps = _snapshot_deps()
    t0 = time.time()
    rc, out, err, text = _run([WEBAPP_PY, BUILD, "--flavor", "full", "--out", str(PKG_ROOT),
                               "--ffmpeg-dir", str(FFMPEG_BIN), "--hf-cache", str(HF_HUB),
                               "--deps-from", str(deps)], timeout=3600)
    build_s = time.time() - t0
    assert_(rc == 0, f"full 构建失败 rc={rc}；输出尾：{_tail(text, 12)}")
    assert_(PKG.is_dir(), f"构建没产出 {PKG}")

    if PKG_COPY.exists():
        shutil.rmtree(PKG_COPY)
    PKG_COPY.parent.mkdir(parents=True, exist_ok=True)
    t1 = time.time()
    shutil.copytree(PKG, PKG_COPY)
    copy_s = time.time() - t1

    wav, seconds = _make_speech()
    audio_dir = PKG_COPY / "pipeline" / "output" / "acc1_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio = audio_dir / f"{STEM}.wav"
    shutil.copyfile(wav, audio)
    _state.update(pkgdir=PKG_COPY, audio=audio_dir, build_s=build_s, copy_s=copy_s,
                  speech_seconds=seconds)
    return PKG_COPY, audio_dir, build_s, copy_s


def _write_pkg_runner(pkg):
    """包内 runner：内容全 ASCII，路径全用 %~dp0 推（包路径含中文也不写进文件）。"""
    runner = pkg / "_qa20_run.cmd"
    runner.write_text(
        "@echo off\r\n"
        'call "%~dp0_setenv.bat"\r\n'
        '"%~dp0python\\python.exe" "%~dp0pipeline\\scripts\\batch_transcribe_fw.py" '
        '--audio-dir "%~dp0pipeline\\output\\acc1_audio" '
        "--model medium --compute-type int8 --lang en\r\n"
        "exit /b %ERRORLEVEL%\r\n",
        encoding="ascii", newline="")
    return runner


def _transcribe_pkg():
    if "pkg_run" in _state:
        return _state["pkg_run"]
    pkg, audio_dir, build_s, copy_s = _prepare_package()
    runner = _write_pkg_runner(pkg)
    _reset_transcripts(audio_dir)
    tmp = _tmpdir("t20qa_env_")
    env = _stripped_env(tmp)
    t0 = time.time()
    rc, out, err, text = _run(["cmd", "/c", runner.name], cwd=str(pkg), env=env, timeout=1200)
    seconds = time.time() - t0
    transcript = audio_dir / "transcripts" / f"{STEM}_transcript.txt"
    assert_(rc == 0, f"包内转写期望退出码 0，实得 {rc}；stderr={_tail(err, 12)}")
    assert_(transcript.is_file(), f"没有产出 transcript：{transcript}")
    body = transcript.read_text(encoding="utf-8", errors="replace")
    assert_("[00:00:" in body, f"transcript 里没有 [00:00: 时间戳行：{_tail(body, 4)}")
    assert_("backend=faster" in err, f"stderr 没有 backend=faster：{_tail(err)}")
    assert_("模型目录" in err, f"stderr 没有『模型目录』：{_tail(err)}")
    model_hint = next((ln.strip() for ln in err.splitlines() if "模型目录" in ln), "")
    assert_(str(pkg).lower() in model_hint.lower(),
            f"模型目录不在包内：{model_hint!r}（期望在 {pkg} 下）")
    hits = [t for t in NET_TOKENS if t.lower() in text.lower()]
    assert_(not hits, f"断网运行里出现联网/重试字样 {hits}：{_tail(text, 12)}")
    _no_traceback(text)
    _state["pkg_run"] = dict(rc=rc, seconds=seconds, stderr=err, stdout=out, text=text,
                             transcript=str(transcript), audio_dir=str(audio_dir),
                             env=env, build_s=build_s, copy_s=copy_s, net_hits=hits,
                             body_lines=[ln for ln in body.splitlines() if "[00:00:" in ln])
    return _state["pkg_run"]


def _run_dev_once():
    """开发机基线：同一段音频、同一个脚本，用 pipeline venv（带真模型）。"""
    wav, _seconds = _make_speech()
    tmp = _tmpdir("t20qa_dev_")
    audio_dir = tmp / "audio"
    audio_dir.mkdir()
    shutil.copyfile(wav, audio_dir / f"{STEM}.wav")
    env = _clean_env(XDG_CACHE_HOME=r"D:\devcache\xdg-cache",
                     HF_HOME=None, HF_HUB_CACHE=None, HUGGINGFACE_HUB_CACHE=None,
                     HF_HUB_OFFLINE=None, WB_MODEL_DOWNLOAD=None)
    t0 = time.time()
    rc, out, err, text = _run([PIPE_PY, SCRIPT, "--audio-dir", audio_dir, "--model", "medium",
                               "--compute-type", "int8", "--lang", "en"], cwd=PIPE, env=env,
                              timeout=1200)
    seconds = time.time() - t0
    transcript = audio_dir / "transcripts" / f"{STEM}_transcript.txt"
    assert_(rc == 0, f"开发机基线期望退出码 0，实得 {rc}；stderr={_tail(err, 12)}")
    assert_(transcript.is_file(), f"开发机基线没产出 transcript：{transcript}")
    body = transcript.read_text(encoding="utf-8", errors="replace")
    assert_("[00:00:" in body, f"开发机 transcript 没有时间戳：{_tail(body, 4)}")
    return dict(rc=rc, seconds=seconds, transcript=str(transcript),
                audio_dir=str(audio_dir), stderr=err)


def t20_11():
    """DoD 5：full 包（当前代码重建）→ 中文+空格路径 → 剥环境 + call _setenv.bat → 断网转写成功。"""
    res = _transcribe_pkg()
    env = res["env"]
    return [
        f"构建：{res['build_s']:.1f}s → {PKG}；复制到中文+空格路径：{res['copy_s']:.1f}s",
        f"剥环境：PATH={env['PATH']}｜TEMP={env['TEMP']}｜HTTP(S)_PROXY={env['HTTP_PROXY']}",
        f"音频：{res['audio_dir']}（{_state['speech_seconds']:.1f}s 英文 SAPI 语音）",
        f"exit={res['rc']}；包内转写耗时 {res['seconds']:.1f}s",
        f"transcript：{res['transcript']}（{len(res['body_lines'])} 行时间戳，首行：{res['body_lines'][0][:90]}）",
        f"stderr：{next((ln.strip() for ln in res['stderr'].splitlines() if '模型目录' in ln), '')[:150]}",
        f"stderr：{next((ln.strip() for ln in res['stderr'].splitlines() if 'backend=' in ln), '')[:150]}",
        f"联网/重试/超时字样命中：{res['net_hits'] or '0'}",
    ]


def _ratio(pkg, dev):
    return pkg["seconds"] / max(0.001, dev["seconds"])


def t20_12():
    """DoD 5 的「断网」是真的：代理黑洞 + 无超时/重试字样 + 耗时 ≤ 开发机 ×1.5（共同判，不看单条）。"""
    pkg = _transcribe_pkg()
    dev = _state.get("dev_run") or _run_dev_once()
    _state["dev_run"] = dev
    env = pkg["env"]
    assert_(env.get("HTTP_PROXY") == "http://127.0.0.1:9"
            and env.get("HTTPS_PROXY") == "http://127.0.0.1:9",
            f"代理没有指向黑洞：HTTP_PROXY={env.get('HTTP_PROXY')!r}")
    assert_(not pkg["net_hits"], f"包里出现联网/重试字样：{pkg['net_hits']}")
    ratio = _ratio(pkg, dev)
    assert_(ratio <= 1.5,
            f"包内 {pkg['seconds']:.1f}s / 开发机 {dev['seconds']:.1f}s = {ratio:.2f} > 1.5，"
            f"像是真的在等网络超时或被别的负载拖慢")
    return [f"HTTP_PROXY = {env['HTTP_PROXY']}（黑洞）",
            f"包内 {pkg['seconds']:.1f}s / 开发机 {dev['seconds']:.1f}s = {ratio:.2f}（≤ 1.5）",
            "联网/重试/超时字样命中 0 行"]


def t20_13():
    """DoD 6：同一段 45 s 音频，开发机与包内各跑一次；两次都 0、都出 transcript；包内 ≤ 开发机 ×1.5。"""
    pkg = _transcribe_pkg()
    dev = _state.get("dev_run") or _run_dev_once()
    _state["dev_run"] = dev
    ratio = _ratio(pkg, dev)
    retested = ""
    if ratio > 1.5:
        # 计划允许：先怀疑别的负载，重测一次再判（两边都重跑，口径一致）
        _state.pop("pkg_run", None)
        dev = _run_dev_once()
        _state["dev_run"] = dev
        pkg = _transcribe_pkg()
        ratio = _ratio(pkg, dev)
        retested = "（首次比值 >1.5，已重测一次）"
        assert_(ratio <= 1.5, f"重测后仍超：包内 {pkg['seconds']:.1f}s / 开发机 {dev['seconds']:.1f}s = {ratio:.2f}")
    assert_(pkg["rc"] == 0 and dev["rc"] == 0, f"两次都要 exit 0：pkg={pkg['rc']} dev={dev['rc']}")
    return [f"音频：{_state['speech_seconds']:.1f}s（同一段，SAPI 合成）",
            f"开发机（pipeline\\.venv）：{dev['seconds']:.1f}s → {dev['transcript']}",
            f"包内（python\\python.exe，经 _setenv.bat）：{pkg['seconds']:.1f}s → {pkg['transcript']}",
            f"比值 = {ratio:.2f}（≤ 1.5）{retested}"]


# ────────────────────────── T20-14：入口自检 ──────────────────────────
def t20_14():
    assert_(Path(sys.executable).resolve() == PIPE_PY.resolve(),
            f"本用例必须用 pipeline\\.venv 跑：sys.executable={sys.executable!r}")
    ids = [cid for cid, _t, _f in CASES]
    assert_(len(ids) == 14, f"应注册 14 条用例，实得 {len(ids)}：{ids}")
    assert_(ids == [f"T20-{i:02d}" for i in range(1, 15)], f"用例 id 不是 T20-01~14：{ids}")
    pkg = _state.get("pkg_run") or {}
    dev = _state.get("dev_run") or {}
    assert_(pkg and dev, "还没有 11/13 的实测秒数（先跑 T20-11/T20-13）")
    return [f"解释器：{sys.executable}",
            f"用例注册：14 条（T20-01 ~ T20-14），无重复",
            f"实测秒数：包内 {pkg['seconds']:.1f}s / 开发机 {dev['seconds']:.1f}s（比值 {_ratio(pkg, dev):.2f}）"]


CASES = [
    ("T20-01", "单测通过（test_transcribe_backend.py 退出 0、无 Traceback）", t20_01),
    ("T20-02", "DoD 1(a)：--backend openai 退役 → 退 2 + 「只带 faster-whisper」+ 无 Traceback", t20_02),
    ("T20-03", "DoD 1(b)：没模型 + 禁止下载 → 退 2 + 「模型」「HF_ENDPOINT」+ 不联网", t20_03),
    ("T20-04", "DoD 1(c)：ctranslate2 导入失败 → 退 2 + 可读中文（ctranslate2 / 这台机器）", t20_04),
    ("T20-05", "DoD 1(d)：空音频目录 → 退 0 + 末行 JSON total==0（不需要模型）", t20_05),
    ("T20-06", "DoD 1(e)：auto 与 faster 同分支、不 import torch/whisper", t20_06),
    ("T20-07", "DoD 1(a2)：CRD-0006 旧断言已改 + 5 条 faster 用例打桩 + Ran 27 tests OK", t20_07),
    ("T20-08", "DoD 2：无 setdefault 离线开关；import model_cache < ensure_model < faster_whisper", t20_08),
    ("T20-09", "DoD 3：无兜底 import whisper/torch", t20_09),
    ("T20-10", "DoD 4：docstring 写明只带 faster-whisper / CPU int8 / openai 退役 / 两份 requirements", t20_10),
    ("T20-11", "DoD 5：full 包（当前代码重建）+ 中文空格路径 + 剥环境 + call _setenv → 断网转写成功", t20_11),
    ("T20-12", "DoD 5 的断网是真的：代理黑洞 + 无超时/重试字样 + 耗时 ≤ 开发机 ×1.5", t20_12),
    ("T20-13", "DoD 6：同一段 45 s 音频，包内 ≤ 开发机 ×1.5（两次秒数）", t20_13),
    ("T20-14", "QA 入口自检：pipeline\\.venv 跑、14 条齐全、打印 11/13 秒数", t20_14),
]


def _cleanup():
    # 只删自己建的临时目录；构建产物 build\portable\qa20 保留（人工查看用）
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    if PKG_COPY.exists():
        shutil.rmtree(PKG_COPY, ignore_errors=True)
        parent = PKG_COPY.parent
        if parent.is_dir() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                pass
    print(f"        [cleanup] 已删临时目录与中文路径副本 {PKG_COPY.parent}；保留构建产物 {PKG_ROOT}（供人工查看，不删）")


def main():
    only = {a.upper() for a in sys.argv[1:] if a.upper().startswith("T20-")}
    print("T-20 转写只走 faster-whisper · QA 用例（清单：.dsh-crew/jobs/portable-package/M2-qa-plan.md T-20 段）")
    print(f"仓库：{REPO}")
    print("")
    try:
        for cid, title, fn in CASES:
            if only and cid not in only:
                continue
            check(cid, title, fn)
    finally:
        _cleanup()
    total = _ok + _fail
    if _state.get("pkg_run") and _state.get("dev_run"):
        print(f"\n实测秒数：包内 {_state['pkg_run']['seconds']:.1f}s / 开发机 "
              f"{_state['dev_run']['seconds']:.1f}s")
    print(f"\n通过 {_ok} / {total}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
