# 科研成果附件系统 — 导出 API 文档

版本：v1  
更新日期：2026-09-01  
权限：只读。不能构建、上传、改框、重新装订。  
鉴权：API Key（与网页登录账号无关）

本文档给对接方使用。密钥由本系统管理员线下提供，不要写入公开仓库。

---

## 1. 基本信息

| 项 | 值 |
|----|----|
| 协议 | HTTP |
| 方法 | 仅 GET |
| 编码 | UTF-8 |
| JSON | `Content-Type: application/json` |
| 文件下载 | 二进制流，见各接口 |
| 前缀 | `/api/v1` |

**Base URL**

| 场景 | 地址 |
|------|------|
| 本机 | `http://127.0.0.1:8000` |
| 局域网（当前） | `http://192.168.2.8:8000` |

完整 URL = Base URL + 路径，例如：

`http://192.168.2.8:8000/api/v1/talents/200050`

在线接口列表（本机打开）：`http://127.0.0.1:8000/docs`

---

## 2. 鉴权

除 `GET /api/v1/health` 外，所有 `/api/v1/*` 必须带密钥。网页 Cookie 无效。

**推荐（请求头）：**

```http
X-Api-Key: <API_KEY>
```

**也可：**

```http
Authorization: Bearer <API_KEY>
```

**调试可用（不推荐生产，密钥会进访问日志）：**

```
GET /api/v1/talents/200050?api_key=<API_KEY>
```

密钥由本系统在 `config.local.yaml` 的 `web.export_api_keys` 配置，或环境变量 `RAS_EXPORT_API_KEY`。对接方只保存管理员发给你的那一串。

可选：服务端可配置 `web.export_allow_ips`。配置后，不在列表中的来源 IP 返回 403。

---

## 3. 约定

- **人才 ID**（`attach_id`）：与汪伦人才库一致，一般为 4–6 位数字，例如 `200050`。也可带前缀 `HJ_200050`，服务端会规范化成纯 ID。
- **装订附件**：每人一份 PDF，文件名为 `{人才ID}.pdf`。
- **相对路径**：JSON 里的 `url`、`pdf_url` 是路径，调用时前面加 Base URL。
- **时间字段**：ISO 8601 字符串，可能为空。
- 本接口 **不会** 因为对方来拉文件而自动开始构建。附件未生成时返回 409，稍后再试。

**文件 `kind`：**

| kind | 含义 |
|------|------|
| `attachment` | 装订后的整本附件 `{人才ID}.pdf` |
| `annotated_pdf` | 单篇标注 PDF（首页红框） |
| `source_pdf` | 单篇原文 PDF |

---

## 4. 推荐对接流程

```
1. GET /api/v1/health          → 确认服务可用
2. GET /api/v1/talents/{id}    → 查此人是否存在、附件是否就绪
3. 若 attachment.ready == true
      GET /api/v1/talents/{id}/attachment  → 保存为 {id}.pdf
4. 若还需要单篇 PDF
      使用返回的 papers[].file_id
      GET /api/v1/files/{file_id}
5. 若 HTTP 409                 → 附件尚未生成，间隔后重试（建议 1–5 分钟）
6. 若 HTTP 404                 → 本系统没有该人才，不要重试同一 ID
```

---

## 5. 接口明细

### 5.1 探活

`GET /api/v1/health`

免密钥。

**成功 `200`**

