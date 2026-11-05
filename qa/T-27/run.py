#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qa/T-27/run.py — T-27 的独立验收（M1-qa-plan.md 的 T-27 第 1–16 条，本文件是第 17 条）。

T-27 = `webapp\\config.py` 的两处读取侧修正：
  A. 账号 out_dir/audio_dir 读取侧归一到当前包（写侧不落盘）；
  B. `run_env()` 不再强制离线（ADR-0008），旧断言由 QA 改成「键不存在」（CRD-0003）。

用例与证据：
   1  工程师单测 account_paths_portable.test.py 退出 0
   2  归一正例 (a)：别的安装位置的 pipeline\\output → 当前包
   3  形状不命中 (b)：E:\\data\\output 原样
   4  相对路径 (c)：output 原样
   5  已在当前 PIPELINE_DIR 之下 (d)：原样
   6  旧路径真实存在也归一 (e)
   7  大小写/斜杠变体命中；别的账号的 audio 尾巴不归一 (f)
   8  读不写文件 (g)：load_accounts / load_accounts_raw 前后 sha256 相同
   9  写侧不变：update/add/set_media_type/set_active/remove 之后旧路径仍在文件里
  10  真实对象：account_artifacts / accounts_with_artifacts 指向当前包
  11  run_env() 没有 HF_HUB_OFFLINE（父进程设了也不带），代理与编码仍在
  12  lease_owner.test.py 的旧断言已由 QA 改成「键不存在」并指向 ADR-0008
  13  四个基线全绿的既有用例仍绿（accounts_write / accounts_build / ledger_unify / overview_kpi）
  14  image_ledger 只允许基线 errors=3（load_secuid 过期桩），无新增
  15  lease_owner 回到 18/21，只剩 3 条 _assert_playable 既有红
  16  端到端换目录：qa27 包复制到 D:\\测试 目录 二\\工作台，旧路径的 accounts.json 仍读到新位置产物

跑法（仓库根）：
    pipeline\\.venv\\Scripts\\python.exe qa\\T-27\\run.py

退出码：0 = 全部通过；1 = 有失败；2 = 有 blocked（环境不满足，如候选端口全被占）。

第 16 条每次都用 packaging\\build_portable.py 重新构建 `build\\portable\\qa27\\slim`
（不会碰 build\\portable\\slim）；`QA27_REUSE=1` 时若 stamp 与 HEAD+config.py 哈希一致才复用。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")          # 控制台是 GBK 时也不因一个字符崩掉
    except Exception:                                  # noqa: BLE001
        pass

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
WEBAPP = REPO / "webapp"
PY_WEBAPP = WEBAPP / ".venv" / "Scripts" / "python.exe"

