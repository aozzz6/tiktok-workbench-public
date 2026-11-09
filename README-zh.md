[English](README.md) | 中文

# TikTok 对标工作台

一台机器上跑的本地工作台：跟踪一批 TikTok 对标账号，采集它们的视频、转写口播、把数据写进 Excel 台账，
再在 `http://127.0.0.1:8789` 上提供一个看板来读这些数据。

这是一个**本地单机工具**：没有服务端、没有登录，采到的数据留在这台机器上。但它**不是断网的**——
除了向 TikTok 取页面，它还可能会连你配置的外部服务：

| 什么会出去 | 发到哪里 | 什么时候 |
|---|---|---|
| 账号页、视频页 | `www.tiktok.com`（走代理） | 抓取，总是 |
| 待翻译的转写文本 | `~/.workbuddy/models.json` 里配置的 LLM 接口 —— 实际是远程 HTTPS API（`workbuddy.tokenmao.cn` 或 `api.deepseek.com`） | 翻译时；`webapp/config.py` 里的 `WB_AUTO_TRANSLATE` **默认是开的** |
| 短文本片段 | `translate.googleapis.com` | 走 `translate_pending.py` 那条路 |
| 商品 / 爆品查询 | TikTok 广告中心（`ads.tiktok.com`）及第三方站点 | 只有跑那些商品脚本时才发 |

也就是说：光把工作台开着不会往外发任何东西 —— **除非你打开看板自带的每日定时**
（`webapp/data/schedule.json` 的 `enabled: true`），那它到点会自己起一轮日更，抓取与翻译都会外发。

> 说法对齐：本文把这个 `webapp/` 服务叫**看板**。仓库里其它文档叫它**工作台**（`start.bat` 打印
> 「工作台启动中」，流水线日志报「工作台不可达」）。是同一个东西。

## 目录里有什么

| 路径 | 是什么 |
|---|---|
| `webapp/` | 看板：FastAPI + 原生 JavaScript，端口 **8789**。启动脚本（`start.bat`、`wb_start.ps1`）会把 `WB_PORT=8789` 写进去；如果你自己直接跑 `python app.py`，拿到的是 `app.py` 自己的默认值 **8787**。只读下面的数据文件，自己不做采集。 |
| `pipeline/` | 采集流水线：抓取 → 下载音频 → 转写 → 翻译 → 写台账。都是普通 Python 脚本，多数能单独跑；例外是 `daily_batch.py`，见下文。 |
| `pipeline/output/` | 采集产物：`scrape_<账号>.json`、`<账号>_audio/`、转写文本。不进 git。 |
| `qa/` | 本次改动的验收用例（`qa/T-*`），加上总跑脚本 `qa/run-all.ps1` 与缺口清单 `qa/gaps.md`。 |
| `reverse/` | 逆向分析的临时空间。**不进 git** —— 第一次启动时由程序创建。 |

**Excel 台账是对标分析的唯一真相源**（14 列，一个账号一个 sheet）。它的路径是
`pipeline/accounts.json` 的 `xlsx` 字段；没配或指向的文件不存在时，看板会回退到 `LEDGER_DIR`
（`webapp/config.py:42`，默认 `<包>\ledger`）里最新改动的那个 `.xlsx`。`start.bat` 只在存在
`D:\1\对标账号` 时才覆盖这个默认值（那是作者自己的目录）；要放到别处，就设 `WB_LEDGER_DIR`。

**台账文件必须放在 `LEDGER_DIR` 里面。** `config.ledger_path()` 只在那里找 —— 放在别处的 `.xlsx`
会被**静默**忽略、当成没有台账。要把台账放到别的目录，就把 `WB_LEDGER_DIR` 指过去。

## 环境要求

