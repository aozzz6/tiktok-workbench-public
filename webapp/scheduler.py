# -*- coding: utf-8 -*-
"""定时调度：每日定点跑 `pipeline/scripts/daily_batch.py`（回执契约 + 重试 + 超时 + 终验）。

★ 2026-10-21 独立验收 P1-D：入口从 `pipeline/daily_update.py` 改为 `daily_batch.py`。
  旧编排写回执时直接覆写当日 summary/latest，一次单账号补跑就会抹掉未跑账号与
  `ledger_verify` 失败（面板假绿）；现在定时任务与「待接管重跑」共用同一条入口。

配置存 data/schedule.json：
  {"enabled": true, "time": "09:30", "accounts": [], "last_run": "2026-10-09"}
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import config
import runner
import store

DEFAULT_SCHEDULE = {"enabled": False, "time": "09:30", "accounts": [], "last_run": None,
                    "last_run_id": None}

_tick_lock = threading.Lock()
_pending_reason = ""


@contextmanager
def _settings_lock():
    """设置保存与定时触发共用锁，包括同一目录在两个端口运行的情况。"""
    with _tick_lock, config.SCHEDULE_JSON.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield  # 关闭句柄时系统释放锁


def load() -> dict:
    cfg = config.read_json(config.SCHEDULE_JSON, None)
    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_SCHEDULE)
    for k, v in DEFAULT_SCHEDULE.items():
        cfg.setdefault(k, v)
    return cfg


def eligible_accounts() -> list[str]:
    rows = config.load_accounts().get("accounts") or []
    return [row["account"] for row in rows if isinstance(row, dict) and row.get("account")
            and row.get("active", True) and not row.get("ledger_skip")]


def _write(cfg: dict) -> None:
    path = config.SCHEDULE_JSON
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def save(settings: dict) -> dict:
    global _pending_reason
    if set(settings) != {"enabled", "time", "accounts"}:
        raise ValueError("请提交定时开关、执行时间和账号范围")
    if not isinstance(settings["enabled"], bool):
        raise ValueError("enabled 必须是布尔值")
    if not isinstance(settings["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", settings["time"]):
        raise ValueError("执行时间必须为 HH:MM（00:00–23:59）")
    if not isinstance(settings["accounts"], list):
        raise ValueError("请选择全部账号或指定账号")
    accounts = validate_accounts(settings["accounts"])
    with _settings_lock():
        cfg = load()
        missing = set(accounts) - set(eligible_accounts())
        # 账号停用之后依然可以原样保存关闭开关。
        if missing and (settings["enabled"] or accounts != cfg["accounts"]):
            raise ValueError("账号未启用或已移除：" + "、".join(sorted(missing)))
        cfg.update(enabled=settings["enabled"], time=settings["time"], accounts=accounts)
        _write(cfg)
        _pending_reason = ""
        return cfg


def validate_accounts(accounts: list[str] | None) -> list[str]:
    if accounts is None:
        return []
    if not isinstance(accounts, list):
        raise ValueError("accounts 必须是账号数组")
    return list(dict.fromkeys(config.validate_account_name(a) for a in accounts))


def build_daily_cmd(accounts: list[str] | None = None) -> list[str]:
    """定时任务统一走编排层：失败自动重试 + 单账号超时 + 机器可读回执。

    ★ 2026-10-21 独立验收 P1-D：入口必须是 `scripts/daily_batch.py`。旧实现走
      `daily_update.py`，它每次直接覆写当日 summary/latest（不按账号合并、不写阶段/
      轮次），一次单账号补跑就能把整轮账号与 `ledger_verify` 失败抹掉 → 面板假绿。
      现在所有产品入口（定时、待接管重跑、自动化）统一走 daily_batch 的回执契约。
    """
    accounts = validate_accounts(accounts)
    cmd = [config.python_exe(),
           str(config.PIPELINE_DIR / "scripts" / "daily_batch.py"),
           "--retry", str(config.SCHEDULE_RETRY), "--timeout", str(config.SCHEDULE_TIMEOUT)]
    if accounts:
        cmd += ["--accounts", *accounts]
    return cmd


def trigger(accounts: list[str] | None = None, reason: str = "手动",
            owner: str = "", scheduled_for: str = "") -> dict:
    """起一轮每日增量。

    `owner` 是修复会话下传的台账租约（见 runner._child_env）：非空表示这是修复派生的
    子任务，并入同一租约而不是被它自己挡住。
    """
    cmd = build_daily_cmd(accounts)
    if scheduled_for:
        cmd += ["--date", scheduled_for]
    run = runner.MANAGER.start(
        kind="daily",
        title=f"每日增量更新（{reason}）",
        cmd=cmd,
        account=",".join(accounts) if accounts else "all",
        owner=owner,
    )
    return run.snapshot()


def _scheduled_history(*, title: str = "", run_id: str = "") -> dict:
    for row in reversed(store.load_all(limit=10**9)):
        if row.get("kind") == "daily" and (
                (run_id and row.get("id") == run_id) or (title and row.get("title") == title)):
            return row
    return {}


def _round_result(day: str, run_id: str) -> dict:
    """只读本次任务自己的回执；手工补跑可能已改写当天的汇总。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") or not re.fullmatch(r"[0-9a-f]{12}", run_id):
        return {}
    root = (os.environ.get("DAILY_BATCH_REPORTS_DIR") or config.load_accounts().get("reports_root")
            or config.REPORTS_DIR)
    receipt = next((Path(root) / day / "rounds").glob(f"*-{run_id}.json"), None)
    if receipt:
        row = config.read_json(receipt, {}) or {}
        return {key: row.get(key) for key in ("level", "counts", "failed_stages")}
    return {}