BUILD_ROOT = REPO / "build" / "portable" / "qa27"
PKG = BUILD_ROOT / "slim"
FFMPEG_DIR = Path(r"D:\ffmpeg\build\ffmpeg-9.0.2-essentials_build\bin")
E2E_TARGET = Path(r"D:\测试 目录 二\工作台")
E2E_ORIG = Path(r"D:\原始安装位置\工作台")        # 只作为「原始安装位置」的字面路径，不必存在

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Blocked(Exception):
    """环境不满足（端口全占、缺构建前置），不算产品失败。"""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tail(text: str, lines: int = 12) -> str:
    rows = [r for r in str(text or "").splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def _run(cmd, timeout=900, cwd=REPO, env_extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd or REPO), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _summary(out: str):
    hits = re.findall(r"通过\s*(\d+)\s*/\s*(\d+)", out or "")
    return (int(hits[-1][0]), int(hits[-1][1])) if hits else None


# ── 9 条路径用沙箱：只改 config 模块里的「当前包」常量，全部落在临时目录 ──────
_SANDBOX_ATTRS = (
    "PIPELINE_DIR", "OUTPUT_DIR", "REPORTS_DIR", "SCRIPTS_DIR", "ACCOUNTS_JSON",
    "LEDGER_DIR", "STATE_FILE", "RUNS_JSONL", "SCHEDULE_JSON", "HANDOFF_DIR",
    "AUTOMATIONS_JSON", "GATE_REPORT", "WORKBUDDY_HOME",
)


@contextlib.contextmanager
def sandbox(tag: str):
    tmp = Path(tempfile.mkdtemp(prefix=f"qa27_{tag}_"))
    pipe, ledger = tmp / "pipeline", tmp / "ledger"
    (pipe / "output").mkdir(parents=True)
    (pipe / "reports").mkdir()
    (pipe / "scripts").mkdir()
    ledger.mkdir()
    old = {k: getattr(config, k) for k in _SANDBOX_ATTRS}
    config.PIPELINE_DIR = pipe
    config.OUTPUT_DIR = pipe / "output"
    config.REPORTS_DIR = pipe / "reports"
    config.SCRIPTS_DIR = pipe / "scripts"
    config.ACCOUNTS_JSON = pipe / "accounts.json"
    config.LEDGER_DIR = ledger
    config.STATE_FILE = pipe / "pipeline_state.json"
    config.RUNS_JSONL = pipe / "runs.jsonl"
    config.SCHEDULE_JSON = pipe / "schedule.json"
    config.HANDOFF_DIR = pipe / "handoff"
    config.AUTOMATIONS_JSON = pipe / "automations.json"
    config.GATE_REPORT = pipe / "gate_report.json"
    config.WORKBUDDY_HOME = tmp / "workbuddy"
    try:
        yield tmp, pipe, ledger
    finally:
        for k, v in old.items():
            setattr(config, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


def write_accounts(pipe: Path, rows: list[dict]) -> Path:
    p = pipe / "accounts.json"
    p.write_text(
        json.dumps({"xlsx": str(pipe.parent / "ledger" / "tiktok_对标.xlsx"),
                    "accounts": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def raw_accounts(pipe: Path) -> dict:
    return json.loads((pipe / "accounts.json").read_text(encoding="utf-8"))


def by_name(cfg: dict) -> dict:
    return {a.get("account"): a for a in cfg.get("accounts") or []}


def _row(account: str, out_dir, audio_dir) -> dict:
    return {"account": account, "active": True, "out_dir": out_dir, "audio_dir": audio_dir}


# ── 用例注册 ─────────────────────────────────────────────────────────
PASSED: list[str] = []
FAILED: list[str] = []
BLOCKED_IDS: list[str] = []


def check(cid: str, title: str, fn):
    print(f"\n[{cid}] {title}")
    try:
        evidence = fn() or []
        for line in evidence:
            print(f"    {line}")
        PASSED.append(cid)
        print(f"    [PASS] {cid}")
    except Blocked as exc:
        BLOCKED_IDS.append(cid)
        print(f"    [BLOCKED] {cid}: {exc}")
    except AssertionError as exc:
        FAILED.append(cid)
        print(f"    [FAIL] {cid}: {exc}")
    except Exception as exc:                       # noqa: BLE001
        FAILED.append(cid)
        print(f"    [FAIL] {cid}: {exc.__class__.__name__}: {exc}")


# ── 1. 工程师单测 ────────────────────────────────────────────────────
def c1():
    code, out = _run([PY_WEBAPP, WEBAPP / "tests" / "account_paths_portable.test.py"], timeout=300)
    assert code == 0, f"退出码 {code}\n{tail(out)}"
    total = _summary(out)
    assert total and total[0] == total[1], f"汇总不是全过：{total}\n{tail(out)}"
    assert "✗" not in out, f"输出里有失败行：\n{tail(out)}"
    return [f"exit=0，汇总 通过 {total[0]} / {total[1]}"]


# ── 2–8. 读取侧归一 ───────────────────────────────────────────────────
def c2():
    with sandbox("a") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [_row("acc1", r"X:\任意\pipeline\output",
                                   r"X:\任意\pipeline\output\acc1_audio")])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        out = config.OUTPUT_DIR
        assert Path(row["out_dir"]) == out, f"out_dir={row['out_dir']!r} 期望 {out}"
        assert Path(row["audio_dir"]) == out / "acc1_audio", f"audio_dir={row['audio_dir']!r}"
        return [f"acc1.out_dir = {row['out_dir']}",
                f"acc1.audio_dir = {row['audio_dir']}"]


def c3():
    with sandbox("b") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [_row("acc1", r"E:\data\output", r"E:\data\acc1_audio")])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        assert row["out_dir"] == r"E:\data\output", f"out_dir 被改了：{row['out_dir']!r}"
        assert row["audio_dir"] == r"E:\data\acc1_audio", f"audio_dir 被改了：{row['audio_dir']!r}"
        return [f"原样：{row['out_dir']} | {row['audio_dir']}"]


def c4():
    with sandbox("c") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [_row("acc1", "output", r"output\acc1_audio")])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        assert row["out_dir"] == "output", f"out_dir 被改了：{row['out_dir']!r}"
        assert row["audio_dir"] == r"output\acc1_audio", f"audio_dir 被改了：{row['audio_dir']!r}"
        return [f"原样：{row['out_dir']} | {row['audio_dir']}"]


def c5():
    with sandbox("d") as (_tmp, pipe, _ledger):
        here_out, here_aud = str(config.OUTPUT_DIR), str(config.OUTPUT_DIR / "acc1_audio")
        write_accounts(pipe, [_row("acc1", here_out, here_aud)])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        assert row["out_dir"] == here_out, f"当前包内 out_dir 被改了：{row['out_dir']!r}"
        assert row["audio_dir"] == here_aud, f"当前包内 audio_dir 被改了：{row['audio_dir']!r}"
        return [f"原样（已在当前 PIPELINE_DIR 之下）：{row['out_dir']}"]


def c6():
    with sandbox("e") as (tmp, pipe, _ledger):
        old = tmp / "oldinstall" / "pipeline" / "output"
        (old / "acc1_audio").mkdir(parents=True)
        assert old.is_dir(), "旧安装位置没建出来（用例自身前置失败）"
        write_accounts(pipe, [_row("acc1", str(old), str(old / "acc1_audio"))])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        assert Path(row["out_dir"]) == config.OUTPUT_DIR, \
            f"旧路径存在时也必须归一：out_dir={row['out_dir']!r}"
        assert Path(row["audio_dir"]) == config.OUTPUT_DIR / "acc1_audio", \
            f"audio_dir={row['audio_dir']!r}"
        return [f"旧路径真实存在：{old}",
                f"仍归一为：{row['out_dir']}"]


def c7():
    with sandbox("f") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [
            _row("acc1", "C:/Some/PIPELINE/OUTPUT", "C:/Some/PIPELINE/OUTPUT/ACC1_AUDIO"),
            _row("acc2", "C:/SomeElse/pipeline/output", "C:/SomeElse/pipeline/output/acc2_audio"),
            _row("acc3", r"D:\Misc\pipeline\output", r"D:\Misc\pipeline\output\other_audio"),
        ])
        rows = by_name(config.load_accounts(include_inactive=True))
        out = config.OUTPUT_DIR
        assert Path(rows["acc1"]["out_dir"]) == out, f"大写变体未命中：{rows['acc1']['out_dir']!r}"
        assert Path(rows["acc1"]["audio_dir"]) == out / "acc1_audio", \
            f"大写 audio 尾巴未命中：{rows['acc1']['audio_dir']!r}"
        assert Path(rows["acc2"]["out_dir"]) == out, f"正斜杠未命中：{rows['acc2']['out_dir']!r}"
        assert Path(rows["acc2"]["audio_dir"]) == out / "acc2_audio", \
            f"正斜杠 audio 未命中：{rows['acc2']['audio_dir']!r}"
        assert rows["acc3"]["audio_dir"] == r"D:\Misc\pipeline\output\other_audio", \
            f"别的账号的 audio 尾巴不该归一：{rows['acc3']['audio_dir']!r}"
        return [f"acc1/acc2 命中 → {rows['acc1']['out_dir']} | {rows['acc2']['out_dir']}",
                f"acc3.audio_dir 原样 → {rows['acc3']['audio_dir']}"]


def c8():
    with sandbox("g") as (_tmp, pipe, _ledger):
        p = write_accounts(pipe, [_row("acc1", r"X:\任意\pipeline\output",
                                        r"X:\任意\pipeline\output\acc1_audio")])
        before = sha256(p)
        config.load_accounts()
        config.load_accounts_raw()
        config.load_accounts(include_inactive=True)
        config.accounts_with_artifacts()
        after = sha256(p)
        assert before == after, "读取把 accounts.json 改了"
        left = raw_accounts(pipe)["accounts"][0]
        assert left["out_dir"] == r"X:\任意\pipeline\output", f"文件里的旧路径被改了：{left['out_dir']!r}"
        return [f"sha256 before = {before[:16]}…",
                f"sha256 after  = {after[:16]}…（相同）"]


# ── 9. 写侧不变 ──────────────────────────────────────────────────────
def c9():
    with sandbox("write") as (_tmp, pipe, _ledger):
        old_out, old_aud = r"X:\任意\pipeline\output", r"X:\任意\pipeline\output\acc1_audio"
        write_accounts(pipe, [_row("acc1", old_out, old_aud), _row("acc2", old_out, old_aud)])

        def acc1_kept(old):
            row = next(a for a in raw_accounts(pipe)["accounts"] if a["account"] == "acc1")
            assert row["out_dir"] == old_out, f"{old} 后 acc1.out_dir 被归一结果覆盖：{row['out_dir']!r}"
            assert row["audio_dir"] == old_aud, f"{old} 后 acc1.audio_dir 被覆盖：{row['audio_dir']!r}"

        config.update_account("acc1", {"note": "x"})
        acc1_kept("update_account")
        added = config.add_account("newacc")
        assert Path(added["out_dir"]) == config.OUTPUT_DIR, f"新账号应指向当前包：{added['out_dir']!r}"
        acc1_kept("add_account")
        config.set_media_type(["acc1"], "image")
        acc1_kept("set_media_type")
        config.set_account_active("acc1", False)
        acc1_kept("set_account_active")
        config.remove_account("acc2")
        names = [a["account"] for a in raw_accounts(pipe)["accounts"]]
        assert "acc2" not in names, f"acc2 没删掉：{names}"
        acc1_kept("remove_account")
        return [f"5 次写操作后文件里仍是：{old_out}",
                f"新账号 newacc.out_dir = {added['out_dir']}（当前包，正确）"]


# ── 10. 真实对象 ─────────────────────────────────────────────────────
def c10():
    with sandbox("art") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [_row("acc1", r"X:\任意\pipeline\output",
                                   r"X:\任意\pipeline\output\acc1_audio")])
        (config.OUTPUT_DIR / "scrape_acc1.json").write_text(
            json.dumps({"items": [{"id": "v1", "publish_time_gmt8": "2026-11-05 10:00:00"}],
                        "errors": []}, ensure_ascii=False), encoding="utf-8")
        audio = config.OUTPUT_DIR / "acc1_audio"
        audio.mkdir()
        (audio / "acc1_qa27.mp3").write_bytes(b"x")

        raw_row = raw_accounts(pipe)["accounts"][0]     # 故意用文件里的原样行
        art = config.account_artifacts(raw_row)
        assert Path(art["out_dir"]) == config.OUTPUT_DIR, f"artifacts.out_dir={art['out_dir']!r}"
        assert Path(art["audio_dir"]) == config.OUTPUT_DIR / "acc1_audio", \
            f"artifacts.audio_dir={art['audio_dir']!r}"
        assert art["has_scrape"] is True, "放好了 scrape_acc1.json 却没认出来"
        assert art["has_audio"] is True, "放好了 acc1_*.mp3 却没认出来"

        got = next((r for r in config.accounts_with_artifacts() if r["account"] == "acc1"), None)
        assert got is not None, "accounts_with_artifacts 里没有 acc1"
        assert Path(got["out_dir"]) == config.OUTPUT_DIR, f"accounts_with_artifacts.out_dir={got['out_dir']!r}"
        assert got["has_scrape"] is True, "accounts_with_artifacts 里 acc1 应 has_scrape=True"
        return [f"account_artifacts: out={art['out_dir']} audio={art['audio_dir']}",
                f"has_scrape={art['has_scrape']} has_audio={art['has_audio']}；"
                f"accounts_with_artifacts 认到 acc1"]


