# 外部只读 API 使用说明（v1）

发给内网调用方即可。本文只写**当前已上线**的能力。  
内部人才库 / 企业库页面接口、审核后台，调用方不必读。

响应样例（脱敏后的白名单字段）：

- `docs/examples/external-read-talent.example.json`（默认脱敏；手机按 11 位号 `13800000100` 掩成 `*******0100`）
- `docs/examples/external-read-enterprise.example.json`

---

## 0. 接入前先拿到这些

向本系统管理员要：

| 项 | 说明 |
|---|---|
| 基址 | 后端 HTTP，默认 `http://<内网主机>:8087`（不是前端端口） |
| API Key | 形如 `sk_…`。管理员创建后**只显示一次**，请自行保管 |
| 权限范围 | `pool.read` 或 `pool.read.pii` 均可读；明文还要 `pool.read.pii` |
| 允许的类型 / 模板 | `talent` / `enterprise`；模板 `QM`（启明）和/或 `HJ`（火炬）。空白名单 = 两类、两模板都可 |
| IP 白名单（可选） | 配了就必须从白名单地址发请求。`pool.read.pii` 客户端通常会强制配白名单 |

**前缀：** `/api/external-read/v1`

本接口只读。没有写入、删除、归档、导入批次的能力。

环境开关 **`EXTERNAL_READ_ENABLED` 必须恰好是 `1`** 才会开这组路由。  
`true` / `yes` / `on` / 非零数字 / 未设置都算关。默认关。关掉时下面所有路径（含探活）一律 **404** `NOT_FOUND`，不看有没有带 Key。

---

## 1. 当前能读什么、不能读什么

| 能 | 不能 |
|---|---|
| `GET` 人才列表 / 单条 | 任何 POST / PUT / PATCH / DELETE |
| `GET` 企业列表 / 单条 | 调内部 `/api/talent-pool`、`/api/enterprise-pool`（那是本系统页面用的，外部 Key 无效） |
| `GET` 探活 | 把浏览器打开后端 `/docs`（Swagger）当调用合同：那里会列出全站内部接口 |
| 按 `attach_id`（人才）或 `credit_code`（企业）精确筛 | 查询参数 `id_number=`（没有这个参数） |
| `updated_since` 增量（见 §4） | HMAC 签名读库（v1 读路径**禁止** hmac；`auth_mode` 不是 `api_key` 会 403） |
| | 用只有 `import.write`、或 scopes 为空的导入 Key 读生产库 |

空 scopes 的导入客户端在服务端会被当成 `import.write`，**没有** `pool.read`，读生产会 **403** `SCOPE_DENIED`。请管理员单独开读权限。

数据从本系统人才库 / 企业库投影后返回：列表是白名单扁平字段；详情多一段 `payload`（库内中文键，按存储原样）。三类字段处理见 §6。

---

## 2. 鉴权

每个请求带 Key，二选一：

```http
X-API-KEY: sk_xxxxxxxx
```

或

```http
Authorization: Bearer sk_xxxxxxxx
```

**不要**用 `X-APP-ID`。只带 `X-APP-ID`、不带 Key → **401** `INVALID_API_KEY`。

v1 读路径是纯 API Key。不要带 HMAC 头，也不要用 `auth_mode=hmac` / `api_key_or_hmac` 的客户端来调：即使用对了 Key，也会 **403** `SCOPE_DENIED`。

管理员若给该客户端配了 **IP 白名单**，请求必须来自白名单地址（或 CIDR）。空白名单 = 不限 IP。

探活：

| 接口 | 鉴权 | 用途 |
|---|---|---|
| `GET /api/health` | 无 | 服务是否活着（不是本接口） |
| `GET /api/external-read/v1/health` | 要 Key | Key / IP / 读权限是否有效 |

```bash
curl -s -H "X-API-KEY: sk_xxx" http://<host>:8087/api/external-read/v1/health
# {"success":true,"app_id":"intranet_dev","status":"ok","scopes":["pool.read"]}
```

失败时 body 为：`{"detail":{"code":"...","message":"..."}}`。

限流：读客户端默认 **60 次/分钟**（按 UTC 分钟窗口计，与导入客户端共用同一计数器）。读路径硬顶 **120**。超了 **429** `RATE_LIMIT`，`Retry-After: 60`。探活也计入。

---

## 3. 接口一览

全部 `GET`，前缀 `/api/external-read/v1`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 探活：Key 是否有效 |
| GET | `/talents` | 人才列表 |
| GET | `/talents/{talent_id}` | 人才详情（含 `payload`、`homepage`） |
| GET | `/enterprises` | 企业列表 |
| GET | `/enterprises/{enterprise_id}` | 企业详情（含 `payload`） |

