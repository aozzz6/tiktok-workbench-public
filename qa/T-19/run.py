# -*- coding: utf-8 -*-
r"""qa/T-19/run.py —— T-19「model_cache」独立验收用例（18 条实现用例 + 1 条入口 = 19）。

用例清单：`.dsh-crew/jobs/portable-package/M2-qa-plan.md` 的 T-19 段（1~17 条），
外加安全加固后的 2 条：C-17 哈希不符不算命中、C-18 下载必须钉住 MEDIUM_REVISION。
判据以契约 `docs/design/api/model-cache-pipeline.md` 为准（比 DoD 更严处：`find_local` 命中要
`model.bin ≥ 1 GB` + `config.json` + `tokenizer.json` + `vocabulary.txt` 四个文件的 sha256 全对）。
被测：`pipeline\scripts\model_cache.py`；不联网——所有下载路径都用打桩（`_import_hub` / `_snapshot_download`）。
不写真实缓存：所有合成缓存都在临时目录；第 16 条只读真实缓存并对比 mtime。

跑法（仓库根）：`pipeline\.venv\Scripts\python.exe qa\T-19\run.py`
全过退出 0；任一条失败退出 1；不设 SKIP。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            _stream.reconfigure(errors="replace")
    except Exception:                                              # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "pipeline" / "scripts"
MODULE = SCRIPTS / "model_cache.py"
UNIT = REPO / "pipeline" / "tests" / "test_model_cache.py"
PY = sys.executable

MODEL_XDG = Path(r"D:\devcache\xdg-cache")
MODEL_HUB = MODEL_XDG / "huggingface" / "hub"
MODEL_REPO_DIR = MODEL_HUB / "models--Systran--faster-whisper-medium"
SNAPSHOTS = MODEL_REPO_DIR / "snapshots"

sys.path.insert(0, str(SCRIPTS))
try:
    import model_cache as mc
except Exception as exc:                                           # noqa: BLE001
    print("FAIL 无法 import model_cache：%s: %s" % (type(exc).__name__, exc))
    sys.exit(1)

_UNSET = object()
TMP = Path(tempfile.mkdtemp(prefix="qa_t19_"))
RESULTS = []          # (cid, name, ok, reason)


# ────────────────────────────── 小工具 ──────────────────────────────
def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _newdir(name: str) -> Path:
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def env_patch(**changes):
    old = {}
    for key, value in changes.items():
        old[key] = os.environ.get(key, _UNSET)
        if value is _UNSET:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is _UNSET:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def patch_attr(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _child(code: str, extra_env: dict | None = None, timeout: int = 180):
    """起一个干净的子进程（清掉 HF_* / XDG_CACHE_HOME，避免宿主环境串味）。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("HF_") and k not in ("HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    return subprocess.run([PY, "-c", code], cwd=str(REPO), capture_output=True,
                          env=env, timeout=timeout, text=True,
                          encoding="utf-8", errors="replace")