# ── 11. run_env() 不再强制离线 ───────────────────────────────────────
def c11():
    env = config.run_env()
    assert "HF_HUB_OFFLINE" not in env, f"run_env() 又写了 HF_HUB_OFFLINE={env.get('HF_HUB_OFFLINE')!r}"
    assert env.get("HTTPS_PROXY"), "HTTPS_PROXY 丢了"
    assert env.get("HTTP_PROXY"), "HTTP_PROXY 丢了"
    assert env.get("PYTHONIOENCODING") == "utf-8", f"PYTHONIOENCODING={env.get('PYTHONIOENCODING')!r}"
    with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}, clear=False):
        env2 = config.run_env()
    assert "HF_HUB_OFFLINE" not in env2, \
        f"父进程设了 HF_HUB_OFFLINE=1 仍被透传：{env2.get('HF_HUB_OFFLINE')!r}"
    return [f"键集合含 HTTPS_PROXY/HTTP_PROXY/PYTHONIOENCODING；无 HF_HUB_OFFLINE",
            f"父进程 HF_HUB_OFFLINE=1 时：{'无该键' if 'HF_HUB_OFFLINE' not in env2 else '仍有该键'}"]


# ── 12. lease_owner 旧断言已由 QA 改掉 ───────────────────────────────
def c12():
    path = WEBAPP / "tests" / "lease_owner.test.py"
    text = path.read_text(encoding="utf-8")
    assert 'env.get("HF_HUB_OFFLINE") == "1"' not in text, \
        "旧断言 env.get(\"HF_HUB_OFFLINE\") == \"1\" 还在"
    assert '"HF_HUB_OFFLINE" not in env' in text, "新断言「键不存在」没找到"
    assert "ADR-0008" in text, "断言旁没有指向 ADR-0008 的注释"
    assert "run_env 的离线开关丢了" not in text, "旧的失败提示文案还在"
    hits = [ln.strip() for ln in text.splitlines() if "HF_HUB_OFFLINE" in ln or "ADR-0008" in ln]
    return [f"{path.relative_to(REPO)}：旧断言已删、新断言 + ADR-0008 注释在位"] + \
           [f"  {h}" for h in hits[:4]]


