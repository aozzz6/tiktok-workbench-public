# T-13 工程师报告（crew-engineer-3 原文落盘）

> 落盘原因：T-13 的 DoD 第 5 条要求「报告贴改前红、改后绿两段真实输出」，DoD 2（更正 1）
> 要求逐条列出 12 条命令的结果；而 role 的报告只活在会话消息里，会话结束消息就没了。
> 按 `qa/T-02` 的惯例，PM 要求把工程师报告原文写进本文件。落盘时间 2026-11-05。
> 注意：这是「当时那一轮」的证据快照。**红段用的是任何源码改动之前的代码，事后不能重跑再现**（绿段与 12 条命令可以按各节命令重跑）。
>
> 任务行：`docs/tasks/tasks.md` 的 **T-13**（2026-11-05 追加 6 个文件后）。共享任务板 `task-3` 已 complete（revision 6）。
> 分支：`crew/fix-check-findings`。仓库：`D:\tiktok-workbench`。看板服务 `127.0.0.1:8789` 全程未重启。

## 1. 红：DoD 5 新用例在**任何源码改动之前**的真实输出

先写用例、再改任何源码。命令（`cd D:\tiktok-workbench\webapp`）：

```
.venv\Scripts\python.exe tests\repo_paths.test.py
```

真实输出：

```text
扫描 123 个文件（tests/*.js、tests/*.py、*.bat、*.vbs）
FAIL 发现 34 处硬编码旧仓库路径（workbuddy_project）：
  _serve.bat:2: cd /d D:\workbuddy_project\tiktok\webapp
  _serve2.bat:2: set "BASE=D:\workbuddy_project\tiktok\webapp"
  _serve_auto.bat:2: cd /d D:\workbuddy_project\tiktok\webapp
  install.bat:5: set BASEPY=D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe
  start_workbench.vbs:12: ' 手动测试：wscript.exe "D:\workbuddy_project\tiktok\webapp\start_workbench.vbs"
  start_workbench.vbs:16: APP_DIR = "D:\workbuddy_project\tiktok\webapp"
  tests\bench_products.test.js:9: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\bench_products.test.js:10: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\bench_render.test.js:7: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\bench_render.test.js:8: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\bench_url.test.js:12: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\bench_url.test.js:13: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\fail_analysis.test.js:7: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\fail_analysis.test.js:8: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\fold_blocks.test.js:14: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\fold_blocks.test.js:15: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\pipe_fold.test.js:13: const HTML = "D:/workbuddy_project/tiktok/webapp/static/index.html";
  tests\pipe_fold.test.js:14: const APP = "D:/workbuddy_project/tiktok/webapp/static/app.js";
  tests\port_override.test.py:58: sys.path.insert(0, str(Path(r"D:/workbuddy_project/tiktok/pipeline")))
  tests\post_update_port.test.py:188: "pipeline_dir": "D:\\workbuddy_project\\tiktok\\pipeline",
  tests\product_scan_acceptance.js:1: const { chromium } = require('D:/workbuddy_project/tiktok/webapp/node_modules/playwright-core');
  tests\refresh_active_view.test.js:13: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\refresh_active_view.test.js:14: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\repo_paths.test.py:2: """旧仓库路径回归：仓库从 D:\\workbuddy_project\\tiktok 搬走后，脚本与用例不许再写死旧路径。
  tests\reverse_progress_ui.test.js:14: const ROOT = "D:/workbuddy_project/tiktok/webapp";
  tests\reverse_restore_e2e.test.py:79: root = Path(r"D:/workbuddy_project/tiktok/reverse")
  tests\reverse_voice_browser.js:3: const out=process.argv[2],id='991027'+Date.now(),root=path.resolve('D:/workbuddy_project/tiktok/reverse'),dir=path.resolve(root,id);
  tests\reverse_voice_origin.test.py:10: 隔离：`tk_reverse.REVERSE_DIR` patch 到临时目录，不碰生产 `D:/workbuddy_project/tiktok/reverse`。
  tests\run_add_account.test.js:13: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\run_add_account.test.js:14: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\run_progress.test.js:14: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
  tests\run_progress.test.js:15: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\smoke_no_ledger.test.js:5: const html = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/index.html", "utf8");
  tests\smoke_no_ledger.test.js:6: const appJs = fs.readFileSync("D:/workbuddy_project/tiktok/webapp/static/app.js", "utf8");
共 23 个文件受影响。
EXIT=1
```

