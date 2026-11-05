# TikTok 对标工作台

本地 Web 控制台，用于编排、监控 `D:\workbuddy_project\tiktok\pipeline` 的 TikTok 对标流水线，
并把需要模型判断的步骤**转交 WorkBuddy Agent** 执行（复用其已装 skill）。

## 快速开始

```
首次：双击 install.bat      # 建独立 .venv + 装 fastapi / uvicorn / openpyxl
日常：双击 start.bat        # 启动并自动打开 http://127.0.0.1:8789
```

## 端口

- **自启 / 快捷方式 / `start.bat` 统一用 8789**：`wb_start.ps1` 默认 8789，并在启动子进程前设置 `WB_PORT`（`_boot2.py` 不接收 `--port`，只能靠环境变量）；已在监听时幂等复用，不会起第二个实例。
- `app.py` 的默认端口仍是 **8787**（直接 `python app.py`、或 `_boot.py` 不带 `--port` 时）；`daily_batch.py` 与桌面入口 `_launch.py` 会优先扫 8789。
- 口播保存的 Origin 只认本机回环地址（`127.0.0.1` / `localhost`，**不限端口**）。非本机 Origin 的真实 HTTP 响应是 403「拒绝跨站或非本机写入」（全局中间件先拒）；`reverse_voice.py` 函数级的文案是「仅允许本机工作台保存口播信息」。

命令行方式：

```powershell
cd D:\workbuddy_project\tiktok\webapp
.\.venv\Scripts\python.exe app.py
```

## 面板（当前 7 个）

| 面板 | 作用 |
|---|---|
| 总览看板 | **台账表格总览**：`WB_LEDGER_DIR` 目录内全部 xlsx 的 sheet（行数 / 列数 / 记录日期范围），点击卡片直接看表；下方为**流水线状态**（每账号「音频 → 抓取 → 转写 → 台账」漏斗对账 + 落地率 + 缺口告警，异常账号优先排序，无产物的账号归入「未启用」）与最近运行 |
| 一键运行 | 选账号/日期/步骤，后台跑 `pipeline.py`；实时日志（SSE）；可终止 |
| 对标分析 | 账号/视频/爆款规律/挂车商品/可借鉴点，数据源＝本地抓取产物 + 台账 |
| 提示词反推 | TikTok 链接 → 分镜/参考图/Seedance 提示词包 |
| 账号管理 | accounts.json 的增删改查 + 分组/大类 + 媒体类型 |
| 提示词优化 | 对标提示词 → 换成我的产品 → 生成优化版与多版创意（旧版可用 `?old=1` 打开） |
| AI 接入 | 模型清单、API Key、默认/回退链、调用记录与测试连接 |

## 编排分工

流水线 9 步中，本地已接适配层、可在工作台直跑的是前 4 级：

```
s0_check → s1_download → s2_scrape → s5_transcribe     ← 工作台一键跑（pipeline.py）
s3_4_sheet / s6_translate / s7_backfill / s9_report    ← 转交 WorkBuddy Agent（skill）
                          ↕
s8_gmv（fastmoss_gmv.py，本地可跑；未命中项需 Agent 侧带登录态补查）
```

## 目录结构

```
webapp/
├─ app.py            FastAPI 后端：全部 API + 静态托管
├─ config.py         路径探测与常量（环境变量可覆盖）
├─ runner.py         子进程任务管理 + SSE 实时日志
├─ store.py          运行记录持久化（data/runs.jsonl）
├─ scheduler.py      每日定时调度
├─ static/           前端（index.html / style.css / app.js）
├─ data/             运行记录、定时配置、转交指令
├─ install.bat / start.bat
└─ requirements.txt
```

