# 新机部署 · 5 步走

> 这是一个「本地单机工具」：数据都留在本机，但**抓取要能连 TikTok**（需要你自己的代理），**转写要模型**，**翻译要 LLM key**。
> 按下面 5 步走完，就能打开看板、加账号、跑采集。

## 0. 前提

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11（仓库只在 Windows 上验证过） |
| Python | **3.13**（`py -3.13 -V` 能打印版本） |
| 代理 | 本机能连 TikTok：Clash / 自由猫 / v2rayN 都行，端口不限（脚本自动探测） |
| ffmpeg | `ffmpeg -version` 能跑（音频提取用） |
| TikTok cookie | 你自己的登录态，最后一步放进去 |
| 翻译（可选） | 一个 OpenAI 兼容的 LLM key，写进 `~/.workbuddy/models.json` |

建议把仓库放在**全英文、无空格**的路径，例如 `D:\tiktok-workbench`。

## 1. 克隆

```bat
git clone https://github.com/aozzz6/tiktok-workbench-public.git D:\tiktok-workbench
cd /d D:\tiktok-workbench
```

## 2. 建两个虚拟环境

```bat
py -3.13 -m venv pipeline\.venv
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt

python -m venv webapp\.venv
webapp\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt
```

> 国内下载慢就加镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`。
> `torch==2.7.0+cpu` 走的是 PyTorch 官方 CPU 源（requirements 里已用 `--extra-index-url` 写好）；
> 官方源连不上时可用交大镜像：`--extra-index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu/`。

## 3. 配代理

先确认代理软件已经启动、能上 TikTok。脚本会**自动探测**：环境变量 `WB_PROXY` → Windows 系统代理 → 常见端口（7892 自由猫 / 7897 Clash / 10809 v2rayN …）。

探测不准时，显式指定（端口换成你自己的）：

```bat
set WB_PROXY=http://127.0.0.1:7892
```

自检：

```bat
curl -x http://127.0.0.1:7892 -s -o NUL -w "%%{http_code}\n" https://www.tiktok.com/
```

## 4. 自检 + 初始化

```bat
webapp\.venv\Scripts\python.exe first_run.py
```

它会检查 Python / 两个 venv / ffmpeg / 代理 / TikTok / whisper 模型，并**只创建缺失的东西**：

- 台账 `ledger\tiktok_对标.xlsx`（14 列表头 + 模板 sheet）
- `pipeline\accounts.json`（示例账号默认停用）
- `pipeline\reports\` 目录

看结果：`[FAIL]` 必须解决，`[WARN]` 按提示处理。

## 5. 放 cookie、启动、加账号

1. 把 TikTok 登录 cookie 存成 `pipeline\cc_cookie.txt`（**绝不能提交进 git**）。
2. 启动看板：

```bat
webapp\start.bat
```

   浏览器打开 <http://127.0.0.1:8789>。
   后台方式：`powershell -NoProfile -ExecutionPolicy Bypass -File webapp\wb_start.ps1`

3. 页面「＋ 新增账号」：填账号名、**面向地区**、**转写语言**（不确定就选 `auto` 自动识别）。
4. 跑「一键运行」，或只跑采集；数据会进台账。

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 转写全部 0 段或卡住 | whisper 模型没下下来。设 `HF_ENDPOINT=https://hf-mirror.com` 重试，或把 `medium-ct2` 目录拷来用 `--model-dir` 指定 |
| `Could not locate cudnn_ops64_9.dll` | 有显卡但没 cuDNN。用 CPU 跑（不要装 CUDA 版 torch），脚本会自动判断 |
| 抓取报 `Failed to connect ... proxy` | 代理没启动 / 端口不对。`WB_PROXY` 指定，或确认代理软件已连接节点 |
| 台账读不到 | xlsx 必须在 `WB_LEDGER_DIR` 目录里（看板只扫这个目录） |
| 看板 502 / 打不开 | 端口 8789 被占用；或 `WB_REVERSE_DIR` 指向不可写目录 |
| 翻译没反应 | 翻译要 LLM：`~/.workbuddy/models.json` 里要有可用 key；「翻译回填」按钮只建队列 |
| 中文路径报错 | 换到全英文路径 |

## 语言与地区

- `lang` 支持 33 种语言 + `auto`（whisper 自动识别，结果写 `transcripts/_detected_langs.json`）。
- `region` 会自动带出语言（`de→de`、`br→pt`、`jp→jp`、`kr→kr`…），显式 `lang` 优先。
- 批量导入（CSV/JSON）每行可以带 `region` / `lang`，多国家账号一次导入。
