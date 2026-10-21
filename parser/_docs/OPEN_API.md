# 开放读取 API 使用文档

给其它程序、脚本或同事用 **API Key** 只读已同步的企业微信聊天记录。

- 协议：HTTP
- 端口：**8767**
- 前缀：`/v1`
- 方法：全部为 `GET`
- 权限：只读。不能发消息、不能改数据、不能拉附件文件

网页界面（5173）不走这套接口，也不需要 API Key。

交互式文档：分析机启动后打开 `http://<分析机IP>:8767/docs`。

---

## 1. 接入信息

向管理员索取两项：

| 项目 | 示例 | 说明 |
|------|------|------|
| 接口地址 | `http://192.168.2.25:8767/v1` | 必须和分析机同一局域网，不要用 `127.0.0.1` |
| API Key | `ci_xxxx` | 相当于读权限，不要发到公开群 |

先探活（**不需要** key）：

```bash
curl http://192.168.2.25:8767/v1/health
```

成功：

```json
{"ok": true, "service": "chatinsight", "read_api": true}
```

`read_api` 为 `false` 表示管理员关闭了开放接口。

---

## 2. 鉴权

除 `/v1/health` 外，每个请求都要带 key，任选一种：

```http
X-API-Key: YOUR_KEY
```

```http
Authorization: Bearer YOUR_KEY
```

调试也可以放在查询参数（会进日志，不推荐）：

```
GET /v1/sources?key=YOUR_KEY
```

失败：

| HTTP | 含义 |
|------|------|
| 401 | 缺少 key 或 key 无效 |
| 403 | 管理员关闭了开放 API |
| 404 | 电脑 / 会话不存在 |
| 422 | 参数不合法（缺 `q`、日期格式不对等） |

错误体一般为：

```json
{"detail": "Invalid API key"}
```

---

## 3. 统一返回

成功时：

```json
{
  "ok": true,
  "data": [],
  "count": 0
}
```

`data` 是本次结果。部分接口还会带 `source_id`、`session_id`、`offset`、`limit`、`total`。

---

## 4. 推荐调用顺序

```
健康检查
  → 列出电脑 /v1/sources
    → 记下 source_id
      → 列出会话 /v1/sources/{source_id}/sessions
        → 记下 username（会话 ID）
          → 拉消息 /v1/sources/{source_id}/messages/{session_id}
```

也可以跳过列表，直接搜索：`/v1/search?q=关键词`。

路径里的 `source_id`、`session_id` 若含 `:`、`/`、中文，必须做 URL 编码。例如会话 `R:123` 写成 `R%3A123`。

---

## 5. 接口

下文 `BASE` = `http://<分析机IP>:8767`。Header 一律带 `X-API-Key`。

### 5.1 探活

```
GET /v1/health
```

无需 key。用于确认网络和防火墙（需放行 **8767**）。

### 5.2 当前 key 信息

```
GET /v1/info
```

```bash
curl -H "X-API-Key: YOUR_KEY" BASE/v1/info
```

```json
{
  "ok": true,
  "data": {
    "key_name": "default",
    "endpoints": [
      "GET /v1/health",
      "GET /v1/info",
      "GET /v1/sources",
      "GET /v1/sources/{source_id}/sessions",
      "GET /v1/sources/{source_id}/messages/{session_id}",
      "GET /v1/search?q="
    ]
  }
}
```

### 5.3 列出电脑

```
GET /v1/sources
```

```bash
curl -H "X-API-Key: YOUR_KEY" BASE/v1/sources
```

`data[]` 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 后续请求用的 `source_id`。本机解密为 `local` |
| `kind` | string | `remote` 远端同步 / `local` 本机 / `ssh` SSH 直连 |
| `computer_name` | string | 电脑名 |
| `operator_name` | string | 使用人姓名（助手里填写的） |
| `last_sync` | string | 最近同步时间 |
| `session_count` | number | 会话数量 |
| `platform` | string | 一般为 `wecom` |
| `host` | string | 来源 IP，可能为空 |

### 5.4 列出某台电脑的会话

```
GET /v1/sources/{source_id}/sessions
```

| 参数 | 位置 | 默认 | 说明 |
|------|------|------|------|
| `limit` | query | 1000 | 1–5000 |

```bash
curl -H "X-API-Key: YOUR_KEY" "BASE/v1/sources/DESKTOP-ABC-123/sessions?limit=50"
```

`data[]` 字段：

| 字段 | 说明 |
|------|------|
| `username` | 会话 ID，拉消息时用这个值 |
| `display_name` | 群名 / 联系人显示名 |
| `session_type` | 会话类型（数字，企业微信内部值） |
| `summary` | 摘要 |
| `last_time` | 最后一条消息时间 |
| `msg_count` | 消息条数 |
| `synced_at` | 本会话最近一次同步时间 |
| `unread` | 未读（远端同步通常为 0） |

### 5.5 拉取某个会话的消息

```
GET /v1/sources/{source_id}/messages/{session_id}
```