def _child_lines(code: str, extra_env: dict | None = None, timeout: int = 180) -> list:
    proc = _child(code, extra_env, timeout)
    assert_(proc.returncode == 0,
            "子进程退出码 %s；stderr 尾部：%s" % (proc.returncode, (proc.stderr or "")[-500:]))
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def _sparse(path: Path, size: int) -> None:
    """造一个逻辑大小 = size 的稀疏文件（NTFS sparse；失败则退化为 truncate）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    try:
        import ctypes
        from ctypes import wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wt.HANDLE
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                    wt.DWORD, wt.DWORD, wt.HANDLE]
        handle = k32.CreateFileW(str(path), 0x40000000, 0, None, 4, 0x80, None)
        if handle in (wt.HANDLE(-1).value, -1):
            raise OSError(ctypes.get_last_error())
        try:
            returned = wt.DWORD(0)
            k32.DeviceIoControl(handle, 0x000900C4, None, 0, None, 0,
                                ctypes.byref(returned), None)       # FSCTL_SET_SPARSE
            k32.SetFilePointerEx(handle, wt.LARGE_INTEGER(size), None, 0)
            k32.SetEndOfFile(handle)
        finally:
            k32.CloseHandle(handle)
    except Exception:                                              # noqa: BLE001
        with open(path, "wb") as fh:
            fh.truncate(size)
    assert_(path.stat().st_size == size,
            "稀疏文件大小不对：%s 期望 %d 实得 %d" % (path, size, path.stat().st_size))


def _snapshot(root: Path, name: str = "s1") -> Path:
    snap = root / "models--Systran--faster-whisper-medium" / "snapshots" / name
    snap.mkdir(parents=True, exist_ok=True)
    return snap


# 合法样本的四件套用固定字节：所有样本内容相同，哈希只算一次。
_CONFIG_BYTES = b'{"model": "medium", "qa": "t19"}\n'
_TOKENIZER_BYTES = b'{"tokenizer": "qa-t19"}\n'
_VOCAB_BYTES = b"<|qa|>\n<|end|>\n"
_GOOD_HASHES = None


def _write_meta(snap: Path) -> None:
    (snap / "config.json").write_bytes(_CONFIG_BYTES)
    (snap / "tokenizer.json").write_bytes(_TOKENIZER_BYTES)
    (snap / "vocabulary.txt").write_bytes(_VOCAB_BYTES)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _good_hashes(snap: Path) -> dict:
    """样本四件套的真实 sha256（合法样本内容相同，只算一次；1 GB 只读一次）。"""
    global _GOOD_HASHES
    if _GOOD_HASHES is None:
        _GOOD_HASHES = {name: _sha256_file(snap / name) for name in mc.SNAPSHOT_FILES}
    return dict(_GOOD_HASHES)


def _write_valid(snap: Path) -> Path:
    """结构 + 哈希都合法的样本（结构三件套 + vocabulary.txt，model.bin 1 GB 稀疏）。"""
    snap.mkdir(parents=True, exist_ok=True)
    _sparse(snap / "model.bin", 1_000_000_000)
    _write_meta(snap)
    return snap


def _fake_hub(snap: Path, record: list | None = None):
    """返回一个 `_import_hub` 替身：记录 import 时刻的 HF_HUB_OFFLINE，并提供 snapshot_download。"""
    class Hub:
        @staticmethod
        def snapshot_download(repo_id, allow_patterns=None, revision=None, local_files_only=False):
            return str(snap)

    def _import():
        if record is not None:
            record.append(os.environ.get("HF_HUB_OFFLINE"))
        return Hub

    return _import


def _fake_hub_raises(exc: Exception):
    class Hub:
        @staticmethod
        def snapshot_download(**kwargs):
            raise exc

    def _import():
        return Hub

    return _import


# ────────────────────────────── 用例 ──────────────────────────────
def c01(ev):
    """1. pipeline 单测 test_model_cache.py 退出 0，无 Traceback。"""
    assert_(UNIT.is_file(), "单测文件不存在：%s" % UNIT)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([PY, str(UNIT)], cwd=str(REPO), capture_output=True,
                          env=env, timeout=300, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert_(proc.returncode == 0, "单测退出码 %s；输出尾部：%s" % (proc.returncode, out[-600:]))
    assert_("Traceback" not in out, "单测输出里有 Traceback")
    ev.extend([ln for ln in out.splitlines() if ln.strip()][-3:])


def c02(ev):
    """2. hf_hub_dir() 五级优先，逐级独立；多级时高优先级胜。"""
    root = _newdir("priority")
    code = ("import sys; sys.path.insert(0, %r); import model_cache; "
            "print(model_cache.hf_hub_dir())" % str(SCRIPTS))
    levels = [
        ("HF_HUB_CACHE", root / "l1", root / "l1"),
        ("HUGGINGFACE_HUB_CACHE", root / "l2", root / "l2"),
        ("HF_HOME", root / "l3", root / "l3" / "hub"),
        ("XDG_CACHE_HOME", root / "l4", root / "l4" / "huggingface" / "hub"),
    ]
    for key, value, expected in levels:
        lines = _child_lines(code, {key: str(value)})
        got = lines[-1].strip()
        assert_(_norm(got) == _norm(expected),
                "只设 %s 时返回 %r，期望 %r" % (key, got, str(expected)))
        ev.append("%s=<tmp> -> %s" % (key, got))
    home = root / "l5home"
    lines = _child_lines(code, {"USERPROFILE": str(home)})
    expected = home / ".cache" / "huggingface" / "hub"
    assert_(_norm(lines[-1].strip()) == _norm(expected),
            "五级都缺时返回 %r，期望 %r" % (lines[-1].strip(), str(expected)))
    ev.append("缺省 -> %s" % lines[-1].strip())
    lines = _child_lines(code, {"HF_HUB_CACHE": str(root / "l1"), "HF_HOME": str(root / "l3")})
    assert_(_norm(lines[-1].strip()) == _norm(root / "l1"), "HF_HUB_CACHE 没有压过 HF_HOME")
    ev.append("HF_HUB_CACHE + HF_HOME -> HF_HUB_CACHE")
    lines = _child_lines(code, {"HUGGINGFACE_HUB_CACHE": str(root / "l2"),
                                "XDG_CACHE_HOME": str(root / "l4")})
    assert_(_norm(lines[-1].strip()) == _norm(root / "l2"),
            "HUGGINGFACE_HUB_CACHE 没有压过 XDG_CACHE_HOME")
    ev.append("HUGGINGFACE_HUB_CACHE + XDG -> HUGGINGFACE_HUB_CACHE")
    # DoD 1 还要求：huggingface_hub 的 constants 已经 import 时，以它为准（之后再改环境变量也不影响）。
    first = root / "const_first"
    later = root / "const_later"
    code2 = (
        "import os, sys\n"
        "os.environ['HF_HUB_CACHE'] = %r\n"
        "from huggingface_hub import constants\n"
        "const = constants.HF_HUB_CACHE\n"
        "os.environ['HF_HUB_CACHE'] = %r\n"
        "sys.path.insert(0, %r)\n"
        "import model_cache\n"
        "print(const)\n"
        "print(model_cache.hf_hub_dir())\n"
        % (str(first), str(later), str(SCRIPTS))
    )
    lines = _child_lines(code2, timeout=240)
    const_line, got_line = lines[-2].strip(), lines[-1].strip()
    assert_(_norm(const_line) == _norm(first),
            "constants.HF_HUB_CACHE=%r，期望 %r" % (const_line, str(first)))
    assert_(_norm(got_line) == _norm(const_line),
            "已 import hub 后 hf_hub_dir()=%r，没有以 constants（%r）为准" % (got_line, const_line))
    ev.append("已 import hub -> 以 constants 为准（%s，环境改成 %s 也不动）" % (got_line, later))


def c03(ev):
    """3. hf_hub_dir() 不创建目录。"""
    missing = _newdir("nodir") / "missing_parent"
    code = ("import sys, os; sys.path.insert(0, %r); import model_cache; "
            "p = model_cache.hf_hub_dir(); print(str(p)); "
            "print(os.path.exists(str(p))); print(os.path.exists(os.path.dirname(str(p))))"
            % str(SCRIPTS))
    lines = _child_lines(code, {"HF_HOME": str(missing)})
    got, exists, parent_exists = lines[-3].strip(), lines[-2].strip(), lines[-1].strip()
    assert_(_norm(got) == _norm(missing / "hub"), "返回 %r，期望 %r" % (got, str(missing / "hub")))
    assert_(exists == "False", "调用后目录被创建了：%r" % got)
    assert_(parent_exists == "False", "调用后父目录被创建了：%r" % str(missing))
    assert_(not missing.exists(), "调用后磁盘上真出现了 %s" % missing)
    ev.append("调用前后 Test-Path(%s) = False / False" % got)


def c04(ev):
    """4. find_local() 结构三件套 + 四件套 sha256：缺 config / 缺 model.bin / 太小 / 缺 tokenizer 都 None；合法样本命中。

    安全加固后命中要求 4 个文件的 sha256 与 `SNAPSHOT_SHA256` 一致；样本是自己造的，
    所以在**同一进程**里把常量 patch 成样本的真实哈希（子进程里 patch 传不进去）。
    """
    assert_("huggingface_hub" not in sys.modules,
            "父进程里 huggingface_hub 已被 import，hf_hub_dir() 会以 constants 为准，本用例无法用临时 HF_HUB_CACHE")
    base = _newdir("findlocal")
    d1 = _snapshot(base / "r1")
    _sparse(d1 / "model.bin", 2_000_000_000)                       # 只有 model.bin
    d2 = _snapshot(base / "r2")
    _write_meta(d2)                                                # 只有三个小文件
    d3 = _snapshot(base / "r3")
    _sparse(d3 / "model.bin", 100_000_000)
    _write_meta(d3)                                                # 太小
    d4 = _write_valid(_snapshot(base / "r4"))                      # 结构 + 哈希都合法
    d5 = _snapshot(base / "r5")
    _sparse(d5 / "model.bin", 1_000_000_000)
    (d5 / "config.json").write_bytes(_CONFIG_BYTES)                # 缺 tokenizer.json
    (d5 / "vocabulary.txt").write_bytes(_VOCAB_BYTES)
    roots = [base / n for n in ("r1", "r2", "r3", "r4", "r5")]
    expect = [None, None, None, str(d4), None]
    got = []
    with patch_attr(mc, "SNAPSHOT_SHA256", _good_hashes(d4)):
        for root in roots:
            with env_patch(HF_HUB_CACHE=str(root)):
                hit = mc.find_local("medium")
            got.append(str(hit) if hit else None)
    assert_([None if x is None else _norm(x) for x in got] ==
            [None if x is None else _norm(x) for x in expect],
            "find_local 结果 %r，期望 %r（①缺 config ②缺 model.bin ③100MB ④合法 ⑤缺 tokenizer）"
            % (got, expect))
    ev.append("①缺config=%(a)s ②缺model=%(b)s ③100MB=%(c)s ④合法=%(d)s ⑤缺tokenizer=%(e)s" % {
        "a": got[0], "b": got[1], "c": got[2], "d": got[3], "e": got[4]})
    ev.append("命中判据含四件套 sha256（SNAPSHOT_SHA256 已 patch 成样本真实哈希）")
    for d in (d1, d2, d3, d4, d5):
        ev.append("%s: %s" % (d.parent.parent.parent.name,
                              ", ".join(sorted(p.name for p in d.iterdir()))))


def c05(ev):
    """5. ensure_model(model_dir=...) 只校验目录：返回它，不 import hub、不改离线变量。"""
    snap = _newdir("modeldir") / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    _sparse(snap / "model.bin", 1_000_000_000)
    called = []

    def boom():
        called.append(1)
        raise AssertionError("model_dir 分支不许 import hub")

    with env_patch(HF_HUB_OFFLINE=_UNSET), patch_attr(mc, "_import_hub", boom):
        got = mc.ensure_model(model_dir=str(snap), reporter=lambda s: None)
        assert_(got == snap, "返回 %r，期望 %r" % (got, snap))
        assert_(not called, "model_dir 分支 import 了 hub")
        assert_("HF_HUB_OFFLINE" not in os.environ,
                "model_dir 分支改了 HF_HUB_OFFLINE：%r" % os.environ.get("HF_HUB_OFFLINE"))
    ev.append("返回 %s；hub 桩调用 0 次；HF_HUB_OFFLINE 未被设置" % got)


def c06(ev):
    """6. allow_download=False（及 WB_MODEL_DOWNLOAD=0 的缺省路径）且本地无模型 → ModelMissing（三关键词）。"""
    with env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET), \
            patch_attr(mc, "find_local", lambda model="medium": None):
        try:
            mc.ensure_model(allow_download=False, reporter=lambda s: None)
            raise AssertionError("应抛 ModelMissing，实际没抛")
        except mc.ModelMissing as exc:
            msg = str(exc)
    for token in ("模型", "未随包", "WB_MODEL_DOWNLOAD"):
        assert_(token in msg, "ModelMissing 消息缺 %r：%s" % (token, msg))
    ev.append("allow_download=False：%s" % msg.replace("\n", " ")[:150])
    # 契约 §5：allow_download 缺省由 WB_MODEL_DOWNLOAD 决定，"0" = 禁止。
    with env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD="0"), \
            patch_attr(mc, "find_local", lambda model="medium": None):
        try:
            mc.ensure_model(reporter=lambda s: None)
            raise AssertionError("WB_MODEL_DOWNLOAD=0 时应抛 ModelMissing，实际没抛")
        except mc.ModelMissing as exc:
            msg2 = str(exc)
    for token in ("模型", "未随包", "WB_MODEL_DOWNLOAD"):
        assert_(token in msg2, "WB_MODEL_DOWNLOAD=0 的 ModelMissing 缺 %r：%s" % (token, msg2))
    ev.append("WB_MODEL_DOWNLOAD=0（不传 allow_download）：同样 ModelMissing")


def c07(ev):
    """7. 命中缓存后 HF_HUB_OFFLINE 被**赋值**为 "1"（父环境留着 "0" 也要被改掉）。"""
    snap = _newdir("hit") / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    # 父环境先留一个 "0"：setdefault 会认为键已存在而不动，命中分支就不会改成 1。
    with env_patch(HF_HUB_OFFLINE="0"), patch_attr(mc, "find_local", lambda model="medium": snap):
        got = mc.ensure_model(reporter=lambda s: None)
        value = os.environ.get("HF_HUB_OFFLINE")
    assert_(got == snap, "返回 %r，期望 %r" % (got, snap))
    assert_(value == "1", "命中后 HF_HUB_OFFLINE=%r，期望 '1'（父环境 '0' 必须被覆盖）" % value)
    ev.append("命中 -> 返回 %s，HF_HUB_OFFLINE 由 '0' 被赋值为 %r" % (got, value))


def c08(ev):
    """8. 允许下载时 "0" 的赋值发生在 import hub 之前（父环境留空串，setdefault 会留下空串 → 红）。"""
    cache = _newdir("dl8cache")
    # 下载后的"产物"必须真在缓存根下（实现下载后会用 _locate_snapshot 扫缓存，不是在打桩里拿返回值）。
    snap = _write_valid(_snapshot(cache, "dl8"))
    counter = {"n": 0}

    def find(model="medium"):
        counter["n"] += 1
        return None if counter["n"] == 1 else snap

    record = []
    # 父环境先留一个空串（不是 "1"，不会触发离线分支）：必须被**赋值**成 "0"；
    # 若实现用 setdefault，空串会留下来，这条就红。
    with patch_attr(mc, "SNAPSHOT_SHA256", _good_hashes(snap)), \
            env_patch(HF_HUB_OFFLINE="", WB_MODEL_DOWNLOAD=_UNSET, HF_HUB_CACHE=cache), \
            patch_attr(mc, "find_local", find), patch_attr(mc, "_import_hub", _fake_hub(snap, record)):
        got = mc.ensure_model(allow_download=True, reporter=lambda s: None)
        value = os.environ.get("HF_HUB_OFFLINE")
    assert_(record == ["0"], "import hub 时刻 HF_HUB_OFFLINE=%r，期望 ['0']（赋值必须在 import 之前）" % record)
    assert_(value == "0", "下载路径结束后 HF_HUB_OFFLINE=%r，期望 '0'（父环境空串必须被覆盖）" % value)
    assert_(got == snap, "返回 %r，期望 %r" % (got, snap))
    ev.append("import hub 时 HF_HUB_OFFLINE=%r；返回 %s" % (record[0], got))


def c09(ev):
    """9. 父环境 HF_HUB_OFFLINE=1 且本地无模型 → ModelOfflineButMissing（含三关键词、不含 HF_ENDPOINT）。"""
    with env_patch(HF_HUB_OFFLINE="1", WB_MODEL_DOWNLOAD=_UNSET), \
            patch_attr(mc, "find_local", lambda model="medium": None):
        try:
            mc.ensure_model(reporter=lambda s: None)
            raise AssertionError("应抛 ModelOfflineButMissing，实际没抛")
        except mc.ModelOfflineButMissing as exc:
            msg = str(exc)
    for token in ("HF_HUB_OFFLINE", "离线", "models\\hf\\hub"):
        assert_(token in msg, "ModelOfflineButMissing 消息缺 %r：%s" % (token, msg))
    assert_("HF_ENDPOINT" not in msg, "离线错误里不该出现 HF_ENDPOINT：%s" % msg)
    ev.append("ModelOfflineButMissing：%s" % msg.replace("\n", " ")[:160])


def c10(ev):
    """10. 下载桩成功但事后仍无 model.bin → ModelDownloadFailed（含 model.bin）。"""
    empty = _newdir("dl10")
    cache = _newdir("dl10cache")
    with env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET, HF_HUB_CACHE=cache), \
            patch_attr(mc, "find_local", lambda model="medium": None), \
            patch_attr(mc, "_import_hub", _fake_hub(empty)):
        try:
            mc.ensure_model(allow_download=True, reporter=lambda s: None)
            raise AssertionError("应抛 ModelDownloadFailed，实际没抛")
        except mc.ModelDownloadFailed as exc:
            msg = str(exc)
    assert_("model.bin" in msg, "ModelDownloadFailed 消息缺 model.bin：%s" % msg)
    assert_("下载" in msg, "ModelDownloadFailed 消息缺 下载：%s" % msg)
    ev.append("ModelDownloadFailed：%s" % msg.replace("\n", " ")[:160])


def c11(ev):
    """11. 下载路径打印序列首行含 正在下载 / 约 1.5 GB，并返回打桩目录。"""
    cache = _newdir("dl11cache")
    snap = _write_valid(_snapshot(cache, "dl11"))
    counter = {"n": 0}

    def find(model="medium"):
        counter["n"] += 1
        return None if counter["n"] == 1 else snap

    lines = []
    with patch_attr(mc, "SNAPSHOT_SHA256", _good_hashes(snap)), \
            env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET, HF_HUB_CACHE=cache), \
            patch_attr(mc, "find_local", find), patch_attr(mc, "_import_hub", _fake_hub(snap)):
        got = mc.ensure_model(allow_download=True, reporter=lines.append)
    assert_(lines, "下载路径没有任何打印")
    assert_("正在下载" in lines[0] and "约 1.5 GB" in lines[0],
            "打印首行 %r 缺 正在下载 / 约 1.5 GB" % lines[0])
    assert_(got == snap, "返回 %r，期望 %r" % (got, snap))
    ev.extend(lines[:3])


def c12(ev):
    """12. 下载打桩抛 RuntimeError → ModelDownloadFailed，消息含 HF_ENDPOINT/hf-mirror/RuntimeError。"""
    cache = _newdir("dl12cache")
    with env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET, HF_HUB_CACHE=cache), \
            patch_attr(mc, "find_local", lambda model="medium": None), \
            patch_attr(mc, "_import_hub", _fake_hub_raises(RuntimeError("模拟断网"))):
        try:
            mc.ensure_model(allow_download=True, reporter=lambda s: None)
            raise AssertionError("应抛 ModelDownloadFailed，实际没抛")
        except mc.ModelDownloadFailed as exc:
            msg = str(exc)
    for token in ("HF_ENDPOINT", "hf-mirror", "RuntimeError"):
        assert_(token in msg, "下载失败消息缺 %r：%s" % (token, msg))
    ev.append("ModelDownloadFailed：%s" % msg.replace("\n", " ")[:160])


def c13(ev):
    """13. ALLOW_PATTERNS 与 faster_whisper.utils.download_model 的 allow_patterns 同一组（5 个）。"""
    code = (
        "import ast, inspect, json\n"
        "import faster_whisper.utils as u\n"
        "tree = ast.parse(inspect.getsource(u.download_model))\n"
        "pats = None\n"
        "for node in ast.walk(tree):\n"
        "    if isinstance(node, ast.Assign):\n"
        "        for t in node.targets:\n"
        "            if isinstance(t, ast.Name) and t.id == 'allow_patterns' "
        "and isinstance(node.value, (ast.List, ast.Tuple)):\n"
        "                pats = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]\n"
        "print(json.dumps(pats))\n"
    )
    lines = _child_lines(code, timeout=240)
    pats = json.loads(lines[-1])
    assert_(isinstance(pats, list) and len(pats) == 5,
            "faster_whisper 的 allow_patterns 不是 5 项：%r" % (pats,))
    assert_(sorted(pats) == sorted(list(mc.ALLOW_PATTERNS)),
            "两组 pattern 不一致：model_cache=%r faster_whisper=%r"
            % (list(mc.ALLOW_PATTERNS), pats))
    ev.append("model_cache  : %r" % (list(mc.ALLOW_PATTERNS),))
    ev.append("faster_whisper: %r" % (pats,))


def c14(ev):
    """14. download_progress_line 三个输入。"""
    s1 = mc.download_progress_line(0, 1_600_000_000)
    s2 = mc.download_progress_line(800_000_000, 1_600_000_000)
    s3 = mc.download_progress_line(320_000_000, 0)
    assert_("/ 约 1500 MB" in s1 and "0%" in s1, "(0, 1.6GB) 输出不对：%r" % s1)
    assert_("50%" in s2, "(800MB, 1.6GB) 输出缺 50%%：%r" % s2)
    assert_("已下载" in s3, "total=0 输出缺 已下载：%r" % s3)
    assert_("/" not in s3, "total=0 输出不该有 '/'：%r" % s3)
    ev.extend([s1, s2, s3])


def c15(ev):
    """15. 模块顶层只 import 标准库（huggingface_hub/faster_whisper/torch/whisper 都不在顶层）。"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    top = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.append(node.module.split(".")[0])
    forbidden = {"huggingface_hub", "faster_whisper", "torch", "whisper"}
    hit = sorted(set(top) & forbidden)
    assert_(not hit, "顶层 import 了非标准库：%r" % hit)
    non_std = sorted(set(top) - set(sys.stdlib_module_names) - {"__future__"})
    assert_(not non_std, "顶层 import 有非标准库模块：%r（顶层 import=%r）" % (non_std, top))
    ev.append("顶层 import = %r；禁用名命中 = %r" % (top, hit))


