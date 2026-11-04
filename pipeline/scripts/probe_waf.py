# -*- coding: utf-8 -*-
"""基线探针：同一条 TikTok 详情页，看 curl_cffi 拿到的是挑战页还是真数据。

用法（pipeline venv）：
    python probe_waf.py <account> <video_id>
输出：状态码 / 字节数 / 有无 __UNIVERSAL_DATA_FOR_REHYDRATION__ / 是否拿到 itemStruct
不下载任何视频，只取一次 HTML。
"""
import os
import re
import sys
import json

PROXY = os.environ.get("WB_PROXY", "http://127.0.0.1:7892")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UNIVERSAL_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


def summarize(html, status):
    out = {
        "status": status,
        "bytes": len(html or ""),
        "has_universal_data": bool(UNIVERSAL_RE.search(html or "")),
        "verdict": "",
    }
    m = UNIVERSAL_RE.search(html or "")
    if not m:
        # 猜一下是不是 WAF 挑战页
        low = (html or "").lower()
        out["verdict"] = "WAF/挑战页（无内嵌数据）" if (
            "slardar" in low or "verify" in low or len(html or "") < 5000
        ) else "无内嵌数据"
        return out
    try:
        data = json.loads(m.group(1))
        scope = data.get("__DEFAULT_SCOPE__", {})
        item = (scope.get("webapp.video-detail") or {}).get("itemInfo", {}).get("itemStruct")
        out["has_itemStruct"] = bool(item)
        out["verdict"] = "OK：拿到 itemStruct" if item else "有内嵌数据但 itemStruct 空（疑似下架/私有）"
    except Exception as e:
        out["verdict"] = "内嵌数据解析失败: %s" % e
    return out


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "imanebtk_"
    vid = sys.argv[2] if len(sys.argv) > 2 else "7701788024782196000"
    url = "https://www.tiktok.com/@%s/video/%s" % (account, vid)
    print("URL:", url)
    print("PROXY:", PROXY)

    from curl_cffi import requests as cffi
    try:
        r = cffi.get(url, impersonate="chrome124",
                     proxies={"http": PROXY, "https": PROXY},
                     timeout=40, headers={"User-Agent": UA},
                     allow_redirects=True)
        res = summarize(r.text, r.status_code)
    except Exception as e:
        res = {"status": "EXC", "bytes": 0, "has_universal_data": False,
               "verdict": "curl_cffi 异常: %s" % e}
    print("\n[curl_cffi chrome124]")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
