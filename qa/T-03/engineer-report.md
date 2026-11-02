# T-03 工程师报告（crew-engineer-3 原文落盘）

> 落盘原因：T-03 的 DoD 第 6 条要求「报告给出先红后绿两段真实输出」，而 role 的报告只活在会话消息里。
> 会话结束后消息就没了，所以 PM 把它**逐字**落盘在这里。落盘时间 2026-11-02，落盘人 PM（lead）。
> 注意：这是「当时那一轮」的证据快照，红段用的是当时的代码，事后**不能重跑再现**（绿段可以）。

## 一、红、绿两段真实输出

**先说明「红」的前提**：动手改代码前，`git diff --stat -- pipeline/scripts/scrape_tiktok_video.py` 只有 T-02 留下的 `65 insertions(+), 15 deletions(-)`，我的 T-03 改动一行都还没有；测试文件 `test_scrape_no_clobber.py` 当时是新建的（`??`）。

**红（改代码前，跑新建的测试）**

```
=== 改动前 git diff --stat -- pipeline/scripts/scrape_tiktok_video.py（仍只有 T-02 的改动，没有 T-03）===
 pipeline/scripts/scrape_tiktok_video.py | 80 ++++++++++++++++++++++++++-------
 1 file changed, 65 insertions(+), 15 deletions(-)
=== 红：改动前跑 T-03 测试 ===
s2 抓取：全失败不覆盖历史产物：
  FAIL DoD1 全失败时目标文件字节级不变 -> 本次 0 条有效时不得改动文件：改前 930 字节，改后 574 字节
  ok DoD2 全失败时退出码仍为 0
  FAIL DoD3 摘要照常输出本次结果并标注未写盘 -> stderr 摘要行应标注「未写盘」，实得 stderr='[saved] C:\\Users\\Administrator\\AppData\\Local\\Temp\\noclog3_xlb09wmk\\scrape_probe.json  items=0 errors=2 blocked=0 removed=2\n'
  ok DoD4 有 items 时照常写盘
  ok DoD5 --merge 合并后历史 2 条仍在且正常写盘

通过 3 / 5
exit=1
```

红落在 DoD 1、3 两条，和 DoD 第 6 条要求一致。

**绿（改代码后，同一命令）**

```
s2 抓取：全失败不覆盖历史产物：
  ok DoD1 全失败时目标文件字节级不变
  ok DoD2 全失败时退出码仍为 0
  ok DoD3 摘要照常输出本次结果并标注未写盘
  ok DoD4 有 items 时照常写盘
  ok DoD5 --merge 合并后历史 2 条仍在且正常写盘

通过 5 / 5
exit=0
```

**统测**：7 个测试文件全部退出码 0，失败文件数 0。

```
test_acceptance_regressions.py     exit=0   Ran 27 tests in 0.364s   (OK)
test_check_api_scope.py            exit=0   通过 5 / 5
test_daily_batch_repair.py         exit=0   通过 18 / 18
test_orchestrator.py               exit=0   通过 5 / 5
test_receipts_flush.py             exit=0   通过 7 / 7
test_scrape_failure_reason.py      exit=0   通过 6 / 6
test_scrape_no_clobber.py          exit=0   通过 5 / 5
```

## 二、DoD 第 1~6 条逐条证据

| DoD | 证据 | 结果 |
|---|---|---|
| 1. 预置 2 条 items，跑一次全失败（mock，不联网）→ 文件**字节级**未变 | `case_dod1_full_failure_keeps_bytes`：`before = path.read_bytes()` / 跑 `main()` / `after == before`；红时这条真的报出「改前 930 字节，改后 574 字节」，另加断言历史 2 条 items 仍在 | ok |
| 2. 退出码仍为 0 | `case_dod2_exit_code_zero`：`assert_(rc == 0, ...)` | ok |
| 3. stdout 末行 JSON 照常输出本次结果（items `[]`、errors 条数与本次一致 2 条、ID 集合相符）；stderr 摘要行含「未写盘」 | `case_dod3_summary_marks_not_written`：解析末行 JSON 断言 `items == []`、`len(errors) == 2`、`{VID_A,VID_B}`；`"未写盘" in err_s`；另断言未写盘时**不出现** `[saved]` | ok |
| 4. 不回退正常路径：items 非空时照常写盘、内容被更新 | `case_dod4_success_still_writes`：`after != before`，文件里 items 为本次 2 条，stderr 出现 `[saved]` | ok |
| 5. `--merge`：预置 2 条历史 + 本次 0 成功 → 合并后 items 仍 2 条，正常写盘 | `case_dod5_merge_keeps_history`：文件 items 排序后 == `[HIST_A, HIST_B]`，errors 里有本次失败的 `VID_A`，文件字节被更新，stderr 出现 `[merge] 本次抓 0 条`、**不含**「未写盘」 | ok |
| 6. 先红后绿两段真实输出 | 见上：红 `通过 3 / 5` exit=1（第 1、3 条红），绿 `通过 5 / 5` exit=0 | ok |