- Windows，**Python 3.13**。
- 两个虚拟环境：`pipeline\.venv` 与 `webapp\.venv`。
- `ffmpeg` 在 `PATH` 上（音频转码用）。
- 抓取需要一个 HTTP 代理：启动时自动探测（`WB_PROXY` → Windows 系统代理 → 常见本地端口 7897 / 7892 / 10809 等）；要固定某一个就设 `WB_PROXY`。
- **没有 pytest**。测试是普通脚本，用退出码 0 / 1 表达通过与否 —— 见下文。

## 安装

**如果 `webapp\.venv` 已经存在且能用，跳过这一节** —— 写这份 README 的那台机器上就是能用的。
这一节需要能访问 PyPI（失败会退清华镜像），而且它会把 `webapp\.venv` 从零重建一遍，
所以不是「随便看看」时该跑的东西。

```bat
cd webapp
install.bat
```

`install.bat` 会拿 `pipeline\.venv` 的解释器**新建一个干净的** `webapp\.venv`（不继承那边的包），
然后只装 `webapp\requirements.txt` 里定死的那些（`fastapi`、`uvicorn`、`openpyxl`）。

**流水线环境现在有 pin 文件了：`pipeline\requirements.txt`。** 它只列流水线代码直接用的包，
版本钉的是作者这台机器 `pipeline\.venv` 里真实的装法。用那个解释器装：

```bat
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
```

三点先说清，免得踩坑：

- 这套是 **Windows + Python 3.13 的 CPU 轮子**（`torch==2.7.0+cpu` 是 PyTorch 索引里的纯 CPU
  构建）。**Linux / macOS 完全没验证过。**
- `scrapling` **不再是依赖，也不要再装它**。抓取层现在就一层：`curl_cffi` 的 chrome124 指纹 +
  本地代理（`pipeline\scripts\tiktok_fetch.py`）。2026-11-04 之前这里写着「scrapling 优先、
  curl_cffi 兜底」，但 scrapling 从来没装过、那层从未生效，已经删掉；它底层同样走 curl_cffi，
  装了买不到新能力。
- `playwright` **不在**清单里。只有浏览器兜底（`pw_detail_fallback.py` / `pw_uid_fallback.py`）
  用得上它，而且要装在**系统 Python** 里，不在 `pipeline\.venv` —— 装法与验证见下面「浏览器兜底」。

`torch==2.7.0+cpu` 需要的 PyTorch CPU 索引**写在 `pipeline\requirements.txt` 里**
（顶部一行 `--extra-index-url`），所以上面那条命令不用再带参数。

`pipeline\.venv` 里到底装了什么，最完整的清单仍然是那边的 `pip freeze`。

## 绿色版（Portable，给没有 Python 的使用者）