# ── 13. 四个基线全绿用例 ─────────────────────────────────────────────
def c13():
    expected = ("accounts_write", "accounts_build", "ledger_unify", "overview_kpi")
    evidence = []
    for name in expected:
        code, out = _run([PY_WEBAPP, WEBAPP / "tests" / f"{name}.test.py"], timeout=600)
        summary = _summary(out)
        assert code == 0, f"{name} exit={code}\n{tail(out)}"
        assert "0 失败" in out or (summary and summary[0] == summary[1]), \
            f"{name} 退出 0 但没有全过汇总：{summary}\n{tail(out, 4)}"
        evidence.append(f"{name}: exit=0" + (f"，{summary[0]}/{summary[1]}" if summary else ""))
    return evidence


# ── 14. image_ledger 只允许基线 errors=3 ─────────────────────────────
def c14():
    code, out = _run([PY_WEBAPP, WEBAPP / "tests" / "image_ledger.test.py"], timeout=600)
    assert code == 1, f"基线是 FAILED (errors=3)（exit=1），实得 exit={code}"
    ran = re.search(r"Ran\s+(\d+)\s+tests?", out)
    assert ran, f"没找到 unittest 的 Ran 行\n{tail(out)}"
    assert "FAILED (errors=3)" in out, f"基线应是 FAILED (errors=3)\n{tail(out)}"
    assert not re.search(r"^FAIL:", out, re.M), f"出现了 failure（基线只有 errors）：\n{tail(out)}"
    error_names = re.findall(r"^ERROR: (\S+)", out, re.M)
    assert len(error_names) == 3, f"ERROR 条数不是 3：{error_names}"
    causes = re.findall(r"^([A-Za-z_][\w.]*(?:Error|Exception)): (.+)$", out, re.M)
    assert len(causes) == 3, f"异常原因行不是 3 条：{causes}"
    assert all("load_secuid" in msg for _typ, msg in causes), \
        f"errors 原因不是基线里的 load_secuid：{causes}"
    joined = "\n".join(msg for _typ, msg in causes)
    for word in ("default_account", "account_artifacts", "ledger_path"):
        assert word not in joined, f"errors 里出现与账号路径相关的字样 {word}"
    return [f"Ran {ran.group(1)} tests；FAILED (errors=3)；3 条都是 load_secuid 过期桩",
            "errors 用例：" + ", ".join(error_names)]


