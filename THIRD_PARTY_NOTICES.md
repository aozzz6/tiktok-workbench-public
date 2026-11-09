# Third-party notices

**The repository's own source code is MIT — see `LICENSE`. The ZIP we publish
is not a pure MIT package: it also contains GPLv3 binaries (the ffmpeg CLI, and
the FFmpeg copy bundled inside PyAV). Those binaries, and the source code that
corresponds to them, are covered by the sections below.**

（中文对照，与上面同一句，逐字：**本仓库源码 MIT；发布出的 ZIP 内含 GPLv3
二进制（ffmpeg 与 PyAV 的 FFmpeg），整体不是纯 MIT 包**。）

## Overall picture

| What | Where | License | Source / license text |
|---|---|---|---|
| The workbench's own code (this repository) | everything not listed below | MIT (`LICENSE`) | this repository |
| ffmpeg / ffprobe CLI (Windows x64, static) | `<pkg>\ffmpeg\bin\` | **GPLv3** (this build is `--enable-gpl --enable-version3`) | `licenses\ffmpeg-COPYING.GPLv3.txt`, offer in `licenses\ffmpeg-SOURCE-OFFER.txt` |
| FFmpeg inside PyAV (`av.libs`) | `<pkg>\python\Lib\site-packages\av.libs\` | **GPLv3** (built with libx264/libx265) | `licenses\PYAV-FFMPEG-SOURCE.txt` |
| Bundled Python packages | `<pkg>\python\Lib\site-packages\` | mixed: MIT / BSD / Apache-2.0 / LGPL / PSF | `licenses\PYTHON-PACKAGES.txt` + the kept `*.dist-info` |
| Microsoft Visual C++ runtime DLLs | `<pkg>\python\` | Microsoft redistributable | this file, section "Microsoft Visual C++ runtime" |
| DM Sans (Latin subset, WOFF2) | `webapp/static/fonts/dm-sans-latin.woff2` | SIL Open Font License 1.1 (`OFL-1.1`) | <https://openfontlicense.org/> — **exact wording still to be confirmed, see the TODO below** |
| Apache ECharts (minified browser bundle) | `pipeline/assets/echarts.min.js` | Apache License 2.0 | <https://www.apache.org/licenses/LICENSE-2.0> |

## `webapp/static/fonts/dm-sans-latin.woff2` — DM Sans, OFL-1.1

Loaded by `webapp/static/style.v2.css`:

```css
@font-face { font-family:"DM Sans"; src:url("/static/fonts/dm-sans-latin.woff2") format("woff2"); }
```

The SIL Open Font License 1.1 allows redistribution and embedding, but it requires
the license text and the copyright notice to travel with the font.

**TODO before the first public release**

- The OFL-1.1 text is **not** reproduced in this repository, and it is not
  paraphrased here on purpose: copy the authoritative text from
  <https://openfontlicense.org/> (or from the upstream DM Sans release) into
  `webapp/static/fonts/OFL.txt`, next to the font.
- Fill in the exact copyright line from that upstream release in the table above.

## `pipeline/assets/echarts.min.js` — Apache ECharts, Apache-2.0

The file keeps its own ASF header at the very top:

```
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
...
   http://www.apache.org/licenses/LICENSE-2.0
