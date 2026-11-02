# Third-party notices

This repository **as a whole** is released under the MIT license — see `LICENSE`.
That license does **not** extend to the third-party assets listed below. Each of
them remains under its own license, and those licenses govern those files.

| Asset | Path | License | Copyright holder | License text |
|---|---|---|---|---|
| DM Sans (Latin subset, WOFF2) | `webapp/static/fonts/dm-sans-latin.woff2` | SIL Open Font License 1.1 (`OFL-1.1`) | The DM Sans Project Authors (Google Fonts) — **exact wording still to be confirmed, see the TODO below** | <https://openfontlicense.org/> |
| Apache ECharts (minified browser bundle) | `pipeline/assets/echarts.min.js` | Apache License 2.0 | The Apache Software Foundation | <https://www.apache.org/licenses/LICENSE-2.0> |

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

## Not covered here

This file lists the third-party **assets that ship inside the repository**. The
Python packages listed in `pipeline/requirements.txt` are not redistributed with
this repository: `pip` downloads them on the user's machine and each of them keeps
its own license.