说明：34 处里有 1 处（`tests\repo_paths.test.py:2`）是**新用例外自己的 docstring** 引了旧路径——
写它的时候还没跑过，随后把 docstring 改成「仓库搬家后」；其余 33 处是仓库里既有的旧路径。这不是别人的代码。

## 2. 绿：改动**之后**的真实输出

命令同上。真实输出：

```text
扫描 123 个文件（tests/*.js、tests/*.py、*.bat、*.vbs）
PASS 未发现硬编码旧仓库路径
EXIT=0
```

用例的扫描范围与自我排除：扫 `webapp/tests/*.js`、`webapp/tests/*.py`、`webapp/*.bat`、`webapp/*.vbs`；
针串在运行时拼成 `"workbuddy" + "_project"`，所以本文件扫到自己也不会误报。

## 3. DoD 2（更正 1）：12 条命令逐条结果

全部在 `cd D:\tiktok-workbench\webapp` 下逐条执行。

### 3.1 (a) 改后已绿的 5 条，必须保持退出码 0 —— 实测全绿

| 命令 | 退出码 | 真实结果（尾行） |
|---|---|---|
| `node tests\fold_blocks.test.js` | 0 | `通过 52 / 52` |
| `node tests\pipe_fold.test.js` | 0 | `通过 17 / 17` |
| `node tests\run_add_account.test.js` | 0 | `结果: 10 通过 / 0 失败` |
| `node tests\smoke_no_ledger.test.js` | 0 | `通过 18 / 18` |
| `.venv\Scripts\python.exe tests\port_override.test.py` | 0 | `Ran 11 tests in 0.158s` / `OK` |

`port_override.test.py` 改前是 `Ran 11 tests` / `FAILED (errors=4)`：4 个 `DailyBatchBaseTests`
因为 `sys.path` 指向不存在的旧 `D:/workbuddy_project/tiktok/pipeline` 而 `ModuleNotFoundError: No module named 'scripts'`；
改成 `Path(__file__).resolve().parents[2] / "pipeline"` 后 11 个用例全过。

### 3.2 (b) 7 条历史红：失败输出里不得再出现 `ENOENT` / `workbuddy_project` —— 实测均无

逐条跑并全量收 stdout+stderr 检查：7 条 `EXIT=1`，`oldPathInOutput = none`。

| 命令 | 退出码 | 真实失败原因（与旧路径无关） |
|---|---|---|
| `node tests\bench_products.test.js` | 1 | `TypeError: Cannot read properties of null (reading 'querySelectorAll')`（test:79） |
| `node tests\bench_render.test.js` | 1 | `结果: 9 通过 / 6 失败`（`✗ 节点 #bench-accounts 仍存在（未被 outerHTML 销毁） -> assertion failed` 等） |
| `node tests\bench_url.test.js` | 1 | `TypeError: Cannot set properties of null (setting 'innerHTML')`（test:32，`#bench-account` 为 null） |
| `node tests\fail_analysis.test.js` | 1 | `ReferenceError: STEP_META is not defined`（eval 的 app.js:2131） |
| `node tests\refresh_active_view.test.js` | 1 | `TypeError: Cannot set properties of null (setting 'innerHTML')`（test:121） |
| `node tests\reverse_progress_ui.test.js` | 1 | `TypeError: Cannot read properties of undefined (reading 'className')`（test:96）；`✗ 步骤胶囊数量 == 后端 steps — 0 !== 5` |
| `node tests\run_progress.test.js` | 1 | `ReferenceError: STEP_META is not defined`（eval 的 app.js:2131） |

**为什么确定不是本次改动造成：**

1. 动手**之前**我逐条跑过基线：11 个 node 用例**全部 `EXIT=1`**（当时都是旧路径 ENOENT）。改后 4 条变绿、7 条仍红，
   **没有任何一条从绿变红**。
2. `git show HEAD:webapp/static/index.html` 里这些用例查的选择器出现次数**都是 0**：
   `id="bench-account"`、`prod-head`、`id="bench-accounts"`、`id="bench-products"`、`rv-steps`。
   且 `git status --short webapp/static/index.html` 为空——我没改过它。也就是说这些用例查的 DOM 在搬家前就已经不存在。
3. `README.md:193-197` 本来就写明：`bench_*.test.js` 等历史用例
   「target the DOM from before the 对标 page was rewritten and are red on purpose-unchanged code.
   They need rewriting; they are not an environment problem.」