```json
{
  "ok": true,
  "api": "v1",
  "mode": "export"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | boolean | 服务正常为 `true` |
| `api` | string | 固定 `v1` |
| `mode` | string | 固定 `export` |

---

### 5.2 人才列表

`GET /api/v1/talents`

需要 API Key。

**成功 `200`**

```json
{
  "count": 1,
  "talents": [
    {
      "attach_id": "200050",
      "name": "张三",
      "mode": "",
      "fetched_at": "2026-08-20T10:00:00+08:00",
      "updated_at": "2026-08-21T15:30:00+08:00",
      "attachment": {
        "ready": true,
        "filename": "200050.pdf",
        "file_id": 12,
        "size": 1234567,
        "url": "/api/v1/talents/200050/attachment",
        "sha256": "ab..."
      }
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `count` | int | `talents` 条数 |
| `talents[].attach_id` | string | 人才 ID |
| `talents[].name` | string | 姓名，可能为空 |
| `talents[].mode` | string | 申报模式等，可能为空 |
| `talents[].fetched_at` | string | 档案拉取时间 |
| `talents[].updated_at` | string | 最近更新时间 |
| `talents[].attachment.ready` | boolean | 装订 PDF 是否可下载 |
| `talents[].attachment.url` | string | 下载路径 |
| `talents[].attachment.filename` | string | 建议保存的文件名（就绪时） |
| `talents[].attachment.file_id` | int / null | 文件库 id，磁盘文件可能为 `null` |
| `talents[].attachment.size` | int / null | 字节数 |
| `talents[].attachment.sha256` | string | 校验和，磁盘文件可能为空 |

未就绪时 `attachment` 至少包含：

```json
{ "ready": false, "url": "/api/v1/talents/200050/attachment" }
```

---

### 5.3 人才详情

`GET /api/v1/talents/{attach_id}`

需要 API Key。

路径参数：

| 参数 | 说明 |
|------|------|
| `attach_id` | 人才 ID |

**成功 `200`**

```json
{
  "attach_id": "200050",
  "name": "张三",
  "mode": "",
  "updated_at": "2026-08-21T15:30:00+08:00",
  "attachment": {
    "ready": true,
    "filename": "200050.pdf",
    "file_id": 12,
    "size": 1234567,
    "url": "/api/v1/talents/200050/attachment",
    "sha256": "ab..."
  },
  "papers": [
    {
      "paper_id": "p1",
      "title": "Example title",
      "title_zh": "示例标题",
      "doi": "10.1234/example",
      "year": "2024",
      "journal": "Nature",
      "author_self": "Zhang San",
      "status": "annotated",
      "file_id": 88,
      "pdf_url": "/api/v1/files/88"
    }
  ],
  "files": [
    {
      "file_id": 12,
      "kind": "attachment",
      "filename": "200050.pdf",
      "size": 1234567,
      "doi": "",
      "paper_id": "",
      "url": "/api/v1/files/12"
    },
    {
      "file_id": 88,
      "kind": "annotated_pdf",
      "filename": "p1.pdf",
      "size": 456000,
      "doi": "10.1234/example",
      "paper_id": "p1",
      "url": "/api/v1/files/88"
    }
  ]
}
```

**`papers[]`**

| 字段 | 类型 | 说明 |
|------|------|------|
| `paper_id` | string | 本系统内论文 id |
| `title` | string | 英文题名 |
| `title_zh` | string | 中文题名 |
| `doi` | string | DOI，可能为空 |
| `year` | string | 年份 |
| `journal` | string | 期刊 / 会议 |
| `author_self` | string | 本人姓名 |
| `status` | string | 如 `annotated`、`pdf_missing` |
| `file_id` | int / null | 优先标注 PDF，否则原文；没有则为 `null` |
| `pdf_url` | string | 有 `file_id` 时为 `/api/v1/files/{id}`，否则空字符串 |

**`files[]`**

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_id` | int | 下载用 id |
| `kind` | string | 见第 3 节 |
| `filename` | string | 下载文件名 |
| `size` | int | 字节 |
| `doi` | string | 关联 DOI |
| `paper_id` | string | 关联论文 id |
| `url` | string | `/api/v1/files/{file_id}` |

**失败**

| HTTP | 条件 | 示例 |
|------|------|------|
| 400 | 人才 ID 为空 | `{"detail":"人才 ID 无效"}` |
| 404 | 本系统没有该人 | `{"detail":"没有该人才档案: 200050"}` |

---

### 5.4 下载装订附件

`GET /api/v1/talents/{attach_id}/attachment`

需要 API Key。

**成功 `200`**

- Body：PDF 二进制
- `Content-Type`：`application/pdf` 或 `application/octet-stream`
- `Content-Disposition`：`attachment; filename*=UTF-8''200050.pdf`
- 可能带响应头 `X-SHA256`

请按二进制保存，不要当 JSON 解析。建议保存名为 `{attach_id}.pdf`。

**失败**

| HTTP | 条件 | Body |
|------|------|------|
| 404 | 没有该人才 | `{"detail":"没有该人才档案: 200050"}` |
| 409 | 有档案但附件未生成 | `{"detail":"附件尚未生成","ready":false,"attach_id":"200050"}` |

409 时应对方稍后重试，不要改用其它接口凑一份附件。

---

### 5.5 按 id 下载文件

`GET /api/v1/files/{file_id}`

需要 API Key。

路径参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `file_id` | int | 详情接口返回的 id |

**成功 `200`**：文件二进制。`Content-Disposition` 带文件名；可能带 `X-SHA256`。

**失败 `404`**：`{"detail":"库中没有该文件"}`

`file_id` 以详情接口当时返回值为准，不要写死。

---

## 6. 统一错误

JSON 错误一般为：

```json
{ "detail": "说明文字" }
```

409 额外带 `ready`、`attach_id`。

| HTTP | 含义 | 对接建议 |
|------|------|----------|
| 400 | 参数无效 | 检查人才 ID |
| 401 | 缺少或错误的 API Key | 检查请求头 |
| 403 | IP 不在允许列表 | 联系本系统管理员加白名单 |
| 404 | 人才或文件不存在 | 不要对同一 ID 频繁重试 |
| 409 | 附件尚未生成 | 间隔重试 |
| 503 | 服务端未配置密钥 | 联系本系统管理员 |

401 示例：

```json
{ "detail": "无效或缺失 API Key" }
```

---

## 7. 调用示例

以下 `$key` 换成管理员提供的密钥。

### curl（Windows）

```powershell
$base = "http://192.168.2.8:8000"
$key  = "<API_KEY>"

curl.exe -H "X-Api-Key: $key" "$base/api/v1/health"
curl.exe -H "X-Api-Key: $key" "$base/api/v1/talents"
curl.exe -H "X-Api-Key: $key" "$base/api/v1/talents/200050"
curl.exe -H "X-Api-Key: $key" -L -o "200050.pdf" "$base/api/v1/talents/200050/attachment"
curl.exe -H "X-Api-Key: $key" -L -o "p1.pdf" "$base/api/v1/files/88"
```

### Python

```python
import requests

BASE = "http://192.168.2.8:8000"
HEADERS = {"X-Api-Key": "<API_KEY>"}

r = requests.get(f"{BASE}/api/v1/talents/200050", headers=HEADERS, timeout=30)
r.raise_for_status()
data = r.json()

if data["attachment"]["ready"]:
    pdf = requests.get(f"{BASE}{data['attachment']['url']}", headers=HEADERS, timeout=120)
    pdf.raise_for_status()
    with open(f"{data['attach_id']}.pdf", "wb") as f:
        f.write(pdf.content)
```

---

## 8. 范围说明

本 API **提供**：按人才 ID 查询、下载装订 PDF、按 `file_id` 下载库内文件。

本 API **不提供**：登录网页、上传清单、检索作者、启动构建、改红框、重新装订。这些属于本系统内部网页，不对对接方开放。
