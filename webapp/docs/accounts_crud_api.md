# 对标账号 CRUD 接口契约

> 适用：TikTok 对标账号工作台（FastAPI，`webapp/app.py`）
> 数据源：`pipeline/accounts.json`（唯一配置源）
> 版本：2026-10-10

---

## 一、数据模型

### 1.1 可写字段（8 个）

| 字段 | 类型 | 必填 | 缺省 | 校验规则 |
|---|---|---|---|---|
| `account` | string | ✅ | — | **主键**。`^[a-z0-9_.]+$`；`"对标记录-" + name` ≤ 31 字符（Excel sheet 名上限）；不含 `[ ] : * ? / \`；**创建后只读** |
| `lang` | string | ✗ | `en` | 枚举 `en` / `es` / `zh`。英文账号必须 `en`（`auto`/`zh` 会产生伪中文） |
| `nickname` | string | ✗ | `""` | 展示名，可空 |
| `tags` | array‹string› | ✗ | `[]` | 入参**也接受逗号分隔字符串**，服务端拆分为数组 |
| `note` | string | ✗ | `""` | 备注，可空 |
| `out_dir` | string | ✗ | `pipeline/output` | 抓取产物目录 |
| `audio_dir` | string | ✗ | `output/{account}_audio` | 音频目录 |
| `active` | boolean | ✗ | `true` | 参与流水线开关；`false` 时被 `load_accounts` / `daily_update` 过滤 |

### 1.2 只读派生字段（响应中返回，不可写）

| 字段 | 来源 | 含义 |
|---|---|---|
| `health` | 落点齐备度 + 新鲜度 | `ok` / `stale` / `partial` / `config_only` / `off` |
| `health_label` | 上项中文名 | `正常` / `陈旧` / `缺件` / `仅配置，无产物` / `已停用` |
| `missing` | 缺失落点 | `["scrape","audio","sheet"]` 的子集 |
| `suggested_actions` | 缺口推导 | `["s2"]` / `["s1"]` / `["transcribe"]` / `["all"]` |
| `video_count` / `audio_count` / `transcript_count` | 产物扫描 | 抓取条数 / 音频数 / 转写数 |
| `scrape_items` / `scrape_errors` | `scrape_{account}.json` | 成功条数 / 失败条数 |
| `products_count` | `{account}_products.json` | 有商品锚点的**视频数**（非商品条数） |
| `ledger_rows` / `ledger_covered` | 台账 sheet | 台账总行数 / 抓取视频中已入账的条数 |
| `gaps` | 逐段对账 | `[{level, label, msg}]`，level ∈ `warn` / `error` |
| `rates` / `landing` / `severity` | 各段落地率 | 分项百分比 / 综合落地率 / 汇总等级 |

**对账口径**（`app.py::_account_view`）：

```
采集  scrape_items + scrape_errors == audio_count
转写  transcript_count == audio_count
商品  products_count >= scrape_items
入账  抓取的 video_id 均已出现在台账 F 列（覆盖判定，非行数相等）
```

---

## 二、接口清单

| # | 方法 | 路径 | 用途 | 成功码 |
|---|---|---|---|---|
| 1 | GET | `/api/accounts` | 查列表（支持筛选） | 200 |
| 2 | GET | `/api/accounts/{name}` | 查详情 | 200 |
| 3 | POST | `/api/accounts` | 新增 | 200 |
| 4 | PATCH | `/api/accounts/{name}` | 修改（含 `active` 启停、`media_type` 换类型） | 200 |
| 5 | POST | `/api/accounts/media-type` | 批量设为视频 / 图文 | 200 |
| 6 | DELETE | `/api/accounts/{name}` | 删除（仅空壳） | 200 |

> 前端入口：侧栏「账号管理」（`#view-accounts`）。启停不再有独立 `/toggle` 路由，统一走 `PATCH {"active": false}`。

---

### 1) 查询列表 `GET /api/accounts`

**Query**

| 参数 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `q` | string | `""` | 账号 / 昵称 / 标签 / 分组名 子串匹配（不区分大小写） |
| `status` | `active` \| `inactive` | `""` | 按配置启停过滤 |
| `media_type` | `video` \| `image` | `""` | 按账号类型过滤 |
| `group` | string | `""` | 按分组 id 过滤 |

**Response 200**

