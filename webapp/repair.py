# -*- coding: utf-8 -*-
"""一键修复（2026-10-24）：把总览「待接管」里可自愈的问题串成一条可执行的修复流水。

架构约束（与既有实现一致，勿回退）：
  · 采集修复不新增写台账入口 —— 走 `scripts/daily_batch.py --accounts X`
    （唯一回执契约；子集补跑按账号并入当日整轮，见 pipeline_ops.start_retry）。
  · 台账修复走 `scripts/post_update_flow.py`（幂等 write + export-trans + 磁盘终验）。
  · 翻译**不可本地修**：webapp 无 LLM，N 列译文只能由翻译巡检 automation / Agent 生成，
    所以恒为「跳过」并写明原因，绝不本地伪造译文。
  · 逐项顺序执行：转写吃 GPU、台账只有一个写者，并发会互相踩。
  · 进度是事实：done/total/percent 全部由「已落终态的项数」推导，不做假进度；
    exit=0 不足以判成功 —— 采集项复跑后再查一次待接管队列，转写项再数一次产物。
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import ai_repair
import config
import llm
import runner

# ── 可修项类型 ─────────────────────────────────────────────────────
KIND_LABEL = {
    "collect": "采集重跑",
    "transcribe": "转写补齐",
    "products": "商品提取",
    "ledger": "台账同步",
    "translation": "翻译回填",
    "receipt": "回执自检",
}
# 执行顺序：先修断链（采集），再补产物，最后对账
KIND_RANK = {"collect": 0, "products": 1, "transcribe": 2, "ledger": 3,
             "translation": 8, "receipt": 9}
# 单项墙钟上限（秒）：采集是全链、转写吃 GPU，给足；台账/商品是轻量对账
# receipt = 重跑上一轮账号重建回执（每个账号 ≈ 一次日更），按单账号上限给足
DEFAULT_TIMEOUT = {"collect": 3600, "transcribe": 2400, "products": 900, "ledger": 1800,
                   "receipt": 5400}
MIN_RUNTIME_S = 2.0          # 秒退护栏：<2s 结束视为空跑（历史坑：不带 no_resume 全跳过）
POLL_S = 1.0
STOP_GRACE_S = 6.0

_LOCK = threading.Lock()
_SESSION: "RepairSession | None" = None

# 修复子任务进度的可读化（2026-10-24）
# 背景：collect 是全链（下载 11s + 抓取 5s/条 + 转写 + 商品 150s + 入账），实测一个账号
#       429s。这期间面板只有「已完成 0/3 · 已用时」，用户会以为卡死并来问「为什么不能修复」。
#       这里把 daily_batch 内部那个 pipeline run 的最后一行阶段透出来。
_STAGE_CN = {
    "s0_check": "环境检查", "s1_download": "下载音频", "s2_scrape": "抓取详情",
    "s5_transcribe": "转写", "s5b_products": "商品提取", "DONE": "收尾入账",
    "PARALLEL": "抓取 + 转写（并行）", "START": "启动",
    # pipeline 打「开始」行时用的是短码（[15:21:40] s2 | 开始: ...）
    "s1": "下载音频", "s2": "抓取详情", "s5": "转写", "s5b": "商品提取",
}
_PIPELINE_RUN_RE = re.compile(r"run_id=([0-9a-f]{12})")
_STAGE_RE = re.compile(r"\[\d\d:\d\d:\d\d\]\s*([A-Za-z0-9_]+)")
_STAGE_OK_RE = re.compile(r"\[\d\d:\d\d:\d\d\]\s*([A-Za-z0-9_]+)\s*\|.*?ok=(True|False)")

# 多步骤任务的阶段表（2026-10-24）：weight 是整体进度占比，合计 100。
# 顺序 = pipeline 的真实执行顺序；s2 与 s5 并行执行，两条会同时「进行中」。
# 权重按 2026-10-24 一个真实账号（403s 全链）的耗时分布定：
#   s1 11s / s2 241s / s5 0.1s(已转完) / s5b 151s —— 抓取是大头，商品次之。
REPAIR_FLOW: tuple[tuple[str, str, int], ...] = (
    ("s1_download", "下载音频", 5),
    ("s2_scrape", "抓取详情", 50),
    ("s5_transcribe", "转写", 30),
    ("s5b_products", "商品提取", 10),
    ("ledger", "台账入账", 5),
)
_FLOW_CODE = {code: code.split("_")[0] for code, _, _ in REPAIR_FLOW}


def _flow_ok_codes(lines: list[str]) -> set[str]:
    """从 pipeline 日志里收集「已经 ok=True 的阶段短码」。

    pipeline 打的是 `[15:25:41] s2_scrape | ok=True cost=241.2s | ...`，
    开始行是 `[15:21:40] s2 | 开始: ...` —— 只认前者，避免「刚开始」被当成「跑完」。
    """
    got: set[str] = set()
    for line in lines:
        m = _STAGE_OK_RE.search(line)
        if m and m.group(2) == "True":
            got.add(m.group(1).split("_")[0])
    return got


def _flow_steps(pip_lines: list[str], db_lines: list[str], final: str = "") -> dict:
    """算多步骤任务的逐步状态与整体占比。

    state: done | running | pending | failed | stopped
    - done    该阶段日志里出现过 ok=True
    - running 日志里「已开始但还没 ok」的阶段 —— s2 与 s5 是并行执行的，
              这样两条会同时亮，而不是只有最后一行那条亮（否则像是在串行骗人）
    - failed / stopped  终态时把停在的第一个未完成步骤标出来，后续保持 pending
    - pending 还没开始
    """
    ok_codes = _flow_ok_codes(pip_lines)
    started: set[str] = set()
    for line in pip_lines:
        m = _STAGE_RE.search(line)
        if m:
            started.add(m.group(1).split("_")[0])
    flow_shorts = {_FLOW_CODE[c] for c, _, _ in REPAIR_FLOW}
    running_codes = (started & flow_shorts) - ok_codes
    # 台账入账由 daily_batch 收尾驱动，不在 pipeline 日志里：
    # 先判「写+终验都 rc=0」= done，再判「已经开始写」= running（顺序不能反）
    if any("post_update" in l and "rc=0" in l for l in db_lines):
        ok_codes.add("ledger")
    elif any("post_update" in l for l in db_lines):
        running_codes.add("ledger")

    steps: list[dict] = []
    total = 0
    marked = False                        # 终态只标记「第一个没做完的」步骤
    for code, label, weight in REPAIR_FLOW:
        short = _FLOW_CODE[code]
        if short in ok_codes:
            state = "done"
            total += weight
        elif final and not marked:
            state, marked = final, True
        elif not final and short in running_codes:
            state = "running"
        else:
            state = "pending"
        steps.append({"key": code, "label": label, "weight": weight, "state": state})

    current = next((_FLOW_CODE[c] for c, _, _ in REPAIR_FLOW
                    if _FLOW_CODE[c] in running_codes), "")
    return {"steps": steps, "pct": min(100, total), "current_key": current}


def _stage_label(line: str) -> str:
    """从 pipeline 日志行里取阶段名并翻成中文；认不出就返回空串。"""
    m = _STAGE_RE.search(line or "")
    if not m:
        return ""
    return _STAGE_CN.get(m.group(1), m.group(1))


def _public(item: dict) -> dict:
    """去掉内部字段（`_` 前缀）后的可下发结构。"""
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _mk(index: int, kind: str, account: str, title: str, reason: str,
        repairable: bool = True, skip_reason: str = "", ctx: dict | None = None) -> dict:
    it = {
        "id": f"rp{index:02d}",
        "kind": kind,
        "kind_label": KIND_LABEL.get(kind, kind),
        "account": account or "",
        "title": title,
        "reason": reason or "",
        "repairable": bool(repairable),
        "skip_reason": "" if repairable else (skip_reason or "不可自动修复"),
    }
    if ctx:
        it["_ctx"] = ctx
    return it


def build_plan(accounts: list[dict] | None = None,
               pipeline_status: dict | None = None,
               pending_translation: int = 0) -> dict:
    """推导修复计划（纯函数，便于测试）。

    accounts         = /api/overview 的 accounts（含 gaps / running / 产物计数）
    pipeline_status  = pipeline_ops.status()
    pending_translation = 待译条数
    """
    accounts = accounts or []
    st = pipeline_status or {}
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    collect_names: set[str] = set()
    # 停用账号（accounts.json active=false / ledger_skip）不自动修复：修了也不会进日更
    disabled = {a.get("account") for a in accounts if a.get("enabled") is False}

    def add(kind, account, title, reason, repairable=True, skip_reason="", ctx=None):
        key = (account, kind)
        if key in seen:
            return None
        seen.add(key)
        it = _mk(len(items) + 1, kind, account, title, reason,
                 repairable, skip_reason, ctx)
        items.append(it)
        return it

    def add_account(kind, account, title, reason, ctx=None):
        """账号级项：停用账号一律降级为「跳过」并写明原因。"""
        off = account in disabled
        return add(kind, account, title, reason,
                   repairable=not off,
                   skip_reason="账号已停用（active=false / ledger_skip），不自动修复" if off else "",
                   ctx=ctx)

    # ── 1) 待接管队列（自动化跑完仍失败的账号，最权威）──────────────
    for row in st.get("takeover") or []:
        name = str(row.get("account") or "").strip()
        if not name:
            continue
        reason = row.get("reason") or "自动化执行失败"
        if (row.get("kind") or "") == "ledger":
            # 采集成功、只是没落台账 → 只补台账，不重跑全链
            add_account("ledger", name, "台账补入账", reason)
        else:
            add_account("collect", name, "采集重跑（全链）", reason)
            collect_names.add(name)

    # ── 2) 逐账号链路缺口 ───────────────────────────────────────────
    running = {a.get("account") for a in accounts if a.get("running")}
    for a in accounts:
        name = a.get("account") or ""
        if not name:
            continue
        is_image = a.get("media_type") == "image"
        ctx = {"out_dir": a.get("out_dir") or "", "audio_dir": a.get("audio_dir") or ""}
        for g in a.get("gaps") or []:
            label = g.get("label")
            level = g.get("level")
            msg = g.get("msg") or ""
            if label == "采集" and level in ("warn", "error"):
                add_account("collect", name, "采集补齐（全链）", msg, ctx=ctx)
                collect_names.add(name)
            elif label == "转写" and level in ("warn", "error") and not is_image:
                add_account("transcribe", name, "转写补齐", msg, ctx=ctx)
            elif label == "商品" and not is_image:
                # 「无商品（账号未挂小黄车）」是正常业务形态，不是问题，不进计划
                if level in ("warn", "error") or (level == "info" and "未提取" in msg):
                    add_account("products", name, "商品补提取", msg, ctx=ctx)
            elif label == "入账" and level in ("warn", "error"):
                add_account("ledger", name, "台账补入账", msg, ctx=ctx)

    # ── 3) 全局：待译（本地无 LLM，恒跳过）──────────────────────────
    if pending_translation:
        add("translation", "", f"翻译回填 {int(pending_translation)} 条",
            "台账「音频」有值而「翻译」为空", repairable=False,
            skip_reason=("本地服务无 LLM：译文由翻译巡检 automation（每 15 分钟）或 Agent 回填；"
                         "此处不做本地伪写，以免把外文写进中文列"))

    # ── 4) 全局：回执存疑（重跑上一轮账号，按 partial 并入当日整轮 → 重写回执）──
    # ★ 2026-10-28：以前这里是 repairable=False，理由写「用重跑全部」，而「重跑全部」
    #   在队列为空时又是一条 400 死路 —— 面板把人指向一个点了没反应的按钮。
    #   现在这项自己就能执行：拿 last_round.accounts 重跑（partial 合并，不丢别的账号），
    #   跑完再复读回执，仍存疑就如实判失败并写出 stale 原因。
    rc = st.get("receipt") or {}
    if rc.get("stale"):
        reasons = rc.get("stale_reasons") or []
        last = [str(x) for x in ((st.get("last_round") or {}).get("accounts") or [])]
        add("receipt", "", "重建日更回执（重跑上一轮）",
            "；".join(reasons) or "回执不可信", repairable=True,
            ctx={"accounts": last})

    # ── 5) 去重与并发保护 ──────────────────────────────────────────
    for it in items:
        if not it["repairable"]:
            continue
        acc = it["account"]
        if it["kind"] != "collect" and acc in collect_names:
            it["repairable"] = False
            it["skip_reason"] = "同账号已有「采集重跑」项（全链已覆盖转写/商品/台账），不重复执行"
        elif acc and acc in running:
            it["repairable"] = False
            it["skip_reason"] = "该账号当前正在运行，跳过以避免并发写同一本台账"

    items.sort(key=lambda x: (KIND_RANK.get(x["kind"], 9), x["account"]))
    for i, it in enumerate(items, 1):
        it["id"] = f"rp{i:02d}"
    repairable = sum(1 for it in items if it["repairable"])
    # items 带内部 _ctx（产物目录等），对外一律走 public()，执行侧才用原始项
    return {
        "as_of": time.time(),
        "items": items,
        "counts": {
            "total": len(items),
            "repairable": repairable,
            "skipped": len(items) - repairable,
        },
    }


def public(plan: dict) -> dict:
    """下发给前端的计划：剥掉内部字段。"""
    return {**plan, "items": [_public(it) for it in plan.get("items") or []]}


# ── 命令构造 ───────────────────────────────────────────────────────
def effective_kind(item: dict) -> str:
    """本次实际执行的动作类型：AI 采纳后覆盖规则类型，未采纳则用规则类型。

    ★ 独立验收 R2：以前 `_steps_for` 看 `_ai_kind`、`_verify` 看 `item['kind']`，
      AI 把 collect 换成 transcribe 后，执行的是补转写、复核却只看采集待接管队列，
      队列为空就假成功。执行/超时/复核必须共用这一个事实源。
    """
    return str(item.get("_ai_kind") or item.get("kind") or "")


def _py() -> str:
    return config.python_exe()


def _steps_for(item: dict, days: int, retry: int, timeout: int) -> list[tuple[str, list[str]]]:
    """把可修项翻译成顺序执行的子进程命令；无法构造时抛 ValueError（→ 该项判失败）。"""
    # AI 可能把动作换成更轻的同类修复（如 collect → ledger），执行按有效动作走
    kind = effective_kind(item)
    acc = item["account"]
    # 未显式指定时按类型默认值下发（曾把 None 传成 0 → daily_batch 单账号上限只剩 300s）
    eff = int(timeout or DEFAULT_TIMEOUT.get(kind, 1800))
    if kind == "collect":
        cmd = [_py(), str(config.SCRIPTS_DIR / "daily_batch.py"),
               "--accounts", acc,
               "--retry", str(max(1, int(retry))),
               "--timeout", str(max(300, eff)),
               "--days", str(max(1, int(days)))]
        return [("采集重跑（抓取→转写→商品→入账）", cmd)]
    if kind == "transcribe":
        ledger = config.ledger_path()
        if not ledger:
            raise ValueError("未找到台账 xlsx，无法补齐转写")
        end = date.today()
        start = end - timedelta(days=max(1, int(days)) - 1)
        steps = [(f"重新转写（{start}~{end}）",
                  [_py(), str(config.PIPELINE_DIR / "pipeline.py"),
                   "--account", acc, "--from", str(start), "--to", str(end),
                   "--xlsx", str(ledger), "--steps", "s5_transcribe", "--no-resume"])]
        steps.append(("台账同步（补「音频」与入账）",
                      [_py(), str(config.SCRIPTS_DIR / "post_update_flow.py"), "--accounts", acc]))
        return steps
    if kind == "products":
        ctx = item.get("_ctx") or {}
        out_dir = Path(ctx.get("out_dir") or config.OUTPUT_DIR)
        scrape = out_dir / f"scrape_{acc}.json"
        if not scrape.exists():
            raise ValueError(f"缺少抓取产物 scrape_{acc}.json（先跑采集重跑）")
        ledger = config.ledger_path()
        aux = (ledger.parent if ledger else Path(r"D:/1/对标账号"))
        out = aux / acc / f"{acc}_products.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        return [("商品提取", [_py(), str(config.SCRIPTS_DIR / "extract_products.py"),
                          "--account", acc, "--scrape", str(scrape), "--out", str(out)])]
    if kind == "ledger":
        return [("台账同步（write + 终验）",
                 [_py(), str(config.SCRIPTS_DIR / "post_update_flow.py"), "--accounts", acc])]
    if kind == "receipt":
        # 回执是 daily_batch 写的：重建只能靠它再跑一轮。只跑上一轮账号 → partial
        # 合并进当日整轮（未跑账号的结论不会被抹掉），验证照样是全局的。
        names = [str(x) for x in ((item.get("_ctx") or {}).get("accounts") or [])]
        cmd = [_py(), str(config.SCRIPTS_DIR / "daily_batch.py")]
        if names:
            cmd += ["--accounts", *names]
        cmd += ["--retry", str(max(1, int(retry))), "--timeout", str(max(300, eff))]
        return [("重建日更回执（重跑上一轮，并入当日整轮）", cmd)]
    raise ValueError(f"不支持的修复类型：{kind}")


def _tail_reason(snap: dict) -> str:
    """从运行日志尾部取一条可读的失败原因。

    runner 在末尾会补 `[进程退出] exit_code=N` / `[用户终止]` 这类合成行，
    它们只有状态没有信息量 —— 先剔掉，再挑最后一行业务输出。
    """
    tail = [str(x).strip() for x in (snap.get("tail") or []) if str(x).strip()]
    if not tail:
        return "无日志输出"
    noisy = ("[进程退出]", "[用户终止]")
    real = [l for l in tail if not l.startswith(noisy)]
    pool = real or tail
    # 优先带错误信号的最后一行，否则退回最后一行
    for line in reversed(pool[-8:]):
        if any(k in line for k in ("失败", "错误", "Error", "ERROR", "Traceback", "exit=", "不可")):
            return line[:220]
    return pool[-1][:220]


def _verify_one(item: dict, kind: str) -> tuple[bool, str]:
    """按**某一个**动作类型核对结果；不轻信退出码。"""
    acc = item.get("account") or ""
    if kind == "receipt":
        # 回执项的动作是「再跑一轮」，唯一的成功标准就是回执不再存疑 ——
        # 退出码 0 只说明这一轮跑完了，不代表阶段失败消失了。
        import pipeline_ops
        try:
            st = pipeline_ops.status()
        except Exception as e:      # 复核失败不冒充成功
            return False, f"回执复核失败：{type(e).__name__}: {e}"
        rc = st.get("receipt") or {}
        if rc.get("stale"):
            return False, ("重跑已结束，但回执仍存疑：" +
                           ("；".join(rc.get("stale_reasons") or []) or "回执不可信"))
        al = st.get("alerts") or []
        if al:
            return False, ("重跑已结束，但仍有阶段告警：" +
                           str((al[0] or {}).get("reason") or "未知阶段失败"))
        return True, ""
    if not acc:
        return True, ""
    if kind == "collect":
        import pipeline_ops
        try:
            st = pipeline_ops.status()
        except Exception as e:      # 复核失败不改变「已跑完」的事实，但不冒充成功
            return False, f"重跑已结束，但复核待接管队列失败：{type(e).__name__}: {e}"
        for row in st.get("takeover") or []:
            if row.get("account") == acc:
                return False, f"重跑后账号仍在待接管：{row.get('reason') or '未知原因'}"
        return True, ""
    if kind == "transcribe":
        audio_dir = (item.get("_ctx") or {}).get("audio_dir") or ""
        if not audio_dir:
            return True, ""
        d = Path(audio_dir)
        if not d.exists():
            return True, ""
        audio = sum(1 for _ in d.glob("*.mp3"))
        trans = sum(1 for _ in d.joinpath("transcripts").glob("*_transcript.txt"))
        if audio and trans < audio:
            return False, f"转写仍缺 {audio - trans} 条（{trans}/{audio}）"
    return True, ""


def _verify(item: dict) -> tuple[bool, str]:
    """结果复核：有效动作的产物 + 原始缺口都必须消失。

    ★ 独立验收 R2：只验证 AI 换后的有效动作会造成假成功（缺转写却只看采集队列）。
      这里两道都要过：
        1) 有效动作（effective_kind）真的补齐了；
        2) 原始规则缺口（item['kind']）也真的消失 —— 否则如实判失败并说明。
    """
    kind = effective_kind(item)
    orig = str(item.get("kind") or kind)
    ok, why = _verify_one(item, kind)
    if not ok:
        return False, why
    if orig and orig != kind:
        ok2, why2 = _verify_one(item, orig)
        if not ok2:
            return False, (f"{KIND_LABEL.get(kind, kind)}已完成，但原始缺口"
                           f"（{KIND_LABEL.get(orig, orig)}）仍在：{why2}")
    return True, ""


# ── 修复会话 ───────────────────────────────────────────────────────
class RepairSession:
    def __init__(self, items: list[dict], days: int, retry: int, timeout: int | None,
                 use_ai: bool = False, model: str = "", lease: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.items = items
        self.days = days
        self.retry = retry
        self.timeout = timeout
        self.use_ai = bool(use_ai)
        self.model = model or ""
        self.lease = lease               # 共享写者租约（R1）：子任务继承，会话结束释放
        self.ai_meta: dict = {"phase": "idle", "model": self.model or "auto",
                              "degraded": False, "error": "", "judged": 0,
                              "attributed": 0, "verdicts": {}}
        self.results: dict[str, dict] = {}
        self.status = "running"          # running | done | stopped
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.current_id: str | None = None
        self.current_step = ""
        self.current_run_id = ""
        self._live_cache: dict = {}      # 阶段表最后一次快照（终态后仍要显示，见 _live_done）
        self.error = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ── 对外快照 ────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            total = len(self.items)
            rows = []
            counts = {"ok": 0, "failed": 0, "skipped": 0}
            for it in self.items:
                r = self.results.get(it["id"])
                row = _public(it)
                row["status"] = (r or {}).get("status", "pending")
                row["detail"] = (r or {}).get("detail", "")
                row["duration_s"] = (r or {}).get("duration_s")
                row["run_id"] = (r or {}).get("run_id", "")
                row["ai"] = (r or {}).get("ai") or self.ai_meta["verdicts"].get(it["id"]) or {}
                if r:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                rows.append(row)
            done = counts["ok"] + counts["failed"] + counts["skipped"]
            cur = next((r for r in rows if r["id"] == self.current_id), None)
            now = self.ended_at or time.time()
            payload = {
                "id": self.id,
                "status": self.status,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration_s": round(now - self.started_at, 1),
                "total": total,
                "done": done,
                "percent": int(round(100 * done / total)) if total else 0,
                "counts": counts,
                "current": None if not cur else {
                    "id": cur["id"], "kind": cur["kind"], "kind_label": cur["kind_label"],
                    "account": cur["account"], "title": cur["title"],
                    "step": self.current_step,
                },
                # 运行中实时算；终态返回最后一次快照并收口（阶段表不能因为结束就消失）
                "live": self._live() if self.status == "running" else self._live_done(),
                "items": rows,
                "error": self.error,
                "ai": {
                    "enabled": self.use_ai,
                    "phase": self.ai_meta.get("phase"),
                    "model": self.ai_meta.get("model"),
                    "degraded": bool(self.ai_meta.get("degraded")),
                    "error": self.ai_meta.get("error") or "",
                    "judged": self.ai_meta.get("judged") or 0,
                    "attributed": self.ai_meta.get("attributed") or 0,
                    # R3：calls/requests 都是**物理模型请求**数（含重问/重试/回退），
                    # 不是逻辑调用数；tokens 为服务端回报的累计用量。
                    "requests": (self.ai_meta.get("budget") or {}).get(
                        "requests", (self.ai_meta.get("budget") or {}).get("calls", 0)),
                    "calls": (self.ai_meta.get("budget") or {}).get(
                        "requests", (self.ai_meta.get("budget") or {}).get("calls", 0)),
                    "tokens": (self.ai_meta.get("budget") or {}).get("tokens", 0),
                    "budget_approx": True,
                    "truthful": not self.ai_meta.get("degraded"),
                },
            }
            if self.status != "running":
                payload["summary"] = {
                    "ok": counts["ok"], "failed": counts["failed"],
                    "skipped": counts["skipped"], "total": total,
                    "failed_items": [
                        {"id": r["id"], "kind": r["kind"], "kind_label": r["kind_label"],
                         "account": r["account"], "title": r["title"],
                         "reason": r["detail"]}
                        for r in rows if r["status"] == "failed"
                    ],
                }
            return payload

    def request_stop(self) -> None:
        self._stop.set()

    def _live(self) -> dict:
        """当前子任务的实时进度 + 多步骤阶段表。

        ★ 2026-10-24：collect 一项就要 7 分钟（抓取 5s/条 + 商品 150s + 入账），
          这期间前端只有「已完成 0/3」，用户会判定「不能修复」。
        repair 的子 run 是 daily_batch，它的日志里带着真正 pipeline 的
        `run_id=<12位>`；顺着这个 id 再取 pipeline 的日志，就能算出走到哪一步。
        """
        rid = self.current_run_id
        if not rid:
            return dict(self._live_cache or {})
        run = runner.MANAGER.get(rid)
        if run is None:                     # 服务重启后管理器已不认这个 run
            return dict(self._live_cache or {"run_id": rid, "gone": True})
        snap = run.snapshot()
        db_lines = [str(x) for x in (snap.get("tail") or [])]
        pip_id = ""
        for line in reversed(db_lines):
            m = _PIPELINE_RUN_RE.search(line)
            if m:
                pip_id = m.group(1)
                break
        line = db_lines[-1] if db_lines else ""
        elapsed = float(snap.get("duration_s") or 0)
        pip_lines: list[str] = []
        if pip_id:
            p = runner.MANAGER.get(pip_id)
            if p is not None:
                ps = p.snapshot()
                pip_lines = [str(x) for x in (ps.get("tail") or [])]
                if pip_lines:
                    line = pip_lines[-1]
                elapsed = float(ps.get("duration_s") or elapsed)
        out = {"run_id": rid, "pipeline_run_id": pip_id, "item_id": self.current_id or "",
               "status": snap.get("status"), "elapsed_s": round(elapsed, 1),
               "stage": _stage_label(line), "line": (line or "")[:160],
               **_flow_steps(pip_lines, db_lines)}
        self._live_cache = out
        return out

    def _live_done(self) -> dict:
        """终态下的阶段表：保留最后一次快照并收口，别让最后一步永远转圈。

        - 会话成功 → 全部 done、百分比 100
        - 会话失败 → 停在第一个未完成步骤标 failed，其余保持 pending
        - 用户中止 → 同上但标 stopped
        """
        base = dict(self._live_cache or {})
        if not base:
            return {}
        steps = [dict(s) for s in (base.get("steps") or [])]
        res = self.results.get(base.get("item_id") or "") or {}
        final = "stopped" if self.status == "stopped" else (
            "failed" if res.get("status") == "failed" else "")
        if final:
            marked = False
            for s in steps:
                if s["state"] == "done":
                    continue
                if not marked:
                    s["state"] = final
                    marked = True
                else:
                    s["state"] = "pending"
            base["pct"] = sum(s["weight"] for s in steps if s["state"] == "done")
        else:
            for s in steps:
                s["state"] = "done"
            base["pct"] = 100
        base["steps"] = steps
        base["final"] = final or "ok"
        return base

    # ── 执行 ────────────────────────────────────────────────────
    def _mark(self, it: dict, status: str, detail: str, duration_s: float = 0.0,
              run_id: str = "", ai: dict | None = None) -> None:
        with self._lock:
            self.results[it["id"]] = {
                "status": status, "detail": detail,
                "duration_s": round(duration_s, 1), "run_id": run_id,
                "ai": ai or self.ai_meta["verdicts"].get(it["id"]) or {},
            }

    def _timeout_for(self, it: dict) -> int:
        """单项墙钟上限：显式 timeout 优先，否则按**有效动作**取默认值。

        ★ R2：旧实现按原始 kind 取默认值，AI 把 collect(3600s) 换成 transcribe(2400s)
          后超时仍是 3600s，两侧口径不一致。
        """
        if self.timeout:
            return int(self.timeout)
        return int(DEFAULT_TIMEOUT.get(effective_kind(it), 1800))

    def _run_cmd(self, it: dict, label: str, cmd: list[str]) -> tuple[bool, str, str]:
        timeout = float(self._timeout_for(it))
        title = f"一键修复 · {it['kind_label']} · {it.get('account') or label}"
        run = runner.MANAGER.start(kind="repair", title=title[:120], cmd=cmd,
                                   account=it.get("account") or "all",
                                   owner=self.lease)
        self.current_run_id = run.id
        deadline = time.time() + timeout
        while run.status == "running" and time.time() < deadline:
            if self._stop.is_set():
                runner.MANAGER.stop(run.id)
                break
            time.sleep(POLL_S)
        if run.status == "running":
            runner.MANAGER.stop(run.id)
            end = time.time() + STOP_GRACE_S
            while run.status == "running" and time.time() < end:
                time.sleep(0.2)
        snap = run.snapshot()
        if self._stop.is_set() and snap.get("status") == "stopped":
            return False, "用户中止", run.id
        if snap.get("status") == "running":
            return False, f"超时 {int(timeout)}s 未结束，已强制终止", run.id
        if snap.get("status") == "stopped":
            # 非用户中止的 stopped = 我们在 deadline 处主动停的（stop() 会抹掉 running 状态）
            return False, f"超时 {int(timeout)}s 未结束，已强制终止", run.id
        if snap.get("status") != "done" or (snap.get("exit_code") or 0) != 0:
            return False, f"{label}失败（exit={snap.get('exit_code')}）：{_tail_reason(snap)}", run.id
        dur = float(snap.get("duration_s") or 0)
        if dur < MIN_RUNTIME_S:
            return False, (f"{label}疑似空跑（{dur}s 就结束，多半是断点续跑把步骤全跳过了）；"
                           "请核对 pipeline_state 与 no_resume 参数"), run.id
        return True, "ok", run.id

    def _exec(self, it: dict) -> tuple[str, str, float]:
        t0 = time.time()
        try:
            steps = _steps_for(it, self.days, self.retry, self.timeout)
        except ValueError as e:
            return "failed", str(e), time.time() - t0
        run_ids = []
        for label, cmd in steps:
            self.current_step = label
            ok, detail, rid = self._run_cmd(it, label, cmd)
            if rid:
                run_ids.append(rid)
            if not ok:
                return "failed", detail, time.time() - t0
        ok, why = _verify(it)
        if not ok:
            return "failed", why, time.time() - t0
        return "ok", "、".join(l for l, _ in steps) + " 完成", time.time() - t0

    # ── AI 诊断（可选）：只改「怎么修」，不改「能不能修数据」────────
    def _diagnose(self) -> None:
        """跑一次批量判定，把结论写进 ai_meta['verdicts'] 与每个 item 的执行计划。

        ★ 失败/超时/预算不足一律降级为规则结论（degraded=True），修复照常进行。
        """
        todo = [it for it in self.items if it.get("repairable")]
        if not todo:
            self.ai_meta.update({"phase": "execute", "judged": 0})
            return
        self.ai_meta["phase"] = "diagnose"
        ai_repair.reset_budget()
        res = ai_repair.diagnose(todo, self.model or None)
        verdicts = res.get("items") or {}
        meta = res.get("meta") or {}
        self.ai_meta.update({
            "phase": "execute",
            "model": meta.get("model") or self.ai_meta["model"],
            "degraded": bool(meta.get("degraded")),
            "error": meta.get("error") or "",
            "judged": len(verdicts),
            "verdicts": verdicts,
            "budget": ai_repair.budget_snapshot(),
        })
        for it in todo:
            v = verdicts.get(it["id"])
            if not v:
                continue
            it["_ai_verdict"] = v
            if not v.get("applied"):
                # 置信度不足：保留规则动作，但把 AI 的顾虑显式记下来
                it["_ai_note"] = f"AI 置信度 {v['confidence']:.2f}，按规则动作执行"
                continue
            target = v.get("target_kind")
            if target is None:
                it["repairable"] = False
                it["skip_reason"] = f"AI 判定（{v['action_label']}）：{v['reason']}"
            else:
                it["_ai_kind"] = target

    def _attribute(self, it: dict, detail: str) -> dict | None:
        """失败归因：只在开了 AI 且有预算时做，失败静默跳过。"""
        if not self.use_ai:
            return None
        try:
            attr = ai_repair.attribute_failure({**it, "detail": detail}, detail, self.model or None)
        except Exception:                   # noqa: BLE001  归因不该影响主流程
            return None
        if not attr:
            return None
        self.ai_meta["attributed"] = int(self.ai_meta.get("attributed") or 0) + 1
        self.ai_meta["budget"] = ai_repair.budget_snapshot()
        return attr

    def run(self) -> None:
        try:
            if self.use_ai:
                try:
                    self._diagnose()
                except Exception as e:      # noqa: BLE001  AI 挂了也必须照常修
                    self.ai_meta.update({"phase": "execute", "degraded": True,
                                         "error": f"{type(e).__name__}: {e}"[:200]})
            else:
                self.ai_meta["phase"] = "execute"
            for it in self.items:
                if self._stop.is_set():
                    self._mark(it, "skipped", "用户中止，未执行")
                    continue
                if not it.get("repairable"):
                    self._mark(it, "skipped", it.get("skip_reason") or "不可自动修复")
                    continue
                self.current_id = it["id"]
                self.current_step = ""
                try:
                    status, detail, dur = self._exec(it)
                except Exception as e:      # 单项异常不能中断整条修复链
                    status, detail, dur = "failed", f"{type(e).__name__}: {e}", 0.0
                ai_row = self.ai_meta["verdicts"].get(it["id"]) or {}
                if status == "failed":
                    attr = self._attribute(it, detail)
                    if attr:
                        ai_row = {**ai_row, "attribution": attr}
                        detail = f"{detail} ｜ AI 归因：{attr['cause']} → {attr['suggestion']}"
                self._mark(it, status, detail, dur, ai=ai_row or None)
            self.ai_meta["phase"] = "done"
            self.ai_meta["budget"] = ai_repair.budget_snapshot()
        finally:
            with self._lock:
                self.current_id = None
                self.current_step = ""
                self.status = "stopped" if self._stop.is_set() else "done"
                self.ended_at = time.time()


# ── 模块级入口 ─────────────────────────────────────────────────────
def is_running() -> bool:
    return bool(_SESSION and _SESSION.status == "running")


def start(plan: dict, ids: list[str] | None = None, days: int = 7,
          retry: int = 1, timeout: int | None = None, use_ai: bool = False,
          model: str = "") -> dict:
    """按计划启动一次修复。并发冲突 / 无可修项 → ValueError（路由层转 400）。

    ★ 独立验收 R1：并发保护不能只是「启动前看一眼」。这里向共享执行层申请
      `ledger` 写者租约，**检查与获取在同一把锁内**，与日更/流水线/台账入口
      双向互斥；修复自己的子进程通过 owner=租约 正常执行。
    """
    global _SESSION
    busy = [r for r in runner.MANAGER.running()
            if r.get("kind") in ("pipeline", "daily", "ledger")]
    if busy:
        names = "、".join(sorted({str(r.get("kind")) for r in busy}))
        raise ValueError(f"有 {len(busy)} 个流水线/台账任务正在运行（{names}），"
                         "请等它结束再修复，避免并发写同一本台账")
    wanted = {str(x) for x in (ids or []) if x}
    rows = []
    for raw in plan.get("items") or []:
        if wanted and raw.get("id") not in wanted:
            continue
        rows.append(dict(raw))
    if not any(r.get("repairable") for r in rows):
        raise ValueError("没有可自动修复的问题（其余项需要人工处理，见跳过原因）")
    with _LOCK:
        if _SESSION and _SESSION.status == "running":
            raise ValueError("已有修复任务在进行，请等它结束")
        lease = runner.RESOURCES.acquire((runner.LEDGER,), kind="repair",
                                         title="一键修复")
        try:
            sess = RepairSession(rows, days=max(1, int(days)), retry=max(1, int(retry)),
                                 timeout=int(timeout) if timeout else None,
                                 use_ai=use_ai, model=model, lease=lease)
        except Exception:
            runner.RESOURCES.release(lease)
            raise
        _SESSION = sess
    threading.Thread(target=_run_and_release, args=(sess,), daemon=True).start()
    return sess.snapshot()


def _run_and_release(sess: "RepairSession") -> None:
    """跑完会话并保证释放写者租约（即使 run 被测试替身替换或抛异常）。"""
    try:
        sess.run()
    finally:
        runner.RESOURCES.release(sess.lease)


def status() -> dict:
    sess = _SESSION
    if not sess:
        return {"status": "idle", "total": 0, "done": 0, "percent": 0,
                "counts": {"ok": 0, "failed": 0, "skipped": 0}, "items": []}
    return sess.snapshot()


def stop() -> dict:
    sess = _SESSION
    if not sess or sess.status != "running":
        return status()
    sess.request_stop()
    if sess.current_run_id:
        try:
            runner.MANAGER.stop(sess.current_run_id)
        except Exception:
            pass
    return sess.snapshot()