`{talent_id}` / `{enterprise_id}` 是库内整数主键。不存在或已被过滤掉 → **404** `NOT_FOUND`（不区分「没有」和「你无权看这个模板」之外的细节：无权模板是 403，见下）。

---

## 4. 查询参数（列表）

空字符串会被忽略，等价于没传。

| 参数 | 默认 | 说明 |
|---|---|---|
| `page` | `1` | 从 1 起。`<1` → 422 |
| `page_size` | `20` | **1..100**。`0` 或 `101` → 422 |
| `q` | 空 | 模糊搜。人才：姓名、`attach_id`；有 `pool.read.pii` 时才额外搜证件号 / 邮箱 / 手机。企业：名称、信用代码、简介、地区，可能扫全表，见下 |
| `mode` | 空 | `QM` / `HJ`。省略、空、`all`（大小写不敏感、可带空格）= **不按单一模板过滤**，但仍受该客户端 `allowed_modes` 约束 |
| `attach_id` | 空 | **仅人才**。精确等于申报 ID |
| `credit_code` | 空 | **仅企业**。精确等于统一社会信用代码 |
| `updated_since` | 空 | 带时区的 ISO-8601，例如 `2026-08-01T00:00:00+08:00`。无偏移（`2026-08-01`）或无法解析 → 422 |

没有 `id_number=` 参数。不要靠它筛人。

`mode` 非法（不是空 / all / QM / HJ）→ **422** `INVALID_QUERY`。  
`mode=HJ` 但客户端只允许 QM → **403** `MODE_NOT_ALLOWED`。列表在 `mode=all` 时也只会返回 `allowed_modes` 里的行。

`allowed_targets` 不含 `enterprise` 时，企业两条路由 → **403** `TARGET_NOT_ALLOWED`（人才同理）。空 `allowed_targets` = 两类都允许。

### `updated_since` 时区

库内 `updated_at` 存的是 **`+08:00` 文本**（秒精度）。过滤是 SQL 字符串比较 `updated_at >= updated_since`，**不会**先换算时区。

请传东八区偏移，例如 `2026-08-01T00:00:00+08:00`。传 `Z` / `+00:00` 会按字面量比较，结果不对。

### 企业 `q` 与锁

企业带非空 `q` 时，服务端会在 SQLite 锁内做**全表扫描**（先 SQL `LIKE` 再在内存里按名称/代码/地区过滤），然后才分页。不要高频轮询带 `q` 的企业列表。增量请用 `updated_since` + `page`。

---

## 5. 成功信封（扁平，没有 `data` 包一层）

### 探活

```json
{
  "success": true,
  "app_id": "intranet_dev",
  "status": "ok",
  "scopes": ["pool.read"]
}
```

### 列表（人才、企业相同外壳）

```json
{
  "success": true,
  "total": 1,
  "page": 1,
  "page_size": 20,
  "mode": "all",
  "items": []
}
```

`mode` 回显：请求了 `QM`/`HJ` 则原样大写；省略 / 空 / `all` 则回 `"all"`。

### 详情

```json
{
  "success": true,
  "item": {}
}
```

HTTP 200。业务失败不会用 `{success:false}`，一律走 §7 的 `detail.code`。

---

## 6. 字段与脱敏

`payload` 里是**库内中文键**，按存储原样返回（不会改成英文别名）。列表项没有完整 `payload`。

### 人才列表项（白名单）

`id`、`attach_id`、`name`、`mode`、`source_year`、`profile_summary`、`completeness`、`created_at`、`updated_at`、`google_scholar_url`、`linkedin_url`、`id_number_masked`、`email_masked`、`phone_masked`。

默认**没有** `id_number` / `email` / `phone`。只有客户端带 `pool.read.pii` 时，列表/详情顶层才会多这三项明文。

### 人才详情

列表字段 +：

- `payload`：业务 JSON（脱敏规则见下）
- `homepage`：`{"google_scholar":{"url":"..."},"linkedin":{"url":"..."}}`（只有 url）

### 企业列表项（白名单）

`id`、`credit_code`、`company_name`、`intro_summary`、`mode`、`completeness`、`created_at`、`updated_at`、`region_text`、`region`。

`region` 固定为 `{"province":"","city":"","district":""}`。

### 企业详情

列表字段 + `payload`。

### 三类字段

| 类 | 例子 | 默认（仅 `pool.read`） | 有 `pool.read.pii` |
|---|---|---|---|
| 1 直接标识 | 证件号码 / 护照号 / 电子邮件 / 邮箱 / 手机号 / 电话 等 | 证件掩成 `E******7`，邮箱 `j***@x.edu`，手机保留末 4 位 | 原文 |
| 2 敏感 | 出生日期、家庭住址、现居住地、联系人电话、银行卡 / 银行账号 等 | 电话类按手机规则掩；其余 `***` | 原文 |
| 3 永不放出 | **`学术主页` 整棵**、`last_job_id`、`edit_notes`、`policy_match`、`source_task_id`、`homepage_lookup_*` | **删除该键** | **仍然删除** |