# ── 15. lease_owner 回到 18/21 ───────────────────────────────────────
def c15():
    code, out = _run([PY_WEBAPP, WEBAPP / "tests" / "lease_owner.test.py"], timeout=600)
    assert code == 1, f"基线 21 条里有 3 条既有红，期望 exit=1，实得 {code}"
    summary = _summary(out)
    assert summary == (18, 21), f"期望 通过 18 / 21，实得 {summary}\n{tail(out)}"
    fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")]
    assert len(fails) == 3, f"失败条数应为 3（既有 _assert_playable 红），实得 {len(fails)}：{fails}"
    assert all("_assert_playable" in f for f in fails), f"出现了既有红之外的失败：{fails}"
    assert not any("HF_HUB_OFFLINE" in f for f in fails), f"HF_HUB_OFFLINE 那条还没变绿：{fails}"
    green = [ln.strip() for ln in out.splitlines()
             if "下传只加一个键" in ln and "run_env" in ln and "✓" in ln]
    assert green, "改过断言的那条 check 不是绿"
    return [f"通过 18 / 21；失败 3 条全是 _assert_playable（既有红）",
            f"绿：{green[0]}",
            f"失败：{fails[0][:80]}",
            f"      {fails[1][:80]}",
            f"      {fails[2][:80]}"] if len(fails) >= 3 else [f"通过 {summary}"]


# ── 16. 端到端：包内换目录 ───────────────────────────────────────────
def _git_head() -> str:
    try:
        code, out = _run(["git", "rev-parse", "HEAD"], timeout=20)
        return out.strip() if code == 0 else ""
    except Exception:                                  # noqa: BLE001
        return ""


