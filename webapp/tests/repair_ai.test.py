# -*- coding: utf-8 -*-
"""repair_ai.test.py — 修复会话与 AI 的协作（真跑 python -c 替身，不打网络）

覆盖：
  · AI 把 collect 改成更轻的 ledger → 执行时真的走 post_update_flow（kind 被覆盖）
  · AI 判 manual/skip → 该项不执行、原因写明「AI 判定」
  · AI 判低置信度 → 保留规则动作（不被 AI 带偏）
  · AI 不可用/降级 → 会话照常执行，ai.degraded 显式暴露
  · 执行失败 → AI 归因写进 detail 与 item.ai.attribution
  · 不勾 AI → 不调用任何 AI 接口（ai_meta.judged=0、无 verdicts）
运行：webapp/.venv/Scripts/python.exe tests/repair_ai.test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import ai_repair  # noqa: E402
import config  # noqa: E402
import repair  # noqa: E402
import runner  # noqa: E402

_ok = 0
_fail = 0
PY = config.python_exe()


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


def item(i, kind, account=None, repairable=True, skip_reason=""):
    name = account or f"acc{i}"
    return {"id": f"rp{i:02d}", "kind": kind, "kind_label": repair.KIND_LABEL.get(kind, kind),
            "account": name, "title": f"{kind} 修复", "reason": "规则判定",
            "repairable": repairable, "skip_reason": skip_reason,
            "_ctx": {"out_dir": "D:/none", "audio_dir": "D:/none"}}


def verdict(action, target, conf=0.9, applied=True, reason="AI 判断", needs_human=False):
    return {"action": action, "action_label": ai_repair.ACTIONS[action]["label"],
            "target_kind": target, "confidence": conf, "reason": reason,
            "applied": applied, "needs_human": needs_human}


def diag_ok(mapping, model="m1"):
    return {"ok": True, "items": mapping,
            "meta": {"model": model, "degraded": False, "error": "", "calls": 1, "tokens": 120}}


def run_session(items, *, use_ai=True, diag=None, attr=None, cmd_ok=True, capture=None):
    """跑完整个会话（同步），返回快照。capture 收集每次实际使用的 kind。"""

    def fake_steps(it, days, retry, timeout):
        if capture is not None:
            capture.append(it.get("_ai_kind") or it["kind"])
        code = "print('ok')" if cmd_ok else "import sys;print('boom: 需要人工');sys.exit(1)"
        return [("测试步骤", [PY, "-c", code])]

    sess = repair.RepairSession(items, 7, 1, None, use_ai=use_ai, model="")
    patches = [
        patch.object(repair, "MIN_RUNTIME_S", 0.0),
        patch.object(repair, "_steps_for", fake_steps),
        patch.object(repair, "_verify", lambda it: (True, "")),
    ]
    if use_ai:
        patches.append(patch.object(ai_repair, "reset_budget", lambda: None))
        patches.append(patch.object(ai_repair, "budget_snapshot", lambda: {"calls": 1, "tokens": 120}))
        patches.append(patch.object(ai_repair, "diagnose",
                                    lambda its, m=None: diag if diag is not None
                                    else diag_ok({})))
        patches.append(patch.object(ai_repair, "attribute_failure",
                                    lambda it, tail, m=None: attr))
    for p in patches:
        p.start()
    try:
        sess.run()
    finally:
        for p in patches:
            p.stop()
    return sess.snapshot()


print("repair_ai — 会话与 AI 协作")

# ── 1. AI 换动作：collect → ledger ───────────────────────────────
cap = []
snap = run_session([item(1, "collect")],
                   diag=diag_ok({"rp01": verdict("sync_ledger", "ledger", 0.88,
                                                 reason="数据已在磁盘，只差入账")}),
                   capture=cap)
check("AI 换动作：执行器按 ledger 走（省掉一小时全链重跑）", lambda: (
    assert_(cap == ["ledger"], f"实际执行 kind={cap}"),
    assert_(snap["counts"]["ok"] == 1, str(snap["counts"])),
    assert_(snap["items"][0]["ai"]["action"] == "sync_ledger", str(snap["items"][0].get("ai"))),
    assert_(snap["items"][0]["ai"]["reason"].startswith("数据已在磁盘"), "AI 理由未透传"),
    assert_(snap["ai"]["judged"] == 1, str(snap["ai"])),
))

# ── 2. AI 判 manual → 不执行 ────────────────────────────────────
cap = []
snap = run_session([item(1, "ledger")],
                   diag=diag_ok({"rp01": verdict("manual", None, 0.8,
                                                 reason="台账列结构可能被改过")}),
                   capture=cap)
check("AI 判转人工：该项不执行，跳过原因带 AI 结论", lambda: (
    assert_(cap == [], f"不该执行，实际 {cap}"),
    assert_(snap["counts"]["skipped"] == 1, str(snap["counts"])),
    assert_("AI 判定" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
    assert_("台账列结构" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
))

# ── 3. 低置信度 → 保留规则动作 ──────────────────────────────────
cap = []
snap = run_session([item(1, "products")],
                   diag=diag_ok({"rp01": verdict("skip", None, 0.31, applied=False,
                                                 reason="不确定", needs_human=True)}),
                   capture=cap)
check("低置信度：保留规则动作（不被 AI 带偏），但 ai.applied=false 可见", lambda: (
    assert_(cap == ["products"], f"应仍按规则执行，实际 {cap}"),
    assert_(snap["counts"]["ok"] == 1, str(snap["counts"])),
    assert_(snap["items"][0]["ai"]["applied"] is False, str(snap["items"][0].get("ai"))),
))

# ── 4. AI 降级 → 照常执行且显式暴露 ─────────────────────────────
cap = []
snap = run_session([item(1, "collect")],
                   diag={"ok": False, "items": {},
                         "meta": {"degraded": True, "error": "模型未返回合法 JSON",
                                  "calls": 0, "tokens": 0}},
                   capture=cap)
check("AI 降级：修复照常跑，ai.degraded/error 透传到前端", lambda: (
    assert_(cap == ["collect"], f"实际 {cap}"),
    assert_(snap["counts"]["ok"] == 1, str(snap["counts"])),
    assert_(snap["ai"]["degraded"] is True, str(snap["ai"])),
    assert_("JSON" in snap["ai"]["error"], str(snap["ai"])),
    assert_(snap["ai"]["truthful"] is False, "降级时 truthful 应为 false"),
))

# ── 5. 失败归因写进 detail 与 item.ai ───────────────────────────
ATTR = {"category": "gate", "cause": "s1 前置门禁未通过", "retry_worth": False,
        "suggestion": "先看 gate_report.json", "model": "m1", "latency_ms": 700}
snap = run_session([item(1, "collect")],
                   diag=diag_ok({"rp01": verdict("rerun_collect", "collect", 0.9)}),
                   attr=ATTR, cmd_ok=False)
check("失败归因：detail 追加上下文，item.ai.attribution 结构齐全", lambda: (
    assert_(snap["counts"]["failed"] == 1, str(snap["counts"])),
    assert_("AI 归因" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
    assert_("gate_report" in snap["items"][0]["detail"], snap["items"][0]["detail"]),
    assert_(snap["items"][0]["ai"]["attribution"]["category"] == "gate",
            str(snap["items"][0].get("ai"))),
    assert_(snap["ai"]["attributed"] == 1, str(snap["ai"])),
))

# ── 6. 不勾 AI：完全不调用 ──────────────────────────────────────
cap = []
calls = {"diag": 0, "attr": 0}


def _boom(*a, **k):
    calls["diag"] += 1
    raise AssertionError("不该调用 AI")


with patch.object(ai_repair, "diagnose", _boom):
    snap = run_session([item(1, "collect")], use_ai=False, capture=cap)
check("不勾 AI：零调用、纯规则模式（ai.enabled=false）", lambda: (
    assert_(calls["diag"] == 0, "不该调用 diagnose"),
    assert_(cap == ["collect"], f"实际 {cap}"),
    assert_(snap["ai"]["enabled"] is False, str(snap["ai"])),
    assert_(snap["ai"]["phase"] == "done", str(snap["ai"])),
))

# ── 7. 混合：一个换动作 + 一个转人工 + 一个失败 ─────────────────
cap = []
snap = run_session(
    [item(1, "collect"), item(2, "ledger"), item(3, "products")],
    diag=diag_ok({"rp01": verdict("sync_ledger", "ledger", 0.85),
                  "rp02": verdict("manual", None, 0.72, reason="需人工核对表头")}),
    attr=ATTR, cmd_ok=False, capture=cap)
check("混合场景：动作覆盖/转人工/失败归因同时生效，进度仍如实", lambda: (
    assert_(snap["total"] == 3 and snap["done"] == 3, f"{snap['done']}/{snap['total']}"),
    assert_(snap["counts"]["skipped"] >= 1, str(snap["counts"])),
    assert_(snap["counts"]["failed"] >= 1, str(snap["counts"])),
    assert_(snap["percent"] == 100, str(snap["percent"])),
    assert_(snap["ai"]["judged"] == 2, str(snap["ai"])),
))

repair._SESSION = None
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
