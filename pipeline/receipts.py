# -*- coding: utf-8 -*-
"""receipts.py — 日更回执的单一实现（契约 v2 · 构造 / 按账号合并 / 落盘）

背景（2026-10-21 独立验收 P1-D）：
产品里有两条会写回执的路径 ——
  A) `pipeline/scripts/daily_batch.py`（新契约：state/stages/failed_stages/alerts/
     round_id/rounds，子集补跑按账号并入当日整轮）
  B) `pipeline/daily_update.py`（旧编排：date/level/total/counts/accounts/takeover，
     每次直接覆写当日 summary 与 latest，不做账号合并）
一次单账号补跑走 B 就能把整轮 15 个账号与 `ledger_verify` 失败一起抹掉，
面板随即「假绿」。本模块把「契约字段 + 合并策略 + 落盘」抽成唯一实现：

  · `recompute()`      —— level / 退出码语义的唯一推导口（accounts + stages）
  · `merge()`          —— partial→按账号并入；full→替换；keep_previous→保留旧账号
  · `persist()`        —— 写当日 summary + latest 指针 + 本轮独立 round 文件
  · `legacy_summary()` —— 旧编排（daily_update）用同一契约构造本轮，
                         没有阶段结论时显式写 contract.legacy_stages=True，
                         消费端据此判「可信度不足」，而不是当成本轮结论。

两条路径都走这里之后，任何入口的单账号补跑都不会再让未跑账号消失。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CONTRACT_VERSION = 2
SUMMARY_NAME = "run_summary.json"
ROUND_KEEP = 12

# 阶段顺序 = 报告里的判定顺序；每个阶段都必须有明确成功标准
STAGES = ("collect", "products", "ledger_write", "ledger_verify")

RECOVERY_ACTION = {
    "precheck": ("先启动工作台服务（默认 8789：自启 wb_start.ps1，或双击「启动工作台服务」快捷方式；"
                 "直接 python app.py 时是 8787），再重跑 daily_batch.py"),
    "collect": "重跑该账号 pipeline（no_resume=true）后复验台账",
    "products": "重跑该账号（s5b 会补商品）或 extract_products.py --account",
    "ledger_write": "重跑 post_update.py write",
    "ledger_verify": "重跑 post_update.py verify，必要时先 write",
}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def stage(name: str, ok: bool, rc: int | None = None, skipped: bool = False,
          reason: str = "", **extra) -> dict:
    return {"stage": name, "ok": bool(ok), "skipped": skipped,
            "rc": rc, "reason": reason, "at": time.time(), **extra}


def recompute(summary: dict) -> dict:
    """由 accounts + stages 推导 counts / takeover / failed_stages / level。

    ★ 单一事实源：level 和退出码都从这里出，避免「rc 记录了失败但 level 还是 INFO」。
    """
    accounts = summary.get("accounts") or {}
    counts = {"ok": 0, "warn": 0, "failed": 0}
    takeover: list[dict] = []
    for name, r in accounts.items():
        st = r.get("status") or "failed"
        counts[st] = counts.get(st, 0) + 1
        if st != "failed":
            continue
        stg = r.get("stages") or {}
        failed_stage = next((k for k, v in stg.items() if v == "failed"), "collect")
        takeover.append({
            "account": name,
            "status": "failed",
            "stage": failed_stage,
            "attempts": r.get("attempts", 1),
            "anomalies": r.get("anomalies") or [],
            "coverage": r.get("coverage"),
            "gates": bool(r.get("gates")),
            "duration_s": r.get("duration_s"),
            "run_ids": r.get("run_ids") or [],
            "recovery": RECOVERY_ACTION.get(failed_stage, "重跑该账号"),
            "payload": "",
        })
    stages = summary.get("stages") or {}
    # STAGES 之外的阶段（precheck/internal）同样要判失败，不能被过滤掉
    stage_keys = list(STAGES) + [k for k in stages if k not in STAGES]
    failed_stages = [k for k in stage_keys
                     if isinstance(stages.get(k), dict)
                     and stages[k].get("skipped") is not True
                     and stages[k].get("ok") is not True]
    summary["total"] = len(accounts)
    summary["counts"] = counts
    summary["attempts"] = {n: r.get("attempts", 1) for n, r in accounts.items()}
    summary["takeover"] = takeover
    summary["stages"] = stages
    summary["failed_stages"] = failed_stages
    if failed_stages or counts["failed"]:
        summary["level"] = "ERROR"
    elif counts["warn"]:
        summary["level"] = "WARN"
    else:
        summary["level"] = "INFO"
    summary["alerts"] = ([{"stage": k,
                           "reason": (stages.get(k) or {}).get("reason") or f"{k} 阶段失败",
                           "recovery": (stages.get(k) or {}).get("recovery")
                           or RECOVERY_ACTION.get(k, "重跑该阶段")}
                          for k in failed_stages]
                         + ([{"stage": "collect",
                              "reason": f"{counts['failed']} 个账号采集失败",
                              "recovery": "按 takeover 逐账号补跑"}]
                            if counts["failed"] else []))
    return summary


def merge_stages(prev: dict | None, new: dict | None) -> dict:
    """子集补跑时合并阶段结论：本轮跳过的阶段保留上一轮的结论。"""
    out = dict(prev or {})
    for k, v in (new or {}).items():
        if isinstance(v, dict) and v.get("skipped") and k in out:
            continue
        out[k] = v
    return out


def round_meta(summary: dict, ran: list[str]) -> dict:
    return {"round_id": summary.get("round_id"),
            "round_kind": summary.get("round_kind") or "full",
            "source": summary.get("source"),
            "accounts_run": ran,
            "started_at": summary.get("started_at"),
            "ended_at": summary.get("ended_at"),
            "counts": summary.get("counts"),
            "failed_stages": summary.get("failed_stages"),
            "level": summary.get("level"),
            # 本轮自己的退出码（合并视图的 level 可能因为别的轮次仍是 ERROR）
            "exit_code": summary.get("exit_code"),
            "exit_scope": summary.get("exit_scope") or "round",
            # ★ 2026-10-24（D6-C）：running 表示「本轮还在写」，interrupted 由看板按
            #   pid 存活判定（只读，不改盘）；消费端据此判断这轮是否走完了。
            "state": summary.get("state") or "done",
            "pid": summary.get("pid")}





def merge(prev: dict | None, summary: dict, partial: bool,
          keep_previous: bool = False) -> dict:
    """把本轮 summary 并进当日视图。

    ★ P1-D：partial / keep_previous 一律按账号合并（未跑账号不消失）；
      full 整轮才替换 accounts。任何来源都写 rounds[]，消费端能看出是哪一轮。
    """
    prev = prev if isinstance(prev, dict) and isinstance(prev.get("accounts"), dict) else None
    merged = dict(summary)
    accs = dict(summary.get("accounts") or {})
    if (partial or keep_previous) and prev:
        merged_accs = dict(prev.get("accounts") or {})
        merged_accs.update(accs)
        accs = merged_accs
        merged["stages"] = merge_stages(prev.get("stages"), summary.get("stages"))
        merged["partial"] = bool(partial)
        merged["kept_previous"] = not partial
    else:
        merged["stages"] = summary.get("stages") or {}
        merged["partial"] = bool(partial)
        merged["kept_previous"] = False
    merged["accounts"] = accs
    merged["merge_policy"] = "partial→按账号并入当日整轮；full→替换"
    rounds = list((prev or {}).get("rounds") or []) if prev else []
    # 同 round_id 不重复入列（重入场景）
    rounds = [r for r in rounds if r.get("round_id") != summary.get("round_id")]
    ran = summary.get("_ran")
    if ran is None:
        ran = list((summary.get("accounts") or {}).keys())
    rounds.append(round_meta(summary, list(ran)))
    merged["rounds"] = rounds[-ROUND_KEEP:]
    merged["last_round"] = rounds[-1]
    merged.pop("_ran", None)
    # 旧编排（无阶段结论）贡献过的轮次会一直留在这个标记里，直到整轮替换
    own_legacy = bool((summary.get("contract") or {}).get("legacy_stages"))
    prev_legacy = bool(((prev or {}).get("contract") or {}).get("legacy_stages"))
    merged["contract"] = {
        "version": CONTRACT_VERSION,
        "legacy_stages": own_legacy or (prev_legacy and (partial or keep_previous)),
    }
    return recompute(merged)


def persist(summary: dict, day_dir: Path, reports_dir: Path, date: str,
            partial: bool, ran: list[str], log=None, keep_previous: bool = False,
            note: str = "") -> dict:
    """写「当日合并回执 + latest 指针 + 本轮独立 round 文件」。

    ★ P1-3：子集补跑（--accounts）按账号合并进当日整轮，未补跑账号不消失；
      整轮（全量）才替换 accounts。所有轮次都写进 rounds[] 与本轮 round 文件。

    ★ P1-D（2026-10-21）：daily_update 也走本函数。旧实现直接覆写 summary/latest，
      单账号补跑就把整轮结果抹掉 —— 现在永远按账号合并，并标记 contract 版本。
    """
    day_dir = Path(day_dir)
    reports_dir = Path(reports_dir)
    day_dir.mkdir(parents=True, exist_ok=True)
    prev = _read_json(day_dir / SUMMARY_NAME, None)
    merged = merge(prev, dict(summary, _ran=list(ran)), partial, keep_previous)

    payload = json.dumps(merged, ensure_ascii=False, indent=2)
    (day_dir / SUMMARY_NAME).write_text(payload, encoding="utf-8")
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "latest_run_summary.json").write_text(payload, encoding="utf-8")
    # 本轮独立留档（P1-3：每次执行独立回执；只记本轮自己跑出来的结果，不写合并视图）
    own = dict(summary)
    own["merged_view"] = {"total": merged.get("total"), "counts": merged.get("counts"),
                          "level": merged.get("level"),
                          "takeover": [x.get("account") for x in merged.get("takeover") or []]}
    write_round_record(day_dir / "rounds" / f"{summary.get('round_id')}.json", own)
    if callable(log):
        log(f"回执写入{('（' + note + '）') if note else ''} {day_dir / SUMMARY_NAME}"
            f"（round={summary.get('round_id')} "
            f"kind={'partial' if partial else 'full'} source={summary.get('source')} "
            f"total={merged.get('total')} level={merged.get('level')}）")
    return merged


def flush(summary: dict, day_dir: Path, reports_dir: Path, date: str,
          ran: list[str], log=None) -> dict:
    """逐账号增量落盘（2026-10-24 D6-C）：本轮**还没结束**时也把已得结论写下去。

    背景：旧实现只在整轮收尾调一次 `persist()`。进程中途被杀（用户 Ctrl-C、
    崩溃、机器重启）时，这一轮**已经跑成功的账号全部不落账** —— 当日视图继续
    保留上一轮（或更早）的 failed，`repair/scan` 据此报假红，人会去重跑本来
    已经成功的账号。2026-10-24 就这么丢了 4 个账号的成功结果。

    两条硬约束（改这里前先读）：
      1. ★ 中间态**永远按账号合并**（partial=True），即使本轮 kind=full。
         否则 full 轮跑到第 3 个账号就被杀，当日视图只剩 3 个账号，
         其余 44 个的历史结论被一起抹掉 —— 比原来的 bug 更糟。
      2. ★ `ended_at=None` + `state="running"`：这一轮还没结束，不能让消费端
         把它当成「已结束的一轮」去算龄期（`pipeline_ops` 会据此判 stale）。
         进程死掉后由消费端按 `pid` 存活置为「疑似中断」，不在这里改盘。
    """
    return persist(summary, day_dir, reports_dir, date, True, ran,
                   log=log, note="增量·本轮未结束")


def write_round_record(round_path: Path, payload: dict) -> None:
    round_path.parent.mkdir(parents=True, exist_ok=True)
    round_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def new_round_id(pid: int | None = None, started_at: float | None = None) -> str:
    """round_id 必带毫秒：同进程同一秒内可能连跑两轮（子集补跑紧跟整轮）。"""
    import datetime as dt
    import os

    started_at = time.time() if started_at is None else started_at
    pid = os.getpid() if pid is None else pid
    return (f"{dt.datetime.fromtimestamp(started_at).strftime('%Y%m%dT%H%M%S')}"
            f"_{int(started_at * 1000) % 1000:03d}_{pid}")


def legacy_summary(date: str, level: str, started_at: float, results: dict,
                   attempts: dict, source: str = "daily_update",
                   round_kind: str = "legacy", extra: dict | None = None) -> dict:
    """旧编排（daily_update）的本轮回执：字段对齐契约 v2。

    ★ 它没有 stages（collect/products/ledger_write/ledger_verify 的阶段结论），
      所以显式写 contract.legacy_stages=True —— 消费端要判「可信度不足」，
      不能拿它当整轮终验结论。
    """
    accounts = {}
    for name, r in (results or {}).items():
        row = dict(r or {})
        row.setdefault("status", "failed")
        row["attempts"] = attempts.get(name, 1) if isinstance(attempts, dict) else 1
        accounts[name] = row
    summary = {
        "date": date,
        "state": "done",
        "level": level or "INFO",
        "round_id": new_round_id(started_at=started_at),
        "round_kind": round_kind,
        "source": source,
        "started_at": started_at,
        "ended_at": time.time(),
        "duration_s": round(time.time() - started_at, 1),
        "total": len(accounts),
        "counts": {},
        "accounts": accounts,
        "stages": {},
        "failed_stages": [],
        "alerts": [],
        "takeover": [],
        "partial": True,
        "contract": {"version": CONTRACT_VERSION, "legacy_stages": True},
        "exit_code": 0 if (level or "INFO") != "ERROR" else 1,
        "exit_scope": "round",
    }
    summary.update(extra or {})
    recompute(summary)
    # 旧编排自己的 level 口径（write_reports 的判定）优先保留 ERROR
    if (level or "") == "ERROR":
        summary["level"] = "ERROR"
    return summary
