# TikTok 对标工作台 · 部署自检清单

> 适用：Windows 10/11，从零把这台工作台跑起来。
> 来源：2026-11-04 在一台真实机器上做全功能检测时，**逐条踩出来**的坑。
> 分工：`README-zh.md` 讲「它是什么、怎么装」；**本文讲「装完怎么确认它真的能跑」**。

本文每一条都是「照着 README 做了，结果失败」的那种问题。顺序照做，每步都有「怎么确认成功」。

---

## 0. 全局流程

```
装依赖 → 配环境变量 → 跑自检 → 配三样东西（账号清单 / 台账 / 模型） → 开看板 → 抓一条视频验证
```

先别急着点页面。**第 1 步的自检能省掉你后面 90% 的困惑。**

---

## 1. 一分钟自检

把下面的脚本存成 `check.ps1`，**改成你的仓库路径**，然后
`powershell -NoProfile -ExecutionPolicy Bypass -File check.ps1`。

> ⚠️ **必须存成「带 BOM 的 UTF-8」**。本脚本含中文，PowerShell 5.1 会按 GBK 读没有 BOM 的文件，
> 结果是语法崩溃（不是乱码那么温柔）。用 VS Code 右下角选 `UTF-8 with BOM` 再保存。

```powershell
function Test-Item([string]$Name, [bool]$Ok, [string]$Detail = "") {
    if ($Ok) { Write-Host ("[ok]  {0,-24} {1}" -f $Name, $Detail) -ForegroundColor Green }
    else     { Write-Host ("[--]  {0,-24} {1}" -f $Name, $Detail) -ForegroundColor Yellow }
}

$repo = "D:\workbuddy_project\tiktok"      # ← 改成你的实际路径
Push-Location $repo
$pyPipe = "pipeline\.venv\Scripts\python.exe"
$pyWeb  = "webapp\.venv\Scripts\python.exe"

Test-Item "pipeline venv"      (Test-Path $pyPipe) $pyPipe
Test-Item "webapp venv"        (Test-Path $pyWeb)  $pyWeb
if (Test-Path $pyPipe) {
    $v = (& $pyPipe --version 2>&1) -join ""
    Test-Item "pipeline Python"  ($v -match '3\.13') $v
}
Test-Item "ffmpeg on PATH"     ([bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)) "音频转码"
Test-Item "node"               ([bool](Get-Command node -ErrorAction SilentlyContinue)) "JS 测试用"
Test-Item "jsdom 已安装"       (Test-Path "webapp\node_modules\jsdom") "node tests 必需"
Test-Item "accounts.json"      (Test-Path "pipeline\accounts.json") "账号清单"
$cfg = Get-Content "pipeline\accounts.json" -Raw -Encoding UTF8 -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($cfg) {
    Test-Item "台账 xlsx"      (Test-Path $cfg.xlsx) "$($cfg.xlsx)"
}
Test-Item "cc_cookie.txt"      (Test-Path "pipeline\cc_cookie.txt") "只有爆品监控需要，缺了不影响抓取"

try {
    $h = (Invoke-WebRequest "http://127.0.0.1:8789/api/health" -UseBasicParsing -TimeoutSec 5).Content
    Test-Item "看板 8789 在跑"  $true "api_version=$((($h | ConvertFrom-Json).api_version))"
    $proxy = ($h | ConvertFrom-Json).diagnostics.proxy
    Test-Item "配置的代理"      $true "$proxy"
    $port = ([uri]$proxy).Port
    $alive = (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue)
    Test-Item "代理端口可连"    $alive "127.0.0.1:$port"
} catch {
    Test-Item "看板 8789 在跑"  $false "先启动看板再跑本项"
}

Pop-Location
```

**怎么读结果：**

| 输出 | 含义 | 怎么办 |
|---|---|---|
| `[ok]` 全绿 | 环境基本就绪 | 跳到第 4 节 |
| `[--] cc_cookie.txt` | **正常**，只有爆品监控需要它 | 不用管 |
| `[--] 台账 xlsx` | 台账路径不对或文件不在 | 见第 3 节 |
| `[--] jsdom 已安装` | `cd webapp; npm install` | 见第 5 节 |
| `[--] 看板 8789 在跑` | 还没启动 | 见第 4 节 |

