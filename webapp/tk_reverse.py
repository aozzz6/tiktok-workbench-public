# -*- coding: utf-8 -*-
"""TikTok 视频提示词反推（tk_reverse）。

用户给一条 TikTok 链接，产出可直接投喂 Dreamina Seedance 2.0 Mini 的分镜级提示词包。
提示词模板：`Seedance2.0通用提示词模板.md`（2026-10-30，六要素 + 保持不变/音频/负向）。
规格来源：`TikTok视频提示词反推_SOP.md`（v1.0 · 2026-10-21）。

分工（重要）：
  · 本模块负责「可自动化的部分」—— 元数据 / 下载 / 读参数 / 抽帧 / 场景检测 /
    首帧导出 / 音轨剥离 / 视觉模型分批读帧 / 分镜结构化 / 报告 HTML 渲染。
  · 视觉模型只做「看见什么」，不做「编造卖点」。任何模型给不出的信息一律留空并在
    报告里标注，不用文案推测画面。
  · 需要人工/Agent 精修时走 `build_handoff()`，产物是可直接粘进对话的交接指令。

目录结构（`config.TK_REVERSE_DIR/<video_id>/`）：
    src.mp4                     原片
    frames/f_001.jpg …          2fps 抽帧（判断动作与切点）
    refs/s1_xxx.png …           分镜首帧（原尺寸，i2v 用）
    audio.wav                   剥离音轨（16k mono）
    manifest.json               元数据 + 参数 + 抽帧表 + 切点
    report.json                 视觉模型的结构化结果（分镜/提示词/旁白/合规）
    TK反推报告_<id>.html        主交付物
    status.json                 阶段/进度/日志（前端轮询）
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

import config
import seedance_template as seedance

# ────────────────────────── 路径与外部依赖 ──────────────────────────
REVERSE_DIR = Path(os.environ.get("WB_REVERSE_DIR", r"D:/workbuddy_project/tiktok/reverse"))
REVERSE_DIR.mkdir(parents=True, exist_ok=True)

YTDLP = Path(os.environ.get(
    "WB_YTDLP",
    r"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/yt-dlp.exe"))
MODELS_JSON = config.WORKBUDDY_HOME / "models.json"


def _clean_stale_uploads(max_age: float = 86400) -> None:
    """清掉 _uploads 里超过一天的孤儿上传（上传中断会留下半截文件）。

    ★ 2026-10-23 审查 R10：反推根目录只增不减，_uploads 的残文件没人管。
    """
    uploads = REVERSE_DIR / "_uploads"
    if not uploads.is_dir():
        return
    cutoff = time.time() - max_age
    for p in uploads.glob("video_*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            pass


try:
    _clean_stale_uploads()
except Exception:   # noqa: BLE001  启动清理失败不影响主流程
    pass

FPS_SAMPLE = 2          # 抽帧速率（SOP P4 轨 A）
SCENE_TH = 0.25         # 场景检测阈值（SOP P4 轨 B）
FRAME_MAX_W = 540       # 送模型的帧宽（降 token，足够判断款式/动作）
MAX_FRAME_FOR_AI = 16   # 单条视频送模型的最大帧数
BATCH_FRAMES = 3        # 每次请求带的帧数（4 帧时推理模型容易把预算全花在 reasoning 上）

# ★ 2026-10-23：前台进度条要显示「走到第几步」，步骤与 _stage 的 (label, progress) 一一对应，
#   只做展示，不参与任何判定。新增阶段时同步改这里，否则进度条会少一格。
PREPARE_STEPS = [("读取原片", 10), ("读取参数", 40), ("抽帧", 55),
                 ("场景检测", 70), ("剥离音轨", 82), ("素材就绪", 100)]
# 图文帖的素材是图集合成的，步骤文案要跟着换（进度百分比与 _prepare 一致）
PHOTO_PREPARE_STEPS = [("下载图集", 10), ("读取参数", 40), ("抽帧", 55),
                       ("场景检测", 70), ("合成素材片", 82), ("素材就绪", 100)]
AD_STEPS = [("逐帧读画面", 5), ("拆结构", 55), ("写提示词", 72),
            ("旁白与合规", 88), ("导出首帧", 93), ("完成", 100)]
RESTORE_STEPS = [("抽取参考图", 5), ("核对镜头与动作", 25), ("生成还原提示词", 55),
                 ("原音轨与参考片段", 82), ("保存报告与素材包", 97), ("完成", 100)]


def _which(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    for c in (f"C:/Users/Administrator/bin/{name}.exe", f"C:/ffmpeg/bin/{name}.exe"):
        if Path(c).exists():
            return c
    return ""


FFMPEG = _which("ffmpeg")
FFPROBE = _which("ffprobe")


class ReverseError(Exception):
    """反推流程中的可读错误（前端直接显示 message）。"""


# ────────────────────────── 状态（内存 + status.json） ──────────────────────────
_LOCK = threading.RLock()
_STATE: dict[str, dict] = {}
_WORKERS: dict[str, tuple[str, threading.Thread]] = {}   # vid -> (kind, thread)
_WORK_START: dict[str, float] = {}                       # vid -> 本阶段开始时间（进度条算已用时）
_CANCEL: dict[str, threading.Event] = {}                 # vid -> 取消信号（R3）
_REPORT_MODE_CACHE: dict[str, tuple[float, int, str]] = {}   # vid -> (mtime, size, mode)（R16）
MAX_LOG = 120


# ★ 2026-10-21 独立验收 P1-1：vid 只允许数字。旧实现直接 REVERSE_DIR / vid，
#   传入 ".." 时 remove() 的 rmtree 会删掉反推根目录的兄弟（webapp/pipeline…）。
#   视频 ID 是纯数字，任何分隔符、"."、".."、盘符都一律拒绝。
_VID_RE = re.compile(r"^\d{1,20}$", re.ASCII)   # 只认 ASCII 数字，全角数字也不行


def _check_vid(vid) -> str:
    """校验并规范化视频 ID（全部按 vid 访问的入口共用）。"""
    s = str(vid if vid is not None else "").strip()
    if not _VID_RE.match(s):
        raise ReverseError("视频 ID 不合法：只接受 20 位以内的数字 ID")
    return s


def _dir(vid: str) -> Path:
    """任务目录 = 反推根目录下、名字恰为该数字 ID 的单层子目录。

    解析后的路径必须直接挂在 REVERSE_DIR 下；根目录本身、父目录、绝对路径、
    符号链接跳到别处一律拒绝（越界抛 ReverseError，由接口转 4xx）。
    """
    v = _check_vid(vid)
    root = REVERSE_DIR.resolve()
    d = (root / v).resolve()
    if d == root or d.parent != root:
        raise ReverseError("视频 ID 不合法：任务目录越出反推根目录")
    return d


def task_dir(vid: str) -> Path:
    """对外（app.py）暴露的目录解析：与内部同一套 ID + 越界校验。"""
    return _dir(vid)


def _now() -> float:
    return time.time()


def _status_path(vid: str) -> Path:
    return _dir(vid) / "status.json"


def _blank(vid: str) -> dict:
    return {
        "video_id": vid,
        "url": "",
        "stage": "pending",       # pending|preparing|ready|analyzing|done|failed|stopped
        "stage_label": "排队中",
        "progress": 0,
        "error": "",
        "log": [],
        "meta": {},
        "probe": {},
        "frames": 0,
        "ai_frames": 0,
        "shots": 0,
        "scenes": [],
        "has_report": False,
        "model": "",
        "created_at": _now(),
        "updated_at": _now(),
        "busy": "",
    }


def _load(vid: str) -> dict:
    with _LOCK:
        if vid in _STATE:
            return _STATE[vid]
        fp = _status_path(vid)
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                base = _blank(vid)
                base.update(d or {})
                # 崩溃/重启后遗留的 running 态不可信，回落为可重试态
                if base.get("stage") in ("preparing", "analyzing"):
                    base["stage"] = "ready" if base.get("frames") else "failed"
                    base["stage_label"] = "已中断（可重跑）"
                    base["busy"] = ""
                _STATE[vid] = base
                return base
            except Exception:
                pass
        base = _blank(vid)
        _STATE[vid] = base
        return base


def _replace_retry(src, dst, attempts: int = 8) -> None:
    """替换状态文件；Windows 上两个进程同时替换同一目标会偶发 WinError 5。

    ★ 2026-10-23 审查 R1 实测：临时名随机化后，两进程各 200 次仍可能撞一次
      os.replace（目标正被另一个写者替换）。os.replace 本身原子，重试即可，
      最后一次写者胜；持续冲突时把最后一次异常原样抛出。
    """
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.01 * (i + 1))


def _save(vid: str, **kw) -> dict:
    with _LOCK:
        st = _STATE.get(vid) or _load(vid)
        st.update(kw)
        st["updated_at"] = _now()
        d = _dir(vid)
        d.mkdir(parents=True, exist_ok=True)
        target = _status_path(vid)
        # ★ 2026-10-23 审查 R1：临时名必须唯一。固定 status.tmp 在多进程/多实例
        #   同时写同一 vid 时会互相踩踏（实测两进程各写 400 次，各失败 92/86 次）。
        fd, tmp_name = tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(st, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_retry(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return st


def _log(vid: str, msg: str, **kw) -> None:
    with _LOCK:
        st = _STATE.get(vid) or _load(vid)
        st.setdefault("log", []).append({"t": _now(), "msg": msg})
        if len(st["log"]) > MAX_LOG:
            st["log"] = st["log"][-MAX_LOG:]
    _save(vid, **kw)


def _stage(vid: str, stage: str, label: str, progress: int, **kw) -> None:
    _save(vid, stage=stage, stage_label=label, progress=max(0, min(100, int(progress))), **kw)


# ────────────────────────── 链接解析 ──────────────────────────
_VIDEO_RE = re.compile(r"/video/(\d{6,})")
_BARE_RE = re.compile(r"^\s*(\d{15,})\s*$")
_SHORT_RE = re.compile(r"https?://(?:vm|vt)\.tiktok\.com/\S+", re.I)


def parse_url(raw: str) -> tuple[str, str]:
    """返回 (规范化链接, 已知 video_id 或 '')。短链/纯数字都接受。"""
    s = (raw or "").strip()
    if not s:
        raise ReverseError("请填入 TikTok 视频链接")
    m = _BARE_RE.match(s)
    if m:
        return f"https://www.tiktok.com/@i/video/{m.group(1)}", m.group(1)
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    if not re.search(r"tiktok\.com", s, re.I):
        raise ReverseError("不是 TikTok 链接（需要 tiktok.com/@xxx/video/xxx 或 vm.tiktok.com 短链）")
    m = _VIDEO_RE.search(s)
    if m:
        return s, m.group(1)
    if _SHORT_RE.match(s):
        return s, ""      # 短链：由 yt-dlp 跟随跳转后拿真实 id
    raise ReverseError("链接里没有 video id，请用视频详情页链接（含 /video/<数字>）")


# ────────────────────────── 子进程 ──────────────────────────
def _run(cmd: list[str], timeout: int = 300, cwd: str | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, creationflags=flags)
    except OSError as e:
        raise ReverseError(f"无法启动外部命令：{Path(str(cmd[0])).name}（{e}）") from None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # ★ 2026-10-23 审查 R4：subprocess.run(timeout=) 只杀直接子进程，
        #   yt-dlp 拉起的 ffmpeg 孙进程会活下来；这里按进程树收尸，并转成可读错误。
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
        try:
            proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise ReverseError(
            f"外部命令超时（{timeout}s）：{Path(str(cmd[0])).name}；"
            "可稍后重试，或先换更短的视频") from None
    return proc.returncode, (out or b"").decode("utf-8", "replace")


def _need_tools(need_ytdlp: bool = True) -> None:
    if not FFMPEG or not FFPROBE:
        raise ReverseError("未找到 ffmpeg / ffprobe，反推需要它们做抽帧与首帧导出")
    if need_ytdlp and not YTDLP.exists():
        raise ReverseError(f"未找到 yt-dlp：{YTDLP}")


# ────────────────────────── P1 元数据 ──────────────────────────
# ★ 2026-10-30：「取元数据失败（代理不通或链接失效）」这句误报过太多次。
#   实测同一条链接：yt-dlp 报 "Your IP address is blocked from accessing this post"，
#   而视频页里写的是 {"webapp.video-detail":{"statusCode":10204,"statusMsg":"author_status,status_reviewing"}}
#   —— 是帖子本身审核中/作者状态异常，跟代理没关系。这里按真实状态给人话。
_PAGE_DETAIL_RE = re.compile(
    r'"webapp\.video-detail":\{"statusCode":(-?\d+)(?:,"statusMsg":"([^"]*)")?')

# 传输层抖动（TLS 被中途掐断、读超时等）：值得原样重试
_TRANSIENT_MARKS = ("ssl_error_syscall", "error queue empty", "connection closed",
                    "connection reset", "connection aborted", "timed out", "timeout",
                    "temporary failure", "handshake", "http error 502", "http error 503",
                    "http error 504")


def _page_video_status(url: str, timeout: int = 20) -> tuple[int, str]:
    """读视频页里的 video-detail 状态码/状态串；取不到就返回 (0, '')。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": PHOTO_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
    })
    handlers = []
    if config.PROXY:
        handlers.append(urllib.request.ProxyHandler(
            {"http": config.PROXY, "https": config.PROXY}))
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return 0, ""
    m = _PAGE_DETAIL_RE.search(html)
    if not m:
        return 0, ""
    return int(m.group(1)), (m.group(2) or "")


def _is_transient(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _TRANSIENT_MARKS)


def _ytdlp_oneline(text: str) -> str:
    """yt-dlp 报错后面拖着大段「please report this issue…」；旧代码取尾部 300 字，
    正好丢掉有用的首句（截图里那句没头没尾的 "queue empty" 就是这么来的）。"""
    s = re.sub(r"\s+", " ", (text or "").strip())
    for cut in ("; please report this issue", "please report this issue on",
                "Confirm you are on the latest version"):
        i = s.find(cut)
        if i > 0:
            s = s[:i]
    return s.strip()[:260]


