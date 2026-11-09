# -*- coding: utf-8 -*-
"""app_version.test.py — T-25：页面能看到版本号（PRD v3 M3 DoD (e)）

DoD：
  1. `app.health()` 返回里有 `version`，值 = 仓库根 `VERSION` 去空白；
     把 `VERSION` 换成 `9.9.9`（打桩目录）时跟着变；`VERSION` 不存在 → `"0.0.0"` 且不抛异常。
  2. 既有字段不变：`ok` / `ts` / `api_version` / `features` / `diagnostics` 仍在；
     `launch_version.test.py`、`acceptance_api.test.py` 仍退出 0。
  3. `webapp\\static\\index.html` 有 `id="appVersion"`，且加载后请求 `/api/health` 把 `version` 填进去。

跑法（仓库根）：`webapp\\.venv\\Scripts\\python.exe webapp\\tests\\app_version.test.py`
只读仓库文件 + 起两个子进程跑既有用例；不联网、不占端口。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
WEBAPP = HERE.parent.parent          # webapp/
ROOT = WEBAPP.parent                 # 仓库根（也是包根）
sys.path.insert(0, str(WEBAPP))
import app as workbench_app  # noqa: E402
import config  # noqa: E402

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


print("app_version — T-25 页面版本号")

# ── 1. health() 的 version ──────────────────────────────────────────
def case_health_version():
    want = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert_(want, "仓库根 VERSION 是空的，先有版本号再谈显示")
    h = workbench_app.health()
    assert_("version" in h, f"health() 返回里没有 version：{sorted(h)}")
    assert_(h["version"] == want, f"version={h['version']!r}，期望 {want!r}")


def case_health_version_override():
    tmp = Path(tempfile.mkdtemp(prefix="t25_ver_"))
    fake_webapp = tmp / "webapp"
    fake_webapp.mkdir()
    (tmp / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    saved = config.WEBAPP_DIR
    try:
        config.WEBAPP_DIR = fake_webapp
        assert_(workbench_app.health()["version"] == "9.9.9",
                f"VERSION=9.9.9 时应跟着变，实得 {workbench_app.health().get('version')!r}")

        (tmp / "VERSION").unlink()               # VERSION 不存在 → 0.0.0，且不许抛
        v = workbench_app.health()["version"]
        assert_(v == "0.0.0", f"VERSION 不存在时应给 0.0.0，实得 {v!r}")
    finally:
        config.WEBAPP_DIR = saved


def case_existing_fields():
    h = workbench_app.health()
    for key in ("ok", "ts", "api_version", "features", "diagnostics"):
        assert_(key in h, f"health() 少了既有字段 {key}：{sorted(h)}")
    assert_(h["ok"] is True, "ok 应仍为 True")
    assert_(isinstance(h["ts"], float), f"ts 应是时间戳，实得 {type(h['ts'])}")
    assert_(isinstance(h["api_version"], int), f"api_version 应是整数，实得 {type(h['api_version'])}")
    assert_(isinstance(h["features"], list), "features 应是列表")
    assert_(isinstance(h["diagnostics"], dict), "diagnostics 应是字典")
    assert_(isinstance(h["version"], str), f"version 应是字符串，实得 {type(h['version'])}")


check("1a health() 的 version == 仓库根 VERSION（去空白）", case_health_version)
check("1b VERSION=9.9.9 时跟着变；VERSION 不存在时 0.0.0 且不抛异常", case_health_version_override)
check("2a 既有字段 ok/ts/api_version/features/diagnostics 不变", case_existing_fields)


# ── 1c~1e. 安全评审：VERSION 文件不可信 ─────────────────────────────
def _with_fake_version(content, fn):
    """把 config.WEBAPP_DIR 指到一个临时包的 webapp/，在包根写 VERSION=content 后调 fn。"""
    tmp = Path(tempfile.mkdtemp(prefix="t25_sec_"))
    fake_webapp = tmp / "webapp"
    fake_webapp.mkdir()
    (tmp / "VERSION").write_text(content, encoding="utf-8")
    saved = config.WEBAPP_DIR
    try:
        config.WEBAPP_DIR = fake_webapp
        return fn(tmp)
    finally:
        config.WEBAPP_DIR = saved


def _short(value, limit=120) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[:limit] + "…"


def case_reject_bad_content():
    r"""超长 / 多行 / 带路径的 VERSION 一律 0.0.0（只认 \d+\.\d+\.\d+）。"""
    cases = [
        ("超长 >64 字节", "1.2.3\n" + "x" * 100),
        ("内容含换行与路径", "1.2.3\nC:\\Users\\you\\.workbuddy\\models.json\n"),
        ("整份就是一个路径", "C:\\Users\\you\\.workbuddy\\models.json"),
        ("空文件", ""),
        ("带前后垃圾", "ver 1.2.3"),
    ]
    for label, content in cases:
        got = _with_fake_version(content, lambda _tmp: workbench_app.health()["version"])
        assert_(got == "0.0.0", f"{label} 应被拒为 0.0.0，实得 {_short(got)}")


def case_reject_symlink():
    """VERSION 是符号链接 → 0.0.0（可被指向 models.json 这类带凭据的文件）。"""
    tmp = Path(tempfile.mkdtemp(prefix="t25_link_"))
    fake_webapp = tmp / "webapp"
    fake_webapp.mkdir()
    target = tmp / "elsewhere.txt"
    target.write_text("9.9.9\n", encoding="utf-8")
    link = tmp / "VERSION"
    made = False
    try:
        link.symlink_to(target)
        made = True
    except (OSError, NotImplementedError):
        # 本机没权限建 symlink：退而求其次，放一个**内容合法**的普通文件，
        # 再让 is_symlink 声称它是链接 —— 这样「读到 9.9.9 就是错的」仍然成立。
        link.write_text("9.9.9\n", encoding="utf-8")

    saved = config.WEBAPP_DIR
    real_is_symlink = Path.is_symlink
    try:
        config.WEBAPP_DIR = fake_webapp
        if made:
            got = workbench_app.health()["version"]
            print("    （本机真建了符号链接，直接验的）")
        else:
            def fake_is_symlink(self):
                return True if self == link else real_is_symlink(self)

            Path.is_symlink = fake_is_symlink
            try:
                got = workbench_app.health()["version"]
            finally:
                Path.is_symlink = real_is_symlink
            print("    （本机建不了 symlink：放了内容为 9.9.9 的普通文件 + monkeypatch is_symlink）")
        assert_(got == "0.0.0", f"符号链接必须被拒，实得 {_short(got)}")
    finally:
        config.WEBAPP_DIR = saved
        Path.is_symlink = real_is_symlink


def case_too_long_is_not_read_whole():
    """大文件不许整读：VERSION 有 1 MB 垃圾时也必须在毫秒级返回 0.0.0。"""
    import time as _t
    blob = "1.2.3\n" + ("A" * (1024 * 1024))
    t0 = _t.perf_counter()
    got = _with_fake_version(blob, lambda _tmp: workbench_app.health()["version"])
    cost = _t.perf_counter() - t0
    assert_(got == "0.0.0", f"1MB 的 VERSION 应被拒，实得 {_short(got)}")
    assert_(cost < 0.5, f"读超长 VERSION 花了 {cost:.3f}s，像是整读了大文件")


check("1c 坏 VERSION（超长/多行/带路径/空/带垃圾）一律 0.0.0", case_reject_bad_content)
check("1d VERSION 是符号链接时 0.0.0", case_reject_symlink)
check("1e 1MB 的 VERSION 不整读（只读前 64 字节）", case_too_long_is_not_read_whole)


# ── 3. 页面：id="appVersion" + 请求 /api/health 填入 ────────────────
def case_index_html():
    html = (WEBAPP / "static" / "index.html").read_text(encoding="utf-8")
    assert_('id="appVersion"' in html, 'index.html 里没有 id="appVersion" 的元素')
    assert_("/api/health" in html, "index.html 里没有请求 /api/health 的取数代码")
    assert_("textContent" in html, "取到 version 之后要写进元素（没找到 textContent）")
    assert_("version" in html, "取数代码里没用到 version 字段")


check("3 index.html 有 id=\"appVersion\"，且请求 /api/health 填 version", case_index_html)


# ── 2b. 既有用例仍绿 ───────────────────────────────────────────────
def case_regressions():
    py = WEBAPP / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.is_file() else sys.executable
    for name in ("launch_version", "acceptance_api"):
        p = subprocess.run([exe, str(WEBAPP / "tests" / f"{name}.test.py")],
                           cwd=str(ROOT), capture_output=True, timeout=180,
                           text=True, encoding="utf-8", errors="replace")
        tail = ((p.stdout or "") + (p.stderr or ""))[-500:]
        assert_(p.returncode == 0, f"{name}.test.py 应退出 0，实得 {p.returncode}；尾部：{tail}")


check("2b launch_version.test.py / acceptance_api.test.py 仍退出 0", case_regressions)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