def build_qa27() -> tuple[str, list[str]]:
    stamp = BUILD_ROOT / ".qa27-stamp.json"
    head, cfg_sha = _git_head(), sha256(WEBAPP / "config.py")
    if os.environ.get("QA27_REUSE") == "1" and stamp.is_file() and (PKG / "python" / "python.exe").is_file():
        try:
            st = json.loads(stamp.read_text(encoding="utf-8"))
        except Exception:                              # noqa: BLE001
            st = {}
        if st.get("head") == head and st.get("config_sha") == cfg_sha:
            return "reuse", [f"复用已构建的包（HEAD+config.py 未变）：{PKG}"]
    assert FFMPEG_DIR.is_dir(), f"缺少构建前置 --ffmpeg-dir：{FFMPEG_DIR}"
    cmd = [PY_WEBAPP, REPO / "packaging" / "build_portable.py",
           "--flavor", "slim", "--out", BUILD_ROOT, "--ffmpeg-dir", FFMPEG_DIR]
    code, out = _run(cmd, timeout=3600)
    assert code == 0, f"构建失败（exit={code}）：\n{tail(out, 20)}"
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"head": head, "config_sha": cfg_sha,
                                 "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                ensure_ascii=False), encoding="utf-8")
    return "built", [f"重建：exit=0；{tail(out, 1)}"]


