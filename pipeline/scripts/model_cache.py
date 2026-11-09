# -*- coding: utf-8 -*-
"""model_cache · faster-whisper 模型定位 / 离线开关 / 下载进度与可读失败。

为什么单独一个模块（ADR-0008）：`HF_HUB_OFFLINE` 是 `huggingface_hub.constants`
在 **import 那一刻**读一次的常量。所以必须"先判断本地有没有模型、把环境变量设好，
再 import hub"。本模块顶层**只 import 标准库**；`huggingface_hub` 只在函数内部 import。

下载完整性（安全评审）：下载钉住 `MEDIUM_REVISION`（不跟随默认分支），并校验
`model.bin`/`config.json`/`tokenizer.json`/`vocabulary.txt` 四个文件的 sha256；
本地快照同样校验——哈希不符不算命中：禁止下载时抛 `ModelMissing`（消息含「校验失败」
与文件名），允许下载时按钉住的 revision 重下并再校验。

契约：`docs/design/api/model-cache-pipeline.md`
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

DEFAULT_MODEL = "medium"
HF_REPO = {"medium": "Systran/faster-whisper-medium"}
APPROX_BYTES = {"medium": 1_600_000_000}
MIN_MODEL_BYTES = 1_000_000_000
# 下载必须钉住一个 commit，绝不跟随默认分支；这个值来自本机真实快照
# D:\devcache\xdg-cache\huggingface\hub\models--Systran--faster-whisper-medium\snapshots\。
MEDIUM_REVISION = "08e178d48790749d25932bbc082711ddcfdfbc4f"
HF_REVISION = {"medium": MEDIUM_REVISION}
# 下载完整性：这四个文件必须与钉住 revision 的 sha256 一致。
# 常量是本机真实快照现算的（model.bin 1.4 GB），不是凭记忆写的。
SNAPSHOT_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
SNAPSHOT_SHA256 = {
    "model.bin": "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
    "config.json": "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}
# 与 faster_whisper.utils.download_model 的 allow_patterns 同一组（评审点名）。
ALLOW_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)
_HEARTBEAT_SECONDS = 10.0
_THREAD_NAME = "model_cache-progress"


class ModelError(RuntimeError):
    """所有模型相关异常的基类。str(exc) 就是给用户看的那句话。"""


class ModelMissing(ModelError):
    """模型不在本地、且不允许下载（或 --model-dir 无效）。"""


class ModelDownloadFailed(ModelError):
    """下载过程出错，或下载后仍找不到 model.bin。"""


class ModelOfflineButMissing(ModelError):
    """父环境强制离线，而本地又没有模型。"""


def _emit(reporter, text: str) -> None:
    """打印：默认 stderr（webapp 的 runner 会把子进程 stdout+stderr 合流读走）。"""
    if reporter is not None:
        reporter(text)
    else:
        print(text, file=sys.stderr)


def hf_hub_dir() -> Path:
    """HF 缓存根目录（纯函数，不创建目录）。

    优先级（契约 §3）：已 import `huggingface_hub` → 以 `constants.HF_HUB_CACHE` 为准；
    否则 `HF_HUB_CACHE` → `HUGGINGFACE_HUB_CACHE` → `HF_HOME\\hub` →
    `XDG_CACHE_HOME\\huggingface\\hub` → `%USERPROFILE%\\.cache\\huggingface\\hub`。
    """
    hub = sys.modules.get("huggingface_hub")
    if hub is not None:
        consts = getattr(hub, "constants", None)
        if consts is None:
            try:
                from huggingface_hub import constants as consts  # noqa: PLC0415
            except Exception:                                        # noqa: BLE001
                consts = None
        if consts is not None:
            cache = getattr(consts, "HF_HUB_CACHE", None)
            if cache:
                return Path(str(cache))

    env = os.environ
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"])
    if env.get("HUGGINGFACE_HUB_CACHE"):
        return Path(env["HUGGINGFACE_HUB_CACHE"])
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]) / "hub"
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "huggingface" / "hub"
    home = env.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".cache" / "huggingface" / "hub"


def _valid_model_bin(path: Path) -> bool:
    """目录里有没有 >= MIN_MODEL_BYTES 的 model.bin（--model-dir 的判据）。"""
    model_bin = Path(path) / "model.bin"
    try:
        return model_bin.is_file() and model_bin.stat().st_size >= MIN_MODEL_BYTES
    except OSError:
        return False


def _valid_snapshot(path: Path) -> bool:
    """快照目录的结构判据：model.bin >= 1 GB 且 config.json / tokenizer.json 都在。"""
    if not _valid_model_bin(path):
        return False
    return (Path(path) / "config.json").is_file() and (Path(path) / "tokenizer.json").is_file()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_snapshot(directory) -> tuple[bool, str]:
    """校验快照四件套的 sha256；返回 (是否通过, 不通过的原因)。

    校验失败的原因会带文件名，供调用方拼进给用户看的错误消息。
    """
    directory = Path(directory)
    for name in SNAPSHOT_FILES:
        path = directory / name
        if not path.is_file():
            return False, "缺少 %s" % name
        expected = SNAPSHOT_SHA256.get(name)
        if not expected:
            continue
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            return False, "读不到 %s（%s）" % (name, exc)
        if actual.lower() != expected.lower():
            return False, "%s 的 sha256 不符（期望 %s…，实得 %s…）" % (
                name, expected[:16], actual[:16])
    return True, ""


def _snapshot_candidates(model: str):
    """缓存里所有候选快照目录（按名字排序）；目录不存在时返回空列表。"""
    repo = HF_REPO.get(model)
    if not repo:
        return []
    snapshots = hf_hub_dir() / ("models--" + repo.replace("/", "--")) / "snapshots"
    try:
        if not snapshots.is_dir():
            return []
        return sorted(p for p in snapshots.iterdir() if p.is_dir())
    except OSError:
        return []


def _locate_snapshot(model: str):
    """第一个结构合法的快照（**不做哈希校验**；下载后用它给出具体的校验失败原因）。"""
    for candidate in _snapshot_candidates(model):
        if _valid_snapshot(candidate):
            return candidate
    return None


def find_local(model: str = DEFAULT_MODEL):
    """在缓存目录的 `snapshots\\*\\` 下找**结构合法且哈希校验通过**的快照。

    纯读：不发请求、不改环境变量、不 import hub。哈希不符的旧/坏快照不算命中
    （`ensure_model` 在禁止下载时会对它给出 ModelMissing + 具体文件名）。
    """
    for candidate in _snapshot_candidates(model):
        if not _valid_snapshot(candidate):
            continue
        ok, _detail = verify_snapshot(candidate)
        if ok:
            return candidate
    return None


def _corrupt_local(model: str):
    """结构合法但哈希不符的第一个快照：(目录, 原因) 或 None。"""
    for candidate in _snapshot_candidates(model):
        if _valid_snapshot(candidate):
            ok, detail = verify_snapshot(candidate)
            if not ok:
                return candidate, detail
    return None


def _env_offline() -> bool:
    value = os.environ.get("HF_HUB_OFFLINE")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _download_allowed(allow_download) -> bool:
    """显式参数优先；否则由 WB_MODEL_DOWNLOAD 决定（未设 = 允许）。"""
    if allow_download is not None:
        return bool(allow_download)
    value = os.environ.get("WB_MODEL_DOWNLOAD")
    if value is None:
        return True
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _approx_mb(n) -> int:
    """字节 → 近似 MB（按 100 MB 取整，用于"约 1500 MB"这种说法）。"""
    try:
        n = max(0, int(n))
    except (TypeError, ValueError):
        return 0
    return int(round((n / (1024.0 * 1024.0)) / 100.0)) * 100


def download_progress_line(have_bytes: int, total_bytes: int) -> str:
    """下载心跳的一行文本：`[模型] 已下载 320 MB / 约 1500 MB（21%）`。

    total<=0 时只报已下载；本函数是纯函数。
    """
    have = _approx_mb(have_bytes)
    try:
        total = int(total_bytes)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        pct = int(round(max(0, min(have_bytes, total)) * 100.0 / total))
        pct = max(0, min(100, pct))
        return "[模型] 已下载 %d MB / 约 %d MB（%d%%）" % (have, _approx_mb(total), pct)
    return "[模型] 已下载 %d MB" % have


def _import_hub():
    """**函数内** import huggingface_hub（必须晚于 HF_HUB_OFFLINE 的赋值）。"""
    import huggingface_hub  # noqa: PLC0415

    return huggingface_hub


def _snapshot_download(repo_id: str, allow_patterns=ALLOW_PATTERNS, revision=None):
    """下载入口（单测打桩点）：环境设好之后才 import hub；revision 必须钉住。"""
    hub = _import_hub()
    return hub.snapshot_download(
        repo_id=repo_id,
        allow_patterns=list(allow_patterns),
        revision=revision,
        local_files_only=False,
    )


def _dir_bytes(path: Path) -> int:
    """目录当前字节数（心跳用；读不到就返回已累计值）。"""
    total = 0
    try:
        for entry in Path(path).rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def ensure_model(model: str = DEFAULT_MODEL,
                 model_dir=None,
                 allow_download=None,
                 reporter=None) -> Path:
    """拿到可用的模型目录；必要时按需下载。返回快照目录 Path。

    - `model_dir` 给了 → 只校验该目录有合法 model.bin（不查缓存、不下载、不改离线）；
    - 缓存命中（三件套 + ≥1 GB + **四件套 sha256 对得上**）→ `HF_HUB_OFFLINE=1`，返回目录；
    - 本地快照结构合法但哈希不符：视为"没有模型"；禁止下载 → ModelMissing（消息含
      「校验失败」与文件名），允许下载 → 重下**钉住 revision** 的文件并在下载后再校验；
    - 父环境 `HF_HUB_OFFLINE=1` 且未命中 → ModelOfflineButMissing（不提示 HF_ENDPOINT）；
    - 允许下载 → 先赋值 `HF_HUB_OFFLINE=0`，**然后**才 import hub 并下载（revision 钉住）；
      下载后仍找不到结构完整的快照 → ModelDownloadFailed；哈希不符 → ModelDownloadFailed。
    """
    if model_dir is not None:
        path = Path(model_dir)
        if not _valid_model_bin(path):
            raise ModelMissing(
                "[模型] 模型目录无效：%s 里没有 ≥1 GB 的 model.bin。"
                "请把它指向 faster-whisper-%s 的快照目录。" % (path, model)
            )
        _emit(reporter, "[模型] 模型目录：%s" % path)
        return path

    local = find_local(model)
    if local is not None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        _emit(reporter, "[模型] 模型目录：%s" % local)
        return local

    if not _download_allowed(allow_download):
        corrupt = _corrupt_local(model)
        if corrupt is not None:
            where, detail = corrupt
            raise ModelMissing(
                "[模型] 本地快照校验失败（%s）：%s\n"
                "这个模型未随包 / 文件已损坏，且当前禁止下载（WB_MODEL_DOWNLOAD=0）。\n"
                "要修复：设 WB_MODEL_DOWNLOAD=1 后重试（按钉住的 revision 重新下载并校验），"
                "或把正确的模型放进 models\\hf\\hub。" % (where, detail)
            )
        raise ModelMissing(
            "[模型] 模型未随包（本地缓存里没有 faster-whisper %s），且当前禁止下载"
            "（WB_MODEL_DOWNLOAD=0）。\n"
            "要联网下载：设 WB_MODEL_DOWNLOAD=1 后重试；或把模型目录放进 models\\hf\\hub。" % model
        )

    if _env_offline():
        raise ModelOfflineButMissing(
            "[模型] 当前环境开着离线模式（HF_HUB_OFFLINE=1），但包内没有模型"
            "（models\\hf\\hub 下没找到 %s）。\n"
            "要么允许联网下载（设 WB_MODEL_DOWNLOAD=1 并关掉离线），"
            "要么把模型放进 models\\hf\\hub。" % model
        )

    repo = HF_REPO.get(model)
    if not repo:
        raise ModelMissing("[模型] 未登记的模型：%s（当前只支持 %s）。" % (model, ", ".join(HF_REPO)))
    revision = HF_REVISION.get(model)

    total = int(APPROX_BYTES.get(model, 0))
    approx_gb = total / float(1024 ** 3) if total else 0.0
    # 硬约束：必须用赋值，而且必须在 import hub 之前。
    os.environ["HF_HUB_OFFLINE"] = "0"
    _emit(reporter, "[模型] 正在下载 faster-whisper %s，约 %.1f GB，首次使用需要联网……"
          % (model, approx_gb))

    cache_root = hf_hub_dir()
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(_HEARTBEAT_SECONDS):
            _emit(reporter, download_progress_line(_dir_bytes(cache_root), total))

    thread = threading.Thread(target=heartbeat, name=_THREAD_NAME, daemon=True)
    thread.start()
    try:
        _snapshot_download(repo, ALLOW_PATTERNS, revision=revision)
    except ModelError:
        raise
    except Exception as exc:                                     # noqa: BLE001
        raise ModelDownloadFailed(
            "[模型] 下载失败：%s: %s\n"
            "重试办法：设置 HF_ENDPOINT=https://hf-mirror.com（国内镜像；"
            "镜像由你决定，包不替你信任任何镜像），"
            "检查代理是否可用，或检查磁盘空间。" % (type(exc).__name__, exc)
        ) from exc
    finally:
        stop.set()
        thread.join(timeout=2.0)

    snapshot = _locate_snapshot(model)
    if snapshot is None:
        raise ModelDownloadFailed(
            "[模型] 下载似乎完成，但缓存里仍然找不到 model.bin（≥1 GB）——"
            "下载不完整或文件损坏。\n"
            "重试办法：删除 models\\hf\\hub 下对应目录后重试，"
            "或设置 HF_ENDPOINT=https://hf-mirror.com（镜像由你决定，包不替你信任任何镜像）。"
        )
    ok, detail = verify_snapshot(snapshot)
    if not ok:
        raise ModelDownloadFailed(
            "[模型] 下载完成但文件校验失败：%s\n"
            "下载内容与钉住的 revision（%s）不一致，可能被镜像/代理篡改或不完整；"
            "请删除 models\\hf\\hub 下对应目录后重试，或改用你信任的下载源。"
            % (detail, revision or "")
        )
    _emit(reporter, "[模型] 模型目录：%s" % snapshot)
    return snapshot
