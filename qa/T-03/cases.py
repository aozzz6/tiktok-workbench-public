# -*- coding: utf-8 -*-
r"""qa/T-03/cases.py — T-03「全失败不覆盖历史产物」的 QA 用例（crew T-03，QA 第 2 步）

清单来源：`<job folder>/T-03-plan.md` 的 C-01 ~ C-09（一条一条照写）
DoD 来源：`docs/tasks/tasks.md` v1 · T-03 的 DoD 第 1-6 条
相关决定：`docs/decisions/adr/0001-first-run-full-failure-no-file.md`
          首次运行就全失败、目标文件本来不存在 → **不写文件是有意的**，
          本文件的用例不把这件事当缺陷（所有用例都先预置历史文件，见 C-01 的前置断言）。

跑法（在仓库根 D:\workbuddy_project\tiktok 下）：
    powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-03\run.ps1

三条硬约束（清单第 9 行 + 任务书）：
  1. **不联网**：`fetch_page` / `parse_item_detail` 全部 mock，
     `browser_batch_fallback` 一律 patch 成 {}（绝不真起 playwright 子进程）。
  2. **不碰 pipeline\output\ 下的真实产物**：每个用例都在 `tempfile.mkdtemp()` 里跑。
  3. **不改产品代码**：只读 `pipeline\scripts\scrape_tiktok_video.py`。
     用例红了就是发现缺陷，报回 PM，绝不改代码让它变绿。
     （C-09 会临时造一份**内存里的**变异源码，只为证明用例能变红；磁盘上的产品代码不动一个字节。）

判定口径：每条用例失败即整脚本退出码 1；输出只用 ASCII 的 ok / FAIL。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:  # 本机控制台是 GBK：编码兜底，别让一条打不出来的字符把整个脚本炸掉
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ---------- 路径与常量 ----------

QA_DIR = Path(__file__).resolve().parent          # ...\qa\T-03
REPO = QA_DIR.parent.parent                       # 仓库根 D:\workbuddy_project\tiktok
PY = REPO / "pipeline" / ".venv" / "Scripts" / "python.exe"
SCRIPTS = REPO / "pipeline" / "scripts"
UNIT_TEST = REPO / "pipeline" / "tests" / "test_scrape_no_clobber.py"

ACC = "x"                                         # 产物文件名就是 scrape_x.json（同清单写法）
VIDS = ["7696000000000000001", "7696000000000000002", "7696000000000000003"]
HIST_A = "7691000000000000001"
HIST_B = "7691000000000000002"

_TMP_DIRS: list[Path] = []


# ---------- 子进程探针（mock 全部写在里面，父进程只管读 stdout/stderr/退出码） ----------

DRIVER = r'''# -*- coding: utf-8 -*-
# T-03 QA 探针：在子进程里跑一次 scrape_tiktok_video.main()，抓取层全部 mock。
# 父进程要的是**真实退出码**与**真实的 stdout/stderr 流**（C-02/C-03/C-04 的判定依据）。
import json
import os
import re
import sys
import types
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

scripts = os.environ["QA_SCRIPTS_DIR"]
sys.path.insert(0, scripts)

mode = os.environ.get("QA_MOCK_MODE", "fail")        # "fail" | "ok"
mutation = os.environ.get("QA_MUTATION", "")         # "" | "force_write" | "never_write"
args = json.loads(os.environ.get("QA_ARGS", "[]"))

if mutation:
    src_path = os.path.join(scripts, "scrape_tiktok_video.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    new_line = "no_write = True" if mutation == "never_write" else "no_write = False"
    src2, n = re.subn(r"no_write\s*=\s*\([^)\n]*\)", new_line, src, count=1)
    if n != 1:
        # 找不到那条判定行 → 变异没生效，用例无法证明任何事，必须响亮地失败
        sys.stderr.write("MUTATE_NOT_FOUND\n")
        sys.exit(3)
    mod = types.ModuleType("scrape_tiktok_video_mut")
    mod.__file__ = src_path
    exec(compile(src2, src_path, "exec"), mod.__dict__)
else:
    import scrape_tiktok_video as mod

ITEM = {
    "desc": "本次抓到的文案 #tail",
    "createTime": 1761900000,
    "stats": {"playCount": 9, "diggCount": 1, "commentCount": 2,
              "shareCount": 3, "collectCount": 4},
    "challenges": [{"title": "tail"}],
    "music": {"title": "bgm"},
    "author": {"uniqueId": "x"},
}


def _fail_detail(html):
    """本次全部失败：10204 作者审核中（归类仍 removed，不重试、不触浏览器兜底）。"""
    return None, "removed", 10204, "author_status,status_reviewing"


def _ok_detail(html):
    return dict(ITEM), "", 0, ""


detail = _fail_detail if mode == "fail" else _ok_detail

sys.argv = ["scrape_tiktok_video.py"] + list(args)
with patch.object(mod, "fetch_page", return_value="<html></html>"), \
        patch.object(mod, "parse_item_detail", side_effect=detail), \
        patch.object(mod, "browser_batch_fallback", return_value={}), \
        patch.object(mod.time, "sleep", side_effect=lambda s: None):
    rc = mod.main()
sys.exit(rc)
'''


# ---------- 房子风格：check / assert_ ----------

_ok = 0
_fail = 0


def check(name, fn):
    global _ok, _fail
    try:
        fn()
        print(f"  ok {name}")
        _ok += 1
    except AssertionError as e:
        print(f"  FAIL {name} -> {e}")
        _fail += 1
    except Exception as e:
        print(f"  FAIL {name} -> {e.__class__.__name__}: {e}")
        _fail += 1


def assert_(c, m="assert failed"):
    if not c:
        raise AssertionError(m)


# ---------- 临时目录与预置数据 ----------

def _mk_out(prefix: str = "qa_t03_") -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TMP_DIRS.append(d)
    return d


def _history_rows() -> list[dict]:
    return [
        {"id": HIST_A, "url": f"https://www.tiktok.com/@{ACC}/video/{HIST_A}",
         "desc": "历史条目一 #tag", "hashtags": ["tag"],
         "publish_time_gmt8": "2026-10-30 09:00:00",
         "stats": {"playCount": 100, "diggCount": 7, "commentCount": 1,
                   "shareCount": 2, "collectCount": 3},
         "music": "bgm-1", "author": ACC},
        {"id": HIST_B, "url": f"https://www.tiktok.com/@{ACC}/video/{HIST_B}",
         "desc": "历史条目二 #tag", "hashtags": ["tag"],
         "publish_time_gmt8": "2026-10-31 10:00:00",
         "stats": {"playCount": 200, "diggCount": 8, "commentCount": 2,
                   "shareCount": 3, "collectCount": 4},
         "music": "bgm-2", "author": ACC},
    ]


def _history_file(out: Path) -> Path:
    """在临时目录预置一份含 2 条历史 items 的 scrape_x.json（模拟上一次全量抓取的产物）。"""
    p = out / f"scrape_{ACC}.json"
    p.write_text(json.dumps({
        "account": ACC, "policy": "strict", "scraped_at": "2026-11-01T09:00:00",
        "reasons": {"blocked": 0, "removed": 0}, "interval_s": 5.0,
        "errors": [], "items": _history_rows(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _audio_dir(out: Path, vids: list[str]) -> Path:
    """建一个假音频目录：collect_ids() 从文件名取视频 ID —— 这是真实事故的「全量抓取」入口。"""
    d = out / f"{ACC}_audio"
    d.mkdir(parents=True, exist_ok=True)
    for v in vids:
        (d / f"{ACC}_{v}.mp3").write_bytes(b"")
    return d


# ---------- 跑一次真实的抓取子进程 ----------

def _run(out: Path, vids, mode: str = "fail", merge: bool = False,
         mutation: str = "") -> dict:
    """跑一次 scrape_tiktok_video 子进程，返回 {rc, stdout, stderr, started}。

    vids=None → 全量抓取形态（--audio-dir，不带 --ids/--merge，真实事故入口）
    vids=[...] → --ids 形态（与 --merge 一起用）
    started 是子进程启动**之前**取的时刻，C-05/C-07 用它判「文件被重新写过」。
    """
    drv_dir = _mk_out("qa_t03_drv_")
    drv = drv_dir / "driver.py"
    drv.write_text(DRIVER, encoding="utf-8")

    args = ["--account", ACC, "--out-dir", str(out),
            "--interval", "0", "--blocked-retry", "0"]
    if vids is None:
        args += ["--audio-dir", str(_audio_dir(out, VIDS))]
    else:
        args += ["--ids", ",".join(vids)]
    if merge:
        args.append("--merge")

    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "QA_SCRIPTS_DIR": str(SCRIPTS),
        "QA_MOCK_MODE": mode,
        "QA_MUTATION": mutation,
        "QA_ARGS": json.dumps(args, ensure_ascii=False),
    })
    started = time.time()
    p = subprocess.run([str(PY), str(drv)], cwd=str(REPO), env=env,
                       capture_output=True, timeout=180)
    return {
        "rc": p.returncode,
        "stdout": p.stdout.decode("utf-8", "replace"),
        "stderr": p.stderr.decode("utf-8", "replace"),
        "started": started,
        "raw": p,
    }


def _rc_msg(r: dict, action: str) -> str:
    if r["rc"] == 3:
        return (f"{action}：变异没生效（产品代码里找不到 no_write 判定行，driver 打印了 "
                f"MUTATE_NOT_FOUND）→ 用例无法证明任何事，按失败处理。"
                f"stderr={r['stderr'][-300:]!r}")
    return (f"{action}：期望退出码 0（失败该由 gate 判，不由本脚本判），实得 {r['rc']}；"
            f"stderr 尾部={r['stderr'][-400:]!r}；stdout 尾部={r['stdout'][-300:]!r}")


def _last_line_json(stdout: str) -> dict:
    """取 stdout 末行解析成 JSON（清单 C-03 的判定方式：就是末行）。"""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    assert_(len(lines) > 0, "stdout 是空的，期望末行是本次摘要 JSON")
    last = lines[-1].strip()
    try:
        return json.loads(last)
    except Exception as e:
        raise AssertionError(
            f"stdout 末行不是可解析的 JSON；期望末行是 {{...}} 摘要，"
            f"实得 {last[:200]!r}（{e.__class__.__name__}: {e}）")


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- C-01 全失败不覆盖已有文件（字节级） ----------

def c01_full_failure_keeps_bytes():
    out = _mk_out()
    path = _history_file(out)
    # 前置：运行前文件必须真的存在，否则「没被改」就是「本来没文件」的假通过
    assert_(path.exists(), f"前置条件不成立：运行前 {path} 必须存在，实得不存在")
    before = path.read_bytes()
    l0 = len(before)
    assert_(l0 > 0, f"前置条件不成立：预置文件应为非空，实得 {l0} 字节")
    assert_(len(_read_json(path)["items"]) == 2,
            f"前置条件不成立：预置文件应有 2 条历史 items，"
            f"实得 {len(_read_json(path)['items'])} 条")

    r = _run(out, None, mode="fail")
    assert_(r["rc"] == 0, _rc_msg(r, "C-01 全失败运行"))

    assert_(path.exists(), f"运行后 {path} 不见了：期望原文件留在原处")
    after = path.read_bytes()
    assert_(len(after) == l0,
            f"期望文件长度仍为 L0={l0} 字节，实得 {len(after)} 字节（文件被改写了）")
    assert_(after == before,
            f"期望字节序列与运行前完全相同（L0={l0} 字节），实得不同"
            f"（改后 {len(after)} 字节，前 120 字节 {after[:120]!r}）")


# ---------- C-02 退出码仍为 0 ----------

def c02_exit_code_still_zero():
    out = _mk_out()
    _history_file(out)
    r = _run(out, None, mode="fail")
    assert_(r["rc"] == 0, _rc_msg(r, "C-02 全失败运行"))


# ---------- C-03 摘要照常输出本次结果 ----------

def c03_summary_reports_this_run():
    out = _mk_out()
    _history_file(out)
    r = _run(out, None, mode="fail")
    assert_(r["rc"] == 0, _rc_msg(r, "C-03 全失败运行"))
    s = _last_line_json(r["stdout"])
    assert_(s.get("items") == [],
            f"期望末行 JSON 的 items == []（本次 0 条），实得 {s.get('items')!r}")
    n_injected = len(VIDS)
    got = s.get("errors") or []
    assert_(len(got) == n_injected,
            f"期望末行 JSON 的 errors 条数等于本次注入的 {n_injected} 条，实得 {len(got)} 条")
    assert_({str(e.get("id")) for e in got} == set(VIDS),
            f"期望 errors 就是本次注入的 {VIDS}，实得 {[e.get('id') for e in got]}")


# ---------- C-04 明确标注「未写盘」 ----------

def c04_marks_not_written():
    out = _mk_out()
    _history_file(out)
    r = _run(out, None, mode="fail")
    assert_(r["rc"] == 0, _rc_msg(r, "C-04 全失败运行"))
    merged = r["stdout"] + "\n" + r["stderr"]
    assert_("未写盘" in merged,
            f"期望本次运行的 stdout+stderr 出现子串「未写盘」，实得没有；"
            f"stderr 尾部={r['stderr'][-400:]!r}")
    assert_("[saved]" not in merged,
            f"期望未写盘时不要出现 [saved]（两者同时出现等于自相矛盾）；"
            f"stderr 尾部={r['stderr'][-400:]!r}")


# ---------- C-05 成功照写（C-01 的反面，成对存在） ----------

def c05_success_still_writes():
    out = _mk_out()
    path = _history_file(out)
    assert_(path.exists(), f"前置条件不成立：运行前 {path} 必须存在，实得不存在")
    before = path.read_bytes()

    r = _run(out, None, mode="ok")
    assert_(r["rc"] == 0, _rc_msg(r, "C-05 成功运行"))

    after = path.read_bytes()
    assert_(after != before,
            f"期望 items 非空时照常写盘（字节与运行前不同），实得字节完全相同"
            f"（{len(before)} 字节）→ 写盘被整个关掉了")
    saved = _read_json(path)
    got = [str(i.get("id")) for i in (saved.get("items") or [])]
    assert_(got == VIDS,
            f"期望文件 items 等于本次结果 {VIDS}，实得 {got}")
    assert_(not (saved.get("errors") or []),
            f"期望成功运行时 errors 为空，实得 {saved.get('errors')!r}")

    mtime = path.stat().st_mtime
    assert_(mtime > r["started"],
            f"期望文件 mtime 晚于本次运行开始时间（证明本次真的写了），"
            f"实得 mtime={mtime}、started={r['started']}")


# ---------- C-06 --merge 下 0 成功，历史 2 条仍在 ----------

def c06_merge_keeps_history():
    out = _mk_out()
    path = _history_file(out)
    preset = _read_json(path)["items"]
    assert_(len(preset) == 2, f"前置条件不成立：应预置 2 条历史 items，实得 {len(preset)} 条")

    r = _run(out, VIDS[:2], mode="fail", merge=True)
    assert_(r["rc"] == 0, _rc_msg(r, "C-06 --merge 0 成功运行"))

    assert_(path.exists(), f"运行后 {path} 不见了：--merge 必须正常写盘")
    saved = _read_json(path)
    items = saved.get("items") or []
    assert_(len(items) == 2,
            f"期望 --merge 合并后 items 仍为 2 条（历史保住），实得 {len(items)} 条")
    for old in preset:
        hit = [it for it in items if str(it.get("id")) == str(old["id"])]
        assert_(len(hit) == 1,
                f"期望历史条目 {old['id']} 仍在且只有一条，实得 {len(hit)} 条；"
                f"文件里的 id={[it.get('id') for it in items]}")
        assert_(hit[0] == old,
                f"期望历史条目 {old['id']} 逐字段不变，实得 {hit[0]!r}，原样 {old!r}")


# ---------- C-07 --merge 下 0 成功仍然真写盘（C-06 的反例） ----------

def c07_merge_still_rewrites_file():
    out = _mk_out()
    path = _history_file(out)
    assert_(path.exists(), f"前置条件不成立：运行前 {path} 必须存在，实得不存在")
    before = path.read_bytes()

    r = _run(out, VIDS[:2], mode="fail", merge=True)
    assert_(r["rc"] == 0, _rc_msg(r, "C-07 --merge 0 成功运行"))

    mtime = path.stat().st_mtime
    assert_(mtime > r["started"],
            f"期望 --merge 下文件被**重新写过**（mtime 应晚于本次运行开始 {r['started']}），"
            f"实得 mtime={mtime} → 文件被跳过了；这样 C-06 的「仍是 2 条」是靠拒绝写盘蒙对的")
    after = path.read_bytes()
    assert_(after != before,
            f"期望 --merge 下字节与运行前不同（scraped_at 与本次 errors 会变），"
            f"实得完全相同（{len(before)} 字节）→ 事实上没写盘")
    assert_("未写盘" not in (r["stdout"] + "\n" + r["stderr"]),
            f"期望 --merge 不走「未写盘」分支，实得出现「未写盘」；"
            f"stderr 尾部={r['stderr'][-400:]!r}")


# ---------- C-08 新增的单元测试文件现在跑是绿的 ----------

def c08_unit_test_file_green():
    assert_(UNIT_TEST.exists(),
            f"期望单元测试文件存在：{UNIT_TEST}，实得不存在")
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    p = subprocess.run([str(PY), str(UNIT_TEST)], cwd=str(REPO), env=env,
                       capture_output=True, timeout=300)
    text = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    assert_(p.returncode == 0,
            f"期望退出码 0，实得 {p.returncode}；输出尾部={text[-600:]!r}")
    m = re.search(r"通过\s*(\d+)\s*/\s*(\d+)", text)
    assert_(m, f"期望输出里有「通过 N/M」，实得没有；输出尾部={text[-600:]!r}")
    n, total = int(m.group(1)), int(m.group(2))
    assert_(total > 0, f"期望总条数 N > 0，实得 N={n} / M={total}")
    assert_(n == total,
            f"期望全部通过（通过 N / N），实得 通过 {n} / {total}")
    print(f"    [C-08] {UNIT_TEST.name} -> 通过 {n}/{total}，退出码 0")


# ---------- C-09 反假绿：变异证明这些断言真的能变红 ----------

def c09_mutation_proof():
    """清单 C-09 要求「先红后绿」两段真实输出 —— 那一半是**文档证据**（工程师报告），
    无法重跑。本用例做它的机器可判定等价物：在**内存里**造两份变异源码，
    证明 C-01/C-04 与 C-05/C-07 的断言在错误实现下确实会红。产品代码一个字节不动。
    """
    # 变异 M1：no_write 恒 False（= 改动前「总是写盘」）→ C-01 与 C-04 必须变红
    out1 = _mk_out()
    p1 = _history_file(out1)
    before1 = p1.read_bytes()
    r1 = _run(out1, None, mode="fail", mutation="force_write")
    assert_(r1["rc"] == 0, _rc_msg(r1, "C-09 变异 M1 运行"))
    after1 = p1.read_bytes()
    assert_(after1 != before1,
            f"变异 M1（no_write 恒 False，模拟改动前的行为）跑完之后文件竟然没变："
            f"说明 C-01 的字节比对区分不出「写盘」与「不写盘」→ C-01 是假绿")
    assert_("未写盘" not in (r1["stdout"] + "\n" + r1["stderr"]),
            "变异 M1（总是写盘）之后 stderr 里仍有「未写盘」："
            "说明 C-04 的字符串断言区分不出 → C-04 是假绿")

    # 变异 M2：no_write 恒 True（= 干脆永不写盘）→ C-05 必须变红
    out2 = _mk_out()
    p2 = _history_file(out2)
    before2 = p2.read_bytes()
    r2 = _run(out2, None, mode="ok", mutation="never_write")
    assert_(r2["rc"] == 0, _rc_msg(r2, "C-09 变异 M2 运行"))
    assert_(p2.read_bytes() == before2,
            f"变异 M2（no_write 恒 True，模拟「干脆永不写盘」）之后文件竟然被改了："
            f"说明 C-05 的写盘断言没有区分力 → C-05 是假绿")

    # 变异 M2 同样要压住 C-07：--merge 下被跳过写盘时 C-07 必须能看出来
    out3 = _mk_out()
    p3 = _history_file(out3)
    before3 = p3.read_bytes()
    r3 = _run(out3, VIDS[:2], mode="fail", merge=True, mutation="never_write")
    assert_(r3["rc"] == 0, _rc_msg(r3, "C-09 变异 M2(--merge) 运行"))
    assert_(p3.read_bytes() == before3,
            f"变异 M2(--merge) 之后文件竟然被改了：说明 C-07 的「被重写过」断言"
            f"没有区分力 → C-07 是假绿")
    mt3 = p3.stat().st_mtime
    assert_(not (mt3 > r3["started"]),
            f"变异 M2(--merge) 之后 mtime 竟然晚于运行开始（{mt3} > {r3['started']}）："
            f"说明 C-07 的 mtime 断言不可信 → C-07 是假绿")


def main() -> int:
    print("T-03 全失败不覆盖历史产物 — QA 用例（qa/T-03）")
    print(f"解释器: {PY}")
    if not PY.exists():
        print(f"  FAIL 前置：找不到解释器 {PY}")
        print(f"\n通过 0 / 1")
        return 1
    if not SCRIPTS.joinpath("scrape_tiktok_video.py").exists():
        print(f"  FAIL 前置：找不到产品脚本 {SCRIPTS / 'scrape_tiktok_video.py'}")
        print(f"\n通过 0 / 1")
        return 1

    cases = [
        ("C-01 一次 0 条有效的抓取不覆盖已有的 scrape_x.json（字节级）",
         c01_full_failure_keeps_bytes),
        ("C-02 全失败运行的退出码仍为 0", c02_exit_code_still_zero),
        ("C-03 摘要照常输出本次结果（items []、errors 条数一致）",
         c03_summary_reports_this_run),
        ("C-04 摘要明确标注未写盘", c04_marks_not_written),
        ("C-05 正常路径没被一起关掉：items 非空时照常写盘", c05_success_still_writes),
        ("C-06 --merge 下 0 成功，历史 2 条仍在", c06_merge_keeps_history),
        ("C-07 --merge 下 0 成功仍然走写盘路径（不是靠拒绝写盘蒙对）",
         c07_merge_still_rewrites_file),
        ("C-08 新增单元测试文件现在跑是绿的", c08_unit_test_file_green),
        ("C-09 反假绿：变异后 C-01/C-04 与 C-05/C-07 都能变红", c09_mutation_proof),
    ]
    try:
        for name, fn in cases:
            check(name, fn)
    finally:
        for d in _TMP_DIRS:
            shutil.rmtree(d, ignore_errors=True)

    print("  note C-09: 清单要求的「改动前跑该测试文件的真实输出」是文档证据（工程师报告），"
          "无法重跑；本用例只机器验证了它的等价性质（错误实现下必然变红），"
          "那两段报告原文仍需 PM 核对。")
    print(f"\n通过 {_ok} / {_ok + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