`dist\` 下那两个 ZIP 就是给使用者的成品：解压、双击，不用装任何东西，**设计上**也不需要管理员权限
（这个目标**还没在干净机器 / 标准账户上验证过**，见 `qa/gaps.md` #24 与 #26）。
使用者说明书随包带——源文件是 `packaging\templates\使用说明.md`，构建时会复制成包根目录的 `使用说明.md`。

在开发机上一条命令出两个包。两个前置：构建要能找得到 **ffmpeg**；**full** 那一份还需要本机
已经有转写模型的 Hugging Face 缓存（`--hf-cache`，或 `HF_HUB_CACHE` / `HF_HOME` /
`XDG_CACHE_HOME` / `~\.cache\huggingface\hub`）。没有缓存就先只出 `--flavor slim`。

```bat
rem 只需一次：让构建找得到 ffmpeg —— 把 ffmpeg.exe + ffprobe.exe 放进 pipeline\ffmpeg_bin\，
rem 或者设 WB_FFMPEG_DIR，或者给下面这条命令加 --ffmpeg-dir "<ffmpeg folder>"
webapp\.venv\Scripts\python.exe packaging\make_zips.py
```

产物：`dist\tiktok-workbench-<VERSION>-full.zip`（带约 1.5 GB 模型，转写可离线）、
`-slim.zip`（不带模型，首次转写会下载约 1.5 GB）、`SHA256SUMS.txt`、`build-report.json`。
只出小包用 `--flavor slim`。不想重构建、直接拿 `build\portable\` 里现成 stage 打包用 `--skip-build`
——但它缺构建标记时会退出 1，所以至少得真构建过一次。

**开发者检出流程不变：仍然是两个 venv + `install.bat`。**
绿色版只是多出来的一份产物——它不替代 `webapp\.venv`、`pipeline\.venv` 和 `webapp\install.bat`，
开发者日常用不到 ZIP。

packaging 目录的测试——一个方面一个脚本，各打印 `通过 N / M`、退出码 0/1。其中有几条要在**真包**上跑，
所以先构建一次 slim：

```bat
webapp\.venv\Scripts\python.exe packaging\build_portable.py --flavor slim --ffmpeg-dir "<ffmpeg folder>"
for %f in (packaging\tests\*.test.py) do @(webapp\.venv\Scripts\python.exe "%f" || exit /b 1)
```

> 包上用例里有几条按 `build\portable\t18` 找包（T-18 那条的约定）：没有就先建一次
> `webapp\.venv\Scripts\python.exe packaging\build_portable.py --flavor slim --out build\portable\t18 --ffmpeg-dir "<ffmpeg>"`。

包里能做什么、不能做什么，照实写：

- **离线可用**：**导入本地视频 + 抽帧**、看台账、对标分析、转写（完整包连模型都随包带着；
  转写走 CPU int8，GPU 路线存在但未验证，见 `qa/gaps.md`）。
- **需要联网 + 你自己的 API key**（在页面「AI 接入 → ＋ 新增模型」里填，页面会自己写
  `workbuddy\models.json`）：AI 翻译、AI 对话、**出反推报告**。反推报告要调用远程的
  「视觉模型」看画面，所以**它不能离线**。
- **粘贴 TikTok 链接在这个版本里不会下载**：包里那个下载组件写死了开发机的 Python 路径，
  普通电脑上跑不起来，所以这个功能没有启用。请自己把视频下载到本地，再导入**本地视频**。
- **采集 TikTok 要你自己的代理**：工作台会复用系统代理，或自动找常见的本地代理端口
  （7892 / 7897 / 10809 …）；要固定一个，就在 `_setenv.local.bat` 里写
  `set WB_PROXY=http://127.0.0.1:7897`（做法见使用者说明书）。

## 启动看板

```bat
cd webapp
start.bat
```

它会打开 `http://127.0.0.1:8789`。健康检查：`http://127.0.0.1:8789/api/health`。
注意 `start.bat` 是**前台窗口**：那个窗口一关，看板就停了。要它常驻，用下面的启动器。

要在后台以**无控制台**方式起、重复执行不重复起、并拿到 PID 与健康报告，用启动器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File webapp\wb_start.ps1
```

注意：本仓库里任何含非 ASCII 字符的 `.ps1` **必须存成带 BOM 的 UTF-8**，否则 Windows PowerShell 5.1
会按 GBK 读，脚本直接坏掉。

## 跑流水线

在仓库根目录下用流水线的解释器：

```bat
set PY=D:\tiktok-workbench\pipeline\.venv\Scripts\python.exe

rem 抓一个账号（全量抓取，会覆盖 scrape_<账号>.json）
%PY% pipeline\scripts\scrape_tiktok_video.py --account <账号> --audio-dir pipeline\output\<账号>_audio --out-dir pipeline\output

