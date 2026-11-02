# TikTok 对标工作台 · 新机部署指南（仅 webapp 展示版）

> 源机基线：Windows + Python 3.13
> **模式说明：新机不部署 pipeline 流水线（不跑采集/转写），只部署 webapp 展示层查看已有数据。**
> 本指南 = 架构说明 + 搬迁清单 + 逐步部署 + 验证清单 + 已知坑

---

## 一、架构总览（搬的是什么）

```
已在源机完成、随包带走的数据：
  TikTok ──s1 音频/s2 抓取──> output/scrape_*.json + *_audio/*.mp3
  s5 转写 / s6 翻译 ──> 台账 xlsx（15 列）

新机只做最后一环：
  output/*.json + 台账 xlsx ──> webapp :8787（总览看板 + 对标分析 + 快速新增账号）
  （采集/转写/一键运行在新机不可用——无 pipeline）
```

| 组件 | 作用 | 技术 |
|---|---|---|
| webapp/ | 对标工作台 UI + API（总览看板/对标分析/快速新增账号） | FastAPI + 原生 JS |
| pipeline/output/ | 各账号数据快照（抓取 JSON/音频/转写），webapp 的数据源 | 随包只读携带 |
| 台账 xlsx | 15 列规范，sheet=对标记录-{账号} | openpyxl 读写 |
| automation | 每日增量日更（09:00，当前 PAUSED） | WorkBuddy automation（仅源机） |

---

## 二、搬迁清单

### 必搬（~30MB）

| 源 | 内容 |
|---|---|
| `webapp/`（除 .venv、attic） | app.py + static/ + data/（automations.json、schedule.json、runs.jsonl）+ requirements.txt |
| `pipeline/accounts.json` | 账号注册表（webapp 启动必需） |
| `pipeline/output/` | 各账号数据快照（scrape JSON + 音频 + 转写，只读展示用） |
| `D:/1/对标账号/tiktok_对标.xlsx` | **台账（最重要资产）** |
| `D:/1/对标账号/{账号}/{账号}_products.json` | 各账号商品文件 |

### 不搬（新机用不到）

- `pipeline/` 其余全部（脚本/gate/venv/ffmpeg.zip/pipeline_state/uid_cache/reports）
- `mcp_tiktok/`、`~/.cache/whisper/`、automation（都属于流水线/采集侧）

### 打包（源机执行）

```
powershell -ExecutionPolicy Bypass -File D:\workbuddy_project\tiktok\pack_webapp_only.ps1
# 产出 D:\workbuddy_project\tiktok_transfer_webapp\（~30MB），整目录拷走即可
```

---

## 三、新机部署步骤

### 0. 前置条件
- Windows 10/11 + Python 3.13（仅此一项环境依赖）
- 建议把项目放同路径：`D:\workbuddy_project\tiktok` 和 `D:\1\对标账号`

### 1. 还原目录
```
tiktok_transfer_webapp\webapp        -> D:\workbuddy_project\tiktok\webapp
tiktok_transfer_webapp\pipeline_core -> D:\workbuddy_project\tiktok\pipeline
                                        （仅 accounts.json + output 快照）
tiktok_transfer_webapp\ledger\*     -> D:\1\对标账号\
```

### 2. 建 webapp venv（唯一环境）
```bat
cd /d D:\workbuddy_project\tiktok\webapp
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. 路径核对（仅 3 处）

| 文件 | 项 | 说明 |
|---|---|---|
| `webapp/config.py` | `LEDGER_DIR`（台账目录） | 同路径部署免改；否则设环境变量 `WB_LEDGER_DIR` |
| `webapp/config.py` | `WORKBUDDY_HOME` | 同路径免改；否则设 `WB_HOME` |
| `pipeline/accounts.json` | 各账号 `out_dir`/`audio_dir` 绝对路径 | 同路径部署免改 |
| `webapp/config.py` | `PIPELINE_DIR`（pipeline 目录） | 同路径免改；否则设环境变量 `WB_PIPELINE_DIR` |

### 4. 启动与验证
```bat
cd /d D:\workbuddy_project\tiktok\webapp
.venv\Scripts\python app.py        # 服务 127.0.0.1:8787
```
浏览器开 `http://127.0.0.1:8787`，逐项验证：
1. 总览看板：账号列表 = accounts.json 的账号，各指标与源机一致
2. 对标分析：下拉出现 gylwwcxb(27)/gutpickde(5)，KPI 与源机一致
3. 快速新增账号可用（注册后无产物属正常——新机没有采集链）

### 5. automation（不适用于本模式）
- 每日增量日更需要 pipeline，**新机不部署**——数据更新仍在源机完成后重新打包带走
- `webapp/data/schedule.json` 保持 `enabled=false`，勿在新机开启（会尝试调用不存在的脚本）

---

## 四、已知坑（webapp 展示版相关）

| # | 坑 | 处置 |
|---|---|---|
| 1 | **台账被 Excel 占用时读旧数据/写冲突** | 查看时别用 Excel 锁着 xlsx |
| 2 | **本机访问 127.0.0.1:8787 走系统代理 502** | curl 加 `--noproxy '*'`；浏览器一般无此问题 |
| 3 | **新注册账号没有数据**（audio=0、健康态"仅配置"） | 正常——新机没有采集链，注册仅入名单；要数据在源机跑完再打包 |
| 4 | **一键运行页不可用** | 按钮还在但会失败（无 pipeline 脚本），忽略即可 |
| 5 | **数据是快照** | 新机看到的是打包时刻的数据；要更新需回源机跑流水线后重新打包 |

---

## 五、关键文件速查

| 文件 | 作用 |
|---|---|
| `pipeline/accounts.json` | 账号注册表（account/lang/out_dir/audio_dir/active） |
| `pipeline/output/scrape_{acc}.json` | 抓取数据（对标分析数据源，随包只读） |
| `pipeline/output/{acc}_audio/transcripts/` | 转写 txt（随包只读） |
| `webapp/data/automations.json` | 总览页流程卡定义 |
| `webapp/data/runs.jsonl` | 运行历史（启动时自动清僵尸） |
| `D:/1/对标账号/tiktok_对标.xlsx` | 15 列台账（唯一真相源） |
| `D:/1/对标账号/{acc}/{acc}_products.json` | 商品文件（0 商品也要落） |

## 六、测试自检（部署完成后）

核心验证 = 三视图打开 + 数据与源机一致。JS 回归需要 Node（可选）：
```bat
cd /d D:\workbuddy_project\tiktok\webapp
set NODE_PATH=<你的 node_modules 路径>
for %f in (tests\*.test.js) do node %f
```
