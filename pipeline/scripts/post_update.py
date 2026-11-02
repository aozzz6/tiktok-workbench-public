#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""post_update.py — 日更后 Agent 侧确定性步骤打包器（2026-10-10 第 3 批优化）

背景（审查报告 P7）：09:00 自动化的台账写入/翻译交接/校验原来以散文 prompt 描述，
LLM 每天按文字复述执行——脚本演进后 prompt 会漂移，且执行路径不确定。
本脚本把其中**不需要 LLM 智能的部分**收敛为确定性命令；LLM 只保留翻译本体。

子命令:
  write                      同步台账（委托 build_ledger_15col.py write，9 账号幂等对账）
  export-trans [--accounts …] 导出 N 列待译清单 -> output/pending_trans.json
  apply-trans <json>         把 {账号: {行号: 译文}} 写入 N 列
  verify [--skip-api]        终验：① /api/overview（服务在线时；端口按 8789→8791→8792→
                             8793→8787 探测，全不通回退 8787）9 账号 gaps；
                             ② openpyxl 复读台账（行数 >= scrape 产物、N 列空缺清单）
                             输出末行 JSON 摘要；rc=1 表示有 ERROR 级问题

本脚本不引入新逻辑——write/export/apply 全部委托 build_ledger_15col.py（单一事实源），
verify 是原自动化 prompt 第 5 步散文（curl + openpyxl 复读）的脚本化。

用法（自动化 prompt 侧）:
  python scripts/post_update.py write
  python scripts/post_update.py export-trans
  # ...LLM 翻译 pending_trans.json -> trans_result.json...
  python scripts/post_update.py apply-trans output/trans_result.json
  python scripts/post_update.py verify
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from desc_split import COL_NOTE, COL_NOTE_ZH  # noqa: E402  13 列口径（单一事实源）
from build_ledger_15col import valid_header, _valid_img_header, vid_of

PY = str(PIPE / ".venv" / "Scripts" / "python.exe")
BUILDER = PIPE / "scripts" / "build_ledger_15col.py"
CFG_PATH = PIPE / "accounts.json"


# ── 工作台端口解析（2026-11-02，T-04）─────────────────────────────────
# 8787 被收掉之后，写死 `http://127.0.0.1:8787` 会让 check_api 永远连不上 →
# 捕获异常后恒返回 skipped（回执是 INFO，其实 API 校验这一层根本没跑：静默失效）。
# 改成与 pipeline/scripts/daily_batch.py 和 webapp/_launch.py 同序的端口优先链：
#   8789（当前自启/快捷方式）→ 8791 → 8792 → 8793 → 8787（app.py 自带默认值）
# 每个端口探一次 /api/health，取第一个「像工作台」的；全不通回退 8787，
# 这样「服务没开」仍然是 skipped（不算失败）的原语义不变。
PORT_CANDIDATES = (8789, 8791, 8792, 8793, 8787)
FALLBACK_PORT = 8787                                  # app.py 的默认端口
HEALTH_TIMEOUT = 2.0