rem 只抓指定视频并「合并」进已有文件（保住历史条目）
%PY% pipeline\scripts\scrape_tiktok_video.py --account <账号> --ids <id1>,<id2> --merge --out-dir pipeline\output
```

**`daily_batch.py` 要求看板正在运行。** 它自己不开流水线：它逐账号向看板 `POST /api/run`，等这次运行结束；
连不上看板就 `[fatal] 工作台不可达` 退出。所以先按上一节把看板跑起来，再：

```bat
%PY% pipeline\scripts\daily_batch.py
```

两条 2026-11-02 起的行为，值得知道：

- 抓取失败时会把页面给的真实原因**追加**在原话术后面，例如
  `itemStruct 空（已下架/私有） [statusCode 10204 author_status,status_reviewing]`，
  不再只有一句「已下架/私有」。
- 一次抓到 **0 条有效数据**的抓取**不会**覆盖已有的 `scrape_<账号>.json`；终端摘要会打 `未写盘`。

## 跑测试

流水线单元测试 —— 一个方面一个脚本，打印 `通过 N / M`，退出码 0/1：

```powershell
cd D:\tiktok-workbench
# 用例里有 ✓ / ✗ / ⚠：不设这一行，默认 GBK 控制台会 UnicodeEncodeError，把用例误判成失败
$env:PYTHONIOENCODING = "utf-8"
Get-ChildItem pipeline\tests\test_*.py | ForEach-Object {
    & .\pipeline\.venv\Scripts\python.exe $_.FullName
}
```

例外：`test_acceptance_regressions.py` 是 stdlib `unittest` 模块，打印的是 `Ran N tests` / `OK`，
不是 `通过 N / M`；退出码仍是 0/1。

看板测试 —— Python 与 Node 并存。**这里有两件事容易搞错，搞错了会得到一片假失败：**

- **解释器要用 `webapp\.venv`。** 看板用例 `import app`，而 `fastapi` 只装在那一边；
  用 `pipeline\.venv` 跑会整片 `ModuleNotFoundError: No module named 'fastapi'`。
- **工作目录要在 `webapp\`。** Node 用例按相对路径读 `static/index.html`，
  在 `webapp\tests` 下跑会 `ENOENT: no such file or directory`。

Node 用例依赖 `jsdom`，它已经写在 `webapp\package.json` 的 `devDependencies` 里：

```powershell
cd webapp
npm install                                                       # 首次，装 jsdom
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tests\accounts_write.test.py   # 任意 *.test.py
node tests\overview_ui.test.js                             # 任意 *.test.js
```

> 注意：部分历史用例（`bench_*.test.js`、`accounts_admin_ui.test.js`、`sheet_view.test.js` 等）
> 针对的是对标页重写前的旧 DOM，当前本来就是红的，与上面这条命令无关 —— 它们需要重写，不是环境问题。
> 2026-11-05 这次三处修复的回归用例是 `tests\bench_empty_data.test.js`、`tests\llm_endpoint_url.test.py` 与 `tests\repo_paths.test.py`。

本次改动的验收用例，走总跑脚本（**在仓库根目录下跑**，下面这个相对路径是相对仓库根的）：

```powershell
cd D:\tiktok-workbench
powershell -NoProfile -ExecutionPolicy Bypass -File qa\run-all.ps1
```

总跑脚本会遍历 `qa\` 下每个目录，按 `run.ps1` / `check.ps1` / `run.py` / `run.sh` 的顺序找入口并执行。
某个目录没有入口文件时会报 `[WARN] … 不算通过`，整轮以非 0 退出 —— 「没有用例」永远不算通过。

> **基线说明：** `qa\run-all.ps1` 在本仓库恒红（`qa\T-04` 钉了不在历史里的提交、`publish_artifacts` 7/13，见 `qa\gaps.md` #21），所以本仓库不拿 run-all 全绿当基线 —— 请按你要改的部分跑对应的 `qa\T-*` 用例。

## 有意不进 git 的东西

运行时数据、环境与本地二进制是有意排除的：`.venv/`、`node_modules/`、`pipeline/output/`、
`webapp/data/`、`pipeline/accounts.json`（真实账号清单）、`pipeline/pipeline_state.json`、
`pipeline/gate_report.json`、`runs*.jsonl`、音频、`*.xlsx`、`*.zip`、`pipeline/ffmpeg_bin/`，
以及所有 `_bak_*` / `_backup_*` / `改前备份_*` 目录。

**会话 cookie 绝对不许提交。** `.gitignore` 覆盖 cookie 文件，包括 `*cookie*.txt` 这个模式
—— 所以 `cc_cookie.txt` 这种名字也在内 —— 提交前请先 `git ls-files` 看一眼，
**哪个模式都不匹配**的 cookie 文件照样会被收进去。
（本仓库的历史里已经有一个这样的文件被跟踪了 —— 见 `qa/gaps.md` 第 15 条。不要再加第二个。）

因为 `pipeline/accounts.json` 不在版本库里，**它没法用 git 回滚**。改它之前先复制一份 ——
项目习惯是在**仓库根下**建 `_bak_<主题>_<日期>/` 目录（`.gitignore` 已经排除了 `_bak_*/`）。

## 新机器上第一次跑

有两样东西不会在仓库里，因为它们是有意不进 git 的：

1. `pipeline/accounts.json` —— 账号清单。最小形状：

   ```json
   {"accounts": [{"account": "<账号名>", "lang": "en", "active": true}],
    "xlsx": "<包>/ledger/<你的台账>.xlsx"}
   ```

   这个 `.xlsx` **必须落在 `LEDGER_DIR` 里**（默认 `<包>\ledger`）—— 见上一节那段说明。
   也可以用 `webapp/config.py` 的 `add_account()`，或在界面上点「＋ 新增账号」。
2. Excel 台账本身，放在 `LEDGER_DIR` 里（或 `WB_LEDGER_DIR` 指过去的那个目录）。

## 账号语言与面向地区

每个账号都有 `lang` 和可选的 `region`（在 `pipeline/accounts.json` 或看板的账号表单里设置）。
`lang` 决定两件事：whisper 按什么语言转写，以及翻译时假定的源语言。

- **`lang: "auto"`** —— 让 whisper 逐条自动识别语种，结果写在
  `pipeline/output/<账号>_audio/transcripts/_detected_langs.json`，可以先看它猜的语言，再固化成具体值。
- **`region`（面向地区）** —— `config.REGION_LANG_MAP` 会按市场给默认语言
  （`us/uk/eu → en`、`de → de`、`br → pt`、`jp → jp`、`kr → kr` 等）；显式填的 `lang` 永远优先于地区推断。
- 目前支持 33 个语言代码（en、de、fr、es、it、pt、nl、pl、sv、no、da、fi、ru、uk、tr、el、cs、ro、ar、he、fa、hi、ur、bn、id、ms、tl、th、vi、zh、jp、kr、auto）
  和 51 个地区键；不够用时在 `REGION_LANG_MAP` / `SUPPORTED_LANGS` 里加一对即可。

## 在你自己机器上用它

本仓库是按**最小可用版**公开的参考实现，不是开箱即用的产品。它当初只在一台 Windows 机器上
跑过，有些地方至今还写着那台机器的路径。这一节就是「换到你的机器上要动什么」的诚实清单。

### 能跑的和不能跑的

- **看板**（`webapp/`）换机器能跑 —— 但要先把下面那些环境变量设好：它用的每个路径都来自
  它们，而它们的内置缺省值就是作者的路径。
- **流水线**（`pipeline/`）**不能**直接跑：主链路有 3 行写死了作者的目录（列在下面），
  `pipeline/scripts/` 下还有十余个辅助脚本也写死了同样的路径。
- **抓取**还要自备代理、自备 TikTok 会话 —— 见本节最后。

### 环境要求

- Windows + **Python 3.13**。
- `ffmpeg` 在 `PATH` 上（音频转码用）。
- 没有 `pytest`：测试是普通脚本，用退出码 0 / 1 表达结果。

需要两个虚拟环境，而**仓库里没有任何命令会替你建流水线那个** ——
`webapp\install.bat` 只建 `webapp\.venv`。所以先建流水线环境，再装依赖：

```bat
py -3.13 -m venv pipeline\.venv
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
```

如果你的机器上 `py -3.13` 没登记（用 `py -0p` 查一下），就把同一条命令换成你 Python 3.13
的完整路径 —— 官方安装包默认放在
`%LOCALAPPDATA%\Programs\Python\Python313\python.exe`。

`webapp\.venv` 由 `install.bat` 建 —— 见上面的「安装」一节。

### 七个环境变量

`webapp/config.py` 读 `WB_PIPELINE_DIR`（`:20`）、`WB_PYTHON`（`:21`）、
`WB_HOME`（`:22`）、`WB_PROXY`（`:40`）、`WB_LEDGER_DIR`（`:42`）；
`webapp/tk_reverse.py` 读 `WB_REVERSE_DIR`（`:47`）和 `WB_YTDLP`（`:50-52`）。
起看板前在命令行（或 `start.bat` 里）设好。

| 变量 | 指向什么 |
|---|---|
| `WB_PIPELINE_DIR` | 你的 `pipeline` 目录（缺省是作者的） |
| `WB_PYTHON` | 跑流水线的解释器（缺省是 `WB_PIPELINE_DIR` 下的 `pipeline\.venv`） |
| `WB_HOME` | 你的 WorkBuddy 用户目录（缺省是作者的 `.workbuddy`） |
| `WB_LEDGER_DIR` | 放你 Excel 台账的那个目录（缺省 `<包>\ledger`；`start.bat` 只在存在 `D:\1\对标账号` 时覆盖） |
| `WB_PROXY` | 看板和抓取共用的代理覆盖项；不设时两边都自动探测（系统代理 → 常见本地端口） |
| `WB_REVERSE_DIR` | 逆向分析的临时目录（缺省作者的 `D:/workbuddy_project/tiktok/reverse`） |
| `WB_YTDLP` | 那个视图用的 `yt-dlp.exe`（缺省作者的 WorkBuddy 二进制目录） |

**`WB_REVERSE_DIR` 要当心：没有 `D:` 盘的机器上，看板根本起不来。**
`webapp/tk_reverse.py:47-48` 会解析这个路径**并且在模块被导入时就把它建出来**，而
`webapp/app.py:43` 在启动过程中就 import 了这个模块 —— 没有 `D:` 盘又没设
`WB_REVERSE_DIR`，启动在第一个请求之前就挂了。把它指到你自己一个可写目录。

### 主链路上 3 处写死路径

这三处都是作者磁盘上的路径。改路径，键名不要动：

1. `pipeline/pipeline.py:39` —— `"python"`：跑流水线每一步的解释器。
2. `pipeline/pipeline.py:46` —— `"output_root"`：抓取产物写到哪里。
3. `pipeline/daily_update.py:45` —— `PY`：同一个解释器，日更流程用。

这 3 处只是**主链路**。`pipeline/scripts/` 下还有十余个辅助 / 历史脚本同样写死了这些路径
—— 例如 `build_13col_sheets.py:17,22`、`migrate_translations.py:13-15`、
`generate_benchmark_report.py:24`、`audit_ledger.py:75`。它们不在主链路上：
真要用某个脚本时，再改那一个。

**本 README 里的命令也不能照抄。** `set PY=...` 和两处 `cd D:\tiktok-workbench`
（都在「跑测试」一节里）都带着作者的路径，要换成你自己的。

### 抓取要自备两样东西

- **代理。** 现在不再写死端口：`pipeline/scripts/proxy_detect.py` 在启动时解析——
  先看 `WB_PROXY`，再看 Windows 系统代理，再探测常见本地端口（7892 自由猫、7897 Clash、
  7890、10809/10808 v2rayN、1080 等），都没有才兜底 `http://127.0.0.1:7897`。
  探测错了就设 `WB_PROXY`。