---

## 2. 环境变量：该配的 8 个

不配也能跑，但会用「作者那台机器」的路径——这是绝大多数怪问题的来源。

| 变量 | 填什么 | 不填的后果 |
|---|---|---|
| `WB_PIPELINE_DIR` | 你的 `pipeline` 目录 | 看板指向作者的 `D:/workbuddy_project/...` |
| `WB_PYTHON` | 跑流水线的解释器 | 同上 |
| `WB_HOME` | 你的 WorkBuddy 用户目录 | 默认指向 `C:\Users\Administrator\.workbuddy`，**AI 配置永远读不到** |
| `WB_LEDGER_DIR` | 放 Excel 台账的目录 | 默认 `D:/1/对标账号`，台账会被**静默**当成没有 |
| `WB_PROXY` | 你的本地代理，如 `http://127.0.0.1:7892` | 抓取全走 7897，端口不对就全部失败 |
| `WB_REVERSE_DIR` | 反推的临时目录 | 默认在 `D:` 盘；**没有 D 盘的机器会直接起不来** |
| `WB_YTDLP` | `yt-dlp.exe` 的完整路径 | 默认是作者的 WorkBuddy 路径 → 反推「链接下载」必失败 |
| `WB_PW_PYTHON` | 装了 playwright 的解释器（可选） | 不填也行，会自动找；见第 5 节 |

最小的做法：启动看板的批处理里统一 `set` 一遍，不要让每个脚本各配各的。

---

## 3. 踩坑对照表

按「你会看到什么」查，不用从头读。

| 现象 | 真正原因 | 处理 |
|---|---|---|
| `python` 报 `Python was not found; run without arguments to install from the Microsoft Store` | Windows 的「应用执行别名」占位符，文件在但跑不了（退出码 **9009**） | 用 `py -3.13` 或解释器的完整路径，别用裸 `python` |
| `py -3 -c "import sys; print(sys.version)"` 出来是 **3.14** | `py -3` 取的是**默认最高版本**，不是 3.13 | 建 venv 明确写 `py -3.13 -m venv pipeline\.venv` |
| 建好 venv 后装依赖失败、找不到轮子 | 用了 3.14 建 venv | 删掉重建，用 `py -3.13` |
| 看板测试整片 `ModuleNotFoundError: No module named 'fastapi'` | 用了 `pipeline\.venv` 跑看板测试，但 `fastapi` 只装在 `webapp\.venv` | 看板测试用 `webapp\.venv\Scripts\python.exe` |
| Node 测试报 `ENOENT: ... static\index.html` | 工作目录在 `webapp\tests`，而用例按相对路径读文件 | **在 `webapp\` 下**跑：`node tests\xxx.test.js` |
| Node 测试报 `Cannot find module 'jsdom'` | 没装测试依赖 | `cd webapp; npm install`（`jsdom` 已写进 devDependencies） |
| 测试打印 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` | 用例里有 `✓`/`✗`/`⚠`，默认控制台是 GBK | 跑之前 `$env:PYTHONIOENCODING = "utf-8"` |
| 含中文的 `.ps1` 一跑就语法报错 | 存成了不带 BOM 的 UTF-8，PS 5.1 按 GBK 读 | 另存为 **UTF-8 with BOM** |
| PowerShell 里读到中文路径变成 `D:/1/瀵规爣璐﹀彿/...` | `Get-Content` / `Invoke-WebRequest.Content` 默认按 ANSI 解码 | 读文件加 `-Encoding UTF8`；读 HTTP 用 `RawContentStream` + `[Text.Encoding]::UTF8` |
| 看板能开，但台账区是空的 / 一直说「没有台账」 | 台账 `.xlsx` **不在 `LEDGER_DIR` 里**，`config.ledger_path()` 只在那儿找，找不到**不报错** | 把台账放进目录，或 `WB_LEDGER_DIR` 指过去 |
| 反推「输入链接」一直失败 | `WB_YTDLP` 指向不存在的文件 | 设 `WB_YTDLP`，或把 `yt-dlp` 放进 `PATH` |
| `health` 里 `cookie_exists: false` | 正常 | **不影响抓取**，只影响爆品监控 |
| AI 相关全部不可用、「0 个模型」 | 没配模型 | 是配置不是 bug，见第 6 节 |
| 抓详情页偶尔拿到一个很小的页面、没有数据 | TikTok 的 WAF 挑战页 | 见第 5 节「浏览器兜底」 |
| 看板端口不是 8789 | 直接用 `python app.py` 起的是 **8787**；只有 `start.bat` / `wb_start.ps1` 才设 8789 | 用启动脚本，或自己 `set WB_PORT=8789` |
| 拿 `/api/pipeline/retry` 当探活，结果真开始抓数据 | 这个接口**空 body 会按上一轮账号真的起跑** | 别拿它探活；探活只用 `/api/health` 和 `GET` |