4. `STEP_META` 在 `webapp/static/app.js` 只有 2 处**读取**（2094、2131），`webapp/static/` 下无任何定义——
   这是 app.js 自身的未定义全局（既有的 `qa/gaps.md` 第 19 条），不是旧路径问题，本任务**不修**。

## 4. (c) 旧路径解析正确性：grep 真实结果

全部实测，`cd D:\tiktok-workbench`：

| 检查 | 命令 | 结果 |
|---|---|---|
| 启动脚本 | `Select-String -Path webapp\*.bat,webapp\*.vbs -Pattern workbuddy_project` | **NONE（0 命中）** |
| 看板用例 | `Select-String -Path webapp\tests\*.js,webapp\tests\*.py -Pattern workbuddy_project` | **NONE（0 命中）** |
| README 两版旧 `cd` | `Select-String -Path README.md,README-zh.md -Pattern 'cd D:\workbuddy_project\tiktok'` | **NONE** |
| README 两版旧 `PY=` | `Select-String -Path README.md,README-zh.md -Pattern 'set PY=D:\workbuddy_project\tiktok'` | **NONE** |

## 5. DoD 1 / 3 / 4 的其它实证

- **DoD 1（启动脚本）**：5 个启动脚本 + 追加的 `install.bat`、`_serve_bg.bat` 里路径全部按脚本自身位置推导
  （`.bat` 用 `%~dp0`；`.vbs` 用 `fso.GetParentFolderName(WScript.ScriptFullName)`），端口统一 **8789**。
  额外实跑：`cscript //nologo start_workbench.vbs` 在 8789 已在监听时 **EXIT=0**——它真的执行到
  `Set envProc = ws.Environment("PROCESS")` / `envProc("WB_PORT") = CStr(PORT)` 之后，由幂等 netstat 检查退出，
  证明 VBS 语法可执行、`WB_PORT` 交给子进程的写法有效、幂等行为保留，且没有起第二个实例。
- **DoD 3（product_scan_acceptance.js）**：无旧路径；页面地址本就是 `http://127.0.0.1:8789/#prompt`；
  `require('playwright-core')` 在 `webapp\tests` 下 `require.resolve` 得
  `D:\tiktok-workbench\webapp\node_modules\playwright-core\index.js`（EXIT=0，未启动浏览器、未跑该手工脚本）。
- **DoD 4（README）**：中英两版命令一致——各 1 处 `set PY=D:\tiktok-workbench\pipeline\.venv\Scripts\python.exe`、
  3 处 `cd D:\tiktok-workbench`（`README.md:129,161,203,341`；`README-zh.md:110,138,173,290`）。
  只替换路径，未改其它章节。

## 6. 回归与附加验证

- 改过的 **14 个 JS** 全部 `node --check` **EXIT=0**（`static\app.js`、11 个 jsdom 用例、
  `product_scan_acceptance.js`、`reverse_voice_browser.js`）。
- 改到的其它 Python 用例（不在 12 条里，但被本次改动碰到）：
  `post_update_port.test.py` **EXIT=0**；`reverse_voice_origin.test.py` **EXIT=0**（Ran 7 tests, OK）；
  `reverse_restore_e2e.test.py` **EXIT=0**（Ran 2 tests, OK）。
- 派生路径实测存在：`webapp\..\pipeline\.venv\Scripts\python.exe`、`webapp\.venv\Scripts\pythonw.exe`；
  `_boot.py` / `_boot2.py` 均存在。
- 未重启 8789 服务；未装任何依赖；未新增依赖。

## 7. 改动文件清单（27 项 = 26 改 + 1 新建）

1. `webapp/start_workbench.vbs`（改）— `APP_DIR = fso.GetParentFolderName(WScript.ScriptFullName)`；
   `PORT = 8789`；新增 `envProc("WB_PORT") = CStr(PORT)`；注释/手动测试命令同步；幂等 netstat 保留。
2. `webapp/start_workbench.bat`（改）— `PORT=8789` + `WB_PORT=8789`；头注释端口。
3. `webapp/_serve.bat`（改）— `cd /d "%~dp0"` + `WB_PORT=8789`。
4. `webapp/_serve_auto.bat`（改）— 同上。
5. `webapp/_serve_bg.bat`（改）— 补 `set "WB_PORT=8789"`（2026-11-05 追加）。
6. `webapp/_serve2.bat`（改）— `BASE=%~dp0`、`PORT=8789`、`WB_PORT`、幂等 netstat 保留。
7. `webapp/install.bat`（改）— `BASEPY=%~dp0..\pipeline\.venv\Scripts\python.exe`（追加）。
8. `webapp/static/app.js`（改，仅 1 行）— 第 507 行重启提示：
   `D:\\workbuddy_project\\tiktok\\webapp\\重启工作台.bat` → `webapp\\重启工作台.bat`。
