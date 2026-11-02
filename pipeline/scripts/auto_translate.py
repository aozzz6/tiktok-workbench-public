# -*- coding: utf-8 -*-
"""auto_translate.py — 本地 LLM 自动翻译回填台账 N 列（run 钩子 / 手动触发通用）

背景（2026-10-16）：15 分钟→2 分钟巡检 automation 实际不触发，翻译长期堆积。
改为「run 完成即触发本地 LLM 翻译」，不再依赖周期调度。

流程:
  1. 直扫台账「音频(M)有值 且 翻译(N)为空」的行 = 待译
  2. 调 LLM（模型取自 ~/.workbuddy/models.json）批量翻译成简体中文
  3. 写 output/trans_apply_auto.json → build_ledger_15col.py apply-trans 写 N 列
  4. 汇报 {translated, accounts, model, ok}

用法:
  python auto_translate.py                       # 全量待译
  python auto_translate.py --accounts a b        # 只译指定账号
  python auto_translate.py --model "TM ·Fast"    # 指定模型
  python auto_translate.py --dry-run             # 只翻译不写盘
退出码: 0 = 成功（含无待译）；1 = 失败
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
PY = str(PIPE / ".venv" / "Scripts" / "python.exe")
BUILDER = PIPE / "scripts" / "build_ledger_15col.py"
MODELS_JSON = Path(os.path.expanduser("~")) / ".workbuddy" / "models.json"
BATCH = 18          # 每批条数（避免单请求过大）
TIMEOUT = 240

sys.path.insert(0, str(PIPE / "scripts"))
import openpyxl  # noqa: E402


def load_cfg() -> dict:
    return json.loads((PIPE / "accounts.json").read_text(encoding="utf-8"))


def load_model(model_id: str | None = None):
    """从 models.json 取模型配置；缺省优先 tokenmao Auto/Fast，再退 deepseek。"""
    if not MODELS_JSON.exists():
        raise SystemExit(f"未找到模型配置: {MODELS_JSON}")
    d = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else d.get("models", [])
    if model_id:
        m = next((x for x in items if x.get("id") == model_id), None)
        if not m:
            raise SystemExit(f"模型不存在: {model_id}；可选: {[x.get('id') for x in items]}")
        return m
    for pref in ("TM ·Auto", "TM ·Fast", "deepseek-flash"):
        m = next((x for x in items if x.get("id") == pref
                  and x.get("apiKey") and x.get("url")), None)
        if m:
            return m
    m = next((x for x in items if x.get("apiKey") and x.get("url")), None)
    if not m:
        raise SystemExit("models.json 无可用模型（缺 apiKey/url）")
    return m


def scan_pending(scope: list[str] | None = None) -> dict:
    """{账号: [{row, vid, text}]} —— 台账中 M 有值而 N 为空的待译行。"""
    cfg = load_cfg()
    xlsx = Path(cfg["xlsx"])
    if not xlsx.exists():
        raise SystemExit(f"台账不存在: {xlsx}")
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    out: dict = {}
    for ws in wb.worksheets:
        if not ws.title.startswith("对标记录-"):
            continue
        acc = ws.title.replace("对标记录-", "")
        if scope and acc not in scope:
            continue
        hdr = [str(c.value or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1), [])]
        mi = next((i for i, h in enumerate(hdr) if h in ("音频", "备注")), 12)
        ni = next((i for i, h in enumerate(hdr) if h in ("翻译", "备注中文翻译")), 13)
        li = next((i for i, h in enumerate(hdr) if "视频链接" in h), 5)
        need = max(mi, ni, li)
        lst = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            row = list(row)
            if len(row) <= need:
                row = row + [None] * (need + 1 - len(row))
            m = str(row[mi] or "").strip()
            n = str(row[ni] or "").strip()
            if m and not n:
                s = str(row[li] or "")
                mm = (re.search(r"ID:\s*\(?\s*(\d{15,25})", s)
                      or re.search(r"/video/(\d{15,25})", s))
                lst.append({"row": i, "vid": mm.group(1) if mm else "", "text": m})
        if lst:
            out[acc] = lst
    wb.close()
    return out


SYS_PROMPT = (
    "你是 TikTok 带货视频口播文案的翻译员。把每条原文翻译成简体中文，"
    "源语言可能是英文、西班牙语、意大利语或中文。要求：\n"
    "1) 译文口语化、自然，贴合带货短视频语境（像主播在推荐产品）；\n"
    "2) 品牌名 / 型号 / 尺码单位保留原文（如 Tilewood、PITO、UK size）；\n"
    "3) 原文是 ASR 转写、常有同音错字或机器腔，请按大意意译，不要逐字硬译；\n"
    "4) 若原文本身是中文，只做顺句、去机器腔，不改变意思；\n"
    "5) 不添加原文没有的功效、数据或价格。\n"
    "严格只输出 JSON，格式为 {\"账号名\":{\"行号\":\"译文\"}}，键为字符串形式的行号，不要输出任何解释或代码块标记。"
)


def call_llm(model: dict, batch: list[dict]) -> dict:
    """batch: [{account,row,text}] → {account: {str(row): 译文}}"""
    payload = [{"account": b["account"], "row": b["row"], "text": b["text"]} for b in batch]
    body = json.dumps({
        "model": model["id"],
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.3,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        model["url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + model["apiKey"]})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8"))
    txt = d["choices"][0]["message"]["content"]
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        raise RuntimeError(f"LLM 未返回 JSON: {txt[:200]}")
    return json.loads(txt[i:j + 1])


def translate_chunk(model: dict, chunk: list[dict], depth: int = 0) -> dict:
    """★ 2026-10-29：带覆盖校验的翻译——大批次（18 条）实测会出现响应截断/
    非法 JSON（或个别行缺失）。失败或行数不全时自动**减半**重试，最小到单条；
    缺行、行损坏不再静默丢。"""
    allowed = {(b["account"], str(b["row"])) for b in chunk}
    try:
        part = call_llm(model, chunk)
        if not isinstance(part, dict):
            raise RuntimeError("返回结构不符")
        # ★ 2026-10-29 实测：模型常省略账号层，返回扁平 {"行号": "译文"}；
        #   chunk 内只涉及一个账号时可直接收敛为嵌套结构再校验。
        all_flat = part and all(isinstance(v, str) for v in part.values())
        single_acc = len({b["account"] for b in chunk}) == 1
        if all_flat and single_acc:
            part = {chunk[0]["account"]: part}
        got = set()
        for acc, rows in part.items():
            if isinstance(rows, dict):
                got |= {(acc, str(r)) for r in rows if str(r).strip()}
        missing = allowed - got
        if missing:
            raise RuntimeError(f"覆盖不全（缺 {len(missing)} 行）")
        return part
    except Exception as e:
        if depth >= 3 or len(chunk) <= 1:
            raise
        mid = len(chunk) // 2
        print(f"[retry] 批次失败({str(e)[:80]})，减半为 {len(chunk[:mid])}+{len(chunk[mid:])} 重试",
              file=sys.stderr)
        merged = translate_chunk(model, chunk[:mid], depth + 1)
        merged.update(translate_chunk(model, chunk[mid:], depth + 1))
        return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 LLM 自动翻译回填台账 N 列")
    ap.add_argument("--accounts", nargs="*", default=None)
    ap.add_argument("--model", default=None, help="模型 id（缺省 TM ·Auto）")
    ap.add_argument("--limit", type=int, default=0, help="最多译多少条（0=不限）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.limit < 0:
        ap.error("--limit 不能为负数")

    pending = scan_pending(a.accounts)
    flat = [{"account": acc, **it} for acc, lst in pending.items() for it in lst]
    if a.limit:
        flat = flat[:a.limit]
    total = len(flat)
    if not total:
        print(json.dumps({"translated": 0, "accounts": [], "model": a.model,
                          "note": "无待译条目", "ok": True}, ensure_ascii=False))
        return 0
    model = load_model(a.model)

    result: dict = {}
    done = 0
    for k in range(0, total, BATCH):
        chunk = flat[k:k + BATCH]
        try:
            part = translate_chunk(model, chunk)   # ★ 2026-10-29 覆盖校验+自动减半
        except Exception as e:
            print(f"[warn] 批次 {k // BATCH + 1} 彻底失败: {str(e)[:150]}", file=sys.stderr)
            continue
        if not isinstance(part, dict):
            print(f"[warn] 批次 {k // BATCH + 1} 返回结构不符", file=sys.stderr)
            continue
        allowed = {(b["account"], str(b["row"])) for b in chunk}
        for acc, rows in part.items():
            if not isinstance(rows, dict):
                continue
            for rk, rv in rows.items():
                if ((acc, str(rk)) in allowed and isinstance(rv, str) and rv.strip()):
                    result.setdefault(acc, {})[str(rk)] = rv.strip()

    done = sum(len(rows) for rows in result.values())
    complete = done == total

    accounts = sorted(result.keys())
    if a.dry_run:
        print(json.dumps({"translated": done, "accounts": accounts, "model": model["id"],
                          "requested": total, "dry_run": True, "ok": complete}, ensure_ascii=False))
        return 0 if complete else 1

    if not result:
        print(json.dumps({"translated": 0, "accounts": [], "model": model["id"],
                          "note": "LLM 未产出译文", "ok": False}, ensure_ascii=False))
        return 1

    expected = {}
    for item in flat:
        expected.setdefault(item["account"], {})[str(item["row"])] = {
            "vid": item["vid"], "text": item["text"]}
    # 每次运行独立文件；写表前复核原文，避免生成期间行移动/人工补译被旧结果覆盖。
    output_dir = PIPE / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="translate_", dir=output_dir) as td:
        out = Path(td) / "result.json"
        source = Path(td) / "source.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        source.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([PY, str(BUILDER), "apply-trans", str(out),
                               "--expected-source", str(source)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = proc.returncode == 0
    tail = "\n".join([l for l in (proc.stdout or "").strip().splitlines() if l.strip()][-2:])
    if not ok:
        print(f"[error] apply-trans 失败: {tail or (proc.stderr or '')[-200:]}", file=sys.stderr)
        return 1
    print(json.dumps({"translated": done, "accounts": accounts, "model": model["id"],
                      "requested": total, "apply": tail[:120], "ok": complete},
                     ensure_ascii=True))  # ★ 2026-10-29 GBK 控制台/管道不再炸
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