def _describe_meta_error(text: str, url: str) -> str:
    low = (text or "").lower()
    head = _ytdlp_oneline(text)
    if "blocked from accessing" in low or "author_status" in low or "10204" in text:
        code, msg = _page_video_status(url)
        extra = f"（statusMsg: {msg}）" if msg else ""
        if code == 10204 or "blocked from accessing" in low:
            if "review" in msg.lower():
                return ("这条视频本身看不了：TikTok 把它标成「审核中 / 作者状态异常」"
                        f"{extra}。不是代理问题，也不是链接写错，换一条视频或等作者恢复后再试")
            if msg:
                return (f"这条视频本身看不了：TikTok 返回 10204{extra}"
                        "（地区或作者限制），不是代理问题")
            return "这条视频本身看不了：TikTok 返回 10204（地区或作者限制），不是代理问题"
        if code in (10216, 10222):
            return "这条视频看不了：私密帖 / 私密账号，工作台没有该账号的登录态"
    if "private" in low or "permission" in low or "login required" in low:
        return "这条视频需要登录或作者未公开，工作台没有 TikTok 登录态，取不到元数据"
    if _is_transient(text):
        return f"连 TikTok 时被中途断开（代理节点不稳，已自动重试）：{head}"
    if "404" in text or "not available" in low or "unable to find" in low:
        return f"链接失效或视频已删除：{head}"
    return f"取元数据失败：{head}"


def _yt_json(url: str) -> dict:
    _need_tools()
    cmd = [str(YTDLP), "--no-warnings", "--skip-download", "--dump-json"]
    if config.PROXY:
        cmd += ["--proxy", config.PROXY]
    cmd.append(url)
    attempts = 3
    last_text = ""
    for i in range(attempts):
        rc, out = _run(cmd, timeout=180)
        text = out.strip()
        if rc == 0 and text:
            line = next((l for l in text.splitlines() if l.strip().startswith("{")), "")
            if line:
                return json.loads(line)
        if not text:
            # ★ 2026-10-29：空输出 = 进程根本没跑起来。yt-dlp.exe 自带的那套 Python
            #   环境（.workbuddy/binaries/python/envs/default）一旦 home 失效，就是
            #   瞬间退出、stderr 一个字都没有，旧代码会把它误报成「代理不通」。
            raise ReverseError(
                f"yt-dlp 启动失败（退出码 {rc}，没有任何输出）：{YTDLP}；"
                "通常是它所在 Python 环境的 pyvenv.cfg 失效（home 指向不可用的运行时）")
        last_text = text
        # 传输层抖动才重试；10204/私密这类确定性结果重试也没用
        if not _is_transient(text) or i == attempts - 1:
            break
        time.sleep(1.5 * (i + 1))
    raise ReverseError(_describe_meta_error(last_text, url))



def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _norm_meta(d: dict) -> dict:
    desc = d.get("description") or d.get("title") or ""
    tags = re.findall(r"#([^\s#]+)", desc)
    hook = next((l.strip() for l in desc.splitlines() if l.strip()), "")
    ct = d.get("create_time")
    date_only = ""
    if not ct and d.get("upload_date"):
        try:
            date_only = time.strftime("%Y-%m-%d", time.strptime(str(d["upload_date"]), "%Y%m%d"))
        except Exception:
            date_only = ""
    return {
        "video_id": str(d.get("id") or ""),
        "url": d.get("webpage_url") or "",
        "author": d.get("uploader") or "",
        "nickname": d.get("channel") or "",
        "desc": desc,
        "desc_full": desc,
        "hook_line": hook,
        "hashtags": tags,
        "language_guess": _guess_lang(desc),
        "duration": float(d.get("duration") or 0),
        "width": int(d.get("width") or 0),
        "height": int(d.get("height") or 0),
        "fps": d.get("fps"),
        "view": int(d.get("view_count") or 0),
        "like": int(d.get("like_count") or 0),
        "comment": int(d.get("comment_count") or 0),
        "repost": int(d.get("repost_count") or 0),
        "upload_at": _fmt_ts(ct) if ct else (date_only or "-"),
        "upload_ts": ct or 0,
        "track": d.get("track") or "",
        "has_shop_tag": any(re.search(r"TikTokShop|TikTokMadeMeBuyIt|shop", t, re.I) for t in tags),
        "source": "yt-dlp",
    }


def _guess_lang(desc: str) -> str:
    """只做粗判，用于选合规规范；判不出就标『待人工确认』。"""
    t = (desc or "").lower()
    de = sum(t.count(w) for w in ("und", "das", "ist", "nicht", "für", "mit ", "der ", "die "))
    en = sum(t.count(w) for w in ("the ", "and ", "this ", "with ", "is ", "you "))
    if re.search(r"[äöüß]", t) or de > en:
        return "de"
    if en > 0:
        return "en"
    return ""


def _is_photo(d: dict) -> bool:
    """这条帖是不是图文帖（Photo Mode）。

    ★ 2026-10-24（B）：TikTok 图文帖的页面数据里 `itemStruct.video` 是空壳
      （playAddr/downloadAddr 为空、duration=0、cover 落在 ...-i-photomode-... 域名），
      yt-dlp 的 formats 只有一条 audio。旧流程照常下载，三次回退全部只落到 dl.mp3，
      最后报「原片下载失败」—— 看起来像 403/网络问题（example_account_051 7696392974585941280、
      example_account_018 7694968114152262944 两条即此型），于是先加了「直接拒掉」。
    ★ 2026-10-30：拒掉不是答案 —— 用户 15 个图文账号都在做对标。现在改为
      「识别出来 → 取图集 → 合成素材片」，下游抽帧/分镜/首帧/还原全部照旧。
      判据不变：格式列表里没有任何带 vcodec 的流，就是图文帖。
    """
    fmts = [f for f in (d.get("formats") or []) if isinstance(f, dict)]
    if not fmts:                      # 拿不到格式列表就不臆断，交给下载阶段兜底
        return False
    if any(str(f.get("vcodec") or "none").lower() not in ("none", "") for f in fmts):
        return False
    return True


# ────────────────────────── 图文帖（Photo Mode）──────────────────────────
PHOTO_SEC_PER_IMAGE = 2.0      # 每张图在素材片里停留 2s
PHOTO_MAX_SEC = 60.0           # 与「60 秒以内」的产品口径一致
PHOTO_W, PHOTO_H = 1080, 1920  # 统一成 9:16 竖屏画布（与 TikTok 一致）
PHOTO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


_FFMPEG_OPTS_CACHE: dict[str, bool] = {}


def _ffmpeg_supports(option: str) -> bool:
    """探测当前 ffmpeg 是否支持某个全局/输出选项（版本差异：-vsync 在 9.0 被移除）。"""
    key = option.strip().lower()
    if key in _FFMPEG_OPTS_CACHE:
        return _FFMPEG_OPTS_CACHE[key]
    ok = False
    if FFMPEG:
        rc, out = _run([FFMPEG, "-hide_banner", "-h", "full"], timeout=30)
        text = (out or "").lower()
        ok = f"-{key}" in text
    _FFMPEG_OPTS_CACHE[key] = ok
    return ok


def _pipeline_python() -> str:
    """跑图集解析用的解释器：必须是 pipeline 的 venv（装了 curl_cffi/scrapling）。"""
    try:
        return config.python_exe()
    except Exception:  # noqa: BLE001
        return str(config.PIPELINE_DIR / ".venv" / "Scripts" / "python.exe")


def _photo_media(url: str) -> dict:
    """取图文帖的图集清单。

    yt-dlp 对图文帖只给音轨 + 1~2 张封面，拿不到完整图集；详情页 SSR 里的
    `itemStruct.imagePost.images[].imageURL.urlList` 才是全量。解析放在
    pipeline/scripts/fetch_item_media.py（复用既有的 tiktok_fetch 抓取层，
    webapp 的 venv 里没有 curl_cffi，不能直接 import）。
    """
    helper = Path(config.PIPELINE_DIR) / "scripts" / "fetch_item_media.py"
    if not helper.exists():
        raise ReverseError(f"缺少图文帖解析脚本：{helper}")
    rc, out = _run([_pipeline_python(), str(helper), url], timeout=240)
    line = next((x for x in reversed((out or "").splitlines()) if x.strip().startswith("{")), "")
    if not line:
        raise ReverseError("图文帖解析没有返回结果：" + (out or "").strip()[-200:])
    try:
        d = json.loads(line)
    except ValueError:
        raise ReverseError("图文帖解析结果不是 JSON：" + line[:200]) from None
    if not d.get("ok"):
        raise ReverseError("图文帖解析失败：" + str(d.get("error") or d.get("reason") or "未知原因"))
    return d


def _photo_meta(url: str, meta: dict) -> dict:
    """给 meta 打上图文字段（图片数 / 合成时长）。取不到图集就抛可读错误。"""
    media = _photo_media(url)
    images = media.get("images") or []
    if not images:
        raise ReverseError("这条是 TikTok 图文帖（Photo Mode），没有视频流；"
                           "图集也没解析到，请换该博主的视频帖链接")
    out = dict(meta)
    out["photo_mode"] = True
    out["photo_images"] = len(images)
    if not out.get("desc"):
        out["desc"] = media.get("desc") or ""
    out["duration"] = round(min(PHOTO_SEC_PER_IMAGE * len(images), PHOTO_MAX_SEC), 2)
    return out