| 参数 | 位置 | 默认 | 说明 |
|------|------|------|------|
| `start_date` | query | — | `YYYY-MM-DD`，含当天 |
| `end_date` | query | — | `YYYY-MM-DD`，含当天 |
| `offset` | query | 0 | 跳过条数 |
| `limit` | query | 200 | 1–1000。单次最多从库中取 5000 条再切片 |

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "BASE/v1/sources/DESKTOP-ABC-123/messages/R%3A1001?start_date=2026-09-01&end_date=2026-09-17&limit=100"
```

```json
{
  "ok": true,
  "data": [
    {
      "time_text": "2026-09-16 13:00",
      "sender": "张三",
      "sender_id": "1688854750794010",
      "text": "申报材料已发",
      "msg_type": 2,
      "msg_type_label": "文本",
      "hour": 13,
      "message_id": 3042,
      "has_attachment": false,
      "attachment_name": "",
      "media_url": ""
    }
  ],
  "count": 1,
  "total": 1,
  "offset": 0,
  "limit": 100,
  "source_id": "DESKTOP-ABC-123",
  "session_id": "R:1001"
}
```

`count` 是本页条数，`total` 是过滤后的总条数。下一页：`offset=100`。

消息字段：

| 字段 | 说明 |
|------|------|
| `time_text` | 发送时间 `YYYY-MM-DD HH:MM` |
| `sender` | 显示名 |
| `sender_id` | 企微用户 ID |
| `text` | 展示正文 |
| `msg_type` | 类型数字，见下表 |
| `msg_type_label` | 类型中文名 |
| `message_id` | 消息 ID |
| `has_attachment` | 是否带文件/图片 |
| `attachment_name` | 文件名（可能为空） |
| `media_url` | 图片 CDN 地址（可能为空） |

`msg_type`：

| 值 | 含义 |
|----|------|
| 0 | 文本/混合 |
| 2 | 文本 |
| 4 | 图片 |
| 7 | 语音 |
| 14 / 16 | 文件 |
| 15 | 图片/文件 |
| 20 | 富文本 |
| 38 | 应用消息 |
| 40 | 通话/音视频 |
| 101 / 102 | 系统消息 |
| 1011 | 会议通知 |

若 `text` 为 `[文本]`，表示类型是文字，但正文未能从企业微信二进制里解出可读内容（常见于「好的」「收到」等短句，或只抽出了无意义元数据）。不是接口故障。

本接口**不提供附件文件下载**。图片一般可直接使用 `media_url`（企微 CDN）。

### 5.6 搜索

```
GET /v1/search
```

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `q` | 是 | — | 关键词，1–80 字。多个词用空格分开，需**同时命中** |
| `source_id` | 否 | 全部已同步电脑 | 限定某台电脑；`local` 只搜本机解密库 |
| `session_id` | 否 | 该电脑全部会话 | 限定某个会话 |
| `limit` | 否 | 50 | 1–200 |

搜索范围：消息正文、发送者、附件名、会话名。不扫原始二进制。

```bash
curl -H "X-API-Key: YOUR_KEY" "BASE/v1/search?q=申报&limit=20"

curl -H "X-API-Key: YOUR_KEY" \
  "BASE/v1/search?q=申报%20清单&source_id=DESKTOP-ABC-123"
```

`data[]` 额外字段：`source_id`、`source_name`、`session_id`、`session_name`、`snippet`（命中摘要）。

---

## 6. 代码示例

把 `BASE` 和 `KEY` 换成管理员给的值。

### curl

```bash
export BASE=http://192.168.2.25:8767
export KEY=YOUR_KEY

curl -s -H "X-API-Key: $KEY" "$BASE/v1/sources" | python -m json.tool
```

### Python

```python
import urllib.parse
import urllib.request
import json

BASE = "http://192.168.2.25:8767"
KEY = "YOUR_KEY"


def get(path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = BASE + path + (("?" + query) if query else "")
    req = urllib.request.Request(url, headers={"X-API-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


sources = get("/v1/sources")["data"]
source_id = sources[0]["id"]
sessions = get(f"/v1/sources/{urllib.parse.quote(source_id)}/sessions")["data"]
session_id = sessions[0]["username"]
messages = get(
    f"/v1/sources/{urllib.parse.quote(source_id)}/messages/{urllib.parse.quote(session_id)}",
    start_date="2026-09-01",
    limit=100,
)
print(messages["count"], messages["data"][:3])
```

### JavaScript

```javascript
const BASE = "http://192.168.2.25:8767";
const KEY = "YOUR_KEY";

async function get(path, params = {}) {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") url.searchParams.set(k, v);
  }
  const res = await fetch(url, { headers: { "X-API-Key": KEY } });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const { data: sources } = await get("/v1/sources");
const sourceId = sources[0].id;
const { data: sessions } = await get(`/v1/sources/${encodeURIComponent(sourceId)}/sessions`);
const sessionId = sessions[0].username;
const { data: messages } = await get(
  `/v1/sources/${encodeURIComponent(sourceId)}/messages/${encodeURIComponent(sessionId)}`,
  { limit: 100 },
);
```

---

## 7. 管理员配置

写在分析机 `config.jsonc`（不要提交 git）：

```jsonc
"open_api": {
  "enabled": true,
  "read_key": "ci_your_main_key",
  "keys": [
    {"name": "同事A", "key": "ci_another_key", "enabled": true}
  ]
}
```

- `read_key` 留空：自动生成到 `export/api_read_key.txt`
- 环境变量 `CHATINSIGHT_API_KEY` 也可当作一把 key
- `enabled: false` 关闭全部 `/v1` 读取（探活仍可用）
- 改 key 后一般立刻生效，不必重启；改代码后需要重新 `start.bat`
- 首页「开放读取 API」可复制地址、key 和 curl 示例
- Windows 防火墙需放行 **8767**。管理员可执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\open_lan_firewall.ps1
```

Key 能看到已同步的聊天正文，按人分发，不用了就从 `keys` 里删掉。

---

## 8. 限制

- 只读已同步（或本机已解密）的数据，不是企业微信官方接口。
- 默认同步不拉附件；`/v1` 也不提供文件下载。
- 消息单次最多处理 5000 条，搜索最多 200 条。
- 必须能访问分析机局域网 IP 的 8767 端口。
- 不要把 key 写进公开仓库或群公告。