## 可覆盖配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WB_PIPELINE_DIR` | `D:/workbuddy_project/tiktok/pipeline` | pipeline 目录 |
| `WB_PYTHON` | `<pipeline>/.venv/Scripts/python.exe` | 跑流水线的解释器 |
| `WB_HOME` | `C:/Users/Administrator/.workbuddy` | WorkBuddy 用户目录（读 skills） |
| `WB_PROXY` | 自动探测 | TikTok 抓取代理（不设时：系统代理 → 常见本地端口 7892/7897/10809…） |
| `WB_LEDGER_DIR` | `D:/1/对标账号` | 台账表格目录，**总览面板的唯一数据源**（自动扫描目录内全部 xlsx） |
| `WB_PORT` | 8787（`app.py` 直接运行时） | 监听端口；自启 / `wb_start.ps1` / `start.bat` 会设成 8789 |
| `WB_RUNS_FILE` | `<webapp>/data/runs.jsonl` | 运行记录文件；测试 / QA 用它隔离生产 |

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/overview` | 总览聚合（账号/门禁/运行） |
| POST | `/api/run` | 启动运行（`kind`=pipeline / daily / script） |
| GET | `/api/runs/{id}/stream` | SSE 实时日志 |
| POST | `/api/runs/{id}/stop` | 终止运行 |
| GET | `/api/ledger` · `/api/ledger/sheet` | 台账 sheet 列表与数据 |
| POST | `/api/ledger/open` | 唤起本机 Excel 打开台账并定位到指定 sheet |
| POST | `/api/handoff` | 生成 Agent 侧待执行指令（总览「自动化流程 · 指令」用） |

## 测试

```powershell
# 后端单测：逐文件跑（Python 3.13 的 unittest discover 收不到 *.test.py，不要再写 -p "*.test.py"）
cd D:\workbuddy_project\tiktok\webapp
$env:PYTHONIOENCODING = "utf-8"     # stdout 走管道/文件时，不设的话 ✓/✗ 会 UnicodeEncodeError（假红）
$env:WB_RUNS_FILE = "$env:TEMP\wb-test-runs.jsonl"
Get-ChildItem tests\*.test.py | ForEach-Object { & .venv\Scripts\python.exe $_.FullName }

# QA 独立用例（qa/<task-id>/；本机没有 bash，所以用 Python runner）
.venv\Scripts\python.exe qa\run-all.py
```

当前已知红：`accounts_build` / `image_ledger` / `lease_owner`（测试桩过期或数据问题，不是产品缺陷；原因见 2026-11-01 全功能体检报告 §3，已知缺口见 `qa/gaps.md`）。

## 已知事项

- **英文账号转写**必须 `--lang en` + `--backend openai`（本机 AVX-512 平台 ctranslate2 会崩）。
- **爆品监控**（UI 已下线）仍需 `pipeline/cc_cookie.txt`；该 cookie 已失效且带上反而触发 WAF。
- **s3_4 / s6 / s7 / s9** 未接本地适配层，走「自动化流程」表里的「指令」按钮生成交接 md。
- **台账表头契约（改格式前务必看）**：`记录日期/对标账号/视频发布时间/视频标题/视频文案(含话题标签)/
  视频链接/ID/视频出单量/播放量/点赞数/评论数/转发数/收藏数/备注/备注中文翻译/风格标签`。
  - **列序与表头名不能改**：`generate_benchmark_report.py` 有 `assert header == COLS`，要求表头与
    现行 15 列逐位一致。列宽/字体/颜色/边框/行高/条件格式/冻结窗格随便改。
    （`daily_update.py` 自 2026-10-09 起已改为按表头定位「视频链接/ID」列，列序微调不会再让它读错列；
    但报表脚本仍会因列序变化直接 assert 失败。）
  - **sheet 名必须保持 `对标记录-{account}`**，否则日更写不进台账、工作台卡片也点不开表。
  - G 列表头「视频出单量」实际带前导空格，pipeline 与工作台都靠 strip 兼容。
- **工作台会自动做列位自检**：总览卡片上出现红色告警说明台账列位与 pipeline 读取方式不符，
  改格式后先看这里，不用等跑一遍日更才发现。
- **pip 装不上的情况**：宿主 IDE 会注入 `PYTHONPATH=<shim>`，其 `sitecustomize` 会中断批量安装。
  解决：`set PYTHONPATH=` 后重试，或用 `python -E -m pip ...`；镜像源不通时改用官方源
  `-i https://pypi.org/simple`。
