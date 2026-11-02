# -*- coding: utf-8 -*-
"""ai_api.test.py — AI 相关接口契约（不联网、不起真实修复）

覆盖：
  · GET  /api/ai/models   key 一律掩码、含动作清单与来源、默认模型
  · GET  /api/ai/config   不含任何 key 字段；带 available 与预算
  · POST /api/ai/config   白名单写盘、未知键忽略、非法模型 400、数值夹紧
  · POST /api/ai/test     透传 probe 结果（不抛异常）
  · GET  /api/ai/calls    审计只回元数据
  · POST /api/repair/start  use_ai=true 但无可用模型 → 400；有模型 → 透传给 repair.start
运行：webapp/.venv/Scripts/python.exe tests/ai_api.test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import ai_repair  # noqa: E402
import app  # noqa: E402
import llm  # noqa: E402
import repair  # noqa: E402
from fastapi import HTTPException  # noqa: E402

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


TMP = Path(tempfile.mkdtemp(prefix="aiapi_"))
CFG_JSON = TMP / "ai_config.json"
CALLS = TMP / "ai_calls.jsonl"
CFG = {"enabled": True, "model": "gpt-4o-mini", "fallbacks": ["claude-x"],
       "timeout_s": 60, "max_calls_per_round": 12, "max_tokens_per_round": 40000,
       "temperature": 0.2, "proxy": ""}

ROWS = [
    {"id": "gpt-4o-mini", "name": "GPT-4o mini", "vendor": "openai", "style": "openai",
     "key_masked": "sk-te…abcd", "supports_images": False, "cooldown_s": 0,
     "last_error": "", "is_default": False, "local": False, "env": False},
]

print("ai_api — 接口契约")

# ── models ──────────────────────────────────────────────────────
with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "list_models", lambda: [dict(r) for r in ROWS]), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "MODELS_JSON", TMP / "models.json"):
    out = app.ai_models()
check("models：返回清单/默认/动作/来源，且不含 key 原文", lambda: (
    assert_(out["default"] == "gpt-4o-mini", out["default"]),
    assert_(out["available"] is True, "available"),
    assert_(len(out["models"]) == 1, str(len(out["models"]))),
    assert_(len(out["actions"]) == 6, str(len(out["actions"]))),
    assert_("models.json" in out["sources"]["models_json"], str(out["sources"])),
    assert_("apiKey" not in json.dumps(out, ensure_ascii=False), "泄露了 apiKey 字段"),
))

# ── config ──────────────────────────────────────────────────────
with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True):
    cfg = app.ai_config_get()
check("config：只回白名单字段 + available + 预算，不含 key", lambda: (
    assert_(cfg["model"] == "gpt-4o-mini", cfg.get("model")),
    assert_("available" in cfg and "budget" in cfg, str(cfg.keys())),
    assert_("apiKey" not in cfg and "api_key" not in cfg, "配置里不该有 key"),
))

with patch.object(llm, "AI_CONFIG_JSON", CFG_JSON), \
     patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "resolve", lambda n: {"id": n}), \
     patch.object(llm, "available", lambda: True):
    saved = app.ai_config_set({"timeout_s": 9999, "hack": 1, "model": "gpt-4o-mini"})
on_disk = json.loads(CFG_JSON.read_text(encoding="utf-8"))
check("config 写盘：未知键丢弃、超时夹紧到 600", lambda: (
    assert_(saved["timeout_s"] == 600, str(saved["timeout_s"])),
    assert_("hack" not in on_disk, "未知键被写盘"),
    assert_(on_disk["timeout_s"] == 600, "未落盘"),
))

with patch.object(llm, "resolve", lambda n: None):
    try:
        app.ai_config_set({"model": "不存在的模型"})
        bad = None
    except HTTPException as e:
        bad = e
check("config 写盘：模型不存在 → 400（不让配置指向不存在的模型）", lambda: (
    assert_(bad is not None and bad.status_code == 400, str(bad)),
    assert_("模型不存在" in str(bad.detail), str(bad.detail)),
))

# ── test（探活）──────────────────────────────────────────────────
# 2026-11-02（CRD 0002 / ADR 0002）：P2 起 /api/ai/test 对显式模型先查模型表，
# 本机 models.json 已无 gpt-4o-mini，会 400。这里补模型表桩，断言（透传结果）不变。
with patch.object(llm, "resolve", lambda n: {"id": n, "name": "GPT-4o mini",
                                             "vendor": "openai", "style": "openai"}), \
     patch.object(llm, "probe", lambda m: {"ok": True, "model": m or "auto",
                                           "latency_ms": 123, "json_ok": True}):
    t1 = app.ai_test({"model": "gpt-4o-mini"})
with patch.object(llm, "probe", lambda m: {"ok": False, "model": m or "", "error": "HTTP 401"}):
    t2 = app.ai_test({})
check("test：透传成功与失败结果（失败不抛 500）", lambda: (
    assert_(t1["ok"] is True and t1["latency_ms"] == 123, str(t1)),
    assert_(t2["ok"] is False and "401" in t2["error"], str(t2)),
))

# ── calls（审计）────────────────────────────────────────────────
CALLS.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
    {"ts": time.time(), "model": "gpt-4o-mini", "purpose": "repair.diagnose",
     "ok": True, "latency_ms": 900, "tokens_in": 100, "tokens_out": 50},
    {"ts": time.time(), "model": "gpt-4o-mini", "purpose": "repair.attribute",
     "ok": False, "error": "HTTP 429"},
]) + "\n", encoding="utf-8")
with patch.object(llm, "AI_CALLS_JSONL", CALLS):
    calls = app.ai_calls(limit=10)
check("calls：最新在前，只回元数据", lambda: (
    assert_(calls["total"] == 2, str(calls["total"])),
    assert_(calls["calls"][0]["purpose"] == "repair.attribute", str(calls["calls"][0])),
    assert_("sk-" not in json.dumps(calls), "审计里有 key"),
))

# ── repair/start 接入 ───────────────────────────────────────────
PLAN = {"as_of": time.time(), "busy_runs": [], "counts": {"total": 1, "repairable": 1, "skipped": 0},
        "items": [{"id": "rp01", "kind": "collect", "kind_label": "采集重跑", "account": "accA",
                   "title": "采集重跑（全链）", "reason": "失败", "repairable": True,
                   "skip_reason": "", "_ctx": {"out_dir": "D:/o", "audio_dir": "D:/o_a"}}]}

with patch.object(app, "_repair_plan", lambda: PLAN), \
     patch.object(llm, "available", lambda: False):
    try:
        app.repair_start({"use_ai": True})
        no_ai = None
    except HTTPException as e:
        no_ai = e
check("repair/start：勾了 AI 但没有模型 → 400（不静默降级）", lambda: (
    assert_(no_ai is not None and no_ai.status_code == 400, str(no_ai)),
    assert_("没有可用的 AI 模型" in str(no_ai.detail), str(no_ai.detail)),
))

seen = {}
with patch.object(app, "_repair_plan", lambda: PLAN), \
     patch.object(llm, "available", lambda: True), \
     patch.object(repair, "start",
                  lambda plan, **kw: seen.update(kw) or {"status": "running", "total": 1}):
    app.repair_start({"use_ai": True, "model": "gpt-4o-mini"})
check("repair/start：use_ai/model 原样传给会话", lambda: (
    assert_(seen.get("use_ai") is True, str(seen)),
    assert_(seen.get("model") == "gpt-4o-mini", str(seen)),
))

seen.clear()
with patch.object(app, "_repair_plan", lambda: PLAN), \
     patch.object(repair, "start", lambda plan, **kw: seen.update(kw) or {}):
    app.repair_start({})
check("repair/start：不勾 AI 时明确传 use_ai=False（规则模式）", lambda: (
    assert_(seen.get("use_ai") is False, str(seen)),
    assert_(seen.get("model") == "", str(seen)),
))

# ── scan 带 AI 可用性 ───────────────────────────────────────────
with patch.object(app, "overview", lambda **k: {"accounts": [], "pending_translation": {"total": 0}}), \
     patch.object(app.pipeline_ops, "status", lambda: {"takeover": [], "receipt": {}}), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "load_config", lambda: dict(CFG)):
    sc = app.repair_scan()
check("scan：附带 AI 可用性与默认模型（前端据此启用开关）", lambda: (
    assert_(sc["ai"]["available"] is True, str(sc.get("ai"))),
    assert_(sc["ai"]["default_model"] == "gpt-4o-mini", str(sc.get("ai"))),
))

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
