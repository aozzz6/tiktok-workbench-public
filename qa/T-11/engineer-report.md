# T-11 工程师报告（crew-engineer-1 原文落盘）

> 落盘原因：T-11 的 DoD 第 1 条要求「报告贴出改前真实红与改后真实绿两段输出」，而 role 的报告只活在会话消息里。
> 会话结束后消息就没了，所以 PM 把它**逐字**落盘在这里。落盘时间 2026-11-05，落盘人 PM（lead）。
> 注意：这是「当时那一轮」的证据快照。两段红用的是当时的代码，事后**不能重跑再现**（绿段可以）。
> 本文件由工程师按要求只写这一个文件，未提交 git。

已认领并按 T-11 行 DoD 完成，共享任务 `task-1` 已 complete（revision 5）。

## 状态

**status: done**，三条 DoD 全部满足。

## 改动文件（只动了清单内 3 个）

- `webapp/static/bench2/derive.js`（+13/-3）：无 series 时 `last7Cut=''`，日期不可解析也跳过 `toISOString()`；返回字段形状与有数据时行为不变。
- `webapp/static/bench2/bench.js`（+18/-3）：`trend()` 空 series 提前返回「暂无发布数据」区块；`topvideos()` 空列表不再读 `list[0].plays`，正文给「暂无视频数据」。
- `webapp/tests/bench_empty_data.test.js`（新建，jsdom 加载真实 derive.js + bcore.js + bench.js）。

## 1. 改前真实红（两段，均为真实输出）

第一段（derive.js 未修，用例 1-3）：

`cd D:\tiktok-workbench\webapp; node tests\bench_empty_data.test.js` → `通过 4 / 13`，9 条 `FAIL ... -> Invalid time value`，`EXITCODE=1`。

```
== 用例 1：空载荷（accounts: []、videos: []）不抛异常，返回结构齐全 ==
  FAIL derive(空) 不抛 RangeError: Invalid time value -> Invalid time value
  FAIL KPI 原样带出（kpis 对象仍在） -> Invalid time value
  FAIL 账号行是空数组（accounts 仍在） -> Invalid time value
  FAIL 趋势桶是空数组（series 仍在） -> Invalid time value
  FAIL 7 日切点相关字段齐备：meta.from/to/days + 视频列表 -> Invalid time value
  FAIL 其余渲染依赖字段都在：baselines / patterns / takeaways -> Invalid time value
  FAIL asOf 是合法的时间串 -> Invalid time value
== 用例 2：有账号配置但 0 视频，同样不抛异常，账号行 recent7 = 0 ==
  FAIL derive(有账号、无视频) 不抛异常 -> Invalid time value
  FAIL 账号行仍产出 1 行，recent7 = 0，rank = 1 -> Invalid time value
== 用例 3：有数据时 7 日切点口径不变（只算 lastDay 前 7 天）==
  PASS 有数据时 derive 正常，series 按天聚合 4 个桶
  PASS meta.from/to 仍是最早 / 最晚一天
  PASS 7 日窗口：10-27 与 11-01 计入，10-20 / 10-22 不计入（recent7 = 2）
  PASS 有数据时返回结构与空数据一致（KPI / 账号行 / 趋势桶齐全）

通过 4 / 13
EXITCODE=1
```

第二段（derive.js 已修、bench.js 未修，新增用例 4）：

`通过 14 / 16`，`FAIL BU.trend(空数据) ... -> Cannot read properties of undefined (reading 'date')`、`FAIL BU.topvideos(空数据) ... -> Cannot read properties of undefined (reading 'plays')`，`EXITCODE=1`。有数据的第 3 条 PASS，证明既有口径没被破坏。

```
== 用例 4：bench.js 渲染单元在空数据下不抛异常（T-11 追加 2026-11-05）==
  FAIL BU.trend(空数据) 不抛 TypeError，仍产出 trend 区块 -> Cannot read properties of undefined (reading 'date')
  FAIL BU.topvideos(空数据) 不抛 TypeError，仍产出 topvideos 区块 -> Cannot read properties of undefined (reading 'plays')
  PASS BU.trend / BU.topvideos 在有数据时也正常（口径不变）

通过 14 / 16
EXITCODE=1
```

## 2. 改后真实绿

