# -*- coding: utf-8 -*-
"""
fastmoss_gmv.py — Step8 出单量筛选与缓存层（v2 #5）

分工（数据边界诚实原则）：
- 本脚本（离线可跑）：读 scrape JSON → 按播放量阈值筛选 → 查本地缓存
  → 命中直接出结果；未命中写 pending_gmv.json 供 FastMoss 查询层（Agent/MCP，带登录态）消费
- FastMoss 查询（需登录态，Agent 侧调用 MCP）→ 结果回写 fastmoss_cache.json → 重跑本脚本即合并

v1 原则：「未收录 ≠ 真 0」——缓存/结果里必须区分 listed（FastMoss 收录）与未收录
输出 stdout 末行 JSON（对齐 gate_gmv）:
  {"results": [{"id","gmv","listed","plays"}...], "pending": [ids]}
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", required=True, help="scrape_{account}.json 路径")
    ap.add_argument("--min-plays", type=int, default=6000,
                    help="播放量阈值，仅 ≥ 此值才查 FastMoss（默认 6000）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--account", default=None,
                    help="账号名：给定时缓存/待查文件按账号隔离（{account}_fastmoss_cache.json / "
                         "pending_gmv_{account}.json）。缺省退回旧共享文件名（兼容历史调用）。")
    a = ap.parse_args()

    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    # 2026-10-10: 共享文件名会被多账号互相覆盖（实测 9 账号各剩 3 条残留）→ 按账号隔离
    if a.account:
        cache_path = out_dir / f"{a.account}_fastmoss_cache.json"
        pending_path = out_dir / f"pending_gmv_{a.account}.json"
    else:
        cache_path = out_dir / "fastmoss_cache.json"
        pending_path = out_dir / "pending_gmv.json"
    cache = load_json(cache_path, {})   # {video_id: {"gmv","listed","queried_at","source"}}

    scrape = load_json(Path(a.scrape), {})
    items = scrape.get("items", [])
    results, pending = [], []
    for it in items:
        plays = (it.get("stats") or {}).get("playCount") or 0
        vid = it["id"]
        if plays <= a.min_plays:
            continue  # 低于阈值：不查 FastMoss（省钱省配额）
        hit = cache.get(vid)
        if hit:
            results.append({"id": vid, "plays": plays, **hit})
        else:
            pending.append({"id": vid, "plays": plays,
                            "url": f"https://www.tiktok.com/@{it.get('author','')}/video/{vid}"})

    if pending:
        pending_path.write_text(json.dumps(
            {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
             "note": "待 FastMoss 查询（需登录态 MCP）；回写 fastmoss_cache.json 后重跑本脚本合并",
             "items": pending}, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {"min_plays": a.min_plays,
               "results": results,
               "pending_ids": [p["id"] for p in pending]}
    print(json.dumps(summary, ensure_ascii=False))
    print(f"[saved] {pending_path}" if pending else "[info] 无待查项", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
