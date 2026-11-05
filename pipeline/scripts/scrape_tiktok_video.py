# -*- coding: utf-8 -*-
"""
scrape_tiktok_video.py — Step2 页面抓取（v1 规格：静态抓内嵌 JSON，2s 限流）

输入: --account --audio-dir（从产物文件名取视频 ID）或 --ids
输出: scrape_{account}.json + stdout 末行 JSON 摘要
摘要 schema（对齐 gate_scrape）:
  {"errors": [{"id","error","reason"}], "items": [{"id","publish_time_gmt8",...}],
   "reasons": {"blocked": N, "removed": M}}
规则（2026-10-24 D2 修正）: 失败分两类，别再混成一个「下架/私有」
  blocked = 页面里没有 __UNIVERSAL_DATA_FOR_REHYDRATION__（风控页/挑战页），**可重试**
  removed = 有内嵌数据但 itemStruct 空（真下架/私有/地区限制），重试无意义
  旧实现两类都写「itemStruct empty（下架/私有）」，实测 381/430 全是这句，
  抽检被判"下架"的视频其实在线（459 播放）→ 指标与修复全被带偏。
★ 2026-11-02（T-02）: 失败文本必须带页面真实 statusCode/statusMsg。
  statusCode 100001（RPC 网络错误，瞬断）归 blocked，走既有 --blocked-retry 重试；
  10204（author_status,status_reviewing 作者审核中）归类仍 removed（PRD 关键决定 1），
  只让错误文本说真话——以前 49 条 10204 全写成「已下架/私有」，排查方向被带偏。
★ 2026-11-02（T-03）: 本次 0 条有效且 errors 非空 → **不写盘**，保留原有的
  scrape_{account}.json 原样不动；stdout 摘要照常输出本次结果，stderr 摘要行标注
  「未写盘」，退出码仍为 0（0 条该由 gate_scrape 判 FAIL，不由本脚本判）。
  事故：全量抓取全失败时整体覆盖，把上一次的历史条目冲掉了——抓得越失败毁得越多
  （交接文档 2026-11-02 第 3 节）。--merge 不受影响：合并结果含历史，照常写盘。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from proxy_detect import detect_proxy  # noqa: E402
PROXY = os.environ.get("WB_PROXY") or detect_proxy()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
RATE_LIMIT_SEC = float(os.environ.get("WB_SCRAPE_INTERVAL", "5"))
# 2026-10-05 由 2s 提到 5s：2s 连抓 30+ 条实测大面积触发风控（返回无内嵌数据的风控页，
# 被误判"itemStruct empty"）。2026-10-24 起可用 --interval / WB_SCRAPE_INTERVAL 覆盖，
# 慢速补抓（retry_scrape_failed.py）与实测对比都走同一入口。
BLOCKED_RETRY = int(os.environ.get("WB_SCRAPE_BLOCKED_RETRY", "1"))
BLOCKED_SLEEP = float(os.environ.get("WB_SCRAPE_BLOCKED_SLEEP", "8"))
# 命中风控页时：默认再等 8s 重取 1 次（风控多为瞬时），失败才记 blocked。
# 工作台批量跑不会因此爆炸性变慢：每条 blocked 最多多花 8s，且 blocked 明显减少。
GMT8 = dt.timezone(dt.timedelta(hours=8))  # 统一以 UTC 为基准换算 GMT+8

UNIVERSAL_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)

REASON_TEXT = {
    "blocked": "风控页（无内嵌数据，可重试）",
    "removed": "itemStruct 空（已下架/私有）",
}

# ★ 2026-11-02（T-02）：网络类 statusCode 归可重试（blocked）。
#   100001 = RPC 网络错误（瞬断），实测混在一批 10204 之间出现；旧实现归 removed，
#   从此不再重试——一条瞬时抖动被当成永久失败。10204 不在本集合里，归类不变。
NETWORK_STATUS_CODES = {"100001"}


def fetch_page(url: str) -> str:
    """统一抓取层（curl_cffi chrome124 指纹 + 代理）+ 退避重试
    （TikTok 间歇性 SSL 掐断/风控 → 重试）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tiktok_fetch import fetch_html
    last = None
    for attempt in range(3):
        try:
            return fetch_html(url)
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def browser_batch_fallback(account: str, vids: list[str]) -> dict[str, dict]:
    """★ 2026-10-29 浏览器兜底：TikTok WAF 波及详情页 SSR 时（恒返挑战页，
    指纹/重试/cookie 均无效），用系统 Python 的 playwright 批量抓 itemStruct。
    返回 {vid: {"status": "ok"|"removed"|"blocked", "item": ...}}，异常返回 {}。"""
    import subprocess
    if not vids:
        return {}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tiktok_fetch import find_playwright_python
    helper = Path(__file__).resolve().parent / "pw_detail_fallback.py"
    # 2026-11-04：原来按 shutil.which 挑解释器，本机全部命中 WindowsApps 商店占位符，
    # 必然失败；改由 tiktok_fetch.find_playwright_python() 实跑验证。
    prefix = find_playwright_python()
    if not prefix:
        print("[warn] 浏览器兜底未找到可用 Python（系统 Python 需装 playwright，"
              "或用 WB_PW_PYTHON 指定解释器），跳过", file=sys.stderr)
        return {}
    cmd = prefix + [str(helper), account, *vids]
    env = {k: v for k, v in os.environ.items() if k != "PLAYWRIGHT_BROWSERS_PATH"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env,
                           encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[warn] 浏览器兜底进程异常: {e}", file=sys.stderr)
        return {}
    if r.returncode != 0:
        print(f"[warn] 浏览器兜底失败: {r.stderr.strip()[-200:]}", file=sys.stderr)
        return {}
    try:
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        return {str(rec["id"]): rec for rec in payload.get("results", []) if rec.get("id")}
    except Exception as e:
        print(f"[warn] 浏览器兜底输出无法解析: {e}", file=sys.stderr)
        return {}


def parse_item_detail(html: str) -> tuple[dict | None, str, int | str | None, str]:
    """解析页面，返回 (itemStruct, reason, statusCode, statusMsg)。

    reason 与 parse_item_ex 完全一致："" 成功 / "blocked" 可重试 / "removed" 真下架。
    statusCode / statusMsg 取 webapp.video-detail 的原始值，失败时写进 errors[].error。
    ★ 2026-11-02（T-02）：新增函数。以前只有 (item, reason)，丢了 statusCode 与
      statusMsg，49 条 10204（author_status,status_reviewing 作者审核中）全被写成
      「已下架/私有」，看报告的人查错方向（交接文档 2026-11-02 第 2 点）。
      parse_item_ex 的二元组返回形状保持不变——fetch_item_media.py 第 39-42 行、
      retry_scrape_failed.py 第 56 行都按二元组解包，仓库习惯是加法优先。
    """
    m = UNIVERSAL_RE.search(html)
    if not m:
        return None, "blocked", None, ""
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None, "blocked", None, ""
    detail = (data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail") or {})
    status_code = detail.get("statusCode")
    status_msg = str(detail.get("statusMsg") or "")
    item = (detail.get("itemInfo") or {}).get("itemStruct") or None
    if item:
        return item, "", status_code, status_msg
    # ★ 2026-11-02（T-02）：网络瞬断类 statusCode 归 blocked（可重试）；
    #   其余（如 10204 作者审核中）归类仍为 removed，只让错误文本带上真实原因。
    if str(status_code) in NETWORK_STATUS_CODES:
        return None, "blocked", status_code, status_msg
    return None, "removed", status_code, status_msg


def parse_item_ex(html: str) -> tuple[dict | None, str]:
    """解析页面，返回 (itemStruct, reason)——兼容旧调用方的二元组入口。

    reason: "" 成功 / "blocked" 风控页（可重试）/ "removed" 真下架（重试无用）。
    ★ 2026-10-24（D2）：以前「无内嵌数据」与「有数据但 itemStruct 空」都返回 None，
      调用方只能写同一句「下架/私有」——判据丢失后，风控被当成下架，指标全歪。
    ★ 2026-11-02（T-02）：内部改为 parse_item_detail 取 statusCode，本函数**返回
      形状不变**（仍是二元组），调用方无需改动。
    """
    item, reason, _status_code, _status_msg = parse_item_detail(html)
    return item, reason


def parse_item(html: str) -> dict | None:
    """兼容旧调用方（retry_scrape_failed / 单测）——只要 item，不关心原因。"""
    return parse_item_ex(html)[0]


def failure_text(reason: str, status_code=None, status_msg: str = "") -> str:
    """失败错误文本：分类话术 + 页面真实 statusCode/statusMsg。

    ★ 2026-11-02（T-02）：以前一律 REASON_TEXT[reason]，10204 全写成
      「已下架/私有」。现在写成
      `itemStruct 空（已下架/私有）[statusCode 10204 author_status,status_reviewing]`，
      一眼能分清「作者审核中」「网络瞬断」「真下架」。
    """
    base = REASON_TEXT.get(reason, "抓取失败")
    tag = " ".join(str(x) for x in (status_code, status_msg) if x not in (None, "", 0))
    return f"{base} [statusCode {tag}]" if tag else base


def to_gmt8(create_time) -> str | None:
    """Unix 秒 → GMT+8 字符串。
    ★ 2026-10-05 修正：原实现为 fromtimestamp(ts) + 8h，但本机时区已是 UTC+8，
    fromtimestamp 返回的已是 GMT+8，再 +8h 会让发布时间**整体偏晚 8 小时**
    （实测台账 kee 7692612863776656670 记 17:06，真实为 09:06）。
    改为显式以 UTC 为基准换算，任何本机时区下结果都正确。"""
    if not create_time:
        return None
    return dt.datetime.fromtimestamp(int(create_time), GMT8).strftime("%Y-%m-%d %H:%M:%S")


def collect_ids(a) -> list[str]:
    if a.ids:
        return list(dict.fromkeys(x.strip() for x in a.ids.split(",") if x.strip()))
    pat = re.compile(rf"^{re.escape(a.account)}_(\d+)\.mp3$")
    ids = []
    for f in sorted(Path(a.audio_dir).glob(f"{a.account}_*.mp3")):
        m = pat.match(f.name)
        if m:
            ids.append(m.group(1))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--audio-dir", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--policy", choices=["strict", "tolerant"], default="strict",
                    help="strict: errors 非空 gate 即停；tolerant: 下架条标注后继续")
    ap.add_argument("--merge", action="store_true",
                    help="与已有 scrape_{account}.json **合并**（增量抓取用）。"
                         "不开则整体覆盖——用 --ids 只抓部分视频时**务必加 --merge**，"
                         "否则会把未参与本次抓取的历史条目冲掉。")
    ap.add_argument("--allow-overwrite", action="store_true",
                    help="逃生口：明确知道自己在丢弃历史时使用，配合 --ids 才放行整体覆盖。")
    ap.add_argument("--interval", type=float, default=None,
                    help="条间隔秒数（默认 RATE_LIMIT_SEC / WB_SCRAPE_INTERVAL=5）；"
                         "实测对比 5s 与 8s 用这个，不用改代码。")
    ap.add_argument("--blocked-retry", type=int, default=None,
                    help="命中可重试失败（风控页/网络瞬断）时的重试次数"
                         "（默认 WB_SCRAPE_BLOCKED_RETRY=1）")
    ap.add_argument("--blocked-sleep", type=float, default=None,
                    help="可重试失败重试前等待秒数（默认 WB_SCRAPE_BLOCKED_SLEEP=8）")
    a = ap.parse_args()
    if a.interval is None:
        a.interval = RATE_LIMIT_SEC
    a.interval = max(0.0, float(a.interval))
    if a.blocked_retry is None:
        a.blocked_retry = BLOCKED_RETRY
    a.blocked_retry = max(0, int(a.blocked_retry))
    if a.blocked_sleep is None:
        a.blocked_sleep = BLOCKED_SLEEP
    a.blocked_sleep = max(0.0, float(a.blocked_sleep))

    # ★ Fail-fast：--ids 表示"只抓一部分"，此时整体覆盖会**不可逆**丢失历史条目。
    #   实测教训：忘了 --merge 会把 scrape_{account}.json 从 N 条缩到几条，
    #   且没有备份可恢复（台账/SQL 无从反推）。故直接拒绝，而非依赖使用者读 help。
    if a.ids and not a.merge and not a.allow_overwrite:
        print("[ERROR] 检测到 --ids 但未加 --merge：这会用本次结果**整体覆盖** "
              f"scrape_{a.account}.json，丢失未参与本次抓取的历史条目。\n"
              "        正确用法：加 --merge（保留历史，同 ID 以本次为准）\n"
              "        确需覆盖：显式加 --allow-overwrite，自担风险",
              file=sys.stderr)
        return 2

    ids = collect_ids(a)
    if not ids:
        print("[ERROR] 未找到待抓取视频ID，未覆盖历史抓取文件", file=sys.stderr)
        return 2
    out = Path(a.out_dir)
    path = out / f"scrape_{a.account}.json"
    old = {}
    if a.merge and path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(old, dict):
                raise ValueError("历史抓取文件不是对象")
            for key in ("items", "errors"):
                rows = old.get(key, [])
                if not isinstance(rows, list) or any(
                        not isinstance(row, dict) or not row.get("id") for row in rows):
                    raise ValueError(f"历史抓取字段 {key} 结构不符")
        except (ValueError, OSError) as e:
            print(f"[ERROR] 无法合并历史抓取文件，未覆盖 {path}: {e}", file=sys.stderr)
            return 1
    items, errors = [], []
    reason_n = {"blocked": 0, "removed": 0}
    total_sc = len(ids)

    def _append_item(vid: str, url: str, item: dict) -> None:
        stats = item.get("stats", {})
        # ★ desc 存全量文案（原 [:200] 会砍掉末尾的 #话题标签；
        #   实测 example_account_029 一条 298 字，标签在 200 字之后被截）
        items.append({
            "id": vid,
            "url": url,
            "desc": item.get("desc") or "",
            # ★ 单独提取 TikTok 话题标签（challenges[].title），不再依赖 desc 内文本
            "hashtags": [c.get("title") for c in (item.get("challenges") or [])
                         if c.get("title")],
            "publish_time_gmt8": to_gmt8(item.get("createTime")),
            "stats": {k: stats.get(k) for k in
                      ("playCount", "diggCount", "commentCount", "shareCount", "collectCount")},
            "music": ((item.get("music") or {}).get("title") or "")[:100],
            "author": (item.get("author") or {}).get("uniqueId", a.account),
        })

    for i, vid in enumerate(ids, start=1):
        print(f"[进度] 视频抓取 {i}/{total_sc}：{vid}", flush=True)
        url = f"https://www.tiktok.com/@{a.account}/video/{vid}"
        try:
            item, reason, status_code, status_msg = parse_item_detail(fetch_page(url))
            # ★ 2026-10-24（D2）：风控页多为瞬时，等 blocked_sleep 再取一次；
            #   重试仍失败才记 blocked。真下架（removed）不重试，省时间也省限额。
            # ★ 2026-11-02（T-02）：网络瞬断（statusCode 100001）也归 blocked，
            #   一并走这条既有重试路径。
            tries = 0
            while (not item) and reason == "blocked" and tries < a.blocked_retry:
                tries += 1
                print(f"[retry] {vid} 命中可重试失败，{a.blocked_sleep:g}s 后重试 "
                      f"({tries}/{a.blocked_retry})", flush=True)
                time.sleep(a.blocked_sleep)
                item, reason, status_code, status_msg = parse_item_detail(fetch_page(url))
            if item:
                _append_item(vid, url, item)
            else:
                reason = reason or "removed"
                reason_n[reason] = reason_n.get(reason, 0) + 1
                # ★ 2026-11-02（T-02）：错误文本带真实 statusCode/statusMsg，
                #   不再清一色「已下架/私有」。
                errors.append({"id": vid, "reason": reason,
                               "error": failure_text(reason, status_code, status_msg)})
        except Exception as e:
            # 传输层异常（超时/SSL/解析炸）按通道类计，供健康度只统计真通道故障
            reason_n["blocked"] += 1
            errors.append({"id": vid, "reason": "blocked", "error": str(e)[:150]})
        if i < len(ids):
            time.sleep(a.interval)

    # ★ 2026-10-29：WAF 挑战页批量兜底——HTTP 通道 blocked 的条目攒齐后
    #   用 playwright 真浏览器一个 ctx 批量抓（同 ctx 过 WAF 后每条很快）。
    blocked_ids = sorted({str(e.get("id")) for e in errors if e.get("reason") == "blocked"})
    if blocked_ids:
        print(f"[fallback] HTTP 通道仍有 {len(blocked_ids)} 条 blocked → 浏览器兜底批量抓取",
              file=sys.stderr, flush=True)
        got = browser_batch_fallback(a.account, blocked_ids)
        for vid, rec in got.items():
            if rec.get("status") == "ok" and rec.get("item"):
                _append_item(vid, rec.get("url", f"https://www.tiktok.com/@{a.account}/video/{vid}"),
                             rec["item"])
                errors = [e for e in errors if str(e.get("id")) != vid]
            elif rec.get("status") == "removed":
                errors = [e for e in errors if str(e.get("id")) != vid]
                # 浏览器通道也确认 itemStruct 空 → 真下架/私有，改判 removed（重试无意义）
                errors.append({"id": vid, "reason": "removed",
                               "error": REASON_TEXT["removed"] + "（双通道一致）"})
        n_ok = sum(1 for rec in got.values() if rec.get("status") == "ok")
        print(f"[fallback] 浏览器兜底结果: 成功 {n_ok} / 还有 {len(errors)} 条 error",
              file=sys.stderr, flush=True)

    # ★ 2026-11-02（T-03）：先记下「本次」的条数——下面的 --merge 分支会把历史条目
    #   合并进 items，判断要不要写盘必须看本次结果，不能看合并后的 items。
    #   （兜底成功/改判的条目已经算进 items/errors，所以取在这里，不取在抓取循环里。）
    run_items = len(items)
    run_errors = len(errors)

    # ★ 增量模式（--ids 搭配 --merge）：本次只抓了部分视频，必须与已有结果合并，
    #   否则整体覆盖会让未参与本次抓取的历史条目全部丢失。
    #   合并规则：同 ID 以本次为准；本次抓成功的 ID 从旧 errors 中移除。
    if a.merge and path.exists():
        merged = {str(i["id"]): dict(i, id=str(i["id"]))
                  for i in (old.get("items") or []) if i.get("id")}
        ok_ids = {i["id"] for i in items}
        for it in items:
            merged[it["id"]] = it
        errs = {str(e["id"]): dict(e, id=str(e["id"]))
                for e in (old.get("errors") or []) if e.get("id")}
        for i in ok_ids:          # 本次抓成功 → 撤销历史失败标记
            errs.pop(i, None)
        for e in errors:
            errs[e["id"]] = e
        items = list(merged.values())
        errors = list(errs.values())
        print(f"[merge] 本次抓 {len(ok_ids)} 条，合并后 items={len(items)} errors={len(errors)}",
              file=sys.stderr)

    # ★ 合并后按最终 errors 重算分类（历史条目可能没有 reason 字段）
    def _reason_of(e: dict) -> str:
        r = (e or {}).get("reason")
        if r in ("blocked", "removed"):
            return r
        return "removed" if "下架" in str((e or {}).get("error") or "") else "blocked"

    reason_n = {"blocked": 0, "removed": 0}
    for e in errors:
        reason_n[_reason_of(e)] += 1

    result = {"account": a.account, "policy": a.policy,
              "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
              "reasons": reason_n,
              "interval_s": a.interval,
              "errors": errors, "items": items}
    # ★ 2026-11-02（T-03）：本次 0 条有效且确有失败 → 不覆盖历史产物。
    #   事故（交接文档 2026-11-02 第 3 节）：全量抓取 49 条全失败（0 items + 49 errors）
    #   时整体覆盖，把 scrape_example_account_022.json 的历史条目当场冲掉——抓得越失败毁得越多，
    #   完全反了。判据只看「本次」：run_items/run_errors 在 --merge 合并之前就取好了。
    #   --merge 不走这个分支：合并结果里含历史条目，写盘正是在保住历史。
    #   退出码不动（仍为 0）：0 items 该由 gate_scrape 判 FAIL，本脚本只负责别毁数据。
    no_write = (run_items == 0 and run_errors > 0 and not a.merge)
    if not no_write:
        out.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    # ★ 2026-10-29：门禁要用 stdout JSON，必须 ensure_ascii=True——
    #   desc 含 emoji 时 GBK 管道曾把整个脚本炸成 UnicodeEncodeError。
    print(json.dumps({"policy": a.policy, "reasons": reason_n,
                      "errors": errors, "items": items},
                     ensure_ascii=True))
    if no_write:
        print(f"[未写盘] {path}  items={len(items)} errors={len(errors)} "
              f"blocked={reason_n['blocked']} removed={reason_n['removed']}"
              f"（本次 0 条有效，保留原文件未覆盖）", file=sys.stderr)
    else:
        print(f"[saved] {path}  items={len(items)} errors={len(errors)} "
              f"blocked={reason_n['blocked']} removed={reason_n['removed']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
