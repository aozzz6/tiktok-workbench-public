# -*- coding: utf-8 -*-
"""无人值守编排视图（只读聚合）。

方案五的「异常 → 待接管」层：不新增写台账入口，只把两处既有事实汇总成一个
可执行队列，供 GET /api/pipeline/status 与前端红点消费。

数据源（按优先级）：
  1. `reports/latest_run_summary.json` —— daily_update.py 写出的机器可读回执，
     带 attempts（重试轮次）与 anomalies（失败原因），最权威。
  2. `data/runs.jsonl` —— 工作台运行记录，覆盖「有人在工作台里点的一键运行」，
     也覆盖 automations 逐账号跑 pipeline 的路径（该路径不产 run_summary）。

判定规则（逐账号取各类任务的最新一条）：
  · 最新 pipeline/daily 失败        → 待接管（采集断链）
  · pipeline 成功但最新 ledger 失败 → 待接管（采集到但没落台账）
  · 只有成功记录                    → 不进队列
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import config

TAKEOVER_WINDOW_DAYS = 3
COLLECT_KINDS = ("pipeline", "daily", "ledger")
KIND_LABEL = {"pipeline": "流水线", "daily": "日更", "ledger": "台账同步"}
STALE_AFTER_H = 30          # 日更每日至少一轮；回执超过 30h 未更新视为过期
RUNNING_STALE_H = 6         # 启动登记 running 超过 6h 仍未结束 → 视为异常中断


def _pid_alive(pid) -> bool:
    """进程是否还活着（只查询，绝不发信号）。

    ★ 不能用 `os.kill(pid, 0)` 探活：Windows 上 os.kill 对非 CTRL_* 信号会直接
      终止目标进程，用它探活等于把正在跑的日更杀掉。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                    # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _receipt_meta(summary: dict | None, now: float) -> dict:
    """回执可信度：时间 / 本轮状态 / 失败阶段 / 格式 / 是否过期（2026-10-20 验收 P2-1）。

    ★ 旧实现 stale 只看「有没有回执」：前置检查失败时脚本不写新回执，面板会把
      上一轮的成功 INFO 继续当成本轮有效。现在失败阶段、过期时间、未结束登记
      都会让 stale=True，并把原因原样交给前端展示。

    ★ 2026-10-21 独立验收 P1-D（消费端）：缺阶段结论（stages）或缺口轮标识
      （round_id/rounds）的回执，无论 level 多绿都要标「可信度不足」—— 旧格式回执
      正是靠「9 字段 + level=INFO」让面板假绿的。合同标记由 pipeline/receipts.py 写入：
      contract.legacy_stages=True 表示这一轮来自没有阶段结论的旧编排。
    """
    if not summary:
        return {"state": "none", "interrupted": False, "level": None, "date": None,
                "round_id": None, "round_kind": None, "started_at": None,
                "ended_at": None, "age_s": None,
                "failed_stages": [], "partial": False, "rounds": 0,
                "alerts": [], "stale": True, "stale_reasons": ["无日更回执"],
                "legacy": True, "contract": None, "confidence": "none"}
    state = summary.get("state") or "done"
    ended = summary.get("ended_at")
    started = summary.get("started_at")
    age = round(now - ended, 1) if isinstance(ended, (int, float)) else None
    contract = summary.get("contract") if isinstance(summary.get("contract"), dict) else None
    rounds = summary.get("rounds") or []
    # 旧格式：整个 stages 字段都不存在（v2 即使空也会写 {}），或契约显式标了 legacy_stages
    legacy = ("stages" not in summary) or bool((contract or {}).get("legacy_stages"))
    reasons: list[str] = []
    # ★ 2026-10-30：「running」是回执的原始契约状态（表示本轮还在写），不是给人看的
    #   结论。进程没了 / 超时没动静，这一轮就是「中断」，总览不能再显示「运行中」，
    #   否则一轮被杀的批次会永远挂在「进行中」（今天就是：10-29 08:15 的批次进程
    #   49468 早退了，卡片还写着运行中 106m）。这里只派生标记，不改盘
    #   （receipts.py 约定：interrupted 由看板按 pid 存活判定，只读）。
    interrupted = False
    if state == "running":
        run_age = (now - started) if isinstance(started, (int, float)) else None
        if run_age is None or run_age > RUNNING_STALE_H * 3600:
            reasons.append(f"上轮登记 running 已超 {RUNNING_STALE_H}h 未结束（疑似进程中断）")
            interrupted = True
        # ★ D6-C（2026-10-24）：进程已经没了就别等 6h。增量回执会把每完成一个
        #   账号的结论先落盘，所以这里是「本轮没走完，但已完成账号可靠」，
        #   而不是「整轮作废」。
        _pid = summary.get("pid")
        if _pid and not _pid_alive(_pid):
            reasons.append(f"登记 running 的进程 {_pid} 已退出（本轮中断，已完成账号的结论已保留）")
            interrupted = True
    failed_stages = summary.get("failed_stages") or []
    if failed_stages:
        reasons.append("失败阶段：" + "、".join(failed_stages))
    counts = summary.get("counts") or {}
    n_failed = counts.get("failed") or 0
    if not failed_stages and (summary.get("level") == "ERROR" or n_failed):
        reasons.append(f"最近一轮 ERROR（{n_failed} 个账号失败）")
    if legacy:
        reasons.append("回执缺阶段结论（旧格式，可信度不足：无法确认商品/台账阶段）")
    if not summary.get("round_id") and not rounds:
        reasons.append("回执缺口轮标识（无法判断是哪一轮写的）")
    if age is not None and age > STALE_AFTER_H * 3600:
        reasons.append(f"回执 {age / 3600:.1f}h 未更新（> {STALE_AFTER_H}h）")
    return {
        "state": state,
        # 派生状态：state==running 但进程已退/超时 → 这一轮实际是「中断」，不是「进行中」
        "interrupted": interrupted,
        "started_at": started,
        "level": summary.get("level"),
        "date": summary.get("date"),
        "round_id": summary.get("round_id"),
        "round_kind": summary.get("round_kind"),
        "source": summary.get("source"),
        "ended_at": ended,
        "age_s": age,
        "failed_stages": failed_stages,
        "partial": bool(summary.get("partial")),
        "rounds": len(rounds),
        "alerts": summary.get("alerts") or [],
        "stale": bool(reasons),
        "stale_reasons": reasons,
        "legacy": legacy,
        "contract": contract,
        "confidence": "low" if legacy else ("ok" if not reasons else "suspect"),
    }


