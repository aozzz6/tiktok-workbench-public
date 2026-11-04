English | [中文](README-zh.md)

# TikTok Benchmark Workbench

A local workbench for tracking a set of TikTok accounts: it collects each account's
videos, transcribes them, writes the numbers into an Excel ledger, and serves a
dashboard on `http://127.0.0.1:8789` for reading them.

It is a **local, single-machine tool**: there is no server side, no login, and the
collected data stays on this machine. But it is not air-gapped — besides fetching
pages from TikTok, it can talk to services you configure:

| What goes out | To where | When |
|---|---|---|
| Account pages, video pages | `www.tiktok.com` (through the proxy) | scraping, always |
| Transcripts to be translated | the LLM endpoint configured in `~/.workbuddy/models.json` — in practice a remote HTTPS API (`workbuddy.tokenmao.cn` or `api.deepseek.com`) | translation; `WB_AUTO_TRANSLATE` in `webapp/config.py` is **on by default** |
| Short text snippets | `translate.googleapis.com` | the `translate_pending.py` path |
| Product / trend lookups | TikTok Ads (`ads.tiktok.com`) and other third-party sites | only when those product scripts are run |

So: nothing is sent anywhere by merely having the workbench open — unless you turn on
its built-in daily schedule (`webapp/data/schedule.json`, `enabled: true`), which then
starts a daily batch by itself, scraping and translating on a timer.

> A note on words: this README calls the `webapp/` service the **dashboard**. Other
> documents in this repository call it the **workbench** (`工作台`) — for example
> `start.bat` prints "工作台启动中" and the pipeline logs "工作台不可达". It is the
> same thing.

## What is in here

| Path | What it is |
|---|---|
| `webapp/` | The dashboard: FastAPI + plain JavaScript, served on port **8789**. The launchers (`start.bat`, `wb_start.ps1`) set `WB_PORT=8789`; if you run `python app.py` yourself you get `app.py`'s own default, **8787**. Reads the data files below; does not collect anything itself. |
| `pipeline/` | The collection pipeline: scrape → download audio → transcribe → translate → write ledger. Plain Python scripts. Most run standalone; `daily_batch.py` is the exception — see below. |
| `pipeline/output/` | Collected artifacts: `scrape_<account>.json`, `<account>_audio/`, transcripts. Not in git. |
| `qa/` | Acceptance cases for the current change (`qa/T-*`), the shared runner `qa/run-all.ps1`, and the standing gap list `qa/gaps.md`. |
| `reverse/` | Scratch space for reverse-engineering work. Only `reverse/<video id>/` and `reverse/_uploads/` are git-ignored — the folder itself is tracked. |

The **Excel ledger is the source of truth** for the analysis (14 columns, one sheet
per account). It lives outside the repository. Its path is the `xlsx` field of
`pipeline/accounts.json`; if that is absent or points at nothing, the webapp falls
back to the newest `.xlsx` in `LEDGER_DIR` (`webapp/config.py:25`, default
`D:/1/对标账号`).

**The ledger file must sit inside `LEDGER_DIR`.** That is the only place
`config.ledger_path()` looks — an `.xlsx` elsewhere is silently not found. To keep
the ledger somewhere else, point `WB_LEDGER_DIR` at that folder instead.

## Requirements

- Windows, **Python 3.13**.
- Two virtual environments: `pipeline\.venv` and `webapp\.venv`.
- `ffmpeg` on `PATH` (audio conversion).
- For scraping: an HTTP proxy reachable at `http://127.0.0.1:7897`
  (the scraper talks to TikTok through it).
- No `pytest`. Tests are plain scripts that return exit code 0 / 1 — see below.

## Install

**Skip this section if `webapp\.venv` already exists and works** — on the machine this
was written on, it does. It needs access to PyPI (falls back to a mirror), and it rebuilds
`webapp\.venv` from scratch, so it is not something you run "just to look around".

```bat
cd webapp
install.bat
```

