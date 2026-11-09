# Changelog

All notable changes to this workbench are recorded here, newest first.
Entries carry the date; since `0.1.0` they also carry the `VERSION` number.

## 0.1.0 — 2026-11-05 — Portable green build: 两个 ZIP（完整 full / 精简 slim）, no bundled LLM key, no PyTorch in the package

### Added

- **两个 ZIP（完整 full / 精简 slim）** — `webapp\.venv\Scripts\python.exe packaging\make_zips.py`
  writes `dist\tiktok-workbench-0.1.0-full.zip` (carries the ~1.46 GB transcription model,
  so transcription works offline) and `dist\tiktok-workbench-0.1.0-slim.zip` (no model; the
  first transcription downloads it), plus `SHA256SUMS.txt` and `build-report.json`. Unzip,
  double-click `启动工作台.bat`, done: no Python, no installer, no administrator rights.
  The user manual (`使用说明.md`) ships inside the ZIP.
- **包内不自带 LLM key** — the ZIP never contains an API key. AI translation and AI chat
  need internet **and** a key the user puts into `workbuddy\models.json`. A key inside a
  redistributable ZIP could not be revoked, so it is not bundled.
- **包内不含 PyTorch** — the package ships faster-whisper + ctranslate2 only and transcribes
  on the CPU (int8). The development environments keep their own dependencies
  (`pipeline\requirements.txt`); the portable set is `pipeline\requirements-portable.txt`.
  Transcription quality and the ledger format are unchanged.
- Third-party licences: the ZIPs bundle **GPLv3** ffmpeg and the FFmpeg libraries inside
  **PyAV**; the repository's own source stays **MIT** — see `THIRD_PARTY_NOTICES.md`.

### Known

- Transcription runs on the CPU. A GPU (`auto` + CUDA) path exists but is unverified on a
  machine without cuDNN, where `auto` falls back to CPU int8 (`qa/gaps.md`).
- Reverse-engineering works on **local video files** only: the package ships no
  `yt-dlp.exe`, so pasting a TikTok link does nothing. Collecting from TikTok still needs
  the user's own proxy, and AI translation/chat needs the user's own key.

## 2026-11-05 — Empty data no longer blanks the benchmark page; AI endpoints resolve correctly; the app survives being moved

### Fixed

- **The 对标分析 (benchmark) page opens again when there is no video data.** With an empty
  `/api/benchmark` payload, `bench2/derive.js` turned an empty last day into
  `new Date('')` → `toISOString()` (`RangeError: Invalid time value`), and `bench2/bench.js`
  dereferenced an empty series in `trend()` (`reading 'date'`) and `topvideos()`
  (`reading 'plays'`). Both now render an empty state. Regression cases:
  `webapp/tests/bench_empty_data.test.js`.
- **AI endpoints stored as full URLs are no longer doubled.** `~/.workbuddy/models.json` may
  store the full endpoint (e.g. `https://api.deepseek.com/v1/chat/completions`), but
  `llm._endpoint()` appended `/chat/completions` unconditionally, so the request went to
  `.../chat/completions/chat/completions` and answered 404. Full endpoints are now used
  as-is; base URLs are still completed exactly as before. Regression case:
  `webapp/tests/llm_endpoint_url.test.py`. (A valid key and balance are still required for
  real calls — the 2026-11-05 check found the configured DeepSeek account at 402 and the
  GLM key at 401.)
- **The app works after the folder was moved.** Launchers (`start_workbench.vbs`,
  `start_workbench.bat`, `_serve*.bat`, `install.bat`) now derive their own location and use
  port 8789 (`_serve_port.bat` takes the port from its caller, default 8789); the 11 jsdom
  cases that hardcoded `D:/workbuddy_project/tiktok/webapp/static`, plus `port_override.test.py`
  and four other test scripts, now resolve paths relative to the repository; both READMEs
  use the current checkout path. New guard against the old path returning:
  `webapp/tests/repo_paths.test.py`.

### Known

- The standalone page `webapp/static/bench2.html` still has the same empty-data crash in
  `bench2/data-live.js`; `index.html` does not load it. See `qa/gaps.md` #22.
