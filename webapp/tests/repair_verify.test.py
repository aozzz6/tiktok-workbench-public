# -*- coding: utf-8 -*-
"""repair_verify.test.py — R2 有效动作与结果复核口径统一（2026-10-24 独立验收整改）

验收报告 R2 的隔离复现：
  · 临时目录 1 个 MP3、0 个转写；普通 transcribe 项复核返回 False（缺 1 条）；
  · 同一项改成原始 collect、`_ai_kind='transcribe'`，并让待接管队列为空 → 旧代码返回 True（假成功）。

本套锁死修复后的口径：
  1. 执行 / 超时 / 复核共用 effective_kind(item)
  2. 复核两道都要过：有效动作的产物 + 原始规则缺口
  3. 反方向（原始 transcribe、AI 改成 collect 全链）不许误报失败
  4. 会话级联调：AI 换动作后缺转写必须判失败，不能只看到「队列为空」就记成功

跑法：webapp/.venv/Scripts/python.exe tests/repair_verify.test.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import _isolate  # noqa: F401  —— 运行 journal 落到临时文件，别污染生产 runs.jsonl
import config  # noqa: E402
import pipeline_ops  # noqa: E402
import repair  # noqa: E402

# 会话级用例会启动短命 repair 子进程：运行 journal 隔离到临时文件，不污染生产 data/runs.jsonl。
_TMP_RUNS = Path(tempfile.mkdtemp(prefix="rpverify_"))
_ORIG_RUNS = config.RUNS_JSONL
config.RUNS_JSONL = _TMP_RUNS / "runs.jsonl"

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


def mk_item(kind, account="acc_a", ai_kind=None, ctx=None, i=1):
    it = {"id": f"rp{i:02d}", "kind": kind, "kind_label": repair.KIND_LABEL.get(kind, kind),
          "account": account, "title": f"{kind} 修复", "reason": "测试",
          "repairable": True, "skip_reason": ""}
    if ctx is not None:
        it["_ctx"] = ctx
    if ai_kind:
        it["_ai_kind"] = ai_kind
    return it


TMP = Path(tempfile.mkdtemp(prefix="rp_verify_"))
AUDIO = TMP / "acc_audio"
(AUDIO / "transcripts").mkdir(parents=True)
(AUDIO / "a.mp3").touch()          # 1 个音频、0 个转写
EMPTY_AUDIO = TMP / "empty_audio"
(EMPTY_AUDIO / "transcripts").mkdir(parents=True)
(EMPTY_AUDIO / "b.mp3").touch()    # 会话级：1 个音频、0 个转写

print("repair_verify — 有效动作与复核口径（R2）")

# ── 1. effective_kind 是唯一事实源 ────────────────────────────────
check("effective_kind：AI 采纳后取 _ai_kind，未采纳取规则 kind", lambda: (
    assert_(repair.effective_kind(mk_item("collect")) == "collect", "规则动作"),
    assert_(repair.effective_kind(mk_item("collect", ai_kind="transcribe")) == "transcribe",
            "AI 覆盖动作未生效"),
    assert_(repair.effective_kind(mk_item("collect", ai_kind="")) == "collect",
            "空 _ai_kind 应回退规则动作"),
))

# ── 2. 命令构造按有效动作 ────────────────────────────────────────
steps_ai = repair._steps_for(mk_item("collect", ai_kind="ledger"), 7, 1, None)
steps_rule = repair._steps_for(mk_item("collect"), 7, 1, None)
check("执行：collect 被 AI 换成 ledger 时走 post_update_flow，不是 daily_batch 全链", lambda: (
    assert_("post_update_flow.py" in " ".join(steps_ai[0][1]), "".join(steps_ai[0][1])),
    assert_("daily_batch.py" in " ".join(steps_rule[0][1]), "规则动作应仍走全链"),
))

# ── 3. 超时按有效动作取默认值 ────────────────────────────────────
sess = repair.RepairSession([], 7, 1, None)
sess_ai = repair.RepairSession([], 7, 1, None)
t_ai = sess_ai._timeout_for(mk_item("collect", ai_kind="transcribe"))
t_rule = sess._timeout_for(mk_item("collect"))
check("超时：按有效动作取默认值（collect→transcribe 后不再用 3600s）", lambda: (
    assert_(t_ai == repair.DEFAULT_TIMEOUT["transcribe"], f"实际 {t_ai}"),
    assert_(t_rule == repair.DEFAULT_TIMEOUT["collect"], f"实际 {t_rule}"),
    assert_(t_ai != t_rule, "两种动作的默认超时本就不同，未切换说明口径没统一"),
))
sess_explicit = repair.RepairSession([], 7, 1, 120)
check("超时：显式 timeout 优先于任何默认值", lambda: (
    assert_(sess_explicit._timeout_for(mk_item("collect", ai_kind="transcribe")) == 120, "显式值被覆盖"),
))

# ── 4. 复核：缺转写必须失败（报告里的假成功场景）──────────────────
plain = mk_item("transcribe", ctx={"audio_dir": str(AUDIO)})
ai_item = mk_item("collect", ai_kind="transcribe", ctx={"audio_dir": str(AUDIO)})
with patch.object(pipeline_ops, "status", lambda *a, **k: {"takeover": []}):
    ok_plain, why_plain = repair._verify(plain)
    ok_ai, why_ai = repair._verify(ai_item)
check("复核：普通 transcribe 缺 1 条 → False", lambda: (
    assert_(ok_plain is False, str(why_plain)),
    assert_("转写仍缺 1 条" in why_plain, why_plain),
))
check("复核：collect→transcribe 且队列为空时，仍按转写产物判失败（不再假成功）", lambda: (
    assert_(ok_ai is False, "AI 换动作后竟判成功"),
    assert_("转写仍缺" in why_ai, why_ai),
))

# ── 5. 复核：有效动作补齐但原始缺口仍在 → 如实失败 ─────────────────
AUDIO.joinpath("transcripts", "a_transcript.txt").write_text("ok", encoding="utf-8")
with patch.object(pipeline_ops, "status",
                  lambda *a, **k: {"takeover": [{"account": "acc_a", "reason": "采集仍失败（exit=1）"}]}):
    ok_both, why_both = repair._verify(ai_item)
    ok_collect, why_collect = repair._verify(mk_item("collect"))
check("复核：转写补齐了，但原始「采集」缺口仍在 → False 且说明是哪一道没过", lambda: (
    assert_(ok_both is False, "原始缺口仍在却判成功"),
    assert_("原始缺口" in why_both, why_both),
    assert_("采集" in why_both, why_both),
))
check("复核：原始 collect 项仍按待接管队列判失败（原行为不退化）", lambda: (
    assert_(ok_collect is False, str(why_collect)),
    assert_("仍在待接管" in why_collect, why_collect),
))

# ── 6. 复核：两道都过才算成功（反方向不许误报失败）─────────────────
with patch.object(pipeline_ops, "status", lambda *a, **k: {"takeover": []}):
    ok_all, why_all = repair._verify(ai_item)
check("复核：转写补齐 + 队列清空 → 成功", lambda: (
    assert_(ok_all is True, str(why_all)),
))
rev = mk_item("transcribe", ai_kind="collect", ctx={"audio_dir": str(AUDIO)})
with patch.object(pipeline_ops, "status", lambda *a, **k: {"takeover": []}):
    ok_rev, why_rev = repair._verify(rev)
check("反方向：原始 transcribe、AI 改成 collect 全链 → 队列空且转写齐，不应误报失败", lambda: (
    assert_(ok_rev is True, str(why_rev)),
))

# ── 7. 会话级：AI 换动作后，结果汇总必须写实 ──────────────────────
def run_session(item):
    sess = repair.RepairSession([dict(item)], 7, 1, None)
    with patch.object(repair, "MIN_RUNTIME_S", 0.0), \
         patch.object(repair, "_steps_for",
                      lambda it, d, r, t: [("测试步骤", [PY, "-c", "print('ok')"])]), \
         patch.object(pipeline_ops, "status", lambda *a, **k: {"takeover": []}):
        sess.run()
    return sess.snapshot()


snap_bad = run_session(mk_item("collect", ai_kind="transcribe",
                               ctx={"audio_dir": str(EMPTY_AUDIO)},
                               account="acc_b", i=2))
check("会话：AI 换动作后实际缺产物 → 汇总记 failed（不是 200 假成功）", lambda: (
    assert_(snap_bad["counts"]["failed"] == 1, str(snap_bad["counts"])),
    assert_("转写仍缺" in snap_bad["items"][0]["detail"], snap_bad["items"][0]["detail"]),
))
snap_ok = run_session(mk_item("collect", ai_kind="transcribe",
                              ctx={"audio_dir": str(AUDIO)}, account="acc_c", i=3))
check("会话：两道都满足 → 汇总记 ok", lambda: (
    assert_(snap_ok["counts"]["ok"] == 1, str(snap_ok["counts"])),
))

repair._SESSION = None
config.RUNS_JSONL = _ORIG_RUNS
shutil.rmtree(_TMP_RUNS, ignore_errors=True)
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
