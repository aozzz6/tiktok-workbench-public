# -*- coding: utf-8 -*-
"""
daily_update.py — v3 每日增量更新编排器（2026-10-03，方案见《每日增量更新方案.md》）

一条命令完成全部对标账号的日更（自动化层入口，Agent/automation 调用本脚本）：

  per account:
    1. manifest 加载（空则从既有产物 bootstrap）
    2. 滚动窗口 = last_success_date - recall_days → today（宁多勿漏，去重靠 video_id）
    3. pipeline.py s0→s1→s2→s5（tolerant 抓取：下架条标注后继续；s5 纯BGM 误停不阻塞后续）
    4. fastmoss_gmv.py 直跑（BGM 门禁停链不影响 GMV 缓存刷新）
    5. manifest 收尾（stages 并集合并，core 成功才推进 last_success_date）
    6. 新视频商品挂车提取（仅差集，旧数据零重复抓取）
    7. snapshot_metrics.py → daily_metrics.jsonl 时序快照
    8. gate_coverage + gate_sanity
    9. ledger_update_{account}.json（Agent 据此经 sheetagent 写台账：加行 + 刷动态列）
  最后:
    reports/{date}/integrity_report.json + email_body.md（分级告警 INFO/WARN/ERROR）
  附加（全局，非按账号）:
    爆品监控: hot_product_monitor.py snapshot + diff（L1 发现层+L2 波动引擎，
    CC SSR 通道需 pipeline/cc_cookie.txt；缺失/失效只降级告警，不影响账号链退出码）

用法:
  python daily_update.py [--accounts vivi_ashop kee.yaden] [--date 2026-10-03]
  退出码: 0=INFO/WARN（含可自愈项），1=ERROR（需人工介入）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

PIPE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPE_DIR))
import manifest as mf  # noqa: E402
import receipts  # noqa: E402  回执契约 v2：与 scripts/daily_batch.py 共用
from gate import run_gates, gate_coverage, gate_sanity  # noqa: E402

CFG_PATH = PIPE_DIR / "accounts.json"
_VENV_PY = PIPE_DIR / ".venv" / "Scripts" / "python.exe"
PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
STATE_FILE = PIPE_DIR / "pipeline_state.json"
HOT_MONITOR_COOKIE = PIPE_DIR / "cc_cookie.txt"

DAILY_STEPS = ["s0_check", "s1_download", "s2_scrape", "s5_transcribe"]
FULL_SCRAPE = False  # --full-scrape：s2 全量重抓（周度刷动态数据；第 4 批 2026-10-10）


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def read_json(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def run_cmd(cmd: list[str], timeout: int | None = None) -> tuple[int, str]:
    """跑子进程，返回 (returncode, stdout 末行 JSON 或末 300 字符)。

    timeout: 墙钟上限（秒）。超时按 Windows 惯例强杀整个进程树（taskkill /T），
    返回码 -9 并在输出尾部标注 [TIMEOUT]，供编排层判为「需接管」而非静默挂死。
    """
    if not timeout:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        out = (proc.stdout or "").strip()
        rc = proc.returncode
    else:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            out, _ = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                out, _ = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                out = ""
            rc = -9
            out = (out or "") + f"\n[TIMEOUT] 超过 {timeout}s，已终止进程树"
        out = (out or "").strip()
    last = ""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            last = line
            break
    return rc, last or out[-300:]


# ────────────────────────── 单账号日更 ──────────────────────────

def _run_image_account(acc: dict, cfg: dict, today: str, report_dir: Path,
                       timeout: int | None = None) -> dict:
    """图文日更只运行采集与独立台账同步，不进入音频/转写/商品链。"""
    name = acc["account"]
    out = Path(acc.get("out_dir") or PIPE_DIR / "output")
    audio = Path(acc.get("audio_dir") or out / f"{name}_audio")
    manifest = mf.load(out, name)
    before = {str(i["id"]) for i in read_json(out / f"scrape_{name}.json", {}).get("items", []) if i.get("id")}
    start, end = mf.compute_window(manifest, dt.date.fromisoformat(today), recall_days=cfg.get("recall_days", 2))
    cmd = [PY, str(PIPE_DIR / "pipeline.py"), "--account", name,
           "--from", start, "--to", end, "--xlsx", cfg.get("img_xlsx") or cfg.get("xlsx", ""),
           "--steps", "s0_check", "s2_scrape", "--allow-scrape-errors", "--no-resume", "--out-dir", str(out)]
    if not FULL_SCRAPE:
        cmd.append("--scrape-incremental")
    rc, detail = run_cmd(cmd, timeout=timeout)
    ledger_rc = 1
    if rc == 0:
        sys.path.insert(0, str(PIPE_DIR / "scripts"))
        import build_ledger_15col
        ledger_rc = build_ledger_15col.main_write([name], cfg)
    ok = rc == 0 and ledger_rc == 0
    mf.update_after_run(out, name, audio, success=ok, today=today)
    scrape = read_json(out / f"scrape_{name}.json", {})
    items = {str(i["id"]): i for i in scrape.get("items", []) if i.get("id")}
    new = sorted(set(items) - before)
    errors = scrape.get("errors") or []
    return {"status": "ok" if ok else "failed", "media_type": "image", "window": [start, end],
            "new": len(new), "known": len(items), "skipped_known": len(before),
            "scrape_failed": len(errors), "snapshot_records": 0,
            "coverage": f"{len(items)} 条图文", "gates": ok,
            "anomalies": [] if ok else [f"图文采集/入账失败: pipeline={rc}, ledger={ledger_rc}; {detail[-200:]}"],
            "ledger": {"rows_to_add": 0, "rows_to_update": 0, "payload": "", "synced": ok},
            "sample_check_url": next((i.get("url") for i in items.values() if i.get("url")), None)}


def run_account(acc: dict, cfg: dict, today: str, report_dir: Path,
                timeout: int | None = None) -> dict:
    if acc.get("media_type") == "image":
        return _run_image_account(acc, cfg, today, report_dir, timeout=timeout)
    name = acc["account"]
    out_dir = Path(acc["out_dir"])
    audio_dir = Path(acc["audio_dir"])
    log(f"=== {name} ===")

    # 1. manifest（空则 bootstrap：从既有产物构建索引，不推进 last_success_date）
    m = mf.load(out_dir, name)
    known_before = set(m["videos"])
    products_path = _products_file(name)
    if not m["videos"] and audio_dir.exists():
        m = mf.update_after_run(out_dir, name, audio_dir, success=False, today=today,
                                products_path=products_path)
        known_before = set(m["videos"])
        log(f"bootstrap: 从产物构建 manifest {len(known_before)} 条")
    prev_last_run = m.get("last_run") or {}
    is_rerun_today = prev_last_run.get("date") == today

    # 2. 滚动窗口
    date_from, date_to = mf.compute_window(m, dt.date.fromisoformat(today),
                                           recall_days=cfg.get("recall_days", 2))
    log(f"窗口 {date_from}~{date_to}（召回 {cfg.get('recall_days', 2)} 天，去重靠 video_id）")

    # 3. pipeline 采集链（tolerant：下架条标注后继续）
    #    ★ 必须带 --no-resume：pipeline_state 的 key 是 "{account}|{step}"（不含日期），
    #    断点续跑会在**次日**把昨天的 ok=True 当成今天已完成而跳过 s0/s1/s2，
    #    导致每日增量抓取彻底失效（2026-10-05 实测：连续两日 s1/s2 全程"已完成，跳过"，
    #    报告 new=0 为假阴性，实际漏掉 11 条新视频）。日更层不使用跨日断点续跑，
    #    s1/s2 内部各自幂等（已下载/已抓取自动跳过），重跑代价可接受。
    cmd = [PY, str(PIPE_DIR / "pipeline.py"),
           "--account", name, "--from", date_from, "--to", date_to,
           "--xlsx", cfg["xlsx"],
           "--steps", *DAILY_STEPS,
           "--allow-scrape-errors",
           "--no-resume",
           "--audio-dir", str(audio_dir), "--out-dir", str(out_dir)]
    # ★ 转写后端（2026-10-14 更新）：改传 auto —— 有 CUDA 设备走 faster+GPU（实测 ~10x
    #   实时，RTX 2060），无 GPU 自动退回 openai/PyTorch CPU。
    #   历史背景：原先必须显式写死 openai，因为本机 AVX-512 CPU 上 faster/ctranslate2
    #   构造即崩（exit=3221225477）。根因是 onnxruntime 段错误导致 VAD 崩，脚本已改为
    #   faster 后端一律 vad_filter=False，且 ct2 走 GPU 不再触发 CPU 那条崩路径。
    if "s5_transcribe" in DAILY_STEPS:
        cmd += ["--backend", "auto"]
    # ★ 日更只关心「最近一天的新内容」→ s2 走增量：只抓新增视频并与历史合并。
    #   默认的全量重抓会刷新所有历史条目的动态数据，代价 O(累计视频数)：
    #   2026-10-10 实测 9 账号约 2 小时；增量通常 1~2 分钟。
    #   ★ 2026-10-10 第 4 批：--full-scrape 周度全量刷——增量模式下历史 stats 冻结
    #   （审查报告 P3），每周日全量重抓一次恢复动态列/时序快照的真实性。
    if "s2_scrape" in DAILY_STEPS and not FULL_SCRAPE:
        cmd.append("--scrape-incremental")
    elif FULL_SCRAPE:
        log("全量刷模式：s2 重抓全部历史条目（刷新动态数据，预计 ~2h）")
    if acc.get("lang"):
        cmd += ["--lang", acc["lang"]]
    rc, _ = run_cmd(cmd, timeout=timeout)
    state = load_state()
    step_ok = {s: state.get(f"{name}|{s}", {}).get("ok", False) for s in DAILY_STEPS}
    core_ok = step_ok["s1_download"] and step_ok["s2_scrape"]
    log(f"pipeline rc={rc} steps={step_ok}")

    # 3.5 s2 失败条目慢速补抓（抗风控：固定短间隔连抓 30+ 条会大面积返回风控页，
    #     被误判 itemStruct empty；放慢到 8s 实测可恢复。幂等，见 retry_scrape_failed.py）
    retry_rc, retry_summary = run_cmd(
        [PY, str(PIPE_DIR / "scripts" / "retry_scrape_failed.py"),
         "--account", name, "--out-dir", str(out_dir),
         "--interval", "8", "--rounds", "1"])
    try:
        retry = json.loads(retry_summary) if retry_summary.startswith("{") else {}
    except json.JSONDecodeError:
        retry = {}
    if retry.get("fixed"):
        log(f"s2 慢速补抓 +{retry['fixed']} 条（剩余失败 {retry.get('remaining', 0)}）")

    # 4. GMV 缓存/pending 刷新（独立直跑，不受 s5 门禁停链影响）
    #    ★ 2026-10-10: 加 accounts.json 级开关 gmv_enabled（默认 False）。
    #    FastMoss 积分 2026-10-02 耗尽后该步是死循环：每日筛选→pending→无人消费→
    #    假 WARN「GMV 待查 N 条（可自愈）」（实测 9 账号 cache=0 从未回填）。
    #    切换 proboost 数据源前先关闭，消噪声。
    gmv = {}
    if cfg.get("gmv_enabled", False):
        gmv_rc, gmv_summary = run_cmd(
            [PY, str(PIPE_DIR / "scripts" / "fastmoss_gmv.py"),
             "--account", name,
             "--scrape", str(out_dir / f"scrape_{name}.json"),
             "--min-plays", "6000", "--out-dir", str(out_dir)])
        try:
            gmv = json.loads(gmv_summary) if gmv_summary.startswith("{") else {}
        except json.JSONDecodeError:
            gmv = {}

    # 5. manifest 收尾（新增判定 = 集合差，不用 first_seen——bootstrap 日其不可靠）
    #    ★ 2026-10-05 修正：新增判定改以**台账实体**为基准（manifest - 台账已有 id）。
    #    原实现的"当日重跑复用 last_run.new_ids"在当日首次运行是**假阴性空跑**
    #    （如 2026-10-05 s1/s2 被断点续跑误跳过）时，会把重跑真实发现的新视频整批吞掉，
    #    导致台账永久漏数据。以台账为基准后，任何情况下"未入台账的 manifest 视频"都会被
    #    正确识别为待新增；台账不可读时降级为原逻辑。
    m = mf.update_after_run(out_dir, name, audio_dir, success=core_ok, today=today,
                            products_path=products_path)
    ledger_ids = _read_ledger_ids(cfg["xlsx"], name)
    if ledger_ids is not None:
        new_ids_today = sorted(set(m["videos"]) - ledger_ids)
    elif is_rerun_today:
        new_ids_today = sorted(set(prev_last_run.get("new_ids", [])) & set(m["videos"]))
    else:
        new_ids_today = sorted(set(m["videos"]) - known_before)
    count_check = (len(new_ids_today), _downloaded_today(state, name)) if not is_rerun_today else None
    m["last_run"] = {"date": today, "new_ids": new_ids_today}
    mf.save(out_dir, name, m)
    known_now = set(m["videos"])

    # 6. 商品挂车提取（差集 + 分批 + 覆盖校验）
    #    ★ 2026-10-09 加固：原实现只按「不在 products 里的」提取一次，且把结果写到
    #    out_dir/{name}_products.json。两个问题：
    #      (a) 抓取若分批补跑（如 lifewillcool 先跑一轮、次日再补抓），后补的视频
    #          当次不会被提取，之后也不会自动回补 → 商品长期漏采（实测 15/204）；
    #      (b) out_dir 曾指向不存在的目录，写入直接失败/落在错误位置。
    #    现改为：① 商品文件统一放对标账号目录（与 run_grab_* 脚本一致）；
    #            ② ID 分批传参，避免 Windows 命令行 8191 字符上限；
    #            ③ 提取后做覆盖校验，仍有缺口记入日志与报告，不静默吞掉。
    products_file = _products_file(name)
    products = read_json(products_file, {})
    s_items, s_errors = mf.scrape_ids(out_dir, name)
    missing = [v for v in s_items if not isinstance(products.get(v), list)]
    if missing:
        log(f"商品挂车提取: 缺失 {len(missing)}/{len(s_items)} 条（含新增与历史漏跑）")
        _extract_products_batched(name, missing, report_dir, products_file, products)
        m = mf.update_after_run(out_dir, name, audio_dir, success=core_ok, today=today,
                                products_path=products_path)

    after = read_json(products_file, {})
    product_gap = [v for v in s_items if not isinstance(after.get(v), list)]
    if product_gap:
        log(f"[warn] 商品覆盖校验：仍缺 {len(product_gap)}/{len(s_items)} 条（下次运行自动补）")
    else:
        log(f"商品覆盖校验: {len(s_items)}/{len(s_items)} 全覆盖")

    # 7. 时序快照
    snap_rc, snap_summary = run_cmd(
        [PY, str(PIPE_DIR / "scripts" / "snapshot_metrics.py"),
         "--scrape", str(out_dir / f"scrape_{name}.json"),
         "--account", name, "--metrics-file", cfg["metrics_file"],
         "--gmv-cache", str(out_dir / f"{name}_fastmoss_cache.json"),
         "--gmv-pending", str(out_dir / f"pending_gmv_{name}.json"),
         "--date", today])
    try:
        snap = json.loads(snap_summary) if snap_summary.startswith("{") else {}
    except json.JSONDecodeError:
        snap = {}

    # 8. 门禁：coverage（三方对齐）+ sanity（对比昨日快照）
    audio_set = set(mf.audio_ids(audio_dir, name))
    prev_recs = load_prev_metrics(cfg["metrics_file"], name, today)
    sanity_payload = {"current": [
        {"id": i["id"], "plays": (i.get("stats") or {}).get("playCount")}
        for i in read_json(out_dir / f"scrape_{name}.json", {}).get("items", [])],
        "previous": [{"id": r["video_id"], "plays": r.get("plays")} for r in prev_recs]}
    coverage_payload = {
        "audio": sorted(audio_set), "scraped": s_items, "scrape_failed": s_errors,
        "manifest": sorted(known_now),
    }
    if count_check:  # 仅当日首次运行核对（重跑时 s1 摘要是缓存的旧值，对账无意义）
        coverage_payload["new_today"] = count_check[0]
        coverage_payload["downloaded_today"] = count_check[1]
    gates_ok = run_gates(
        [("coverage", coverage_payload), ("sanity", sanity_payload)],
        report_dir / f"gate_{name}.json")

    # 9. ledger 更新载荷（Agent + sheetagent 消费）
    ledger = build_ledger_payload(name, cfg, out_dir, audio_dir, m,
                                  new_ids_today, today)
    (report_dir / f"ledger_update_{name}.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    # 汇总
    anomalies = []
    if not core_ok:
        anomalies.append(f"核心采集失败: {[s for s, ok in step_ok.items() if not ok]}")
    if not step_ok.get("s5_transcribe"):
        anomalies.append("转写未全过（疑似纯BGM待标注，见 gate_report）")
    if gmv.get("pending_ids"):
        anomalies.append(f"GMV 待查 {len(gmv['pending_ids'])} 条（FastMoss 额度/登录态，可自愈）")
    cov_detail = _gate_detail(report_dir / f"gate_{name}.json", "coverage")
    if not gates_ok and cov_detail and not cov_detail[0]:
        anomalies.append(f"coverage FAIL: {cov_detail[1][:120]}")

    status = "failed" if not core_ok else ("warn" if anomalies else "ok")
    return {
        "status": status,
        "window": [date_from, date_to],
        "new": len(new_ids_today),
        "known": len(known_now),
        "skipped_known": len(known_before),
        "scrape_failed": len(s_errors),
        "snapshot_records": snap.get("records", 0),
        "coverage": f"{len(s_items) + len(s_errors)}/{len(audio_set)}",
        "gates": gates_ok,
        "anomalies": anomalies,
        "ledger": {"rows_to_add": len(ledger["add_rows"]),
                   "rows_to_update": len(ledger["update_rows"]),
                   "payload": str(report_dir / f"ledger_update_{name}.json")},
        "sample_check_url": ledger["add_rows"][0]["url"] if ledger["add_rows"] else None,
    }


def _downloaded_today(state: dict, name: str) -> int:
    """s1 摘要 JSON 里的 downloaded 数（数量守恒已由 gate_download 把关）。"""
    detail = state.get(f"{name}|s1_download", {}).get("detail", "")
    try:
        i, j = detail.index("{"), detail.rindex("}") + 1
        return int(json.loads(detail[i:j]).get("downloaded", -1))
    except (ValueError, IndexError, json.JSONDecodeError):
        return -1


def _read_ledger_ids(xlsx: str, account: str) -> set | None:
    """读台账 sheet「对标记录-{account}」的「视频链接/ID」列，提取 video_id 集合。
    新增判定的权威基准（台账=已交付事实）。不可读时返回 None 触发降级。

    ★ 2026-10-09 修正（原实现读错列，导致新增判定恒为全量）：
      旧实现按第 5 列硬读（`max_col=5` + `row[4]`），那是 **旧 16 列结构**的残留——
      旧结构中 E 列=视频链接。删去「商品链接/商品名称」后，链接列已挪到 F 列，于是
      读到的是旧 E 列的文案。后果：ledger_ids 与 manifest 的
      video_id 集合交集恒为空 → `set(manifest) - ledger_ids` 恒等于全量 →
      coverage 门禁 new_today != downloaded_today 必然 FAIL、add_rows 每次全量膨胀。
      实测（kee.yaden 277 行）：旧实现返回 71 个文案片段，正确应为 277 个纯数字 ID。

      ★ 2026-10-14 改版（15→13 列：删「视频出单量」「风格标签」，E 列改为「话题标签」）
      对本函数无影响：链接列仍在 F 列，且本函数按表头名定位、不写死序号。

      现改为：① 按表头「视频链接」定位列（不写死序号，列序调整不再错位，定位失败退回 F 列）；
              ② 正则提 ID——F 列值形如
                 `https://www.tiktok.com/@xx/video/7689821342610361630（ID: 7689821342610361630）`，
                 末尾带中文后缀，`split("/")[-1]` 切不干净，必须先匹配 `/video/<id>`。
    """
    try:
        import re
        import openpyxl
        wb = openpyxl.load_workbook(xlsx, read_only=True)
        ws = wb[f"对标记录-{account}"]
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        idx = next((i for i, h in enumerate(header)
                    if h and "视频链接" in str(h)), 5)  # 定位失败退回 F 列
        ids = set()
        for row in ws.iter_rows(min_row=2, max_col=idx + 1, values_only=True):
            if idx >= len(row) or not row[idx]:
                continue
            s = str(row[idx])
            m = re.search(r"/video/(\d{15,25})", s) or re.search(r"(\d{15,25})", s)
            if m:
                ids.add(m.group(1))
        wb.close()
        return ids
    except Exception as e:  # 路径/权限/表名异常 → 不阻断日更，降级
        log(f"[warn] 台账不可读({str(e)[:80]})，新增判定降级为 manifest 差集")
        return None


def _aux_dir() -> Path:
    """对标账号辅助目录 = 台账所在目录（products 及其子目录都放这里）。"""
    try:
        return Path(load_cfg()["xlsx"]).parent
    except Exception:
        return Path("D:/1/对标账号")


def _products_file(name: str) -> Path:
    """商品文件路径：{台账目录}/{account}/{account}_products.json（与 run_grab_* 一致）。"""
    p = _aux_dir() / name / f"{name}_products.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _extract_products_batched(name: str, ids: list, report_dir: Path,
                              products_file: Path, products: dict,
                              batch: int = 60) -> dict:
    """分批调用 extract_products.py（每批 60 条，避免命令行超长），成功后合并回写。"""
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        tmp = report_dir / f"products_tmp_{name}_{i // batch}.json"
        rc, _ = run_cmd([PY, str(PIPE_DIR / "scripts" / "extract_products.py"),
                         "--account", name, "--ids", ",".join(chunk),
                         "--out", str(tmp)])
        if rc == 0:
            products.update(read_json(tmp, {}))
        else:
            log(f"[warn] 商品提取批次 {i // batch + 1} 失败 rc={rc}")
    products_file.write_text(json.dumps(products, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return products


def _gate_detail(report: Path, gate: str):
    rep = read_json(report, {})
    for c in rep.get("checks", []):
        if c.get("gate") == gate:
            return c.get("ok"), c.get("detail")
    return None


def load_prev_metrics(metrics_file: str, account: str, today: str) -> list[dict]:
    """昨日（< today 的最近一天）该账号的快照行，作 sanity 基线。"""
    p = Path(metrics_file)
    if not p.exists():
        return []
    prev_date, rows = None, []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("account") != account or r.get("date") >= today:
            continue
        if prev_date is None or r["date"] > prev_date:
            prev_date, rows = r["date"], []
        if r["date"] == prev_date:
            rows.append(r)
    return rows


def build_ledger_payload(name: str, cfg: dict, out_dir: Path, audio_dir: Path,
                         m: dict, new_ids_today: list, today: str) -> dict:
    """台账更新载荷：add_rows（新视频整行）+ update_rows（已有视频刷动态列）。
    列位映射由 Agent 侧完成（现行 15 列结构见台账「说明」页/§5；
    2026-10-09 起 G 列表头已规范化为无空格的「视频出单量」，读列仍按 strip 容错），本层只给数据。"""
    scrape = read_json(out_dir / f"scrape_{name}.json", {})
    items = {i["id"]: i for i in scrape.get("items", []) if i.get("id")}
    products = read_json(_products_file(name), {})   # 与步骤 6 同源（账号目录）
    cache = read_json(out_dir / f"{name}_fastmoss_cache.json", {})

    def _row(vid: str, it: dict) -> dict:
        tr_file = audio_dir / "transcripts" / f"{name}_{vid}_transcript.txt"
        prods = products.get(vid) or []
        hit = cache.get(vid)
        return {
            "video_id": vid,
            "url": it.get("url") or f"https://www.tiktok.com/@{name}/video/{vid}",
            "desc": it.get("desc", ""),
            "publish_time_gmt8": it.get("publish_time_gmt8"),
            "stats": it.get("stats") or {},
            "transcript_file": str(tr_file) if tr_file.exists() else None,
            "transcript_chars": tr_file.stat().st_size if tr_file.exists() else 0,
            "products": [{"product_id": p.get("product_id"), "title": p.get("title"),
                          "link": f"https://shop.tiktok.com/view/product/{p.get('product_id')}"}
                         for p in prods if p.get("product_id")],
            "gmv": hit.get("gmv") if hit else None,
            "gmv_status": m["videos"].get(vid, {}).get("gmv_status", "below_threshold"),
        }

    add_rows = [_row(v, items[v]) for v in new_ids_today if v in items]
    update_rows = [_row(v, items[v]) for v in items if v not in set(new_ids_today)]
    return {"account": name, "date": today, "xlsx": cfg["xlsx"],
            "sheet": f"对标记录-{name}",
            "note": "add_rows=新视频整行录入；update_rows=仅刷动态列(播放/点赞/出单)，"
                    "静态列(标题/发布时间/文案/商品)勿动；写入后必须磁盘终验",
            "add_rows": add_rows, "update_rows": update_rows}


# ────────────────────────── 爆品监控（全局步骤） ──────────────────────────

def run_hot_monitor(today: str, report_dir: Path) -> dict:
    """类目爆品监控：L1 发现层每日快照 + L2 波动差分（hot_product_monitor.py）。

    独立于账号采集链：cookie 缺失/过期只降级告警为 WARN，不影响账号链的
    ERROR 语义（ERROR 仅由账号核心采集失败触发）。
    """
    if not HOT_MONITOR_COOKIE.exists():
        log("[warn] 爆品监控: cc_cookie.txt 缺失，跳过（浏览器登录 CC 后导出）")
        return {"status": "skipped", "reason": "cookie_missing", "sources_ok": 0,
                "items": 0, "candidates": 0, "top_candidates": [], "anomalies": []}

    rc, snap_summary = run_cmd(
        [PY, str(PIPE_DIR / "scripts" / "hot_product_monitor.py"),
         "snapshot", "--cookie-file", str(HOT_MONITOR_COOKIE), "--date", today])
    try:
        snap = json.loads(snap_summary) if snap_summary.startswith("{") else {}
    except json.JSONDecodeError:
        snap = {}
    ok_sources = snap.get("ok_sources", 0)
    total_items = sum(s.get("items", 0) for s in snap.get("sources", []))
    log(f"爆品监控快照: ok_sources={ok_sources} items={total_items}")

    rc2, diff_summary = run_cmd(
        [PY, str(PIPE_DIR / "scripts" / "hot_product_monitor.py"),
         "diff", "--date", today])
    try:
        diff = json.loads(diff_summary) if diff_summary.startswith("{") else {}
    except json.JSONDecodeError:
        diff = {}
    cand = read_json(PIPE_DIR / "output" / "hot_monitor" / "candidates.json", {})
    cands = cand.get("candidates", [])
    log(f"爆品监控差分: 候选 {len(cands)} 条（rc={rc2}）")

    anomalies = []
    if ok_sources == 0:
        anomalies.append("爆品监控快照 0 源成功（cookie 过期或 CC 页面改版，重导 cookie）")
    return {
        "status": "ok" if ok_sources else "warn",
        "sources_ok": ok_sources,
        "items": total_items,
        "candidates": len(cands),
        "top_candidates": [{"name": c.get("name"), "growth_pct": c.get("growth_pct"),
                            "top_metric": c.get("top_metric"),
                            "window_days": c.get("window_days")}
                           for c in cands[:5]],
        "anomalies": anomalies,
    }


# ────────────────────────── 汇总报告 ──────────────────────────

def write_reports(cfg: dict, results: dict, today: str, report_dir: Path,
                  hot: dict | None = None) -> str:
    level = "INFO"
    if any(r["status"] == "failed" for r in results.values()):
        level = "ERROR"
    elif any(r["anomalies"] for r in results.values()) or (hot and hot["anomalies"]):
        level = "WARN"

    report = {
        "date": today,
        "accounts": results,
        "hot_monitor": hot,
        "ledger": {
            "rows_to_add": sum(r["ledger"]["rows_to_add"] for r in results.values()),
            "rows_to_update": sum(r["ledger"]["rows_to_update"] for r in results.values()),
            "disk_verify": "pending（Agent 写台账后执行）",
        },
        "alert_level": level,
    }
    (report_dir / "integrity_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 邮件正文（Agent 补台账结果后经 Agent Mail 发送）
    lines = [f"# TikTok 对标日更报告 {today}", "",
             f"**告警级别: {level}**", "",
             "| 账号 | 状态 | 新增 | 已知 | 抓取失败 | 快照 | 覆盖 | 门禁 |",
             "|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        lines.append(f"| {name} | {r['status']} | {r['new']} | {r['known']} "
                     f"| {r['scrape_failed']} | {r['snapshot_records']} "
                     f"| {r['coverage']} | {'PASS' if r['gates'] else 'FAIL'} |")
    if hot:
        lines += ["", f"**类目爆品监控**: {hot['status']} · 快照 {hot['items']} 条"
                      f"（{hot['sources_ok']} 源）· 候选 {hot['candidates']} 条"]
        for c in hot["top_candidates"]:
            lines.append(f"  - {c['name']}（{c['top_metric']} +{c['growth_pct']}%，"
                         f"{c['window_days']} 天窗口）")
    for name, r in results.items():
        if r["anomalies"]:
            lines.append(f"\n**{name} 异常**:")
            lines += [f"- {a}" for a in r["anomalies"]]
    if hot and hot["anomalies"]:
        lines += ["\n**爆品监控异常**:", *[f"- {a}" for a in hot["anomalies"]]]
    samples = [r["sample_check_url"] for r in results.values() if r["sample_check_url"]]
    if samples:
        lines += ["", "**人工抽检（点开核对台账数据）**:", *[f"- {u}" for u in samples]]
    lines += ["", "---",
              "Agent 待办: ①按 ledger_update_*.json 写台账(加行+刷动态列) "
              "②写后磁盘终验(重开xlsx核对行数与抽样) ③新视频音频文案/翻译补录 "
              "④将本报告与台账结果发送用户邮箱"]
    (report_dir / "email_body.md").write_text("\n".join(lines), encoding="utf-8")
    return level


# ────────────────────────── 编排层（无人值守）──────────────────────────
# 方案五落地：单账号失败不再决定整轮命运。
#   · --retry N      失败账号自动重跑 N 轮（轮内串行，轮间只跑上一轮失败者）
#   · --timeout S    单账号墙钟上限，超时强杀进程树 → 判 failed → 进待接管
#   · --resume       当日已有 ok/warn 记录的账号直接复用，支持中断后断点续跑
#   · run_summary.json 机器可读回执（工作台 /api/pipeline/status 唯一数据源）
SUMMARY_NAME = "run_summary.json"


def _empty_result(anomaly: str) -> dict:
    return {"status": "failed", "window": [], "new": 0, "known": 0,
            "skipped_known": 0, "scrape_failed": 0, "snapshot_records": 0,
            "coverage": "-", "gates": False, "anomalies": [anomaly],
            "ledger": {"rows_to_add": 0, "rows_to_update": 0, "payload": ""},
            "sample_check_url": None}


def read_prev_summary(report_dir: Path) -> dict:
    d = read_json(report_dir / SUMMARY_NAME, {})
    return d if isinstance(d, dict) else {}


def run_all(accounts: list[dict], cfg: dict, today: str, report_dir: Path,
            retry: int = 1, timeout: int | None = None,
            resume: bool = False) -> tuple[dict, dict]:
    """逐账号执行 + 失败重试。返回 (results, attempts)。"""
    prev = (read_prev_summary(report_dir).get("accounts") or {}) if resume else {}
    results: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    pending: list[dict] = []

    for acc in accounts:
        name = acc["account"]
        if resume and prev.get(name, {}).get("status") in ("ok", "warn"):
            log(f"{name}: 本轮已有 {prev[name]['status']} 记录，resume 跳过")
            results[name] = prev[name]
            attempts[name] = 0
            continue
        pending.append(acc)

    rounds = max(1, int(retry or 1))
    for rnd in range(1, rounds + 1):
        if not pending:
            break
        if rnd > 1:
            log(f"=== 第 {rnd}/{rounds} 轮重试：{', '.join(x['account'] for x in pending)} ===")
        nxt: list[dict] = []
        for acc in pending:
            name = acc["account"]
            t0 = time.time()
            try:
                r = run_account(acc, cfg, today, report_dir, timeout=timeout)
            except Exception as e:  # 单账号异常不炸全局日更
                r = _empty_result(f"EXCEPTION: {e}")
                log(f"{name} EXCEPTION: {e}")
            r["duration_s"] = round(time.time() - t0, 1)
            results[name] = r
            attempts[name] = rnd
            if r.get("status") == "failed":
                nxt.append(acc)
        pending = nxt

    return results, attempts


def write_run_summary(cfg: dict, results: dict, attempts: dict, today: str,
                      report_dir: Path, started_at: float, level: str) -> dict:
    """机器可读运行回执：工作台据此渲染「待接管」，无需解析日志。

    ★ 2026-10-21 独立验收 P1-D：本函数**不再直接覆写**当日 summary 与 latest。
      旧实现把 `{date,level,total,counts,accounts,attempts,takeover}` 直接写进
      `run_summary.json` / `latest_run_summary.json`：一次单账号补跑就把当日整轮
      （15 账号 + ledger_verify 失败）覆盖成 2 账号 + INFO，面板随即「假绿」。
      现在的做法与 scripts/daily_batch.py 完全一致：
        1. 用 receipts.legacy_summary() 构造带 state/round_id/round_kind/source 的本轮；
        2. 用 receipts.persist() 按账号并入当日整轮、追加 rounds[]、同步 latest；
        3. 本路没有阶段结论，显式写 contract.legacy_stages=True —— 消费端
           （pipeline_ops）会把它标成「可信度不足」，而不是当成本轮终验结论。
    """
    reports_root = Path(cfg.get("reports_root") or Path(report_dir).parent)
    day_dir = Path(report_dir)
    summary = receipts.legacy_summary(today, level, started_at, results, attempts,
                                      source="daily_update", round_kind="legacy")
    merged = receipts.persist(summary, day_dir, reports_root, today, True,
                              list((results or {}).keys()), log=log)
    # ★ 返回合并视图：旧调用方（main）用 counts/takeover 打日志，不能让「本轮只跑了 1 个
    #   账号」把当日其它失败账号从日志里抹掉。
    return merged


def main() -> int:
    global FULL_SCRAPE
    ap = argparse.ArgumentParser(description="TikTok 对标账号每日增量更新")
    ap.add_argument("--accounts", nargs="*", default=None, help="只跑指定账号")
    ap.add_argument("--date", default=None, help="覆盖运行日期（默认今天）")
    ap.add_argument("--full-scrape", action="store_true",
                    help="s2 全量重抓全部历史条目（刷新动态数据；9 账号约 2h）。"
                         "周度用：增量模式下历史 stats 冻结，建议每周日跑一次恢复真实性")
    ap.add_argument("--retry", type=int, default=1,
                    help="失败账号重试轮次（含首轮；默认 1 = 不重试）")
    ap.add_argument("--timeout", type=int, default=5400,
                    help="单账号墙钟上限秒（默认 5400=90 分钟），超时强杀并记入待接管")
    ap.add_argument("--resume", action="store_true",
                    help="断点续跑：当日 run_summary 里已 ok/warn 的账号跳过")
    ap.add_argument("--no-hot", action="store_true", help="跳过爆品监控全局步骤")
    a = ap.parse_args()
    FULL_SCRAPE = bool(a.full_scrape)
    started_at = time.time()

    cfg = load_cfg()
    today = a.date or dt.date.today().isoformat()
    report_dir = Path(cfg["reports_root"]) / today
    report_dir.mkdir(parents=True, exist_ok=True)
    log(f"日更开始 date={today} reports={report_dir} retry={a.retry} timeout={a.timeout}s "
        f"resume={a.resume}")

    accounts = [x for x in cfg["accounts"]
                if x.get("active", True)                      # 停用账号退出日更（缺省视为启用）
                and (not a.accounts or x["account"] in a.accounts)]
    if not accounts:
        skipped = [x["account"] for x in cfg["accounts"] if not x.get("active", True)]
        log("无匹配账号（检查 --accounts / accounts.json）"
            + (f"；已跳过停用账号: {', '.join(skipped)}" if skipped else ""))
        return 1

    # 账号间串行；图文由 run_account 分流到采集与独立台账。
    results, attempts = run_all(accounts, cfg, today, report_dir,
                                retry=a.retry, timeout=a.timeout, resume=a.resume)

    # 爆品监控（全局步骤：账号链之后、汇总报告之前；失败不阻断日更）
    hot = None
    if not a.no_hot and any(x.get("media_type") != "image" for x in accounts):
        try:
            hot = run_hot_monitor(today, report_dir)
        except Exception as e:
            log(f"爆品监控 EXCEPTION: {e}")
            hot = {"status": "warn", "sources_ok": 0, "items": 0, "candidates": 0,
                   "top_candidates": [], "anomalies": [f"EXCEPTION: {e}"]}

    level = write_reports(cfg, results, today, report_dir, hot)
    summary = write_run_summary(cfg, results, attempts, today, report_dir, started_at, level)
    failed = summary["counts"].get("failed", 0)
    log(f"日更完成 alert_level={level} ok={summary['counts'].get('ok', 0)} "
        f"warn={summary['counts'].get('warn', 0)} failed={failed}（报告: {report_dir}）")
    if failed:
        log("待接管账号: " + ", ".join(x["account"] for x in summary["takeover"]))
    return 1 if level == "ERROR" else 0


if __name__ == "__main__":
    sys.exit(main())
