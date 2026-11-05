# T-12 工程师报告（crew-engineer-2 原文落盘）

> 落盘原因：T-12 的 DoD 第 1 条要求「报告贴改前红、改后绿两段真实输出」，而 role 的报告只活在会话消息里。
> 会话结束后消息就没了，所以把它**逐字**落盘在这里。落盘时间 2026-11-05，由工程师本人（crew-engineer-2）落盘，不是 PM 转述。
> 注意：这是「当时那一轮」的证据快照。红段用的是改 `webapp/llm.py` **之前**的代码，事后**不能重跑再现**（绿段可以）。
> 复跑绿段：`cd D:\tiktok-workbench\webapp; .venv\Scripts\python.exe tests\llm_endpoint_url.test.py`

T-12（共享任务 `task-2`）状态：**done**，已 complete（task-2 revision 4）。

## 1. 改动的文件（只这两个）

- `webapp/llm.py` — 只改 `_endpoint()`：先判断「url 已是完整端点」则原样返回（openai `.../chat/completions`、
  anthropic `.../messages`、gemini 含 `:generateContent`），否则按老规则拼接，老行为一字未改。附 docstring 说明 T-12。
  git diff：1 file changed, 14 insertions(+)，0 deletions。
- `webapp/tests/llm_endpoint_url.test.py` — 新建回归用例（9 条 check），导入真实 `llm` 模块；
  开头把 stdout 重配成 UTF-8（原因见第 5 节）。docstring 已写成 T-12。

## 2. DoD 1 — 新用例：改前真实红 / 改后真实绿

命令：`cd D:\tiktok-workbench\webapp; .venv\Scripts\python.exe tests\llm_endpoint_url.test.py`

改前（真实输出，exit 1）：

```
  ✗ openai：完整 .../chat/completions 原样返回，只有一个 /chat/completions -> https://api.deepseek.com/v1/chat/completions/chat/completions
  ✗ openai：完整端点带结尾斜杠也只返回一个 /chat/completions -> https://api.deepseek.com/v1/chat/completions/chat/completions
  ✗ anthropic：完整 .../messages 原样返回 -> https://api.anthropic.com/v1/messages/v1/messages
  ✗ gemini：完整 ...:generateContent 原样返回（不再套一层 /models/） -> ...:generateContent/models/gemini-2.0-flash:generateContent
  ✓ openai：基础 .../v1 仍拼成 .../v1/chat/completions
  ✓ openai：无路径基础地址仍拼成 .../chat/completions
  ✓ anthropic：基础 .../v1 仍拼成 .../messages（不是 /v1/v1/messages）
  ✓ gemini：基础地址仍拼成 .../v1beta/models/<id>:generateContent
  ✗ 三种风格：任何输入都不会出现重复的路径段 -> https://api.deepseek.com/v1/chat/completions/chat/completions
通过 4 / 9
EXITCODE=1
```

改后（真实输出，exit 0）：

```
  ✓ openai：完整 .../chat/completions 原样返回，只有一个 /chat/completions
  ✓ openai：完整端点带结尾斜杠也只返回一个 /chat/completions
  ✓ anthropic：完整 .../messages 原样返回
  ✓ gemini：完整 ...:generateContent 原样返回（不再套一层 /models/）
  ✓ openai：基础 .../v1 仍拼成 .../v1/chat/completions
  ✓ openai：无路径基础地址仍拼成 .../chat/completions
  ✓ anthropic：基础 .../v1 仍拼成 .../messages（不是 /v1/v1/messages）
  ✓ gemini：基础地址仍拼成 .../v1beta/models/<id>:generateContent
  ✓ 三种风格：任何输入都不会出现重复的路径段
通过 9 / 9
EXITCODE=0
```

红段的失败症状就是本次 bug 本身：`https://api.deepseek.com/v1/chat/completions/chat/completions`
（双份 `/chat/completions`，对应的线上表现是 HTTP 404，设置页「测试连接」永远失败）。

## 3. DoD 2、3 — 按任务行字面调用实测（python -c，真实输出）

```
'https://api.deepseek.com/v1/chat/completions'                            # 完整端点，原样
'https://api.deepseek.com/v1/chat/completions'                            # 基础 .../v1
'https://api.anthropic.com/v1/messages'                                   # anthropic .../v1
'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent'
EXITCODE=0
```

命令形状：`cd D:\tiktok-workbench\webapp; $env:PYTHONIOENCODING="utf-8";
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); import llm; print(repr(llm._endpoint('openai', {'url': 'https://api.deepseek.com/v1/chat/completions'}))); ..."`

## 4. DoD 4 — 既有用例 `webapp/tests/ai_llm.test.py`

`$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe tests\ai_llm.test.py` →

```
  ✓ R3 重问也受预算约束：上限 1 次时第二次追问被拦，首问的 200 token 仍记账
通过 24 / 24
EXITCODE=0
```

**必须如实说明**：不加 `PYTHONIOENCODING=utf-8` 时，这条既有用例在本机 GBK 管道下会 exit 1 ——
但这不是本次改动造成的，我在**改 llm.py 之前**先跑过一次，同样崩：

```
File "D:\tiktok-workbench\webapp\tests\ai_llm.test.py", line 43, in check
print(f"  ✗ {name} -> ...")
UnicodeEncodeError: 'gbk' codec can't encode character '\u2717'
```

原因与仓库自述一致：`webapp/README.md:93` 写着「stdout 走管道/文件时，不设的话 ✓/✗ 会
UnicodeEncodeError（假红）」。该文件不在 T-12 的 owns 范围内，没有改它。同理新用例在文件内自行
`reconfigure(encoding="utf-8")`，所以 DoD 里那条**原样命令**能直接 exit 0。
另：`chcp 65001` 无效（管道用的是 ANSI 代码页），不用再试。

## 5. 顺手做的回归（未计入 DoD，均为真实输出）

所有 `import llm` 的既有用例：

- `tests\ai_api.test.py` → 通过 10 / 10，EXITCODE=0
- `tests\ai_repair.test.py` → 通过 19 / 19，EXITCODE=0
- `tests\api_error_semantics.test.py` → Ran 13 tests, OK，EXITCODE=0

## 6. 未满足 / 未做的

- 无 DoD 项未满足。DoD 4 的情况见第 4 节（需带 README 记录的环境变量，且是改前就存在的编码问题，不是逻辑失败）。
- 没有调真实 LLM API（避免花钱）；只做纯函数检查与既有用例。
- 没有 git 写操作（未 commit/push/merge/分支），未重启 8789 服务，未新增依赖，未动用户数据。
- 提醒：`git status --short` 里另有 `?? pipeline/scripts/.secuid_cache.json`、
  `?? pipeline/scripts/.uid_cache.json`（不是本任务产生的，看起来是运行时缓存），staging 时请注意别带上。
