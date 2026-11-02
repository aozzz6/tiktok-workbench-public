# -*- coding: utf-8 -*-
"""repair_plan.test.py — 一键修复的「计划推导」纯函数（不启动任何子进程）

覆盖：
  · 待接管 daily 失败 → 采集重跑（可修）
  · 待接管 ledger 失败 → 只补台账（不重跑全链）
  · 链路缺口映射：采集/转写/商品/入账
  · 「无商品（未挂小黄车）」不算问题，不进计划
  · 同账号已有采集重跑 → 其它项标跳过并写明原因（不重复执行）
  · 运行中的账号 → 跳过（避免并发写台账）
  · 待译 → 跳过项；回执存疑 → 可修项（重跑上一轮账号重建回执，partial 合并）
  · 执行顺序：采集在台账之前
  · public() 不泄露内部 _ctx
运行：webapp/.venv/Scripts/python.exe tests/repair_plan.test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import repair  # noqa: E402

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


def acc(name, gaps=None, **kw):
    base = {
        "account": name, "media_type": "video", "running": False,
        "scrape_items": 10, "audio_count": 10, "transcript_count": 10,
        "products_count": 10, "ledger_covered": 10,
        "out_dir": f"D:/out/{name}", "audio_dir": f"D:/out/{name}_audio",
        "gaps": gaps or [],
    }
    base.update(kw)
    return base


def kinds(plan):
    return [(it["kind"], it["account"]) for it in plan["items"]]


def by_kind(plan, kind, account=None):
    for it in plan["items"]:
        if it["kind"] == kind and (account is None or it["account"] == account):
            return it
    return None


print("repair_plan — 计划推导")

# ── 1. 空输入 ─────────────────────────────────────────────────────
check("无账号无队列：空计划、0 可修", lambda: (
    assert_(repair.build_plan([], {}, 0)["counts"]["total"] == 0, "应为空计划")
))

# ── 2. 待接管 daily → 采集重跑 ────────────────────────────────────
plan = repair.build_plan([], {"takeover": [
    {"account": "accA", "reason": "日更失败（exit=1）", "kind": "daily"}]}, 0)
check("daily 失败 → collect 可修，原因原文透传", lambda: (
    assert_(kinds(plan) == [("collect", "accA")], f"实际 {kinds(plan)}"),
    assert_(plan["items"][0]["repairable"] is True, "应可修"),
    assert_("exit=1" in plan["items"][0]["reason"], "原因丢了"),
))

# ── 3. 待接管 ledger → 只补台账 ───────────────────────────────────
plan = repair.build_plan([], {"takeover": [
    {"account": "accB", "reason": "台账同步失败（exit=1）", "kind": "ledger"}]}, 0)
check("ledger 失败 → ledger 项（不重跑全链）", lambda: (
    assert_(kinds(plan) == [("ledger", "accB")], f"实际 {kinds(plan)}")
))

# ── 4. 链路缺口映射 ───────────────────────────────────────────────
gaps = [
    {"level": "warn", "label": "采集", "msg": "抓取 10 + 失败 2 与音频 9 相差 3 条"},
    {"level": "warn", "label": "转写", "msg": "转写缺 2 条"},
    {"level": "warn", "label": "商品", "msg": "商品 3/10（部分视频未挂车）"},
    {"level": "warn", "label": "入账", "msg": "抓取 10 条中有 3 条未入账"},
]
plan = repair.build_plan([acc("accC", gaps)], {}, 0)
check("采集缺口 → collect；其余同账号项标跳过（全链已覆盖）", lambda: (
    assert_(by_kind(plan, "collect", "accC")["repairable"] is True, "采集应可修"),
    assert_(by_kind(plan, "transcribe", "accC")["repairable"] is False, "转写应被覆盖跳过"),
    assert_("采集重跑" in by_kind(plan, "products", "accC")["skip_reason"], "跳过原因应说明覆盖"),
))

# ── 5. 单独缺口（无采集项）→ 各自可修 ─────────────────────────────
plan = repair.build_plan([acc("accD", [
    {"level": "warn", "label": "转写", "msg": "转写缺 2 条"},
    {"level": "warn", "label": "入账", "msg": "缺 3 条"},
    {"level": "warn", "label": "商品", "msg": "商品 3/10"},
])], {}, 0)
check("转写/商品/入账缺口各自成为可修项", lambda: (
    assert_(by_kind(plan, "transcribe", "accD")["repairable"] is True, "转写"),
    assert_(by_kind(plan, "products", "accD")["repairable"] is True, "商品"),
    assert_(by_kind(plan, "ledger", "accD")["repairable"] is True, "入账"),
    assert_(plan["counts"]["repairable"] == 3, f"可修数 {plan['counts']['repairable']}"),
))

# ── 6. 商品 info 的两种语义 ───────────────────────────────────────
plan = repair.build_plan([acc("accE", [
    {"level": "info", "label": "商品", "msg": "商品未提取（可手动补跑 extract_products）"}])], {}, 0)
check("「商品未提取」→ 可修的产品提取项", lambda: (
    assert_(by_kind(plan, "products", "accE")["repairable"] is True, "应可修")
))
plan = repair.build_plan([acc("accF", [
    {"level": "info", "label": "商品", "msg": "无商品（账号未挂小黄车，纯内容/bio带货）"}])], {}, 0)
check("「无商品（未挂车）」是正常形态 → 不进计划", lambda: (
    assert_(plan["counts"]["total"] == 0, f"不应有项，实得 {kinds(plan)}")
))

# ── 7. 运行中账号 → 跳过 ──────────────────────────────────────────
plan = repair.build_plan([acc("accG", [
    {"level": "warn", "label": "入账", "msg": "缺 3 条"}], running=True)], {}, 0)
check("运行中账号 → 跳过并说明原因", lambda: (
    assert_(plan["items"][0]["repairable"] is False, "应跳过"),
    assert_("正在运行" in plan["items"][0]["skip_reason"], plan["items"][0]["skip_reason"]),
))

# ── 8. 待译 / 回执存疑 ────────────────────────────────────────────
plan = repair.build_plan([], {"receipt": {
    "stale": True, "stale_reasons": ["回执 31.2h 未更新（> 30h）"]}}, 3)
check("待译 → 跳过项，原因写明「本地无 LLM」", lambda: (
    assert_(by_kind(plan, "translation")["repairable"] is False, "待译不可自动修"),
    assert_("LLM" in by_kind(plan, "translation")["skip_reason"], "原因应说明无 LLM"),
    assert_("3" in by_kind(plan, "translation")["title"], "标题应带条数"),
))
check("回执存疑 → 可修项（重跑上一轮重建），原因带具体 stale 原因", lambda: (
    assert_(by_kind(plan, "receipt")["repairable"] is True, "回执应可自动修"),
    assert_("31.2h" in by_kind(plan, "receipt")["reason"], by_kind(plan, "receipt")["reason"]),
))
steps = repair._steps_for({"kind": "receipt", "account": "", "title": "回执",
                           "_ctx": {"accounts": ["accZ"]}}, 7, 1, None)
check("回执修复：走 daily_batch 且带上一轮账号（partial 合并重建回执）", lambda: (
    assert_("daily_batch.py" in " ".join(steps[0][1]), "".join(steps[0][1])),
    assert_("accZ" in steps[0][1], "".join(steps[0][1])),
))

# ── 9. 顺序：采集 → 商品 → 转写 → 台账 → 跳过项 ───────────────────
plan = repair.build_plan(
    [acc("accH", [{"level": "warn", "label": "入账", "msg": "缺 1 条"}]),
     acc("accI", [{"level": "warn", "label": "商品", "msg": "商品 1/9"}])],
    {"takeover": [{"account": "accJ", "reason": "日更失败", "kind": "daily"}],
     "receipt": {"stale": True, "stale_reasons": ["无日更回执"]}},
    1)
check("执行顺序：采集最先、回执跳过项最后", lambda: (
    assert_(plan["items"][0]["kind"] == "collect", f"首项 {plan['items'][0]['kind']}"),
    assert_(plan["items"][-1]["kind"] == "receipt", f"末项 {plan['items'][-1]['kind']}"),
))
check("id 连续且唯一", lambda: (
    assert_([it["id"] for it in plan["items"]] ==
            [f"rp{i:02d}" for i in range(1, len(plan["items"]) + 1)], "id 不连续")
))

# ── 10. public() 剥内部字段 ───────────────────────────────────────
plan = repair.build_plan([acc("accK", [{"level": "warn", "label": "商品", "msg": "商品 1/9"}])], {}, 0)
check("build_plan 内部项带 _ctx，public() 后不泄露", lambda: (
    assert_("_ctx" in plan["items"][0], "内部项应带 _ctx 供执行侧用"),
    assert_(all(not k.startswith("_") for it in repair.public(plan)["items"] for k in it),
            "下发结构不应有 _ 前缀字段"),
))

# ── 11. 重复账号不产生重复项 ──────────────────────────────────────
plan = repair.build_plan(
    [acc("accL", [{"level": "warn", "label": "入账", "msg": "缺 2 条"}])],
    {"takeover": [{"account": "accL", "reason": "台账同步失败（exit=1）", "kind": "ledger"}]}, 0)
check("队列与缺口指向同一账号同一类 → 合并成 1 项", lambda: (
    assert_(len([it for it in plan["items"] if it["kind"] == "ledger"]) == 1,
            f"实际 {kinds(plan)}")
))

# ── 12. 停用账号不自动修 ─────────────────────────────────────────
plan = repair.build_plan(
    [acc("accM", [{"level": "warn", "label": "商品", "msg": "商品 1/8（部分视频未挂车）"}],
         enabled=False)],
    {"takeover": [{"account": "accM", "reason": "日更失败（exit=1）", "kind": "daily"}]}, 0)
check("停用账号：项保留但降级为跳过并写明原因", lambda: (
    assert_(all(it["repairable"] is False for it in plan["items"]),
            f"不应可修：{[(it['kind'], it['repairable']) for it in plan['items']]}"),
    assert_(all("停用" in it["skip_reason"] for it in plan["items"]),
            str([it["skip_reason"] for it in plan["items"]])),
    assert_(plan["counts"]["repairable"] == 0, "可修数应为 0"),
))

# ── 13. 启用账号不受影响 ─────────────────────────────────────────
plan = repair.build_plan(
    [acc("accN", [{"level": "warn", "label": "入账", "msg": "缺 1 条"}], enabled=True)],
    {"takeover": [{"account": "accN", "reason": "台账同步失败（exit=1）", "kind": "ledger"}]}, 0)
check("启用账号：台账项仍可修", lambda: (
    assert_(by_kind(plan, "ledger", "accN")["repairable"] is True, "应可修"),
    assert_(plan["counts"]["repairable"] == 1, str(plan["counts"])),
))

# ── 14. 命令构造：超时/天数/重试按默认值下发 ───────────────────
it = {"kind": "collect", "account": "accX", "title": "采集重跑（全链）"}
steps = repair._steps_for(it, days=7, retry=1, timeout=None)
cmd = steps[0][1]
check("采集重跑：缺省超时用类型默认值（不是 0/300），并带 --days", lambda: (
    assert_(cmd[cmd.index("--timeout") + 1] == str(repair.DEFAULT_TIMEOUT["collect"]),
            "timeout=" + cmd[cmd.index("--timeout") + 1]),
    assert_(cmd[cmd.index("--days") + 1] == "7", "缺 --days"),
    assert_("daily_batch.py" in " ".join(cmd), "collect 必须走 daily_batch（唯一回执契约）"),
))
steps = repair._steps_for({"kind": "ledger", "account": "accY", "title": "台账"}, 7, 1, None)
check("台账修复：走 post_update_flow（幂等 write + 终验）", lambda: (
    assert_("post_update_flow.py" in " ".join(steps[0][1]), "".join(steps[0][1])),
))
check("转写修复：带 --no-resume 且两步（转写 → 台账同步）", lambda: (
    assert_(len(repair._steps_for(
        {"kind": "transcribe", "account": "accZ", "title": "转写"}, 7, 1, None)) == 2, "应两步"),
    assert_("--no-resume" in " ".join(repair._steps_for(
        {"kind": "transcribe", "account": "accZ", "title": "转写"}, 7, 1, None)[0][1]), "缺 --no-resume"),
))

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