def _http_bytes(url: str, timeout: int = 90, attempts: int = 3) -> bytes:
    """取图：tiktokcdn 走代理时偶发读超时（单张 800KB 要 20s+），必须重试。

    ★ 2026-10-30：首版没重试，第 5 张图 read timeout 直接把整条图文帖判失败；
      图集里少一张不算致命（跳过并记日志），但一次超时就整单失败才是真问题。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": PHOTO_UA,
        "Referer": "https://www.tiktok.com/",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    })
    handlers = []
    if config.PROXY:
        handlers.append(urllib.request.ProxyHandler(
            {"http": config.PROXY, "https": config.PROXY}))
    opener = urllib.request.build_opener(*handlers)
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            with opener.open(req, timeout=timeout) as r:
                data = r.read()
            if len(data) >= 3000:
                return data
            last = ReverseError(f"内容过短（{len(data)} 字节）")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise last if isinstance(last, Exception) else ReverseError("下载失败")


def _photo_scenes(n: int, per: float) -> list[float]:
    """图文帖的切点：每张图的边界就是一个切点（n 张 → n-1 个）。

    ★ 不用 ffmpeg 场景检测：实测「图1→图2」这一刀经常检不出来（5 张图只报
      3 个切点，分镜会把前两张并成一段）。图文帖的切点是确定的，直接算。
    """
    n = max(0, int(n or 0))
    per = max(0.01, float(per or 0))
    return [round(per * i, 2) for i in range(1, n)]


def _photo_slideshow(vid: str, url: str) -> tuple[Path, int, float]:
    """图文帖 → 素材片：下载图集 → 统一 9:16 画布 → concat 成 src.mp4。

    ★ 为什么合成视频而不是改下游：抽帧、场景检测、首帧导出、原片还原全都吃
      src.mp4。合成一条「每张图停 2s」的片子，下游一行都不用改，分镜天然等于
      图集的每一张（场景检测会在每张图的切点上报切点）。
    """
    d = _dir(vid)
    target = d / "src.mp4"
    media = _photo_media(url)
    images = media.get("images") or []
    if not images:
        raise ReverseError("图文帖没有解析到图片，无法反推")
    n = len(images)
    per = round(min(PHOTO_SEC_PER_IMAGE, PHOTO_MAX_SEC / n), 2)
    imgdir = d / "imgs"
    norm = imgdir / "norm"
    # ★ 只清派生目录，保留已下好的原图：代理抖一下导致第 N 张超时，重试不该重下整套
    shutil.rmtree(norm, ignore_errors=True)
    imgdir.mkdir(parents=True, exist_ok=True)
    norm.mkdir(parents=True, exist_ok=True)
    for stale in imgdir.glob("i_*.jpg"):
        try:
            idx = int(stale.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        if idx > n:
            stale.unlink(missing_ok=True)
    lines: list[str] = []
    ready: list[Path] = []
    for i, u in enumerate(images, 1):
        _log(vid, f"下载图集 {i}/{n}", progress=min(35, 10 + int(i * 25 / max(1, n))))
        raw = imgdir / f"i_{i:03d}.jpg"
        try:
            if raw.is_file() and raw.stat().st_size > 3000:
                pass                      # 重试复用已下好的图，别为一张超时重下全套
            else:
                raw.write_bytes(_http_bytes(u))
        except Exception as e:  # noqa: BLE001
            # 单张图失败不判整单死刑：少一张图仍能出分镜，日志里说清楚是哪张
            _log(vid, f"第 {i} 张图下载失败，跳过：{type(e).__name__}: {str(e)[:120]}")
            continue
        # 归一化后按 1..N 连续编号：image2 解复用器要求序号连续（中间跳过失败图）
        out = norm / f"seq_{len(ready) + 1:03d}.png"
        rc, msg = _run([FFMPEG, "-v", "error", "-i", str(raw), "-vf",
                        f"scale={PHOTO_W}:{PHOTO_H}:force_original_aspect_ratio=decrease,"
                        f"pad={PHOTO_W}:{PHOTO_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
                        "-frames:v", "1", str(out), "-y"], timeout=120)
        if rc != 0 or not out.is_file():
            _log(vid, f"第 {i} 张图归一化失败，跳过：{(msg or '').strip()[-120:]}")
            continue
        ready.append(out)
    if not ready:
        raise ReverseError("图文帖的图片一张都没下载成功（网络/代理问题），请稍后重试")
    n = len(ready)
    tmp = d / ".src-photo.mp4"
    tmp.unlink(missing_ok=True)
    _stage(vid, "preparing", "合成素材片", 45)
    # ★ 2026-10-30：合成方式从 concat 解复用器换成 image2 + -framerate。
    #   concat 的 duration 只对「后面还有文件」的段生效：5 张图出 9.96s（少一张），
    #   2 张图出 1.96s，把末帧重复一次又多出 2s —— 两头都不对。
    #   image2 按固定帧率读图：-framerate 1/per 即「每张停留 per 秒」，实测
    #   5 张=10.000s、2 张=4.000s，且不需要 -vsync / -fps_mode（ffmpeg 9 已移除前者）。
    rc, msg = _run([FFMPEG, "-v", "error", "-framerate", f"{1.0 / per:.6f}",
                    "-i", str(norm / "seq_%03d.png"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-r", "25", str(tmp), "-y"],
                   timeout=900)
    if rc != 0 or not tmp.is_file() or tmp.stat().st_size < 5_000:
        tmp.unlink(missing_ok=True)
        raise ReverseError("图文帖合成素材片失败：" + (msg or "").strip()[-200:])
    os.replace(tmp, target)
    return target, n, per


# ────────────────────────── P2 下载 ──────────────────────────
def _download(vid: str, url: str, fresh: bool = False) -> Path:
    """下载原片。

    ★ 2026-10-23 审查 R9：旧版重建时先删 src.mp4 再下载，下载失败原片就没了。
      现在先落到 dl.*，校验通过才替换 src.mp4；fresh=True 表示强制重下，
      失败仍保留旧原片。
    ★ 审查 R12：WebM/MKV 不能直接改名成 src.mp4（静态路由按扩展名发
      video/mp4，浏览器可能放不出），非 MP4 容器先转封装。
    """
    d = _dir(vid)
    target = d / "src.mp4"
    if target.exists() and target.stat().st_size > 10_000 and not fresh:
        return target
    attempts = [
        ["-f", "bv*+ba/b"],
        ["-f", "b"],
        ["-f", "b", "--extractor-args", "tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com"],
    ]
    last = ""
    for i, extra in enumerate(attempts, 1):
        for stale in d.glob("dl.*"):
            stale.unlink(missing_ok=True)
        cmd = [str(YTDLP), "--no-warnings", "-o", "dl.%(ext)s"] + extra
        if config.PROXY:
            cmd += ["--proxy", config.PROXY]
        cmd.append(url)
        _log(vid, f"下载原片（第 {i} 次尝试：{' '.join(extra)}）", progress=12 + i * 4)
        rc, out = _run(cmd, timeout=600, cwd=str(d))
        if rc == 0:
            # yt-dlp 可能留下分片（dl.f137.mp4）：排除后取最大文件，避免拿到无音轨片段。
            candidates = [p for p in d.glob("dl.*")
                          if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")
                          and p.stat().st_size > 10_000
                          and not re.search(r"\.f\d+\.", p.name)]
            got = max(candidates, key=lambda p: p.stat().st_size, default=None)
            if got is not None:
                if _container(_probe(got).get("format_name") or "") in _MP4_FAMILY:
                    os.replace(got, target)
                    return target
                raw = d / f"src{got.suffix.lower()}"
                os.replace(got, raw)
                try:
                    _to_mp4(vid, raw)
                except ReverseError as e:
                    # 转封装失败不能把整轮下载重试打断：记下来换下一种 -f 方案。
                    last = str(e)
                    _log(vid, f"转封装失败，换下一种下载方案：{last[:120]}")
                    continue
                finally:
                    raw.unlink(missing_ok=True)
                return target
        last = out.strip()[-300:]
        _log(vid, f"第 {i} 次下载失败：{last}")
    # 兜底：全部方案只落了音轨 → 多半是图文帖/无视频流，别只甩 yt-dlp 进度尾巴
    if any(p.suffix.lower() in (".mp3", ".m4a", ".aac", ".wav") for p in d.glob("dl.*")):
        raise ReverseError("原片下载失败：该帖只提供音轨（多为图文帖 Photo Mode），"
                           "没有视频流可下；请换该博主的视频帖链接")
    raise ReverseError(f"原片下载失败：{last}")


# ────────────────────────── P3 参数 ──────────────────────────
def _num(value, default: float = 0.0) -> float:
    """ffprobe 某些容器会回 "N/A"；别让它在 int()/float() 上炸成 500。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _probe(src: Path) -> dict:
    """读视频参数。

    ★ 2026-10-23 验收 A1：WebM/MKV 的时长常只写在容器（format）层，视频流 duration 为空。
      只读流层会把合法短视频读成 0 秒，进而被「60 秒以内」闸门误杀。
      取值顺序：流层 → 容器层 → nb_frames/fps；三者都拿不到时 duration_source=''
      表示「读不到时长」，由调用方给专门提示，不再冒充超限。
    """
    cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,r_frame_rate,nb_frames,duration", "-of", "json", str(src)]
    rc, out = _run(cmd, timeout=60)
    info: dict = {}
    try:
        st = (json.loads(out).get("streams") or [{}])[0]
    except Exception:
        st = {}
    fr = st.get("r_frame_rate") or "0/1"
    try:
        num, den = fr.split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0.0
    except Exception:
        fps = 0.0
    info["width"] = int(_num(st.get("width")))
    info["height"] = int(_num(st.get("height")))
    info["fps"] = fps
    info["nb_frames"] = int(_num(st.get("nb_frames")))
    info["duration"] = round(_num(st.get("duration")), 2)
    info["duration_source"] = "stream" if info["duration"] > 0 else ""
    # ★ 2026-10-23 审查 R12：容器格式一次取回，导入/下载时据此决定要不要转封装。
    rc_c, out_c = _run([FFPROBE, "-v", "error", "-show_entries",
                        "format=format_name,duration", "-of", "json", str(src)], timeout=60)
    try:
        fmt = json.loads(out_c).get("format") or {}
    except (ValueError, TypeError, AttributeError):
        fmt = {}
    info["format_name"] = str(fmt.get("format_name") or "")
    try:
        c_dur = float(fmt.get("duration") or 0)
    except (ValueError, TypeError):
        c_dur = 0.0
    if info["duration"] <= 0 and c_dur > 0:
        info["duration"] = round(c_dur, 2)
        info["duration_source"] = "format"
    if info["duration"] <= 0 and info["fps"] > 0 and info["nb_frames"] > 0:
        info["duration"] = round(info["nb_frames"] / info["fps"], 2)
        info["duration_source"] = "frames"
    info["has_video"] = bool(info["width"] and info["height"])
    # 音轨
    rc2, out2 = _run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries",
                      "stream=codec_name,channels", "-of", "json", str(src)], timeout=60)
    try:
        audio = (json.loads(out2).get("streams") or [])
    except Exception:
        audio = []
    info["has_audio"] = bool(audio)
    info["audio_codec"] = audio[0].get("codec_name") if audio else ""
    return info


# ────────────────────────── 容器转封装（R12） ──────────────────────────
_MP4_FAMILY = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}


def _container(probe_name: str) -> str:
    """ffprobe format_name 可能是 'matroska,webm' 这种逗号串，取第一段。"""
    return (probe_name or "").split(",")[0].strip().lower()


def _to_mp4(vid: str, src: Path) -> Path:
    """非 MP4 容器转封装成 MP4（copy，不重编码），保证 MIME 与浏览器兼容。

    ★ 2026-10-23 审查 R12：StaticFiles 按扩展名发 video/mp4，文件其实是
      VP9/WebM 时 Safari 之类不嗅探的浏览器会黑屏。先试纯 copy（秒级），
      音轨不兼容时只把音频转 aac（视频仍不重编码）。
    """
    final = _dir(vid) / "src.mp4"
    out = _dir(vid) / ".src-remux.mp4"
    # ① 纯 copy（秒级）；② 音轨不兼容时只转音频；③ 容器实在装不下（极少见）才重编码视频。
    for extra in (["-c", "copy"],
                  ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"],
                  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", "-b:a", "128k"]):
        rc, _msg = _run([FFMPEG, "-v", "error", "-i", str(src), *extra,
                         "-movflags", "+faststart", str(out), "-y"], timeout=300)
        if rc == 0 and out.is_file() and out.stat().st_size > 10_000:
            # 先写临时名，成功才替换：旧原片在转封装失败时仍然保留（R9）。
            os.replace(out, final)
            return final
    out.unlink(missing_ok=True)
    raise ReverseError("视频转封装失败（可能是不支持的编码），请先转成 mp4 再导入")


# ────────────────────────── P4 抽帧 + 场景检测 ──────────────────────────
# ffmpeg showinfo / metadata=print 的时间戳；兼容科学计数法（pts_time:1e-05）
_PTS_RE = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def _frames(vid: str, src: Path, dur: float) -> list[dict]:
    d = _dir(vid)
    fdir = d / "frames"
    if fdir.exists():
        shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-v", "info", "-i", str(src), "-vf",
           f"fps={FPS_SAMPLE},scale={FRAME_MAX_W}:-2,showinfo",
           str(fdir / "f_%03d.jpg"), "-y"]
    rc, out = _run(cmd, timeout=600)
    if rc != 0:
        raise ReverseError("抽帧失败：" + out.strip()[-300:])
    files = sorted(fdir.glob("f_*.jpg"), key=lambda p: p.name)
    # ★ 2026-10-23 审查 R17：旧版用 i/2 推算帧时间，首帧时间戳不为 0 时整条
    #   时间轴会系统性偏移。现在从同一次 showinfo 输出里取真实 pts_time，
    #   数量对不上才退回 1/2s 推算（并记日志）。
    times = [round(float(x), 3) for x in _PTS_RE.findall(out)]
    if len(times) != len(files):
        if files:
            _log(vid, f"帧时间戳与抽帧数量不一致（{len(times)} vs {len(files)}），"
                      f"退回 1/{FPS_SAMPLE}s 推算")
        times = [round(i / FPS_SAMPLE, 3) for i in range(len(files))]
    return [{"n": i + 1, "t": times[i], "path": p.name} for i, p in enumerate(files)]


def _scenes(vid: str, src: Path) -> list[float]:
    """轨 B：场景变化检测。无输出不等于单镜头（SOP P4 明确），只作参考。"""
    cmd = [FFMPEG, "-v", "info", "-i", str(src), "-vf",
           f"select='gt(scene,{SCENE_TH})',metadata=print", "-f", "null", "-"]
    rc, out = _run(cmd, timeout=600)
    ts = sorted({round(float(m), 2) for m in _PTS_RE.findall(out)})
    return ts


def _audio(vid: str, src: Path, has_audio: bool) -> None:
    if not has_audio:
        return
    out = _dir(vid) / "audio.wav"
    if out.exists() and out.stat().st_size > 1024:
        return
    # ★ 2026-10-23 审查 R17：旧版丢弃返回码，转音频失败也静默继续，
    #   报告仍写「含音轨」。现在失败即报错，可用「重建素材」重试。
    rc, msg = _run([FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out), "-y"], timeout=300)
    if rc != 0 or not out.is_file() or out.stat().st_size <= 1024:
        raise ReverseError("音轨剥离失败：" + (msg or "").strip()[-200:]
                           + "；可用「重建素材」重试")


# ────────────────────────── 送模型的帧挑选（SOP P5） ──────────────────────────
def _pick_frames(frames: list[dict], dur: float, scenes: list[float]) -> list[dict]:
    if not frames:
        return []
    if dur <= 6:
        pick = frames                                    # 短片全看
    else:
        step = max(1, int(round(2.0 * FPS_SAMPLE)))      # ≥10s：每 2 秒 1 张
        pick = frames[::step]
        if frames[-1] not in pick:
            pick.append(frames[-1])                      # 结尾定格必看
    for t in scenes:                                     # 切点处补帧
        near = min(frames, key=lambda f: abs(f["t"] - t))
        if near not in pick:
            pick.append(near)
    pick = sorted({f["n"]: f for f in pick}.values(), key=lambda f: f["t"])
    if len(pick) > MAX_FRAME_FOR_AI:
        # Spread over the full timeline; truncating a prefix loses the ending.
        pick = [pick[round(i * (len(pick)-1) / (MAX_FRAME_FOR_AI-1))]
                for i in range(MAX_FRAME_FOR_AI)]
    return pick


# ────────────────────────── 视觉模型 ──────────────────────────
def load_vision_models() -> list[dict]:
    if not MODELS_JSON.exists():
        return []
    try:
        raw = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = raw if isinstance(raw, list) else raw.get("models", [])
    return [m for m in items if m.get("supportsImages") and m.get("apiKey") and m.get("url")]


def pick_model(name: str = "") -> dict:
    models = load_vision_models()
    if not models:
        raise ReverseError(f"没有可用的视觉模型：{MODELS_JSON} 里没有 supportsImages=true 的条目")
    if name:
        for m in models:
            if m.get("id") == name or m.get("name") == name:
                return m
    for m in models:
        if m.get("id") == "deepseek-flash":
            return m
    return models[0]


_RETRY_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}