第 3 类即使有 `pool.read.pii` 也不会出现在响应里。人才学术主页请看顶层 `google_scholar_url` / `linkedin_url` 和详情 `homepage.*.url`。

默认掩码规则（类 1）：

- 证件：保留首尾各 1 位，中间 `*`；长度 ≤4 则全 `*`
- 邮箱：本地部分保留首 1 位 + `***@域名`
- 手机：保留末 4 位，前面 `*`；长度 ≤4 则原样

---

## 7. 错误码

本文列出的 `detail.code` 均为此形：

```json
{"detail":{"code":"NOT_FOUND","message":"not found"}}
```

路径/查询类型校验（例如 `{talent_id}` 非整数、`page=foo`）仍可能是 FastAPI 默认 422，`detail` 为 `{loc,msg,type}` 列表。

| HTTP | `detail.code` | 何时 |
|---|---|---|
| 404 | `NOT_FOUND` | 读开关关闭；或该 id 不存在 |
| 401 | `INVALID_API_KEY` | Key 错或缺失（含只带 `X-APP-ID`） |
| 403 | `CLIENT_DISABLED` | 客户端停用或过期 |
| 403 | `IP_DENIED` | 不在白名单 |
| 403 | `SCOPE_DENIED` | 没有 `pool.read` / `pool.read.pii`；或该客户端不是纯 `api_key`（含 hmac） |
| 403 | `TARGET_NOT_ALLOWED` | 未授权人才或企业 |
| 403 | `MODE_NOT_ALLOWED` | 请求的模板，或不属于你的那条记录的模板，不在 `allowed_modes` |
| 429 | `RATE_LIMIT` | 超 QPS，看 `Retry-After` |
| 422 | `INVALID_QUERY` | `page` / `page_size` / `mode` / `updated_since` 不合法 |
| 503 | `UNAVAILABLE` | 鉴权库暂时不可用 |

公开 `GET /api/health` 与本探活不是同一个：读探活必须带 Key。开关关闭时连探活也是 404，不要据此当「Key 无效」。

---

## 8. curl / 样例

```bash
BASE=http://<host>:8087
KEY=sk_xxx

curl -s -H "X-API-KEY: $KEY" $BASE/api/external-read/v1/health

curl -s -H "X-API-KEY: $KEY" \
  "$BASE/api/external-read/v1/talents?page=1&page_size=20&mode=all"

curl -s -H "X-API-KEY: $KEY" \
  "$BASE/api/external-read/v1/talents?attach_id=55441"

curl -s -H "X-API-KEY: $KEY" \
  "$BASE/api/external-read/v1/talents?updated_since=2026-08-01T00:00:00%2B08:00"

curl -s -H "X-API-KEY: $KEY" $BASE/api/external-read/v1/talents/1

curl -s -H "X-API-KEY: $KEY" \
  "$BASE/api/external-read/v1/enterprises?page=1&page_size=20&credit_code=91320000MA1XXXXXX1"

curl -s -H "X-API-KEY: $KEY" $BASE/api/external-read/v1/enterprises/1
```

```python
import json, urllib.request

BASE = "http://<host>:8087"
KEY = "sk_xxx"

def call(path):
    req = urllib.request.Request(
        BASE + path,
        method="GET",
        headers={"X-API-KEY": KEY},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

print(call("/api/external-read/v1/health"))
print(call("/api/external-read/v1/talents?page=1&page_size=20"))
print(call("/api/external-read/v1/talents/1"))
print(call("/api/external-read/v1/enterprises?page=1&page_size=20"))
```

列表/详情字段形状见：

- `docs/examples/external-read-talent.example.json`（默认脱敏，无 `pool.read.pii`；手机按 11 位号 `13800000100` 掩成 `*******0100`）
- `docs/examples/external-read-enterprise.example.json`

---

## 9. 调用方不要做的事

- 不要调 `/api/talent-pool`、`/api/enterprise-pool`：内部页面接口，外部 Key 无效。
- 不要把后端 `/docs` 当合同。
- 不要用导入专用 Key（空 scopes 或只有 `import.write`）读生产。
- 不要带 `X-APP-ID`，不要对读接口做 HMAC。
- 不要传 `id_number=`；没有这个查询参数。
- 不要对企业列表带 `q` 做高频轮询：可能长时间占住 SQLite 锁。
- 不要假设 `payload.学术主页` 会返回：第 3 类永不放出。
- 不要把 `updated_since=2026-08-01`（无时区）当合法增量条件。

需要 Key、IP 白名单、`pool.read` / `pool.read.pii` 或提高限额：找本系统管理员配置。读路径限额最高 120 次/分钟。
