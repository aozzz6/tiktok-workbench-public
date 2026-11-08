# -*- coding: utf-8 -*-
"""
batch_transcribe_fw.py — Step5 批量转写（只走 faster-whisper，CPU int8）

本包只带 faster-whisper（CPU int8）；openai 后端已不可用（退出码 2）。
模型不再由本脚本"按名字"联网拉取：一律走 `model_cache.ensure_model()`（缓存命中 /
按 WB_MODEL_DOWNLOAD 决定是否下载 / `--model-dir` 直指本地目录），并且**先把模型备好、
再去 import faster-whisper** —— `HF_HUB_OFFLINE` 是 `huggingface_hub.constants` 在
import 那一刻读一次的常量，顺序错了下载分支不会生效（ADR-0008，T-20）。

两份依赖文件的关系：
  · `pipeline\\requirements.txt`          开发机用：带 torch / openai-whisper，方便跑别的实验；
  · `pipeline\\requirements-portable.txt` 随包用：**不带 torch**，只保证本脚本需要的
    faster-whisper + ctranslate2 可用。便携包 / Release 里装的是这一份。

输入: --audio-dir
输出: transcripts/{video_id}_transcript.txt（带时间戳分段）
      + .transcribe_state.json 断点续传
      + stdout 末行 JSON 摘要（对齐 gate_transcribe）:
        {"total": n, "done": [ids], "suspect_hallucination": [{"id","text"}]}
门禁阈值: 单条全文 <30 字符 → 疑似幻觉/纯 BGM，标记不盲信

★ 历史（保留原因，避免以后又把兜底加回来）：v3（2026-10-14）曾用 GPU（ctranslate2 CUDA）
  + openai/PyTorch CPU 兜底。那条路线要 torch，而便携包按 ADR-0002 不带 PyTorch；
  本机 AVX-512 CPU 上 ctranslate2 构造还有崩溃史（0xC0000005）。所以现在只用
  faster-whisper，且**不再 import torch**：
  · `--backend auto`：ct2 看得到 CUDA 设备 **且** cuDNN 真能用（cudnn_ops/heuristic 可加载）
    → GPU float16；否则 CPU int8。探测里的任何异常都静默退回 CPU int8。
  · `--backend faster`：显式 CPU int8（不自动跳 GPU）；`--device cpu/cuda` 可再强制覆盖。
  · openai 后端已退役：`--backend openai` → 退出码 2。
"""
import argparse
import ctypes
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

# 同目录（pipeline\scripts）下的 model_cache：顶层只 import 标准库，可以放心先 import。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_cache  # noqa: E402

AUDIO_EXTS = (".mp3", ".m4a", ".wav")
HALLUCINATION_MIN_CHARS = 30
SIMPLIFIED_PROMPT = "以下是普通话的句子。"  # 偏置简体中文输出
_DLL_HANDLES = []  # Windows 句柄必须保持存活，否则目录会立即从 DLL 搜索路径移除。


def setup_cuda_dll_paths() -> list[str]:
    """把 site-packages/nvidia/*/bin 加进 PATH（ct2 在 C++ 侧 LoadLibrary，只认 PATH）。

    只在用户显式 `--device cuda` 时调用；返回已加入的目录列表（便于日志/排错）。"""
    added = []
    try:
        import site
        roots = list(site.getsitepackages())
    except Exception:                        # noqa: BLE001  某些精简解释器没有 site
        roots = []
    roots.append(str(Path(sys.prefix) / "Lib" / "site-packages"))
    for root in roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            if os.path.isdir(d):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(d))
                except Exception:            # noqa: BLE001
                    pass
                added.append(d)
    return added


def cuda_device_count() -> int:
    """ct2 可见的 CUDA 设备数（仅诊断用；`--device cuda` 是否真能跑还取决于 cuDNN）。

    保留这个函数是为了兼容既有调用方/测试的 patch 点；本脚本不再用它自动选后端。"""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:                        # noqa: BLE001
        return 0