```jsonc
{
  "total": 45,          // 全量条数（不受筛选影响）
  "shown": 12,          // 本次筛选后条数
  "counts": { "all": 45, "active": 45, "inactive": 0, "video": 30, "image": 15, "artifacts": 45 },
  "accounts": [
    {
      "account": "baliee_official",
      "nickname": "Baliee", "region": "de", "lang": "de", "tags": ["德国AI对标"],
      "note": "", "active": true, "media_type": "video",
      "out_dir": "", "audio_dir": "",
      "audio_count": 29, "transcript_count": 29, "scrape_items": 29, "scrape_errors": 0,
      "products_count": 29, "ledger_rows": 29, "last_success_date": "2026-10-18",
      "severity": "ok", "landing": 100,
      "has_artifacts": true,            // 有产物 → 不可删除
      "group_id": "g1", "group_name": "钟如妹账号"
    }
  ]
}
```

**性能**：统计复用 `/api/overview` 的 5s 缓存，不额外扫盘（45 账号约 20 KB / 30 ms）。

**排序**：停用沉底 → `severity` 异常优先 → 账号名字典序。

---

### 2) 查询详情 `GET /api/accounts/{name}`

**Path**：`name` — 账号名。

**Response 200**：列表项的全字段，**外加**

| 字段 | 说明 |
|---|---|
| `steps` | 各流水线步骤状态 `{s1_download: {label, ok, cost_s, detail, agent_side}}` |
| `editable_fields` | 可写字段名清单（`config.EDITABLE_FIELDS`） |
| `field_schema` | **字段自描述**（类型/必填/只读/正则/枚举/缺省），供前端渲染表单 |

**异常**

| 码 | 条件 | detail |
|---|---|---|
| 404 | 账号不存在 | `账号不存在：{name}` |

> 注：详情不存在返回 **404**（资源缺失），而写操作的不存在返回 **400**（业务冲突）——见第四节。

---

### 3) 新增 `POST /api/accounts`

**Body**

```jsonc
{
  "account": "example_account_047",     // 必填
  "lang": "en",
  "nickname": "我的号",
  "tags": "自己账号,美区",     // 字符串或数组皆可
  "note": "",
  "out_dir": "",              // 空串=用缺省
  "audio_dir": "",
  "active": true,
  "media_type": "video"       // video | image（image 会补「图文账号」标签并跳过滤音/转写）
}
```

**Response 200**：`{"ok": true, "account": { /* 写入后的完整行 */ }}`

**行为**：缺省字段由 `default_account()` 按主流水线约定生成；`out_dir` 缺省 = `pipeline/output`，`audio_dir` 缺省 = `output/{account}_audio`。

**异常**

| 码 | 条件 | detail 示例 |
|---|---|---|
| 400 | 账号名为空 | `账号名不能为空` |
| 400 | 含非法字符 | `账号名只能含小写字母、数字、下划线、点（与 TikTok 用户名一致）` |
| 400 | 超出长度 | `账号名过长：对标记录-{name} 超过 Excel sheet 名 31 字符上限` |
| 400 | 含 Excel 非法字符 | `账号名不能含 []:*?/\ 等 Excel sheet 非法字符` |
| 400 | 重名 | `账号已存在：{name}` |
| 500 | 写盘失败 | `写 accounts.json 失败: {e}` |

---

### 4) 修改 `PATCH /api/accounts/{name}`

**Body**：`1.1` 中任意可写字段（**按需传，未传的不动**）。

**Response 200**：`{"ok": true, "account": { /* 修改后的完整行 */ }}`

**语义**：**部分更新**（不是整体替换）。
- `tags` 传字符串会自动拆分为数组；传数组则原样使用。
- `account` 是主键，**试图修改直接 400**（如需改名：新建 + 停用旧）。
- `media_type` 可在此一并改：内部走 `config.set_media_type()`（补/移「图文账号」标签 + 失效台账缓存），与批量接口同语义。

**异常**

| 码 | 条件 | detail |
|---|---|---|
| 400 | 试图改主键 | `account 是主键，不可修改；如需改名请新建账号` |
| 400 | 账号不存在 | `账号不存在：{name}` |
| 400 | 名称非法 | 同新增 |
| 500 | 写盘失败 | `写 accounts.json 失败: {e}` |

---

### 5) 批量类型 `POST /api/accounts/media-type`

**Body**：`{"accounts": ["a","b"], "media_type": "image"}`

**Response 200**：`{"ok": true, "saved": [...], "same": [...], "not_found": [...], "invalid": [...]}`（逐条结果，不因单条失败中断）

**行为**：`image` 写 `media_type="image"` 并补「图文账号」标签，流水线跳过音频/转写、改走图文枚举；
`video` 移除该字段与标签。只改配置，不动台账 sheet 与任何产物。

**启停**：不再提供 `/toggle` 路由，等价调用 `PATCH /api/accounts/{name}` + `{"active": false}`。

---

### 6) 删除 `DELETE /api/accounts/{name}`

**Response 200**：`{"ok": true, "deleted": "{name}", "group_cleaned": true}`

