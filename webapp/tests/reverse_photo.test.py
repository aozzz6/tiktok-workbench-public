# -*- coding: utf-8 -*-
"""reverse_photo.test.py — 图文帖（Photo Mode）反推：识别 / 合成素材片 / 抽帧切点

背景（2026-10-30）：图文帖以前在下载前就被拒掉（「请换该博主的视频帖链接」），
用户 15 个图文账号全做不了反推。现在改成「取图集 → 按 2s/张合成 src.mp4」，
下游抽帧/场景检测/分镜/首帧导出全部复用。本测试用真实 ffmpeg 跑一遍合成，
确保：图集能变素材片、时长等于 N×2s、抽帧与切点对得上。

运行：webapp/.venv/Scripts/python.exe tests/reverse_photo.test.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEBAPP))
import tk_reverse as T  # noqa: E402

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  OK  {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def eq(got, want, msg=""):
    if got != want:
        raise AssertionError(f"{msg} 期望 {want!r}，实得 {got!r}")


def _png(path: Path, color: str) -> None:
    rc, out = T._run([T.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                      f"color=c={color}:s=720x1280:d=1", "-frames:v", "1", str(path), "-y"],
                     timeout=60)
    if rc != 0 or not path.is_file():
        raise RuntimeError("测试图生成失败：" + out[-200:])


def case_is_photo():
    eq(T._is_photo({"formats": [{"vcodec": "none", "acodec": "mp3"}]}), True,
       "只有音轨 = 图文帖")
    eq(T._is_photo({"formats": [{"vcodec": "h264", "acodec": "aac"}]}), False,
       "有视频流 = 普通视频")
    eq(T._is_photo({"formats": []}), False, "拿不到格式列表不臆断")


def case_photo_meta():
    orig = T._photo_media
    T._photo_media = lambda url: {"ok": True, "images": ["a", "b", "c"], "count": 3,
                                  "desc": "文案"}
    try:
        meta = T._photo_meta("https://x", {"author": "acc", "desc": ""})
        eq(meta["photo_mode"], True, "要打 photo_mode")
        eq(meta["photo_images"], 3, "图片数")
        eq(meta["duration"], 6.0, "3 张 × 2s")
        eq(meta["desc"], "文案", "空简介要回填")
        # 图集很长时按 60s 封顶，每张停留时间被压缩
        T._photo_media = lambda url: {"ok": True, "images": [str(i) for i in range(40)],
                                      "count": 40, "desc": ""}
        meta = T._photo_meta("https://x", {"author": "acc"})
        eq(meta["duration"], 60.0, "40 张要压到 60s 上限")
    finally:
        T._photo_media = orig


def case_slideshow():
    tmp = Path(tempfile.mkdtemp(prefix="rvphoto_"))
    orig_dir, orig_media, orig_http = T.REVERSE_DIR, T._photo_media, T._http_bytes
    colors = ["red", "green", "blue"]
    srcs = []
    for i, c in enumerate(colors):
        p = tmp / f"src{i}.png"
        _png(p, c)
        srcs.append(p)
    urls = [f"https://example.invalid/{i}.jpg" for i in range(3)]
    T.REVERSE_DIR = tmp / "reverse"
    T.REVERSE_DIR.mkdir(parents=True, exist_ok=True)
    T._photo_media = lambda url: {"ok": True, "images": urls, "count": 3, "desc": ""}
    T._http_bytes = lambda url: srcs[urls.index(url)].read_bytes()
    try:
        vid = "7690000000000000001"
        src, n, per = T._photo_slideshow(vid, "https://example.invalid/post")
        eq(n, 3, "合成张数")
        eq(per, 2.0, "每张 2s")
        assert src.is_file() and src.stat().st_size > 10_000, "素材片没生成"
        probe = T._probe(src)
        assert abs(float(probe["duration"]) - 6.0) <= 0.2, f"时长应≈6s，实得 {probe['duration']}"
        eq(probe["has_audio"], False, "图文帖没有音轨")
        frames = T._frames(vid, src, probe["duration"])
        assert len(frames) >= 10, f"2fps 抽 6s 应有 ≥10 帧，实得 {len(frames)}"
        # ffmpeg 场景检测在图文合成片上会漏首刀（实测 3 张只报 1 个），所以
        # 生产路径用 _photo_scenes 按图边界直接算；这里只验检测本身有输出。
        scenes = T._scenes(vid, src)
        assert len(scenes) >= 1, f"合成片至少要能检出切点，实得 {scenes}"
        eq(T._photo_scenes(3, 2.0), [2.0, 4.0], "3 张图 → 2 个切点")
        eq(T._photo_scenes(5, 1.99), [1.99, 3.98, 5.97, 7.96], "5 张图 → 4 个切点")
        eq(T._photo_scenes(1, 2.0), [], "单张图没有切点")
        # 重试复用：图已下好时不再重下（同一 vid 第二次调用不该再发请求）
        calls = {"n": 0}
        def _count(url):
            calls["n"] += 1
            return srcs[urls.index(url)].read_bytes()
        T._http_bytes = _count
        T._photo_slideshow(vid, "https://example.invalid/post")
        eq(calls["n"], 0, "已下好的图应复用，不该重下")
        # 单张图失败不应整单失败：换一个新 vid（无缓存），让第 2 张下载失败
        T._http_bytes = lambda url: (_ for _ in ()).throw(RuntimeError("boom")) \
            if url == urls[1] else srcs[urls.index(url)].read_bytes()
        src2, n2, _per = T._photo_slideshow("7690000000000000002", "https://example.invalid/post")
        eq(n2, 2, "丢一张图仍要合成剩下 2 张")
        assert src2.is_file(), "部分失败后素材片应仍然存在"
        # 一张都下不到才报错
        T._http_bytes = lambda url: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            T._photo_slideshow("7690000000000000003", "https://example.invalid/post")
            raise AssertionError("全失败应抛 ReverseError")
        except T.ReverseError:
            pass
    finally:
        T.REVERSE_DIR, T._photo_media, T._http_bytes = orig_dir, orig_media, orig_http
        shutil.rmtree(tmp, ignore_errors=True)


print("reverse_photo：")
check("图文帖识别（只有音轨 / 有视频流 / 无格式）", case_is_photo)
check("图文字段与 60s 封顶", case_photo_meta)
check("图集 → 素材片（真实 ffmpeg：时长/抽帧/切点/部分失败容错）", case_slideshow)
print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
