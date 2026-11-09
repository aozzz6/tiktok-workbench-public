# -*- coding: utf-8 -*-
"""T-19 `model_cache` 单测：模型在哪、要不要离线、下载进度与可读失败。

被测：`pipeline\\scripts\\model_cache.py`（顶层只 import 标准库）。
不联网：所有下载路径都用打桩；真实缓存只在存在时做只读检查。

跑法（仓库根）：`pipeline\\.venv\\Scripts\\python.exe pipeline\\tests\\test_model_cache.py`
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
MODULE = SCRIPTS / "model_cache.py"
sys.path.insert(0, str(SCRIPTS))
try:
    import model_cache as mc  # noqa: E402
except Exception as _import_exc:                             # noqa: BLE001
    print("FAIL 无法 import model_cache：%s: %s" % (type(_import_exc).__name__, _import_exc))
    sys.exit(1)

ENV_KEYS = (
    "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME",
    "USERPROFILE", "HF_HUB_OFFLINE", "WB_MODEL_DOWNLOAD",
)
REAL_XDG = r"D:\devcache\xdg-cache"
REAL_HUB = Path(REAL_XDG) / "huggingface" / "hub"
# 下载完整性校验涉及四个文件（与 model_cache.SNAPSHOT_FILES 同一组）。
HASHED_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print("  PASS " + name)
        _ok += 1
    except AssertionError as e:
        print("  FAIL " + name + " -> " + str(e))
        _fail += 1
    except Exception as e:                                     # noqa: BLE001
        print("  FAIL " + name + " -> " + e.__class__.__name__ + ": " + str(e))
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _clear_hub():
    for key in [k for k in list(sys.modules) if k == "huggingface_hub" or k.startswith("huggingface_hub.")]:
        sys.modules.pop(key, None)


@contextlib.contextmanager
def clean_env(**updates):
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        _clear_hub()
        yield
    finally:
        _clear_hub()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def make_snapshot(root, size=None, config=True, tokenizer=True, vocabulary=True):
    """造一个假快照：model.bin 用 truncate（按需硬链接，避免每次真写 1 GB）。"""
    root = Path(root)
    snap = root / "models--Systran--faster-whisper-medium" / "snapshots" / ("0" * 40)
    snap.mkdir(parents=True, exist_ok=True)
    if size is not None:
        big = root / ("_big_%d.bin" % int(size))
        if not big.exists():
            big.write_bytes(b"")
            os.truncate(big, int(size))
        target = snap / "model.bin"
        if target.exists():
            target.unlink()
        try:
            os.link(big, target)
        except OSError:
            shutil.copyfile(big, target)
    if config:
        (snap / "config.json").write_text("{}", encoding="utf-8")
    if tokenizer:
        (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    if vocabulary:
        (snap / "vocabulary.txt").write_text("fake-vocab\n", encoding="utf-8")
    return snap


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_hashes(snap):
    """把模块的期望哈希换成这份假快照的真实哈希（返回原值，调用方负责恢复）。"""
    saved = getattr(mc, "SNAPSHOT_SHA256", None)
    mc.SNAPSHOT_SHA256 = {name: sha256_file(Path(snap) / name)
                          for name in HASHED_FILES if (Path(snap) / name).is_file()}
    return saved


def restore_hashes(saved):
    if saved is None:
        if hasattr(mc, "SNAPSHOT_SHA256"):
            del mc.SNAPSHOT_SHA256
    else:
        mc.SNAPSHOT_SHA256 = saved


def collect():
    lines = []
    return lines, lambda text: lines.append(str(text))


print("T-19 model_cache")

# ── 1. hf_hub_dir 的五级优先（纯函数，不建目录） ─────────────────────
print("[1] hf_hub_dir：五级优先 + 不创建目录")


def _hub_five_levels():
    with clean_env(HF_HUB_CACHE="C:/t/hubcache", HUGGINGFACE_HUB_CACHE="C:/t/hfc",
                   HF_HOME="C:/t/hfhome", XDG_CACHE_HOME="C:/t/xdg", USERPROFILE="C:/t/user"):
        assert_(mc.hf_hub_dir() == Path("C:/t/hubcache"), "HF_HUB_CACHE 应最高优先")
        os.environ.pop("HF_HUB_CACHE")
        assert_(mc.hf_hub_dir() == Path("C:/t/hfc"), "第二级 HUGGINGFACE_HUB_CACHE")
        os.environ.pop("HUGGINGFACE_HUB_CACHE")
        assert_(mc.hf_hub_dir() == Path("C:/t/hfhome") / "hub", "第三级 HF_HOME\\hub")
        os.environ.pop("HF_HOME")
        assert_(mc.hf_hub_dir() == Path("C:/t/xdg") / "huggingface" / "hub", "第四级 XDG_CACHE_HOME")
        os.environ.pop("XDG_CACHE_HOME")
        assert_(mc.hf_hub_dir() == Path("C:/t/user") / ".cache" / "huggingface" / "hub",
                "第五级 %USERPROFILE%\\.cache\\huggingface\\hub")
        for p in ("C:/t/hubcache", "C:/t/hfc", "C:/t/hfhome", "C:/t/xdg", "C:/t/user"):
            assert_(not Path(p).exists(), "hf_hub_dir() 不许创建目录：" + p)


check("五级优先 HF_HUB_CACHE → HUGGINGFACE_HUB_CACHE → HF_HOME\\hub → XDG → %USERPROFILE%",
      _hub_five_levels)


def _hub_constants_wins():
    with clean_env(HF_HUB_CACHE="C:/t/envhub"):
        stub = types.ModuleType("huggingface_hub")
        stub.constants = types.SimpleNamespace(HF_HUB_CACHE="C:/t/consthub")
        sys.modules["huggingface_hub"] = stub
        assert_(mc.hf_hub_dir() == Path("C:/t/consthub"),
                "已 import hub 时应以 constants.HF_HUB_CACHE 为准")


check("已 import huggingface_hub 时以 constants.HF_HUB_CACHE 为准", _hub_constants_wins)


# ── 2. find_local：命中 / 未命中 ────────────────────────────────────
print("[2] find_local：model.bin ≥1 GB + config.json + tokenizer.json")


def _find_hit():
    tmp = Path(tempfile.mkdtemp(prefix="t19_hit_"))
    saved = None
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
            saved = set_hashes(snap)                 # 假快照：让它满足哈希校验
            assert_(mc.find_local("medium") == snap, "合法快照应命中")
    finally:
        restore_hashes(saved)
        shutil.rmtree(tmp, ignore_errors=True)


check("命中：model.bin == 1 GB 且三套件齐全 + 哈希对得上", _find_hit)


def _find_small():
    tmp = Path(tempfile.mkdtemp(prefix="t19_small_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            make_snapshot(tmp, size=100_000_000)
            assert_(mc.find_local("medium") is None, "model.bin < 1 GB 应算未命中")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("未命中：model.bin < 1 GB", _find_small)


def _find_missing_config():
    tmp = Path(tempfile.mkdtemp(prefix="t19_cfg_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            make_snapshot(tmp, size=mc.MIN_MODEL_BYTES, config=False)
            assert_(mc.find_local("medium") is None, "缺 config.json 应算未命中")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("未命中：缺 config.json", _find_missing_config)


def _find_missing_tokenizer():
    tmp = Path(tempfile.mkdtemp(prefix="t19_tok_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            make_snapshot(tmp, size=mc.MIN_MODEL_BYTES, tokenizer=False)
            assert_(mc.find_local("medium") is None, "缺 tokenizer.json 应算未命中（契约 §5）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("未命中：缺 tokenizer.json", _find_missing_tokenizer)


def _find_unknown_model():
    with clean_env(HF_HUB_CACHE="C:/t/unknown"):
        assert_(mc.find_local("tiny") is None, "未登记模型应返回 None")


check("未登记模型 → None（纯读、不抛）", _find_unknown_model)


# ── 3. ensure_model(model_dir=...) 只校验目录 ──────────────────────
print("[3] ensure_model(model_dir=...)：只校验目录，不查缓存/不下载/不改离线")


def _model_dir_valid():
    tmp = Path(tempfile.mkdtemp(prefix="t19_dir_"))
    snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
    orig = mc._snapshot_download
    try:
        mc._snapshot_download = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应下载"))
        with clean_env(HF_HUB_CACHE="C:/t/never"):
            got = mc.ensure_model("medium", model_dir=str(snap))
            assert_(Path(got) == snap, "model_dir 命中应原样返回：" + repr(got))
            assert_("HF_HUB_OFFLINE" not in os.environ, "model_dir 分支不许改 HF_HUB_OFFLINE")
    finally:
        mc._snapshot_download = orig
        shutil.rmtree(tmp, ignore_errors=True)


check("model_dir 合法 → 原样返回，不下载、不改 HF_HUB_OFFLINE", _model_dir_valid)


def _model_dir_invalid():
    tmp = Path(tempfile.mkdtemp(prefix="t19_dirbad_"))
    try:
        with clean_env(HF_HUB_CACHE="C:/t/never"):
            try:
                mc.ensure_model("medium", model_dir=str(tmp))
            except mc.ModelMissing:
                pass
            else:
                raise AssertionError("model_dir 无效时应抛 ModelMissing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("model_dir 无效 → ModelMissing", _model_dir_invalid)


# ── 4. 不允许下载 → ModelMissing（消息三要素） ─────────────────────
print("[4] 不允许下载 → ModelMissing")


def _deny_download():
    tmp = Path(tempfile.mkdtemp(prefix="t19_deny_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            try:
                mc.ensure_model("medium", allow_download=False)
            except mc.ModelMissing as exc:
                msg = str(exc)
                for token in ("模型", "未随包", "WB_MODEL_DOWNLOAD"):
                    assert_(token in msg, "消息缺 %r：%r" % (token, msg))
                assert_("HF_HUB_OFFLINE" not in os.environ, "拒绝下载分支不该设 HF_HUB_OFFLINE")
            else:
                raise AssertionError("应抛 ModelMissing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("allow_download=False → ModelMissing 含 模型/未随包/WB_MODEL_DOWNLOAD", _deny_download)


def _deny_by_env():
    tmp = Path(tempfile.mkdtemp(prefix="t19_denyenv_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="0"):
            try:
                mc.ensure_model("medium")
            except mc.ModelMissing:
                pass
            else:
                raise AssertionError("WB_MODEL_DOWNLOAD=0 应视为不允许下载")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("WB_MODEL_DOWNLOAD=0 缺省即不允许下载", _deny_by_env)


# ── 5. 命中缓存 → HF_HUB_OFFLINE=1 ────────────────────────────────
print("[5] 命中 → 赋值 HF_HUB_OFFLINE=1，打印模型目录")


def _hit_sets_offline():
    tmp = Path(tempfile.mkdtemp(prefix="t19_off_"))
    saved = None
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
            saved = set_hashes(snap)
            os.environ["HF_HUB_OFFLINE"] = "0"
            lines, reporter = collect()
            got = mc.ensure_model("medium", reporter=reporter)
            assert_(Path(got) == snap, "应返回快照目录")
            assert_(os.environ.get("HF_HUB_OFFLINE") == "1", "命中缓存必须赋值 1")
            assert_(any("模型目录" in s and str(snap) in s for s in lines),
                    "应打印模型目录：%r" % (lines,))
    finally:
        restore_hashes(saved)
        shutil.rmtree(tmp, ignore_errors=True)


check("命中 → HF_HUB_OFFLINE=1 且打印 [模型] 模型目录", _hit_sets_offline)


# ── 6. 父环境离线 + 无模型 → ModelOfflineButMissing ──────────────
print("[6] 父环境 HF_HUB_OFFLINE=1 + 本地无模型 → ModelOfflineButMissing")


def _offline_but_missing():
    tmp = Path(tempfile.mkdtemp(prefix="t19_om_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp), HF_HUB_OFFLINE="1"):
            try:
                mc.ensure_model("medium", allow_download=True)
            except mc.ModelOfflineButMissing as exc:
                msg = str(exc)
                for token in ("HF_HUB_OFFLINE", "离线", "models\\hf\\hub"):
                    assert_(token in msg, "消息缺 %r：%r" % (token, msg))
                assert_("HF_ENDPOINT" not in msg, "离线错误里不许出现 HF_ENDPOINT：%r" % msg)
            else:
                raise AssertionError("应抛 ModelOfflineButMissing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("父环境离线 + 无模型 → 独立异常，消息含三要素且不含 HF_ENDPOINT", _offline_but_missing)


# ── 7. 下载：首行文案 / 打桩 / allow_patterns ─────────────────────
print("[7] 下载路径：文案、打桩、allow_patterns")


def _download_ok():
    tmp = Path(tempfile.mkdtemp(prefix="t19_dl_"))
    orig = mc._snapshot_download
    calls = {}
    saved = None

    def fake_download(repo_id, allow_patterns=None, revision=None):
        calls["repo"] = repo_id
        calls["patterns"] = tuple(allow_patterns or ())
        calls["revision"] = revision
        calls["offline"] = os.environ.get("HF_HUB_OFFLINE")
        snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
        nonlocal saved
        saved = set_hashes(snap)                     # 下载结果满足哈希校验
        return str(tmp)

    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="1"):
            mc._snapshot_download = fake_download
            lines, reporter = collect()
            got = mc.ensure_model("medium", reporter=reporter)
            assert_(lines, "应有打印")
            assert_("正在下载" in lines[0], "首行应含 正在下载：%r" % (lines[0],))
            assert_("约 1.5 GB" in lines[0], "首行应含 约 1.5 GB：%r" % (lines[0],))
            assert_(Path(got).is_dir(), "下载后应返回快照目录")
            assert_(calls["repo"] == "Systran/faster-whisper-medium", "repo=%r" % (calls["repo"],))
            assert_(calls["patterns"] == tuple(mc.ALLOW_PATTERNS), "下载清单必须与 ALLOW_PATTERNS 一致")
            assert_(calls["offline"] == "0", "下载调用时 HF_HUB_OFFLINE 必须是 0（赋值、不是 setdefault）")
            assert_(calls["revision"] == mc.MEDIUM_REVISION,
                    "下载必须钉 revision=%r，实得 %r" % (mc.MEDIUM_REVISION, calls["revision"]))
    finally:
        mc._snapshot_download = orig
        restore_hashes(saved)
        shutil.rmtree(tmp, ignore_errors=True)


check("下载成功：revision 已钉、首行含 正在下载 + 约 1.5 GB、返回目录、offline=0", _download_ok)


def _download_sets_offline_before_import():
    tmp = Path(tempfile.mkdtemp(prefix="t19_order_"))
    orig_import = mc._import_hub
    seen = {}
    saved = {}

    def fake_import():
        seen["at_import"] = os.environ.get("HF_HUB_OFFLINE")
        return stub

    stub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kwargs):
        seen["at_call"] = os.environ.get("HF_HUB_OFFLINE")
        snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
        saved["hashes"] = set_hashes(snap)
        return str(tmp)

    stub.snapshot_download = snapshot_download
    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="1"):
            mc._import_hub = fake_import
            lines, reporter = collect()
            mc.ensure_model("medium", reporter=reporter)
            assert_(seen.get("at_import") == "0",
                    "import hub 之前必须已赋值 HF_HUB_OFFLINE=0，实得 %r" % (seen.get("at_import"),))
            assert_(seen.get("at_call") == "0", "snapshot_download 调用时 offline 应为 0")
    finally:
        mc._import_hub = orig_import
        restore_hashes(saved.get("hashes"))
        shutil.rmtree(tmp, ignore_errors=True)


check("HF_HUB_OFFLINE=0 发生在 import hub 之前（import 时序）",
      _download_sets_offline_before_import)


def _download_sys_modules_stub():
    """DoD 2 的字面写法：把 huggingface_hub 预置成桩放进 sys.modules，不打桩
    `_import_hub`，断言桩里看到的 offline 已经是 0。"""
    tmp = Path(tempfile.mkdtemp(prefix="t19_stub_"))
    saved = {}
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            stub = types.ModuleType("huggingface_hub")
            seen = {}

            def snapshot_download(**kwargs):
                seen["offline"] = os.environ.get("HF_HUB_OFFLINE")
                seen["revision"] = kwargs.get("revision")
                snap = make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)
                saved["hashes"] = set_hashes(snap)
                return str(tmp)

            stub.snapshot_download = snapshot_download
            stub.constants = types.SimpleNamespace(HF_HUB_CACHE=str(tmp))
            sys.modules["huggingface_hub"] = stub
            lines, reporter = collect()
            got = mc.ensure_model("medium", allow_download=True, reporter=reporter)
            assert_(seen.get("offline") == "0",
                    "sys.modules 桩里看到的 HF_HUB_OFFLINE 应为 0，实得 %r" % (seen.get("offline"),))
            assert_(seen.get("revision") == mc.MEDIUM_REVISION,
                    "sys.modules 桩也应收到 revision=%r，实得 %r"
                    % (mc.MEDIUM_REVISION, seen.get("revision")))
            assert_(Path(got).is_dir(), "应返回快照目录")
    finally:
        restore_hashes(saved.get("hashes"))
        shutil.rmtree(tmp, ignore_errors=True)


check("sys.modules 预置 huggingface_hub 桩：下载时 offline 已是 0",
      _download_sys_modules_stub)


def _download_insufficient():
    tmp = Path(tempfile.mkdtemp(prefix="t19_dlbad_"))
    orig = mc._snapshot_download
    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="1"):
            mc._snapshot_download = lambda *a, **k: str(tmp)      # 不创建 model.bin
            try:
                mc.ensure_model("medium", reporter=lambda s: None)
            except mc.ModelDownloadFailed as exc:
                msg = str(exc)
                assert_("下载" in msg and "model.bin" in msg,
                        "下载后仍缺 model.bin 的可读消息：%r" % msg)
            else:
                raise AssertionError("应抛 ModelDownloadFailed")
    finally:
        mc._snapshot_download = orig
        shutil.rmtree(tmp, ignore_errors=True)


check("打桩成功但事后无 model.bin → ModelDownloadFailed（含 model.bin）", _download_insufficient)


def _download_raises():
    tmp = Path(tempfile.mkdtemp(prefix="t19_dlfail_"))
    orig = mc._snapshot_download
    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="1"):
            def boom(*a, **k):
                raise ConnectionError("network down")
            mc._snapshot_download = boom
            try:
                mc.ensure_model("medium", reporter=lambda s: None)
            except mc.ModelDownloadFailed as exc:
                msg = str(exc)
                for token in ("HF_ENDPOINT", "hf-mirror", "ConnectionError"):
                    assert_(token in msg, "失败消息缺 %r：%r" % (token, msg))
            else:
                raise AssertionError("应抛 ModelDownloadFailed")
    finally:
        mc._snapshot_download = orig
        shutil.rmtree(tmp, ignore_errors=True)


check("下载抛异常 → ModelDownloadFailed 含 HF_ENDPOINT/hf-mirror/原异常类名", _download_raises)


# ── 7b. 下载完整性：钉 revision + 四件套 sha256 校验 ───────────────
print("[7b] 下载完整性：revision 钉住 + sha256 校验")


def _revision_pinned():
    assert_(mc.MEDIUM_REVISION == "08e178d48790749d25932bbc082711ddcfdfbc4f",
            "MEDIUM_REVISION 必须是本机真实快照的 commit：%r" % (getattr(mc, "MEDIUM_REVISION", None),))
    assert_(getattr(mc, "HF_REVISION", {}).get("medium") == mc.MEDIUM_REVISION,
            "HF_REVISION['medium'] 必须指向同一个 revision")
    assert_(set(mc.SNAPSHOT_FILES) == set(HASHED_FILES),
            "SNAPSHOT_FILES 必须是四件套：%r" % (getattr(mc, "SNAPSHOT_FILES", None),))
    for name in HASHED_FILES:
        value = (getattr(mc, "SNAPSHOT_SHA256", {}) or {}).get(name)
        assert_(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
                "SNAPSHOT_SHA256[%r] 应是 64 位十六进制：%r" % (name, value))


check("MEDIUM_REVISION/HF_REVISION/SNAPSHOT_SHA256 常量齐全且格式正确", _revision_pinned)


def _download_hash_mismatch():
    tmp = Path(tempfile.mkdtemp(prefix="t19_hash_"))
    orig = mc._snapshot_download

    def fake_download(repo_id, allow_patterns=None, revision=None):
        make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)   # 结构合法，但内容与真常量不符
        return str(tmp)

    try:
        with clean_env(HF_HUB_CACHE=str(tmp), WB_MODEL_DOWNLOAD="1"):
            mc._snapshot_download = fake_download
            try:
                mc.ensure_model("medium", reporter=lambda s: None)
            except mc.ModelDownloadFailed as exc:
                msg = str(exc)
                assert_("校验失败" in msg, "哈希不符必须说 校验失败：%r" % msg)
                assert_("model.bin" in msg, "消息里要点名不符的文件：%r" % msg)
            else:
                raise AssertionError("下载后哈希不符应抛 ModelDownloadFailed")
    finally:
        mc._snapshot_download = orig
        shutil.rmtree(tmp, ignore_errors=True)


check("下载后哈希不符 → ModelDownloadFailed 含「校验失败」与文件名", _download_hash_mismatch)


def _local_hash_mismatch():
    tmp = Path(tempfile.mkdtemp(prefix="t19_localhash_"))
    try:
        with clean_env(HF_HUB_CACHE=str(tmp)):
            make_snapshot(tmp, size=mc.MIN_MODEL_BYTES)   # 没 set_hashes → 与真常量不符
            assert_(mc.find_local("medium") is None, "哈希不符的本地快照不能算命中")
            try:
                mc.ensure_model("medium", allow_download=False)
            except mc.ModelMissing as exc:
                msg = str(exc)
                for token in ("校验失败", "模型", "未随包", "WB_MODEL_DOWNLOAD"):
                    assert_(token in msg, "本地哈希不符的 ModelMissing 缺 %r：%r" % (token, msg))
            else:
                raise AssertionError("本地快照哈希不符且禁止下载 → ModelMissing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


check("本地快照哈希不符 → find_local 不命中；禁止下载时 ModelMissing（含校验失败）",
      _local_hash_mismatch)


def _allow_patterns_match():
    import faster_whisper.utils as fu
    src = inspect.getsource(fu.download_model)
    block = src[src.index("allow_patterns = ["):]
    block = block[: block.index("]") + 1]
    theirs = re.findall(r'"([^"]+)"', block)
    assert_(len(theirs) == 5, "faster_whisper 里应是 5 个模式：%r" % (theirs,))
    assert_(list(mc.ALLOW_PATTERNS) == theirs,
            "ALLOW_PATTERNS 必须与 faster_whisper 同一组：%r vs %r" % (list(mc.ALLOW_PATTERNS), theirs))


check("ALLOW_PATTERNS 与 faster_whisper.utils.download_model 的 5 个模式一致", _allow_patterns_match)


# ── 8. 进度文案 ────────────────────────────────────────────────────
print("[8] download_progress_line")


def _progress_lines():
    line0 = mc.download_progress_line(0, 1_600_000_000)
    assert_("/ 约 1500 MB" in line0, "总数应显示 约 1500 MB：%r" % line0)
    assert_("0%" in line0, "应有 0%%：%r" % line0)
    line50 = mc.download_progress_line(800_000_000, 1_600_000_000)
    assert_("50%" in line50, "应有 50%%：%r" % line50)
    line_none = mc.download_progress_line(320_000_000, 0)
    assert_("已下载" in line_none, "total=0 也要有 已下载：%r" % line_none)
    assert_("约" not in line_none and "%" not in line_none,
            "total=0 时不应显示总数/百分比：%r" % line_none)


check("(0,total) 含 / 约 1500 MB 与 0%；(half) 含 50%；total=0 只含 已下载", _progress_lines)


# ── 9. 顶层只 import 标准库（ast） ────────────────────────────────
print("[9] 顶层 import 只能是标准库")


def _ast_top_level_stdlib():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    forbidden = {"huggingface_hub", "faster_whisper", "torch", "whisper"}
    hit = roots & forbidden
    assert_(not hit, "顶层 import 了重量级库：%r（必须放函数内）" % (hit,))
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    nonstd = roots - stdlib
    assert_(not nonstd, "顶层 import 了非标准库：%r" % (nonstd,))


check("顶层 import 只有标准库（huggingface_hub/faster_whisper/torch 在函数内）",
      _ast_top_level_stdlib)


# ── 10. 真实缓存只读检查（本机有才跑） ────────────────────────────
print("[10] 真实缓存（只读）")


def _real_cache_readonly():
    if not REAL_HUB.is_dir():
        print("  SKIP 本机没有真实缓存：%s" % REAL_HUB)
        return
    with clean_env(XDG_CACHE_HOME=REAL_XDG):
        assert_(mc.hf_hub_dir() == REAL_HUB,
                "XDG_CACHE_HOME 下应得到 %r，实得 %r" % (REAL_HUB, mc.hf_hub_dir()))
        got = mc.find_local("medium")
        assert_(got is not None and Path(got).is_dir(),
                "真实快照应命中，实得 %r" % (got,))


check("XDG_CACHE_HOME 指向真实缓存时 hf_hub_dir/find_local 正确（只读）", _real_cache_readonly)


print("\n通过 %d / %d" % (_ok, _ok + _fail))
sys.exit(0 if _fail == 0 else 1)