def _mtimes(paths):
    out = {}
    for path in paths:
        try:
            out[str(path)] = os.stat(path).st_mtime_ns
        except OSError:
            out[str(path)] = None
    return out


def c16(ev):
    """16. 真实缓存只读验证（清 HF_HOME/HF_HUB_CACHE/HUGGINGFACE_HUB_CACHE，只留 XDG_CACHE_HOME）。"""
    assert_(MODEL_HUB.is_dir(), "本机真实缓存不存在：%s" % MODEL_HUB)
    assert_(MODEL_REPO_DIR.is_dir(), "真实缓存里没有 models--Systran--faster-whisper-medium")
    watched = [MODEL_HUB, MODEL_REPO_DIR, SNAPSHOTS]
    before = _mtimes(watched)
    code = ("import sys; sys.path.insert(0, %r); import model_cache; "
            "print(model_cache.hf_hub_dir()); p = model_cache.find_local('medium'); "
            "print(str(p) if p else 'None')" % str(SCRIPTS))
    lines = _child_lines(code, {"XDG_CACHE_HOME": str(MODEL_XDG)})
    hub_line, snap_line = lines[-2].strip(), lines[-1].strip()
    assert_(_norm(hub_line) == _norm(MODEL_HUB),
            "hf_hub_dir()=%r，期望 %r（前三个 HF_* 已清、XDG 已设）" % (hub_line, str(MODEL_HUB)))
    assert_(snap_line != "None", "find_local('medium') 返回 None（真实缓存没被认出来）")
    snap = Path(snap_line)
    assert_(snap.is_dir(), "find_local 返回的不是目录：%r" % snap_line)
    assert_(snap.name == mc.MEDIUM_REVISION,
            "真实快照目录名 %r 与钉住的 MEDIUM_REVISION=%r 不一致" % (snap.name, mc.MEDIUM_REVISION))
    model_bin = snap / "model.bin"
    assert_(model_bin.is_file() and model_bin.stat().st_size >= mc.MIN_MODEL_BYTES,
            "真实快照的 model.bin 不存在或 < 1 GB：%r" % (str(model_bin),))
    after = _mtimes(watched)
    assert_(before == after, "只读检查改了缓存目录 mtime：before=%r after=%r" % (before, after))
    ev.append("hf_hub_dir() = %s" % hub_line)
    ev.append("find_local('medium') = %s（model.bin %d 字节）" % (snap_line, model_bin.stat().st_size))
    ev.append("缓存目录 mtime 前后一致：%d 处" % len(watched))