def _merge_runs(limit: int = 600) -> list[dict]:
    """runs.jsonl 是「开始 + 终态」两条记录，按 id 合并后按开始时间排序。"""
    p = config.RUNS_JSONL
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    merged: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        prev = merged.get(row["id"], {})
        merged[row["id"]] = {**prev, **row}
    rows = [r for r in merged.values() if r.get("started_at")]
    rows.sort(key=lambda r: r.get("started_at") or 0)
    return rows


def _split_accounts(value: str) -> list[str]:
    return [a.strip() for a in re.split(r"[,\s]+", value or "") if a.strip() and a.strip() != "all"]


def batch_brief(now: float | None = None) -> dict:
    """总览「今日批次」卡用的轻量摘要（2026-10-24）。

    ★ 为什么不直接调 status()：status 会重扫 runs.jsonl 做待接管合并、算 takeover，
      而总览已经有自己的今日聚合，重复做一遍纯属浪费（overview 5s 缓存内每拍都跑）。
      这里只读一份回执 + 复用 _receipt_meta 的可信度结论，口径与 /api/pipeline/status 完全一致。
    """
    now = now if now is not None else time.time()
    summary = latest_summary()
    rec = _receipt_meta(summary, now)
    out = {
        "date": summary.get("date") if summary else None,
        "round_id": rec.get("round_id"),
        "round_kind": rec.get("round_kind"),
        "state": rec.get("state"),                 # done | failed | running | none
        # 前端据此把「running 但进程已没」渲染成「已中断」，而不是永远「进行中」
        "interrupted": bool(rec.get("interrupted")),
        "level": summary.get("level") if summary else None,
        "total": summary.get("total") if summary else 0,
        "counts": (summary.get("counts") or {}) if summary else {},
        "started_at": summary.get("started_at") if summary else None,
        "ended_at": rec.get("ended_at"),
        "age_s": rec.get("age_s"),
        "duration_s": summary.get("duration_s") if summary else None,
        "partial": rec.get("partial"),
        "failed_stages": rec.get("failed_stages") or [],
        "stale": rec.get("stale"),
        "stale_reasons": (rec.get("stale_reasons") or [])[:3],
        "alerts": (rec.get("alerts") or [])[:3],
        "confidence": rec.get("confidence"),
        "legacy": rec.get("legacy"),
    }
    return out