- **你自己的 TikTok 会话 cookie**，存成 `pipeline/cc_cookie.txt`。

**cookie 文件绝不能提交。** 那是一份活的登录态。`.gitignore` 覆盖 cookie 文件，包括
`*cookie*.txt` 这个模式 —— 所以 `cc_cookie.txt` 这种名字也在内 —— 但提交前还是要跑一下
`git status`（或 `git ls-files`）看一眼：**哪个模式都不匹配**的 cookie 文件照样会被收进去。
还有一个前提：忽略规则**不会**把已经被 git 跟踪的文件移出版本库 —— 那种要
`git rm --cached <文件>`。

### 浏览器兜底（过 WAF 挑战页）

TikTok 偶尔会让详情页 SSR 恒返 WAF 挑战页，这时纯 HTTP 指纹（含 curl_cffi chrome124）无效，
只有真浏览器执行 JS 才能拿到内嵌数据。`pw_detail_fallback.py` 与 `pw_uid_fallback.py`
就是这层兜底，必须由**装了 `playwright` 的系统 Python** 跑，不是流水线的 venv：

```powershell
py -3 -m pip install "playwright==1.64.1"
# 浏览器内核复用本机已有的 chromium-1248（%LOCALAPPDATA%\ms-playwright），
# 脚本里的 pick_browsers_dir() 会自动定位；机器上没有内核时才需要：
py -3 -m playwright install chromium
```