def c17(ev):
    """17（安全加固新增）：哈希不符 → 不命中；禁止下载时 ModelMissing 且消息含「校验失败」。

    样本是自己造的，所以在同一进程里把 SNAPSHOT_SHA256 patch 成**合法样本**的真实哈希；
    再把另一个样本的 tokenizer.json 改一个字节（model.bin 相同），它必须判为未命中。
    """
    assert_("huggingface_hub" not in sys.modules,
            "父进程里 huggingface_hub 已被 import，hf_hub_dir() 会以 constants 为准，本用例无法用临时 HF_HUB_CACHE")
    good_root = _newdir("hash_good")
    good = _write_valid(_snapshot(good_root, "good"))
    bad_root = _newdir("hash_bad")
    bad = _write_valid(_snapshot(bad_root, "bad"))
    (bad / "tokenizer.json").write_bytes(b'{"tokenizer": "QA-CORRUPT"}\n')
    hashes = _good_hashes(good)
    with patch_attr(mc, "SNAPSHOT_SHA256", hashes):
        with env_patch(HF_HUB_CACHE=str(good_root)):
            hit = mc.find_local("medium")
        with env_patch(HF_HUB_CACHE=str(bad_root), HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET):
            miss = mc.find_local("medium")
            try:
                mc.ensure_model(allow_download=False, reporter=lambda s: None)
                raise AssertionError("哈希不符且禁止下载时应抛 ModelMissing，实际没抛")
            except mc.ModelMissing as exc:
                msg = str(exc)
    assert_(hit == good, "合法样本应命中 %r，实得 %r（patch 的哈希不对？）" % (good, hit))
    assert_(miss is None, "被改过一字节的样本仍被判命中：%r" % (miss,))
    for token in ("校验失败", "tokenizer.json"):
        assert_(token in msg, "哈希不符的 ModelMissing 消息缺 %r：%s" % (token, msg))
    ev.append("合法样本命中：%s" % hit)
    ev.append("tokenizer.json 改 1 字节后 find_local=None")
    ev.append("ModelMissing：%s" % msg.replace("\n", " ")[:170])