def _looks_like_workbench(port: int, timeout: float = HEALTH_TIMEOUT) -> bool:
    """health 指纹判定（与 daily_batch._looks_like_workbench 同口径）。

    只看 `"ok": true` 不够：本机 8790/8788 是「token 余额监控」（也返回 ok:true，
    字段是 db/upstream），8787 还可能被 reporter.js 抢占（返回 1x1 GIF）。
    工作台的 health 必带 ts + diagnostics.pipeline_dir/ledger_files。

    用空 ProxyHandler 绕过系统代理：本机回环不该走代理，而 config.run_env() 会给
    pipeline 子进程塞 HTTP(S)_PROXY（daily_batch 也这么做，同一套原因）。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            # 2026-11-02（T-04 评审）：不要只读前 8KB。/api/health 会带
            # diagnostics.ledger_files，台账文件多/路径长时响应超过 8KB，
            # 截断后 json.loads 失败 → 真工作台被判成「不是工作台」→ 整条链
            # 回退 8787 → check_api 又静默 skipped（正是本任务要修的那类复发）。
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:                                 # noqa: BLE001  超时/重置/非 JSON 一律不算
        return False
    if not isinstance(data, dict) or data.get("ok") is not True or "ts" not in data:
        return False
    diag = data.get("diagnostics")
    return isinstance(diag, dict) and "pipeline_dir" in diag and "ledger_files" in diag


def resolve_workbench_port(probe=None, candidates=PORT_CANDIDATES,
                           fallback: int = FALLBACK_PORT) -> int:
    """端口优先链（probe 可注入，便于单测）：第一个 probe(port) 为真的端口；全不通回退 8787。

    单个端口探测抛异常时跳过它继续往后试——一个端口的连接被重置不该让整条链失效。
    """
    probe = probe if probe is not None else _looks_like_workbench
    for port in candidates:
        try:
            if probe(port):
                return port
        except Exception:                             # noqa: BLE001
            continue
    return fallback


def workbench_base(probe=None) -> str:
    """工作台 base URL（无尾部斜杠），端口由 resolve_workbench_port 决定。"""
    return f"http://127.0.0.1:{resolve_workbench_port(probe)}"


def _cell(v) -> str:
    """单元格取文本（None → 空串，非字符串先转字符串再 strip）。"""
    return "" if v is None else str(v).strip()


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def run_builder(args: list[str]) -> int:
    """委托 build_ledger_15col.py（单一事实源，本脚本不复制其逻辑）。"""
    cmd = [PY, str(BUILDER)] + args
    log("运行: " + " ".join(cmd[2:]))
    proc = subprocess.run(cmd)
    return proc.returncode


# ────────────────────────── verify ──────────────────────────

def check_api(scope: list[str] | None = None, cfg: dict | None = None,
              base: str | None = None) -> dict:
    """工作台 overview 校验。服务不在线返回 skipped（不算失败）。

    base：工作台地址；缺省用 workbench_base() 的端口优先链（8789 → … → 8787）。

    ★ 2026-10-21 验收 B（停用账号顶红）：旧实现 scope=None 时遍历 overview 全部账号，
      与 check_ledger 的 active/ledger_skip 口径不一致 —— 一个停用账号
      （accounts.json `active:false`，overview 里 `active:true, enabled:false`）
      就能把 check_api 判成 error → post_update rc=1 → 日更的 ledger_verify 恒 ERROR。

      注意 overview 的 `active` 是「有产物」语义，不是配置开关；这里必须按
      accounts.json 的 **active + ledger_skip** 计算「启用名单」，再限定终验范围。
      停用账号的历史产物仍在 overview/台账里可见，只是不再参与终验判定。
    """
    url = f"{base or workbench_base()}/api/overview?refresh=true"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"[warn] 工作台不可达（{url}）：{str(e)[:80]}；跳过 API 校验，仅磁盘核对")
        return {"status": "skipped", "reason": str(e)[:120]}
    accounts = data.get("accounts") or []
    # 启用名单：与 check_ledger 完全同口径（active 缺省视为启用；ledger_skip 排除）
    cfg = cfg or load_cfg()
    enabled = {a.get("account") for a in cfg.get("accounts", [])
               if a.get("active", True) and not a.get("ledger_skip", False)}
    seen = len(accounts)
    disabled = sorted({str(a.get("account")) for a in accounts
                       if a.get("account") and a.get("account") not in enabled})
    accounts = [a for a in accounts if a.get("account") in enabled]
    if scope is not None:
        accounts = [a for a in accounts if a.get("account") in scope]
    bad, warned = [], []
    for a in accounts:
        name = a.get("account", "?")
        sev = a.get("severity")
        gaps = a.get("gaps") or []
        row = {"account": name, "severity": sev,
               "gaps": [f"{g.get('label')}:{g.get('msg')}" for g in gaps]}
        # ★ 口径对齐（2026-10-17）：severity 由工作台聚合，info 级 gap（翻译待回填、
        # 商品未提取、无小黄车）已被排除；这里只认 severity，避免把正常待办当 ERROR。
        # ★ 2026-10-28：只有 severity=error 才算 ERROR，warn 单列进 warn（不算失败）。
        #   以前 warn 也当 error，于是任何一个启用账号「商品提取不全 / 抓取未全部入账」
        #   都会让 verify 恒 rc=1 → 回执永远 failed_stages=[ledger_verify] → 工作台
        #   待接管面板长期「回执存疑」且一键修复恒为 0 项。warn 是工作台自己的
        #   「待处理」级（正常业务待办），与 ERROR 不是一回事。
        if sev == "error":
            bad.append(row)
        elif sev not in (None, "ok"):
            warned.append(row)
    return {"status": "ok" if not bad else "error", "bad": bad, "warn": warned,
            "checked": len(accounts), "scope_basis": "accounts.json active+ledger_skip",
            "skipped_disabled": disabled, "overview_total": seen}


def check_ledger(cfg: dict | None = None, scope: list[str] | None = None) -> dict:
    """openpyxl 复读台账：行数对账 + N 列空缺清单。纯只读。"""
    import openpyxl
    cfg = cfg or load_cfg()
    accounts = [a for a in cfg.get("accounts", []) if a.get("active", True) and not a.get("ledger_skip", False)]
    if scope is not None:
        known = {a["account"] for a in accounts}
        if any(a not in known for a in scope):
            return {"status": "error", "error": "指定账号不在台账范围"}
        accounts = [a for a in accounts if a["account"] in scope]
    need_video = any(a.get("media_type") != "image" for a in accounts)
    need_image = any(a.get("media_type") == "image" for a in accounts)
    ledger = Path(cfg.get("xlsx") or "")
    if need_video and not ledger.is_file():
        return {"status": "error", "error": f"视频台账不存在: {ledger}"}
    img_ledger = Path(cfg.get("img_xlsx") or "")
    if need_image and (not cfg.get("img_xlsx") or img_ledger.resolve() == ledger.resolve()):
        return {"status": "error", "error": "img_xlsx 必须为独立图文文件"}
    if need_image and not img_ledger.is_file():
        return {"status": "error", "error": f"图文台账不存在: {img_ledger}"}
    targets = ([ledger] if need_video else []) + ([img_ledger] if need_image else [])
    if any(list(p.parent.glob("~$*")) for p in targets):
        return {"status": "error", "error": "台账被占用（~$ 锁文件存在，Excel/WPS 未关闭？）"}
    wb = openpyxl.load_workbook(ledger, read_only=True) if need_video else None
    img_wb = openpyxl.load_workbook(img_ledger, read_only=True) if need_image else None
    details, bad = [], []
    for acc_cfg in accounts:
        acc = acc_cfg["account"]
        is_img = acc_cfg.get("media_type") == "image"
        sn = "图文账号记录" if is_img else f"对标记录-{acc}"   # 图文账号共用一张（B 列区分账号）
        ws_src = img_wb if (is_img and img_wb is not None) else wb   # 图文查独立文件 img_xlsx
        # scrape 产物行数基准
        out = Path(acc_cfg.get("out_dir") or PIPE / "output")
        sp = out / f"scrape_{acc}.json"
        if sn not in ws_src.sheetnames:
            details.append({"account": acc, "sheet": "MISSING", "rows": 0,
                            "scrape_items": None, "n_empty": None,
                            "media_type": "image" if is_img else "video"})
            bad.append(acc)
            continue
        if not sp.exists():
            # 与 write 阶段保持一致：没有本轮抓取产物的账号不参与行数对账。
            details.append({"account": acc, "status": "skipped",
                            "reason": "scrape artifact missing", "rows": None,
                            "scrape_items": None})
            continue
        try:
            expected = {str(i["id"]) for i in json.loads(sp.read_text(encoding="utf-8"))
                        .get("items", []) if i.get("id")}
            items_n = len(expected)
        except Exception as e:
            details.append({"account": acc, "status": "error",
                            "reason": f"scrape artifact unreadable: {e}",
                            "rows": None, "scrape_items": None})
            bad.append(acc)
            continue
        ws = ws_src[sn]
        rows, n_empty, actual = 0, [], []
        header_ok = _valid_img_header(ws) if is_img else valid_header(ws)
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            # 图文共用表：按 B 列「对标账号」只统计本账号的行（避免与其他图文账号互串）
            if is_img:
                if len(row) < 2 or str(row[1] or "").lstrip("@").strip() != acc:
                    continue
                rows += 1
                v = vid_of(row[5]) if len(row) > 5 else None
                if v:
                    actual.append(v)
                continue
            rows += 1
            v = vid_of(row[5]) if len(row) > 5 else None
            if v:
                actual.append(v)
            # 视频表：N 列（备注中文翻译）空缺检查（图文表无音频/翻译列，已在上方 return）
            # ★ 2026-10-14：13 列改版后 备注=L(12)、备注中文翻译=M(13)。
            #   越界一律视为空，避免短行 IndexError 导致误判。
            padded = list(row) + [None] * (COL_NOTE_ZH - len(row))
            m, n = _cell(padded[COL_NOTE - 1]), _cell(padded[COL_NOTE_ZH - 1])
            if m and not n:
                n_empty.append(i)
        short = rows < items_n
        missing = sorted(expected - set(actual))
        duplicates = len(actual) - len(set(actual))
        details.append({"account": acc, "rows": rows, "scrape_items": items_n,
                        "rows_short": short, "n_empty_count": len(n_empty),
                        "n_empty_rows": n_empty[:20],
                        "missing_ids": missing, "duplicate_ids": duplicates, "header_ok": header_ok,
                        "media_type": "image" if is_img else "video"})
        if short or missing or duplicates or not header_ok:
            bad.append(acc)
    if wb is not None:
        wb.close()
    if img_wb is not None:
        img_wb.close()
    return {"status": "ok" if not bad else "error", "bad": bad, "accounts": details}


def main_verify(skip_api: bool, scope: list[str] | None = None) -> int:
    api = {"status": "skipped", "reason": "--skip-api"} if skip_api else (check_api(scope) if scope is not None else check_api())
    ledger = check_ledger(scope=scope) if scope is not None else check_ledger()
    level = "ERROR" if "error" in (api.get("status"), ledger.get("status")) else "INFO"
    print(json.dumps({"level": level, "api": api, "ledger": ledger,
                      "date": dt.date.today().isoformat()},
                     ensure_ascii=False, indent=1))
    # 人读摘要
    if ledger.get("status") == "ok":
        for d in ledger["accounts"]:
            extra = f" N空缺={d['n_empty_count']}" if d.get("n_empty_count") is not None else ""
            warn = " ⚠行数不足" if d.get("rows_short") else ""
            print(f"  {d['account']}: 台账 {d['rows']} 行 / scrape {d['scrape_items']}{extra}{warn}")
    print(f"verify: {level}")
    return 1 if level == "ERROR" else 0


# ────────────────────────── CLI ──────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="日更后 Agent 侧确定性步骤打包器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="同步台账（委托 build_ledger_15col write）")
    w.add_argument("--accounts", nargs="*", default=None)
    e = sub.add_parser("export-trans", help="导出 N 列待译清单")
    e.add_argument("--accounts", nargs="*", default=None)
    a = sub.add_parser("apply-trans", help="写入 N 列译文")
    a.add_argument("json_file")
    v = sub.add_parser("verify", help="终验（API + 台账磁盘复读）")
    v.add_argument("--skip-api", action="store_true", help="跳过工作台 API 校验")
    v.add_argument("--accounts", nargs="*", default=None, help="仅核验指定账号")
    args = ap.parse_args()
    if hasattr(args, "accounts") and args.accounts == []:
        print("[error] --accounts 需要至少一个账号，未执行")
        return 1

    if args.cmd == "write":
        extra = ["--accounts", *args.accounts] if args.accounts else []
        return run_builder(["write"] + extra)
    if args.cmd == "export-trans":
        extra = ["--accounts", *args.accounts] if args.accounts else []
        return run_builder(["export-trans"] + extra)
    if args.cmd == "apply-trans":
        return run_builder(["apply-trans", args.json_file])
    if args.cmd == "verify":
        return main_verify(args.skip_api, args.accounts)
    return 1


if __name__ == "__main__":
    sys.exit(main())