---

## 4. 启动看板并验收

```powershell
cd webapp
start.bat
```

期望：

1. 浏览器打开 `http://127.0.0.1:8789`；
2. `http://127.0.0.1:8789/api/health` 返回 `"ok":true`；
3. 左侧 7 个页面都能点开，**浏览器控制台没有红色报错**：
   总览看板 / 对标分析 / 提示词反推 / 提示词优化 / 账号管理 / 一键运行 / AI 接入。

要后台常驻、重复执行不重复起：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File webapp\wb_start.ps1
```

---

## 5. 第一次使用必须配的三样

### 5.1 账号清单 `pipeline/accounts.json`

从示例复制再改：

```powershell
copy pipeline\accounts.example.json pipeline\accounts.json
```

**`metrics_file` 和 `reports_root` 是必填**，不是可选——`daily_update.py` 用硬下标读它们，
缺一个就是启动即 `KeyError`。

### 5.2 台账 `xlsx`

只要记住一条：**台账文件必须落在 `LEDGER_DIR` 里面**（默认 `D:/1/对标账号`）。
放在别处不会报错，但会被当成「没有台账」——这是最容易被误判成 bug 的一条。

### 5.3 模型（只有 AI 功能需要）

在「AI 接入」页填文本模型和视觉模型的地址 / Key / 模型名。
**不配也能用**：采集、转写、建表、回填、统计报告都是本地确定性功能，不依赖模型。
不配时页面会如实显示「AI 不可用」，那不是故障。

### 5.4 浏览器兜底（WAF 挑战页才需要）

TikTok 偶尔会让详情页恒返 WAF 挑战页，这时纯 HTTP 指纹无效，只有真浏览器能过。
兜底脚本需要 `playwright`，而且必须装在**系统 Python** 里（不是 `pipeline\.venv`）：

```powershell
py -3.13 -m pip install "playwright==1.64.1"
# 机器上还没有浏览器内核时，才需要这一步：
py -3.13 -m playwright install chromium
```

**先判断今天到底需不需要它**，别盲目装：

```powershell
$env:WB_PROXY="http://127.0.0.1:7892"
pipeline\.venv\Scripts\python.exe pipeline\scripts\probe_waf.py <账号> <视频ID>
```

- 输出 `OK：拿到 itemStruct` → 现在不需要兜底，curl_cffi 够用；
- 输出 `WAF/挑战页（无内嵌数据）` → 需要兜底，按上面装 playwright。

---

## 6. 跑测试：真实基线（别被一片红吓到）

三个套件、三种跑法，**别混**：

```powershell
# ① 流水线单元测试（用流水线自己的 venv）
cd D:\workbuddy_project\tiktok
$env:PYTHONIOENCODING = "utf-8"
Get-ChildItem pipeline\tests\test_*.py | ForEach-Object {
    & .\pipeline\.venv\Scripts\python.exe $_.FullName
}

# ② 看板测试（用看板自己的 venv，且必须在 webapp\ 下）
cd webapp
$env:PYTHONIOENCODING = "utf-8"
node tests\overview_ui.test.js
.\.venv\Scripts\python.exe tests\accounts_write.test.py