def c18(ev):
    """18（安全加固新增）：_snapshot_download 收到的 revision 必须等于 MEDIUM_REVISION。"""
    cache = _newdir("revision_cache")
    snap = _write_valid(_snapshot(cache, "revision"))
    counter = {"n": 0}

    def find(model="medium"):
        counter["n"] += 1
        return None if counter["n"] == 1 else snap

    called = {}

    def fake_download(repo_id, allow_patterns=(), revision=None):
        called.update({"repo_id": repo_id, "allow_patterns": tuple(allow_patterns),
                       "revision": revision})
        return str(snap)

    with patch_attr(mc, "SNAPSHOT_SHA256", _good_hashes(snap)), \
            env_patch(HF_HUB_OFFLINE=_UNSET, WB_MODEL_DOWNLOAD=_UNSET, HF_HUB_CACHE=cache), \
            patch_attr(mc, "find_local", find), patch_attr(mc, "_snapshot_download", fake_download):
        got = mc.ensure_model(allow_download=True, reporter=lambda s: None)
    assert_(got == snap, "返回 %r，期望 %r" % (got, snap))
    assert_(called.get("revision") == mc.MEDIUM_REVISION,
            "下载收到的 revision=%r，期望钉住的 MEDIUM_REVISION=%r"
            % (called.get("revision"), mc.MEDIUM_REVISION))
    assert_(called.get("repo_id") == mc.HF_REPO["medium"],
            "下载收到的 repo_id=%r，期望 %r" % (called.get("repo_id"), mc.HF_REPO["medium"]))
    assert_(called.get("allow_patterns") == tuple(mc.ALLOW_PATTERNS),
            "下载收到的 allow_patterns=%r，期望 %r"
            % (called.get("allow_patterns"), tuple(mc.ALLOW_PATTERNS)))
    ev.append("revision=%r（MEDIUM_REVISION=%r）" % (called.get("revision"), mc.MEDIUM_REVISION))
    ev.append("repo_id=%r；allow_patterns=%d 项" % (called.get("repo_id"),
                                                   len(called.get("allow_patterns") or ())))