`group_cleaned`：是否顺带清掉了 `data/account_groups.json` 里该账号的历史成员记录（幂等；
清理失败不影响删除结果，只在响应里标 `false`，服务端日志留痕）。

**⚠️ 安全约束（核心设计）**：**仅允许删除「无任何产物」的空壳账号**。
只要有抓取 / 音频 / 台账 sheet 中任意一项，就拒绝删除。

**异常**

| 码 | 条件 | detail |
|---|---|---|
| 400 | 账号不存在 | `账号不存在：{name}` |
| 400 | 已有产物 | `{name} 已有产物（抓取/音频），请改用「停用」保留历史索引` |

> **为什么不允许删有产物的账号**：台账与音频是**历史索引**，删除配置会让这些产物变成「孤儿」，
> 后续按账号名反查全部失效。保留历史应该用**停用**（`active=false`），而不是删除。

---

## 三、校验规则汇总

| 规则 | 实现 | 违反时 |
|---|---|---|
| 名称非空 | `validate_account_name` | 400 |
| 名称字符集 `^[a-z0-9_.]+$` | 同上 | 400 |
| 名称长度（含 `对标记录-` 前缀 ≤ 31） | 同上 | 400 |
| 禁止 `[]:*?/\` | 同上 | 400 |
| 主键唯一 | `add_account` 查重 | 400 |
| 主键只读 | `edit_account` 拦截 | 400 |
| 删除需无产物 | `remove_account` + `account_artifacts` | 400 |
| `tags` 类型归一 | 字符串 → 数组 | — |
| `active` 缺省 `true` | `_account_norm` | — |

**`active` 缺省语义（高危坑）**：`accounts.json` 中**没有** `active` 字段的账号视为**启用**。
否则历史配置会被静默判为停用、流水线不跑。

---

## 四、状态码约定

| 码 | 用于 | 例子 |
|---|---|---|
| **200** | 成功 | 所有 CRUD 成功 |
| **400** | **参数非法 / 业务冲突** | 名称非法、重名、改主键、改/删不存在的账号、删有产物 |
| **403** | **来源不是本机** | 写接口被局域网 IP 调用 |
| **404** | **查询目标资源不存在** | `GET /api/accounts/{name}` 未命中 |
| **500** | 服务端故障 | 写 `accounts.json` 失败 |

> 400 与 404 的边界：**读操作**用 404（目标不存在）；**写操作**统一用 400（请求语义有误）。

**权限模型（本地工作台的最小边界）**

- 本工作台是单机工具，没有账号体系；写接口只接受**回环地址**（`127.0.0.1` / `::1` / `localhost`），
  其他来源一律 **403**（`_guard_local_write`）。这样即使把 `uvicorn` 改绑到 `0.0.0.0`，局域网也无法改配置。
- 破坏性操作在 UI 侧再加一层：**删除**只对 `has_artifacts=false` 的空壳开放，
  有产物的账号只给「停用」，且删除前二次确认（确认文案里写明"不删任何文件"）。
- 所有写操作串行化（`_ACCOUNT_WRITE_LOCK`），避免读-改-写互相覆盖。

---

## 五、并发与一致性

| 机制 | 说明 |
|---|---|
| 原子写 | `tmp` + `os.replace()`，避免流水线读到半个文件 |
| 写串行 | `_ACCOUNT_WRITE_LOCK`（新增/修改/删类型/删除共用一把锁） |
| 缓存 | `/api/overview` 5s；台账总览 60s；前端账号列表本地 15s |
| 缓存失效 | 任何写操作后调用 `_bust_accounts_cache()` 清空 `_accounts_cache` 与 `_overview_cache`；改 `media_type` 额外失效台账缓存 |
| 幂等 | 新增按主键查重；修改为部分更新；删除按主键定位 |

**已知边界**：账号级 `out_dir` / `audio_dir` 的生效取决于调用方——
`daily_update.py` 显式传 `--audio-dir/--out-dir`（生效），
工作台「运行」按钮不传（走 `output_root/{account}_audio` 兜底）。

---

## 六、自动化测试

| 测试 | 覆盖 |
|---|---|
| `tests/accounts_write.test.py`（15 项） | **数据层** `config.py`：原子写、主键只读、校验、停用过滤、删除保护 |
| `tests/accounts_admin_ui.test.js`（11 项） | **界面层**：列表/筛选/KPI、新增校验与载荷、编辑 PATCH、删除保护、启停、详情弹窗 |

两套互补，均含**反证验证**（注入缺陷后测试须变红）。接口层属实时行为，验证方式：
`curl :8787/api/accounts` 逐条对照本文档第二节。