# ③ QA 验收（在仓库根目录）
cd D:\workbuddy_project\tiktok
powershell -NoProfile -ExecutionPolicy Bypass -File qa\run-all.ps1
```

**2026-11-04 在一台干净机器上的真实基线：**

| 套件 | 通过 | 已知红（不是你的环境坏了） |
|---|---|---|
| 流水线单元测试 | **8 / 8** | — |
| 看板 Python 测试 | **50 / 53** | `bench_ledger_merge`、`image_ledger`、`lease_owner` —— 这三条是**陈旧用例**，还在测已经改过的内部函数 |
| 看板 Node 测试 | **19 / 43** | 约 24 条，多为 `bench_*` / `accounts_admin_ui` / `sheet_view` 等，还在断言对标页重写**之前**的旧 DOM |
| QA 验收 | **2 / 3** | `T-04` 依赖 `git show d58d9ed`，而公开仓库是单个提交，那个基线提交不存在 |

判断方法：**比上面这些红得更多的，才需要查你的环境**。

---

## 7. 抓取通道现状（一句话）

抓取层**只有一层**：`curl_cffi` 的 chrome124 指纹 + 本地代理 + 匿名 SSR 内嵌 JSON。

- 音视频下载用 `yt-dlp`（音频 `bestaudio`→mp3；反推原片有 3 套 `-f` 方案）；
- 详情页数据来自 SSR 里的 `__UNIVERSAL_DATA_FOR_REHYDRATION__`，**不调签名 API、不需要登录 cookie**；
- `scrapling` **不是依赖，也不用装**——它底层同样走 curl_cffi，装了买不到新能力。

---

## 8. 已知缺陷（截至 2026-11-04，尚未修）

| 缺陷 | 影响 | 规避 |
|---|---|---|
| `POST /api/pipeline/retry` 空 body 会真的起跑（会抓平台、写产物） | 误触会启动真实任务 | 别拿它当探活；前端按钮才用 |
| `favicon.ico` 返回 404 | 控制台多一条红字，无功能影响 | 忽略 |
| 约 24 条前端用例 + 3 条 Python 用例陈旧 | 测试不能当验收依据 | 见第 6 节基线 |
| `qa/T-04` 依赖不存在的提交 | `qa\run-all.ps1` 整体非 0 | 忽略 T-04，或把基线改成 fixture |
| 反推「链接下载原片」依赖 `WB_YTDLP` | 不配就失败 | 第 2 节 |

---

## 9. 换机器时最容易忘的三件事

1. **代理端口**：脚本全部从 `WB_PROXY` 读，但**你的代理软件自己要真的开在那个端口**。
2. **两个 venv 都是 Python 3.13**，不是系统默认版本。`py -3` 给的可能是 3.14。
3. **台账和账号清单不进 git**，必须自己带。`pipeline/accounts.json` 没法用 git 回滚，改之前先复制一份。

---

## 10. 更新代码：直连 GitHub 可能不通

`git pull` / `git push` 报这个错，**不是仓库坏了，是网络到不了 GitHub**：

```
fatal: unable to access 'https://github.com/...': Failed to connect to github.com
port 443 after 21066 ms: Could not connect to server
```

先用 `Test-NetConnection` 确认，再决定要不要走代理（**别一上来就怀疑权限**）：

```powershell
Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
```

不通就走本地代理。下面这条是**一次性**的，不改你的 git 配置，最安全：

```powershell
git -c http.proxy=http://127.0.0.1:7892 push origin main
```

> 端口填**你自己**的代理端口（本文档其它地方用的是 `WB_PROXY`，本机是 7892）。

如果每次都要加太麻烦，可以设成长期配置——但它会作用于**这台机器上所有仓库**，设之前想清楚：

```powershell
git config --global http.proxy http://127.0.0.1:7892     # 设
git config --global --unset http.proxy                   # 想撤的时候
```

两种常见误判，先说在前面：

| 你可能以为 | 实际 |
|---|---|
| 「没权限 / token 过期了」 | 报错写的是 `Could not connect`，那是**连接**问题；权限问题会写 `403` / `Authentication failed` |
| 「`gh` 已登录就一定能推」 | `gh` 的登录状态和 `git push` 用的传输通道是两件事，网络不通照样失败 |