CASES = [
    ("C-01", "pipeline 单测 test_model_cache.py 通过", c01),
    ("C-02", "hf_hub_dir() 五级优先、逐级独立、多级取高", c02),
    ("C-03", "hf_hub_dir() 不创建目录", c03),
    ("C-04", "find_local() 结构三件套 + 四件套哈希（缺config/缺model/缺tokenizer/100MB/合法）", c04),
    ("C-05", "ensure_model(model_dir=...) 只校验目录、不 import hub", c05),
    ("C-06", "allow_download=False / WB_MODEL_DOWNLOAD=0 → ModelMissing（三关键词）", c06),
    ("C-07", "命中缓存后 HF_HUB_OFFLINE=1", c07),
    ("C-08", "允许下载时 0 的赋值在 import hub 之前", c08),
    ("C-09", "父环境离线且无模型 → ModelOfflineButMissing（不含 HF_ENDPOINT）", c09),
    ("C-10", "下载桩成功但仍无 model.bin → ModelDownloadFailed", c10),
    ("C-11", "下载打印首行含 正在下载 / 约 1.5 GB", c11),
    ("C-12", "下载抛异常 → ModelDownloadFailed（HF_ENDPOINT/hf-mirror/RuntimeError）", c12),
    ("C-13", "ALLOW_PATTERNS 与 faster_whisper 同一组（5 个）", c13),
    ("C-14", "download_progress_line 三个输入", c14),
    ("C-15", "模块顶层只 import 标准库", c15),
    ("C-16", "真实缓存只读验证（XDG=真实缓存，命中且 revision 一致，mtime 不变）", c16),
    ("C-17", "哈希不符 → 不命中；禁止下载时 ModelMissing 含「校验失败」", c17),
    ("C-18", "_snapshot_download 收到的 revision == MEDIUM_REVISION", c18),
]