def latest_summary() -> dict | None:
    """最新一份 daily_update 运行回执；优先稳定指针，退化到目录扫描。"""
    root = config.REPORTS_DIR
    pointer = root / "latest_run_summary.json"
    if pointer.exists():
        try:
            d = json.loads(pointer.read_text(encoding="utf-8"))
            # ★ 前置失败/中断的终态回执可能没有 accounts（total=0），但带 state，
            #   也要收：否则消费者会继续把上一轮的旧 INFO 当成本轮结果（P2-1）。
            if isinstance(d, dict) and (d.get("accounts") or d.get("state")):
                return d
        except (OSError, json.JSONDecodeError):
            pass
    candidates = sorted(root.glob("*/run_summary.json"), key=lambda p: p.stat().st_mtime)
    for p in reversed(candidates):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(d, dict) and (d.get("accounts") or d.get("state")):
            return d
    return None


def _summary_takeover(summary: dict | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not summary:
        return out
    for item in summary.get("takeover") or []:
        name = item.get("account")
        if not name:
            continue
        out[name] = {
            "account": name,
            "reason": "；".join(item.get("anomalies") or []) or "采集失败",
            "kind": "daily",
            "kind_label": KIND_LABEL["daily"],
            "run_id": "",
            "at": summary.get("ended_at"),
            "attempts": item.get("attempts") or 1,
            "duration_s": item.get("duration_s"),
            "payload": item.get("payload") or "",
        }
    return out


def status(now: float | None = None) -> dict:
    """待接管总览：{counts, takeover[], summary, last_run_at}。"""
    now = now or time.time()
    cutoff = now - TAKEOVER_WINDOW_DAYS * 86400
    summary = latest_summary()

    per_account: dict[str, dict[str, dict]] = {}
    for run in _merge_runs():
        kind = run.get("kind")
        if kind not in COLLECT_KINDS:
            continue
        if (run.get("started_at") or 0) < cutoff:
            continue
        for name in _split_accounts(run.get("account") or ""):
            per_account.setdefault(name, {})[kind] = run

    items: dict[str, dict] = {}
    for name, kinds in per_account.items():
        latest = max(kinds.values(), key=lambda r: r.get("started_at") or 0)
        reason = ""
        if latest.get("status") == "failed":
            reason = f"{KIND_LABEL.get(latest.get('kind'), latest.get('kind'))}失败" \
                     f"（exit={latest.get('exit_code')}）"
        elif kinds.get("pipeline", {}).get("status") == "done" and \
                (kinds.get("ledger", {}).get("status") == "failed"):
            lt = kinds["ledger"]
            reason = f"台账同步失败（exit={lt.get('exit_code')}）"
        if not reason:
            continue
        items[name] = {
            "account": name,
            "reason": reason,
            "kind": latest.get("kind"),
            "kind_label": KIND_LABEL.get(latest.get("kind"), latest.get("kind") or ""),
            "run_id": latest.get("id"),
            "at": latest.get("started_at"),
            "attempts": 1,
            "duration_s": latest.get("duration_s"),
            "payload": "",
        }

    # run_summary 更权威（含重试次数与门禁原因），覆盖同账号的 jsonl 推断
    for name, item in _summary_takeover(summary).items():
        items[name] = {**items.get(name, {}), **item}

    takeover = sorted(items.values(), key=lambda x: (x.get("at") or 0), reverse=True)
    counts = summary.get("counts") if isinstance(summary, dict) else {}
    receipt = _receipt_meta(summary, now)
    return {
        "as_of": now,
        "window_days": TAKEOVER_WINDOW_DAYS,
        "takeover": takeover,
        "failed": len(takeover),
        "counts": {
            "ok": (counts or {}).get("ok", 0),
            "warn": (counts or {}).get("warn", 0),
            "failed": len(takeover),
        },
        "summary": None if not summary else {
            "date": summary.get("date"),
            "level": summary.get("level"),
            "total": summary.get("total"),
            "duration_s": summary.get("duration_s"),
            "ended_at": summary.get("ended_at"),
            "counts": summary.get("counts"),
            # P2-1：回执状态与失败阶段一并暴露，前端据此区分「本轮失败」与旧 INFO
            "state": receipt["state"],
            "interrupted": receipt.get("interrupted", False),
            "failed_stages": receipt["failed_stages"],
            "partial": receipt["partial"],
            "round_id": receipt["round_id"],
        },
        # 回执可信度（时间/状态/过期原因）与本轮告警，独立于逐账号失败
        "receipt": receipt,
        # 最近一轮实际跑的账号：队列为空但回执存疑时，「重跑全部/重建回执」要用它
        # （重跑这些账号按 partial 并入当日整轮，既重建回执又不丢其它账号的结论）
        "last_round": {
            "round_id": receipt.get("round_id"),
            "round_kind": receipt.get("round_kind"),
            "accounts": [str(x) for x in
                         (((summary or {}).get("last_round") or {}).get("accounts_run") or [])],
        },
        "alerts": receipt["alerts"],
        "stale": receipt["stale"],
        "stale_reasons": receipt["stale_reasons"],
    }


def start_retry(accounts: list[str] | None = None, retry: int = 2,
                timeout: int = 5400) -> dict:
    """重跑失败账号：走 daily_batch 编排层（唯一回执契约），不直接写台账。

    ★ 2026-10-21 独立验收 P1-D：旧实现启动的是 `pipeline/daily_update.py`，它写回执时
      直接覆写当日 summary 与 latest（不按账号合并、不写 stages/rounds/state），一次
      「待接管重跑」就能把没重跑的账号与 `ledger_verify` 失败一起抹掉。现在与定时任务、
      自动化同一条入口：`scripts/daily_batch.py --accounts ...`，子集补跑按账号并入当日
      整轮，未跑账号与全局失败阶段继续可见。

    台账由既有 finish 钩子（_auto_ledger_sync）在 run 成功后自动跟进，保持「唯一写者」不变。
    """
    import runner

    raw = accounts or []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("accounts 必须是账号数组")
    names = list(dict.fromkeys(config.validate_account_name(a) for a in raw if a))
    cmd = [config.python_exe(), str(config.PIPELINE_DIR / "scripts" / "daily_batch.py")]
    if names:
        cmd += ["--accounts", *names]
    cmd += ["--retry", str(max(1, int(retry))), "--timeout", str(max(60, int(timeout)))]
    run = runner.MANAGER.start(
        kind="daily",
        title=f"待接管重跑 · {('、'.join(names) if names else '全部账号')}",
        cmd=cmd,
        account=",".join(names) if names else "all",
    )
    return run.snapshot()
