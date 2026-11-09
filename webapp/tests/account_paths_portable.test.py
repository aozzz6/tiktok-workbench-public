# -*- coding: utf-8 -*-
"""account_paths_portable.test.py — T-27：账号路径读取侧归一 + `run_env()` 不再强制离线

两条改动同一个性质：让「读到的路径 / 环境」指向**当前包**，而不是创建那一刻的机器。
（任务表 T-27；ADR-0008 三处强制离线表；HLD §1.2 归一规则、§4.3）

DoD 对应：
  1(a)~1(g) 归一判据（三个条件合取）与「不写文件」；
  2         写侧不变：旧路径不会因为归一面落盘；
  3         `account_artifacts()` / `accounts_with_artifacts()` 用当前包内路径判产物；
  4         `run_env()` 里没有 `HF_HUB_OFFLINE`（父进程设了也不留），代理与编码仍在。

跑法（仓库根）：
    webapp\\.venv\\Scripts\\python.exe webapp\\tests\\account_paths_portable.test.py
全部只写 tempfile 目录；不联网、不占 8789。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
WEBAPP = HERE.parent.parent
sys.path.insert(0, str(WEBAPP))
import config  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ✓ {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  ✗ {name} -> {e}")
        _fail += 1
    except Exception as e:                       # noqa: BLE001
        print(f"  ✗ {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(cond, msg="assert failed"):
    if not cond:
        raise AssertionError(msg)


# ── 沙箱：把 config 的「当前包」指到临时目录 ─────────────────────────
_SAVED = ("PIPELINE_DIR", "OUTPUT_DIR", "REPORTS_DIR", "SCRIPTS_DIR", "ACCOUNTS_JSON",
          "LEDGER_DIR", "STATE_FILE", "RUNS_JSONL", "SCHEDULE_JSON", "HANDOFF_DIR",
          "AUTOMATIONS_JSON", "GATE_REPORT")


@contextlib.contextmanager
def sandbox(tag):
    tmp = Path(tempfile.mkdtemp(prefix=f"t27_{tag}_"))
    pipe, ledger = tmp / "pipeline", tmp / "ledger"
    (pipe / "output").mkdir(parents=True)
    (pipe / "reports").mkdir()
    (pipe / "scripts").mkdir()
    ledger.mkdir()
    old = {k: getattr(config, k) for k in _SAVED}
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
    try:
        yield tmp, pipe, ledger
    finally:
        for k, v in old.items():
            setattr(config, k, v)


def write_accounts(pipe: Path, rows: list[dict]) -> Path:
    p = pipe / "accounts.json"
    p.write_text(json.dumps({"xlsx": str(pipe.parent / "ledger" / "tiktok_对标.xlsx"),
                             "accounts": rows}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def raw_accounts(pipe: Path) -> dict:
    return json.loads((pipe / "accounts.json").read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def by_name(cfg: dict) -> dict:
    return {a.get("account"): a for a in cfg.get("accounts") or []}


print("account_paths_portable — T-27：读取侧路径归一 + run_env 不再强制离线")

# ── 1(a)~1(f) 归一判据 ─────────────────────────────────────────────
_OLD_ABS = r"X:\任意\pipeline\output"
_OLD_AUD = r"X:\任意\pipeline\output\acc1_audio"


def case_normalize():
    with sandbox("norm") as (tmp, pipe, _ledger):
        # (e) 旧路径在本机真实存在时也必须归一
        old_install = tmp / "oldinstall"
        (old_install / "pipeline" / "output" / "acc_old_audio").mkdir(parents=True)
        rows = [
            # (a) 标标准准的「别的安装位置」
            {"account": "acc1", "active": True, "out_dir": _OLD_ABS, "audio_dir": _OLD_AUD},
            # (e) 旧路径真的存在（上面已建出来）
            {"account": "acc_old", "active": True,
             "out_dir": str(old_install / "pipeline" / "output"),
             "audio_dir": str(old_install / "pipeline" / "output" / "acc_old_audio")},
            # (b) 自定义路径：形状不命中第②条 → 原样
            {"account": "acc_custom", "active": True,
             "out_dir": r"E:\data\output", "audio_dir": r"E:\data\acc_custom_audio"},
            # (c) 相对路径：不满足第①条 → 原样
            {"account": "acc_rel", "active": True,
             "out_dir": "output", "audio_dir": r"output\acc_rel_audio"},
            # (d) 本来就在当前 PIPELINE_DIR 之下：不满足第③条 → 原样
            {"account": "acc_here", "active": True,
             "out_dir": str(config.OUTPUT_DIR),
             "audio_dir": str(config.OUTPUT_DIR / "acc_here_audio")},
            # (f) 大小写 / 斜杠都认
            {"account": "acc_case", "active": True,
             "out_dir": "C:/Some/PIPELINE/OUTPUT",
             "audio_dir": "C:/Some/PIPELINE/OUTPUT/ACC_CASE_AUDIO"},
            # (f) 全小写 + 正斜杠：`pipeline/output/<账号>_audio` 也要命中
            {"account": "acc_slash", "active": True,
             "out_dir": "C:/SomeElse/pipeline/output",
             "audio_dir": "C:/SomeElse/pipeline/output/acc_slash_audio"},
        ]
        write_accounts(pipe, rows)
        cfg = by_name(config.load_accounts(include_inactive=True))
        out = str(config.OUTPUT_DIR)

        assert_(cfg["acc1"]["out_dir"] == out, f"acc1.out_dir={cfg['acc1']['out_dir']!r} 期望 {out!r}")
        assert_(cfg["acc1"]["audio_dir"] == str(Path(out) / "acc1_audio"),
                f"acc1.audio_dir={cfg['acc1']['audio_dir']!r}")
        assert_(cfg["acc_old"]["out_dir"] == out, f"旧路径真实存在时也应归一：{cfg['acc_old']['out_dir']!r}")
        assert_(cfg["acc_old"]["audio_dir"] == str(Path(out) / "acc_old_audio"),
                f"{cfg['acc_old']['audio_dir']!r}")
        assert_(cfg["acc_custom"]["out_dir"] == r"E:\data\output", "自定义路径必须原样")
        assert_(cfg["acc_custom"]["audio_dir"] == r"E:\data\acc_custom_audio", "自定义路径必须原样")
        assert_(cfg["acc_rel"]["out_dir"] == "output", "相对路径必须原样")
        assert_(cfg["acc_rel"]["audio_dir"] == r"output\acc_rel_audio", "相对路径必须原样")
        assert_(cfg["acc_here"]["out_dir"] == str(config.OUTPUT_DIR), "当前包内路径必须原样")
        assert_(cfg["acc_case"]["out_dir"] == out, f"大小写/斜杠：{cfg['acc_case']['out_dir']!r}")
        assert_(cfg["acc_case"]["audio_dir"] == str(Path(out) / "acc_case_audio"),
                f"账号名大小写应不敏感：{cfg['acc_case']['audio_dir']!r}")
        assert_(cfg["acc_slash"]["out_dir"] == out,
                f"全小写 + 正斜杠的 out_dir 也要命中：{cfg['acc_slash']['out_dir']!r}")
        assert_(cfg["acc_slash"]["audio_dir"] == str(Path(out) / "acc_slash_audio"),
                f"pipeline/output/<账号>_audio 形状要命中：{cfg['acc_slash']['audio_dir']!r}")
        print(f"    归一后 acc1: {cfg['acc1']['out_dir']} | {cfg['acc1']['audio_dir']}")


def case_audio_tail_mismatch():
    with sandbox("tail") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [
            {"account": "acc1", "active": True,
             "out_dir": _OLD_ABS, "audio_dir": r"X:\任意\pipeline\output\other_audio"},
        ])
        row = by_name(config.load_accounts(include_inactive=True))["acc1"]
        assert_(row["audio_dir"] == r"X:\任意\pipeline\output\other_audio",
                f"audio_dir 尾巴与账号名对不上，不许归一：{row['audio_dir']!r}")


def case_no_write():
    with sandbox("nowrite") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [
            {"account": "acc1", "active": True, "out_dir": _OLD_ABS, "audio_dir": _OLD_AUD},
        ])
        acc = pipe / "accounts.json"
        before = sha(acc)
        config.load_accounts()
        config.load_accounts_raw()
        config.load_accounts(include_inactive=True)
        config.accounts_with_artifacts()
        after = sha(acc)
        assert_(before == after, "读取（load_accounts / load_accounts_raw）把 accounts.json 改了")
        left = raw_accounts(pipe)["accounts"][0]
        assert_(left["out_dir"] == _OLD_ABS and left["audio_dir"] == _OLD_AUD,
                f"文件里的旧路径被改写了：{left}")


check("1(a)(d)(e) 别的安装位置 / 当前包内 / 旧路径真实存在：三个条件都按合取判", case_normalize)
check("1(f) audio_dir 尾巴必须与账号名对得上（other_audio 不归一）", case_audio_tail_mismatch)
check("1(g) 读取不写文件：load_accounts / load_accounts_raw 前后 accounts.json 的 sha256 相同", case_no_write)


# ── 2. 写侧不变：归一结果绝不落盘 ──────────────────────────────────
def case_write_keeps_old_paths():
    with sandbox("write") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [
            {"account": "acc1", "active": True, "out_dir": _OLD_ABS, "audio_dir": _OLD_AUD},
            {"account": "acc2", "active": True, "out_dir": _OLD_ABS, "audio_dir": _OLD_AUD},
        ])

        def old_paths_kept(who):
            row = next(a for a in raw_accounts(pipe)["accounts"] if a["account"] == who)
            assert_(row["out_dir"] == _OLD_ABS, f"{who}.out_dir 被归一结果覆盖了：{row['out_dir']!r}")
            assert_(row["audio_dir"] == _OLD_AUD, f"{who}.audio_dir 被归一结果覆盖了：{row['audio_dir']!r}")

        config.update_account("acc1", {"note": "x"})
        old_paths_kept("acc1")

        row = config.add_account("newacc")
        assert_(Path(row["out_dir"]) == config.OUTPUT_DIR,
                f"新账号应指向当前包：{row['out_dir']!r}")
        old_paths_kept("acc1")

        config.set_media_type(["acc1"], "image")
        old_paths_kept("acc1")

        config.set_account_active("acc1", False)
        old_paths_kept("acc1")

        config.remove_account("acc2")
        names = [a["account"] for a in raw_accounts(pipe)["accounts"]]
        assert_("acc2" not in names, f"acc2 没删掉：{names}")
        old_paths_kept("acc1")


check("2 写侧不变：update/add/set_media_type/set_active/remove 之后文件里仍是旧路径", case_write_keeps_old_paths)


# ── 3. 真实对象：account_artifacts / accounts_with_artifacts 用当前包内路径 ──
def case_artifacts():
    with sandbox("art") as (_tmp, pipe, _ledger):
        write_accounts(pipe, [
            {"account": "acc1", "active": True, "out_dir": _OLD_ABS, "audio_dir": _OLD_AUD},
        ])
        (config.OUTPUT_DIR / "scrape_acc1.json").write_text(
            json.dumps({"items": [{"id": "v1", "publish_time_gmt8": "2026-11-05 10:00:00"}],
                        "errors": []}, ensure_ascii=False), encoding="utf-8")
        (config.OUTPUT_DIR / "acc1_audio").mkdir()
        (config.OUTPUT_DIR / "acc1_audio" / "acc1_v1.mp3").write_bytes(b"x")

        row = raw_accounts(pipe)["accounts"][0]          # 故意用**文件里的原样行**
        art = config.account_artifacts(row)
        assert_(Path(art["out_dir"]) == config.OUTPUT_DIR, f"artifacts.out_dir={art['out_dir']!r}")
        assert_(Path(art["audio_dir"]) == config.OUTPUT_DIR / "acc1_audio",
                f"artifacts.audio_dir={art['audio_dir']!r}")
        assert_(art["has_scrape"] is True, f"放好了 scrape_acc1.json 却没认出来（out_dir={art['out_dir']}）")
        assert_(art["has_audio"] is True, "放好了 acc1_*.mp3 却没认出来")

        rows = config.accounts_with_artifacts()
        got = next(r for r in rows if r["account"] == "acc1")
        assert_(Path(got["out_dir"]) == config.OUTPUT_DIR, f"accounts_with_artifacts.out_dir={got['out_dir']!r}")
        assert_(got["has_scrape"] is True, "accounts_with_artifacts 里 acc1 应 has_scrape=True")


check("3 account_artifacts / accounts_with_artifacts 指向当前包，且能认到 scrape 与音频", case_artifacts)


# ── 4. run_env() 不再强制离线 ──────────────────────────────────────
def case_run_env():
    env = config.run_env()
    assert_("HF_HUB_OFFLINE" not in env,
            f"run_env() 不该再写 HF_HUB_OFFLINE，实得 {env.get('HF_HUB_OFFLINE')!r}")
    assert_(env.get("HTTPS_PROXY") == config.PROXY, f"HTTPS_PROXY={env.get('HTTPS_PROXY')!r}")
    assert_(env.get("HTTP_PROXY") == config.PROXY, f"HTTP_PROXY={env.get('HTTP_PROXY')!r}")
    assert_(env.get("PYTHONIOENCODING") == "utf-8", f"PYTHONIOENCODING={env.get('PYTHONIOENCODING')!r}")

    with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}, clear=False):
        env2 = config.run_env()
    assert_("HF_HUB_OFFLINE" not in env2,
            f"父进程设了 HF_HUB_OFFLINE=1 也不许带下去，实得 {env2.get('HF_HUB_OFFLINE')!r}")


check("4 run_env()：没有 HF_HUB_OFFLINE（父进程设了也不带），代理与编码仍在", case_run_env)

print(f"\n通过 {_ok} / {_ok + _fail}")
sys.exit(1 if _fail else 0)
