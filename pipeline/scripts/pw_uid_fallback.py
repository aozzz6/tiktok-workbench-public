# -*- coding: utf-8 -*-
"""pw_uid_fallback.py — 浏览器兜底解析 TikTok 账号 uid（负责过 WAF 挑战页）。

背景（2026-10-29）：TikTok 对 @主页 SSR 入口整体启用 SlardarWAF 挑战页，
纯 HTTP（curl_cffi chrome124 指纹）恒拿 1462 字节 WAF 页，登录 cookie 也无效；
只有真浏览器执行 JS 后才能拿到含 user-detail 的完整首页。
该脚本必须由装有 playwright 的系统 Python 运行（不是管线 venv）：
  python pw_uid_fallback.py <account> [cookie_file]
成功时 stdout 末行输出 JSON：{"uid": "...", "nickname": "..."}；失败 exit=1。
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


def resolve_uid_browser(account: str, cookie_file: str) -> str:
    raw = ""
    p = Path(cookie_file)
    if p.exists():
        raw = p.read_text(encoding="utf-8").strip()
    from playwright.sync_api import sync_playwright
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    PROXY = "http://127.0.0.1:7897"
    last = ""
    with sync_playwright() as pl:
        browser = pl.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = browser.new_context(user_agent=UA)
        if raw:
            ctx.add_cookies(parse_cookie_header(raw))
        page = ctx.new_page()
        for i in range(30):  # 最多 ~90s：WAF JS 挑战放行后 universal data 即出现
            try:
                page.goto(f"https://www.tiktok.com/@{account}", timeout=45000)
            except Exception as e:
                last = f"goto:{type(e).__name__}"
            html = page.content()
            m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                          html, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))
                    ui = (data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
                          .get("userInfo") or {}).get("user", {})
                    uid = ui.get("id")
                    if uid:
                        browser.close()
                        return json.dumps({"uid": str(uid), "secUid": ui.get("secUid", ""),
                                           "nickname": ui.get("nickname", "")},
                                          ensure_ascii=False)
                except Exception:
                    pass
            page.wait_for_timeout(3000)
        try:
            browser.close()
        except Exception:
            pass
    raise RuntimeError(f"浏览器兜底也未解析到 uid（{last or 'universal data 未出现'}）")



if __name__ == "__main__":
    try:
        # ★ 2026-10-29：Windows 控制台默认 GBK，昵称含 emoji 会炸掉 JSON 输出
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8")
        if not pick_browsers_dir():
            print("no playwright browsers dir found", file=sys.stderr)
            sys.exit(1)
        print(resolve_uid_browser(sys.argv[1],
                                  sys.argv[2] if len(sys.argv) > 2 else
                                  str(Path(__file__).resolve().parent.parent / "cc_cookie.txt")))
    except Exception as e:
        print(f"fallback_fail: {e}", file=sys.stderr)
        sys.exit(1)
