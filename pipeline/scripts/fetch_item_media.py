# -*- coding: utf-8 -*-
"""fetch_item_media.py — 取单条 TikTok 帖的媒体清单（图文帖图集 / 视频流）。

用途：提示词反推要支持「图文帖（Photo Mode）」。yt-dlp 对图文帖只给音轨和
1~2 张封面，拿不到图集；详情页 SSR 数据里的 itemStruct.imagePost.images 才是全量。

用法：python fetch_item_media.py <url>
输出（stdout 末行 JSON）：
  {"ok": true, "kind": "photo", "images": ["https://...", ...], "count": N,
   "desc": "...", "author": "...", "create_time": 0, "music": "..."}
  {"ok": false, "reason": "blocked|removed|error", "error": "..."}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _pick_url(node) -> str:
    """imageURL 可能是 {urlList:[...]} 或字符串；取第一个非空。"""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        for u in (node.get("urlList") or []):
            if isinstance(u, str) and u.startswith("http"):
                return u
        for k in ("url", "uri"):
            v = node.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
    return ""


def media_of(url: str) -> dict:
    from tiktok_fetch import fetch_html
    from scrape_tiktok_video import parse_item_ex

    html = fetch_html(url)
    item, reason = parse_item_ex(html)
    if not item:
        return {"ok": False, "reason": reason or "error",
                "error": "详情页没有 itemStruct（风控页或已下架）"}
    ip = item.get("imagePost") or {}
    raw = ip.get("images") or []
    images = []
    for row in raw:
        if isinstance(row, dict):
            u = _pick_url(row.get("imageURL") or row)
        else:
            u = _pick_url(row)
        if u and u not in images:
            images.append(u)
    cover = _pick_url(ip.get("cover") or "")
    if not images and cover:
        images = [cover]
    kind = "photo" if images else "video"
    return {
        "ok": True,
        "kind": kind,
        "images": images,
        "count": len(images),
        "desc": item.get("desc") or "",
        "author": ((item.get("author") or {}).get("uniqueId") or ""),
        "create_time": item.get("createTime") or 0,
        "music": ((item.get("music") or {}).get("title") or ""),
        "video": bool(((item.get("video") or {}).get("playAddr")) or ""),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "reason": "error", "error": "缺少 URL"},
                         ensure_ascii=False))
        return 2
    try:
        out = media_of(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "reason": "error",
               "error": f"{type(e).__name__}: {str(e)[:200]}"}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
