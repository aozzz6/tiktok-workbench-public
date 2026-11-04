# -*- coding: utf-8 -*-
"""pw_detail_fallback.py — 浏览器兜底抓视频详情页 itemStruct（负责过 WAF 挑战页）。

背景（2026-10-29）：TikTok WAF 波及 /@user/video/{id} 详情页 SSR——curl_cffi
chrome124 指纹恒返 1462 字节 SlardarWAF 挑战页，无内嵌数据。只有真浏览器执行
JS 过 WAF 后才能拿到完整详情页（__UNIVERSAL_DATA_FOR_REHYDRATION__ →
webapp.video-detail.itemStruct）。

该脚本必须由装有 playwright 的系统 Python 运行（不是管线 venv）：
  python pw_detail_fallback.py <account> <video_id> [<video_id> ...]
单个浏览器批量抓取（同 ctx 每 3s 一条限流），stdout 末行输出 JSON：
  {"results": [{"id","url","status","item"?}] , "account": "..."}
status: ok = 拿到 itemStruct；removed = 有内嵌数据但 itemStruct 空（真下架/私有）；
        blocked = 到底也没拿到内嵌数据（罕见，留给人工判断）。
失败 exit=1。
"""
import json
import os
import re
import sys
import time
from pathlib import Path


def pick_browsers_dir():
    """auto 定位 playwright 浏览器缓存目录（含 chromium-* 子目录）覆盖 PLAYWRIGHT_BROWSERS_PATH。"""
    envp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    cands = [envp,
             str(Path.home() / "AppData" / "Local" / "ms-playwright"),
             r"D:\AI\ms-playwright"]
    for c in cands:
        if c and Path(c).exists():
            try:
                subs = [d for d in Path(c).iterdir() if d.name.startswith("chromium")]
                if subs:
                    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(c)
                    return str(c)
            except Exception:
                pass
    return ""


def parse_cookie_header(raw):
    out = []
    for part in raw.split(";"):
        if "=" in part:
            name, _, value = part.strip().partition("=")
            out.append({"name": name.strip(), "value": value.strip(),
                        "domain": ".tiktok.com", "path": "/"})
    return out


UNIVERSAL_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


def fetch_items_browser(account: str, vids: list, cookie_file: str) -> dict:
    raw = ""
    p = Path(cookie_file)
    if p.exists():
        raw = p.read_text(encoding="utf-8").strip()
    from playwright.sync_api import sync_playwright
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    # 2026-11-04：原来写死 7897，与主流链路（WB_PROXY，本机实际 7892）不一致，
    # 代理一改端口兜底就连不上。统一读环境变量。
    PROXY = os.environ.get("WB_PROXY", "http://127.0.0.1:7897")
    results = []
    with sync_playwright() as pl:
        browser = pl.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = browser.new_context(user_agent=UA)
        if raw:
            ctx.add_cookies(parse_cookie_header(raw))
        page = ctx.new_page()
        for i, vid in enumerate(vids):
            url = f"https://www.tiktok.com/@{account}/video/{vid}"
            status, item = "blocked", None
            for attempt in range(12):   # 首条含过 WAF 时间，后续条通常 1~2 轮即中
                try:
                    page.goto(url, timeout=45000)
                except Exception as e:
                    page.goto = page.goto  # goto 超时不算致命，轮询 content 继续
                html = page.content()
                m = UNIVERSAL_RE.search(html)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        detail = (data.get("__DEFAULT_SCOPE__", {})
                                  .get("webapp.video-detail") or {})
                        item = (detail.get("itemInfo") or {}).get("itemStruct") or None
                        status = "ok" if item else "removed"
                        break
                    except Exception:
                        pass
                page.wait_for_timeout(2500)
            rec = {"id": vid, "url": url, "status": status}
            if item:
                rec["item"] = item
            results.append(rec)
            print(f"[进度] 浏览器兜底 {i + 1}/{len(vids)}：{vid} → {status}",
                  file=sys.stderr, flush=True)
            if i < len(vids) - 1:
                page.wait_for_timeout(3000)   # 同 ctx 限流，别再触发风控
        try:
            browser.close()
        except Exception:
            pass
    return {"account": account, "results": results}


def main():
    try:
        # ★ 2026-10-29：Windows 控制台默认 GBK，desc 里的 emoji 会炸掉 JSON 输出
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8")
        if not pick_browsers_dir():
            print("no playwright browsers dir found", file=sys.stderr)
            sys.exit(1)
        account = sys.argv[1]
        vids = [v for v in sys.argv[2:] if v.strip()]
        if not vids:
            print("no video ids given", file=sys.stderr)
            sys.exit(1)
        cookie_file = Path(__file__).resolve().parent.parent / "cc_cookie.txt"
        print(json.dumps(fetch_items_browser(account, vids, str(cookie_file)),
                         ensure_ascii=False))
    except Exception as e:
        print(f"fallback_fail: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