`cd D:\tiktok-workbench\webapp; node tests\bench_empty_data.test.js` → **`通过 16 / 16`，`EXITCODE=0`**（16 条：derive 空 / 有账号无视频 / 7 日窗口口径 / trend / topvideos 空+有数据）。

```
== 用例 1：空载荷（accounts: []、videos: []）不抛异常，返回结构齐全 ==
  PASS derive(空) 不抛 RangeError: Invalid time value
  PASS KPI 原样带出（kpis 对象仍在）
  PASS 账号行是空数组（accounts 仍在）
  PASS 趋势桶是空数组（series 仍在）
  PASS 7 日切点相关字段齐备：meta.from/to/days + 视频列表
  PASS 其余渲染依赖字段都在：baselines / patterns / takeaways
  PASS asOf 是合法的时间串
== 用例 2：有账号配置但 0 视频，同样不抛异常，账号行 recent7 = 0 ==
  PASS derive(有账号、无视频) 不抛异常
  PASS 账号行仍产出 1 行，recent7 = 0，rank = 1
== 用例 3：有数据时 7 日切点口径不变（只算 lastDay 前 7 天）==
  PASS 有数据时 derive 正常，series 按天聚合 4 个桶
  PASS meta.from/to 仍是最早 / 最晚一天
  PASS 7 日窗口：10-27 与 11-01 计入，10-20 / 10-22 不计入（recent7 = 2）
  PASS 有数据时返回结构与空数据一致（KPI / 账号行 / 趋势桶齐全）
== 用例 4：bench.js 渲染单元在空数据下不抛异常（T-11 追加 2026-11-05）==
  PASS BU.trend(空数据) 不抛 TypeError，仍产出 trend 区块
  PASS BU.topvideos(空数据) 不抛 TypeError，仍产出 topvideos 区块
  PASS BU.trend / BU.topvideos 在有数据时也正常（口径不变）

通过 16 / 16
UNIT_EXITCODE=0
```

## 3. 浏览器实测

playwright-core + chromium-1248，页面 `http://127.0.0.1:8789/#bench`（服务未重启），空数据下：

```
URL: http://127.0.0.1:8789/#bench
has #bench-s096 host: true
has shadow root: true
#b96-load hidden: true | err class: false
#b96-load tip: "正在读取 /api/benchmark …"
shadow text has "Invalid time value": false
shadow text has "读取 /api/benchmark 失败": false
empty-state markers found: ["暂无发布数据","暂无视频数据","暂无","0 个账号"]
account count chip: "全站 · 0 个账号"
has reading 'date' crash text: false
has reading 'plays' crash text: false
rendered units (10): ["kpi","accbars","trend","patterns","acctable","videos","products","topvideos","takeaways","foot"]
visible text head (200): "/* ============================================================ 总览看板 100 方案 · 基座样式 12 套配色令牌 + 密度/动效修饰 + 通用组件（KPI/面板/表格/阶段条/抽屉） 布局由 arch-*.css 负责，这里只管「料」不管「摆」 =========================================="
pageerror: []
console.error: ["Failed to load resource: the server responded with a status of 404 (Not Found)","Error: <line> attribute x1: Expected length, \"NaN\".","Error: <line> attribute x2: Expected length, \"NaN\"."]

RESULT: PASS
BROWSER_EXITCODE=0
```

## 4. 未满足的 DoD 项

无。

## 5. 两点如实说明（不影响 DoD，供 PM 决定是否记进 QA gap）

1. 空态下控制台仍有 2 条不可见警告：`<line> attribute x1/x2: Expected length, "NaN"`（0 账号散点图的坐标轴几何，`bcore.js scatter`；不抛错、不显示、10 区块正常）。另有 1 条 `404`（静态资源，改前就有）。
2. `webapp/static/bench2/data-live.js:59-62` 有一份完全相同的旧 `new Date(''+…)` 代码，但 `index.html` 不加载它（旧适配层），本次未动。

## 6. 约束核对

- 未执行任何 git 写命令；未新增依赖；未改用户数据；未重启 8789。
- 编辑时 bench.js 的 UTF-8 BOM 被工具剥掉，已用字节写回恢复；最终 diff 只剩两处守卫（`git diff --stat`：bench.js 18 insertions, 3 deletions，derive.js 13 insertions, 3 deletions）。
- 新建的测试文件是 `webapp/tests/bench_empty_data.test.js`，未改任何其它测试。