9. `webapp/tests/bench_products.test.js`（改）— 加 `const path = require("path")`，static 改 `path.resolve(__dirname, "..", "static", ...)`。
10. `webapp/tests/bench_render.test.js`（改）— 同上。
11. `webapp/tests/bench_url.test.js`（改）— 同上。
12. `webapp/tests/fail_analysis.test.js`（改）— 同上。
13. `webapp/tests/fold_blocks.test.js`（改）— 同上。
14. `webapp/tests/pipe_fold.test.js`（改）— `HTML`/`APP` 常量改 `path.resolve(...)`。
15. `webapp/tests/refresh_active_view.test.js`（改）— 同上。
16. `webapp/tests/reverse_progress_ui.test.js`（改）— `ROOT = path.resolve(__dirname, "..")`。
17. `webapp/tests/run_add_account.test.js`（改）— 同上。
18. `webapp/tests/run_progress.test.js`（改）— 同上。
19. `webapp/tests/smoke_no_ledger.test.js`（改）— 同上。
20. `webapp/tests/product_scan_acceptance.js`（改）— `require('playwright-core')`。
21. `webapp/tests/port_override.test.py`（改）— `Path(__file__).resolve().parents[2] / "pipeline"`。
22. `webapp/tests/post_update_port.test.py`（改，1 行）— mock 载荷 `"pipeline_dir"` 改派生路径（追加）。
23. `webapp/tests/reverse_restore_e2e.test.py`（改，1 行）— `_real_dirs()` 改 `parents[2] / "reverse"`（追加）。
24. `webapp/tests/reverse_voice_browser.js`（改，1 行）— `path.resolve(__dirname,'..','..','reverse')`（追加）。
25. `webapp/tests/reverse_voice_origin.test.py`（改，1 行）— docstring 旧目录改 `reverse/`（追加）。
26. `README.md`、`README-zh.md`（改）— 1 处 `set PY=`、3 处 `cd`；中英一致。
27. `webapp/tests/repo_paths.test.py`（**新建**，本任务的测试文件）。

## 8. 未满足项与说明

1. **更正前的原 DoD 2「12 条命令各自退出码 0」按字面不达成**：其中 7 条是改前就红的旧 DOM 用例
   （与旧路径无关，见第 3.2 节）。已在 `docs/tasks/tasks.md` 由 PM 写「更正 1（2026-11-05）」，
   并在 `qa/gaps.md` 第 18 条记入。按更正后的 (a)(b)(c)(d) 标准全部达成。
   **除此之外没有未满足的 DoD 项。**
2. 按 PM 指示**未修** `STEP_META`（`qa/gaps.md` 第 19 条），也未重写 7 个历史用例。
3. 残留观察（不在 T-13 清单、未改）：`README.md:315` 与 `README-zh.md:269` 仍写 `WB_REVERSE_DIR`
   缺省是 `D:/workbuddy_project/tiktok/reverse`——这是**准确描述** `webapp/tk_reverse.py:47` 的代码缺省值；
   DoD 4 只禁 `cd` 与旧 `PY=`，故合规。要彻底去旧路径需改 `tk_reverse.py`（产品代码、影响 reverse 数据落盘位置），
   属另一个任务。

## 9. 约束核对

- 未执行任何 git 写命令（add/commit/push/merge/分支）；未建分支或 worktree。
- 未重启 `127.0.0.1:8789` 服务、未杀其进程。
- 未新增依赖、未跑 npm/pip install。
- 未改 `.dsh-crew/`、`pipeline/scripts/`、`qa/`（本文件除外）、用户数据（`webapp/data`、`pipeline/output`、`ledger`）。
- 只改 T-13 Owns 里的文件；别人正在改的 `webapp/llm.py`、`webapp/static/bench2/bench.js`、`webapp/static/bench2/derive.js`、
  `webapp/tests/bench_empty_data.test.js`、`webapp/tests/llm_endpoint_url.test.py` 一律没碰。