def fmt_ts(sec: float) -> str:
    ms = int(sec * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cudnn_available() -> bool:
    """cuDNN 9 是否真的能用（**不 import torch**）。

    ctranslate2 的 Windows 轮子只自带 `cudnn64_9.dll`，缺 `cudnn_ops`/`cudnn_heuristic`
    —— 只装 ctranslate2 时 cuda 路线会在 C++ 侧直接崩（0xC0000409，Python 抓不住），
    所以必须先确认这几块 DLL 能加载，再决定走 GPU。
    """
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:                          # 非 Windows：本项目不支持，按"不可用"处理
        return False
    for name in ("cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_heuristic64_9.dll"):
        try:
            win_dll(name)
        except OSError:
            return False
    return True


def choose_runtime(backend: str, device_arg: str, compute_type: str, gpu_compute_type: str,
                   cuda_devices: int, cudnn_ok: bool) -> tuple[str, str]:
    """纯函数：决定 (device, compute_type)（便于单测，不碰模型/不碰 DLL）。

    · `--device cpu` / `--device cuda` 是用户的明确要求，直接照办；
    · `--backend auto`：ct2 看得到 CUDA 设备 **且** cuDNN 真能用 → GPU（默认 float16）；
    · `--backend faster`：用户显式选了 faster，就走 CPU int8（不自动跳 GPU）；
    · 其它（含探测异常被上层吞成 devices=0 / cudnn=False）→ CPU int8。
    """
    if device_arg == "cuda":
        return "cuda", gpu_compute_type
    if device_arg == "cpu":
        return "cpu", compute_type
    if backend == "auto" and cuda_devices > 0 and cudnn_ok:
        return "cuda", gpu_compute_type
    return "cpu", compute_type


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--model", default="medium")
    ap.add_argument("--compute-type", default="int8", help="CPU 路径量化类型")
    ap.add_argument("--gpu-compute-type", default="float16", help="--device cuda 时的精度")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--simplified", action="store_true",
                    help="加 initial_prompt 偏置简体中文")
    ap.add_argument("--backend", choices=["auto", "faster", "openai"], default="auto",
                    help="auto=faster（历史兼容）；faster=faster-whisper/ctranslate2；"
                         "openai 后端已不可用（退出码 2，本包只带 faster-whisper）")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto",
                    help="auto/cpu 都用 CPU int8（便携包没有 CUDA 运行时）；"
                         "cuda 仅开发机显式指定（要求 cuDNN 在 PATH）")
    ap.add_argument("--vad", choices=["off", "on"], default="off",
                    help="⚠ off 为默认：本机 onnxruntime 段错误，vad_filter=True 必崩")
    ap.add_argument("--model-dir", default=None,
                    help="本地 CTranslate2 模型目录（直接用，不查缓存、不下载）；"
                         "缺省时由 model_cache 决定：缓存命中 / 按 WB_MODEL_DOWNLOAD 下载。")
    a = ap.parse_args()

    if a.backend == "openai":
        print("[error] 本包只带 faster-whisper（CPU int8）；openai/PyTorch 后端已不可用"
              "（退出码 2）。\n"
              "[提示] 换模型用 --model（默认 medium），换精度用 --compute-type（默认 int8）。",
              file=sys.stderr)
        return 2

    a.lang = {"jp": "ja", "kr": "ko"}.get(a.lang, a.lang)
    # 2026-11-05：auto / 空 = whisper 自动识别语种；识别结果写 transcripts/_detected_langs.json
    if str(a.lang or "").strip().lower() in ("", "auto", "none"):
        a.lang = None

    audio_dir = Path(a.audio_dir)
    if not audio_dir.is_dir():
        ap.error(f"音频目录不存在: {audio_dir}")
    tr_dir = audio_dir / "transcripts"
    tr_dir.mkdir(parents=True, exist_ok=True)
    state_path = tr_dir / ".transcribe_state.json"
    state = {}
    if state_path.exists():  # 断点续传
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or not isinstance(state.get("done", {}), dict)
                    or not isinstance(state.get("suspect_hallucination", []), list)):
                raise ValueError("断点文件结构不符")
        except (ValueError, OSError) as e:
            print(f"[error] 无法读取断点文件，未覆盖: {e}", file=sys.stderr)
            return 1
    done = state.get("done", {})          # {video_id: {"file":..., "chars":...}}
    suspect = state.get("suspect_hallucination", [])
    detected: dict = {}

    # ★ 同一 video_id 可能存在多种容器格式（mp3/m4a/wav，历史遗留或重复下载）。
    #   必须去重到「唯一 stem」：否则 total（按文件数）永远 > done（按 stem 去重），
    #   门禁 gate_transcribe 的 `len(done) != total` 会**永久 FAIL**。
    _PREF = {".mp3": 0, ".m4a": 1, ".wav": 2}
    _uniq: dict = {}
    for p in sorted(audio_dir.iterdir(), key=lambda x: x.name):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS or p.stat().st_size <= 0:
            continue
        cur = _uniq.get(p.stem)
        if cur is None or _PREF.get(p.suffix.lower(), 9) < _PREF.get(cur.suffix.lower(), 9):
            _uniq[p.stem] = p
    files = sorted(_uniq.values(), key=lambda p: p.name)
    total = len(files)
    current_ids = {p.stem for p in files}
    done = {vid: record for vid, record in done.items()
            if vid in current_ids and (tr_dir / f"{vid}_transcript.txt").is_file()
            and (tr_dir / f"{vid}_transcript.txt").stat().st_size > 0}
    suspect = [s for s in suspect if isinstance(s, dict) and s.get("id") in current_ids]
    print(f"[info] 共 {total} 个音频（已按 video_id 去重；目录文件 {len(list(audio_dir.iterdir()))}），"
          f"已完成 {len(done)}（断点续传）", file=sys.stderr)

    if len(done) < total:
        # ── 1. 模型先就位：必须在 import faster_whisper 之前（ADR-0008） ──
        try:
            model_path = model_cache.ensure_model(model=a.model, model_dir=a.model_dir)
        except model_cache.ModelOfflineButMissing as exc:
            # 父环境钉了离线、本地又没有模型：原样输出它的话（这条里不许出现 HF_ENDPOINT）
            print(f"[error] {exc}", file=sys.stderr)
            return 2
        except model_cache.ModelError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            print("[提示] 国内直连 HuggingFace 常失败：可设 HF_ENDPOINT=https://hf-mirror.com "
                  "后重试，或把 faster-whisper 模型目录拷进包内 models\\hf\\hub。",
                  file=sys.stderr)
            return 2

        # ── 2. 设备/精度：只走 ctranslate2，不 import torch（T-20 DoD 3） ──
        #    auto：ct2 看得到 CUDA 设备 **且** cuDNN 真能用 → GPU(float16)，否则 CPU int8。
        #    探测里任何异常都吞掉、退回 CPU：不许因为"探测 GPU"把整批转写搞崩。
        cuda_devices, cudnn_ok, added = 0, False, []
        try:
            if a.device == "cuda" or (a.backend == "auto" and a.device == "auto"):
                added = setup_cuda_dll_paths()       # 把 nvidia/*/bin 加进 PATH（没有就空）
            if a.backend == "auto" and a.device == "auto":
                cuda_devices = cuda_device_count()
                cudnn_ok = cuda_devices > 0 and cudnn_available()
        except Exception:                            # noqa: BLE001  探测失败一律当"没有 GPU"
            cuda_devices, cudnn_ok = 0, False
        device, ctype = choose_runtime(a.backend, a.device, a.compute_type,
                                       a.gpu_compute_type, cuda_devices, cudnn_ok)
        print(f"[info] backend=faster device={device} compute_type={ctype} vad={a.vad} "
              f"model={a.model} cuda_devices={cuda_devices} cudnn={cudnn_ok} "
              f"nvidia_dll_dirs={len(added)}", file=sys.stderr)

        # ── 3. 加载 faster-whisper：失败给一句可读中文 + 退出 2，不留 traceback ──
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:                     # noqa: BLE001  含 ctranslate2 的导入失败
            print(f"[error] faster-whisper / ctranslate2 在这台机器上加载失败："
                  f"{type(exc).__name__}: {exc}\n"
                  "[提示] 便携包只带 faster-whisper（CPU int8）所需的 ctranslate2；"
                  "若它被杀软拦了、或文件不完整，重新解压整个包即可。", file=sys.stderr)
            return 2

        def _load(dev: str, ct: str):
            return WhisperModel(str(model_path), device=dev, compute_type=ct)

        try:
            model = _load(device, ctype)
        except Exception as exc:                     # noqa: BLE001  模型文件损坏/不兼容
            if device == "cuda" and a.device == "auto":
                # auto 自己挑的 GPU 用不了：一句话说清，退回 CPU int8（不打印 traceback）
                print(f"[warn] CUDA 不可用（{type(exc).__name__}: {exc}），已退回 CPU int8",
                      file=sys.stderr)
                device, ctype = "cpu", a.compute_type
                try:
                    model = _load(device, ctype)
                except Exception as exc2:            # noqa: BLE001
                    print(f"[error] 模型加载失败（{type(exc2).__name__}: {exc2}）。\n"
                          f"[提示] 模型目录：{model_path}；可删掉后重下，"
                          "或用 --model-dir 指一个完好的目录。", file=sys.stderr)
                    return 2
            else:
                print(f"[error] 模型加载失败（{type(exc).__name__}: {exc}）。\n"
                      f"[提示] 模型目录：{model_path}；可删掉后重下，"
                      "或用 --model-dir 指一个完好的目录。", file=sys.stderr)
                return 2

        def _trans(p):
            seg_iter, info = model.transcribe(
                str(p), language=a.lang, vad_filter=(a.vad == "on"),
                initial_prompt=SIMPLIFIED_PROMPT if a.simplified else None)
            lines, text_all = [], []
            for s in seg_iter:
                lines.append(f"[{fmt_ts(s.start)} -> {fmt_ts(s.end)}] {s.text.strip()}")
                text_all.append(s.text.strip())
            if a.lang is None and getattr(info, "language", None):
                detected[p.stem] = info.language
            return lines, "".join(text_all), float(info.duration or 0)

        for p in files:
            vid = p.stem  # 约定文件名 {account}_{video_id}.mp3
            if vid in done:
                continue
            suspect = [s for s in suspect if s.get("id") != vid]
            t0 = dt.datetime.now()
            try:
                lines, full, dur = _trans(p)
                (tr_dir / f"{vid}_transcript.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                done[vid] = {"file": p.name, "chars": len(full),
                             "seconds": round(dur, 1)}
                if len(full) < HALLUCINATION_MIN_CHARS:
                    suspect.append({"id": vid, "text": full[:40]})
            except Exception as e:                   # noqa: BLE001  单条失败不拖垮整批
                suspect.append({"id": vid, "text": f"TRANSCRIBE_ERROR: {e}"[:60]})
            cost = (dt.datetime.now() - t0).total_seconds()
            print(f"[info] {vid}: {len(done)}/{total} 耗时 {cost:.0f}s", file=sys.stderr)
            state.update({"done": done, "suspect_hallucination": suspect})
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(state_path)
    else:
        print("[info] 全部已完成，直接出摘要", file=sys.stderr)

    if detected:
        dp = tr_dir / "_detected_langs.json"
        prev = {}
        if dp.exists():
            try:
                prev = json.loads(dp.read_text(encoding="utf-8"))
            except Exception:                        # noqa: BLE001
                prev = {}
        prev.update(detected)
        dp.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[info] 自动识别语种 {len(detected)} 条 -> {dp}", file=sys.stderr)
    summary = {"total": total, "done": list(done.keys()),
               "suspect_hallucination": suspect}
    state.update({"done": done, "suspect_hallucination": suspect})
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if len(done) == total else 1


if __name__ == "__main__":
    sys.exit(main())
