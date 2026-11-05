#!/usr/bin/env python3
"""统一抓取层：curl_cffi chrome124 指纹 + 本地代理。

2026-11-04 决定：删掉原来「scrapling Fetcher 优先、curl_cffi 兜底」的两层写法。
三条理由，都是本机实测的：
  1. scrapling 从未进 requirements，本机也一直没装 → 那层 L1 每次都在 except 里
     落到 curl_cffi，等于只有一层，却让人以为有两层保护；
  2. Fetcher.get(..., impersonate="chrome124") 传的就是 curl_cffi 的参数，底层是同一种
     TLS 指纹，装了也买不到新能力；
  3. 原注释已记录：scrapling 的 stealthy_headers 会触发 TikTok 地区风控
     （302 → /hk/about），所以当初就绕开了它的特色功能。

要过 WAF 挑战页只有真浏览器一条路：pw_detail_fallback.py / pw_uid_fallback.py。
它们的解释器由 find_playwright_python() 统一挑选（原来那种 shutil.which 写法在本机
必然失败，见该函数说明）。

供 tk_audio_download.py（uid 解析）与 scrape_tiktok_video.py（详情抓取）统一调用；
解析逻辑（__UNIVERSAL_DATA_FOR_REHYDRATION__ 等）留在各脚本内。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from proxy_detect import detect_proxy  # noqa: E402
PROXY = os.environ.get("WB_PROXY") or detect_proxy()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def fetch_html(url: str, timeout: int = 30) -> str:
    """抓取 URL 返回 HTML 文本（curl_cffi chrome124 指纹 + 代理）。"""
    from curl_cffi import requests as cffi
    r = cffi.get(url, impersonate="chrome124",
                 proxies={"http": PROXY, "https": PROXY},
                 timeout=timeout, headers={"User-Agent": UA},
                 allow_redirects=True)
    r.raise_for_status()
    return r.text


def extract_universal_data(html: str) -> dict:
    """从 TikTok 页面 HTML 提取 __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON。"""
    import json
    import re
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html, re.S)
    if not m:
        raise RuntimeError("页面无内嵌数据（可能被风控或返回验证页）")
    return json.loads(m.group(1))


def find_playwright_python() -> list[str] | None:
    """找一个**真的能 import playwright** 的解释器，返回可执行前缀，例如 ["py", "-3"]。

    2026-11-04：原实现用 ``shutil.which("python"/"py"/"python3")`` 挑解释器。本机这三个
    名字全部解析到 ``WindowsApps`` 的商店占位符——文件存在、运行即报
    「Python was not found; run without arguments to install from the Microsoft Store」；
    另有一个写死的 ``C:\\Users\\Administrator\\...\\Python314\\python.exe``，该用户目录
    根本不存在。所以「按文件名找到」不等于「能用」：这里逐个**实跑** ``import playwright``
    验证，并支持用 ``WB_PW_PYTHON`` 显式指定。

    返回 None 表示这台机器没有可用的浏览器兜底，调用方应当跳过而不是当成功。
    """
    cands: list[list[str]] = []
    env = (os.environ.get("WB_PW_PYTHON") or "").strip()
    if env:
        cands.append([env])
    # py 启动器排在前面：Windows 上它最可靠（本机就是靠 py -3 找到 Python 3.14）
    p = shutil.which("py")
    if p:
        cands.append([p, "-3"])
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            cands.append([p])

    seen: set[tuple[str, ...]] = set()
    for prefix in cands:
        key = tuple(prefix)
        if key in seen:
            continue
        seen.add(key)
        exe = prefix[0]
        if _is_store_stub(exe):
            continue
        if not Path(exe).exists():
            continue
        try:
            r = subprocess.run(prefix + ["-c", "import playwright"],
                               capture_output=True, timeout=30)
        except Exception:
            continue
        if r.returncode == 0:
            return prefix
    return None


def _is_store_stub(exe: str) -> bool:
    """WindowsApps 下的 python.exe / python3.exe 是「应用执行别名」，存在但运行返回 9009。

    同一目录下的 **py.exe 是真的启动器**，不能一起排除——2026-11-04 实测：
      WindowsApps\\py.EXE -3   → exit 0，能 import playwright
      WindowsApps\\python.exe  → exit 9009「Python was not found」
    """
    low = exe.lower().replace("/", "\\")
    return "windowsapps" in low and Path(exe).name.lower() in ("python.exe", "python3.exe")


if __name__ == "__main__":
    # 冒烟：python scripts/tiktok_fetch.py [url]
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tiktok.com/@kee.yaden"
    h = fetch_html(url)
    print(f"len={len(h)}")
    print(f"playwright python={find_playwright_python()}")