- The launcher path change was checked statically and by an idempotent start while 8789 was
  already running; a true cold start from another working directory was not performed
  (`qa/gaps.md` #20).
- `qa/run-all.ps1` stays red in this repository because `qa/T-04` pins a commit that is not in
  this history and `test_publish_artifacts.py` is 8/13; see `qa/gaps.md` #21.

## 2026-11-04 — A deploy checklist for the next machine

### Added

- `DEPLOY_CHECKLIST.md` — everything that went wrong while testing this build on a real
  machine, rewritten as a checklist the next deployer can follow: a one-minute self-check
  script (paste and run), the eight environment variables and what breaks without each one,
  a symptom → cause → fix table covering the interpreter, proxy port, encoding, `.ps1` BOM,
  ledger location and favicon-level annoyances, and the real test baselines (8/8 pipeline,
  50/53 dashboard Python, 19/43 dashboard Node, 2/3 QA) so a fresh machine does not panic at
  the known-red legacy cases. It also covers updating the code when `git` cannot reach
  GitHub directly: the one-shot `-c http.proxy=` form, and why "Could not connect" is a
  network problem rather than a permissions one.

## 2026-11-04 — The scraping layer is one honest layer, and the WAF fallback works

### Changed

- `pipeline/scripts/tiktok_fetch.py` no longer pretends to have two fetch layers. It tried
  `scrapling`'s `Fetcher` first and fell back to `curl_cffi`, but scrapling was never in
  `requirements.txt` and never installed, so the first layer never ran — and its `Fetcher`
  is built on curl_cffi anyway, so installing it would not have added a second capability.
  The module is now explicitly "curl_cffi chrome124 fingerprint + proxy". At the time of the
  change a detail-page fetch through curl_cffi returned 200 with a full `itemStruct`, so
  nothing was broken by removing the dead layer.

### Fixed

- **The browser fallback (for WAF challenge pages) was broken four ways and now works.**
  It needed `playwright` (absent), hard-coded proxy port 7897 while this machine uses 7892,
  picked its interpreter with `shutil.which("python"/"py")` — on this machine those resolve
  to Store app-execution aliases that exit 9009 — and one candidate was a
  `C:\Users\Administrator\…` path that does not exist. Both `pw_*.py` now read `WB_PROXY`,
  and `tiktok_fetch.find_playwright_python()` chooses an interpreter by actually running
  `import playwright` (override with `WB_PW_PYTHON`).
- Verified end to end: `pw_detail_fallback.py` returns `status: ok` with a full `itemStruct`,
  `pw_uid_fallback.py` returns uid/secUid/nickname, and the scraper's
  `browser_batch_fallback()` resolves to `('ok', True)`.

### Added

- `pipeline/scripts/probe_waf.py` — a one-request probe that reports whether a detail page
  came back as real data or as a WAF challenge page, so "is the fallback needed today?" can
  be answered instead of assumed.

## 2026-11-04 — The reverse page now opens on a fresh machine

### Fixed

- **「提示词反推」renders when there are zero reverse-engineering tasks.** On a fresh
  install `GET /api/reverse/list` returns an empty list, and the page used to throw
  `TypeError: Cannot read properties of undefined (reading 'createdDay')` while building
  its KPI row, so `renderAll()` aborted and the view stayed an empty shell. The KPI now
  shows `暂无任务`, and the task-detail blocks get an empty-state hint instead of being
  handed `undefined`. Regression case: `webapp/tests/rv2_kpi_empty.test.js`.
- The dashboard test commands in `README.md` / `README-zh.md` were wrong and produced a
  wall of false failures: they used `pipeline\.venv` (which has no `fastapi`) and ran the
  Node cases from `webapp\tests` (which breaks their relative `static/index.html` reads).
  Both are corrected, and the pipeline test block now sets `PYTHONIOENCODING=utf-8`, so the
  `✓`/`✗` output no longer raises `UnicodeEncodeError` on a default GBK console.

### Added

- `jsdom` is now declared in `webapp/package.json` under `devDependencies`. All 42
  `webapp/tests/*.test.js` cases `require("jsdom")`, but it was missing, so a clean
  `npm install` could not run a single Node case.

### Known: a batch of UI cases is stale

Around 20 cases under `webapp/tests/` (`bench_*.test.js`, `accounts_admin_ui.test.js`,
`sheet_view.test.js`, …) still assert against the pre-`bench2` DOM (`#bench-accounts` and
friends), which the 对标 page no longer renders. They are red on unchanged code and need
rewriting; they are not an environment problem.

## 2026-11-02 — Scrape failures now say the real reason, and no longer destroy history

### Changed

- A failed video scrape now records **why** the page failed. The error text carries the
  page's own status code and message, for example
  `itemStruct 空（已下架/私有） [statusCode 10204 author_status,status_reviewing]`, instead of
  the blanket `itemStruct 空（已下架/私有）`. You can now tell three different situations
  apart — author under review, video really deleted, network blip — without opening the
  page by hand.
- `100001` (an RPC network error, a transient drop) is now classified as retryable and goes
  through the existing `--blocked-retry` / `--blocked-sleep` path, instead of being treated
  as permanently removed and never tried again.
- The `--blocked-retry` / `--blocked-sleep` help text now says these retries also cover
  network drops, not only risk pages.

### Fixed

- **A run that scraped 0 valid videos used to overwrite `scrape_<account>.json`**, taking
  the previous run's play counts and likes with it. It no longer writes the file in that
  case: the file keeps its old bytes, the run prints a "not written" marker, and the exit
  code stays 0, so the pipeline still stops on a zero-result run exactly as before.
- Two tests in `pipeline/tests/test_acceptance_regressions.py` had been broken (and
  silently hitting the real network and the browser fallback) since 2026-10-24: they mocked
  a function that `main()` no longer calls. They pass again — still 27 cases, assertions
  unchanged.

### Known cost of the history protection — please read

The dashboard's scrape-health panel reads that **same** `scrape_<account>.json`. Because a
fully failed run no longer refreshes it, such a run **no longer shows up on that panel**: the
account's error count and last-run time stay frozen at their previous values, and the account
can drop out of the panel's 48-hour window, so its failure stops counting against the success
rate. For an account that has an older file, this makes the success rate look *better* than
it is.

The failure itself is not lost — it is still recorded in `webapp/data/runs.jsonl`,
`pipeline/pipeline_state.json` and the gate report. But if you rely on the dashboard's health
numbers, know that they under-report failures now. Making the panel read `pipeline_state.json`
instead is a separate piece of work.

### Data

- Account `example_account_022` is deactivated (`"active": false` in `pipeline/accounts.json`).
  TikTok reports its author as under review (`author_status,status_reviewing`) for all 49
  videos. Its history index is kept and it is still visible in the account list; it just no
  longer runs in the daily pipeline. A pre-change copy of the file is in
  `_bak_deactivate_20261102/`.
- The scrape data of that account that was **already lost cannot be recovered**: an earlier
  full-failure run had overwritten `scrape_example_account_022.json` to "0 videos + 49 errors", so
  the play/like snapshots that file used to hold are gone. The Excel ledger was not affected
  — it is a separate file and remains the source of truth.

### Repository layout

- `README.md` / `README-zh.md` added (the repository had none).
- `qa/` added: acceptance cases for this change (`qa/T-01` … `qa/T-04`), the shared runner
  `qa/run-all.ps1`, and the standing gap list `qa/gaps.md`.

### Not done

- Videos that failed **before** this change are not re-fetched automatically. Run the scrape
  again with `--merge` when you want them filled in.
- `10204` (author under review) is still classified as "removed", so it is **not retried
  inside a scrape run** — only its message changed. The scheduled daily update
  (`daily_batch.py`) does not retry it either. Note that the older, manual daily-update path
  (`daily_update.py`) does run a slow-retry pass over everything in `errors[]`, regardless of
  reason.
- That slow-retry pass (`retry_scrape_failed.py`) still writes its own fixed error text, so
  entries it touches **lose the page's real status code and message** again. Fixing it is
  separate work; until then, the real reason is guaranteed only for what a scrape run writes.
