# TikTok 对标工作台 · 新机部署指南（仅界面版）

> **本包只含 webapp 界面外壳**：页面、服务端、测试。不含任何数据与采集功能。
> 数据与流水线功能在另一台电脑上按需逐步加载（见「五、后续加功能」）。

---

## 一、这个包里有什么

| 组件 | 说明 |
|---|---|
| webapp/ | FastAPI 服务端（app.py/config.py/runner 等）+ 前端三件套（static/）+ 测试（tests/）+ requirements.txt |
| DEPLOY_GUIDE.md | 本指南 |

**没有的**（需要时再加载，见第五节）：
- `pipeline/accounts.json`（账号注册表）——没有它服务能启动，但总览/对标分析为空
- `pipeline/output/`（各账号数据快照）——对标分析页的数据源
- 台账 xlsx + 商品 JSON
- pipeline 采集/转写脚本、ffmpeg、whisper 模型

---

## 二、部署步骤（5 分钟）

### 0. 前置
- Windows + Python 3.13
- 建议目录布局（与源机一致可免改路径）：`D:\workbuddy_project\tiktok\webapp`、`D:\workbuddy_project\tiktok\pipeline`

### 1. 还原目录
```
tiktok_transfer_ui\webapp -> D:\workbuddy_project\tiktok\webapp
```

### 2. 建 venv 并启动
```bat
cd /d D:\workbuddy_project\tiktok\webapp
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

### 3. 验证
浏览器开 `http://127.0.0.1:8787`：
- 首页/对标分析/一键运行三个视图正常打开
- 总览为空、对标分析无账号 —— **正常**（还没有数据）

---

## 三、路径环境变量（加数据时才需要关心）

`webapp/config.py` 支持环境变量覆盖，默认值如下：

| 变量 | 默认 | 作用 |
|---|---|---|
| `WB_PIPELINE_DIR` | `D:/workbuddy_project/tiktok/pipeline` | accounts.json / 产物所在目录 |
| `WB_LEDGER_DIR` | `D:/1/对标账号` | 台账 xlsx / 商品文件目录 |
| `WB_HOME` | `C:/Users/Administrator/.workbuddy` | WorkBuddy 数据根 |
| `WB_PYTHON` | `{WB_PIPELINE_DIR}/.venv/Scripts/python.exe` | 流水线解释器（跑功能时才需要） |

同路径布局则全部免改。

---

## 四、已知说明

| # | 项 | 说明 |
|---|---|---|
| 1 | 总览/对标分析为空 | 正常——放数据前没有可展示内容 |
| 2 | 一键运行页按钮可用但会失败 | 无 pipeline 脚本与 venv；加功能后再用 |
| 3 | 快速新增账号 | 可用（写 accounts.json），但注册后无数据直到放入采集产物 |
| 4 | 服务启动命令 | `webapp` 目录下 `.venv\Scripts\python app.py` |

---

## 五、后续加功能（按需，逐级加载）

| 级别 | 放入什么 | 得到什么 |
|---|---|---|
| L1 数据展示 | `pipeline/accounts.json` + `pipeline/output/`（数据快照）+ 台账 xlsx + `*_products.json` | 总览看板 + 对标分析完整数据 |
| L2 本地采集/转写 | 完整 pipeline（脚本 + .venv + ffmpeg）+ whisper 模型 + 代理 | 一键运行可用，可持续更新数据 |
| L3 自动化 | WorkBuddy automation 指向本机脚本 | 每日自动更新 |

对应的完整打包脚本（源机上执行）：
- L1：`pack_webapp_only.ps1` → `tiktok_transfer_webapp\`（界面+数据快照，~25MB）
- L2/L3：`pack_for_transfer.ps1` → `tiktok_transfer\`（全量含 venv 线索与模型，~2.2GB）

---

## 六、测试自检（可选）

```bat
cd /d D:\workbuddy_project\tiktok\webapp
set NODE_PATH=<你的 node_modules 路径>
for %f in (tests\*.test.js) do node %f
```