def status() -> dict:
    cfg = load()
    now = dt.datetime.now()
    today = now.date().isoformat()
    eligible = eligible_accounts()
    selected = cfg["accounts"]
    targets = [name for name in eligible if not selected or name in selected]
    next_at = None
    waiting = ""
    if cfg["enabled"]:
        day = now.date() + dt.timedelta(days=int(cfg["last_run"] == today))
        next_at = day.isoformat() + " " + cfg["time"]
        if not targets:
            waiting = "等待可更新的账号，请检查账号管理或调整范围。"
        elif day == now.date() and now.strftime("%H:%M") >= cfg["time"]:
            waiting = _pending_reason or "已到执行时间，等待后台调度。"
    result = None
    if cfg["last_run_id"]:
        live = runner.MANAGER.get(cfg["last_run_id"])
        row = live.snapshot() if live else _scheduled_history(run_id=cfg["last_run_id"])
        if row:
            result = {k: row.get(k) for k in ("id", "status", "started_at", "exit_code", "account_issues")}
            result.update(_round_result(cfg["last_run"], cfg["last_run_id"]))
    return {**cfg, "eligible_accounts": eligible, "target_count": len(targets),
            "next_at": next_at, "waiting": waiting, "last_result": result}


def _due(cfg: dict, now: dt.datetime) -> bool:
    return (cfg["enabled"] is True and cfg["last_run"] != now.date().isoformat()
            and now.strftime("%H:%M") >= cfg["time"])


def _tick(now: dt.datetime | None = None) -> None:
    global _pending_reason
    now = now or dt.datetime.now()
    if not _due(load(), now):
        return
    with _settings_lock():
        cfg = load()
        if not _due(cfg, now):
            return
        today = now.date().isoformat()
        reason = f"定时 {today}"
        # runner 先落 journal 再启动任务。两次写盘之间重启时复用已有任务。
        run = _scheduled_history(title=f"每日增量更新（{reason}）")
        if not run:
            eligible = eligible_accounts()
            selected = [a for a in cfg["accounts"] if a in eligible]
            if not eligible or (cfg["accounts"] and not selected):
                return
            try:
                run = trigger(selected, reason=reason, scheduled_for=today)
            except runner.ResourceBusy:
                _pending_reason = "台账正由其他任务写入，稍后自动重试。"
                return
        cfg.update(last_run=today, last_run_id=run["id"])
        _write(cfg)
        _pending_reason = ""


def loop(stop: threading.Event, interval: int = 30) -> None:
    global _pending_reason
    # 先让 HTTP 服务开始监听，daily_batch 启动后需要回调本工作台。
    while not stop.wait(interval):
        try:
            _tick()
        except Exception:
            _pending_reason = "后台调度遇到错误，稍后自动重试。请查看服务日志。"
            logging.exception("每日更新调度失败")


def start_background(stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=loop, args=(stop,), daemon=True, name="wb-scheduler")
    t.start()
    return t
