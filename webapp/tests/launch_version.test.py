# -*- coding: utf-8 -*-
"""launch_version.test.py — R6 启动器版本检测与重启指引（2026-10-24 独立验收整改）

验收报告 R6：
  · 404 toast 指引双击「启动工作台.bat」，但启动器只检查 health 指纹，发现旧工作台
    就复用，不会重启、也不看新路由 —— 按提示操作仍会 404；
  · 包内 00 文档指向「重启工作台.bat」，两处指引不一致。

本套锁死修复后的行为：
  1. /api/health 暴露 api_version + features
  2. 启动器发现旧进程（版本低/缺接口）→ 返回非 0 并打印真正的重启入口，**不打开旧页面**
  3. 进程是当前版本 → 正常复用并打开页面
  4. 前端 404 文案指向「重启工作台.bat」而不是会复用旧进程的启动器
  5. app.API_VERSION 与启动器要求不脱节（升级时忘了加版本会在这里挂）

跑法：webapp/.venv/Scripts/python.exe tests/launch_version.test.py
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _launch  # noqa: E402
import app  # noqa: E402

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


OLD_HEALTH = {"ok": True, "ts": 1.0, "diagnostics": {"pipeline_dir": "D:/p", "ledger_files": []}}
NEW_HEALTH = {**OLD_HEALTH, "api_version": app.API_VERSION,
              "features": list(app.API_FEATURES)}


def run_main(health_payload, probe_map=None):
    """跑一次启动器 main()，返回 (rc, stdout, opened_urls)。"""
    opened = []
    probes = probe_map or {p: ("workbench" if p == 8789 else "foreign") for p in _launch.PORTS}
    out = io.StringIO()
    with patch.object(_launch, "probe", lambda port: probes.get(port, "foreign")), \
         patch.object(_launch, "health", lambda port, timeout=2.5: health_payload), \
         patch.object(_launch, "open_page", lambda url: opened.append(url)), \
         redirect_stdout(out):
        rc = _launch.main()
    return rc, out.getvalue(), opened


print("launch_version — 启动器版本检测（R6）")

# ── 1. health 契约 ───────────────────────────────────────────────
health = app.health()
check("health：暴露 api_version 与 features（启动器据此判断新旧）", lambda: (
    assert_(health.get("ok") is True, str(health)),
    assert_(int(health.get("api_version") or 0) >= 2, f"api_version={health.get('api_version')}"),
    assert_(set(app.API_FEATURES) <= set(health.get("features") or []), str(health.get("features"))),
    assert_(health.get("diagnostics"), "diagnostics 不能丢（旧指纹仍要有效）"),
))
check("版本要求不脱节：app.API_VERSION ≥ 启动器 REQUIRED_API_VERSION", lambda: (
    assert_(app.API_VERSION >= _launch.REQUIRED_API_VERSION,
            f"app={app.API_VERSION} launcher={_launch.REQUIRED_API_VERSION}"),
))

# ── 2. api_status 判定 ───────────────────────────────────────────
ok_old, detail_old = _launch.api_status(OLD_HEALTH)
ok_new, detail_new = _launch.api_status(NEW_HEALTH)
ok_feat, detail_feat = _launch.api_status({**NEW_HEALTH, "features": ["repair"]})
check("api_status：旧进程（无 api_version）判不合格并说明原因", lambda: (
    assert_(ok_old is False, "旧 health 竟判合格"),
    assert_("api_version" in detail_old, detail_old),
))
check("api_status：当前版本 + 必需能力 → 合格", lambda: (
    assert_(ok_new is True, detail_new),
    assert_("api_version" in detail_new, detail_new),
))
check("api_status：版本够但缺接口 → 仍不合格（不能只看版本号）", lambda: (
    assert_(ok_feat is False, "缺 ai 接口竟判合格"),
    assert_("/api/ai/*" in detail_feat, detail_feat),
))

# ── 3. 旧进程：不打开旧页面，明确给出重启入口 ─────────────────────
rc_old, out_old, opened_old = run_main(OLD_HEALTH)
check("旧进程：main() 返回 2（非 0），且不打开会 404 的旧页面", lambda: (
    assert_(rc_old == 2, f"rc={rc_old}"),
    assert_(opened_old == [], f"不该打开旧页面：{opened_old}"),
    assert_("旧版工作台" in out_old, out_old),
    assert_("api_version=0" in out_old, out_old),
))
check("旧进程：提示里给的是「重启工作台.bat」这个真正的重启入口", lambda: (
    assert_("重启工作台.bat" in out_old, out_old),
    assert_(str(_launch.PORTS[0]) in out_old, "应带端口号，方便多实例"),
))

# ── 4. 当前版本：正常复用并打开页面 ───────────────────────────────
rc_new, out_new, opened_new = run_main(NEW_HEALTH)
check("当前版本：main() 返回 0 并打开页面（复用是安全的）", lambda: (
    assert_(rc_new == 0, f"rc={rc_new}"),
    assert_(len(opened_new) == 1 and opened_new[0] == "http://127.0.0.1:8789/", str(opened_new)),
    assert_("已在 8789 运行" in out_new, out_new),
))

# ── 5. 前端 404 文案指向真正的重启入口 ────────────────────────────
app_js = (WEBAPP / "static" / "app.js").read_text(encoding="utf-8")
check("前端 404：提示「重启工作台.bat」，不再指向会复用旧进程的启动器", lambda: (
    assert_("重启工作台.bat" in app_js, "app.js 未提到重启工作台.bat"),
    assert_("请重启工作台（双击桌面「启动工作台.bat」）" not in app_js,
            "旧文案仍在：会让人重复双击启动器"),
))

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