`install.bat` uses `pipeline\.venv`'s interpreter to create a **clean** `webapp\.venv`
— that environment does *not* inherit the pipeline's packages — and then installs
only what `webapp\requirements.txt` pins (`fastapi`, `uvicorn`, `openpyxl`).

**The pipeline environment has a pin file now: `pipeline\requirements.txt`.** It
lists only the packages the pipeline imports directly, pinned to what the author's
`pipeline\.venv` actually has. Install it with that interpreter:

```bat
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
```

Three caveats, so nobody is surprised:

- Those are **Windows + Python 3.13 CPU wheels** (`torch==2.7.0+cpu` is the CPU-only
  build from the PyTorch index). Nothing here has been tried on Linux or macOS.
- `scrapling` is **no longer a dependency — do not install it**. The fetch layer is now
  one thing only: `curl_cffi`'s chrome124 fingerprint plus the local proxy
  (`pipeline\scripts\tiktok_fetch.py`). Until 2026-11-04 this file described a
  "scrapling first, curl_cffi fallback" pair, but scrapling was never installed and that
  layer never ran; it has been removed. Its `Fetcher` is built on curl_cffi anyway, so
  installing it buys nothing new.
- `playwright` is **not** in the list either. Only the browser fallback
  (`pw_detail_fallback.py` / `pw_uid_fallback.py`) uses it, and it must live in the
  **system Python**, not in `pipeline\.venv` — see "The browser fallback" below.

The PyTorch CPU index that `torch==2.7.0+cpu` needs is declared **inside**
`pipeline\requirements.txt` itself (an `--extra-index-url` line at the top), so the
command above needs no extra flags.

`pip freeze` inside `pipeline\.venv` is still the complete list of what is actually
installed there.

## Run the dashboard

```bat
cd webapp
start.bat
```

It opens `http://127.0.0.1:8789`. Health check: `http://127.0.0.1:8789/api/health`.
Note that `start.bat` runs in a foreground console window: close that window and the
dashboard stops. For anything long-running, use the launcher below.

To run it in the background — detached, with no console window, idempotently (never
two copies), and with a PID plus health report — use the launcher instead:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File webapp\wb_start.ps1
```

Note: any `.ps1` in this repository that contains non-ASCII text **must be saved as
UTF-8 with BOM**, or Windows PowerShell 5.1 reads it as GBK and the script breaks.

## Run the pipeline

Everything runs from the repository root with the pipeline interpreter:

```bat
set PY=D:\workbuddy_project\tiktok\pipeline\.venv\Scripts\python.exe

rem scrape one account (full run, overwrites scrape_<account>.json)
%PY% pipeline\scripts\scrape_tiktok_video.py --account <account> --audio-dir pipeline\output\<account>_audio --out-dir pipeline\output