解释器不用手填：`tiktok_fetch.find_playwright_python()` 会逐个**实跑** `import playwright`
验证（不再靠 `shutil.which`，本机那些名字全是商店占位符），也可以用 `WB_PW_PYTHON`
显式指定。找不到就跳过，抓取脚本会打一行
`[warn] 浏览器兜底未找到可用 Python（…），跳过`，其余流程不受影响。

代理统一由 `WB_PROXY` 或 `proxy_detect.detect_proxy()` 决定——不要在兜底脚本里写死端口。

### 从示例账号清单开始

```bat
copy pipeline\accounts.example.json pipeline\accounts.json
```

然后改里面的账号名、两个目录、`xlsx` 路径，以及 `metrics_file` / `reports_root` 这两条。
示例里的账号名都是明显的假名；`pipeline/accounts.json` 本身不进 git —— 在你机器上，
那份文件是你自己的。

关于这个文件还有两件事：

- **`xlsx` 必须落在 `LEDGER_DIR` 里面**（或者把 `WB_LEDGER_DIR` 指到它所在的目录）。
  `webapp/config.py` 只在那里找 —— 放在别处的台账会被**静默**当成「没有台账」，不报错。
- **`metrics_file` 和 `reports_root` 是必填，不是可选。** `pipeline/daily_update.py` 用
  `cfg[...]` 硬下标读它们（第 `:288`、`:299`、`:726` 行），缺一个就是启动即 `KeyError`。
## 继续读

- `DEPLOY_QUICKSTART.md` — 新机部署 5 步走（含 `first_run.py` 自检脚本）。
- `DEPLOY_CHECKLIST.md` —— **部署自检清单**：装完先跑它的一分钟自检，能提前挡掉大部分坑
  （解释器、代理端口、台账位置、测试基线、WAF 兜底都在里面）。
- `DEPLOY_GUIDE.md` —— 把「只有看板」的副本搬到另一台机器（端口 8789、14 列台账、台账在 `ledger\`）。
  终端使用者请直接用上面的**绿色版** ZIP。
- `qa/gaps.md` —— 本次改动的测试**没能**验证什么、为什么。
- **许可（重要）。** **仓库源码**是 **MIT**；`LICENSE` 里的版权行是 `Copyright (c) 2026 aozzz6`
  —— 原样分发就保留它，只有你 fork 之后改了代码，才换成你自己的名字。
  **发布出来的 ZIP 不只有 MIT**：包里带着 **GPLv3** 的 **ffmpeg**（以及 **PyAV** 里那套 FFmpeg），
  分发 ZIP 就要随附 GPLv3 全文和源码获取方式 —— 见 `THIRD_PARTY_NOTICES.md`。
