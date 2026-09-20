# 申报书智能修改系统 — 开放 API

版本：v1.0  
鉴权：API Key（与网页登录账号无关）  
前缀：`/api/v1`

对接方只保存管理员在后台「开放 API」发放的密钥。**不要把密钥写入公开仓库或前端页面。**

---

## 1. 基本信息

| 项 | 值 |
|----|------|
| 协议 | HTTP |
| 编码 | UTF-8 |
| JSON | `Content-Type: application/json` |
| 上传 | `multipart/form-data`（推荐）或 JSON + Base64 |
| 前缀 | `/api/v1` |
| 单文件上限 | 40MB |
| 频率 | 每把密钥约 120 次 / 分钟 |

完整 URL = Base URL + 路径。本机示例：`http://127.0.0.1:3777/api/v1/health`。

健康检查：`GET /api/v1/health`（无需密钥）。带密钥访问 `GET /api/v1` 可查看接口列表。

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

密钥由本系统管理员在 `/admin` →「开放 API」生成或审核用户申请后发放。管理员可在后台密钥列表查看完整 Key。停用或删除后立即失效。

---

## 3. 任务状态

| status | 含义 |
|--------|------|
| `queued` | 已入队 |
| `running` | 预处理 / 出计划 / 写入中 |
| `planned` | 计划已生成，等待确认写入 |
| `done` | 已写入，可下载产出 |
| `failed` | 失败，可 `POST .../retry` |

创建时传 `autoApply=true`（或 JSON `autoApply`），计划生成后自动按 Gemini 意见写入，成功则直接到 `done`。

---

## 4. 创建任务

`POST /api/v1/tasks`

### 4.1 multipart（推荐）

| 字段 | 必填 | 说明 |
|------|------|------|
| `app` | 是 | 申报书文件：`.docx / .docm / .wps / .xlsx / .xlsm / .xls / .pdf` |
| `opinions` | 否 | 修改意见，可多文件（Word / Excel / 图片 / 录音 / txt / md）。不传则从申报书标注栏抽取 |
| `autoApply` | 否 | `true` / `1` 则自动确认写入 |
| `mode` | 否 | `QM` 或 `HJ`，不传则自动识别 |

```bash
curl -X POST "http://127.0.0.1:3777/api/v1/tasks" \
  -H "X-Api-Key: YOUR_KEY" \
  -F "app=@申报书.docx" \
  -F "opinions=@意见.docx" \
  -F "autoApply=true"
```

成功：

```json
{ "ok": true, "id": "…", "status": "queued", "autoApply": true }
```

### 4.2 JSON

```json
{
  "app": { "name": "申报书.docx", "dataB64": "<base64>", "mode": "HJ" },
  "opinions": [{ "name": "意见.txt", "dataB64": "<base64>" }],
  "autoApply": false
}
```

---

## 5. 查询与确认

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/tasks` | 本密钥创建的任务列表 |
| GET | `/api/v1/tasks/{id}` | 状态、日志尾、产出清单 |
| GET | `/api/v1/tasks/{id}/plan` | 编辑计划（计划未就绪返回 404） |
| POST | `/api/v1/tasks/{id}/apply` | 确认写入。空 body 或 `{}` 即采用计划中的 Gemini 意见；也可提交 `edits` / `leftovers` |
| POST | `/api/v1/tasks/{id}/retry` | `failed` 或 `planned` 时重试出计划 |
| GET | `/api/v1/tasks/{id}/files?dir=output&name=` | 下载产出（`dir` 为 `output` / `input`） |

轮询建议间隔 2–5 秒，直到 `planned` / `done` / `failed`。

下载成品时 `name` 取任务 JSON 里 `deliverables[].name`。

---

## 6. 错误码

| HTTP | 含义 |
|------|------|
| 401 | 缺少或无效的 API Key |
| 403 | 密钥已停用或过期 |
| 404 | 任务不存在，或不属于这把密钥 |
| 400 | 参数 / 文件类型 / 当前状态不允许该操作 |
| 429 | 超过频率限制 |
| 503 | 数据库不可用 |

---

## 7. 数据隔离

- 一把密钥只能看到自己创建的任务。
- 后台任务总览仍能看到全部任务；归属显示为绑定用户，或 `api:密钥名称`。
- 调用历史在管理后台「开放 API」可查，含失败鉴权；约 90 天后自动清理。