def _chat(model: dict, content: list, max_tokens: int = 4000, timeout: int = 420,
          retries: int = 2) -> str:
    """调视觉/文本模型。

    空返回自动重试并加倍 max_tokens（推理模型会把预算全花在 reasoning 上、
    正文为空，实测 deepseek-flash 4 帧时会出现）。
    ★ 2026-10-23 审查 R5：429/5xx/网络抖动按指数退避重试；400/401/403/404
      等参数或鉴权错误直接失败（重试也没用）。旧版一次抖动就整单失败。
    """
    body_base = {
        "model": model.get("id") or model.get("name"),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.4,
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连，只建一次
    last_err = ""
    budget = max_tokens
    for attempt in range(retries + 1):
        body = dict(body_base, max_tokens=budget)
        req = urllib.request.Request(
            str(model["url"]).rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + str(model["apiKey"])},
        )
        try:
            r = json.load(opener.open(req, timeout=timeout))
        except urllib.error.HTTPError as e:
            raw = e.read()[:300].decode("utf-8", "replace")
            if e.code in _RETRY_HTTP and attempt < retries:
                last_err = f"HTTP {e.code} {raw[:80]}"
                time.sleep(min(8, 2 ** attempt))
                continue
            raise ReverseError(f"视觉模型返回 {e.code}：{raw}")
        except ValueError as e:
            # 网关返回 200 + HTML 错误页时 json.load 抛 JSONDecodeError（ValueError）。
            last_err = f"响应不是 JSON：{e}"
            if attempt < retries:
                time.sleep(min(8, 2 ** attempt))
                continue
            raise ReverseError(f"视觉模型返回非 JSON（已重试 {retries} 次）：{last_err}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, http.client.HTTPException) as e:
            if attempt < retries:
                last_err = f"{type(e).__name__} {e}"
                time.sleep(min(8, 2 ** attempt))
                continue
            raise ReverseError(
                f"视觉模型调用失败（已重试 {retries} 次）：{last_err or e}") from None
        if not isinstance(r, dict):
            raise ReverseError("视觉模型返回格式异常：" + str(r)[:300])
        ch = (r.get("choices") or [{}])[0]
        if not isinstance(ch, dict):
            ch = {}
        txt = ((ch.get("message") or {}).get("content") or "").strip()
        if txt:
            return txt
        usage = r.get("usage") or {}
        last_err = (f"空正文（finish_reason={ch.get('finish_reason')}, "
                    f"reasoning_tokens={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')}, "
                    f"max_tokens={budget}）")
        budget = min(20000, budget * 2)
    raise ReverseError(f"模型连续 {retries + 1} 次返回空内容：{last_err}")



_FENCE_HEAD = re.compile(r"^```[A-Za-z0-9_+-]*[ \t]*\r?\n?")
_FENCE_TAIL = re.compile(r"\r?\n?```[ \t]*$")


def _strip_fence(t: str) -> str:
    t = (t or "").strip()
    if "```" in t[:20]:
        t = _FENCE_HEAD.sub("", t)
        t = _FENCE_TAIL.sub("", t)
    return t.strip()


def _repair_json(t: str) -> str:
    """模型笔误兜底：中文引号当定界符、尾逗号、BOM。"""
    t = t.lstrip("\ufeff")
    t = (t.replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2018", "'").replace("\u2019", "'"))
    return re.sub(r",\s*([}\]])", r"\1", t)


def _first_opener(t: str):
    """返回最先出现的 JSON 开符号 (char, idx)：模型可能先写 `[`（数组）也可能直接写 `{`。"""
    i, j = t.find("["), t.find("{")
    if i < 0 and j < 0:
        return "", -1
    if i < 0:
        return "{", j
    if j < 0:
        return "[", i
    return ("[", i) if i < j else ("{", j)


def _balanced(t: str, i: int):
    """从 t[i] 的括号开始做配平扫描，返回完整片段；未闭合（被截断）返回 None。"""
    pairs = {"[": "]", "{": "}"}
    stack: list[str] = []
    in_str = esc = False
    for k in range(i, len(t)):
        ch = t[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "]}":
            if not stack or stack[-1] != ch:
                return None
            stack.pop()
            if not stack:
                return t[i:k + 1]
    return None


def _scan_values(t: str, i: int) -> list:
    """从 i 起顺序解析 JSON 值，遇坏掉的即停（截断/多值拼接的头部抢救）。"""
    dec = json.JSONDecoder()
    out: list = []
    n = len(t)
    while i < n:
        while i < n and t[i] not in "[{":
            i += 1
        if i >= n:
            break
        try:
            val, i = dec.raw_decode(t, i)
        except ValueError:
            break
        out.append(val)
    return out


def _jload(text: str, partial: bool = False):
    """从模型输出里抠 JSON。

    覆盖 5 类真实故障：```json 围栏、前后解释文字、多值拼接、
    中文引号/尾逗号笔误、**输出被 max_tokens 截断**（partial=True 时返回
    已解析成功的头部元素，由调用方补读缺失部分）。
    """
    t = _strip_fence(text)
    if not t:
        raise ReverseError("模型返回空内容（没有任何正文）")
    cands = (t, _repair_json(t))
    for c in cands:
        try:
            return json.loads(c)
        except Exception:
            pass
    for c in cands:                                   # 模型把数组写成了并列对象：只在以 { 开头时成立
        op, i = _first_opener(c)
        if op == "{":
            vals = _scan_values(c, i)
            if len(vals) > 1 and all(isinstance(v, dict) for v in vals):
                return vals
            if len(vals) == 1:
                return vals[0]
    for c in cands:                                   # 前后有废话 → 括号配平截取（只从第一个开括号起）
        op, i = _first_opener(c)
        if i < 0:
            continue
        seg = _balanced(c, i)
        if not seg:
            continue
        for s in (seg, _repair_json(seg)):
            try:
                return json.loads(s)
            except Exception:
                pass
    if partial:                                       # 截断抢救：只交回完整头部
        for c in cands:
            op, i = _first_opener(c)
            if op != "[":
                continue
            vals = _scan_values(c, i + 1)
            if vals:
                return vals
    head = t[:160].replace("\n", " ")
    if t.count("[") > t.count("]") or t.count("{") > t.count("}"):
        raise ReverseError(f"模型输出被截断（括号未闭合，输出预算不够）：{head}…")
    raise ReverseError("模型没有返回可解析的 JSON：" + head)


def _as_int(value, default: int = 0) -> int:
    """从模型输出里抠整数：'S1'→1、'第3段'→3、'2.0s'→2；抠不到用 default。"""
    m = re.search(r"-?\d+", str(value if value is not None else ""))
    return int(m.group()) if m else default


def _as_float(value, default: float = 0.0) -> float:
    """从模型输出里抠数字：'2.0s'→2.0、'1.5–2.0s'→1.5；抠不到用 default。"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(value if value is not None else ""))
    return float(m.group()) if m else default


def validate_ad_structure(value, duration: float) -> dict:
    """ad 模式的最小结构校验：分镜非空、idx 可解析且唯一、时间轴单调不越界。

    ★ 2026-10-23 审查 R6：旧版只查 shots 非空，模型把 idx 写成 "S1"、t_end 写成
      "2.0s" 时会在导出首帧（93%、钱已花完）抛 ValueError，用户拿不到报告。
      这里只做「容错解析 + 规范化」，不因个别字段的写法异常整单失败。
    """
    if not isinstance(value, dict):
        raise ReverseError("结构分析没有返回 JSON 对象（模型输出不合规），请重试或换模型")
    shots = value.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ReverseError("结构分析没有返回分镜（模型输出不合规），请重试或换模型")
    if len(shots) > 64:
        raise ReverseError("分镜数量异常（>64），请重试或换模型")
    prev = 0.0
    seen: set[int] = set()
    for i, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ReverseError(f"第 {i} 个分镜不是对象（模型输出不合规），请重试或换模型")
        idx = _as_int(shot.get("idx"), i)
        if idx <= 0 or idx in seen:
            idx = i
            while idx in seen:          # 重编号也要避开已占用的号，否则按 idx 覆盖会丢段
                idx += 1
        seen.add(idx)
        start = _as_float(shot.get("t_start"), prev)
        end = _as_float(shot.get("t_end"), start + 1.0)
        if duration > 0:
            # 起点也要留 0.05s 余量，否则 clamp 后 end 会越出片长 0.05s，
            # _export_refs 的 -ss 越界 → 最后一镜首帧丢失（review ②）。
            start = max(0.0, min(start, max(0.0, duration - 0.05)))
            end = max(start + 0.05, min(end, duration))
            end = min(end, duration)
        if end <= start:
            end = start + 0.05
        shot["idx"] = idx
        shot["t_start"], shot["t_end"] = round(start, 3), round(end, 3)
        shot["name"] = str(shot.get("name") or f"镜头{i}")[:100]
        prev = shot["t_end"]
    return value


STRICT_JSON = (
    "上一次输出不是合法 JSON（可能被截断或夹了解释文字）。这次只输出一个合法 JSON，"
    "不要 Markdown 围栏、不要任何前后说明，每个字段控制在 80 字以内。")


def _chat_json(model: dict, content: list, max_tokens: int = 6000,
               partial: bool = False, retries: int = 1):
    """要 JSON 的调用：解析失败自动加「只输出 JSON」再问一次。"""
    txt = _chat(model, content, max_tokens=max_tokens)
    try:
        return _jload(txt, partial=partial)
    except ReverseError as e:
        first = str(e)
        if retries <= 0:
            raise
    txt2 = _chat(model, list(content) + [{"type": "text", "text": STRICT_JSON}],
                 max_tokens=max_tokens)
    try:
        return _jload(txt2, partial=partial)
    except ReverseError as e2:
        raise ReverseError(f"{e2}（首次尝试：{first[:120]}）") from None


def _img_part(p: Path) -> dict:
    b64 = base64.b64encode(p.read_bytes()).decode()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


# ────────────────────────── AI 阶段 ──────────────────────────
DESCRIBE_SYS = (
    "你是 TikTok 带货视频的画面拆解员。只看图，不推测、不补全。"
    "读不出的项写「不可辨」，不许用文案常识代替画面。商品颜色必须写到色卡级别"
    "（如「深炭灰」而非「深色」）。只输出 JSON，不要解释。"
)


FRAME_SCHEMA = (
    '{"t":秒数,"person":"人物：年龄段/族裔/发型/妆容/体型/表情",'
    '"product":"商品：品类/精确颜色/结构细节(五金·标签·缝线·印花)/材质反光",'
    '"outfit":"搭配：其他服饰/鞋/配饰",'
    '"scene":"场景：地点类型/地面材质/背景元素/天气/可验细节(积雪·水渍)",'
    '"action":"动作：手脚位置与朝向/视线/表情变化(写到能直接演)",'
    '"camera":"镜头：景别/机位高度/运动方式(推拉摇跟手持)/是否变焦/焦点",'
    '"light":"光线：光源性质/阴影软硬/主色调/饱和度"}')


def _describe_one(vid: str, model: dict, f: dict) -> dict:
    """单帧兜底重读：批量输出被截断时逐帧补，避免整条反推失败。"""
    head = (DESCRIBE_SYS + "\n\n" + f"这是某条 TikTok 视频 {f['t']:.1f}s 处的一帧。"
            "输出**单个 JSON 对象**（不要数组、不要围栏），字段：\n" + FRAME_SCHEMA
            + "\n每个字段 40–80 字，写重点即可，禁止超长。")
    item = _chat_json(model, [{"type": "text", "text": head},
                              _img_part(_dir(vid) / "frames" / f["path"])],
                      max_tokens=4000)
    if isinstance(item, list):
        item = item[0] if item and isinstance(item[0], dict) else {}
    if not isinstance(item, dict) or not item:
        raise ReverseError(f"{f['t']:.1f}s 这一帧读取失败（模型两次都没给出可用 JSON）")
    return item


def _describe_frames(vid: str, model: dict, frames: list[dict], base_pct: int, span: int,
                     cancel: threading.Event | None = None) -> list[dict]:
    """分批读帧 → 每帧 7 类信息（人物/商品/场景/动作/镜头/光线）。

    输出被截断时（推理模型很常见）先抢救已解析的头部帧，再逐帧补读，不整条失败。
    """
    out: list[dict] = []
    batches = [frames[i:i + BATCH_FRAMES] for i in range(0, len(frames), BATCH_FRAMES)]
    for bi, batch in enumerate(batches, 1):
        _check_cancel(cancel)
        ts = "、".join(f"{f['t']:.1f}s" for f in batch)
        head = DESCRIBE_SYS + "\n\n" + (
            f"这是同一条 TikTok 视频按 {FPS_SAMPLE}fps 抽出的连续帧，第 {bi}/{len(batches)} 批，"
            f"对应时间点依次为 {ts}。逐帧提取，输出 JSON 数组，长度必须等于 {len(batch)}，每项字段：\n"
            + FRAME_SCHEMA
            + "\n每个字段 40–80 字，写重点即可；描述过长会被截断导致解析失败。")
        content: list = [{"type": "text", "text": head}]
        for f in batch:
            content.append(_img_part(_dir(vid) / "frames" / f["path"]))
        got = _chat_json(model, content, max_tokens=12000, partial=True)
        if not isinstance(got, list):
            got = [got] if isinstance(got, dict) else []
        # ★ 2026-10-21 独立验收 P2-1：绝不能「先过滤非 dict、再按新位置配对」——
        #   [帧1, null, 帧3] 过滤成两项后，帧3 会被绑到帧2 的时间点，整条时间轴错位。
        #   这里保留原槽位：只对缺失/无效槽位逐帧补读，补到的描述落回它自己的槽位。
        raw = list(got[:len(batch)])
        slots: list = [g if isinstance(g, dict) and g else None for g in raw]
        slots += [None] * (len(batch) - len(slots))
        filled = sum(1 for s in slots if s is not None)
        if not filled:
            raise ReverseError("读帧结果为空（模型没有返回任何可用画面描述）")
        if filled < len(batch):
            why = "被截断" if len(raw) < len(batch) else "含无效项"
            _log(vid, f"读帧 {bi}/{len(batches)} 批{why}，只拿到 {filled}/{len(batch)} 帧，按原槽位逐帧补读")
        for k in range(len(batch)):
            item = slots[k]
            if item is None:
                item = _describe_one(vid, model, batch[k])
                slots[k] = item
            item["t"] = float(batch[k]["t"])
            item["n"] = batch[k]["n"]
            out.append(item)
        pct = base_pct + int(span * bi / len(batches))
        _log(vid, f"读帧 {bi}/{len(batches)} 批完成（{len(out)} 帧）", progress=pct)
    return out


STRUCT_PROMPT = """你是 TikTok 带货片的结构分析师。下面是同一条视频的逐帧画面描述和元数据，请输出分镜反推。

硬性规则：
- 切点误差标注 ±0.2s；{dur:.1f}s 的视频切点控制在 5–8 个镜头。
- 每个镜头必须能回答「它承担什么信息点」，答不出就合并到相邻镜头。
- 商品描述只依据帧描述里出现过的细节，不许补常识。
- 结构配比 hook/selling/proof/ending 四个百分比必须加到 100。

只输出 JSON，字段如下：
{{"product_summary":"商品一句话（品类+颜色+关键结构）",
 "person_summary":"人物一句话（跨段必须一致的写法）",
 "scene_summary":"场景一句话",
 "hook_line":"钩子句（原 desc 第一行，没有就按画面写一句疑问句）",
 "phys_proof":"有无可验证的物理证明(雪/水/风/摩擦)，一句话；没有写「无」",
 "why":[{{"point":"这条片子为什么能跑","basis":"依据"}}],
 "ratio":{{"hook":0,"selling":0,"proof":0,"ending":0}},
 "shots":[{{"idx":1,"name":"场景锚定","t_start":0.0,"t_end":1.8,
   "shot_size":"中全景·低机位缓推","action":"画面与动作","info":"这一镜的说服任务"}}]}}

元数据：
- 作者：{author} · 时长 {dur:.2f}s · {w}×{h} · {fps}fps
- 文案：{desc}
- hashtag：{tags}
- 场景检测切点（参考，可能漏检）：{scenes}

逐帧描述：
{frames}
"""


PROMPT_SYS = (
    "你是 Seedance 2.0 图生视频提示词工程师，写给中文母语的投放团队看。"
    "严格按《Seedance 2.0 通用提示词模板》的六要素顺序填字段："
    "主体 → 场景环境 → 动作表演 → 运镜方式 → 光影氛围 → 风格质感；"
    "图生视频还要写「保持不变」，音频单独成句，负向词由程序统一追加。"
    "动作要量化、连续，一条 10 秒片只写 2–3 个递进动作；运镜用推拉摇移跟、环绕、俯冲、手持、浅景深等专业术语，"
    "一句话最多两个运镜变化；风格用一句话收尾定调；音频分别写环境声/音效/台词/BGM。"
    "禁止：把字幕/价格/水印/文字贴片写进画面；跨段省略商品描述（写「同上」）；"
    "绝对化形容词（perfect/flawless/完美/无瑕/高质量）；不要自己输出段落标题，由程序按模板拼装。"
)


# ────────────────────────── 报告渲染 ──────────────────────────
def _esc(s) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


CSS = """
:root{--ink:#111827;--sub:#5f6672;--line:#e5e7eb;--card:#fff;--bg:#f7f7f8;--pri:#b45309;
--pri-l:#fdf6ec;--green:#1f7a4d;--red:#b3261e;--amber:#92400e;--mono:"Cascadia Code",Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:0 0 6px}
h2{font-size:18px;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 8px}
.muted{color:var(--sub);font-size:12.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
.meta{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px 18px}
.meta div span{display:block;font-size:11.5px;color:var(--sub);letter-spacing:.02em}
.meta div b{font-weight:600;font-size:13.5px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--card)}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#faf9f7;font-weight:600;font-size:12.5px;color:var(--sub);white-space:nowrap}
td.t{font-variant-numeric:tabular-nums;white-space:nowrap;font-family:var(--mono);font-size:12px}
.why li{margin:6px 0}
.why b{color:var(--pri)}
.shot{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:14px 0;overflow:hidden}
.shot-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;background:var(--pri-l);border-bottom:1px solid var(--line)}
.shot-h .no{font-weight:700;color:var(--pri)}
.shot-h .du{font-family:var(--mono);font-size:12px;color:var(--sub)}
.shot-b{padding:6px 14px 14px}
.shot-b .ln{font-size:12px;color:var(--sub);margin:8px 0 2px}
.shot-b .ln code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-family:var(--mono);font-size:11.5px}
pre{margin:8px 0 0;background:#141210;color:#e7e5e4;padding:14px;border-radius:8px;white-space:pre-wrap;
word-break:break-word;font-family:var(--mono);font-size:12.5px;line-height:1.75}
.cp{float:right;margin-top:8px;padding:5px 12px;border:1px solid var(--line);background:#fff;border-radius:7px;
font:inherit;font-size:12px;cursor:pointer;color:var(--ink)}
.cp:hover{border-color:var(--pri);color:var(--pri)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.grid figure{margin:0}
.grid img{width:100%;border-radius:8px;border:1px solid var(--line);display:block}
.grid figcaption{font-size:11.5px;color:var(--sub);margin-top:5px;font-family:var(--mono)}
.ratio{display:flex;height:12px;border-radius:6px;overflow:hidden;margin:10px 0;background:#eee}
.ratio i{display:block}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;background:#f3f4f6;color:var(--sub);margin-right:6px}
.warn{background:#fffbeb;border:1px solid #fde68a;color:var(--amber);padding:10px 14px;border-radius:9px;font-size:13px;margin:10px 0}
.red{background:#fef2f2;border:1px solid #fecaca;color:var(--red);padding:10px 14px;border-radius:9px;font-size:13px;margin:10px 0}
.neg{background:#f9fafb;border:1px dashed var(--line);padding:10px 14px;border-radius:9px;font-family:var(--mono);font-size:12px;color:var(--sub)}
"""

JS = """
function cp(id,btn){var e=document.getElementById(id);if(!e)return;
var t=e.innerText;navigator.clipboard.writeText(t).then(function(){var o=btn.innerText;
btn.innerText='已复制';setTimeout(function(){btn.innerText=o},1200);});}
"""


def _render_report(vid: str, st: dict, meta: dict, probe: dict, ai: dict) -> str:
    if ai.get('_mode') == 'restore':
        import reverse_restore
        return reverse_restore.render_report(vid, ai)
    shots = ai.get("shots") or []
    prompts = {_as_int(p.get("idx"), 0): (p.get("text") or "")
               for p in (ai.get("prompts") or []) if isinstance(p, dict)}
    ratio = ai.get("ratio") or {}
    why = [w for w in (ai.get("why") or []) if isinstance(w, dict)]
    narr = ai.get("narration") or {}
    if not isinstance(narr, dict):
        narr = {}
    comp = [c for c in (ai.get("compliance") or []) if isinstance(c, dict)]
    tmpl = [t for t in (ai.get("template") or []) if isinstance(t, dict)]
    dur = float(probe.get("duration") or meta.get("duration") or 0)   # ffprobe 更精确（yt-dlp 取整）
    refs = sorted((_dir(vid) / "refs").glob("*.png")) if (_dir(vid) / "refs").exists() else []

    def pct(k):
        try:
            return max(0, min(100, int(round(float(ratio.get(k) or 0)))))
        except Exception:
            return 0

    bar_colors = [("hook", "#b45309"), ("selling", "#1f4e79"),
                  ("proof", "#1f7a4d"), ("ending", "#9ca3af")]
    bar = "".join(f'<i style="width:{pct(k)}%;background:{c}" title="{k} {pct(k)}%"></i>'
                  for k, c in bar_colors)

    def _clean_name(n) -> str:
        """去掉模型爱加的切点备注（表格「时间轴」列已经统一标 ±0.2s）。"""
        t = re.sub(r"[（(]\s*切点[^）)]*[）)]", "", str(n or ""))
        t = re.sub(r"[；;、]?\s*切点\s*[±+\-]?\s*0?\.?2\s*s?", "", t)
        return t.strip(" ；;、")

    rows = "".join(
        f'<tr><td class="t">S{_esc(s.get("idx"))}</td>'
        f'<td class="t">{_esc(s.get("t_start"))}–{_esc(s.get("t_end"))} ±0.2s</td>'
        f'<td>{_esc(_clean_name(s.get("name")))}</td><td>{_esc(s.get("shot_size"))}</td>'
        f'<td>{_esc(s.get("action"))}</td><td>{_esc(_clean_name(s.get("info")))}</td></tr>'
        for s in shots)

    cards = ""
    for s in shots:
        idx = _as_int(s.get("idx"), 0)
        txt = prompts.get(idx) or "（本段提示词缺失：模型未返回，请重跑 AI 反推）"
        ref = next((r for r in refs if r.name.startswith(f"s{idx}_")), None)
        ref_cell = f"refs/{ref.name}" if ref else "未导出"
        pid = f"p{idx}"
        cards += (
            f'<div class="shot"><div class="shot-h"><span class="no">S{idx}</span>'
            f'<b>{_esc(_clean_name(s.get("name")))}</b>'
            f'<span class="du">{_esc(s.get("t_start"))}–{_esc(s.get("t_end"))}s · '
            f'时长 {round(_as_float(s.get("t_end")) - _as_float(s.get("t_start")), 2)}s</span>'
            f'<span class="muted" style="margin-left:auto">{_esc(s.get("shot_size"))}</span></div>'
            f'<div class="shot-b"><div class="ln">首帧 <code>{_esc(ref_cell)}</code>'
            f'　信息点 <code>{_esc(s.get("info"))}</code></div>'
            f'<button class="cp" onclick="cp(\'{pid}\',this)">复制提示词</button>'
            f'<pre id="{pid}">{_esc(txt)}</pre></div></div>')

    figs = ""
    for r in refs:
        m = re.match(r"s(\d+)_(.+)\.png$", r.name)
        idx = m.group(1) if m else "?"
        name = m.group(2) if m else r.stem
        t = next((s.get("t_start") for s in shots if str(s.get("idx")) == idx), "")
        figs += (f'<figure><img src="refs/{_esc(r.name)}" alt="S{idx} 首帧">'
                 f'<figcaption>S{idx} · {_esc(name)} · {_esc(t)}s</figcaption></figure>')

    why_html = "".join(f'<li><b>{_esc(w.get("point"))}</b> —— {_esc(w.get("basis"))}</li>'
                       for w in why) or "<li>模型未给出分析</li>"
    comp_html = "".join(f'<li><b>{_esc(c.get("rule"))}</b> —— {_esc(c.get("risk"))}</li>'
                        for c in comp) or "<li>按 SOP 第 7.3 节六条红线执行</li>"
    tmpl_rows = "".join(
        f'<tr><td class="t">{_esc(t.get("seg"))}</td><td>{_esc(t.get("func"))}</td>'
        f'<td>{_esc(t.get("formula"))}</td><td class="t">{_esc(t.get("pct"))}</td></tr>'
        for t in tmpl)
    tmpl_table = (f'<table><thead><tr><th>段</th><th>功能</th><th>通用公式</th><th>时长占比</th></tr>'
                  f'</thead><tbody>{tmpl_rows}</tbody></table>') if tmpl else \
        '<p class="muted">模型未返回模板，见 SOP 第 5 节骨架表。</p>'

    audio_note = ("原片含音轨（%s），**未做 ASR**：本工作台不做语音转写，旁白按下表重写版使用。"
                  "SOP 记录该工具对德语人声识别基本无效，如需原文请人工听写。"
                  % _esc(probe.get("audio_codec") or "未知编码")) if probe.get("has_audio") else \
                 "原片无音轨，旁白为纯重写版。"

    a_words = narr.get("a_words") or 0
    b_words = narr.get("b_words") or 0
    limit = int(round(dur * 2.3)) if dur else 0

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TK反推报告_{_esc(vid)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{'TikTok 图文帖提示词反推' if meta.get('photo_mode') else 'TikTok 视频提示词反推'}</h1>
<p class="muted">@{_esc(meta.get('author'))} · {_esc(vid)} · 反推 {time.strftime('%Y-%m-%d %H:%M')} · 模型 {_esc(st.get('model') or '-')}</p>

<div class="card"><div class="meta">
<div><span>作者</span><b>@{_esc(meta.get('author'))}{(' / ' + _esc(meta.get('nickname'))) if meta.get('nickname') else ''}</b></div>
<div><span>发布</span><b>{_esc(meta.get('upload_at'))}</b></div>
<div><span>时长</span><b>{dur:.2f}s</b></div>
<div><span>素材</span><b>{('图文帖 ' + str(meta.get('photo_images') or '?') + ' 张 · 每张 2s 合成') if meta.get('photo_mode') else '视频原片'}</b></div>
<div><span>分辨率</span><b>{probe.get('width') or meta.get('width')}×{probe.get('height') or meta.get('height')}</b></div>
<div><span>帧率</span><b>{probe.get('fps') or meta.get('fps') or '-'} fps</b></div>
<div><span>镜头数</span><b>{len(shots)}</b></div>
<div><span>播放 / 点赞</span><b>{meta.get('view', 0)} / {meta.get('like', 0)}</b></div>
<div><span>挂车线索</span><b>{'有（hashtag 命中）' if meta.get('has_shop_tag') else '未见'}</b></div>
</div>
<p class="muted" style="margin:14px 0 0">文案：{_esc((meta.get('desc') or '')[:220])}</p>
<p style="margin:8px 0 0">{''.join(f'<span class="tag">#{_esc(t)}</span>' for t in (meta.get('hashtags') or [])[:14])}</p>
</div>

<h2>一、这条片子为什么能跑</h2><ul class="why">{why_html}</ul>

<h2>二、分镜反推表</h2>
<table><thead><tr><th>#</th><th>时间轴</th><th>段名</th><th>景别 / 机位</th><th>画面与动作</th><th>信息点</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="muted" style="margin-top:10px">钩子 {pct('hook')}% / 卖点 {pct('selling')}% / 证明 {pct('proof')}% / 收尾 {pct('ending')}%（合计 {pct('hook') + pct('selling') + pct('proof') + pct('ending')}%）</p>
<div class="ratio">{bar}</div>
<p style="margin:6px 0 0"><b>物理证明</b>：{_esc(ai.get('phys_proof') or '无')}</p>

<h2>三、可直接使用的提示词</h2>
<p class="muted">参数：9:16 · 720p · 24fps · 图生视频 · 单段 4–15s。目标镜头短于 4s 的，生成 4–5s 后剪辑裁切，不许把 prompt 压成 1–2s。</p>
<p class="muted">提示词模板：{seedance.VERSION}（主体/场景环境/动作表演/运镜方式/光影氛围/风格质感 + 保持不变/音频/负向）。</p>
{cards}

<h2>四、首帧参考图</h2>
<p class="muted">原尺寸导出，未放大未裁切。i2v 优先用动作起始前的静止帧。</p>
<div class="grid">{figs or '<p class="muted">未导出首帧</p>'}</div>

<h2>五、旁白与字幕</h2>
<div class="warn">{audio_note}</div>
<h3>A 版 · 零风险（纯视觉描述，无功能宣称）</h3>
<button class="cp" onclick="cp('na',this)" style="float:none;margin:0 0 8px">复制</button>
<pre id="na">{_esc(narr.get('a') or '（未生成）')}</pre>
<p class="muted">词数 {a_words} / 参考上限 {limit} 词（德语 2.3 词/秒 × {dur:.2f}s）</p>
<h3>B 版 · 卖点版（每个功能词都需前置条件）</h3>
<button class="cp" onclick="cp('nb',this)" style="float:none;margin:0 0 8px">复制</button>
<pre id="nb">{_esc(narr.get('b') or '（未生成）')}</pre>
<p class="muted">词数 {b_words} / 参考上限 {limit} 词</p>
<p class="muted">注：{_esc(narr.get('notes') or '字幕行不超过 38 字符，超过拆两行；文字一律后期叠加，不进 prompt。')}</p>

<h2>六、合规与实操</h2>
<div class="red"><b>德国站 + TikTok Shop DE 红线</b>
<ul style="margin:8px 0 0">
<li>禁用最高级与绝对化：beste / perfekte / Nr.1 / immer / nie / garantiert</li>
<li>功能词（wasserdicht / winddicht / bis −20 °C / atmungsaktiv）必须有供应商书面参数，否则只能留在 B 版并标 ⚠</li>
<li>价格与尺码必须与详情页一致，TikTok 会比对</li>
<li>禁用 Sonderpreis / 虚假倒计时（无真实历史价支撑时）</li>
<li>健康与医疗功效一律不碰</li>
<li>他人画面仅可拆解参考，不可搬运二次发布；复用首帧需换模特</li>
</ul></div>
<h3>本片特别提示</h3><ul>{comp_html}</ul>
<h3>落地顺序</h3><ol>
<li>按第三节逐段生成（首帧用第四节图），每段 4–5s</li>
<li>ffmpeg 裁到目标时长 → 段间 3–5 帧叠化拼接</li>
<li>叠德语旁白（A 版可直接用）→ 后期叠字幕与价格贴片</li>
</ol>
<div class="neg">负向词（工具支持 negative 栏时填）：extra fingers, deformed hands, distorted legs, warped zipper, floating fabric, plastic skin, over-smooth skin, doll face, uncanny, oversaturated, warm sunset light, text watermark, logo distortion, cartoon, 3d render</div>

<h2>七、换品复用模板</h2>
{tmpl_table}
<p class="muted">换品时保留骨架，替换商品描述块 / 场景 / S5 的物理证明道具；人物与镜头语言不动。</p>
</div><script>{JS}</script></body></html>"""


# ────────────────────────── 首帧导出（P8） ──────────────────────────
def _export_refs(vid: str, src: Path, shots: list[dict]) -> list[str]:
    d = _dir(vid)
    rdir = d / "refs"
    if rdir.exists():
        shutil.rmtree(rdir, ignore_errors=True)
    rdir.mkdir(parents=True, exist_ok=True)
    names = []
    for s in shots:
        idx = _as_int(s.get("idx"), 0)
        t = float(s.get("t_start") or 0)
        slug = _slug(s.get("name") or "", idx)
        out = rdir / f"s{idx}_{slug}.png"
        rc, _ = _run([FFMPEG, "-v", "error", "-ss", f"{max(0.0, t + 0.05):.2f}",
                      "-i", str(src), "-frames:v", "1", str(out), "-y"], timeout=120)
        if rc == 0 and out.exists():
            names.append(out.name)
    return names


def _slug(s: str, idx: int = 0) -> str:
    """首帧文件名里的动作英文。模型给的段名常是中文自造词，
    统一走功能映射，映射不到就用序号兜底，避免出现 s2_0_2s.png 这种脏名。"""
    t = re.sub(r"[\d.]+\s*[–\-~]\s*[\d.]+\s*s?", " ", s or "")   # 先剥掉时间轴
    m = {"场景锚定": "scene", "钩子": "hook", "开场": "hook", "注意力": "point",
         "指向": "point", "手势": "point", "卖点密集": "detail", "特写": "detail",
         "细节": "detail", "近景": "detail", "版型": "walk", "行走": "walk",
         "走动": "walk", "走向": "walk", "物理证明": "proof", "证明": "proof",
         "触": "proof", "雪": "proof", "水": "proof", "收束": "local", "局部": "local",
         "复盘": "local", "记忆点": "smile", "收尾": "end", "笑脸": "smile", "露脸": "smile"}
    for k, v in m.items():
        if k in t:
            return v
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:16]
    return ascii_slug or f"shot{idx}"


# ────────────────────────── 主流程 ──────────────────────────
def _check_cancel(cancel: threading.Event | None = None) -> None:
    """批次/阶段边界的检查点：已请求取消就抛可读错误（由 _bg 落成「已取消」）。"""
    if cancel is not None and cancel.is_set():
        raise ReverseError("已取消（已完成的分镜与帧图保留）")


def _bg(vid: str, kind: str, fn, *a) -> None:
    """起后台线程。

    ★ 2026-10-23 审查 R3：每个任务配一个取消信号；目标函数必须接收
      cancel=... 关键字，并在批次/阶段边界调用 _check_cancel。
    """
    cancel = threading.Event()

    def runner_fn():
        try:
            fn(vid, *a, cancel=cancel)
        except ReverseError as e:
            if cancel.is_set():
                _save(vid, stage="stopped", stage_label="已取消", error="", busy="")
                _log(vid, "已取消（已完成的分镜与帧图保留）")
            else:
                _save(vid, stage="failed", stage_label="失败", error=str(e), busy="")
                _log(vid, "失败：" + str(e))
        except Exception as e:                                   # noqa: BLE001
            _save(vid, stage="failed", stage_label="异常中断",
                  error=f"{type(e).__name__}: {e}", busy="")
            _log(vid, f"异常：{type(e).__name__}: {e}")
        finally:
            with _LOCK:
                _WORKERS.pop(vid, None)
                _WORK_START.pop(vid, None)
                _CANCEL.pop(vid, None)

    t = threading.Thread(target=runner_fn, name=f"tkrev-{kind}-{vid}", daemon=True)
    with _LOCK:
        _WORKERS[vid] = (kind, t)
        _WORK_START[vid] = _now()
        _CANCEL[vid] = cancel
    t.start()


def create(raw_url: str) -> dict:
    """解析链接 + 取元数据 + 建任务目录，然后后台下载抽帧。"""
    url, vid = parse_url(raw_url)
    st0 = _load(vid) if vid else None
    if vid and st0 and st0.get("stage") in ("preparing", "analyzing"):
        raise ReverseError("这条视频正在处理中，等它跑完")

    if not vid:
        _need_tools()
        d = _yt_json(url)
        vid = str(d.get("id") or "")
        if not vid:
            raise ReverseError("无法从短链解析出 video id")
        vid = _check_vid(vid)
        meta = _norm_meta(d)
        if _is_photo(d):             # 图文帖：先确认图集取得到，再建任务
            meta = _photo_meta(url, meta)
    else:
        meta = {}

    _dir(vid).mkdir(parents=True, exist_ok=True)
    # ★ 2026-10-23 审查 R2/R13：「查阶段 → 置 preparing」必须在同一把锁内，
    #   否则两个并发请求会各起一个 prepare 线程互踩 frames 目录。
    #   复用分支带 reused/notice，前端不再假装「已开始生成素材」。
    with _LOCK:
        prev = _load(vid)
        if vid in _WORKERS or prev.get("stage") in ("preparing", "analyzing"):
            raise ReverseError("这条视频正在处理中，等它跑完")
        if prev.get("frames") and prev.get("stage") in ("ready", "done", "stopped"):
            _save(vid, url=url, meta=meta or prev.get("meta") or {}, busy="")
            _log(vid, "素材已存在，直接复用（要重跑素材请点「重建素材」）")
            out = status(vid)
            out["reused"] = True
            out["notice"] = ("素材已存在，直接复用；要生成提示词请点该卡片的「② AI 反推」"
                             if not out.get("has_report") else
                             "素材与报告都已存在；要重跑请点「重跑 AI 反推」")
            return out
        st = _blank(vid)
        st.update({"url": url, "meta": meta, "stage": "preparing",
                   "stage_label": "取元数据", "progress": 5, "busy": "prepare"})
        _STATE[vid] = st
        _save(vid, **st)
    _log(vid, f"开始处理 {vid}")
    _bg(vid, "prepare", _prepare, url, meta)
    return status(vid)


def _prepare(vid: str, url: str, meta: dict, fresh: bool = False,
             cancel: threading.Event | None = None) -> None:
    if not meta:
        _need_tools()
        raw = _yt_json(url)
        meta = _norm_meta(raw)
        if _is_photo(raw):
            meta = _photo_meta(url, meta)
        _save(vid, meta=meta)
    else:
        # 本地导入只需要 ffmpeg/ffprobe；不要把 yt-dlp 缺失也算进来。
        _need_tools(meta.get('origin') != 'local')
    _check_cancel(cancel)
    if meta.get('origin') == 'local':
        _stage(vid, 'preparing', '读取本地原片', 10, busy='prepare')
        src = _dir(vid) / 'src.mp4'
        if not src.is_file():
            raise ReverseError('本地原片不存在，请重新导入')
    elif meta.get("photo_mode"):
        n = int(meta.get("photo_images") or 0)
        _stage(vid, "preparing", f"下载图集（{n} 张）" if n else "下载图集", 10, busy="prepare")
        src, n2, per = _photo_slideshow(vid, url)
        meta["photo_images"] = n2
        meta["duration"] = round(n2 * per, 2)
        _save(vid, meta=meta)
        _log(vid, f"图文帖 {n2} 张 · 每张 {per}s → 合成素材片 {n2 * per:.1f}s")
    else:
        _stage(vid, "preparing", "下载原片", 10, busy="prepare")
        src = _download(vid, url, fresh=fresh)
    _check_cancel(cancel)

    _stage(vid, "preparing", "读取参数", 40)
    probe = _probe(src)
    if not probe.get("duration"):
        probe["duration"] = meta.get("duration") or 0
    _save(vid, probe=probe)
    _log(vid, f"{probe.get('width')}×{probe.get('height')} · {probe.get('fps')}fps · "
              f"{probe.get('duration')}s · 音轨{'有' if probe.get('has_audio') else '无'}")
    _check_cancel(cancel)

    _stage(vid, "preparing", "抽帧", 55)
    frames = _frames(vid, src, probe.get("duration") or 0)
    _log(vid, f"抽出 {len(frames)} 帧（{FPS_SAMPLE}fps）")
    _check_cancel(cancel)

    _stage(vid, "preparing", "场景检测", 70)
    if meta.get("photo_mode"):
        n_img = int(meta.get("photo_images") or 0)
        per_img = round((probe.get("duration") or 0) / max(1, n_img), 2)
        scenes = _photo_scenes(n_img, per_img)
        _log(vid, f"图文帖切点 {len(scenes)} 个（每张图一个镜头，按 {per_img}s/张推算）")
    else:
        scenes = _scenes(vid, src)
        _log(vid, f"场景检测切点 {len(scenes)} 个"
                  + ("（无输出≠单镜头，以抽帧视觉判断为准）" if not scenes else ""))
    _check_cancel(cancel)

    _stage(vid, "preparing", "剥离音轨", 82)
    _audio(vid, src, bool(probe.get("has_audio")))
    _check_cancel(cancel)

    ai_frames = _pick_frames(frames, probe.get("duration") or 0, scenes)
    manifest = {"meta": meta, "probe": probe, "frames": frames,
                "scenes": scenes, "ai_frames": ai_frames,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    mf = _dir(vid) / "manifest.json"
    mtmp = mf.with_suffix(".json.tmp")
    mtmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(mtmp, mf)      # 原子替换：中断不会留下半截 JSON

    _save(vid, stage="ready", stage_label="素材就绪", progress=100, busy="",
          frames=len(frames), ai_frames=len(ai_frames), scenes=scenes)
    _log(vid, f"素材就绪：{len(frames)} 帧 / {len(ai_frames)} 帧待读")


def import_local(temp: Path, filename: str) -> dict:
    """Import a completed local upload without invoking a model or downloader.

    ★ 2026-10-23 验收 A2：只查后缀名会放过「文本改名 .mp4」，而且旧实现先建任务目录、
      移文件、返回 preparing，后台才失败 —— 用户已经多了一条垃圾任务。
      现在先对临时文件做真实探测（有没有视频流、能不能读出时长），全过了才建任务。
    """
    _need_tools(False)          # 本地导入只用 ffmpeg/ffprobe，不需要 yt-dlp
    probe = _probe(temp)
    if not probe.get("has_video"):
        raise ReverseError('这个文件里没有视频流，不是有效视频（只改后缀名不会被接受）')
    if not probe.get("duration"):
        raise ReverseError('读不到视频时长，文件可能不完整或已损坏；请换一个视频，或先转成 mp4 再导入')
    vid = str(int(time.time()*1000)) + str(uuid.uuid4().int % 100000).zfill(5)
    directory = task_dir(vid)
    directory.mkdir(parents=True, exist_ok=False)
    # ★ 2026-10-23 审查 R12：WebM/MKV 不能直接改名成 src.mp4 —— 静态路由按扩展名
    #   发 video/mp4，Safari 这类不嗅探的浏览器会黑屏。非 MP4 容器先 copy 转封装。
    container = _container(probe.get("format_name") or "")
    raw = directory / f"src.{container or 'bin'}"
    try:
        shutil.move(str(temp), str(raw))
        if not container or container not in _MP4_FAMILY:
            _to_mp4(vid, raw)
            raw.unlink(missing_ok=True)
        elif raw != directory / "src.mp4":
            os.replace(raw, directory / "src.mp4")
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)   # 别留下半条任务
        raise
    meta = {'id': vid, 'origin': 'local', 'author': '本地素材', 'desc': Path(filename).name, 'url': '',
            'duration': probe['duration'], 'width': probe['width'], 'height': probe['height'],
            'fps': probe['fps']}
    st = _blank(vid)
    st.update(stage='preparing', stage_label='读取本地视频', busy='prepare', progress=5,
              meta=meta, probe=probe)
    _save(vid, **st)
    _bg(vid, 'prepare', _prepare, '', meta)
    return status(vid)


def analyze(vid: str, model_name: str = "", mode: str = 'ad') -> dict:
    if mode not in ('ad', 'restore'):
        raise ReverseError('请选择有效的反推模式')
    v = _check_vid(vid)
    # ★ 2026-10-23 审查 R2：检查阶段与置位必须在同一把锁内（TOCTOU），
    #   并把 _WORKERS 纳入判断，否则两个并发请求会各起一个线程、互踩同一批文件。
    with _LOCK:
        st = _load(v)
        if v in _WORKERS or st.get("stage") in ("preparing", "analyzing"):
            raise ReverseError("这条视频正在处理中")
        if st.get("stage") == "pending" or not st.get("frames"):
            raise ReverseError("素材还没生成，先点「生成素材」")
        model = pick_model(model_name)
        _save(v, stage="analyzing", stage_label="准备读帧", progress=2,
              busy="analyze", error="", model=model.get("id") or model.get("name"), mode=mode)
    _log(v, f"AI 反推开始 · 模型 {model.get('id') or model.get('name')}")
    _bg(v, "analyze", _analyze, model, mode)
    return status(v)


def cancel(vid: str) -> dict:
    """请求取消在跑的任务（线程在批次/阶段边界停下，中间产物保留）。

    ★ 2026-10-23 审查 R3：旧版没有任何取消入口，preparing/analyzing 期间
      删除/重建都被拒，用户只能干等。
    """
    v = _check_vid(vid)
    with _LOCK:
        ev = _CANCEL.get(v)
        if ev is None:
            raise ReverseError("这条任务当前没有在跑")
        ev.set()
    _log(v, "已请求取消，将在当前步骤结束后停止")
    return status(v)


def _analyze(vid: str, model: dict, mode: str = 'ad',
             cancel: threading.Event | None = None) -> None:
    if mode == 'restore':
        import reverse_restore
        return reverse_restore.run(vid, model, cancel=cancel)
    d = _dir(vid)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    meta, probe = manifest["meta"], manifest["probe"]
    ai_frames = manifest.get("ai_frames") or []
    dur = float(probe.get("duration") or meta.get("duration") or 0)
    if not ai_frames:
        raise ReverseError("没有可送模型的帧，请重建素材")

    _stage(vid, "analyzing", "逐帧读画面", 5)
    desc = _describe_frames(vid, model, ai_frames, base_pct=5, span=45, cancel=cancel)
    (d / "describe.json").write_text(json.dumps(desc, ensure_ascii=False, indent=1), encoding="utf-8")
    _check_cancel(cancel)

    _stage(vid, "analyzing", "拆结构", 55)
    prompt = STRUCT_PROMPT.format(
        dur=dur, author=meta.get("author") or "-",
        w=probe.get("width"), h=probe.get("height"), fps=probe.get("fps"),
        desc=(meta.get("desc") or "")[:600],
        tags=" ".join("#" + t for t in (meta.get("hashtags") or [])[:15]),
        scenes=manifest.get("scenes") or [],
        frames="\n".join(
            f"[{f['t']:.1f}s] 人物:{f.get('person')} | 商品:{f.get('product')} | "
            f"搭配:{f.get('outfit')} | 场景:{f.get('scene')} | 动作:{f.get('action')} | "
            f"镜头:{f.get('camera')} | 光线:{f.get('light')}"
            for f in desc))
    struct = _chat_json(model, [{"type": "text", "text": prompt}], max_tokens=14000)
    struct = validate_ad_structure(struct, dur)
    _log(vid, f"分镜 {len(struct['shots'])} 段，配比 "
              f"{struct.get('ratio')}", progress=70)

    _stage(vid, "analyzing", "写提示词", 72)
    prompts = _write_prompts(vid, model, struct, meta, probe, cancel=cancel)
    struct["prompts"] = prompts

    _stage(vid, "analyzing", "旁白与合规", 88)
    struct.update(_write_copy(vid, model, struct, meta, dur))

    _check_cancel(cancel)
    _stage(vid, "analyzing", "导出首帧", 93)
    refs = _export_refs(vid, d / "src.mp4", struct["shots"])
    _log(vid, f"首帧导出 {len(refs)} 张")

    struct["_model"] = model.get("id") or model.get("name")
    struct["_mode"] = "ad"          # 让 status 能按报告自身判定模式（R16 不再靠 state 猜）
    struct["_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (d / "report.json").write_text(json.dumps(struct, ensure_ascii=False, indent=1), encoding="utf-8")

    html = _render_report(vid, {"model": struct["_model"]}, meta, probe, struct)
    (d / f"TK反推报告_{vid}.html").write_text(html, encoding="utf-8")

    _save(vid, stage="done", stage_label="已完成", progress=100, busy="",
          shots=len(struct["shots"]), has_report=True, mode='ad', bundle='', restore_refs=0)
    _log(vid, "报告已生成")


# 真实人物质感模块（SOP 4.3 强制项）：不依赖模型自觉，代码层面兜底追加。
# 用户硬性要求：出现真人的每段 prompt 必须带 realism 咒语，否则生成容易出塑料感。
REALISM_HUMAN = (
    "真实人物质感：realistic human, natural facial expressions, authentic relaxed smile, "
    "realistic skin texture with visible pores, natural hair strands, natural hand gestures "
    "with correct finger count, no uncanny AI look, no plastic skin, no over-smoothing."
)
REALISM_LEGS = " no distorted limbs, correct leg anatomy."
REALISM_PRODUCT = (
    "真实质感：realistic fabric texture, visible stitching, accurate visible product details, "
    "no plastic or CGI look."
)


LEG_RE = re.compile(r"腿|脚|走|迈步|跨|蹲|弯腰|靴|鞋|裤脚|下半身|步")


PERSON_RE = re.compile(r"人物|女性|男性|模特|女孩|男孩|手|脸|面部|肤|眼|唇|表情")
NEG_PERSON_RE = re.compile(r"无人脸|无人物|无人出镜|不露脸|纯商品|无脸")


def _ensure_realism(text: str, shot: dict) -> str:
    """真实质感模块兜底（SOP 4.3 强制项，不依赖模型自觉）。

    判定顺序：有真人痕迹（含手/脸/肤）→ 必须 human 版；纯商品无人 → 产品版。
    模型自己写的模块也可能缺 leg 子句或选错版本，这里统一纠正。
    """
    t = (text or "").rstrip()
    blob = (t + " " + str(shot.get("action") or "") + " " + str(shot.get("shot_size") or "")
            + " " + str(shot.get("name") or ""))
    probe_blob = NEG_PERSON_RE.sub(" ", blob)      # 先剥掉「无人脸/纯商品」，否则脸字会误判
    has_person = bool(PERSON_RE.search(probe_blob)) or "不露脸" in blob and "手" in blob
    need_legs = bool(LEG_RE.search(blob)) and "correct leg anatomy" not in t

    if "realistic human" in t:
        return (t + REALISM_LEGS) if need_legs else t
    if "realistic fabric texture" in t and not has_person:
        return t
    if "realistic fabric texture" in t:             # 有手/有人却写了产品版 → 换掉
        t = re.sub(r"真实质感：[^\n]*", "", t).rstrip()
    block = REALISM_HUMAN if has_person else REALISM_PRODUCT
    return t + "\n" + block + (REALISM_LEGS if need_legs else "")


PROMPT_FIELDS = ("subject", "scene", "action", "camera", "light", "style", "audio")
DEFAULT_KEEP = "保持原图人物长相、服装、构图和光影关系不变；商品结构与可见细节不变。"


def _prompt_title(idx, shot) -> str:
    name = str(shot.get("name") or "").strip()
    return "竖屏 9:16｜Seedance 2.0 图生视频（T2）｜S%s%s" % (idx, ("｜" + name) if name else "")


def _clean_keep(value, default: str = "") -> str:
    """keep 只保留「保持不变」约束；模型爱把原片参数/秒数写进来，会误导生成。"""
    text = re.sub(r"^保持不变[:：]\s*", "", str(value or "").strip())
    text = re.sub(r"原片参数[:：][^；;\n]*[；;]?", "", text)
    text = re.sub(r"\b\d{2,5}\s*[×x]\s*\d{2,5}\b", "", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*fps\b", "", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*s\b", "", text)
    return text.strip(" ；;，,") or default or DEFAULT_KEEP


def _prompt_sections(row, shot):
    """模型分段字段 → 可拼装的六要素；风格段保留原有真实质感兜底。"""
    sections = {key: str(row.get(key) or "").strip() for key in PROMPT_FIELDS}
    if not seedance.check_fields(sections):
        sections["style"] = _ensure_realism(sections["style"], shot)
    return sections


def _write_prompts(vid: str, model: dict, struct: dict, meta: dict, probe: dict,
                   cancel: threading.Event | None = None) -> list[dict]:
    """逐段写提示词：每批 3 段，避免单次输出被截断。

    ★ 2026-10-30：不再让模型自由写整段。模型只填六要素字段，顺序、标题、
      「保持不变」、音频段和负向词由 seedance_template.compose() 统一生成，
      保证每段都符合《Seedance 2.0 通用提示词模板》。
    """
    shots = struct["shots"]
    out: list[dict] = []
    base = (f"商品：{struct.get('product_summary')}\n人物：{struct.get('person_summary')}\n"
            f"场景：{struct.get('scene_summary')}\n原片参数：{probe.get('width')}×{probe.get('height')}、"
            f"{probe.get('fps')}fps、{probe.get('duration')}s\n")
    chunks = [shots[i:i + 3] for i in range(0, len(shots), 3)]
    for ci, chunk in enumerate(chunks, 1):
        _check_cancel(cancel)
        body = "\n".join(
            f"S{s.get('idx')} {s.get('name')} | {s.get('t_start')}–{s.get('t_end')}s | "
            f"{s.get('shot_size')} | 动作：{s.get('action')} | 信息点：{s.get('info')}"
            for s in chunk)
        prompt = (
            f"{PROMPT_SYS}\n\n【本条视频的固定设定，每段必须一字不差地重复，禁止写「同上」】\n{base}\n"
            f"【要写的镜头】\n{body}\n\n"
            "输出 JSON，数组顺序必须与上面镜头顺序一致：\n"
            '{"prompts":[{"idx":1,"subject":"","scene":"","action":"","camera":"",'
            '"light":"","style":"","audio":"","keep":""}]}\n'
            "字段要求：\n"
            "- subject：主体外观/服装/情绪 + 商品全量描述（颜色/结构/五金/面料/收口），不得省略商品；\n"
            "- scene：地点 + 背景元素 + 天气/时间；\n"
            "- action：1–3 个递进动作，写清幅度、速度、朝向、视线、表情，不要堆动作；\n"
            "- camera：景别 + 机位 + 运动方式 + 焦点，用推拉摇移跟/环绕/俯冲/手持/浅景深等术语；"
            "一条最多两个运镜变化，不写秒数；\n"
            "- light：光源方向/类型 + 色调 + 氛围；\n"
            "- style：一句话锁定媒介质感与参考调性（如 35mm 胶片颗粒、自然光、纪实感）；\n"
            "- audio：环境声：…；音效：…；台词：…（没有台词证据写「台词：无」）；BGM：…；\n"
            "- keep：只写保持原图人物长相、服装、构图、光影关系和商品可见结构不变；"
            "不要写分辨率、帧率、秒数等原片参数；\n"
            "只描述画面，禁止字幕、价格、水印文字；不要输出段落标题，程序会按模板拼。")
        got = _chat_json(model, [{"type": "text", "text": prompt}], max_tokens=14000)
        items = got.get("prompts") if isinstance(got, dict) else got
        items = items or []
        items = [it for it in items if isinstance(it, dict)]
        # 模型经常从 1 重新编号（导致 idx 重复、后续段落被覆盖）：
        # 以本批镜头顺序为准硬映射，数量对不上才退回模型自带 idx。
        aligned = len(items) == len(chunk)
        for k, it in enumerate(items):
            if aligned:
                idx = _as_int(chunk[k].get("idx"), 0)
                shot = chunk[k]
            else:
                idx = _as_int(it.get("idx"), 0)
                shot = next((s for s in chunk if _as_int(s.get("idx"), 0) == idx), {})
            if not idx or not shot:
                continue
            sections = _prompt_sections(it, shot)
            missing = seedance.check_fields(sections)
            if missing:
                _log(vid, f"S{idx} 提示词字段不全（{'、'.join(missing)}），转为单段重试")
                continue
            keep = _clean_keep(it.get("keep"))
            text = seedance.compose(sections, title=_prompt_title(idx, shot),
                                    keep=keep, audio=sections["audio"])
            for issue in seedance.audit(text):
                _log(vid, f"S{idx} 模板提醒：{issue}")
            out.append({"idx": idx, "text": text, "keep": keep,
                        "sections": {key: sections[key] for key in PROMPT_FIELDS}})
        _log(vid, f"提示词 {ci}/{len(chunks)} 批完成（{len(items)} 段）",
             progress=72 + int(14 * ci / len(chunks)))
    # 去重（同一 idx 只留最后一条），按 idx 排序
    dedup = {p["idx"]: p for p in out if p["idx"]}
    out = sorted(dedup.values(), key=lambda x: x["idx"])
    missing = [s["idx"] for s in shots if _as_int(s.get("idx"), 0) not in dedup]
    if missing:
        _log(vid, f"警告：S{missing} 提示词缺失（模型未返回），报告里已标注")
    return out


COPY_PROMPT = """基于以下分镜，写德语旁白两版 + 合规提示 + 换品模板。

分镜：{shots}
商品：{product}
时长 {dur:.1f}s（德语语速 2.3 词/秒，全文词数上限约 {limit} 词）

要求：
- A 版：纯视觉描述，零功能宣称，可直接用。
- B 版：可带功能词，但每个功能词在 notes 里标出前置条件。
- 两版都不许出现 beste/perfekte/Nr.1/immer/nie/garantiert/Sonderpreis。
- 名词首字母大写，复合词连写，变音符号不省略。
- compliance 给 2–3 条本条视频特别要注意的点（如功能词无据、尺码口播不一致、健康功效擦边）。

输出 JSON：{{"narration":{{"a":"","b":"","a_words":0,"b_words":0,"notes":""}},
"compliance":[{{"rule":"","risk":""}}],
"template":[{{"seg":"S1","func":"场景锚定","formula":"","pct":"15–18%"}}]}}
"""


def _write_copy(vid: str, model: dict, struct: dict, meta: dict, dur: float) -> dict:
    shots = "\n".join(
        f"S{s.get('idx')} {s.get('name')} {s.get('t_start')}–{s.get('t_end')}s：{s.get('action')}"
        for s in struct["shots"])
    p = COPY_PROMPT.format(shots=shots, product=struct.get("product_summary"),
                           dur=dur, limit=int(round(dur * 2.3)))
    got = _chat_json(model, [{"type": "text", "text": p}], max_tokens=7000)
    if not isinstance(got, dict):
        raise ReverseError("旁白生成返回格式异常")
    narr = got.get("narration") or {}
    if not isinstance(narr, dict):
        narr = {}
    for k in ("a", "b"):
        w = len(re.findall(r"[\wÄÖÜäöüß-]+", str(narr.get(k) or "")))
        narr[f"{k}_words"] = w
    comp = got.get("compliance")
    tmpl = got.get("template")
    return {"narration": narr,
            "compliance": comp if isinstance(comp, list) else [],
            "template": tmpl if isinstance(tmpl, list) else []}


# ────────────────────────── 查询 / 维护 ──────────────────────────
def _report_mode(d: Path, vid: str, fallback: str) -> str:
    """按 (mtime, size) 缓存 report.json 的 _mode，避免每次轮询都解析几十 KB。

    ★ 2026-10-23 审查 R16：list_all 每 2s 对每个任务读一次 report.json，
      且在全局锁内；任务多了会线性变慢。
    """
    fp = d / "report.json"
    try:
        stt = fp.stat()
    except OSError:
        return fallback
    hit = _REPORT_MODE_CACHE.get(vid)
    if hit and hit[0] == stt.st_mtime and hit[1] == stt.st_size:
        return hit[2]
    try:
        mode = json.loads(fp.read_text(encoding="utf-8")).get("_mode") or fallback
    except (OSError, ValueError, AttributeError):
        mode = fallback
    _REPORT_MODE_CACHE[vid] = (stt.st_mtime, stt.st_size, mode)
    return mode


def status(vid: str) -> dict:
    st = _load(vid)
    out = dict(st)
    worker = _WORKERS.get(vid)
    out["busy"] = bool(worker)
    out["busy_kind"] = str(worker[0]) if worker else str(st.get("busy") or "")
    out["busy_since"] = round(_WORK_START.get(vid) or 0, 3)   # 进度条算「已用 1 分 23 秒」
    out["age_s"] = round(max(0.0, _now() - float(out.get("updated_at") or 0)), 1)
    d = _dir(vid)
    out["report"] = ""
    if out.get("has_report") or (d / f"TK反推报告_{vid}.html").exists():
        out["has_report"] = True
        out["report"] = f"/tk_reverse/{vid}/TK反推报告_{vid}.html"
        # A failed re-analysis may retain a report from the other mode.
        out['report_mode'] = _report_mode(d, vid, out.get('mode') or 'ad')
    if not out.get('has_report') or out.get('report_mode') != 'restore':
        out['bundle'] = ''
    out["dir"] = str(d)
    # ★ 2026-10-30：卡片缩略图。提示词优化的「对标素材库」要一眼认出是哪条片子，
    #   用抽帧首图（540px 宽，够卡片用）；没抽帧就留空，前端显示占位块。
    out["thumb"] = (f"/tk_reverse/{vid}/frames/f_001.jpg"
                    if (d / "frames" / "f_001.jpg").exists() else "")
    out["refs"] = (out.get('restore_refs') or 0) if out.get('report_mode') == 'restore' else (len(list((d / "refs").glob("*.png"))) if (d / "refs").exists() else 0)
    # 进度条步骤清单：只按当前阶段/模式挑一份，前端据此把已过、当前、未到分开画
    stage = out.get("stage") or ""
    if out["busy_kind"] == "prepare" or stage == "preparing":
        table = PHOTO_PREPARE_STEPS if (out.get("meta") or {}).get("photo_mode") else PREPARE_STEPS
    elif (out.get("mode") or out.get("report_mode")) == "restore" or stage == "analyzing" and out.get("mode") == "restore":
        table = RESTORE_STEPS
    else:
        table = AD_STEPS
    out["steps"] = [{"label": label, "at": pct} for label, pct in table]
    # ★ 2026-10-30：图文帖要能在卡片上标出来（素材是图集合成的，不是原片）
    out["photo_mode"] = bool((out.get("meta") or {}).get("photo_mode"))
    out["photo_images"] = int((out.get("meta") or {}).get("photo_images") or 0)
    return out


def list_all() -> list[dict]:
    items = []
    for p in REVERSE_DIR.iterdir():
        if not p.is_dir() or not _VID_RE.match(p.name):
            continue
        fp = p / "status.json"
        if not fp.exists():
            continue
        try:
            st = _load(p.name)
        except Exception:
            continue
        items.append(status(p.name))
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def rebuild(vid: str) -> dict:
    """重建素材（删掉派生物后重跑；原片保留到新片下载成功才替换）。"""
    v = _check_vid(vid)
    with _LOCK:
        st = _load(v)
        if v in _WORKERS or st.get("stage") in ("preparing", "analyzing"):
            raise ReverseError("正在处理中")
        local = st.get('meta', {}).get('origin') == 'local'
        if not st.get("url") and not local:
            raise ReverseError("缺少原始链接，无法重建")
        _save(v, stage="preparing", stage_label="重建素材", progress=2,
              error="", busy="prepare")
    d = _dir(v)
    for sub in ("frames", "refs"):
        shutil.rmtree(d / sub, ignore_errors=True)
    # ★ 2026-10-23 审查 R9：不再先删 src.mp4 —— 交给 fresh=True 下载覆盖，
    #   下载失败时旧原片还在，任务不会变成既没素材也没报告的空壳。
    for f in ("audio.wav", "manifest.json", "describe.json", "report.json"):
        (d / f).unlink(missing_ok=True)
    for f in d.glob("TK反推报告_*.html"):
        f.unlink(missing_ok=True)
    for f in d.glob("dl.*"):
        f.unlink(missing_ok=True)
    meta = st.get("meta") or {}
    if not local:
        try:                               # 顺手刷新播放/点赞/发布时间，取不到就沿用旧值
            raw = _yt_json(st["url"])
            meta = _norm_meta(raw)
            # ★ 2026-10-30：图文帖重建后仍要走图集合成，别把 photo_mode 刷没了
            if _is_photo(raw):
                meta = _photo_meta(st["url"], meta)
        except Exception:                  # noqa: BLE001
            pass
    _save(v, frames=0, ai_frames=0, shots=0, has_report=False,
          bundle="", restore_refs=0, error="", busy="prepare", log=[], meta=meta)
    _bg(v, "prepare", _prepare, st.get("url") or '', meta, True)
    return status(v)


def remove(vid: str, keep_files: bool = False) -> dict:
    """删除一条反推任务。

    ★ 2026-10-21 独立验收 P1-1：删除前必须再核一次「目标就是根目录下的单层数字目录」，
      且不许用 ignore_errors —— 删不掉就是删不掉，不能再回 ok/removed=true。
    """
    v = _check_vid(vid)
    # ★ 2026-10-23 审查 R10：以线程为准判断是否在跑。stage 可能已写成 ready/done
    #   但线程还在收尾（写报告、打包），此时 rmtree 会删掉正在写的目录，
    #   线程随后又把目录建回来 →「删了还在」的幽灵任务。
    with _LOCK:
        if v in _WORKERS:
            raise ReverseError("后台仍在运行，请先点「停止」再删除")
        st = _load(v)
        if st.get("stage") in ("preparing", "analyzing"):
            raise ReverseError("正在处理中，不能删除")
    d = _dir(v)
    raw = REVERSE_DIR / v
    root = REVERSE_DIR.resolve()
    # 名字必须原样等于 vid（链接/大小写陷阱会让 resolve 后的名字对不上），
    # 且解析结果必须直接挂在根目录下。
    if raw.is_symlink() or d.name != v or d.parent != root:
        raise ReverseError("任务目录不合法，已拒绝删除")
    if not keep_files:
        # 删除全程持锁：与 analyze/create 的「查阶段→置位」互斥，
        # 避免刚检查完就被另一个请求置成 analyzing 再被 rmtree 掉。
        with _LOCK:
            # 复查：第一次检查与这里是两个临界区，中间可能已被置成 analyzing/preparing。
            if v in _WORKERS or _load(v).get("stage") in ("preparing", "analyzing"):
                raise ReverseError("后台仍在运行或正在处理，不能删除")
            if d.exists():
                if not d.is_dir():
                    raise ReverseError("任务路径不是目录，已拒绝删除")
                try:
                    shutil.rmtree(d)
                except OSError as e:
                    raise ReverseError(f"删除任务目录失败：{e}") from e
                if d.exists():
                    raise ReverseError("删除任务目录失败：目录仍然存在")
            _STATE.pop(v, None)
            _REPORT_MODE_CACHE.pop(v, None)
        (config.HANDOFF_DIR / f"tk_reverse_{v}.md").unlink(missing_ok=True)
        return {"ok": True, "removed": True}
    with _LOCK:
        _STATE.pop(v, None)
        _REPORT_MODE_CACHE.pop(v, None)
    (config.HANDOFF_DIR / f"tk_reverse_{v}.md").unlink(missing_ok=True)
    return {"ok": True, "removed": False}


def build_handoff(vid: str) -> dict:
    """生成给 Agent 的交接包（工作台不做多模态精修时的第二条路）。"""
    st = status(vid)
    if not st.get("frames"):
        raise ReverseError("素材还没生成，先点「生成素材」")
    d = _dir(vid)
    meta = st.get("meta") or {}
    probe = st.get("probe") or {}
    frames = sorted((d / "frames").glob("f_*.jpg"))
    lines = [
        f"# TikTok 视频提示词反推 · 交接包 `{vid}`",
        "",
        f"- 链接：{meta.get('url') or st.get('url')}",
        f"- 作者：@{meta.get('author')}（{meta.get('nickname')}）· 发布 {meta.get('upload_at')}",
        f"- 参数：{probe.get('width')}×{probe.get('height')} · {probe.get('fps')}fps · "
        f"{probe.get('duration')}s · 音轨{'有' if probe.get('has_audio') else '无'}",
        f"- 播放/点赞：{meta.get('view')} / {meta.get('like')}",
        f"- 文案：{(meta.get('desc') or '')[:300]}",
        f"- 抽帧目录：`{d / 'frames'}`（{len(frames)} 张，{FPS_SAMPLE}fps，t=(N-1)/{FPS_SAMPLE}）",
        f"- 原片：`{d / 'src.mp4'}`",
        f"- 首帧输出目录：`{d / 'refs'}`",
        f"- 报告输出：`{d / f'TK反推报告_{vid}.html'}`",
        "",
        "## 你要做的事（严格按 SOP）",
        "1. 用多模态 **逐张** Read 上面 frames 目录的帧（≥10s 每 2 秒 1 张，<6s 每 0.5 秒 1 张），"
        "每帧提取 6 类信息：人物/商品/场景/动作/镜头，商品颜色写到色卡级。",
        "2. 分镜切分 S1…Sn，字段：序号 | 时间轴 | 时长 | 景别机位 | 画面与动作 | 信息点；"
        "切点误差标 ±0.2s，10–15s 的片子切 5–8 个镜头。",
        "3. 每段写完整提示词（Seedance 2.0 六要素：主体/场景环境/动作表演/运镜方式/光影氛围/风格质感，"
        "再写保持不变/音频/负向），商品描述逐段全量重复，禁止「同上」，禁止把字幕写进 prompt。",
        "4. 核算结构配比（钩子/卖点/证明/收尾，合计 100%）。",
        "5. 德语旁白 A/B 两版，按 2.3 词/秒核字数；B 版每个功能词标前置条件。",
        "6. 按 SOP 第 6 节七章结构产出单文件 HTML 到上面的报告输出路径，图片用相对路径 `refs/`。",
        "",
        "## 约束",
        "- 不许凭文案猜画面；读不出的写「不可辨」。",
        "- 目标镜头短于 4s 的，一律按生成 4–5s 后裁切处理。",
        "- 禁止绝对化视觉词（perfect/flawless）。",
        "- 未做 ASR 就明确写「未转写、旁白为重写版」，不要假装有原文。",
        "",
        "## 自检（交付前逐条打勾）",
        "- [ ] 每帧都真读过了 - [ ] 颜色到色卡级 - [ ] 每段都重复了商品描述",
        "- [ ] 每段都有六要素/保持不变/音频/负向 - [ ] 没有 <4s 的生成时长 - [ ] 首帧未放大",
        "- [ ] A 版零功能宣称 - [ ] 切点标了 ±0.2s - [ ] 配比合计 100%",
        "",
        f"提示词模板：`Seedance2.0通用提示词模板.md`（工作台根目录，{seedance.VERSION}）",
        f"SOP 全文：`D:/ai/planA/2026-10-21-10-13-27/TikTok视频提示词反推_SOP.md`",
    ]
    text = "\n".join(lines)
    fp = config.HANDOFF_DIR / f"tk_reverse_{vid}.md"
    fp.write_text(text, encoding="utf-8")
    return {"text": text, "saved": str(fp)}