rem scrape only selected videos and MERGE into the existing file (keeps history)
%PY% pipeline\scripts\scrape_tiktok_video.py --account <account> --ids <id1>,<id2> --merge --out-dir pipeline\output
```

**`daily_batch.py` needs the dashboard running.** It does not drive the pipeline
itself: for each account it POSTs `/api/run` to the workbench and waits for the run
to finish. If it cannot reach a workbench it exits with `[fatal] 工作台不可达`.
Start the dashboard first (section above), then:

```bat
%PY% pipeline\scripts\daily_batch.py
```

Two behaviours worth knowing, both added 2026-11-02:

- A failed scrape now **appends** the page's real reason to the plain-language error
  text, for example
  `itemStruct 空（已下架/私有） [statusCode 10204 author_status,status_reviewing]`.
  It is no longer just "removed/private".
- A scrape run that produced **0 valid items** does **not** overwrite the existing
  `scrape_<account>.json`; the terminal summary says `未写盘` (not written).

## Run the tests

Pipeline unit tests — one script per area, each exits 0/1 and prints `通过 N / M`:

```powershell
cd D:\workbuddy_project\tiktok
# The cases print ✓ / ✗ / ⚠. Without this line a default GBK console raises
# UnicodeEncodeError and the case is reported as a failure.
$env:PYTHONIOENCODING = "utf-8"
Get-ChildItem pipeline\tests\test_*.py | ForEach-Object {
    & .\pipeline\.venv\Scripts\python.exe $_.FullName
}
```

Exception: `test_acceptance_regressions.py` is a stdlib `unittest` module, so it
prints `Ran N tests` / `OK` instead of `通过 N / M`. It still exits 0/1.

Dashboard tests — Python and Node side by side. **Two things are easy to get wrong,
and getting them wrong produces a wall of false failures:**

- **Use the `webapp\.venv` interpreter.** These cases `import app`, and `fastapi` is
  installed only in that environment — running them with `pipeline\.venv` gives
  `ModuleNotFoundError: No module named 'fastapi'` across the board.
- **Run from `webapp\`.** The Node cases read `static/index.html` by relative path,
  so running them from `webapp\tests` gives `ENOENT: no such file or directory`.

The Node cases need `jsdom`; it is declared in `webapp\package.json` under
`devDependencies`:

```powershell
cd webapp
npm install                                                       # first time; installs jsdom
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tests\accounts_write.test.py   # any *.test.py
node tests\overview_ui.test.js                             # any *.test.js
```

> Note: some legacy cases (`bench_*.test.js`, `accounts_admin_ui.test.js`,
> `sheet_view.test.js`, …) target the DOM from before the 对标 page was rewritten
> and are red on purpose-unchanged code. They need rewriting; they are not an
> environment problem. The regression case added for this fix is
> `tests\rv2_kpi_empty.test.js`.

Acceptance cases for the current change, through the shared runner (run this from
the repository root — the path below is relative to it):

```powershell
cd D:\workbuddy_project\tiktok
powershell -NoProfile -ExecutionPolicy Bypass -File qa\run-all.ps1
```

The runner walks every directory under `qa\`, finds an entry point in the order
`run.ps1` / `check.ps1` / `run.py` / `run.sh`, and runs it. A directory with no entry
point is reported as `[WARN] … 不算通过` and the whole run exits non-zero — "no cases
found" must never look like a pass.

## What is deliberately not in git

Runtime data, environments and local binaries are excluded on purpose: `.venv/`,
`node_modules/`, `pipeline/output/`, `webapp/data/`, `pipeline/accounts.json` (real
account list), `pipeline/pipeline_state.json`, `pipeline/gate_report.json`,
`runs*.jsonl`, audio, `*.xlsx`, `*.zip`, `pipeline/ffmpeg_bin/`, and every
`_bak_*` / `_backup_*` / `改前备份_*` directory.

**Session cookies must never be committed.** `.gitignore` covers cookie files,
including the `*cookie*.txt` pattern — so a name like `cc_cookie.txt` is caught too;
check `git ls-files` before you commit, because a cookie file matched by none of those
patterns will be picked up. (One such file has been tracked in this repository's
history already — see `qa/gaps.md` #15. Do not add another.)

Because `pipeline/accounts.json` is not tracked, **it cannot be rolled back with
git**. Copy it before changing it — the project habit is a `_bak_<topic>_<date>/`
folder **in the repository root** (`.gitignore` already excludes `_bak_*/`).

## First run on a fresh clone

Two things will not be there, because they are intentionally not in git:

1. `pipeline/accounts.json` — the account list. Minimum shape:

   ```json
   {"accounts": [{"account": "<name>", "lang": "en", "active": true}],
    "xlsx": "D:/1/对标账号/<your ledger>.xlsx"}
   ```

   The `.xlsx` must be inside `LEDGER_DIR` (default `D:/1/对标账号`) — see the note
   in the section above. `webapp/config.py` has `add_account()`, and the web UI has
   a "＋ 新增账号" button, if you prefer to create the file from the app.
2. The Excel ledger itself, in `LEDGER_DIR` (or wherever `WB_LEDGER_DIR` points).

## Use it on your own machine

This repository is published as a **minimal, working reference**, not as a
plug-and-play product. It was built and used on one Windows machine, and a few
places still say so. This section is the honest list of what has to change
elsewhere.

### What runs, and what does not

- The **dashboard** (`webapp/`) runs on another machine — but only once the
  environment variables below are set: every path it uses comes from them, and
  their built-in defaults are the author's own paths.
- The **pipeline** (`pipeline/`) does **not** run as-is: three lines in the main
  chain point at the author's directories (listed below), and more than a dozen
  helper scripts under `pipeline/scripts/` hard-code the same paths.
- **Scraping** additionally needs your own proxy and your own TikTok session —
  see the end of this section.

### Requirements

- Windows and **Python 3.13**.
- `ffmpeg` on `PATH` (audio conversion).
- No `pytest`: the tests are plain scripts that exit 0 or 1.

Two virtual environments are expected, and **nothing in this repository creates
the pipeline one** — `webapp\install.bat` creates only `webapp\.venv`. So create
the pipeline environment first, then install its dependencies:

```bat
py -3.13 -m venv pipeline\.venv
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
```

If `py -3.13` is not registered on your machine (check with `py -0p`), run the
same command with the full path to your Python 3.13 instead — the per-user
installer puts it at `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`.

`webapp\.venv` is created by `install.bat` — see **Install** above.

### The seven environment variables

`webapp/config.py` reads `WB_PIPELINE_DIR` (`:20`), `WB_PYTHON` (`:21`),
`WB_HOME` (`:22`), `WB_PROXY` (`:23`) and `WB_LEDGER_DIR` (`:25`);
`webapp/tk_reverse.py` reads `WB_REVERSE_DIR` (`:47`) and `WB_YTDLP` (`:50-52`).
Set them in your shell (or inside `start.bat`) before you start the dashboard.

| Variable | What it points at |
|---|---|
| `WB_PIPELINE_DIR` | your `pipeline` directory (default: the author's) |
| `WB_PYTHON` | the interpreter that runs the pipeline (default: `pipeline\.venv` under `WB_PIPELINE_DIR`) |
| `WB_HOME` | your WorkBuddy home directory (default: the author's `.workbuddy` folder) |
| `WB_LEDGER_DIR` | the folder that holds your Excel ledger (default: `D:/1/对标账号`) |
| `WB_PROXY` | proxy for the **dashboard itself only**; it does not affect scraping (default `http://127.0.0.1:7897`) |
| `WB_REVERSE_DIR` | the reverse-engineering scratch folder (default: the author's `D:/workbuddy_project/tiktok/reverse`) |
| `WB_YTDLP` | the `yt-dlp.exe` that view uses (default: the author's WorkBuddy binaries folder) |

**Watch `WB_REVERSE_DIR`: on a machine with no `D:` drive the dashboard does not
start at all.** `webapp/tk_reverse.py:47-48` resolves that path **and creates it
while the module is being imported**, and `webapp/app.py:43` imports that module
during startup — so with no `D:` drive and no `WB_REVERSE_DIR`, startup dies
before the first request. Point it at any writable folder of your own.

### The three hard-coded lines in the main chain

Each of these is a path on the author's disk. Change the path, keep the key:

1. `pipeline/pipeline.py:36` — `"python"`: the interpreter that runs each pipeline
   step.
2. `pipeline/pipeline.py:41` — `"output_root"`: where scraping writes its output.
3. `pipeline/daily_update.py:44` — `PY`: the same interpreter, used by the daily
   run.

Those three are the **main chain only**. More than a dozen helper and historical
scripts under `pipeline/scripts/` hard-code the same paths — for example
`build_13col_sheets.py:17,22`, `migrate_translations.py:13-15`,
`generate_benchmark_report.py:24`, `audit_ledger.py:75`. They are off the main
chain: fix each one when you actually need that script.

**The commands in this README are not portable either.** `set PY=...` and the two
`cd D:\workbuddy_project\tiktok` lines (in *Run the pipeline* and *Run the tests*)
carry the author's path; replace them with your own.

### Scraping needs two things you have to supply

- **A proxy.** It is **hard-coded in each script's `PROXY` constant** —
  `pipeline/scripts/tiktok_fetch.py:13`,
  `pipeline/scripts/scrape_tiktok_video.py:34`,
  `pipeline/scripts/tk_audio_download.py:19` and about a dozen more. There is no
  `--proxy` flag, so a different port means editing those constants.
  **`WB_PROXY` does not change scraping**: it is read only by `webapp/config.py`
  and affects the dashboard itself.
- **Your own TikTok session cookie**, saved as `pipeline/cc_cookie.txt`.

**The cookie file must never be committed.** It is a live login. `.gitignore`
covers cookie files, including the `*cookie*.txt` pattern — so a name like
`cc_cookie.txt` is caught too — but run `git status` (or `git ls-files`) before
you commit anyway: a cookie file matched by none of those patterns will be picked
up. One caveat: an ignore rule does not untrack a file git already tracks — for
those you need `git rm --cached <file>`.

### The browser fallback (getting past the WAF challenge)

TikTok sometimes makes the detail-page SSR return a WAF challenge page, and then no pure
HTTP fingerprint (curl_cffi chrome124 included) gets through — only a real browser running
the JS can reach the embedded data. `pw_detail_fallback.py` and `pw_uid_fallback.py` are
that fallback, and they must run under a **system Python that has `playwright`** — not the
pipeline venv:

```powershell
py -3 -m pip install "playwright==1.64.1"
# The browser binary reuses the chromium-1248 already on this machine
# (%LOCALAPPDATA%\ms-playwright); pick_browsers_dir() finds it automatically.
# Only if the machine has no browser build at all:
py -3 -m playwright install chromium
```

You do not pick the interpreter by hand: `tiktok_fetch.find_playwright_python()` tries each
candidate by actually running `import playwright` (it no longer trusts `shutil.which`, whose
names on this machine all resolve to Store placeholders), and `WB_PW_PYTHON` overrides it.
When nothing works the step is skipped and the scraper prints
`[warn] 浏览器兜底未找到可用 Python（…），跳过`; nothing else breaks.

The proxy comes from `WB_PROXY` — never hard-code a port in the fallback scripts.

### Starting from the sample account list

```bat
copy pipeline\accounts.example.json pipeline\accounts.json
```

Then edit the account names, the two directories, the `xlsx` path, and the
`metrics_file` / `reports_root` paths inside it. The account names in the sample
are obvious fakes, and `pipeline/accounts.json` is not tracked in git — on your
machine that file is yours to fill.

Two things about that file:

- **`xlsx` must sit inside `LEDGER_DIR`** (or you must point `WB_LEDGER_DIR` at
  its folder). `webapp/config.py` looks there only — a ledger anywhere else is
  **silently** treated as "no ledger", with no error message.
- **`metrics_file` and `reports_root` are required, not optional.**
  `pipeline/daily_update.py` reads them with `cfg[...]` (lines `:288`, `:299`,
  `:726`), so a missing key is an immediate `KeyError` at startup.
## More reading

- `DEPLOY_GUIDE.md` — moving the dashboard-only copy to another machine. Note that
  it still says port 8787 and 15 columns; both are out of date (8789, 14 columns).
- `工作上下文归档_最新.md` — working context: fixed rules, paths, interpreters.
- `qa/gaps.md` — what the current change's tests could **not** verify, and why.
- `LICENSE` — MIT. **Before you publish, replace `<YOUR NAME>` in the copyright line
  with your own name.**
- `THIRD_PARTY_NOTICES.md` — the third-party assets (font, chart library) and the
  licenses they stay under; MIT does not cover them.