def _free_port() -> int:
    """让系统分配一个空闲的本机端口（不占用工作台的候选端口，避免与并行 QA 抢）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(port: int, path: str, timeout: float = 10.0) -> dict:
    with _OPENER.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
        raw = resp.read()
        assert resp.status == 200, f"{path} 状态码 {resp.status}"
    return json.loads(raw.decode("utf-8", "replace"))


def _wait_health(port: int, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = _http_json(port, "/api/health", timeout=2.0)
            if data.get("ok") is True:
                return data
        except Exception:                              # noqa: BLE001
            pass
        time.sleep(0.25)
    return None


def _kill_own(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)
        try:
            proc.wait(timeout=15)
        except Exception:                              # noqa: BLE001
            pass


# ── 并发互斥：两个 T-27 用例同时跑会争用同一个构建目录与复制路径 ──────
LOCK_DIR = BUILD_ROOT / ".qa27.lock"


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        proc = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)
        return str(pid) in (proc.stdout or "")
    except Exception:                                  # noqa: BLE001
        return True                                    # 判不出来按活着处理，绝不误删别人的锁


def _lock_stale() -> bool:
    try:
        info = json.loads((LOCK_DIR / "owner.json").read_text(encoding="utf-8"))
        pid, ts = int(info.get("pid") or 0), float(info.get("ts") or 0)
    except Exception:                                  # noqa: BLE001
        try:
            ts = LOCK_DIR.stat().st_mtime
        except OSError:
            return False
        pid = 0
    if pid and _pid_alive(pid):
        return False
    return bool(ts) and (time.time() - ts) > 300


@contextlib.contextmanager
def suite_lock(wait_seconds: float = 900.0):
    """同一时间只允许一个 T-27 用例跑（build/portable/qa27 与复制路径是共享的）。"""
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    deadline, announced = time.time() + wait_seconds, False
    while True:
        try:
            LOCK_DIR.mkdir()
            break
        except FileExistsError:
            if _lock_stale():
                shutil.rmtree(LOCK_DIR, ignore_errors=True)
                continue
            if time.time() >= deadline:
                raise Blocked(f"另一个 T-27 QA 还在跑，等了 {int(wait_seconds)}s 没释放：{LOCK_DIR}")
            if not announced:
                print(f"    [wait] 另一个 T-27 QA 正在跑（{LOCK_DIR}），等它释放")
                announced = True
            time.sleep(2)
    try:
        (LOCK_DIR / "owner.json").write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
        yield
    finally:
        shutil.rmtree(LOCK_DIR, ignore_errors=True)


def c16():
    mode, evidence = build_qa27()
    evidence = [f"构建：{mode}"] + evidence
    assert (PKG / "python" / "python.exe").is_file(), f"包不完整：{PKG}"
    assert (PKG / "webapp" / "app.py").is_file(), f"包不完整：{PKG}"

    tmp = Path(tempfile.mkdtemp(prefix="qa27_e2e_"))
    proc = None
    target = E2E_TARGET
    try:
        assert str(target).lower().startswith("d:\\"), f"目标路径异常：{target}"
        if target.exists():
            shutil.rmtree(target)                      # QA 约定路径：上一次的残留先清掉
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(PKG, target)
        evidence.append(f"复制：{PKG} -> {target}")

        orig_out = str((PKG / "pipeline" / "output").resolve())
        orig_aud = orig_out + "\\acc1_audio"
        accfile = target / "pipeline" / "accounts.json"
        accfile.write_text(json.dumps(
            {"xlsx": str(target / "ledger" / "tiktok_对标.xlsx"),
             "accounts": [{"account": "acc1", "active": True, "lang": "en",
                           "out_dir": orig_out, "audio_dir": orig_aud}]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "pipeline" / "output" / "scrape_acc1.json").write_text(json.dumps(
            {"items": [{"id": "qa27v1", "desc": "T-27 QA fixture",
                        "stats": {"playCount": 1000, "diggCount": 10, "commentCount": 2,
                                  "shareCount": 1, "collectCount": 3},
                        "publish_time_gmt8": "2026-11-05 10:00:00",
                        "url": "https://example.invalid/v1"}],
             "errors": []}, ensure_ascii=False), encoding="utf-8")
        sha_before = sha256(accfile)
        evidence.append(f"accounts.json 的 out_dir/audio_dir 指向原始安装位置：{orig_out}")
        evidence.append(f"新位置放 scrape_acc1.json；accounts.json sha256={sha_before[:16]}…")

        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        env = {
            "SystemRoot": system_root, "windir": system_root,
            "COMSPEC": os.path.join(system_root, "system32", "cmd.exe"),
            "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            "SystemDrive": os.environ.get("SystemDrive", "C:"),
            "USERPROFILE": os.environ.get("USERPROFILE", str(Path.home())),
            "APPDATA": os.environ.get("APPDATA", ""),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
            "TEMP": str(tmp), "TMP": str(tmp),
            "PATH": f"{target / 'python'};{target / 'ffmpeg' / 'bin'};"
                    f"{system_root}\\system32;{system_root}",
            "WB_ROOT": str(target) + "\\",
            "PY": str(target / "python" / "python.exe"),
            "WB_PYTHON": str(target / "python" / "python.exe"),
            "WB_PYTHONW": str(target / "python" / "pythonw.exe"),
            "WB_PIPELINE_DIR": str(target / "pipeline"),
            "WB_HOME": str(target / "workbuddy"),
            "WB_LEDGER_DIR": str(target / "ledger"),
            "WB_REVERSE_DIR": str(target / "reverse"),
            "WB_LOG_DIR": str(target / "logs"),
            "HF_HOME": str(target / "models" / "hf"),
            "PYTHONIOENCODING": "utf-8",
        }
        # 端口用系统分配的临时端口：这份用例只验「包内换路径」，不需要和别人的
        # 工作台抢 8789；同一个端口上可能已有别的工作台在跑（QA 并行时的实测坑），
        # 所以每次启动后必须核对 health 的 pipeline_dir 确实是本包，否则换端口重试。
        health, port, tries = None, 0, []
        for _attempt in range(3):
            port = _free_port()
            out_fh = open(tmp / "server.out", "wb")
            err_fh = open(tmp / "server.err", "wb")
            try:
                proc = subprocess.Popen(
                    [str(target / "python" / "python.exe"), str(target / "webapp" / "_boot.py"),
                     "--port", str(port)],
                    cwd=str(target / "webapp"), env=env, stdout=out_fh, stderr=err_fh)
            finally:
                out_fh.close()
                err_fh.close()
            candidate = _wait_health(port, 60)
            diag = (candidate or {}).get("diagnostics") or {}
            is_mine = bool(diag) and \
                Path(str(diag.get("pipeline_dir") or "")) == (target / "pipeline").resolve()
            tries.append(f"port={port} health={'有' if candidate else '无'} "
                         f"pipeline_dir={diag.get('pipeline_dir')!r}")
            if candidate and is_mine:
                health = candidate
                break
            _kill_own(proc)
            proc = None
            time.sleep(0.5)
        if health is None:
            log = target / "logs" / "_server_run.log"
            detail = ""
            if log.is_file():
                detail = tail(log.read_text(encoding="utf-8", errors="replace"), 12)
            raise AssertionError(
                "3 次启动都没拿到本包自己的 /api/health：" + "；".join(tries) + f"\n{detail}")
        diag = health.get("diagnostics") or {}
        evidence.append(f"health：port={port}，diagnostics.pipeline_dir={diag.get('pipeline_dir')}"
                        f"（尝试：{'；'.join(tries)}）")

        cfg = _http_json(port, "/api/config")
        detail = {a.get("account"): a for a in cfg.get("accounts_detail") or []}
        assert "acc1" in detail, f"/api/config 里没有 acc1：{list(detail)}"
        row = detail["acc1"]
        assert Path(str(row.get("out_dir", ""))) == (target / "pipeline" / "output").resolve(), \
            f"accounts_detail.acc1.out_dir 未归一：{row.get('out_dir')!r}"
        assert Path(str(row.get("audio_dir", ""))) == \
            (target / "pipeline" / "output" / "acc1_audio").resolve(), \
            f"accounts_detail.acc1.audio_dir 未归一：{row.get('audio_dir')!r}"
        evidence.append(f"/api/config：acc1.out_dir={row['out_dir']}")
        evidence.append(f"/api/config：acc1.audio_dir={row['audio_dir']}")

        bench = _http_json(port, "/api/benchmark")
        acc_row = next((a for a in bench.get("accounts") or [] if a.get("account") == "acc1"), None)
        assert acc_row is not None, "对标分析聚合里没有 acc1"
        assert acc_row.get("has_scrape") is True, f"acc1.has_scrape={acc_row.get('has_scrape')!r}"
        sources = bench.get("data_sources") or {}
        evidence.append(f"/api/benchmark：acc1 videos={acc_row.get('videos')} "
                        f"has_scrape={acc_row.get('has_scrape')} scrape_dir={sources.get('scrape_dir')}")

        sha_after = sha256(accfile)
        assert sha_after == sha_before, \
            f"请求前后 accounts.json 被改了：{sha_before[:16]}… -> {sha_after[:16]}…"
        raw = json.loads(accfile.read_text(encoding="utf-8"))["accounts"][0]
        assert raw["out_dir"] == orig_out and raw["audio_dir"] == orig_aud, \
            f"文件里的原始路径被落盘覆盖：{raw['out_dir']!r} / {raw['audio_dir']!r}"
        evidence.append(f"accounts.json sha256 前后一致：{sha_after[:16]}…；文件里仍是原始路径")
        return evidence
    finally:
        _kill_own(proc)
        shutil.rmtree(tmp, ignore_errors=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        parent = target.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


# ── 入口 ─────────────────────────────────────────────────────────────
def _run_checks():
    check("1", "工程师单测 account_paths_portable.test.py 退出 0", c1)
    check("2", "归一正例 (a)：别的安装位置的 pipeline\\output → 当前包", c2)
    check("3", "形状不命中 (b)：E:\\data\\output 原样", c3)
    check("4", "相对路径 (c)：output 原样", c4)
    check("5", "已在当前 PIPELINE_DIR 之下 (d)：原样", c5)
    check("6", "旧路径真实存在也归一 (e)", c6)
    check("7", "大小写/斜杠变体命中；别的账号的 audio 尾巴不归一 (f)", c7)
    check("8", "读不写文件 (g)：读取前后 accounts.json sha256 相同", c8)
    check("9", "写侧不变：归一结果不落盘（update/add/media_type/active/remove）", c9)
    check("10", "真实对象 account_artifacts / accounts_with_artifacts 指向当前包", c10)
    check("11", "run_env() 没有 HF_HUB_OFFLINE（父进程设了也不带）", c11)
    check("12", "lease_owner 旧断言已由 QA 改成「键不存在」并指向 ADR-0008", c12)
    check("13", "四个基线全绿用例仍绿（accounts_write/accounts_build/ledger_unify/overview_kpi）", c13)
    check("14", "image_ledger 只允许基线 errors=3（无新增）", c14)
    check("15", "lease_owner 回到 18/21，只剩 3 条 _assert_playable 既有红", c15)
    check("16", "端到端换目录：旧路径 accounts.json 仍读到新位置产物", c16)


def main() -> int:
    print("T-27 独立验收（M1-qa-plan 第 1–16 条；本文件是第 17 条）")
    print(f"仓库：{REPO}")
    print(f"QA 解释器：{sys.executable}")
    print(f"webapp 解释器：{PY_WEBAPP}")

    if not (REPO / "webapp" / "config.py").is_file():
        print(f"[BLOCKED] 仓库根不对：{REPO}")
        return 2
    if not PY_WEBAPP.is_file():
        print(f"[BLOCKED] 缺 webapp 虚拟环境解释器：{PY_WEBAPP}")
        return 2

    global config
    sys.path.insert(0, str(WEBAPP))
    import config  # noqa: E402

    try:
        with suite_lock():
            _run_checks()
    except Blocked as exc:
        print(f"\n[BLOCKED] {exc}")
        return 2

    total = len(PASSED) + len(FAILED) + len(BLOCKED_IDS)
    print("\n" + "=" * 60)
    print(f"T-27 QA 汇总：通过 {len(PASSED)} / {total}")
    if FAILED:
        print(f"失败：{', '.join(FAILED)}")
    if BLOCKED_IDS:
        print(f"blocked：{', '.join(BLOCKED_IDS)}")
    print(f"退出码：{2 if BLOCKED_IDS and not FAILED else (1 if FAILED else 0)}")
    return 2 if BLOCKED_IDS and not FAILED else (1 if FAILED else 0)


if __name__ == "__main__":
    sys.exit(main())