def _print_ev(ev):
    for line in ev:
        for sub in str(line).splitlines():
            if sub.strip():
                print("      " + sub.rstrip())


def main() -> int:
    print("qa/T-19/run.py —— T-19 model_cache 独立验收（用例清单：M2-qa-plan.md T-19 1~17 + 安全加固 2 条 = 19）")
    print("被测：%s" % MODULE)
    print("解释器：%s" % PY)
    print("不联网：所有下载路径打桩；合成缓存全部在 %s" % TMP)
    ok = fail = 0
    try:
        for cid, name, fn in CASES:
            ev = []
            try:
                fn(ev)
            except AssertionError as exc:
                print("FAIL %s %s -> %s" % (cid, name, exc))
                _print_ev(ev)
                fail += 1
            except Exception as exc:                                   # noqa: BLE001
                print("FAIL %s %s -> %s: %s" % (cid, name, type(exc).__name__, exc))
                _print_ev(ev)
                fail += 1
            else:
                print("ok %s %s" % (cid, name))
                _print_ev(ev)
                ok += 1
        if fail == 0:
            print("ok C-19 QA 入口（18 条实现用例全部通过，退出 0）")
            ok += 1
        else:
            print("FAIL C-19 QA 入口 -> %d 条用例未通过" % fail)
            fail += 1
        total = ok + fail
        print("")
        print("通过 %d / %d" % (ok, total))
        print("T-19 RESULT: %d/%d ok, exit %d" % (ok, total, 0 if fail == 0 else 1))
        return 0 if fail == 0 else 1
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