补充两点透明说明：

- **我自己写错过一条断言，在碰代码之前就改正了**：第一次跑红时 DoD5 也红了，原因是我断言「--merge 时 stdout 摘要 items 应为 []」。那一行是**既有行为**（`--merge` 的 stdout 摘要本来就是合并后的视图），DoD 第 5 条也只要求**文件里** items 保持 2 条。于是我把该断言改成读 stderr 的 `[merge] 本次抓 0 条` 来证明「本次 0 成功」。这次改动发生在**源码一行未动**之前。
- **GBK 控制台**：测试用 ASCII 的 `ok`/`FAIL`，中文只有「未写盘」等 GBK 可编码字符。DoD 原命令在本机 `cp936` 下正常跑完、无 `UnicodeEncodeError`。

## 三、改了哪几行、判断条件

判断条件一句话：**`no_write = (本次成功条数 == 0 and 本次 errors 条数 > 0 and not --merge)` → 不写 `scrape_<account>.json`，stdout JSON 照常打印，stderr 打 `[未写盘] ...`，仍 `return 0`。**

- 关键在「**本次**」二字：`run_items = len(items)` / `run_errors = len(errors)` 取在 `--merge` 合并**之前**，否则合并后的 items 会被误判成本次成功。
- `--merge` 排除在判断之外：合并结果里含历史条目，写盘正是在保住历史（DoD 5）。
- 退出码不动：0 items 该由 `gate_scrape` 判 FAIL（我确认过 `pipeline/pipeline.py` 第 394-397 行是拿**步骤 stdout JSON** 去跑门禁，不读文件），所以不写盘不会让门禁误判成 PASS。

改动位置（`pipeline/scripts/scrape_tiktok_video.py`）：

1. 模块 docstring 加 `★ 2026-11-02（T-03）` 说明（仓库既有习惯）。
2. 新增 `run_items` / `run_errors`，并把原来这里的 `out.mkdir(...)` 移进写盘分支（未写盘时不建目录）。
3. `no_write` 判断 + 用 `if not no_write:` 包住 `mkdir / tmp.write_text / tmp.replace`。
4. stderr 摘要行分叉 —— 未写盘打 `[未写盘] <path> items=... errors=... blocked=... removed=...（本次 0 条有效，保留原文件未覆盖）`，否则沿用 `[saved]`。

相对「T-02 落地后的工作区状态」：**新增 29 行、删除 6 行**。

## 四、改动文件清单

- 改：`D:\workbuddy_project\tiktok\pipeline\scripts\scrape_tiktok_video.py`（+29/−6）
- 新建：`D:\workbuddy_project\tiktok\pipeline\tests\test_scrape_no_clobber.py`（5 个用例，对应 DoD 1~5）

**没碰**：`pipeline/tests/test_acceptance_regressions.py`（T-04 的）、`pipeline/gate.py`、`qa/`、`pipeline/accounts.json`、任何依赖；没联网（`fetch_page` / `parse_item_detail` 全 mock，`browser_batch_fallback` patch 成 `{}`）。

## 五、一个边界情况，请 PM 决定（原文见 ADR-0001）

判据里没有「目标文件是否已存在」这一条，所以：**首次运行就全失败（目标文件本来不存在）时，现在不会创建任何 `scrape_<account>.json`**（旧行为会创建一个 0 条的空文件）。DoD 与交接文档的字面都是「本次 items 为空且 errors 非空 → 不写盘」，我按字面做了；若你希望「无历史文件时仍落一份错误记录」，那要改判据（加 `and path.exists()`），属于要你决定的取舍——我没有写 `Q-02`，因为它不阻塞 M1，且 DoD 文字本身已经限定了方向。
