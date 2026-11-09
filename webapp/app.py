# -*- coding: utf-8 -*-
"""TikTok 对标工作台 · FastAPI 后端。

职责：
  - 总览看板：账号状态 / 断点进度 / 门禁 / 最近运行
  - 一键运行：pipeline.py、daily_update.py、单脚本，实时日志（SSE）
  - 对标分析 / 提示词反推 / 账号管理 / 台账表格
  - 一键修复 + AI 诊断（只决策不执行）
  - 转交 Agent：POST /api/handoff 生成交接指令（由「自动化流程 · 指令」按钮调用）

★ 2026-10-24（D1-A）已下线四个页面与接口：报表与台账（/api/reports*）、
  定时自动运行（/api/schedule*）、爆品监控（/api/hot/*）、转交 Agent 页面
  （/api/skills + GET /api/handoff）。scheduler 模块与 hot_product_monitor 脚本保留。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import ai_repair
import config
import groups as account_groups
import llm
import pipeline_ops
import repair
import runner
import scheduler
import store
import benchmark
import tk_reverse
import prompt_optimizer
import reverse_voice

app = FastAPI(title="TikTok 对标工作台", version="1.0.0")
app.include_router(prompt_optimizer.router)
app.include_router(reverse_voice.router)

# ── 接口版本（R6）──────────────────────────────────────────────────
# 启动器 (`_launch.py`) 用它在复用旧进程前判断「磁盘已是新代码、进程还是旧代码」。
# 只要新增/改动了启动器必须依赖的路由，就把版本 +1 并把能力写进 FEATURES。
#   1 = 无 /api/repair/*、/api/ai/*（2026-10-24 之前的进程）
#   2 = 一键修复 + AI 接入（2026-10-24）
API_VERSION = 4
API_FEATURES = ("repair", "ai", "prompt_optimizer", "reverse_voice")


@app.exception_handler(account_groups.GroupStorageError)
async def group_storage_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(runner.ResourceBusy)
async def resource_busy(request, exc):
    """租约冲突 → 400。

    ★ 2026-10-24（B）：`/api/run` 等入口以前不捕获 ResourceBusy，直接冒到 ASGI 层变成
      **500 Internal Server Error**，前端只能看到「Internal Server Error」，
      真正的「有任务正在写同一本台账（xxx）」提示全丢了。这里统一转 400 并带上持有者。
    """
    return JSONResponse({"detail": str(exc), "holders": getattr(exc, "holders", [])},
                        status_code=400)

STATIC_DIR = config.WEBAPP_DIR / "static"

# ────────────────────────── 基础 ──────────────────────────
_overview_cache: dict = {"ts": 0.0, "data": None}
_ledger_cache: dict = {"ts": 0.0, "data": None}
_accounts_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/health")
def health() -> dict:
    r"""健康检查 + 接口版本指纹（启动器据此判断是否需要重启，见 R6）。

    ★ M3（T-25）：带包根（= `webapp\` 的上一级）`VERSION` 里的版本号，页面右上角显示它。

    ★ 安全评审（2026-11）：`VERSION` 是**不可信文件**（包可能被解压到多用户可写目录，
      任何本地进程都能 GET /api/health）。所以这里：
        ① 符号链接一律给 `"0.0.0"` —— 否则它可能被换成指向 `workbuddy\models.json`
          （里面有 LLM key）的链接，请求一次 health 就把凭据读出来了；
        ② 只读**前 64 字节** —— 换成大文件也不会被整读、被轮询拖慢；
        ③ 只接受 `数字.数字.数字`，其它内容（换行、路径、垃圾）一律 `"0.0.0"`。
      任何一条不满足都给 `"0.0.0"`，健康检查本身绝不抛异常。
    """
    version = "0.0.0"
    try:
        version_path = config.WEBAPP_DIR.parent / "VERSION"
        if not version_path.is_symlink():
            with open(version_path, "rb") as f:
                raw = f.read(64)                       # ② 只读前 64 字节
            text = raw.decode("utf-8").strip()
            if re.fullmatch(r"\d+\.\d+\.\d+", text):    # ③ 只认 x.y.z
                version = text
    except (OSError, UnicodeError, ValueError):
        version = "0.0.0"
    return {"ok": True, "ts": time.time(), "api_version": API_VERSION,
            "version": version,
            "features": list(API_FEATURES), "diagnostics": config.diagnostics()}


@app.get("/api/config")
def get_config() -> dict:
    # UI 下拉只列启用账号；accounts_detail 含停用项（供账号页/编辑用）
    cfg = config.load_accounts()
    all_cfg = config.load_accounts(include_inactive=True)
    return {
        "accounts": [a.get("account") for a in cfg.get("accounts", [])],
        "accounts_detail": all_cfg.get("accounts", []),
        "accounts_inactive": [a.get("account") for a in all_cfg.get("accounts", [])
                              if not a.get("active", True)],
        "steps": config.PIPELINE_STEPS,
        "step_labels": config.STEP_LABELS,
        "local_steps": config.LOCAL_STEPS,
        "agent_steps": sorted(config.AGENT_STEPS),
        "daily_steps": config.DAILY_STEPS,
        "ledger": str(config.ledger_path()) if config.ledger_path() else None,
        "diagnostics": config.diagnostics(),
    }


# ────────────────────────── 总览看板 ──────────────────────────
def _count_files(d: Path, pattern: str) -> int:
    try:
        return sum(1 for _ in d.glob(pattern))
    except Exception:
        return 0


def _first_existing(cands: list) -> Path | None:
    for c in cands:
        if not c:
            continue
        p = Path(c)
        if p.exists():
            return p
    return None


def _newest_mtime(paths) -> float:
    """取一组路径（文件或目录）里最新的 mtime；全都不存在 → 0。

    ★ 2026-10-24（总览诊断 P1-2）：数据新鲜度改看「产物最后写入时间」。
      不能用 manifest.last_success_date —— 那个字段只有 legacy daily_update.py 推进，
      主链路 daily_batch → pipeline.py 根本不写，实测 45 个启用账号里 42 个是 null。
    """
    best = 0.0
    for p in paths:
        if not p:
            continue
        try:
            m = Path(p).stat().st_mtime
        except OSError:
            continue
        if m > best:
            best = m
    return best


def _resolve_dirs(acc: dict) -> tuple[Path, Path]:
    """产物目录解析：优先 pipeline/output（实际产物位），再退回 accounts.json 声明。"""
    name = acc.get("account", "?")
    cfg_out = Path(acc["out_dir"]) if acc.get("out_dir") else None
    cfg_audio = Path(acc["audio_dir"]) if acc.get("audio_dir") else None
    default_audio = config.OUTPUT_DIR / f"{name}_audio"
    audio_dir = _first_existing([default_audio, cfg_audio, cfg_out]) or default_audio
    out_dir = _first_existing([config.OUTPUT_DIR, cfg_out, audio_dir]) or config.OUTPUT_DIR
    return audio_dir, out_dir


def _account_view(acc: dict, state: dict, ledger_rows: dict | None = None,
                  ledger_ids: dict | None = None) -> dict:
    name = acc.get("account", "?")
    is_image = acc.get("media_type") == "image"
    audio_dir, out_dir = _resolve_dirs(acc)

    man_path = _first_existing([audio_dir / f"manifest_{name}.json",
                                out_dir / f"manifest_{name}.json"])
    man = (config.read_json(man_path, None) or {}) if man_path else {}
    videos = man.get("videos", {}) or {}

    # 路径先解析再读：新鲜度要 stat 同一个文件，别读一遍再猜路径
    scrape_path = _first_existing([out_dir / f"scrape_{name}.json",
                                   config.OUTPUT_DIR / f"scrape_{name}.json"])
    scrape = (config.read_json(scrape_path, None) or {}) if scrape_path else {}
    items = scrape.get("items", []) or []
    errors = scrape.get("errors", []) or []
    _today_str = time.strftime("%Y-%m-%d")

    aux = _aux_dir()
    prod = _first_existing([
        aux / name / f"{name}_products.json",
        out_dir / f"{name}_products.json",
        audio_dir / f"{name}_products.json",
    ])
    products = config.read_json(prod, {}) if prod else {}
    # ★ 2026-10-24（D4）：口径拆成两个数，勿再合并（原实现把「探测过的视频数」当「有商品数」，
    #   导致 46/46 显示 100% 覆盖，而真实挂车只有 21.5%（83/386，10-24 实测），总览与对标分析互相打脸）：
    #     prod_probed = 跑过商品提取的视频数（键存在即算，值是空数组也算探测过）
    #     prod_count  = 其中**真的挂车**（数组非空）的视频数
    #   流水线健康看 prod_probed（提取覆盖），挂车率是业务事实、不当缺口报警。
    if isinstance(products, dict):
        entries = products.get("products", products)
        if isinstance(entries, dict):
            _lists = [v for v in entries.values() if isinstance(v, list)]
            prod_probed = len(_lists)
            prod_count = sum(1 for v in _lists if v)
        elif isinstance(entries, list):
            prod_probed = len(entries)
            prod_count = sum(1 for v in entries if v)
        else:
            prod_probed = prod_count = 0
    elif isinstance(products, list):
        prod_probed = len(products)
        prod_count = sum(1 for v in products if v)
    else:
        prod_probed = prod_count = 0

    audio_n = _count_files(audio_dir, "*.mp3")
    trans_n = _count_files(audio_dir / "transcripts", "*_transcript.txt")
    scraped_n = len(items)
    failed_n = len(errors)
    ledger_n = (ledger_rows or {}).get(name)
    # 入账覆盖：抓取的视频中有多少已出现在台账「视频链接/ID」列
    ledger_set = (ledger_ids or {}).get(name)
    if is_image and ledger_set is None:
        ledger_set = set()
    # 翻译覆盖：抓取的视频中有多少已出现在台账 N 列「翻译」（已译）
    trans_all = (ledger_ids or {}).get("__translation__") or {}
    trans_set = trans_all.get(name)
    scrape_ids = {i.get("id") for i in items if i.get("id")}
    covered = len(scrape_ids & ledger_set) if ledger_set is not None else None
    translation_covered = (len(scrape_ids & trans_set) if trans_set is not None else None)

    # ── P1/P2 漏斗对账：以音频为基准逐段查缺口 ──
    gaps: list[dict] = []
    if not is_image and audio_n and scraped_n + failed_n != audio_n:
        d = scraped_n + failed_n - audio_n
        gaps.append({"level": "warn", "label": "采集",
                     "msg": f"抓取 {scraped_n} + 失败 {failed_n} 与音频 {audio_n} 相差 {d:+d} 条"})
    if not is_image and audio_n and trans_n < audio_n:
        gaps.append({"level": "warn", "label": "转写",
                     "msg": f"转写缺 {audio_n - trans_n} 条"})
    if not is_image and scraped_n and prod_probed < scraped_n:
        # ★ 口径修正（2026-10-16）：区分「未提取」与「已提取但无商品」
        # 无商品文件 = 该账号从未跑过商品提取（手动一键运行不产商品，属正常）；
        # 有文件但 0 商品 = 已提取、账号本身未挂小黄车（纯内容/bio带货），属正常。
        # ★ 2026-10-24（D4）：判定基准由 prod_count 改为 prod_probed ——
        #   挂车率低是业务常态（实测 32 视频账号 83/386=21.5%），不能当流水线缺口报警，
        #   否则一键修复会给几乎每个账号排「商品补提取」，把真缺口淹掉。
        acc_region = acc.get("region") or ""
        if acc_region == "eu":
            # eu 账号按 2026-10-15 地区规则不抓商品；历史存量 products.json 覆盖率
            # 必然落后于新增视频（6/N 部分覆盖），属预期，一律不报警
            pass
        elif prod is None:
            # 完全没提取过（无商品文件）：信息级提示，非 error
            gaps.append({"level": "info", "label": "商品",
                         "msg": "商品未提取（可手动补跑 extract_products）"})
        else:
            # 提取覆盖不全才算缺口；提取全了但没人挂车属正常，不报警
            # ★ 2026-10-24（D4 二次修正）：采集**成功**覆盖没齐（scraped < audio）时只报 info，
            #   不占「缺口/待处理」名额 —— 那些账号只抓到 1~2 条（其余全是失败），
            #   先补采集才有意义，否则「一键修复」会给 12 个账号排商品补提取，把真问题挤到后面。
            #   注意不能把 failed 算进"齐"：失败意味着没数据。
            collect_complete = scraped_n >= audio_n if audio_n else True
            gaps.append({"level": "warn" if collect_complete else "info", "label": "商品",
                         "msg": f"商品提取 {prod_probed}/{scraped_n} 条"
                                f"（其中挂车 {prod_count} 条）"
                                + ("" if collect_complete else "；采集未齐，先补采集")})
    if covered is not None and scraped_n and covered != scraped_n:
        missing = scraped_n - covered
        gaps.append({"level": "warn", "label": "入账",
                     "msg": f"抓取 {scraped_n} 条中有 {missing} 条未入账（台账共 {ledger_n} 行）"})

    # P1：翻译覆盖 —— 抓取的视频中有多少已在台账 N 列「翻译」有值。
    # 翻译由 15 分钟巡检 automation（kunlun-fanyi）回填，未译属正常待办 → info（不判 warn，
    # 与「无商品=账号未挂车」同理，避免把正常待办当异常报警；巡检会在 15min 内补齐）。
    if not is_image and scraped_n and translation_covered is not None and translation_covered < scraped_n:
        tmiss = scraped_n - translation_covered
        gaps.append({"level": "info", "label": "翻译",
                     "msg": f"翻译 {translation_covered}/{scraped_n}，缺 {tmiss} 条"
                            "（待 15 分钟巡检回填 N 列）"})

    def _rate(have, want):
        return round(100 * have / want) if want else None

    rates = {
        "scrape": _rate(scraped_n, audio_n) if not is_image else None,
        "transcribe": _rate(trans_n, audio_n) if not is_image else None,
        "ledger": _rate(covered, scraped_n) if covered is not None else None,
        # 商品**提取覆盖**计入落地率（不是挂车率）：提取是流水线步骤，挂车是业务事实。
        # ★ 2026-10-24（D4）：原为 prod_count/scraped_n，把「没挂车」算成流水线没干完。
        "products": _rate(prod_probed, scraped_n) if not is_image else None,
        # 翻译覆盖：抓取视频中已译（N 列有值）的比例
        "translation": _rate(translation_covered, scraped_n)
        if translation_covered is not None and not is_image else None,
    }
    vals = [v for v in rates.values() if v is not None]
    landing = round(sum(vals) / len(vals)) if vals else 0

    severity = "ok"
    if any(g["level"] == "error" for g in gaps):
        severity = "error"
    elif any(g["level"] in ("warn", "error") for g in gaps):
        severity = "warn"

    steps = {}
    for s in config.PIPELINE_STEPS:
        rec = state.get(f"{name}|{s}")
        steps[s] = {
            "label": config.STEP_LABELS.get(s, s),
            "ok": bool(rec.get("ok")) if rec else None,
            "cost_s": rec.get("cost_s") if rec else None,
            "detail": (rec.get("detail") or "")[:300] if rec else "",
            "agent_side": s in config.AGENT_STEPS,
        }

    return {
        "account": name,
        "media_type": "image" if is_image else "video",
        "lang": acc.get("lang", ""),
        "out_dir": str(out_dir),
        "audio_dir": str(audio_dir),
        # ★ 2026-10-20 语义拆分（勿回退）：active/has_data = 有产物（可删性判断），
        #   enabled = accounts.json 的日更开关，running = 真正在跑（由 overview() 覆盖）。
        #   历史上前端把 active 当「运行中」，导致所有有数据的账号泳道卡片全亮 run 条。
        "active": bool(audio_n or trans_n or scraped_n or prod_count),
        "has_data": bool(audio_n or trans_n or scraped_n or prod_count),
        "enabled": bool(acc.get("active", True)) and not acc.get("ledger_skip", False),
        "running": False,
        "audio_count": audio_n,
        "transcript_count": trans_n,
        "scrape_items": scraped_n,
        "scrape_errors": failed_n,
        "products_count": prod_count,
        # ★ 2026-10-24（D4）：新增「探测/提取过的视频数」，前端双列显示
        #   「有商品 prod_count / 已提取 prod_probed」；抓取数见 scrape_items。
        "products_probed": prod_probed,
        "ledger_rows": ledger_n,
        "ledger_covered": covered,
        "translation_covered": translation_covered,
        # P0：manifest 未生成时明确区分「不存在」与「0 条」
        "manifest_exists": man_path is not None,
        "manifest_videos": len(videos) if man_path else None,
        "last_success_date": man.get("last_success_date") if man_path else None,
        # ★ 2026-10-24：新鲜度与「今日新视频」——都是零额外 IO（items/mtime 已在手）
        "art_mtime": _newest_mtime([scrape_path, man_path, audio_dir]),
        "scrape_mtime": _newest_mtime([scrape_path]),
        "new_today": sum(1 for i in items
                         if str(i.get("publish_time_gmt8") or "")[:10] == _today_str),
        "rates": rates,
        "landing": landing,
        "severity": severity,
        "gaps": gaps,
        "steps": steps,
        "progress": landing,  # 兼容旧字段名：现在表示数据落地率
    }


def _running_account_set() -> set[str]:
    """当前真正在跑的账号集合（多账号任务 account 是逗号串）。

    ★ 2026-10-20 验收 P2-2：运行态查询不能走 list(limit=N) —— 那是「先取最新 N 条
      再过滤 running」，一条早起的长任务会被后续成批 done 记录挤出窗口，导致卡片
      误判「未在运行」。
    """
    out: set[str] = set()
    for r in runner.MANAGER.running():
        out.update(x.strip() for x in (r.get("account") or "").split(",") if x.strip())
    return out


# ── 总览「今日」聚合（2026-10-24 诊断 P0-2 / P1-1 / P1-2）──────────────────
# ★ 三条口径，改之前先看这里：
#   1) 只统计真实执行（pipeline/daily/ledger/script）。repair 是内部子任务：一次
#      「一键修复」会派生出多条 run，计进去会让「今日执行」凭空翻倍。
#   2) 排除单测桩账号（config.TEST_ACCOUNTS）——它们真的写进过生产 runs.jsonl。
#   3) 统计窗口是「全部今日记录」，不能用 overview 的 runs 字段（那是最近 20 条，
#      47 个账号跑一轮日更就有 45+ 条，窗口必然截断，旧实现就是这么少算 4 倍的）。
_WRITE_KINDS = ("pipeline", "daily", "ledger", "script")
_STOPPED_STATES = ("stopped", "interrupted")
_day_cache: dict = {"key": None, "data": {}}


def _day_of(ts) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or 0))


def _real_runs(rows: list[dict]) -> list[dict]:
    """剔掉 repair 子任务与单测桩账号，只留真实写台账的执行记录。"""
    out: list[dict] = []
    for r in rows:
        if r.get("kind") not in _WRITE_KINDS:
            continue
        names = [n.strip() for n in str(r.get("account") or "").split(",") if n.strip()]
        if names and all(n in config.TEST_ACCOUNTS for n in names):
            continue
        out.append(r)
    return out


def _day_stats(now: float | None = None) -> dict:
    """今日执行聚合 + 7 日序列。按（日期, runs.jsonl mtime）缓存，不重复读 570KB。"""
    now = now if now is not None else time.time()
    today = _day_of(now)
    try:
        mt = config.RUNS_JSONL.stat().st_mtime
    except OSError:
        mt = 0.0
    key = (today, mt)
    if _day_cache["key"] == key:
        return _day_cache["data"]

    real = _real_runs(store.load_all(limit=50000))
    today_rows = [r for r in real if _day_of(r.get("started_at")) == today]
    states = [r.get("status") for r in today_rows]
    covered: set[str] = set()
    for r in today_rows:
        if r.get("kind") not in ("pipeline", "daily"):
            continue
        if r.get("status") in ("failed",) + _STOPPED_STATES:
            continue
        for n in str(r.get("account") or "").split(","):
            n = n.strip()
            if n and n != "all":
                covered.add(n)
    failed_runs = [
        {"id": r.get("id"), "account": r.get("account") or "—",
         "title": r.get("title") or "", "kind": r.get("kind") or "",
         "started_at": r.get("started_at"), "exit_code": r.get("exit_code"),
         "duration_s": r.get("duration_s"), "status": r.get("status")}
        for r in sorted(today_rows, key=lambda x: x.get("started_at") or 0, reverse=True)
        if r.get("status") in ("failed",) + _STOPPED_STATES
    ][:6]

    series = []
    for i in range(6, -1, -1):
        d = _day_of(now - i * 86400)
        sub = [r for r in real if _day_of(r.get("started_at")) == d]
        series.append({
            "date": d[5:],
            "success": sum(1 for r in sub if r.get("status") == "done"),
            "failed": sum(1 for r in sub if r.get("status") in ("failed",) + _STOPPED_STATES),
        })

    data = {
        "date": today,
        "total": len(today_rows),
        "success": states.count("done"),
        "failed": states.count("failed"),
        "stopped": sum(1 for s in states if s in _STOPPED_STATES),
        "running": states.count("running"),
        "accounts_covered": len(covered),
        "failed_runs": failed_runs,
        "series": series,
    }
    _day_cache["key"] = key
    _day_cache["data"] = data
    return data


def _freshness(accounts: list[dict], now: float | None = None,
               stale_h: float = 24.0) -> dict:
    """数据新鲜度：启用账号的产物最后写入时间（max / 超期清单）。

    不用 last_success_date（legacy 字段，主链路不写，实测 42/45 是 null）；
    用 scrape json / manifest / 音频目录的 mtime，每天跑过就一定新。
    """
    now = now if now is not None else time.time()
    en = [a for a in accounts if a.get("enabled")]
    times = [a.get("art_mtime") or 0 for a in en]
    as_of = max(times) if times else 0.0
    stale = [a for a in en if (a.get("art_mtime") or 0) <= 0
             or now - (a.get("art_mtime") or 0) > stale_h * 3600]
    stale.sort(key=lambda a: a.get("art_mtime") or 0)
    return {
        "as_of": as_of or None,
        "as_of_text": time.strftime("%m-%d %H:%M", time.localtime(as_of)) if as_of else "",
        "stale_hours": stale_h,
        "stale_count": len(stale),
        "stale_accounts": [
            {"account": a["account"],
             "art_at": time.strftime("%m-%d %H:%M", time.localtime(a["art_mtime"]))
             if a.get("art_mtime") else "无产物",
             "days": round((now - (a.get("art_mtime") or 0)) / 86400, 1)
             if a.get("art_mtime") else None}
            for a in stale[:8]
        ],
    }


def _scrape_health(accounts: list[dict], now: float | None = None) -> dict:
    """抓取通道健康度：最近一轮采集的成功占比 + 当前代理。

    ★ 2026-10-24：出口 IP 被 TikTok WAF 拦时，items 会骤降到 0、errors 暴涨
      （当天实测 65 err / 1 item），而环境条只查本地文件存在性，完全看不出来。
    ★ 只统计 48h 内真跑过采集的账号（看 scrape json 的 mtime）——否则几个月前的
      旧错误会一直摊在里面，把「今天 5/5 全成」算成 66%。一个都没跑过时退回全量，
      并在 scope 里标明，前端据此提示这是历史口径。
    """
    now = now if now is not None else time.time()
    en = [a for a in accounts if a.get("enabled")]
    recent = [a for a in en if (a.get("scrape_mtime") or 0) > now - 48 * 3600]
    scope = "recent" if recent else "all"
    pool = recent or en
    items = sum(a.get("scrape_items") or 0 for a in pool)
    errors = sum(a.get("scrape_errors") or 0 for a in pool)
    total = items + errors
    ratio = round(items / total, 3) if total else None
    level = "unknown" if ratio is None else (
        "bad" if ratio < 0.6 else "warn" if ratio < 0.9 else "ok")
    proxy = (config.PROXY or "").replace("http://", "").replace("https://", "")
    return {"items": items, "errors": errors, "ratio": ratio, "level": level,
            "proxy": proxy, "accounts": len(pool), "scope": scope}


@app.get("/api/overview")
def overview(refresh: bool = False) -> dict:
    if not refresh and _overview_cache["data"] and time.time() - _overview_cache["ts"] < 5:
        return _overview_cache["data"]

    cfg = config.load_accounts(include_inactive=True)
    state = config.read_json(config.STATE_FILE, {}) or {}
    gate = config.read_json(config.GATE_REPORT, {})
    ledger_rows = _ledger_row_counts(refresh)
    ledger_ids = _ledger_id_sets(refresh)

    accounts = [_account_view(a, state, ledger_rows, ledger_ids) for a in cfg.get("accounts", [])]
    # ★ 2026-10-20：真运行态唯一来源是 runner 的运行队列（多账号任务 account 是逗号串）。
    #   泳道卡片/组头据此判断「运行中」，不再复用 active（有产物）。
    # ★ 必须用 running()：list(limit=50) 会漏掉被大量新记录挤出去的早期长任务
    running_set = _running_account_set()
    for _a in accounts:
        _a["running"] = _a["account"] in running_set
    # P2：异常账号优先，未启用（无任何产物）的沉底单独分组
    sev_order = {"error": 0, "warn": 1, "ok": 2}
    accounts.sort(key=lambda a: (
        0 if a["active"] else 1,
        sev_order.get(a["severity"], 3),
        -a["audio_count"],
    ))
    runs = store.load_all(limit=20)
    # ★ 2026-10-24（总览诊断）：以下四块是首屏新 KPI 的数据源，全部只读、零副作用。
    day = _day_stats()
    day["accounts_total"] = sum(1 for a in accounts if a["enabled"])
    day["new_videos"] = sum(a.get("new_today") or 0 for a in accounts if a["enabled"])

    data = {
        "ts": time.time(),
        "server_now": time.time(),
        "accounts": [a for a in accounts if a["active"]],
        "idle_accounts": [a for a in accounts if not a["active"]],
        "gate": gate or {},
        "runs": runs[::-1],
        "active_runs": runner.MANAGER.list(limit=10),
        "schedule": scheduler.load(),
        "pending_translation": _pending_snapshot(),
        "diagnostics": config.diagnostics(),
        "day": day,
        "batch": pipeline_ops.batch_brief(),
        "freshness": _freshness(accounts),
        "scrape_health": _scrape_health(accounts),
    }
    _overview_cache.update(ts=time.time(), data=data)
    return data


# ────────────────────────── 一键运行 ──────────────────────────
def _build_pipeline_cmd(p: dict) -> list[str]:
    account = p.get("account")
    if not account:
        raise HTTPException(400, "缺少 account")
    try:
        if not isinstance(account, str):
            raise ValueError("账号名必须是字符串")
        account = config.validate_account_name(account)
        from datetime import date
        start = date.fromisoformat(p.get("date_from", ""))
        end = date.fromisoformat(p.get("date_to", ""))
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"账号或日期无效: {e}")
    # 2026-11-08（T-20 / CRD-0006）：openai 转写后端已退役（脚本遇到它会直接退 2）。
    # 但老前端下拉（index.html 的「openai（CPU 兜底，慢）」）和旧调用方仍可能带这个值；
    # 这里按 auto 执行并留一行日志 —— 绝不让整轮日更 /「一键运行」因为一个已退役的
    # 后端名而整体失败。非法值仍然 400。
    if p.get("backend"):
        if p["backend"] == "openai":
            print("[run] backend=openai 已退役（本包只带 faster-whisper），按 auto 执行",
                  flush=True)
            p = {**p, "backend": "auto"}
        elif p["backend"] not in ("auto", "faster"):
            raise HTTPException(400, "不支持的转写后端（只支持 auto / faster；openai 已退役）")
    ledger = config.ledger_path()
    xlsx = p.get("xlsx") or (str(ledger) if ledger else "")
    if not xlsx:
        raise HTTPException(400, "未找到台账 xlsx，请显式指定")

    cmd = [config.python_exe(), str(config.PIPELINE_DIR / "pipeline.py"),
           "--account", account,
           "--from", p.get("date_from", ""), "--to", p.get("date_to", ""),
           "--xlsx", xlsx]
    steps = p.get("steps") or []
    if ("steps" in p and (not isinstance(p["steps"], list) or not p["steps"])) or \
            not isinstance(steps, list) or any(s not in config.PIPELINE_STEPS for s in steps):
        raise HTTPException(400, "无效的流水线步骤")
    # ★ 一键本地运行自动补商品提取（2026-10-16）：若 steps 含本地抓取/转写
    #   但未含 s5b_products，则追加，使 us/uk 账号手动运行也落 *_products.json。
    #   默认全链（steps 为空）时 pipeline 的 ORDER 已含 s5b，无需处理。
    if steps and "s5b_products" not in steps and any(
            s in steps for s in ("s2_scrape", "s5_transcribe")):
        steps = steps + ["s5b_products"]
    if steps:
        cmd += ["--steps", *steps]
    if p.get("no_resume"):
        cmd.append("--no-resume")
    if p.get("allow_scrape_errors"):
        cmd.append("--allow-scrape-errors")
    lang = p.get("lang")
    if lang:
        if not isinstance(lang, str) or lang not in config.SUPPORTED_LANGS:
            raise HTTPException(400, f"不支持的转写语言: {lang}")
    else:
        account_cfg = config.load_accounts_raw().get("accounts", [])
        account_row = next((a for a in account_cfg
                            if isinstance(a, dict) and a.get("account") == account), {})
        lang = config.guess_lang(account_row.get("region"), account_row.get("lang"))
    cmd += ["--lang", lang]
    if p.get("backend"):
        cmd += ["--backend", p["backend"]]
    return cmd


def _multi_title(p: dict, accounts: list[str]) -> str:
    """多账户运行标题：N 个账号 + 日期区间。"""
    return (f"多账户 · {len(accounts)} 个 · {p.get('date_from')}~{p.get('date_to')} · "
            + ("、".join(accounts[:6]) + ("…" if len(accounts) > 6 else "")))


def _build_multi_pipeline_cmd(p: dict, accounts: list[str]) -> list[str]:
    """多账户批跑：对每个账号复用单账号校验+命令构造（含 s5b 自动补、lang 推断），
    包在一个 python 驱动里顺序串行执行 → 单任务/单日志流/单进度面板。

    顺序而非并发：转写占 GPU/模型，并发会互相抢资源且日志交织难读；
    串行让每个账号完整跑完再下一个，某账号失败即整链停（与单账号 fail-fast 同语义）。
    """
    per_cmds = [_build_pipeline_cmd({**p, "account": acc}) for acc in accounts]
    driver = (
        "import subprocess, sys\n"
        "cmds = " + repr(per_cmds) + "\n"
        "accounts = " + repr(accounts) + "\n"
        "for i, c in enumerate(cmds, 1):\n"
        "    print(f'[multi] ({i}/{len(cmds)}) 账号: ' + accounts[i-1], flush=True)\n"
        "    r = subprocess.run(c)\n"
        "    if r.returncode != 0:\n"
        "        print(f'[multi] 第 {i}/{len(cmds)} 个账号失败 rc={r.returncode}，停止后续', flush=True)\n"
        "        sys.exit(r.returncode)\n"
        "print('[multi] 全部账号完成', flush=True)\n"
    )
    return [config.python_exe(), "-c", driver]


# 2026-10-15 更新：本地步骤完成后由 _auto_ledger_sync 自动触发台账同步，
# 提示只保留仍需 Agent 侧补齐的部分（新账号建表 / 中文翻译 / 回填）。
AGENT_PENDING_NOTE = (
    "ℹ 本地步骤完成 → 已自动触发「台账同步」（write 幂等入账 + 导出待译 + 磁盘终验）。"
    "新账号建表(s3_4_sheet)/中文翻译(s6)/回填(s7)仍为 Agent 侧步骤：待译条目>0 时请点"
    "「生成指令」转交 Agent，译文回来后 post_update.py apply-trans 回填。"
)


# ── 跑完自动入台账（2026-10-15，用户要求）─────────────────────────────
# pipeline / daily_update 跑完即自动落台账，不再依赖手动点「台账同步 + 翻译交接」。
# post_update_flow 内部为幂等对账（已入账视频不重复写），重复触发无害。
_LEDGER_SYNC_LOCK = threading.Lock()
_LEDGER_SYNC_ACTIVE = False
_LEDGER_SYNC_QUEUE: list[dict] = []


def _image_only_run(account: str) -> bool:
    names = account.split(",") if account and account != "all" else []
    types = {a.get("account"): a.get("media_type") for a in config.load_accounts_raw().get("accounts", [])}
    return bool(names) and all(types.get(name) == "image" for name in names)


def _start_ledger_sync(title: str, account: str, translate_backfill: bool) -> bool:
    """在锁内登记并启动一次台账同步，防止完成钩子并发漏掉任务。

    ★ R1：台账写入由执行层的 `ledger` 租约互斥。若此刻修复会话正持租约，
      这里不能静默丢掉 —— 重新排队，等租约释放的回调把它接上。
    """
    global _LEDGER_SYNC_ACTIVE
    flow = config.SCRIPTS_DIR / "post_update_flow.py"
    if not flow.exists():
        return False
    tag = " [trans-backfill]" if translate_backfill else ""
    _LEDGER_SYNC_ACTIVE = True
    cmd = [config.python_exe(), str(flow)]
    if account and account != "all":
        cmd += ["--accounts", *account.split(",")]
    try:
        runner.MANAGER.start(
            kind="ledger",
            title=f"台账同步 · 自动跟随「{title}」{tag}",
            cmd=cmd,
            account=account,
        )
    except runner.ResourceBusy as e:
        _LEDGER_SYNC_ACTIVE = False
        _LEDGER_SYNC_QUEUE.append({"title": title, "account": account,
                                   "translate_backfill": translate_backfill})
        print(f"[ledger] 台账写入被占用，排队等租约释放：{e}", flush=True)
        return False
    return True


def _drain_ledger_queue(_lease=None) -> None:
    """租约释放后接上被挡下的台账同步（回调由 runner.RESOURCES 触发）。"""
    with _LEDGER_SYNC_LOCK:
        if _LEDGER_SYNC_ACTIVE or _trans_running or not _LEDGER_SYNC_QUEUE:
            return
        queued = _LEDGER_SYNC_QUEUE.pop(0)
    _start_ledger_sync(queued["title"], queued["account"],
                       queued["translate_backfill"])


runner.RESOURCES.on_release(_drain_ledger_queue)


def _auto_ledger_sync(run) -> None:
    global _LEDGER_SYNC_ACTIVE
    if run.kind not in {"pipeline", "daily"} or run.status != "done":
        return
    if "台账同步" in run.title:  # 防自我套娃
        return
    # 若源 pipeline run 勾选了「翻译回填」，在台账同步 run 标题上打标，
    # 让 _on_ledger_sync_done 完成后衔接翻译（巡检下一拍立即翻，不等 15 分钟）。
    translate_backfill = _TRANS_BACKFILL_RUNS.pop(run.id, False)
    with _LEDGER_SYNC_LOCK:
        ledger_running = any(r.get("kind") == "ledger"
                             for r in runner.MANAGER.running())
        if _LEDGER_SYNC_ACTIVE or _trans_running or ledger_running:
            _LEDGER_SYNC_QUEUE.append({"title": run.title, "account": run.account,
                                       "translate_backfill": translate_backfill})
            print(f"[ledger] 已有台账同步运行，排队等待：{run.title}", flush=True)
            return
        if translate_backfill:
            print(f"[trans-backfill] 本 run 勾选翻译回填，台账同步完成后衔接翻译", flush=True)
        _start_ledger_sync(run.title, run.account, translate_backfill)


runner.MANAGER.add_finish_hook(_auto_ledger_sync)

PENDING_TRANS_FILE = Path(__file__).resolve().parent / "data" / "pending_translation.json"

# ★ 2026-10-24：队列的读写必须串行。_refresh 会写/删同一个 json，而
#   POST /api/translation/backfill（手动点按钮）与 _pending_snapshot()（5s 轮询）
#   可能同时进来 → write_text 非原子，并发下会读到半截 JSON。
#   用 RLock（不是 Lock）：_pending_snapshot 已持锁后还要再调 _refresh → 普通 Lock 自锁死。
_PENDING_LOCK = threading.RLock()
_PENDING_SIG = {"mtime": -1.0}


def _pending_ledger_signature(ledger):
    if not ledger:
        raise FileNotFoundError("未找到台账，无法确认待译数量")
    path = Path(ledger)
    stat = path.stat()
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)

# 手动一键运行勾选「翻译回填」的 pipeline run 集合（run.id → True）：
# 台账同步完成后若命中，立即衔接一次翻译刷新（不等 15 分钟巡检下一拍）。
_TRANS_BACKFILL_RUNS: dict[str, bool] = {}


def _refresh_pending_translation() -> int:
    """串行化的队列重建入口（真正的扫描在 _scan_pending_translation）。"""
    with _PENDING_LOCK:
        return _scan_pending_translation()


def _scan_pending_translation() -> int:
    """直扫台账真值：「音频(M) 有值而 翻译(N) 为空」的行 = 待译。

    非空则写入 data/pending_translation.json 供 15 分钟巡检 automation 消费；
    空则删除队列文件。返回待译条数。

    ★ 列定位按表头名（兼容「音频/备注」与「翻译/备注中文翻译」），不硬编码索引：
      14 列规范 M 音频=第 13 列(0-based 12)、N 翻译=第 14 列(0-based 13)。
    ★ 别直接调本函数：外层 _refresh_pending_translation() 负责加锁。
    """
    import json as _json
    import openpyxl as _openpyxl
    items = []
    ledger = config.ledger_path()
    signature = _pending_ledger_signature(ledger)
    if ledger and Path(ledger).exists():
        wb = _openpyxl.load_workbook(ledger, read_only=True, data_only=True)
        try:
            for sn in wb.sheetnames:
                if not sn.startswith("对标记录-"):
                    continue
                acc = sn.replace("对标记录-", "")
                ws = wb[sn]
                hdr = [str(c.value or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1), [])]
                # 音频列（M）：表头「音频」或旧名「备注」；翻译列（N）：「翻译」或「备注中文翻译」
                mi = next((i for i, h in enumerate(hdr) if h in ("音频", "备注")), 12)
                ni = next((i for i, h in enumerate(hdr) if h in ("翻译", "备注中文翻译")), 13)
                li = next((i for i, h in enumerate(hdr) if "视频链接" in h), 5)
                for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                    row = list(row)
                    pad = [None] * (max(mi, ni, li) + 1 - len(row))
                    row = row + pad
                    audio = str(row[mi] or "").strip()
                    zh = str(row[ni] or "").strip()
                    if audio and not zh:
                        s = str(row[li] or "")
                        m = re.search(r"ID:\s*\(?\s*(\d{15,25})", s) or re.search(r"/video/(\d{15,25})", s)
                        items.append({"account": acc, "row": i, "vid": m.group(1) if m else "",
                                      "text": audio[:2000]})
        finally:
            wb.close()
    if _pending_ledger_signature(ledger) != signature:
        raise OSError("扫描期间台账已变化，保留旧队列并等待重试")
    PENDING_TRANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if items:
        # Readers outside this process do not share _PENDING_LOCK.
        descriptor, name = tempfile.mkstemp(prefix='pending_', suffix='.tmp',
                                             dir=PENDING_TRANS_FILE.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                _json.dump({"ts": time.time(), "total": len(items), "items": items},
                           target, ensure_ascii=False, indent=1)
            # Windows readers can briefly hold the destination without delete sharing.
            for attempt in range(6):
                try:
                    Path(name).replace(PENDING_TRANS_FILE)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            Path(name).unlink(missing_ok=True)
    elif PENDING_TRANS_FILE.exists():
        PENDING_TRANS_FILE.unlink()
    # 同步 mtime 签名：显式刷新过就别让 _pending_snapshot() 再扫一遍
    try:
        _PENDING_SIG["mtime"] = Path(ledger).stat().st_mtime if ledger else 0.0
    except (OSError, TypeError, ValueError):
        pass
    _PENDING_SIG.update(signature=signature, queue_exists=bool(items))
    return len(items)


def _read_pending_translation() -> dict:
    """串行化的队列读取入口（文件写是非原子的，读也要排队，避免读到半截 JSON）。"""
    with _PENDING_LOCK:
        return _read_pending_unlocked()


def _read_pending_unlocked() -> dict:
    """待译队列摘要。除 total/ts 外补 by_account（总览「待翻译」卡下钻用）。

    ★ 2026-10-24：只加字段不改语义 —— 原有消费者只读 total，保持兼容。
    ★ 别直接调本函数：外层 _read_pending_translation() 负责加锁。
    """
    try:
        import json as _json
        d = _json.loads(PENDING_TRANS_FILE.read_text(encoding="utf-8"))
        import math
        if (not isinstance(d, dict) or type(d.get('total')) is not int
                or d['total'] < 0 or not isinstance(d.get('items'), list)
                or len(d['items']) != d['total']
                or any(not isinstance(it, dict) for it in d['items'])):
            raise ValueError('待译队列条数与明细不一致')
        ts = float(d.get('ts') or 0)
        if not math.isfinite(ts) or ts < 0:
            raise ValueError('待译队列时间戳无效')
        if not d.get("total"):
            return {"total": 0, "ok": True}
        counts: dict[str, int] = {}
        for it in (d.get("items") or []):
            name = (it or {}).get("account") or "未知"
            counts[name] = counts.get(name, 0) + 1
        by_account = [{"account": k, "count": v}
                      for k, v in sorted(counts.items(), key=lambda kv: -kv[1])][:8]
        ts = float(d.get("ts") or 0)
        # ★ 2026-10-24（修「待翻译 9 条」常驻）：必须下发快照龄期。
        #   队列只由 webapp 自己的 3 个入口重建，真正写 N 列的是外部巡检 automation，
        #   没有 age_s 前端就无法区分「真的待译」与「过期快照」→ 只能永久显示 9 条。
        return {"total": d.get("total", 0), "ts": d.get("ts"), "ok": True,
                "age_s": round(time.time() - ts, 1) if ts else None,
                "by_account": by_account,
                "truncated": len(counts) > len(by_account),
                "by_account_total": len(counts)}
    except FileNotFoundError:
        # 队列文件不存在 = 待译已清零（_refresh 空结果会 unlink）→ 正常态，不是读取失败
        return {"total": 0, "ok": True}
    except Exception:
        # 读不出来 ≠ 没有待译：用 ok=False 让前端能区分「真的 0」与「读失败」
        return {"total": 0, "ok": False}


# ★ 2026-10-24（A 方案：按台账 mtime 惰性重扫）
#   背景：pending_translation.json 是台账的物化快照，重建入口只有 3 个本地调用点
#   （/api/translation/pending、台账同步后钩子、auto_translate 完成）。而真正写 N 列的是
#   外部「待译巡检 automation」——它直写 xlsx，不回调本服务 → 快照只增不减，
#   台账已全部译完仍会显示「待翻译 9 条」（实测陈旧 66 分钟）。
#   这里以台账 mtime 作失效信号：任何写入方都会在下一次 overview 自动纠正，
#   只在 mtime 变化后付一次全表扫描成本（实测 720ms，外层另有 5s overview 缓存）。
#   （_PENDING_LOCK / _PENDING_SIG 定义在 PENDING_TRANS_FILE 之后）


def _pending_snapshot() -> dict:
    """台账变了才重算待译队列，然后返回摘要。没变则纯读缓存文件（零扫描）。"""
    with _PENDING_LOCK:
        try:
            ledger = config.ledger_path()
            signature = _pending_ledger_signature(ledger)
            if (signature != _PENDING_SIG.get('signature')
                    or Path(ledger).stat().st_mtime != _PENDING_SIG['mtime']
                    or (_PENDING_SIG.get('queue_exists') and not PENDING_TRANS_FILE.exists())):
                _refresh_pending_translation()
        except Exception as e:
            print(f"[pending] 台账重扫失败（保留旧队列）: {e}", flush=True)
            return {**_read_pending_translation(), 'ok': False,
                    'error': '台账暂不可核验，待译数字不可信'}
        return _read_pending_translation()


# ────────────────────────── 手动翻译回填（2026-10-16 新增）──────────────────────────
# ★ 关键架构约束：webapp 是纯本地服务，无 LLM 入口；N 列「翻译」必须由
#   kunlun-fanyi（WorkBuddy 模型能力 / 15 分钟巡检 automation b35e15be）生成。
#   待译队列 pending_translation.json 只存 {row, account, text(原文)}，**不含译文**，
#   本地无法生成译文——任何「本地把原文写进 N 列」的实现都是错的（会把外文写进中文列）。
#
#   因此按钮的真实语义 = **手动立即触发 15 分钟巡检 automation 的翻译动作**：
#   队列空 → 直接返回「无待译」；队列非空 → 提示走巡检/Agent（巡检下一拍会自动翻），
#   绝不本地伪造译文。这样既满足用户「立即回填」诉求（巡检 15 分钟内完成），
#   又保证翻译质量与自动巡检完全一致（同一 LLM 引擎、同一 apply-trans 入口）。
TRANS_ENGINE_NOTE = (
    "翻译引擎 = kunlun-fanyi（与 15 分钟巡检 automation b35e15be 同口径，质量零差异；"
    "巡检每 15 分钟自动兜底，即使本次触发未翻全也会补齐）。"
    "本地服务无 LLM，N 列译文只能由巡检/Agent 生成，本按钮 = 手动立即触发该动作。"
)


@app.get("/api/translation/pending")
def translation_pending() -> dict:
    """待译队列：{total, ts}，供前端按钮角标显示。"""
    return _read_pending_translation()


@app.post("/api/translation/auto")
def translation_auto() -> dict:
    """手动触发本地 LLM 自动翻译（不等 run 钩子 / 周期巡检）。

    后台线程执行 auto_translate.py：扫台账待译 → LLM 翻译 → 写 N 列。
    """
    if not config.AUTO_TRANSLATE_ENABLED:
        raise HTTPException(400, "本地自动翻译已关闭（WB_AUTO_TRANSLATE=0）")
    script = config.SCRIPTS_DIR / "auto_translate.py"
    if not script.exists():
        raise HTTPException(404, f"缺少脚本: {script}")
    pending = _read_pending_translation().get("total", 0)
    threading.Thread(target=_run_auto_translate, daemon=True).start()
    return {"ok": True, "triggered": True, "pending": pending,
            "model": "TM ·Auto（models.json）",
            "note": "已后台触发本地 LLM 翻译；完成后 N 列自动写入、待译队列清空。"}


@app.post("/api/translation/backfill")
def translation_backfill(payload: dict | None = None) -> dict:
    """手动立即触发「翻译回填」动作（等价手动点 15 分钟巡检一次）。

    先重扫台账刷新待译队列（修复：队列被误删后，按钮能自动重建），
    再返回待译明细。**不本地写 N 列**（本地无 LLM，伪造译文是错的）。
    """
    # ★ 先刷新队列（直扫台账：音频有值而翻译为空的行），修复「队列被误删导致巡检空转」
    with _PENDING_LOCK:
        fresh = _refresh_pending_translation()
        q = _read_pending_translation()
        # ★ 2026-10-24（D5）：只有**明确** ok=False 才算读取失败。
        #   旧写法 `if not q.get("ok")` 把「缺 ok 字段」也判成失败 → 与旧调用方/测试桩
        #   （返回 {'total': N}）不兼容，直接 503。缺省视为正常，语义与
        #   `_read_pending_unlocked()` 的四态一致：ok=False 仅代表"读失败"。
        if q.get("ok") is False:
            raise HTTPException(503, "待译队列读取失败，请稍后重试")
        total = max(q.get("total", 0), fresh)
        # ★ 2026-10-24（D5 二次修复）：不再「二次读文件 + 与 max(total,fresh) 死比」。
        #   旧实现有两个坑：① 绕过 _read_pending_unlocked 的校验与锁语义，形成第二个事实源；
        #   ② fresh 比队列快照新时 len(items) != total 必然成立 → 499/503「队列在刷新后已变化」。
        #   现在优先用摘要自带的 by_account（与队列同一份快照），只有摘要没带才回退读文件，
        #   且只校验文件自洽性，不拿它跟 total 对赌。
        by_acc: dict[str, int] = {}
        if total:
            ba = q.get("by_account") or []
            if isinstance(ba, list) and ba:
                for row in ba:
                    if isinstance(row, dict) and row.get("account") is not None:
                        by_acc[str(row.get("account"))] = int(row.get("count") or 0)
            else:
                try:
                    d = json.loads(PENDING_TRANS_FILE.read_text(encoding="utf-8"))
                    items = d["items"]
                    if len(items) != int(d.get("total") or 0):
                        raise ValueError("队列条数与明细不一致")
                    for it in items:
                        k = str((it or {}).get("account") or "default")
                        by_acc[k] = by_acc.get(k, 0) + 1
                except Exception as e:
                    raise HTTPException(503, "待译队列暂不可读取，请稍后重试") from e
    if not total:
        return {"ok": True, "translated": 0, "accounts": [],
                "note": "无待译条目（队列为空，且台账无音频未译行）", "engine": "kunlun-fanyi"}
    return {
        "ok": True, "pending": True, "translated": 0, "pending_total": total,
        "accounts": sorted(by_acc.keys()), "per_account": by_acc,
        "note": (f"待译 {total} 条（{len(by_acc)} 账号），待译队列已刷新。"
                 "请交给 Agent 按 kunlun-fanyi 规范翻译并回填 N 列；本按钮不会启动 Agent。"),
        "engine": "kunlun-fanyi",
    }


def _on_ledger_sync_done(run) -> None:
    """台账同步（write+导出待译）完成后 → 刷新待译队列 → 本地 LLM 自动翻译回填。

    ★ 2026-10-16 重构：翻译不再依赖周期巡检 automation（MINUTELY 巡检实际不触发，
    导致翻译长期堆积）。改为「台账同步完成即触发本地 LLM 翻译」（run 钩子），
    真自动、不等调度。翻译模块 pipeline/scripts/auto_translate.py（读 models.json）。
    周期巡检保留为兜底。
    """
    global _LEDGER_SYNC_ACTIVE, _trans_running
    if run.kind != "ledger":
        return
    n = 0
    if run.status == "done":
        try:
            n = _refresh_pending_translation()
            if n:
                print(f"[pending] 待译队列已更新：{n} 条", flush=True)
        except Exception as e:
            print(f"[pending] 待译队列刷新失败: {e}", flush=True)
    with _LEDGER_SYNC_LOCK:
        _LEDGER_SYNC_ACTIVE = False
        if _LEDGER_SYNC_QUEUE:
            queued = _LEDGER_SYNC_QUEUE.pop(0)
            _start_ledger_sync(queued["title"], queued["account"],
                               queued["translate_backfill"])
            return
        if n and config.AUTO_TRANSLATE_ENABLED and not _image_only_run(run.account):
            # Reserve the workbook writer before releasing the ledger slot.
            _trans_running = True
            threading.Thread(target=_run_auto_translate, kwargs={"claimed": True},
                             daemon=True).start()
    try:
        if "[trans-backfill]" in (run.title or "") and not _image_only_run(run.account):
            print(f"[trans-backfill] 已衔接翻译回填：{n} 条待译", flush=True)
    except Exception as e:
        print(f"[auto-translate] 启动失败: {e}", flush=True)


# ── 本地 LLM 自动翻译（run 钩子触发；2026-10-16）────────────────────────
_trans_running = False


def _run_auto_translate(claimed: bool = False) -> None:
    """执行 auto_translate.py：扫台账待译 → LLM 翻译 → 写 N 列。串行防并发。

    ★ R1：翻译也写同一本台账（N 列），所以同样要占 `ledger` 租约；
      拿不到就跳过本次（巡检/下一轮会再触发），绝不与修复并发写工作簿。
    """
    global _trans_running
    if not claimed:
        with _LEDGER_SYNC_LOCK:
            if _LEDGER_SYNC_ACTIVE or _trans_running:
                print("[auto-translate] 已有台账写入或翻译在跑，跳过本次触发", flush=True)
                return
            _trans_running = True
    try:
        lease = runner.RESOURCES.acquire((runner.LEDGER,), kind="translate",
                                         title="自动翻译回填")
    except runner.ResourceBusy as e:
        with _LEDGER_SYNC_LOCK:
            _trans_running = False
        print(f"[auto-translate] 台账写入被占用，跳过本次触发：{e}", flush=True)
        return
    try:
        script = config.SCRIPTS_DIR / "auto_translate.py"
        if not script.exists():
            print(f"[auto-translate] 缺少脚本 {script}", flush=True)
            return
        print("[auto-translate] 触发本地 LLM 翻译 …", flush=True)
        cmd = [config.python_exe(), str(script)]
        if config.AUTO_TRANSLATE_MODEL:
            cmd += ["--model", config.AUTO_TRANSLATE_MODEL]
        proc = subprocess.run(cmd,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        tail = "\n".join([l for l in (proc.stdout or "").strip().splitlines() if l.strip()][-2:])
        if proc.returncode == 0:
            print(f"[auto-translate] 完成: {tail}", flush=True)
            try:
                _refresh_pending_translation()   # 翻译后剩余待译刷新（多为 0）
            except Exception:
                pass
        else:
            print(f"[auto-translate] 失败 rc={proc.returncode}: "
                  f"{tail or (proc.stderr or '').strip()[-200:]}", flush=True)
    except Exception as e:
        print(f"[auto-translate] 异常: {type(e).__name__}: {e}", flush=True)
    finally:
        runner.RESOURCES.release(lease)
        with _LEDGER_SYNC_LOCK:
            _trans_running = False
            if _LEDGER_SYNC_QUEUE and not _LEDGER_SYNC_ACTIVE:
                queued = _LEDGER_SYNC_QUEUE.pop(0)
                _start_ledger_sync(queued["title"], queued["account"],
                                   queued["translate_backfill"])


runner.MANAGER.add_finish_hook(_on_ledger_sync_done)


def _account_in_file(name: str) -> bool:
    """accounts.json 里是否真的有这个账号（停用账号算「存在」）。

    ★ P1-2（2026-11-01）：`config.validate_account_name` 只做正则/长度校验，不查文件，
      于是格式合法的假账号（`acc_not_exist_zz`）能真起子进程、还在 runs.jsonl 落一条
      记录（体检活 SSE 抓到子进程 stderr + exit_code=2）。存在性必须单独查。
      这里用 `load_accounts(include_inactive=True)`（不过滤 active）：停用账号只是退出
      定时链，产物索引和手动补跑都还在，必须继续放行。
    """
    try:
        rows = config.load_accounts(include_inactive=True).get("accounts") or []
    except Exception:
        rows = []
    return any(isinstance(a, dict) and str(a.get("account") or "").strip() == name
               for a in rows)


def _require_accounts_exist(accounts) -> None:
    """账号必须存在于 accounts.json，否则 400「账号不存在：<name>」。

    必须在 `runner.MANAGER.start` / `scheduler.trigger` **之前**调用：不起进程、
    不写运行记录。空列表（= 全部账号，如 daily 不带 accounts）一律放行，保持原行为。
    """
    if isinstance(accounts, str):
        names = [accounts.strip()]
    elif isinstance(accounts, (list, tuple)):
        names = [str(a).strip() for a in accounts if a is not None and str(a).strip()]
    else:
        names = []
    missing = [n for n in dict.fromkeys(names) if not _account_in_file(n)]
    if missing:
        raise HTTPException(400, "账号不存在：" + "、".join(missing))


def _lease_owner(payload: dict) -> str:
    """取出「内部子任务要继承的台账租约」。

    ★ 2026-10-24（B）：修复会话持有台账写者租约，它派生的 daily_batch.py 会在子进程里
      再 POST /api/run。这个令牌由 runner 通过环境变量下传、daily_batch 原样带回，
      表示「我属于那个会话」，于是并入同一租约而不是被它自己挡住。
    只认**当前确实持有台账写权限**的令牌：伪造/过期一律 400，不会平白放开写者互斥。
    """
    owner = str(payload.get("lease_owner") or "").strip()
    if not owner:
        return ""
    if not runner.RESOURCES.owns(owner, (runner.LEDGER,)):
        raise HTTPException(400, "lease_owner 无效或已过期（该租约不再持有台账写权限）；"
                                 "内部子任务请勿手动指定")
    return owner


@app.post("/api/run")
def start_run(payload: dict) -> dict:
    kind = payload.get("kind", "pipeline")
    owner = _lease_owner(payload)
    if kind == "pipeline":
        # 显式名单优先于主下拉；单元素名单同样有效，避免执行未选择的账号。
        try:
            accounts = scheduler.validate_accounts(payload["accounts"] if "accounts" in payload
                                                   else [payload.get("account")])
            if not accounts:
                raise ValueError("至少选择一个账号")
        except ValueError as e:
            raise HTTPException(400, str(e))
        _require_accounts_exist(accounts)   # P1-2：假账号不许起进程（停用账号仍放行）
        payload = {**payload, "account": accounts[0]}
        if len(accounts) >= 2:
            cmd = _build_multi_pipeline_cmd(payload, accounts)
            run = runner.MANAGER.start(kind, _multi_title(payload, accounts), cmd,
                                        account=",".join(accounts), owner=owner)
        else:
            cmd = _build_pipeline_cmd(payload)
            title = f"流水线 · {payload.get('account')} · {payload.get('date_from')}~{payload.get('date_to')}"
            run = runner.MANAGER.start(kind, title, cmd, account=payload.get("account", ""),
                                       owner=owner)
        # 记录「翻译回填」开关，台账同步完成后据此立即衔接翻译（不等 15 分钟巡检）
        if payload.get("translate_backfill"):
            _TRANS_BACKFILL_RUNS[run.id] = True
        data = run.snapshot()
        data["agent_pending_note"] = AGENT_PENDING_NOTE
        return data
    if kind == "daily":
        try:
            accounts = scheduler.validate_accounts(payload.get("accounts"))
            if "accounts" in payload and not accounts:
                raise ValueError("至少选择一个账号")
            _require_accounts_exist(accounts)   # P1-2：kind=daily 同样校验
            return scheduler.trigger(accounts, reason="工作台", owner=owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if kind == "script":
        script = payload.get("script")
        if not script:
            raise HTTPException(400, "缺少 script")
        sp = (config.SCRIPTS_DIR / script) if not Path(script).is_absolute() else Path(script)
        if not sp.exists():
            raise HTTPException(404, f"脚本不存在: {sp}")
        args = payload.get("args") or []
        cmd = [config.python_exe(), str(sp), *[str(a) for a in args]]
        if payload.get("cookie") and config.COOKIE_FILE.exists():
            cmd += ["--cookie-file", str(config.COOKIE_FILE)]
        return runner.MANAGER.start("script", f"脚本 · {sp.name}", cmd).snapshot()
    raise HTTPException(400, f"未知 kind: {kind}")


@app.get("/api/runs")
def list_runs(limit: int = Query(50, ge=1, le=500)) -> dict:
    # 2026-11-02（P2-3）：limit 必须有边界。以前 `limit: int = 50` 毫无约束：
    # 负数会被 store.load_all 的 `out[-limit:]` 当成「除最后 N 条以外的全部」，
    # 501 也没有上限（前端只传 8/10）。越界现在由 FastAPI 统一回 422。
    active = {r["id"]: r for r in runner.MANAGER.all()}
    hist = store.load_all(limit=limit)[::-1]
    merged = []
    for h in hist:
        row = dict(h)
        if h["id"] in active:
            row.update(active.pop(h["id"]))
        merged.append(row)
    merged = list(active.values()) + merged
    return {"runs": merged[:limit]}


def _history_run(run_id: str) -> dict | None:
    """内存里没有这个 run 时，到 runs.jsonl 里按 id 找历史记录（只读，不改 store.py）。

    2026-11-02（P2-2）：重启后 `/api/runs` 列表里的历史 id 来自 journal，
    但详情 / SSE 只查内存 → 恒 404「运行不存在」，页面「最近运行」点进去就是报错。
    """
    try:
        rows = store.load_all(limit=10**6)
    except Exception:                                    # noqa: BLE001
        return None                                      # 坏行/IO 问题不该把 404 变成 500
    for row in reversed(rows):
        if isinstance(row, dict) and row.get("id") == run_id:
            return row
    return None


def _history_stream(row: dict):
    """历史运行没有日志可推：发一条 done 事件后结束（前端据此收尾，不再 404）。"""
    def gen():
        yield _sse({"status": row.get("status"), "exit_code": row.get("exit_code"),
                    "done": True})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, since: int = 0) -> dict:
    r = runner.MANAGER.get(run_id)
    if r:
        snap = r.snapshot()
        snap["lines"] = runner.MANAGER.lines(run_id, since)
        return snap
    row = _history_run(run_id)
    if row is None:
        raise HTTPException(404, "运行不存在")
    # 历史记录：字段与 /api/runs 列表里的同一行完全一致（同一个 journal 行），
    # 只是没有日志可回放。
    out = dict(row)
    out["lines"] = []
    return out


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: str, since: int = 0):
    r = runner.MANAGER.get(run_id)
    if not r:
        row = _history_run(run_id)
        if row is not None:
            return _history_stream(row)
        raise HTTPException(404, "运行不存在")

    def gen():
        q = runner.MANAGER.subscribe(run_id)
        try:
            for item in runner.MANAGER.lines(run_id, since):
                yield _sse({"line": item})
            yield _sse({"status": r.status, "exit_code": r.exit_code,
                        "done": r.status != "running"})
            if r.status != "running":
                return
            while True:
                try:
                    item = q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item.get("done"):
                    yield _sse({"status": item["status"], "exit_code": item["exit_code"], "done": True})
                    break
                yield _sse({"line": item})
        finally:
            runner.MANAGER.unsubscribe(run_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    # 2026-11-02（P2-4）：内存里没有这个 run 时，旧实现返回 200 {"stopped": false}，
    # 前端把「id 打错 / 记录已被清理」误读成「停止没成功」。现在直接 404。
    if runner.MANAGER.get(run_id) is None:
        raise HTTPException(404, "运行不存在")
    return {"stopped": runner.MANAGER.stop(run_id)}


# ────────────────────────── 台账与表格 ──────────────────────────
# ★ 2026-10-24（D1-A）：报表菜单已下线 → /api/reports、/api/reports/view、
#   /api/reports/json 三个路由一并删除（页面段也删了，前端不再有任何调用方）。
#   _aux_dir / _safe_path 保留：前者被账号视图用来定位 *_products.json，
#   后者被 /api/ledger/open 的路径守卫复用。
def _aux_dir() -> Path:
    lp = config.ledger_path()
    return lp.parent if lp else Path(r"D:/1/对标账号")


def _safe_path(raw: str, roots: list[Path]) -> Path:
    p = Path(raw).resolve()
    for root in roots:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise HTTPException(403, "路径越权")


# ── 单元格/表头工具 ────────────────────────────────────────────────
def _cell_str(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 and getattr(v, "second", 0) == 0:
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def _norm_header(s) -> str:
    """表头归一化：去首尾空格 + 全角空格 + 换行（G 列「视频出单量」实带前导空格）。"""
    return str(s or "").replace("\u3000", "").replace("\n", "").strip()


def _is_note(title: str) -> bool:
    return config.NOTE_SHEET_KEY in title


def _account_of(title: str) -> str:
    if title.startswith(config.SHEET_PREFIX):
        return title[len(config.SHEET_PREFIX):]
    return title.split("-", 1)[-1] if "-" in title else title


def _find_col(header: list, key: str) -> int:
    """按别名定位列索引：先 strip 后精确匹配，再退化为包含匹配。"""
    aliases = config.COL_ALIASES.get(key, [key])
    normed = [_norm_header(h) for h in header]
    for a in aliases:
        for i, h in enumerate(normed):
            if h == a:
                return i
    for a in aliases:
        for i, h in enumerate(normed):
            if a and a in h:
                return i
    return -1


def _hidden_cols(ws) -> set:
    """Excel 里被隐藏的列索引（0-based）。"""
    from openpyxl.utils import column_index_from_string
    out = set()
    try:
        for letter, dim in ws.column_dimensions.items():
            if getattr(dim, "hidden", False):
                try:
                    out.add(column_index_from_string(letter) - 1)
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _merged_fills(ws) -> dict:
    """合并区域 → 非左上角单元格的填充值（把合并区摊平，行判空才准）。"""
    fills = {}
    try:
        for rng in ws.merged_cells.ranges:
            min_col, min_row, max_col, max_row = rng.bounds
            tl = ws.cell(row=min_row, column=min_col).value
            for r in range(min_row, max_row + 1):
                for c in range(min_col, max_col + 1):
                    if (r, c) != (min_row, min_col):
                        fills[(r, c)] = tl
    except Exception:
        pass
    return fills


def _read_grid(ws, limit: int | None = None) -> dict:
    """读取 sheet：合并单元格摊平；返回全列网格（含隐藏列，由调用方裁剪）。"""
    fills = _merged_fills(ws)
    header: list = []
    rows: list[list] = []
    for r_i, row in enumerate(ws.iter_rows(), start=1):
        vals = []
        for cell in row:
            v = cell.value
            if v is None and (cell.row, cell.column) in fills:
                v = fills[(cell.row, cell.column)]
            vals.append(v)
        if r_i == 1:
            header = vals
            continue
        if all(v is None or str(v).strip() == "" for v in vals):
            continue
        rows.append(vals)
        if limit and len(rows) >= limit:
            break
    return {"header": header, "rows": rows, "hidden": _hidden_cols(ws)}


def _column_checks(header: list, media_type: str = "video") -> list[dict]:
    """列位自检：对照 pipeline 的真实读取方式，把隐患变成看得见的告警。"""
    from openpyxl.utils import get_column_letter
    checks: list[dict] = []

    # 1) daily_update._read_ledger_ids 的列定位（2026-10-09 起改为按表头定位，失败退回 F 列）
    link_idx = _find_col(header, "视频链接/ID")
    if link_idx == -1:
        checks.append({
            "level": "error",
            "msg": "未识别到「视频链接/ID」列；daily_update 会退回按第 6 列读取，新增判定可能失效",
        })
    elif link_idx != 5:
        checks.append({
            "level": "info",
            "msg": f"「视频链接/ID」位于第 {link_idx + 1} 列（{get_column_letter(link_idx + 1)}），"
                   f"现行规范为第 6 列（F）。daily_update 按表头定位，不受影响",
        })

    # 2) 与现行 15 列规范逐位比对（generate_benchmark_report.py 有 assert header == COLS）
    normed = [_norm_header(h) for h in header]
    while normed and not normed[-1]:
        normed.pop()
    exp = [_norm_header(c) for c in (config.IMAGE_EXPECTED_COLS if media_type == "image" else config.EXPECTED_COLS)]
    if len(normed) != len(exp):
        checks.append({"level": "warn",
                       "msg": f"列数 {len(normed)} ≠ 规范 {len(exp)} 列"})
    diffs = [f"第{i + 1}列 期望「{e}」实际「{a}」"
             for i, (e, a) in enumerate(zip(exp, normed)) if e != a]
    if diffs:
        checks.append({"level": "warn",
                       "msg": "列序与规范不符：" + "；".join(diffs[:6])
                              + ("…" if len(diffs) > 6 else "")})

    # 3) 表头首尾空格（脚本靠 strip 兼容，但仍提示）
    spaced = [f"第{i + 1}列 {h!r}" for i, h in enumerate(header)
              if isinstance(h, str) and h != h.strip()]
    if spaced:
        checks.append({"level": "info",
                       "msg": "表头含首尾空格（读取已自动 strip）：" + "、".join(spaced[:4])})
    return checks


def _sheet_stats(ws) -> dict:
    """单 sheet 统计：表头、有效数据行数、记录日期范围（合并/隐藏列已处理）。"""
    grid = _read_grid(ws)
    header = grid["header"]
    rows = grid["rows"]
    date_idx = _find_col(header, "记录日期")
    dmin = dmax = None
    if date_idx >= 0:
        for r in rows:
            if date_idx >= len(r):
                continue
            s = _cell_str(r[date_idx]).strip()
            if not s:
                continue
            if dmin is None or s < dmin:
                dmin = s
            if dmax is None or s > dmax:
                dmax = s
    hidden = grid["hidden"]
    media_type = "image" if getattr(ws, "title", "").startswith("图文") else "video"
    account_rows = {}
    if media_type == "image":
        for r in rows:
            name = str(r[1] or "").lstrip("@").strip() if len(r) > 1 else ""
            if name:
                account_rows[name] = account_rows.get(name, 0) + 1
    return {
        "media_type": media_type,
        "account_rows": account_rows,
        "header": [_norm_header(h) for h in header],
        "rows": len(rows),
        "cols": len(header),
        "visible_cols": len([i for i in range(len(header)) if i not in hidden]),
        "hidden_cols": sorted(hidden),
        "date_min": dmin,
        "date_max": dmax,
        "checks": [] if _is_note(getattr(ws, "title", "")) else _column_checks(header, media_type),
    }


def _ledger_file(file: str | None = None) -> Path:
    if file is not None:
        if not isinstance(file, str) or not file.strip():
            raise HTTPException(400, "台账文件名无效")
        lp = _safe_path(str(config.LEDGER_DIR / file), [config.LEDGER_DIR])
        if lp.suffix.lower() != ".xlsx":
            raise HTTPException(400, "仅支持 xlsx 台账")
    else:
        lp = config.ledger_path()
    if not lp or not lp.is_file():
        raise HTTPException(404, "台账文件不存在")
    return lp


def _open_ledger(file: str | None = None):
    """打开指定（或主）台账。返回 (workbook, path)。普通模式：可读隐藏列/合并区域。

    P2-1（2026-11-02）：台账被 Excel 独占打开时 openpyxl 抛 `PermissionError`，
    旧实现落到 `ledger_sheet` 的通用 `except Exception` → 裸 500。
    这里转成 423（Locked），前端可提示「关闭 Excel 后重试」。
    """
    import openpyxl
    lp = _ledger_file(file)
    try:
        return openpyxl.load_workbook(lp), lp
    except PermissionError:
        raise HTTPException(423, "台账被占用，请关闭 Excel 后重试")


def _ledger_overview_data(refresh: bool = False) -> dict:
    """台账目录总览数据：目录内全部 xlsx → 各 sheet 统计（60 秒缓存）。"""
    if not refresh and _ledger_cache["data"] and time.time() - _ledger_cache["ts"] < 60:
        return _ledger_cache["data"]
    try:
        import openpyxl
    except Exception as e:
        raise HTTPException(500, f"openpyxl 不可用: {e}")

    files = config.list_ledgers()
    out_files, sheets_all = [], []
    total_rows = 0
    for fp in files:
        info = {"name": fp.name, "path": str(fp),
                "size_kb": round(fp.stat().st_size / 1024, 1),
                "mtime": fp.stat().st_mtime, "sheets": [], "error": None}
        try:
            wb = openpyxl.load_workbook(fp)  # 普通模式：才拿得到隐藏列/合并区域
        except Exception as e:
            info["error"] = str(e)
            out_files.append(info)
            continue
        for ws in wb.worksheets:
            st = _sheet_stats(ws)
            title = ws.title
            entry = {"file": fp.name, "file_path": str(fp), "sheet": title,
                     "account": _account_of(title), "is_note": _is_note(title), **st}
            if st["media_type"] == "image" and len(st["account_rows"]) == 1:
                entry["account"] = next(iter(st["account_rows"]))
            info["sheets"].append(entry)
            sheets_all.append(entry)
            if not entry["is_note"]:
                total_rows += st["rows"]
        wb.close()
        out_files.append(info)

    data = {
        "dir": str(config.LEDGER_DIR),
        "dir_exists": config.LEDGER_DIR.exists(),
        "files": out_files,
        "sheets": sheets_all,
        "summary": {
            "files": len(files),
            "sheets": sum(len(f["sheets"]) for f in out_files),
            "rows": total_rows,
        },
        "ts": time.time(),
    }
    _ledger_cache.update(ts=time.time(), data=data)
    return data


_ledger_ids_cache: dict = {"ts": 0.0, "data": None}


def _ledger_id_sets(refresh: bool = False) -> dict:
    """{account: {video_id, ...}} —— 台账「视频链接/ID」列中已入账的视频 ID 集合。

    入账判定必须用「覆盖」而非「行数相等」：
      · 台账累积历史视频（行数 > 抓取条数）→ 按行数比会**误报**缺口；
      · 反之历史行数恰好接近抓取条数（gylwwcxb: 18 行 vs 16 条）→ 按行数比会**漏报**
        真正未入账的新视频。
    """
    if not refresh and _ledger_ids_cache["data"] and time.time() - _ledger_ids_cache["ts"] < 60:
        return _ledger_ids_cache["data"]
    out: dict = {}
    trans: dict = {}   # {account: set(video_id)} —— 台账 N 列「翻译」有值的视频 ID
    try:
        import openpyxl
        paths = [config.ledger_path(), config.image_ledger_path()]
        for lp in dict.fromkeys(p for p in paths if p is not None):
            wb = openpyxl.load_workbook(lp, read_only=True, data_only=True)
            try:
                for ws in wb.worksheets:
                    if _is_note(ws.title):
                        continue
                    image = ws.title.startswith("图文")
                    acc = _account_of(ws.title)
                    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
                    idx = next((i for i, h in enumerate(header) if h and "视频链接" in str(h)), 5)
                    tid = next((i for i, h in enumerate(header)
                                if h and str(h).strip() in ("翻译", "备注中文翻译")), 13)
                    if not image:
                        out.setdefault(acc, set())
                        trans.setdefault(acc, set())
                    for row in ws.iter_rows(min_row=2, max_col=max(idx, tid) + 1, values_only=True):
                        owner = str(row[1] or "").lstrip("@").strip() if image else acc
                        if not owner or idx >= len(row) or not row[idx]:
                            continue
                        m = re.search(r"(\d{15,25})", str(row[idx]))
                        if m:
                            out.setdefault(owner, set()).add(m.group(1))
                            if not image and tid < len(row) and str(row[tid] or "").strip():
                                trans.setdefault(owner, set()).add(m.group(1))
            finally:
                wb.close()
    except Exception:
        return out
    _ledger_ids_cache.update(ts=time.time(),
                             data={**out, "__translation__": trans})
    return {**out, "__translation__": trans}


def _ledger_row_counts(refresh: bool = False) -> dict:
    """{account: 台账数据行数}，供流水线卡片做「入账」段对账。

    refresh 需透传：台账总览有 60s 缓存，若不透传，新建 sheet 后
    `/api/overview?refresh=true` 仍会读到旧缓存（台账列显示 —），误导用户。
    """
    out: dict = {}
    try:
        data = _ledger_overview_data(refresh)
    except Exception:
        return out
    for s in data.get("sheets", []):
        if s.get("is_note"):
            continue
        if s.get("media_type") == "image":
            for name, rows in s.get("account_rows", {}).items():
                out[name] = out.get(name, 0) + rows
        else:
            out.setdefault(s["account"], s["rows"])
    return out


@app.get("/api/ledger/overview")
def ledger_overview(refresh: bool = False) -> dict:
    """台账目录总览：目录内全部 xlsx → 各 sheet 统计。"""
    return _ledger_overview_data(refresh)


@app.get("/api/ledger")
def ledger_info() -> dict:
    """台账目录内**全部** xlsx 与 sheet 清单。

    P2-8（2026-11-02）：旧实现只打开「视频主簿」一本（`config.ledger_path()`），
    `sheets` 只有视频表（实测 50 张），但同一响应的 `files` 却列了两本（含图文本），
    两处自相矛盾。现在 `files` 与 `sheets` 来自**同一次** `config.list_ledgers()`
    枚举，sheet 总数与 `/api/ledger/overview` 一致。
    """
    files = config.list_ledgers()
    if not files:
        return {"exists": False, "dir": str(config.LEDGER_DIR), "path": None,
                "files": [], "sheets": []}
    try:
        import openpyxl
    except Exception as e:
        return {"exists": False, "dir": str(config.LEDGER_DIR), "path": None,
                "error": str(e), "files": [p.name for p in files], "sheets": []}
    main = config.ledger_path() or files[0]
    sheets: list[dict] = []
    errors: dict[str, str] = {}
    for fp in files:
        try:
            wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
        except Exception as e:
            errors[fp.name] = str(e)      # 单本读不了不影响其它本：files 仍列全
            continue
        try:
            for ws in wb.worksheets:
                sheets.append({"name": ws.title, "file": fp.name,
                               "rows": ws.max_row, "cols": ws.max_column})
        finally:
            wb.close()
    return {"exists": True, "dir": str(config.LEDGER_DIR), "path": str(main),
            "size_kb": round(main.stat().st_size / 1024, 1),
            "files": [p.name for p in files], "sheets": sheets, "errors": errors}


def _ledger_account_media_type(account: str) -> str:
    """accounts.json 里该账号的 media_type；缺字段、读不到文件一律按 video。

    P1-1（2026-11-01）：视频主簿里残留了图文账号的同名 `对标记录-<账号>` 旧表，
    所以「精确 sheet 名」和「图文表逐行命中」谁优先，必须先看账号口径。
    """
    try:
        rows = config.load_accounts_raw().get("accounts") or []
    except Exception:
        return "video"
    for a in rows:
        if isinstance(a, dict) and str(a.get("account") or "").strip() == account:
            return str(a.get("media_type") or "video").strip().lower()
    return "video"


@app.get("/api/ledger/resolve")
def ledger_resolve(account: str = Query(...)) -> dict:
    """按账号名在台账目录内定位 sheet。

    判定顺序（2026-11-01 修正 P1-1）：
      ⓪ 账号 `media_type=image` 且图文表里逐行匹配到 `@account` → 优先返回图文表。
         视频主簿历史残留的同名 `对标记录-<账号>` 不能再抢走它
         （example_account_019 旧表 9 行 vs 真实图文 26 条；example_account_051 型没有旧表也走这条）。
      ① 精确 sheet 名 `对标记录-{account}` / `{account}`（视频主簿一账号一表）
         —— 视频账号按 ①→②；图文账号在图文表里没有行时也按 ① 兜底，不返回 None。
      ② 共用/图文表里逐行匹配 `@account`（表里真的存在这个账号的行）
      ③ 都没有 → 返回 sheet=None，让前端走已写好的「未找到 + 候选列表」分支。
      ④ P2-新增（2026-11-02）：账号**不在 accounts.json** → 404「账号不存在：<名>」，
         不再把目录内几十个真实 sheet 名当候选回吐（账号存在但没台账仍走 ③）。
    旧实现 `exact_hit or row_hit` 让精确名无条件优先；旧旧实现按 media_type 取共用
    图文表却不校验行，让 15 个图文账号落到 372 行的别人表上。
    """
    # P2-新增：先查账号是否存在。必须用 include_inactive（停用账号仍算存在），
    # 与 P1-2 的 `_account_in_file` 同一口径，避免两个入口各判一套。
    if not _account_in_file(str(account).strip()):
        raise HTTPException(404, f"账号不存在：{account}")
    files = config.list_ledgers()
    if not files:
        raise HTTPException(404, "台账目录内无 xlsx")
    try:
        import openpyxl
    except Exception as e:
        raise HTTPException(500, str(e))

    all_names: list[str] = []
    exact_hit: tuple | None = None
    row_hit: tuple | None = None
    for fp in files:
        try:
            wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
            names = [n for n in wb.sheetnames if "说明" not in n]
            if exact_hit is None:
                ex = [n for n in names if n == account or n == config.SHEET_PREFIX + account]
                if ex:
                    exact_hit = (ex[0], fp, names)
            if row_hit is None:
                for n in [x for x in names if x.startswith("图文")]:
                    if any(str(r[0] or "").lstrip("@").strip() == account
                           for r in wb[n].iter_rows(min_row=2, min_col=2, max_col=2,
                                                    values_only=True)):
                        row_hit = (n, fp, names)
                        break
            wb.close()
        except Exception:
            continue
        all_names += names

    # 图文账号：图文表里的逐行命中优先于视频主簿残留的同名精确 sheet；
    # 图文表里没有它的行时回退 exact_hit（兜底，不返回 None）。视频账号保持原顺序。
    is_image = _ledger_account_media_type(account) == "image"
    hit = (row_hit or exact_hit) if is_image else (exact_hit or row_hit)
    if hit:
        sheet, fp, names = hit
        return {"account": account, "sheet": sheet, "file": fp.name,
                "file_path": str(fp), "candidates": names}
    return {"account": account, "sheet": None, "file": None,
            "file_path": None, "candidates": sorted(set(all_names))}


@app.get("/api/ledger/sheet")
def ledger_sheet(name: str = Query(...), file: str | None = None,
                 limit: int = 5000, offset: int = 0) -> dict:
    try:
        wb, lp = _open_ledger(file)
    except HTTPException:
        raise
    try:
        if name not in wb.sheetnames:
            wb.close()
            raise HTTPException(404, f"sheet 不存在: {name}")
        ws = wb[name]
        grid = _read_grid(ws, limit=20000)  # 全量读取（2 万行上限防极端表）
        hidden = grid["hidden"]
        keep = [i for i in range(len(grid["header"])) if i not in hidden]
        header = [_cell_str(grid["header"][i]) for i in keep]
        all_rows = grid["rows"]
        total = len(all_rows)
        page = all_rows[offset:offset + limit]
        rows = [[_cell_str(r[i]) if i < len(r) else "" for i in keep] for r in page]
        checks = [] if _is_note(name) else _column_checks(grid["header"], "image" if name.startswith("图文") else "video")
        wb.close()
        return {"sheet": name, "file": lp.name, "header": header, "rows": rows,
                "offset": offset, "limit": limit, "total": total,
                "hidden_cols": sorted(hidden),
                "all_cols": [_cell_str(h) for h in grid["header"]],
                "checks": checks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ────────────────────────── 对标分析（第二版 · A 方案） ──────────────────────────
@app.get("/api/benchmark")
def benchmark_view(account: str | None = None, accounts: str | None = None,
                   refresh: bool = False) -> dict:
    """对标分析数据：账号→视频→爆款规律→可借鉴点（聚合 scrape+products+台账）。

    账号维度、视频排行、爆款规律、可借鉴点。数据源为本地真实产物，
    出单/GMV 如实标注「待接入」，不编造。

    accounts：逗号分隔的账号子集（组内对标入口用），与 account 二选一；
    传 accounts 时按子集聚合 KPI/patterns。
    """
    acc_list = list(dict.fromkeys(a.strip() for a in accounts.split(",") if a.strip())) if accounts is not None else None
    return benchmark.build(account=account, refresh=refresh, accounts=acc_list)


# 用本机 Excel 打开（零额外依赖：走 PowerShell COM）
_EXCEL_PS = r"""
$ErrorActionPreference = 'Stop'
$path  = $env:LEDGER_PATH
$sheet = $env:LEDGER_SHEET
$excel = $null
try { $excel = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application') } catch { }
if ($null -eq $excel) { $excel = New-Object -ComObject Excel.Application }
$excel.Visible = $true
try { $excel.DisplayAlerts = $false } catch { }
$wb = $null
foreach ($b in $excel.Workbooks) { if ($b.FullName -ieq $path) { $wb = $b; break } }
if ($null -eq $wb) { $wb = $excel.Workbooks.Open($path) }
if ($sheet -ne '') { try { $wb.Worksheets.Item($sheet).Activate() } catch { } }
try { $wb.Activate() } catch { }
Write-Output 'OK'
"""


def _open_in_excel(path: Path, sheet: str = "") -> dict:
    """唤起本机 Excel 打开台账并尽量激活指定 sheet；失败则退化为直接打开文件。"""
    env = {**os.environ, "LEDGER_PATH": str(path), "LEDGER_SHEET": sheet or ""}
    err = ""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _EXCEL_PS],
            env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and "OK" in (proc.stdout or ""):
            return {"ok": True, "method": "excel-com", "file": path.name, "sheet": sheet}
        err = ((proc.stderr or "") + (proc.stdout or ""))[-400:]
    except Exception as e:
        err = str(e)

    try:
        os.startfile(str(path))  # noqa: S606  降级：直接打开文件
        return {"ok": True, "method": "startfile", "file": path.name, "sheet": "",
                "note": "未能定位到指定 sheet，已直接打开整个文件。" + err[:160]}
    except Exception as e2:
        return {"ok": False, "error": f"{err[:200]} | {e2}"}


@app.post("/api/ledger/open")
def ledger_open(payload: dict, request: Request) -> dict:
    """用本机 Excel 打开台账（尽量定位到指定 sheet）。

    这是「在本机拉起一个进程」的动作，与账号写接口同级别的权限边界：
    只允许回环来源，避免服务被绑到 0.0.0.0 后被远端借刀开文件。
    """
    _guard_local_write(request)
    file = payload.get("file")
    sheet = payload.get("sheet") or ""
    lp = _ledger_file(file)
    return _open_in_excel(lp, sheet)


# ────────────────────────── 定时（已下线）──────────────────────────
# ★ 2026-10-24（D1-A）：定时自动运行菜单下线 → /api/schedule（GET/POST）与
#   /api/schedule/trigger 删除。scheduler 模块保留：/api/run?kind=daily 仍用它
#   校验账号名单，daily_batch/自动化也共用 build_daily_cmd。真正的日更入口是
#   pipeline/scripts/daily_batch.py（唯一回执契约）。


# ────────────────────────── 无人值守 · 待接管（方案五）──────────────────────────
_pipeline_status_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/pipeline/status")
def pipeline_status(force: bool = Query(False)) -> dict:
    """待接管队列：失败账号 + 原因 + 重试次数。15s 缓存，force=1 跳过。"""
    now = time.time()
    if not force and _pipeline_status_cache["data"] and now - _pipeline_status_cache["ts"] < 15:
        return _pipeline_status_cache["data"]
    data = pipeline_ops.status(now)
    _pipeline_status_cache.update({"ts": now, "data": data})
    return data


@app.post("/api/pipeline/retry")
def pipeline_retry(payload: dict | None = None) -> dict:
    """重跑失败账号（缺省 = 队列内全部）。

    ★ 2026-10-21 独立验收 P1-D：本端点必须走 `scripts/daily_batch.py`（唯一回执契约，
      子集补跑按账号并入当日整轮）。旧实现走 `daily_update.py`，一次重跑就会把未跑账号
      与 ledger_verify 失败一起覆写掉 —— 见 pipeline_ops.start_retry 的说明。
    """
    payload = payload or {}
    st = pipeline_ops.status()
    accounts = payload.get("accounts")
    scope = "accounts"
    if accounts is None:
        accounts = [x["account"] for x in st.get("takeover", [])]
    if not accounts:
        rc = st.get("receipt") or {}
        if st.get("alerts") or rc.get("stale"):
            # ★ 2026-10-28：队列为空、但回执仍停在「失败阶段/过期」时，「重跑全部」
            #   的真实语义是「重建回执」：重跑上一轮实际跑过的账号，按 partial 并入
            #   当日整轮并重写回执（拿不到上一轮账号时才退化成全量整轮）。
            #   旧实现直接 400「没有待接管账号」，于是面板自己让你点「重跑全部」，
            #   点下去却什么都不发生 —— 唯一能清掉红标的动作被它自己堵死了。
            accounts = list((st.get("last_round") or {}).get("accounts") or [])
            scope = "receipt"
        else:
            # 队列空、回执也正常：不做事，避免误触发全账号长跑
            raise HTTPException(400, "没有待接管账号")
    try:
        run = pipeline_ops.start_retry(accounts,
                                       retry=int(payload.get("retry", 2)),
                                       timeout=int(payload.get("timeout", 5400)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "run": run, "accounts": accounts, "scope": scope}


# ────────────────────────── 一键修复（2026-10-24）──────────────────────────
def _repair_plan() -> dict:
    """扫描可修复问题：待接管队列 + 每账号链路缺口 + 待译/回执。

    复用 /api/overview 的账号视图（已含 gaps/running/产物计数）与 /api/pipeline/status，
    不新增读盘路径；计划本身是纯推导，不写任何状态。
    """
    ov = overview(refresh=False)
    st = pipeline_ops.status()
    pending = int((ov.get("pending_translation") or {}).get("total") or 0)
    plan = repair.build_plan(ov.get("accounts") or [], st, pending)
    plan["busy_runs"] = [
        {"kind": r.get("kind"), "title": r.get("title")}
        for r in runner.MANAGER.running() if r.get("kind") in ("pipeline", "daily", "ledger")
    ]
    return plan


@app.get("/api/repair/scan")
def repair_scan() -> dict:
    """修复计划预览：将修几项、跳几项、各自原因。不执行任何动作。"""
    plan = _repair_plan()
    out = repair.public(plan)
    out["running"] = repair.is_running()
    out["ai"] = {"available": llm.available(), "enabled": llm.load_config().get("enabled"),
                 "default_model": llm.load_config().get("model") or ""}
    return out


@app.post("/api/repair/start")
def repair_start(payload: dict | None = None) -> dict:
    """一键修复：按计划顺序执行可修项，返回会话快照（前端随后轮询 status）。

    拒绝条件（400）：已有流水线/台账任务在跑（并发写台账）、已有修复会话、
    无任何可自动修复项。ids 可指定只修某些项（用于「重试失败项」）。
    """
    payload = payload or {}
    ids = payload.get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)):
        raise HTTPException(400, "ids 必须是字符串数组")
    plan = _repair_plan()
    if ids:
        keep = {str(x) for x in ids}
        plan = {**plan, "items": [it for it in plan["items"] if it.get("id") in keep]}
        if not plan["items"]:
            raise HTTPException(400, "指定的修复项已不存在，请重新扫描")
    use_ai = bool(payload.get("use_ai"))
    model = str(payload.get("model") or "").strip()
    if use_ai and not llm.available():
        # 明确告诉用户为什么用不上 AI，而不是静默降级成规则模式
        raise HTTPException(400, "当前没有可用的 AI 模型（检查 ~/.workbuddy/models.json "
                                 "或 WB_AI_BASE/WB_AI_KEY），请先取消「AI 诊断」或配置模型")
    try:
        return repair.start(plan, ids=ids,
                            days=int(payload.get("days", 7)),
                            retry=int(payload.get("retry", 1)),
                            timeout=int(payload["timeout"]) if payload.get("timeout") else None,
                            use_ai=use_ai, model=model)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/repair/status")
def repair_status() -> dict:
    """修复进度：status/total/done/percent/current/counts/items(+失败原因)。"""
    return repair.status()


@app.post("/api/repair/stop")
def repair_stop() -> dict:
    """中止修复：当前子进程立即终止，未执行项标「用户中止」。"""
    return repair.stop()


# ────────────────────────── AI 模型接口（2026-10-24）──────────────────────────
@app.get("/api/ai/models")
def ai_models() -> dict:
    """可选模型清单（key 一律掩码）。

    适配方式：三种 API 风格自动路由 —— openai（/chat/completions，覆盖 OpenAI /
    DeepSeek / Kimi / GLM / 通义 / 豆包 / MiniMax / OpenRouter / Groq / Ollama /
    vLLM 等兼容实现）、anthropic（/v1/messages）、gemini（generateContent）。
    """
    cfg = llm.load_config()
    return {
        "enabled": bool(cfg.get("enabled")),
        "available": llm.available(),
        "default": cfg.get("model") or "",
        "fallbacks": cfg.get("fallbacks") or [],
        "models": llm.list_models(),
        "actions": ai_repair.describe_actions(),
        "sources": {"models_json": str(llm.MODELS_JSON), "exists": llm.MODELS_JSON.exists(),
                    "config": str(llm.AI_CONFIG_JSON)},
    }


@app.get("/api/ai/config")
def ai_config_get() -> dict:
    """AI 配置（不含任何 key）。"""
    cfg = llm.load_config()
    cfg["available"] = llm.available()
    cfg["budget"] = ai_repair.budget_snapshot()
    return cfg


@app.post("/api/ai/config")
def ai_config_set(payload: dict | None = None) -> dict:
    """改配置：enabled / model / fallbacks / timeout_s / 预算 / proxy。

    白名单字段合并写入 data/ai_config.json，未知键忽略（不接受任意字段落盘）。
    """
    payload = payload or {}
    if "model" in payload and payload["model"]:
        if not llm.resolve(str(payload["model"])):
            raise HTTPException(400, f"模型不存在：{payload['model']}")
    if "purposes" in payload:
        raw = payload.get("purposes") or {}
        if not isinstance(raw, dict):
            raise HTTPException(400, "purposes 必须是对象（用途 → 模型 id）")
        clean: dict[str, str] = {}
        for k, v in raw.items():
            if k not in llm.PURPOSES:
                raise HTTPException(400, f"未知用途：{k}")
            mid = str(v or "").strip()
            if not mid:
                continue
            m = llm.resolve(mid)
            if not m:
                raise HTTPException(400, f"模型不存在：{mid}")
            if k == "ps_product_scan" and not m.get("supportsImages"):
                raise HTTPException(400, f"产品图识别只能绑定支持图片的模型：{mid}")
            clean[str(k)] = mid
        payload = {**payload, "purposes": clean}
    try:
        cfg = llm.save_config(payload)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"配置值非法：{e}")
    cfg["available"] = llm.available()
    return cfg


@app.post("/api/ai/test")
def ai_test(payload: dict | None = None) -> dict:
    """测试连接：真发一次极短请求，回报延迟、是否能用 JSON 模式、用的是哪个模型。

    ★ P2-6（2026-11-02）：显式传了不存在的模型时，以前会静默回退到默认/内置偏好模型
      （返回体 model 变成默认那个，用户以为自己测的是指定模型）。现在直接 400，且**不发出
      任何探测请求**；显式模型合法或不传模型（= 默认）时行为不变。
    """
    model = str((payload or {}).get("model") or "").strip()
    if model and llm.resolve(model) is None:
        raise HTTPException(400, f"模型不存在：{model}")
    return llm.probe(model or None)


@app.get("/api/ai/calls")
def ai_calls(limit: int = Query(20, ge=1, le=200)) -> dict:
    """最近的模型调用审计（只含元数据：模型/用途/耗时/token/成败/错误摘要）。"""
    path = llm.AI_CALLS_JSONL
    rows: list[dict] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            lines = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"total": len(rows), "calls": rows[::-1], "budget": ai_repair.budget_snapshot()}


# ──────────────────── AI 接入 · 模型清单管理（2026-10-28）────────────────────
# 背景：models.json 是 AI 接入的唯一真相源（apiKey 明文），以前加/删一把 key 只能手改
# 文件、还得自己保证 JSON 不写坏。这里只补「清单增删改」这一层，边界收在服务端：
#   · 只写 llm.MODELS_JSON 一个路径，不提供任意路径写入；
#   · 写前把旧文件备份到 data/backups/models-<时间戳>.json，最多留 20 份；
#   · 同目录 tmp + os.replace 原子替换，中途失败不会留下半个 JSON；
#   · 响应体永远不含 apiKey 原文，只给掩码（与 GET /api/ai/models 同口径）。
_MODELS_LOCK = threading.RLock()
_MODELS_BACKUP_KEEP = 20
_MODEL_WRITE_FIELDS = ("id", "name", "vendor", "url", "apiKey", "api_style",
                       "maxInputTokens", "maxOutputTokens", "keyUpdatedAt",
                       "supportsToolCall", "supportsImages", "supportsReasoning")


def _models_doc() -> dict:
    """读 models.json，保留容器形态：裸数组 或 {"models": [...]}（含其它顶层键）。"""
    path = llm.MODELS_JSON
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"shape": "list", "extra": {}, "items": []}
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, f"models.json 读取失败（{e}），请先手工修好该文件")
    if isinstance(raw, list):
        return {"shape": "list", "extra": {}, "items": [m for m in raw if isinstance(m, dict)]}
    if isinstance(raw, dict) and isinstance(raw.get("models"), list):
        extra = {k: v for k, v in raw.items() if k != "models"}
        return {"shape": "object", "extra": extra,
                "items": [m for m in raw["models"] if isinstance(m, dict)]}
    raise HTTPException(500, 'models.json 顶层必须是数组或 {"models": [...]}')


def _models_backup() -> str:
    """写前备份，返回备份路径（文件还不存在时返回空串）。"""
    path = llm.MODELS_JSON
    if not path.exists():
        return ""
    d = config.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = d / f"models-{stamp}.json"
    n = 1
    while dst.exists():
        dst = d / f"models-{stamp}-{n}.json"
        n += 1
    dst.write_bytes(path.read_bytes())
    olds = sorted(d.glob("models-*.json"))
    for old in olds[:-_MODELS_BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(dst)


def _models_write(doc: dict) -> None:
    """原子写回（保持容器形态与缩进风格，方便人工继续手改）。"""
    path = llm.MODELS_JSON
    body = doc["items"] if doc["shape"] == "list" else {**doc["extra"], "models": doc["items"]}
    text = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _model_public(m: dict, cfg: dict) -> dict:
    """管理页的行：key 只给掩码，另标是否缺 key / 本地 / 默认 / 在回退链里。"""
    mid = str(m.get("id") or "")
    key = str(m.get("apiKey") or "").strip()
    url = str(m.get("url") or "").strip()
    return {
        "id": mid,
        "name": str(m.get("name") or mid),
        "vendor": str(m.get("vendor") or ""),
        "url": url,
        "api_style": str(m.get("api_style") or ""),
        "key_masked": llm.mask_key(key),
        "has_key": bool(key),
        "local": any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker")),
        "supportsToolCall": bool(m.get("supportsToolCall")),
        "supportsImages": bool(m.get("supportsImages")),
        "supportsReasoning": bool(m.get("supportsReasoning")),
        "maxInputTokens": m.get("maxInputTokens"),
        "maxOutputTokens": m.get("maxOutputTokens"),
        "is_default": mid == (cfg.get("model") or ""),
        "in_fallbacks": mid in list(cfg.get("fallbacks") or []),
        "cooling_ms": llm.cooldown_ms(mid),
        "key_updated_at": m.get("keyUpdatedAt") or None,
    }


def _model_clean(payload: dict, base: dict | None) -> dict:
    """白名单字段 + 校验。apiKey 传空串/不传 = 沿用原 key（编辑时不必回填明文）。"""
    base = dict(base or {})

    def pick(k, default=""):
        return payload[k] if k in payload else base.get(k, default)

    mid = str(pick("id") or "").strip()
    if not mid:
        raise HTTPException(400, "模型 id 必填")
    if len(mid) > 64 or any(c in mid for c in "\r\n\t"):
        raise HTTPException(400, "模型 id 不能超过 64 字符，也不能含换行/制表符")
    url = str(pick("url") or "").strip()
    if not url:
        raise HTTPException(400, "接口地址 url 必填（例：https://api.deepseek.com/v1）")
    if not re.match(r"^https?://", url):
        raise HTTPException(400, f"url 必须以 http:// 或 https:// 开头：{url}")
    local = any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "host.docker"))
    key = str(payload.get("apiKey") or "").strip() or str(base.get("apiKey") or "").strip()
    if not key and not local:
        raise HTTPException(400, "apiKey 不能为空（本地 127.0.0.1 / localhost 推理服务除外）")
    style = str(pick("api_style") or "").strip()
    if style and style not in ("openai", "anthropic", "gemini"):
        raise HTTPException(400, f"api_style 只能是 openai / anthropic / gemini：{style}")
    out = {"id": mid,
           "name": str(pick("name") or mid).strip() or mid,
           "vendor": str(pick("vendor") or "").strip(),
           "url": url,
           "apiKey": key}
    if style:
        out["api_style"] = style
    for k in ("maxInputTokens", "maxOutputTokens"):
        v = pick(k, None)
        if v in (None, ""):
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} 必须是正整数")
        if iv <= 0:
            raise HTTPException(400, f"{k} 必须大于 0")
        out[k] = iv
    for k in ("supportsToolCall", "supportsImages", "supportsReasoning"):
        v = pick(k, None)
        if v is not None:
            out[k] = bool(v)
    # 换 key 时记一笔时间（只在这次真的提交了非空 apiKey 时刷新）
    if str(payload.get("apiKey") or "").strip():
        out["keyUpdatedAt"] = int(time.time())
    elif base.get("keyUpdatedAt"):
        out["keyUpdatedAt"] = base["keyUpdatedAt"]
    return {k: out[k] for k in _MODEL_WRITE_FIELDS if k in out}


@app.get("/api/ai/models/admin")
def ai_models_admin() -> dict:
    """管理页数据：全部条目（含缺 key 的）+ 默认/回退 + 备份清单。key 只有掩码。"""
    cfg = llm.load_config()
    doc = _models_doc()
    backups = []
    bdir = config.DATA_DIR / "backups"
    if bdir.exists():
        for p in sorted(bdir.glob("models-*.json"))[-5:]:
            try:
                st = p.stat()
                backups.append({"name": p.name, "ts": st.st_mtime, "bytes": st.st_size})
            except OSError:
                pass
    return {"models": [_model_public(m, cfg) for m in doc["items"]],
            "default": cfg.get("model") or "",
            "fallbacks": list(cfg.get("fallbacks") or []),
            "purposes": dict(cfg.get("purposes") or {}),
            "purposes_supported": True,
            "restore_supported": True,
            "enabled": bool(cfg.get("enabled")),
            "available": llm.available(),
            "path": str(llm.MODELS_JSON),
            "backups": backups,
            "env_override": bool(os.environ.get("WB_AI_KEY"))}


@app.post("/api/ai/models")
def ai_models_save(request: Request, payload: dict | None = None) -> dict:
    """新增或更新一个模型条目（同 id = 更新）。apiKey 留空表示沿用原 key。"""
    _guard_local_write(request)
    payload = payload or {}
    mid = str(payload.get("id") or "").strip()
    with _MODELS_LOCK:
        doc = _models_doc()
        idx = next((i for i, m in enumerate(doc["items"])
                    if str(m.get("id") or "") == mid), -1)
        item = _model_clean(payload, doc["items"][idx] if idx >= 0 else None)
        if idx < 0:
            doc["items"].append(item)
        else:
            doc["items"][idx] = item
        _models_backup()
        _models_write(doc)
    cfg = llm.load_config()
    return {"ok": True, "created": idx < 0, "model": _model_public(item, cfg),
            "models": [_model_public(m, cfg) for m in doc["items"]]}


@app.delete("/api/ai/models/{model_id}")
def ai_models_delete(model_id: str, request: Request) -> dict:
    """删除条目，并把默认模型/回退链里对它的引用一并清掉（避免指向不存在的模型）。"""
    _guard_local_write(request)
    mid = str(model_id or "").strip()
    with _MODELS_LOCK:
        doc = _models_doc()
        keep = [m for m in doc["items"] if str(m.get("id") or "") != mid]
        if len(keep) == len(doc["items"]):
            raise HTTPException(404, f"模型不存在：{mid}")
        if not keep:
            raise HTTPException(400, "至少保留 1 个模型，否则 AI 诊断/反推会整体不可用")
        doc["items"] = keep
        _models_backup()
        _models_write(doc)
    cfg = llm.load_config()
    patch = {}
    if (cfg.get("model") or "") == mid:
        patch["model"] = ""
    fbs = [x for x in (cfg.get("fallbacks") or []) if x != mid]
    if fbs != list(cfg.get("fallbacks") or []):
        patch["fallbacks"] = fbs
    if patch:
        cfg = llm.save_config(patch)
    return {"ok": True, "deleted": mid,
            "default": cfg.get("model") or "",
            "fallbacks": list(cfg.get("fallbacks") or []),
            "models": [_model_public(m, cfg) for m in keep]}


@app.post("/api/ai/models/restore")
def ai_models_restore(request: Request, payload: dict | None = None) -> dict:
    """把 models.json 回滚到某份写前备份。

    安全边界与增删一致：只认 data/backups/ 下的 models-*.json 文件名，
    覆盖前先把当前版本再备份一份（所以这一步可逆），同样原子替换。
    """
    _guard_local_write(request)
    name = str((payload or {}).get("name") or "").strip()
    if (not name or name != os.path.basename(name)
            or not name.startswith("models-") or not name.endswith(".json")):
        raise HTTPException(400, f"备份文件名不合法：{name}")
    src = config.DATA_DIR / "backups" / name
    if not src.exists():
        raise HTTPException(404, f"备份不存在：{name}")
    with _MODELS_LOCK:
        raw = src.read_bytes()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(400, f"这份备份不是合法 JSON（{e}），不覆盖")
        items = parsed if isinstance(parsed, list) else (parsed or {}).get("models")
        if not isinstance(items, list) or not items:
            raise HTTPException(400, "这份备份里没有模型条目，拒绝用它覆盖当前清单")
        _models_backup()                    # 先备份当前，回滚可撤销
        path = llm.MODELS_JSON
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_bytes(raw)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    doc = _models_doc()
    return {"ok": True, "restored": name, "count": len(doc["items"]),
            "models": [_model_public(m, llm.load_config()) for m in doc["items"]]}


# ──────────────── 提示词优化 · 产品图识别（2026-10-26）───────────────
# 前端上传 1–4 张产品图 → 视觉模型识别 → 结构化产品档案。
# 设计约束：
#   · 提示词固定在服务端，不收前端 prompt（避免这个端点被当成通用 chat 用）；
#   · AI 不可用 / 超预算 / 识别失败一律返回 degraded（HTTP 200），前端降级为手填；
#   · 同一组图 + 同一模型命中缓存，不重复烧 token；
#   · 图片只在本进程内存里 decode，不落盘（缓存只存识别结果）。
PS_SCAN_CACHE = Path(__file__).resolve().parent / "data" / "ps_scan_cache.json"
PS_SCAN_LIMITS = {"images": 4, "b64": 6 * 1024 * 1024, "cache": 300}
_ps_scan_lock = threading.Lock()

_PS_SCAN_PROMPT = (
    "你是德国 TikTok 电商短视频的选品与拍摄顾问。看这些图，只输出一个 JSON 对象，键固定：\n"
    '{"subject":"画面主体，一句中文，≤30 字",'
    '"category":"品类，如 女士运动休闲套装",'
    '"material":"可见材质，看不见填 未知",'
    '"colors":["最多 3 个可见颜色，中文"],'
    '"scene":"拍摄场景，一句中文",'
    '"selling":["3 条能从图中直接验证的卖点，不夸大"],'
    '"audience":"目标人群，一句中文",'
    '"shoot":["2 条针对该品类的拍摄建议"]}\n'
    "硬性要求：不得出现德语绝对化用语（beste / garantiert / 100% / Nr.1）；"
    "信息不足的键填 \"未知\" 或 []，不要编造。\n"
    "**只输出这一个 JSON 对象：不要写思考过程、不要解释、不要复述要求，第一个字符必须是 {**。"
)


def _ps_vision_model(pref: str | None = None) -> str | None:
    """挑一个能看图的模型：显式指定 > 当前配置 > 清单里第一个 supports_images。"""
    def ok(mid: str) -> bool:
        m = llm.resolve(mid)
        return bool(m and (m.get("supportsImages") or m.get("supports_images")))

    if pref and ok(str(pref)):
        return str(pref)
    cur = str(llm.load_config().get("model") or "")
    if cur and ok(cur):
        return cur
    for m in llm.list_models():
        if m.get("supports_images") and ok(str(m.get("id") or "")):
            return str(m.get("id"))
    return None


def _ps_cache_read() -> dict:
    if not PS_SCAN_CACHE.exists():
        return {}
    try:
        data = json.loads(PS_SCAN_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _ps_cache_write(key: str, product: dict, model: str) -> None:
    with _ps_scan_lock:
        data = _ps_cache_read()
        data[key] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "product": product}
        if len(data) > PS_SCAN_LIMITS["cache"]:                 # 超出按时间裁掉最旧
            for k in sorted(data, key=lambda x: str(data[x].get("at") or ""))[: len(data) - PS_SCAN_LIMITS["cache"]]:
                data.pop(k, None)
        try:
            PS_SCAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            PS_SCAN_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError:
            pass


@app.get("/api/ps/scan-status")
def ps_scan_status() -> dict:
    """产品图识别的可用性：前端据此决定「能识别」还是「只能手填」。"""
    rows = [m for m in llm.list_models() if m.get("supports_images")]
    return {
        "ok": True,
        "ai": llm.available(),
        "model": _ps_vision_model(None),
        "vision_models": [{"id": m.get("id"), "name": m.get("name")} for m in rows][:8],
        "limits": {"images": PS_SCAN_LIMITS["images"], "mb_per_image": round(PS_SCAN_LIMITS["b64"] / 1048576, 1)},
    }


@app.post("/api/ps/product-scan")
def ps_product_scan(payload: dict | None = None) -> dict:
    """看图识别产品 → {subject, category, material, colors, scene, selling, audience, shoot}。

    永远返回 200：失败时 ok=false + degraded=true + reason，前端据此降级，不打断流程。
    """
    payload = payload or {}
    raw_images = payload.get("images") or []
    hint = str(payload.get("hint") or "").strip()[:300]
    model_pref = str(payload.get("model") or "").strip() or None

    if not isinstance(raw_images, list) or not raw_images:
        return {"ok": False, "degraded": True, "reason": "没有收到图片"}
    if len(raw_images) > PS_SCAN_LIMITS["images"]:
        return {"ok": False, "degraded": True,
                "reason": f"最多 {PS_SCAN_LIMITS['images']} 张图，收到 {len(raw_images)} 张"}

    parts: list[dict] = []
    digests: list[str] = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data") or "").strip()
        mime = str(item.get("mime") or "image/jpeg").strip()
        if not data:
            continue
        if len(data) > PS_SCAN_LIMITS["b64"]:
            return {"ok": False, "degraded": True, "reason": "单张图片超过 6MB（base64 后），请压缩后再传"}
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        digests.append(hashlib.sha1(data.encode("utf-8")).hexdigest()[:16])
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    if not parts:
        return {"ok": False, "degraded": True, "reason": "图片数据为空"}

    model = _ps_vision_model(model_pref)
    if not model:
        return {"ok": False, "degraded": True, "reason": "没有可用的视觉模型（设置 → 模型里挑一个支持图片的）"}
    if not llm.available():
        return {"ok": False, "degraded": True, "reason": "AI 未启用（设置 → 模型）", "model": model}

    cache_key = hashlib.sha1(("|".join(digests) + "|" + model + "|" + hint).encode("utf-8")).hexdigest()
    cached = _ps_cache_read().get(cache_key)
    if cached and isinstance(cached.get("product"), dict):
        return {"ok": True, "cached": True, "model": cached.get("model") or model,
                "product": cached["product"], "latency_ms": 0}

    text_block = _PS_SCAN_PROMPT + (f"\n补充信息（用户填写，优先采信）：{hint}" if hint else "")
    messages = [{"role": "user", "content": [{"type": "text", "text": text_block}] + parts}]
    t0 = time.time()
    try:
        out = llm.chat_json(messages, model, max_tokens=1800,
                            purpose="ps_product_scan", retries=1)
        product = out.get("data") or {}
    except llm.LLMError as exc:
        return {"ok": False, "degraded": True, "reason": f"识别失败：{exc}", "model": model}
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "degraded": True, "reason": f"识别异常：{type(exc).__name__}", "model": model}

    if not isinstance(product, dict) or not product:
        return {"ok": False, "degraded": True, "reason": "模型没有返回 JSON 对象", "model": model}

    clean: dict = {}
    for k in ("subject", "category", "material", "scene", "audience"):
        v = product.get(k)
        clean[k] = str(v).strip()[:120] if isinstance(v, (str, int, float)) else ""
    for k in ("colors", "selling", "shoot"):
        v = product.get(k)
        clean[k] = [str(x).strip()[:80] for x in v if str(x).strip()][:4] if isinstance(v, list) else []

    _ps_cache_write(cache_key, clean, str(out.get("model") or model))
    return {"ok": True, "cached": False, "model": out.get("model") or model,
            "latency_ms": int((time.time() - t0) * 1000), "product": clean,
            "usage": out.get("usage")}



# ────────────────────────── 爆品监控 ──────────────────────────
HOT_DIR = config.OUTPUT_DIR / "hot_monitor"


# ────────────────────────── 对标账号分组 ──────────────────────────
@app.get("/api/groups")
def groups_list() -> dict:
    """分组清单（含成员与计数）。内置「默认分组」不可删改；成员不在 members 里的账号视为默认组。"""
    return account_groups.list_groups()


def _key_msg(e: KeyError) -> str:
    """KeyError 的 404 文案：取原始消息，去掉 str(KeyError) 自动加的首尾单引号。

    ★ P2-5（2026-11-02）：分组/分类写接口把 `str(KeyError)` 直接当 detail，KeyError 的
      repr 自带单引号，客户端看到 `'大分类不存在：x'`（体检实测 DELETE /api/categories/
      16262b7e43 → {"detail":"'大分类不存在：16262b7e43'"}）。只影响文案，不改状态码。
    """
    args = getattr(e, "args", ())
    return str(args[0]) if args else str(e)


@app.post("/api/groups")
def groups_create(payload: dict) -> dict:
    """新建分组；可选 category_id 指定所属大分类（缺省 = 未分类）。"""
    payload = payload or {}
    try:
        g = account_groups.create_group(payload.get("name", ""), payload.get("category_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, "group": g}


@app.patch("/api/groups/{gid}")
def groups_update(gid: str, payload: dict) -> dict:
    """改分组：{"name": ...} 改名，{"category_id": ...} 换大分类，可同时传。"""
    payload = payload or {}
    if "name" not in payload and "category_id" not in payload:
        raise HTTPException(400, "需要 name 或 category_id")
    try:
        g = account_groups.update_group(gid, name=payload.get("name"),
                                        category_id=payload.get("category_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, "group": g}


# 兼容旧函数名（测试与外部脚本按名字直调）
groups_rename = groups_update


@app.patch("/api/groups/{gid}/category")
def groups_set_category(gid: str, payload: dict) -> dict:
    """把分组整体挪到另一个大分类（组内账号随组迁移）。"""
    cid = (payload or {}).get("category_id")
    if not cid:
        raise HTTPException(400, "缺少 category_id")
    try:
        g = account_groups.set_group_category(gid, cid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, "group": g}


# ── 大分类（顶级分类）──────────────────────────────────────────────
@app.get("/api/categories")
def categories_list() -> dict:
    """大分类清单（含分组数 / 账号数）。层级：大分类 ← 分组 ← 账号。"""
    d = account_groups.list_groups()
    return {"ok": True, "categories": d["categories"]}


@app.post("/api/categories")
def categories_create(payload: dict) -> dict:
    payload = payload or {}
    try:
        c = account_groups.create_category(payload.get("name", ""), payload.get("desc", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "category": c}


@app.patch("/api/categories/{cid}")
def categories_update(cid: str, payload: dict) -> dict:
    payload = payload or {}
    if "name" not in payload and "desc" not in payload:
        raise HTTPException(400, "需要 name 或 desc")
    try:
        c = account_groups.update_category(cid, name=payload.get("name"),
                                           desc=payload.get("desc"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, "category": c}


@app.delete("/api/categories/{cid}")
def categories_delete(cid: str) -> dict:
    """删除大分类：内置不可删；其下还有分组时拒绝（需先把分组挪走）。"""
    try:
        r = account_groups.delete_category(cid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, **r}


@app.delete("/api/groups/{gid}")
def groups_delete(gid: str) -> dict:
    """删除分组：成员自动回「默认分组」，账号本身不会被删除。"""
    try:
        r = account_groups.delete_group(gid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, **r}


@app.post("/api/groups/{gid}/members")
def groups_add_member(gid: str, payload: dict) -> dict:
    try:
        r = account_groups.add_member(gid, (payload or {}).get("account", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, **r}


@app.post("/api/groups/{gid}/transfer")
def groups_transfer(gid: str, payload: dict) -> dict:
    """批量迁移账号到目标分组（2026-10-19）。

    入参：{"accounts": ["a", "b"]}；逐条返回 moved/same/not_found，不因单条失败中断。
    典型用法：台账卡片勾选多个账号 → 选目标分组 → 一次迁移。
    """
    accounts = (payload or {}).get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise HTTPException(400, "accounts 必须是非空数组")
    try:
        r = account_groups.transfer_members(gid, accounts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, **r}


@app.delete("/api/groups/{gid}/members/{account}")
def groups_remove_member(gid: str, account: str) -> dict:
    """移出分组 = 回到默认分组（账号保留）。"""
    try:
        r = account_groups.remove_member(gid, account)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(404, _key_msg(e))
    return {"ok": True, **r}


def _group_metrics_for(gid: str) -> dict:
    """组内账号指标聚合 + 健康度（方案 B）。数据源：本地 scrape_{acc}.json，无数据如实标注。

    指标（每账号）：视频数 / 总播放 / 均播放 / 爆款数(播放≥1万) / 7 天更新数 / 挂车率 / 健康标签。
    组级：账号数、空组、低价值账号数（爆款=0 且 ≥7 天没更新）、未更新账号（>7 天无新视频）。
    """
    import datetime as _dt
    g = next((x for x in account_groups.list_groups().get("groups", [])
              if x.get("id") == gid), None)
    if g is None:
        raise HTTPException(404, "分组不存在")
    members = [m["account"] for m in (g.get("members") or [])]
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).replace(tzinfo=None)
    cutoff = now - _dt.timedelta(days=7)
    accounts = []
    for acc in members:
        p = config.OUTPUT_DIR / f"scrape_{acc}.json"
        data = config.read_json(p, None) or {}
        raw_items = data.get("items", []) if isinstance(data, dict) else []
        items, seen = [], set()
        for it in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(it, dict):
                continue
            vid = str(it.get("id") or it.get("video_id") or "")
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            items.append(it)
        stats_list = [it.get("stats") if isinstance(it.get("stats"), dict) else {} for it in items]
        plays = [benchmark._num(s.get("playCount")) for s in stats_list]
        total_plays = sum(plays)
        n = len(items)
        viral = sum(1 for x in plays if x >= 10000)
        pubs = []
        for it in items:
            try:
                d = _dt.datetime.strptime(it.get("publish_time_gmt8"), "%Y-%m-%d %H:%M:%S")
                if d <= now:
                    pubs.append(d)
            except (ValueError, TypeError):
                continue
        recent = sum(cutoff <= d <= now for d in pubs)
        last = max(pubs) if pubs else None
        last_pub = last.strftime("%Y-%m-%d %H:%M:%S") if last else ""
        stale_days = (now - last).days if last else None
        # 挂车率
        products = benchmark._load_products_map(acc)
        prod_n = sum(bool(products.get(str(it.get("id") or it.get("video_id") or ""))) for it in items)
        coverage = round(prod_n / n * 100, 1) if n else 0
        # 健康标签
        tags = []
        if n == 0:
            tags.append("无数据")
        if viral == 0:
            tags.append("无爆款")
        if stale_days is not None and stale_days > 7:
            tags.append(f"{stale_days}天未更新")
        elif stale_days == 0:
            tags.append("今日更新")
        accounts.append({
            "account": acc, "videos": n, "total_plays": total_plays,
            "avg_plays": round(total_plays / n) if n else 0,
            "viral": viral, "recent7d": recent,
            "last_pub": last_pub, "stale_days": stale_days,
            "product_coverage": coverage, "tags": tags,
        })
    low_value = [a["account"] for a in accounts
                 if a["videos"] > 0 and a["viral"] == 0 and (a["stale_days"] or 0) > 7]
    stale = [a["account"] for a in accounts if (a["stale_days"] or 0) > 7]
    return {"group": {"id": g["id"], "name": g.get("name"), "count": len(members)},
            "accounts": accounts,
            "low_value": low_value, "stale": stale,
            "empty": len(members) == 0}


@app.get("/api/groups/{gid}/metrics")
def groups_metrics(gid: str) -> dict:
    """组内账号指标对比 + 健康度提醒（读本地 scrape/products，不写任何数据）。"""
    return _group_metrics_for(gid)


# ★ 2026-10-24（D1-A）：爆品监控菜单下线 → /api/hot/latest 与 /api/hot/{action} 删除。
#   爆品脚本本身没删（scripts/hot_product_monitor.py），仍可由「自动化流程」的
#   kind=hot 条目执行；如需彻底下线，删 data/automations.json 里的 hot_snapshot。
HOT_DIR = config.OUTPUT_DIR / "hot_monitor"   # 保留常量：其他模块/排障脚本仍可能引用


# ────────────────────────── 自动化流程 ──────────────────────────
def _automation_run_cmd(flow: dict) -> list[str] | None:
    """把一条自动化流程映射为本地可执行命令。返回 None 表示该流程只能转交 Agent。"""
    kind = flow.get("kind")
    if kind == "daily":
        try:
            return scheduler.build_daily_cmd(flow.get("accounts"))
        except ValueError as e:
            raise HTTPException(400, str(e))
    if kind == "script":
        script = flow.get("script")
        if not script:
            return None
        sp = Path(script)
        if not sp.is_absolute():
            sp = (config.SCRIPTS_DIR / script).resolve()
        if not sp.exists():
            raise HTTPException(404, f"脚本不存在: {sp}")
        args = [str(a) for a in (flow.get("args") or [])]
        if flow.get("cookie") and config.COOKIE_FILE.exists():
            args += ["--cookie-file", str(config.COOKIE_FILE)]
        return [config.python_exe(), str(sp), *args]
    return None


def _bust_accounts_cache() -> None:
    """写操作后立即失效缓存，避免页面读到旧状态。"""
    _accounts_cache.update(ts=0.0, data=None)
    _overview_cache.update(ts=0.0, data=None)


# 账号字段 schema：供详情接口返回，前端据此渲染表单/校验（机器可读的自描述）
ACCOUNT_FIELD_SCHEMA = {
    "account": {"type": "string", "readonly": True, "required": True,
                "pattern": "^[a-z0-9_.]+$", "max_len_with_prefix": 31,
                "desc": "主键，同时是 Excel sheet 名后缀「对标记录-{account}」；创建后不可修改"},
    "region": {"type": "string",
               "enum": ["us", "uk", "eu", "de", "fr", "es", "it",
                        "cn", "cn-hk", "cn-tw", "jp", "kr", ""],
               "default": "",
               "desc": "账号面向地区；决定转写语言（做哪个地区就用该地区语言）。"
                       "us/uk→en，de→de，fr→fr，es→es，it→it，cn/cn-hk/cn-tw→zh。"
                       "未填 = 默认 en；新账号注册务必按实际面向地区补填。"},
    "lang": {"type": "string", "enum": ["en", "de", "es", "zh", "fr", "it", "jp", "kr"],
             "default": "en",
             "desc": "转写语言；未显式指定时按 region 自动推断（做哪个地区用该地区语言）。"
                     "中文口播账号须 zh（auto/en 会产生伪转写）。"},
    "nickname": {"type": "string", "default": "", "desc": "展示名，可空"},
    "tags": {"type": "array<string>", "default": [],
             "desc": "分类标签；入参也接受逗号分隔的字符串，服务端会拆分"},
    "note": {"type": "string", "default": "", "desc": "备注，可空"},
    "out_dir": {"type": "string", "default": "pipeline/output",
                "desc": "抓取产物目录；缺省随主流水线约定"},
    "audio_dir": {"type": "string", "default": "pipeline/output/{account}_audio",
                  "desc": "音频目录；缺省 output/{account}_audio"},
    "active": {"type": "boolean", "default": True,
               "desc": "是否参与流水线；false 时被 load_accounts / daily_update 过滤"},
}


# ── 账号写操作的最小权限边界（2026-10-19） ──────────────────────────
# 本工作台没有多用户体系：写接口只对本机回环开放，避免将来把 host 改成
# 0.0.0.0 后被局域网任意改 accounts.json。testclient 供自动化测试使用。
_LOCAL_WRITE_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_ACCOUNT_WRITE_LOCK = threading.Lock()   # 读-改-写串行，避免并发互相覆盖


def _guard_local_write(request: Request) -> None:
    host = (getattr(getattr(request, "client", None), "host", "") or "").lower()
    if host not in _LOCAL_WRITE_HOSTS:
        raise HTTPException(403, "写操作仅限本机访问（127.0.0.1）")
    # 跨站校验统一在 `_no_store_frontend` 中间件里做（见 _csrf_ok）：
    # 这里只判来源 IP，既覆盖所有写路由，也不要求直接调用本函数的单测去伪造 headers。


def _csrf_ok(request: Request) -> bool:
    """跨站写请求判定（R15）：Origin/Sec-Fetch-Site 都不可信时才拒绝。"""
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return False
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        from urllib.parse import urlsplit
        return (urlsplit(origin).hostname or "") in _LOCAL_WRITE_HOSTS
    return True


@app.post("/api/accounts")
def create_account(payload: dict, request: Request) -> dict:
    """新增账号。account 必填，其余可选（缺省按主流水线约定生成）。

    media_type 可选（video / image）：image 会额外打上「图文账号」标签，
    流水线据此跳过音频与转写，改走图文枚举。
    """
    _guard_local_write(request)
    payload = payload or {}
    name = payload.get("account")
    mt = str(payload.get("media_type") or "video").strip().lower()
    if mt not in ("video", "image"):
        raise HTTPException(400, "media_type 只能是 video / image")
    with _ACCOUNT_WRITE_LOCK:
        try:
            row = config.add_account(name, payload)
            if mt == "image":
                config.set_media_type([row.get("account")], "image")
                _ledger_cache.update(ts=0.0, data=None)
                # set_media_type 会补「图文账号」标签，返回体的 row 需重新读一次
                row = next((a for a in config.load_accounts_raw().get("accounts", [])
                            if isinstance(a, dict) and a.get("account") == row.get("account")), row)
                row = dict(row)
                row["media_type"] = "image"
        except ValueError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(500, f"写 accounts.json 失败: {e}")
    _bust_accounts_cache()
    return {"ok": True, "account": row}


@app.get("/api/accounts")
def list_accounts(q: str = "", status: str = "", media_type: str = "",
                  group: str = "") -> dict:
    """账号清单（只读）：配置字段 + 产物统计 + 健康等级 + 所属分组。

    统计直接复用 /api/overview 的 5s 缓存，不额外扫盘；筛选在后端做是为了
    让 counts 始终返回「全量」口径（筛选结果只影响 accounts 列表）。
    """
    cfg = config.load_accounts(include_inactive=True)
    rows = [a for a in cfg.get("accounts", []) if isinstance(a, dict)]
    ov = overview(refresh=False)
    stats = {}
    for a in (ov.get("accounts") or []) + (ov.get("idle_accounts") or []):
        if a.get("account"):
            stats[a["account"]] = a
    gmap = {}
    try:
        for g in account_groups.list_groups().get("groups", []):
            for m in g.get("members", []):
                gmap[m.get("account")] = {"group_id": g.get("id"),
                                          "group_name": g.get("name")}
    except Exception:
        gmap = {}

    items = []
    for a in rows:
        name = a.get("account")
        st = stats.get(name, {})
        grp = gmap.get(name, {})
        items.append({
            "account": name,
            "nickname": a.get("nickname") or "",
            "region": a.get("region") or "",
            "lang": a.get("lang") or "",
            "tags": a.get("tags") or [],
            "note": a.get("note") or "",
            "active": bool(a.get("active", True)),
            "media_type": "image" if a.get("media_type") == "image" else "video",
            "out_dir": a.get("out_dir") or "",
            "audio_dir": a.get("audio_dir") or "",
            "audio_count": st.get("audio_count", 0),
            "transcript_count": st.get("transcript_count", 0),
            "scrape_items": st.get("scrape_items", 0),
            "scrape_errors": st.get("scrape_errors", 0),
            "products_count": st.get("products_count", 0),
            "products_probed": st.get("products_probed", 0),   # ★ D4：已提取/探测条数
            "ledger_rows": st.get("ledger_rows", 0),
            "last_success_date": st.get("last_success_date"),
            "severity": st.get("severity", "ok"),
            "landing": st.get("landing", 0),
            "has_artifacts": bool(st.get("audio_count") or st.get("scrape_items")
                                  or st.get("ledger_rows")),
            "group_id": grp.get("group_id") or "",
            "group_name": grp.get("group_name") or "未分组",
        })

    counts = {
        "all": len(items),
        "active": sum(1 for x in items if x["active"]),
        "inactive": sum(1 for x in items if not x["active"]),
        "video": sum(1 for x in items if x["media_type"] == "video"),
        "image": sum(1 for x in items if x["media_type"] == "image"),
        "artifacts": sum(1 for x in items if x["has_artifacts"]),
    }

    kw = (q or "").strip().lower()
    st_filter = (status or "").strip().lower()
    mt_filter = (media_type or "").strip().lower()
    g_filter = (group or "").strip()
    out = []
    for x in items:
        if kw:
            hay = " ".join([x["account"] or "", x["nickname"] or "",
                            " ".join(x["tags"] or []), x["group_name"] or ""]).lower()
            if kw not in hay:
                continue
        if st_filter in ("active", "inactive") and \
                x["active"] != (st_filter == "active"):
            continue
        if mt_filter in ("video", "image") and x["media_type"] != mt_filter:
            continue
        if g_filter and x["group_id"] != g_filter:
            continue
        out.append(x)

    # 停用沉底 → 异常优先 → 账号名字典序
    sev = {"error": 0, "warn": 1, "ok": 2}
    out.sort(key=lambda x: (0 if x["active"] else 1,
                            sev.get(x["severity"], 3),
                            (x["account"] or "").lower()))
    return {"total": counts["all"], "shown": len(out), "counts": counts, "accounts": out}


# ── 账号详情 / 编辑 / 删除（2026-10-16 补齐：此前只有 POST，DELETE 缺失导致
#    测试账号无法清理、残留空壳拖垮全量台账同步） ──────────────────────
@app.post("/api/accounts/media-type")
def set_accounts_media_type(payload: dict, request: Request) -> dict:
    """批量设置账号类型：{"accounts": [...], "media_type": "image"|"video"}（2026-10-19）。

    用于把选中的视频账号导入为图文账号（或反向）。只改 accounts.json 的类型与标签，
    不迁移、不删除任何台账 sheet 与产物。
    """
    payload = payload or {}
    _guard_local_write(request)
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise HTTPException(400, "accounts 必须是非空数组")
    with _ACCOUNT_WRITE_LOCK:
        try:
            r = config.set_media_type(accounts, payload.get("media_type") or "image")
        except ValueError as e:
            raise HTTPException(400, str(e))
    _bust_accounts_cache()
    _ledger_cache.update(ts=0.0, data=None)      # 图文区块要按新类型重算
    return {"ok": True, **r}


@app.get("/api/accounts/{name}")
def account_detail(name: str) -> dict:
    """账号详情：视图统计 + 配置 + 可编辑字段 schema。"""
    cfg = config.load_accounts_raw()
    row = next((a for a in cfg.get("accounts", [])
                if a.get("account") == name), None)
    if row is None:
        raise HTTPException(404, f"账号不存在：{name}")
    state = config.read_json(config.STATE_FILE, {}) or {}
    try:
        view = _account_view(row, state, _ledger_row_counts(False), _ledger_id_sets(False))
    except Exception:
        view = {"account": name}
    out = dict(view)          # 以视图统计为基底
    out.update(row)           # 再叠加原始配置（顺序不可反，否则丢 scrape_items 等）
    out["editable_fields"] = list(config.EDITABLE_FIELDS)
    out["field_schema"] = ACCOUNT_FIELD_SCHEMA
    return out


@app.patch("/api/accounts/{name}")
def account_update(name: str, payload: dict, request: Request) -> dict:
    """改账号可编辑字段（主键 account 只读）；media_type 可一并改。

    media_type 走 config.set_media_type（会补/移「图文账号」标签并失效台账缓存），
    与批量接口保持同一套语义。
    """
    _guard_local_write(request)
    payload = dict(payload or {})
    mt = payload.pop("media_type", None)
    mt = str(mt).strip().lower() if mt is not None else None
    if mt is not None and mt not in ("video", "image"):
        raise HTTPException(400, "media_type 只能是 video / image")
    with _ACCOUNT_WRITE_LOCK:
        try:
            row = config.update_account(name, payload)
            if mt is not None:
                config.set_media_type([name], mt)
                _ledger_cache.update(ts=0.0, data=None)
                # set_media_type 会重写文件（补/移「图文账号」标签），上面拿到的 row 已过期
                row = next((a for a in config.load_accounts_raw().get("accounts", [])
                            if isinstance(a, dict) and a.get("account") == name), row)
        except ValueError as e:
            raise HTTPException(404 if "不存在" in str(e) else 400, str(e))
    _bust_accounts_cache()
    row = dict(row)
    row["media_type"] = (mt or ("image" if row.get("media_type") == "image" else "video"))
    return {"ok": True, "account": row}


@app.delete("/api/accounts/{name}")
def account_delete(name: str, request: Request) -> dict:
    """删除账号（仅限无任何产物的空壳；有产物须改用「停用」）。"""
    _guard_local_write(request)
    with _ACCOUNT_WRITE_LOCK:
        try:
            config.remove_account(name)
        except ValueError as e:
            raise HTTPException(404 if "不存在" in str(e) else 400, str(e))
    _bust_accounts_cache()
    # 顺带清掉分组里残留的历史成员记录：否则重新创建同名账号会"凭空回到旧组"
    group_cleaned = False
    try:
        group_cleaned = account_groups.forget_account(name)
    except Exception as e:  # 分组文件异常不应让"已删成功"变失败
        print(f"[groups] 清理 {name} 的分组记录失败：{e}", flush=True)
    return {"ok": True, "deleted": name, "group_cleaned": group_cleaned}


@app.get("/api/automations")
def list_automations() -> dict:
    items = []
    for f in config.load_automations():
        row = dict(f)
        row["runnable"] = (f.get("kind") in {"daily", "script", "hot", "schedule"})
        items.append(row)
    return {"items": items, "workbuddy_home": str(config.WORKBUDDY_HOME)}


@app.post("/api/automations/{flow_id}/run")
def run_automation(flow_id: str, payload: dict | None = None) -> dict:
    flow = next((f for f in config.load_automations() if f.get("id") == flow_id), None)
    if not flow:
        raise HTTPException(404, f"未知自动化流程: {flow_id}")
    payload = payload or {}
    accounts = payload.get("accounts", flow.get("accounts"))
    # P1-2：账号必须真实存在于 accounts.json —— 任何本地可跑的自动化流程都不得拿假账号
    # 起进程。空列表（= 全部账号；现有 6 条流程的 accounts 都是 []）一律放行。
    _require_accounts_exist(accounts)

    # 爆品监控：复用 hot 动作入口
    if flow.get("kind") == "hot":
        action = payload.get("action") or flow.get("run_action") or "snapshot"
        if action not in {"probe", "snapshot", "diff"}:
            raise HTTPException(400, "动作仅支持 probe/snapshot/diff")
        script = config.SCRIPTS_DIR / "hot_product_monitor.py"
        if not script.exists():
            raise HTTPException(404, "缺少 hot_product_monitor.py")
        cmd = [config.python_exe(), str(script), action]
        if action in {"probe", "snapshot"}:
            if not config.COOKIE_FILE.exists():
                raise HTTPException(400, "缺少 cc_cookie.txt（爆品监控需登录 Cookie）")
            cmd += ["--cookie-file", str(config.COOKIE_FILE)]
        return runner.MANAGER.start(
            "hot", f"{flow.get('name')} · {action}", cmd
        ).snapshot()

    # 纯 Agent 侧流程：不支持本地执行，提示改用「生成指令」
    cmd = _automation_run_cmd({**flow, "accounts": accounts})
    if not cmd:
        raise HTTPException(400, "该流程需在 WorkBuddy Agent 侧执行，请点击「生成指令」转交")

    run = runner.MANAGER.start(
        kind=flow.get("kind", "script"),
        title=flow.get("name") or flow_id,
        cmd=cmd,
        account=",".join(accounts) if accounts else "all",
    )
    return run.snapshot()


# ────────────────────────── 转交 WorkBuddy Agent ──────────────────────────
@app.post("/api/handoff")
def handoff(payload: dict) -> dict:
    account = payload.get("account", "")
    date_from = payload.get("date_from", "")
    date_to = payload.get("date_to", "")
    steps = payload.get("steps") or sorted(config.AGENT_STEPS)
    skills = payload.get("skills") or []
    note = payload.get("note", "")

    acc_detail = next((a for a in config.load_accounts_raw().get("accounts", [])
                       if a.get("account") == account), {})
    out_dir = Path(acc_detail.get("out_dir") or config.OUTPUT_DIR)
    audio_dir = Path(acc_detail.get("audio_dir") or (config.OUTPUT_DIR / f"{account}_audio"))

    step_skill = {
        "s3_4_sheet": "tiktok-benchmark-recorder",
        "s6_translate": "kunlun-fanyi",
        "s7_backfill": "tk-audio-to-sheet",
        "s9_report": "tiktok-content-pipeline",
    }
    lines = [
        "# TikTok 对标流水线 · Agent 侧待执行步骤",
        "",
        f"- 账号：{account or '(未指定)'}",
        f"- 日期范围：{date_from} ~ {date_to}",
        f"- 台账：{config.ledger_path()}",
        f"- 音频产物：{audio_dir}/*.mp3",
        f"- 转写产物：{audio_dir}/transcripts/*_transcript.txt",
        f"- 抓取产物：{out_dir / f'scrape_{account}.json'}",
        "",
        "## 待执行步骤",
    ]
    for i, s in enumerate(steps, 1):
        lines.append(f"{i}. `{s}` {config.STEP_LABELS.get(s, '')}"
                     f" —— 使用 skill: `{step_skill.get(s, '按需选择')}`")
    if skills:
        lines += ["", "## 指定 skill", *[f"- `{s}`" for s in skills]]
    lines += ["", "## 约束", "- 每步产物需通过 `pipeline/gate.py` 对应门禁",
              "- 严禁编造数据；缺失一律标注「未收录」", "- 英文账号转写必须 `--lang en`"]
    if note:
        lines += ["", "## 补充说明", note]
    text = "\n".join(lines)

    name = f"handoff_{account or 'all'}_{date_to or time.strftime('%Y%m%d')}.md"
    fp = config.HANDOFF_DIR / name
    fp.write_text(text, encoding="utf-8")
    return {"text": text, "saved": str(fp)}


# ──────────────────────── 提示词反推（2026-10-21 新增）────────────────────────
# 给一条 TikTok 链接 → 分镜级 Seedance 2.0 Mini 提示词包。
# 规格来源：D:/ai/planA/2026-10-21-10-13-27/TikTok视频提示词反推_SOP.md
# 分工：工作台自动做元数据/下载/抽帧/场景检测/首帧导出 + 视觉模型分批读帧；
#       需要人工精修时走 /api/reverse/handoff 生成 Agent 交接包。
@app.post("/api/reverse/create")
def reverse_create(payload: dict) -> dict:
    try:
        return tk_reverse.create((payload or {}).get("url") or "")
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/reverse/list")
def reverse_list() -> dict:
    return {"items": tk_reverse.list_all()}


@app.get("/api/reverse/status/{vid}")
def reverse_status(vid: str) -> dict:
    try:
        return tk_reverse.status(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/reverse/models")
def reverse_models() -> dict:
    models = tk_reverse.load_vision_models()
    items = [{"id": m.get("id"), "name": m.get("name") or m.get("id")} for m in models]
    try:
        default = tk_reverse.pick_model("").get("id") or ""
    except Exception:
        default = items[0]["id"] if items else ""
    return {"items": items, "default": default}


@app.post("/api/reverse/analyze/{vid}")
def reverse_analyze(vid: str, payload: dict | None = None) -> dict:
    try:
        return tk_reverse.analyze(vid, ((payload or {}).get("model") or ""), (payload or {}).get('mode') or 'ad')
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/reverse/stop/{vid}")
def reverse_stop(vid: str) -> dict:
    try:
        return tk_reverse.cancel(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post('/api/reverse/import')
async def reverse_import(request: Request, filename: str = Query('local.mp4')) -> dict:
    _guard_local_write(request)
    if len(filename) > 240 or Path(filename).suffix.lower() not in ('.mp4', '.mov', '.webm', '.mkv'):
        raise HTTPException(400, '请选择 mp4、mov、webm 或 mkv 视频')
    limit = 500 * 1024 * 1024
    if int(request.headers.get('content-length') or 0) > limit:
        raise HTTPException(413, '视频不能超过500MB')
    uploads = tk_reverse.REVERSE_DIR / '_uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix='video_', suffix=Path(filename).suffix.lower(), dir=uploads)
    os.close(descriptor)
    temporary = Path(raw_path)
    size = 0
    try:
        with temporary.open('wb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, '视频不能超过500MB')
                target.write(chunk)
        if not size:
            raise HTTPException(400, '视频文件为空')
        return tk_reverse.import_local(temporary, filename)
    except tk_reverse.ReverseError as error:
        raise HTTPException(400, str(error))
    finally:
        temporary.unlink(missing_ok=True)


@app.post("/api/reverse/rebuild/{vid}")
def reverse_rebuild(vid: str) -> dict:
    try:
        return tk_reverse.rebuild(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/reverse/{vid}")
def reverse_delete(vid: str) -> dict:
    try:
        return tk_reverse.remove(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/reverse/handoff/{vid}")
def reverse_handoff(vid: str) -> dict:
    try:
        return tk_reverse.build_handoff(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/reverse/open/{vid}")
def reverse_open(vid: str) -> dict:
    # ★ 2026-10-21 独立验收 P1-1：打开目录同样走 tk_reverse 的 ID + 越界校验，
    #   不许在这里自己 REVERSE_DIR / vid 拼路径。
    try:
        d = tk_reverse.task_dir(vid)
    except tk_reverse.ReverseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not d.exists():
        raise HTTPException(status_code=404, detail="任务目录不存在")
    try:
        os.startfile(str(d))  # noqa: S606  本地工具：直接开资源管理器
        return {"ok": True, "dir": str(d)}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "dir": str(d), "error": str(e)}


# 报告与首帧走静态目录：HTML 里图片用相对路径 refs/xxx.png，必须同源可访问
app.mount("/tk_reverse", StaticFiles(directory=str(tk_reverse.REVERSE_DIR)), name="tk_reverse")


# ────────────────────────── 静态前端 ──────────────────────────
# 本地工具：前端资源一律禁缓存 —— 否则改完 app.js/style.css 后，浏览器与内置预览面板
# 仍可能跑旧文件，表现为「点击没反应 / 新增按钮不存在」这类假故障。
@app.middleware("http")
async def _no_store_frontend(request, call_next):
    # ★ 2026-10-23 审查 R15：写方法统一挡本地 CSRF。只靠各接口自己调
    #   _guard_local_write 覆盖不全 —— /api/reverse/create、/api/reverse/analyze
    #   等反推写接口根本没调它，恶意网页可以直接 POST 过来烧模型额度。
    #   合并进这一个中间件，避免再叠一层 BaseHTTPMiddleware 影响 SSE 流。
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        host = (getattr(getattr(request, "client", None), "host", "") or "").lower()
        if host not in _LOCAL_WRITE_HOSTS or not _csrf_ok(request):
            return JSONResponse({"detail": "拒绝跨站或非本机写入"}, status_code=403)
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/")
def index() -> FileResponse:
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse({"error": "前端未构建"}, status_code=500)
    return FileResponse(idx)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _listen_port(default: int = 8787) -> int:
    """服务端口（2026-10-23）：WB_PORT 优先，其次 PORT，非法值回退默认。

    背景：8787 是约定端口（自动化、daily_batch、文档都指它），但本机别的工具
    也会抢这个端口；硬编码时只能靠杀进程。改成可覆盖，默认仍是 8787。
    """
    for key in ("WB_PORT", "PORT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            print(f"[startup] {key}={raw!r} 不是整数，忽略", flush=True)
            continue
        if 1024 <= port <= 65535:
            return port
        print(f"[startup] {key}={raw} 超出 1024-65535 范围，忽略", flush=True)
    return default


def _port_free(port: int) -> bool:
    """端口能不能绑（uvicorn 自己会吞掉 bind 异常只打一行 ERROR，所以先自己探一次）。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    import uvicorn
    _swept = store.sweep_stale_running()
    if _swept:
        print(f"[startup] 清扫僵尸运行记录 {_swept} 条 -> interrupted", flush=True)
    scheduler.start_background()
    _port = _listen_port()
    print(f"[startup] 监听 http://127.0.0.1:{_port}", flush=True)
    if not _port_free(_port):
        print(f"[startup] 端口 {_port} 已被别的程序占用，无法启动。\n"
              f"[startup] 换端口启动：在命令行先 set WB_PORT=8789 再跑，"
              f"或双击 启动工作台服务_8789.lnk。", flush=True)
        raise SystemExit(1)
    try:
        uvicorn.run(app, host="127.0.0.1", port=_port, log_level="info")
    except OSError as exc:                       # 探测到真正绑定之间仍可能被抢
        print(f"[startup] 端口 {_port} 启动失败：{exc}", flush=True)
        raise SystemExit(1) from exc
