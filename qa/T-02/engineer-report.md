# T-02 工程师报告（crew-engineer-2 原文落盘）

> 落盘原因：T-02 的 DoD 第 6 条要求「报告给出先红后绿两段真实输出」，而 role 的报告只活在会话消息里。
> 会话结束后消息就没了，所以 PM 把它**逐字**落盘在这里。落盘时间 2026-11-02，落盘人 PM（lead）。
> 注意：这是「当时那一轮」的证据快照。红段用的是当时的代码，事后**不能重跑再现**（绿段可以）。

已认领并按 T-02 行 DoD 完成，任务板 `task-2` 已 complete。

## 1. 红：改动产品代码**之前**的真实输出

测试文件先写好（此时 `scrape_tiktok_video.py` 一行未改），命令：
`D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe pipeline\tests\test_scrape_failure_reason.py`

```
抓取失败原因（T-02 DoD）：
  FAIL 10204 错误文本带真实 statusCode+statusMsg（归类仍 removed） -> error 应含 statusCode 10204，实得 'itemStruct 空（已下架/私有）'
  FAIL 100001 归 blocked，--blocked-retry 2 时真抓 3 次 -> --blocked-retry 2 应对同一 URL 抓 1+2=3 次，实得 1 次
  ok 无内嵌数据仍归 blocked（不回退）
  ok 完整 itemStruct 仍成功、items 非空、errors 为空
  FAIL parse_item_ex 仍是 (item, reason) 二元组 -> 100001 应归 blocked，实得 'removed'
  ok --blocked-retry/--blocked-sleep 默认值与环境覆盖不变

通过 3 / 6
EXITCODE=1
```

## 2. 绿：改动**之后**的真实输出

```
抓取失败原因（T-02 DoD）：
  ok 10204 错误文本带真实 statusCode+statusMsg（归类仍 removed）
  ok 100001 归 blocked，--blocked-retry 2 时真抓 3 次
  ok 无内嵌数据仍归 blocked（不回退）
  ok 完整 itemStruct 仍成功、items 非空、errors 为空
  ok parse_item_ex 仍是 (item, reason) 二元组
  ok --blocked-retry/--blocked-sleep 默认值与环境覆盖不变

通过 6 / 6
EXITCODE=0
```

真实错误文本样例（同一份代码跑出来的）：
```
10204  -> removed | itemStruct 空（已下架/私有） [statusCode 10204 author_status,status_reviewing]
100001 -> blocked | 风控页（无内嵌数据，可重试） [statusCode 100001 RPC 网络错误（瞬断）]
parse_item_ex(10204) -> (None, 'removed')
```

## 3. 新增函数与返回形状

- **`parse_item_detail(html) -> (itemStruct, reason, statusCode, statusMsg)`**（4 元组）：reason 与旧函数完全一致，后两项是 `webapp.video-detail` 里的原始 `statusCode`/`statusMsg`。
- **`failure_text(reason, status_code, status_msg) -> str`**：拼错误文本，有 status 时追加 `[statusCode 10204 author_status,status_reviewing]`，没有则退回原话术。
- **`parse_item_ex()` 为什么没被破坏**：它现在只是 `item, reason, _, _ = parse_item_detail(html)` 再 `return item, reason`，**仍是 2 元组、名字不变**；实测 `fetch_item_media.py:42` 与 `retry_scrape_failed.py:56` 的解包写法原样成立（测试里有 `item, reason = sc.parse_item_ex(...)` 断言）。`parse_item()` 兼容入口也仍在、可用。归类上 `NETWORK_STATUS_CODES = {"100001"}`，10204 不在集合内 → 仍 `removed`（PRD 关键决定 1）。

## 4. DoD 第 1~6 条对应证据

| # | 证据 |
|---|---|
| 1 | 用例「10204 错误文本带真实 statusCode+statusMsg」绿。固定 HTML 写死在测试里（`statusCode:10204`、`statusMsg:"author_status,status_reviewing"`、`itemInfo:{}`），走完一次抓取后 `errors[0].error` 同时含 `10204` 与 `author_status,status_reviewing`，`reason` 仍 `removed`；抓 1 次、未触发兜底 |
| 2 | 用例「100001 归 blocked，--blocked-retry 2 时真抓 3 次」绿：计数 mock 下同一 URL 被调 **3** 次，`errors[0].reason == "blocked"`，`--blocked-sleep 0.25` 落在 sleep 记录里恰好两次（`[0.25, 0.25]`），不靠计时 |
| 3 | 两条用例绿：无 `__UNIVERSAL_DATA_FOR_REHYDRATION__` → `blocked`；完整 `itemStruct` → `items` 1 条、`errors == []`，`hashtags == ["tail"]`、`playCount == 123` |
| 4 | 用例绿：`item, reason = sc.parse_item_ex(SAMPLE_10204)` 不抛异常，`item is None`、`reason == "removed"`；并断言 `parse_item_detail` 是 4 元组、statusCode=10204、statusMsg 原样 |
| 5 | 用例绿：清空 `WB_SCRAPE_BLOCKED_*` 的子进程里默认值是 `[1, 8.0]`；设 `WB_SCRAPE_BLOCKED_RETRY=4`、`WB_SCRAPE_BLOCKED_SLEEP=2.5` → `[4, 2.5]`。传参行为由第 2 条（retry=2 / sleep=0.25）证明 |
| 6 | 上面第 1、2 节就是红、绿两段真实输出（红 3/6 退出码 1 → 绿 6/6 退出码 0） |

## 5. 回归与约束核对

- 统测跑了一遍全量 pipeline 测试：`test_check_api_scope` 5/5、`test_daily_batch_repair` 18/18、`test_orchestrator` 5/5、`test_receipts_flush` 7/7、`test_scrape_failure_reason` 6/6 全绿；`test_acceptance_regressions` 仍是基线那 2 个 error，**没有新增失败**——正是 T-04 的活。
- 未联网：`fetch_page` 全部 mock，HTML 样例写死；`browser_batch_fallback` 在每个用例里都 patch 成 `{}`。
- 未动 `pipeline/gate.py`；未加依赖；未执行 git 写操作。
- 顺带把 `--blocked-retry` / `--blocked-sleep` 的 help 文案从「风控页」改成「可重试失败（风控页/网络瞬断）」，只是说明文字，行为未变。

## 6. 改动文件清单

1. `pipeline/scripts/scrape_tiktok_video.py`（改）
   - 新增 `NETWORK_STATUS_CODES = {"100001"}`
   - 新增 `parse_item_detail()`；`parse_item_ex()` 改为委托它、返回形状不变
   - 新增 `failure_text()`
   - 抓取循环改用 `parse_item_detail`，失败文本改走 `failure_text`
   - 模块 docstring 补 2026-11-02（T-02）说明
2. `pipeline/tests/test_scrape_failure_reason.py`（新建，6 条用例）

## 7. 给 T-04 的 API 事实

`main()` 现在真正调的是 **`parse_item_detail`**，所以 `test_acceptance_regressions.py` 里两处 mock 目标要这样改：
- `scrape_run()`：把 `patch.object(sc,'parse_item',...)` 改成 `patch.object(sc,'parse_item_detail',return_value=(html_item, "", 0, ""))`（4 元组）；
- `test_retry_scrape_preserves_long_desc_and_hashtags`：`retry_scrape_failed.py` 里调的是 `sc.parse_item_ex`，所以改成 `patch.object(sc,'parse_item_ex',return_value=(item, ""))`（2 元组）。
`parse_item` / `parse_item_ex` 两个兼容入口都还在、签名未变。
