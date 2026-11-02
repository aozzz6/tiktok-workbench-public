# T-04 工程师报告（crew-engineer-4 原文落盘）

> 为什么在这里：T-04 的 DoD 第 1、2 条要求「报告给出改动前后两段真实输出」。报告原本只存在于会话消息里，
> 消息不是文件。PM 把 `crew-engineer-4` 交回的报告**逐字**落盘在这里，供 `qa\T-04\cases.py` 的
> C-08/C-09 读取。落盘时间：2026-11-02，落盘人：PM（lead）。
> **注意**：这是「当时那一轮」的证据快照，不是可以重跑复现的东西；能重跑的部分见用例自己的替代判据。

T-04 完成（共享任务板 task-4 已 complete）。只改了 `pipeline/tests/test_acceptance_regressions.py`，没碰 `pipeline/scripts/scrape_tiktok_video.py`（T-03 的文件），没加依赖，没做任何 git 操作。

## 1. 改动前真实输出（基线复现，退出码 1）
命令：`.\pipeline\.venv\Scripts\python.exe pipeline\tests\test_acceptance_regressions.py`

```
test_retry_scrape_preserves_long_desc_and_hashtags ... ERROR
test_scrape_long_desc_and_rate_limit ... [fallback] HTTP 通道仍有 2 条 blocked → 浏览器兜底批量抓取
[warn] 浏览器兜底失败: fallback_fail: Page.content: Unable to retrieve content because the page is navigating and changing the content.
[fallback] 浏览器兜底结果: 成功 0 / 还有 2 条 error
[merge] 本次抓 0 条，合并后 items=0 errors=2
[saved] ...\tmpw37bll0a\output\scrape_probe.json  items=0 errors=2 blocked=2 removed=0
ERROR

ERROR: test_retry_scrape_preserves_long_desc_and_hashtags
  File "...\test_acceptance_regressions.py", line 311, in ...
    self.assertEqual(data['items'][0]['desc'],item['desc'])
IndexError: list index out of range

ERROR: test_scrape_long_desc_and_rate_limit
  File "...\test_acceptance_regressions.py", line 200, in ...
    self.assertGreater(len(data['items'][0]['desc']),200)
IndexError: list index out of range

Ran 27 tests in 10.970s
FAILED (errors=2)
[EXITCODE=1]
```
注意基线输出里**真的出现了** `[fallback]` 与 `[warn] 浏览器兜底失败`——即真上网，和 DoD 第 3 条的病一致。

## 2. 改动后真实输出（退出码 0）
```
Ran 27 tests in 0.371s
OK
[EXITCODE=0]
```
`def test_` 计数 = **27**；`skipTest|@unittest.skip` 计数 = **0**；断言一条未改（`git diff` 只动了 patch 目标，断言行原样保留）。

## 3. 两处 mock 目标：从什么 → 改成什么，为什么对（对着产品代码真实签名）

**A. `scrape_run()`（原第 193 行）**
- 改前：`patch.object(sc,'parse_item',return_value=None if fail else html_item)`
- 改后：`patch.object(sc,'parse_item_detail',return_value=parsed)`，其中
  `parsed = (None,'removed',None,'') if fail else (html_item,'',0,'')`
- 依据（我读了产品代码，不是照抄）：`pipeline/scripts/scrape_tiktok_video.py`
  - 第 108 行 `def parse_item_detail(html) -> tuple[dict|None, str, int|str|None, str]`（**4 元组**）
  - 第 283 行 `item, reason, status_code, status_msg = parse_item_detail(fetch_page(url))`
  - 第 294 行 重试分支同样调 `parse_item_detail(fetch_page(url))`
  - 第 152-154 行 `parse_item(html)` 现在只是 `parse_item_ex(html)[0]`，而 `main()` **完全不调** `parse_item` → 旧 mock 打空 → `fetch_page` 真跑 → 403/风控页 → `reason='blocked'` → 第 313-317 行进浏览器兜底 → `items` 为空。
  改后 mock 落在 `main()` 真正解包的位置，返回形状与第 283 行一致；`('', 0, '')` 让 `failure_text` 不追加假 statusCode。`fail=True` 分支（当前无用例使用）返回 `reason='removed'`，因此不触发重试、也不触发浏览器兜底，语义仍自洽。

**B. `test_retry_scrape_preserves_long_desc_and_hashtags`（原第 308 行）**
- 改前：`patch.object(sc,'parse_item',return_value=item)`
- 改后：`patch.object(sc,'parse_item_ex',return_value=(item,''))`
- 依据：`pipeline/scripts/retry_scrape_failed.py` 第 56 行 `item, reason = sc.parse_item_ex(sc.fetch_page(url))`——按**二元组**解包，所以目标是 `parse_item_ex`，且必须返回 `(item, reason)` 二元组，不能返回裸 item（否则 unpack 报错），也不能改成 4 元组。

两处都保留了原有的 `patch.object(sc,'fetch_page',return_value='fixture')`，现在它只是双保险：解析函数已被 mock，`fetch_page` 的返回值不再决定结果。

## 4. 证明「输出里没有 `[fallback]`」的真实证据
把整次运行输出重定向到 `%TEMP%\t04_after.log` 后检索：
```
Select-String -Path $env:TEMP\t04_after.log -Pattern '\[fallback\]|浏览器兜底'
→ NO [fallback] / 浏览器兜底 in output (0 matches)
```
旁证：同一条命令耗时从基线的 **10.970s** 掉到 **0.371s**（约 1/30），符合「不再真上网、不再起 playwright」的判断；基线那次确实有 10.9s 的网络+浏览器开销。

## 5. 改动文件清单（只有 1 个）
- `pipeline/tests/test_acceptance_regressions.py`（`git diff --stat`：1 file changed, 8 insertions(+), 2 deletions(-)；同时带有 `★ 2026-11-02（T-04）` 中文注释说明改因，符合 PRD 第 3 节代码风格）。

## 6. 需要你知道的两点
- 工作区当前还有别人未提交的改动：`M pipeline/scripts/scrape_tiktok_video.py`（T-02/T-03）、`?? pipeline/tests/test_scrape_failure_reason.py`（T-02）、`?? qa/`。都**不是我**改的，我一个字都没动。
- `scrape_run(..., fail=True)` 这个参数目前没有任何用例使用（改前就是这样）。我保留它的可用性但没为它新增用例——增删用例都超出 T-04 的 DoD，如需要请 PM 定。
