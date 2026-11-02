# -*- coding: utf-8 -*-
"""ai_repair.test.py — AI 修复决策层（不打网络：patch llm.chat_json / available）

覆盖：
  · 正常判定：动作/置信度/是否采纳
  · 白名单：未知名动作、动作与缺口类型不兼容、幻觉 id → 一律丢弃
  · 低置信度 → applied=False 且 needs_human=True（调用方保留规则动作）
  · manual / skip → target_kind=None（不执行）
  · AI 不可用 / 预算耗尽 / 输出非法 → ok=False + degraded（上层回退规则）
  · 失败归因：类别兜底、retry_worth 推断、每轮条数上限
  · 动作清单是单一事实源（6 条，含 kind 兼容集）
运行：webapp/.venv/Scripts/python.exe tests/ai_repair.test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import ai_repair  # noqa: E402
import llm  # noqa: E402

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


CFG = {"enabled": True, "model": "m1", "fallbacks": [], "timeout_s": 60,
       "max_calls_per_round": 12, "max_tokens_per_round": 40000,
       "temperature": 0.2, "proxy": ""}


def fake_ok(items, model="m1"):
    return {"text": "{}", "model": model, "style": "openai", "latency_ms": 800,
            "usage": {"in": 100, "out": 50}, "fallbacks": [], "attempt": 1,
            "data": {"items": items}}


def IT(i, kind, account=None, repairable=True):
    return {"id": i, "kind": kind, "kind_label": ai_repair.ACTIONS.get(kind, {}).get("label", kind),
            "account": account or f"acc{i}", "title": kind, "reason": "测试原因",
            "repairable": repairable}


def run_diag(items, payload, cfg=None):
    with patch.object(llm, "load_config", lambda: dict(cfg or CFG)), \
         patch.object(llm, "available", lambda: True), \
         patch.object(llm, "chat_json", lambda *a, **k: fake_ok(payload)):
        ai_repair.reset_budget()
        return ai_repair.diagnose(items)


print("ai_repair — 决策层")

# ── 1. 动作清单 ─────────────────────────────────────────────────
check("动作白名单：6 条且每条声明兼容的缺口类型", lambda: (
    assert_(len(ai_repair.describe_actions()) == 6, str(len(ai_repair.describe_actions()))),
    assert_(set(ai_repair.ACTION_TO_KIND) == set(ai_repair.ACTIONS), "映射与白名单不一致"),
    assert_(ai_repair.ACTION_TO_KIND["manual"] is None, "manual 不该执行"),
))

# ── 2. 正常判定 ─────────────────────────────────────────────────
items = [IT("rp01", "collect"), IT("rp02", "ledger")]
res = run_diag(items, [
    {"id": "rp01", "action": "sync_ledger", "confidence": 0.86, "reason": "抓取完整，只是没入账"},
    {"id": "rp02", "action": "sync_ledger", "confidence": 0.9, "reason": "就是台账没写"},
])
v1 = res["items"]["rp01"]
check("正常判定：collect 项被改成更轻的 sync_ledger，置信度与理由保留", lambda: (
    assert_(res["ok"] is True, str(res["meta"])),
    assert_(v1["action"] == "sync_ledger", v1["action"]),
    assert_(v1["target_kind"] == "ledger", str(v1["target_kind"])),
    assert_(v1["applied"] is True, "应采纳"),
    assert_(abs(v1["confidence"] - 0.86) < 1e-6, str(v1["confidence"])),
    assert_(res["meta"]["judged"] == 2, str(res["meta"])),
))

# ── 3. 白名单与兼容性拒绝 ───────────────────────────────────────
res = run_diag([IT("rp01", "products"), IT("rp02", "collect"), IT("rp03", "ledger")], [
    {"id": "rp01", "action": "rm_rf", "confidence": 0.99, "reason": "删库"},          # 未知名动作
    {"id": "rp02", "action": "extract_products", "confidence": 0.9, "reason": "x"},  # 兼容
    {"id": "rp99", "action": "sync_ledger", "confidence": 0.9, "reason": "幻觉 id"},  # 不存在的项
])
check("白名单：未知名动作被丢弃（模型无法让系统执行任意命令）", lambda: (
    assert_("rp01" not in res["items"], f"不该接受 rm_rf：{res['items'].get('rp01')}"),
))
check("幻觉 id：不在本次输入集合的项一律丢弃", lambda: (
    assert_("rp99" not in res["items"], "不该接受幻觉 id"),
))
res2 = run_diag([IT("rp01", "products")], [
    {"id": "rp01", "action": "sync_ledger", "confidence": 0.9, "reason": "不兼容"},  # products 项给台账动作
])
check("兼容性：动作与缺口类型不匹配 → 丢弃（宁可不用 AI 建议）", lambda: (
    assert_(res2["ok"] is False, str(res2["meta"])),
    assert_(res2["meta"]["degraded"] is True, str(res2["meta"])),
))

# ── 4. 低置信度 / manual / skip ─────────────────────────────────
res = run_diag([IT("rp01", "collect"), IT("rp02", "ledger"), IT("rp03", "transcribe")], [
    {"id": "rp01", "action": "rerun_collect", "confidence": 0.3, "reason": "不太确定"},
    {"id": "rp02", "action": "manual", "confidence": 0.9, "reason": "台账结构可能错了"},
    {"id": "rp03", "action": "skip", "confidence": 0.8, "reason": "数据本身有问题"},
])
check("低置信度：applied=False（保留规则动作）且标 needs_human", lambda: (
    assert_(res["items"]["rp01"]["applied"] is False, str(res["items"]["rp01"])),
    assert_(res["items"]["rp01"]["needs_human"] is True, "应标需人工"),
))
check("manual / skip：target_kind 为 None（执行器不会跑）", lambda: (
    assert_(res["items"]["rp02"]["target_kind"] is None, str(res["items"]["rp02"])),
    assert_(res["items"]["rp02"]["needs_human"] is True, "manual 应需人工"),
    assert_(res["items"]["rp03"]["target_kind"] is None, str(res["items"]["rp03"])),
))

# ── 5. 回退：不可用 / 预算 / 非法输出 ───────────────────────────
with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: False):
    ai_repair.reset_budget()
    res = ai_repair.diagnose([IT("rp01", "collect")])
check("AI 不可用：ok=False + degraded，上层据此回退规则结论", lambda: (
    assert_(res["ok"] is False, str(res)),
    assert_(res["meta"]["degraded"] is True, str(res["meta"])),
    assert_("没有可用" in res["meta"]["error"], res["meta"]["error"]),
))

tight = {**CFG, "max_calls_per_round": 1}
with patch.object(llm, "load_config", lambda: dict(tight)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", lambda *a, **k: fake_ok([{"id": "rp01", "action": "rerun_collect",
                                                              "confidence": 0.9, "reason": "x"}])):
    ai_repair.reset_budget()
    first = ai_repair.diagnose([IT("rp01", "collect")])
    second = ai_repair.diagnose([IT("rp01", "collect")])
check("预算耗尽：第一次成功，第二次直接 degraded（不再打网络）", lambda: (
    assert_(first["ok"] is True, str(first["meta"])),
    assert_(second["ok"] is False, "第二次应被预算拦住"),
    assert_("预算" in second["meta"]["error"], second["meta"]["error"]),
))

with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", side_effect=llm.LLMError("模型未返回合法 JSON")):
    ai_repair.reset_budget()
    res = ai_repair.diagnose([IT("rp01", "collect")])
check("非法输出：异常被吞成 degraded（不抛给调用方，修复继续）", lambda: (
    assert_(res["ok"] is False, str(res)),
    assert_("JSON" in res["meta"]["error"], res["meta"]["error"]),
))

# ── 6. 失败归因 ─────────────────────────────────────────────────
item = IT("rp01", "collect")
item["detail"] = "采集重跑失败（exit=1）：gate 未过"
with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", lambda *a, **k: {
         **fake_ok([]), "data": {"category": "gate", "cause": "s1 门禁未通过",
                                 "retry_worth": False, "suggestion": "先看 gate_report.json"}}):
    ai_repair.reset_budget()
    att = ai_repair.attribute_failure(item, "日志尾部")
check("归因：类别/原因/是否值得重试/建议四段齐全", lambda: (
    assert_(att["category"] == "gate", str(att)),
    assert_(att["retry_worth"] is False, str(att)),
    assert_("gate_report" in att["suggestion"], str(att)),
))

with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", lambda *a, **k: {
         **fake_ok([]), "data": {"category": "外星人干的", "cause": "?", "retry_worth": "yes"}}):
    ai_repair.reset_budget()
    att2 = ai_repair.attribute_failure(item, "x")
check("归因：非法类别兜底 unknown，非布尔 retry_worth 按类别推断", lambda: (
    assert_(att2["category"] == "unknown", str(att2)),
    assert_(att2["retry_worth"] is False, str(att2)),
))

with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", lambda *a, **k: {
         **fake_ok([]), "data": {"category": "network", "cause": "抖动", "retry_worth": True,
                                 "suggestion": "重试"}}):
    ai_repair.reset_budget()
    results = [ai_repair.attribute_failure(item, "x") for _ in range(6)]
check("归因上限：每轮最多 4 条，其余返回 None（省 token）", lambda: (
    assert_(sum(1 for r in results if r) == ai_repair.MAX_ATTR_PER_ROUND,
            f"实际归因 {sum(1 for r in results if r)} 条"),
))

# ── 7. R4：非法置信度不得被提升成 1.0 ───────────────────────────
def _val(conf):
    return ai_repair._validate(IT("rp01", "collect"),
                               {"action": "sync_ledger", "confidence": conf, "reason": "bad value"})


def _bad_conf_ok(v):
    out = _val(v)
    assert_(out is not None, f"动作/类型合法，不该被整体丢弃：{v!r}")
    assert_(out["applied"] is False, f"{v!r} 竟被采纳：{out}")
    assert_(out["confidence"] == 0.0, f"{v!r} 置信度变成 {out['confidence']}")
    assert_(out["needs_human"] is True, f"{v!r} 应转人工")


check("R4 置信度：NaN / ±inf / 字符串 'NaN' / 布尔值 → 0 置信度、不采纳",
      lambda: [_bad_conf_ok(v) for v in (float("nan"), float("inf"), float("-inf"),
                                         "NaN", "inf", "-Infinity", None, True)])
check("R4 置信度：JSON 非标准 NaN（json 默认接受）也拦得住", lambda: (
    _bad_conf_ok(json.loads('{"confidence": NaN}')["confidence"]),
))
check("R4 置信度：正常数值仍夹紧到 [0,1]，数值字符串仍可用", lambda: (
    assert_(abs(_val(0.86)["confidence"] - 0.86) < 1e-9, str(_val(0.86))),
    assert_(abs(_val(9.9)["confidence"] - 1.0) < 1e-9, str(_val(9.9))),
    assert_(_val(-3)["confidence"] == 0.0, str(_val(-3))),
    assert_(abs(_val("0.90")["confidence"] - 0.90) < 1e-9, str(_val("0.90"))),
))


# ── 8. R3：预算按物理请求 + 累计 usage 记账 ─────────────────────
def fake_two_requests(items):
    """模拟 chat_json 实际打了 2 次网络：首发非法 + 严格重问成功。"""
    return {"text": "{}", "model": "m1", "style": "openai", "latency_ms": 900,
            "usage": {"in": 20, "out": 20},
            "usage_total": {"in": 220, "out": 20},          # 两次响应合计 240
            "requests": 2, "fallbacks": [], "attempt": 1,
            "data": {"items": items}}


with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json", lambda *a, **k: fake_two_requests(
         [{"id": "rp01", "action": "rerun_collect", "confidence": 0.9, "reason": "x"}])):
    ai_repair.reset_budget()
    ai_repair._BUDGET.calls_limit = 5
    ai_repair._BUDGET.tokens_limit = 2000
    res = ai_repair.diagnose([IT("rp01", "collect")])
    snap = ai_repair.budget_snapshot()
check("R3 预算：JSON 重问的两次请求都要计入（2 次 / 240 token，而不是 1 次 / 40）", lambda: (
    assert_(res["ok"] is True, str(res["meta"])),
    assert_(snap["requests"] == 2, f"物理请求记为 {snap['requests']}"),
    assert_(snap["calls"] == 2, f"兼容字段 calls 也不该是逻辑次数：{snap['calls']}"),
    assert_(snap["tokens"] == 240, f"token 记为 {snap['tokens']}"),
    assert_(res["meta"]["requests"] == 2, str(res["meta"])),
    assert_(res["meta"]["tokens"] == 240, str(res["meta"])),
    assert_(snap["approx"] is True, "应明确标注为估算/回报值"),
))
check("R3 预算快照：requests 与 calls 同义且带单位说明", lambda: (
    assert_(snap["requests"] == snap["calls"], str(snap)),
    assert_(snap["unit"] == "physical_request", str(snap)),
))

with patch.object(llm, "load_config", lambda: dict(CFG)), \
     patch.object(llm, "available", lambda: True), \
     patch.object(llm, "chat_json",
                  side_effect=llm.BudgetExceeded("本轮 AI 预算已用尽（1/1 次模型请求）")):
    ai_repair.reset_budget()
    blocked = ai_repair.diagnose([IT("rp01", "collect")])
check("R3 预算：超预算被拦下 → degrade 为规则模式（不抛给调用方，也不假装 AI 生效）", lambda: (
    assert_(blocked["ok"] is False, str(blocked)),
    assert_(blocked["meta"]["degraded"] is True, str(blocked["meta"])),
    assert_("预算" in blocked["meta"]["error"], blocked["meta"]["error"]),
))

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