```

**Keep that header.** The bundle also contains an embedded
`Copyright (c) Microsoft Corporation.` notice; keep that one too.

**TODO before the first public release**

- Ship the Apache-2.0 text with the distribution (for example as
  `pipeline/assets/echarts-LICENSE.txt`), plus the upstream `NOTICE` file if the
  ECharts release ships one — Apache-2.0 requires both to travel with the code.

## ffmpeg CLI (`ffmpeg.exe`, `ffprobe.exe`) — GPLv3

- **Version:** `ffmpeg version 9.0.2-essentials_build-www.gyan.dev` (Windows x64).
- **License:** GPLv3 (the build is `--enable-gpl --enable-version3 --enable-static`).
  The full GPLv3 text ships as `licenses\ffmpeg-COPYING.GPLv3.txt`.
- **Source assets:** the FFmpeg source for this build is commit
  `946fcce07b` (<https://github.com/FFmpeg/FFmpeg/commit/946fcce07b>), published
  next to the ZIP as `ffmpeg-9.0.2-source.tar.xz`; the 37 statically linked
  third-party libraries are indexed in `ffmpeg-9.0.2-static-libs-sources.txt`.
- **Written offer:** `licenses\ffmpeg-SOURCE-OFFER.txt` is the GPLv3 §6(b)
  written offer — valid at least three (3) years, covering the complete
  corresponding source, medium/postage cost only, with a concrete request
  channel (the repository issue tracker).
- **How we use it:** as a **separate process** — the workbench runs
  `ffmpeg.exe`/`ffprobe.exe` through `subprocess` and does **not link** them
  with the project's code, so the MIT project source is not a derivative work
  of FFmpeg.

## The FFmpeg inside PyAV — GPLv3, loaded in-process

- **What:** the `av` Python package (PyAV) ships its own prebuilt FFmpeg in
  `<pkg>\python\Lib\site-packages\av.libs\` (36 DLLs).
- **Version:** PyAV **14.2.0**, embedding **FFmpeg 7.1** (libavcodec 61.19.100 —
  different from the 9.0.2 CLI build; two copies, two versions).
- **License:** GPLv3 — this build contains `libx264`/`libx265`.
- **How it is loaded:** **in the same process** as the workbench's Python code
  (through PyAV's extension), which is why it has its own notice and its own
  source asset: `ffmpeg-7.1-source.tar.xz`.
- **Details and source instructions:** `licenses\PYAV-FFMPEG-SOURCE.txt`.
  The same GPLv3 §6(b) offer in `licenses\ffmpeg-SOURCE-OFFER.txt` covers it.

## Bundled Python packages

The ZIP ships the Python packages the workbench needs (about 350 MB), installed
under `<pkg>\python\Lib\site-packages\`. Each package keeps its **own** license,
and its original metadata is **kept**: every distribution's
`*.dist-info\METADATA` (name, version, license fields) stays in place — the
build never deletes `.dist-info`.

- **Package list:** `licenses\PYTHON-PACKAGES.txt` in the package (one
  `name==version` line per installed distribution, generated at build time from
  the dist-info metadata).
- **Licenses to expect:** MIT, BSD-2/3-Clause, Apache-2.0, LGPL-3.0 and PSF —
  most of them require the copyright and license text to travel with the code,
  which is why `.dist-info` is kept.
- **Not the same as the developer checkout:** `pipeline/requirements.txt` is for
  a developer machine; the ZIP is built from
  `pipeline/requirements-portable.txt` (no PyTorch / openai-whisper) and the
  list above is the actual installed set.

## Microsoft Visual C++ runtime

`<pkg>\python\` contains the Microsoft Visual C++ runtime DLLs
(`msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `concrt140.dll`,
`vcruntime140.dll`, `vcruntime140_1.dll`). They are copied **unmodified** from a
Windows installation so the bundled `ctranslate2` / `av` / `onnxruntime` native
modules can load on a machine without the VC++ redistributable installed
(app-local deployment).

- **License:** Microsoft's redistributable terms for the Visual C++ runtime;
  the files are not modified, renamed in a misleading way, or relicensed.
- **Where they come from:** `%SystemRoot%\System32` on the build machine
  (see `packaging\build_portable.py --vcruntime-dir`).
- If you replace them, keep Microsoft's terms and keep them unmodified.

## Not covered here

Third-party **assets checked into this repository** (the font and the ECharts
bundle above) are listed with their own sections. Third-party **binaries that
ship only inside the portable package** are covered by the sections above and by
the `licenses\` folder of that package; they are not part of the repository's
source tree.
