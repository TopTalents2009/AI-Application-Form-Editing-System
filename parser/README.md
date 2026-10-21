# 企业微信聊天记录分析

本机或局域网电脑上的企业微信聊天记录解密、同步、检索与查看。分析机跑 Web 界面，远端电脑只装同步助手；默认同步文字记录，图片走 CDN，文件按需拉取。

也保留个人微信、钉钉的解密与日报能力，见文末「其它平台」。

## 能做什么

- **网页查看**：按电脑 → 会话 → 消息浏览，支持日期筛选和统计
- **全文搜索**：服务端检索已同步消息的正文、发送者、文件名
- **远端同步**：把 `WeComSyncAgent.exe` 拷到对方电脑，默认同步全部群聊和单聊
- **按需下文件**：同步默认不拉附件；点击下载后由在线助手回传本地缓存
- **助手自动更新**：分析机打包新版本后，已安装更新逻辑的助手会自行替换 exe
- **本机解密**：分析机自己装着企业微信时，可直接扫进程密钥并解密

## 架构

```
远端 Windows（企业微信已登录）
  WeComSyncAgent.exe
    → 内存提取密钥、解密 message/session/user 等库
    → 默认同步全部群聊/单聊 JSON 推送到分析机
    → 心跳保活；按需回传 Cache 里的文件
    → 发现新版本则下载校验并替换自身

分析机
  FastAPI :8767     入库、搜索、附件任务、助手更新包
  Vue :5173         来源列表 / 聊天 / 统计 / 搜索
  export/synced/    远端同步数据（不入库 git）
```

## 环境

- Windows 10+
- Python 3.10+
- Node.js 18+（前端）
- 企业微信保持登录（解密需要扫 `WXWork.exe` 内存）

```bash
git clone https://github.com/accten/WXwork-chat-history-analysis.git
cd WXwork-chat-history-analysis
git submodule update --init --recursive
pip install -r requirements.txt
cd web && npm install && cd ..
copy config.example.jsonc config.jsonc
```

`config.jsonc` 不要提交。里面的 `llm.auth_token`、远端密码、同步令牌只放本机。

## 快速开始（分析机）

双击 `start.bat`，或：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器打开 [http://localhost:5173](http://localhost:5173)。同一局域网的手机或其它电脑打开启动窗口里打印的 `http://<局域网IP>:5173`。后端 API 在 8767 端口。

首页会显示可点击复制的局域网地址和同步令牌。把助手装到其它电脑时用 8767 地址，不要填 `127.0.0.1`（会连到对方自己）。

若其它设备打不开，用管理员运行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\open_lan_firewall.ps1
```

## 远端同步助手

1. 分析机打包：

```bat
build_agent.bat
```

产物是 `dist\WeComSyncAgent.exe`（gitignore，需自行分发）。

2. 拷到远端电脑运行，填写真实姓名（用于在线提醒）。助手会向分析机拉令牌。
3. 保持企业微信已登录，点「解密并刷新会话」。默认会勾选全部群聊和单聊。
4. 点「完成」转入后台；开机可自启。新出现的聊天会自动纳入同步。

默认同步间隔 5 分钟，上传全部有消息的群聊和单聊。点「退出应用」会取消开机项并结束进程。

### 按需拉文件

同步不下载附件。网页里点「下载」后：

1. 分析机给这台电脑建一个任务
2. 助手约 5 秒心跳拿到任务，在本机企业微信 `Cache` 里找文件
3. 上传到 `export/synced/<电脑>/files/`，网页再下载

文件从未在企业微信里打开过，缓存里就没有，会提示「对方电脑未缓存该文件」。图片一般走企微 CDN，不必回传。

### 自动更新

改 `agent/VERSION` 和 `agent/CHANGELOG.md` → 再跑 `build_agent.bat`。助手心跳会对比版本和 SHA256，校验通过后替换 exe 并重启。再次打开助手会弹出本次更新说明。配置在 `%APPDATA%\WeComSyncAgent`，不会被覆盖。

**没有更新逻辑的旧助手仍需手动拷一次新 exe。**

## 本机解密

分析机自己登录了企业微信时：

```bat
decrypt_wecom.bat
```

或在助手里点「解密并刷新会话」。解密输出在 `export/wxwork_decrypted/`（gitignore）。

密钥来自 `WXWork.exe` 内存中的 wxSQLite3 AES-128 缓存 key。进程内存越大、库文件越多，越慢；机械盘和杀毒实时扫描也会拉长时间。

## 搜索

顶栏搜索框检索全部已同步来源；聊天页「搜索本会话」只搜当前会话。多个词用空格分开，需同时命中。

```
GET /api/search?q=申报&source_id=&session_id=&limit=50
```

## 开放读取 API

完整说明见 [_docs/OPEN_API.md](_docs/OPEN_API.md)。其它程序用 API Key 只读聊天记录，接口在 **8767** 端口的 `/v1`。网页浏览仍走 5173，不需要这个 key。

首页可复制地址和 key。也可写在 `config.jsonc` 的 `open_api`。`read_key` 留空时自动生成到 `export/api_read_key.txt`。

```bash
curl http://192.168.2.25:8767/v1/health
curl -H "X-API-Key: YOUR_KEY" http://192.168.2.25:8767/v1/sources
```

## 主要接口

| 路径 | 说明 |
|------|------|
| `GET /api/sources` | 本机 + 已同步电脑 |
| `GET /api/sources/{id}/sessions` | 会话列表 |
| `GET /api/sources/{id}/messages/{sid}` | 消息 |
| `GET /api/search` | 关键词搜索 |
| `GET /api/sources/{id}/attachments/{mid}` | 本机缓存或按需回传 |
| `GET /api/ingest/info` | 令牌、局域网 URL、助手更新信息 |
| `POST /api/ingest/wecom` | 助手推送会话 |
| `GET /api/ingest/heartbeat` | 在线心跳；返回文件任务和更新信息 |
| `GET /v1/health` | 探活，无需 key |
| `GET /v1/sources` | 电脑列表（需 API Key） |
| `GET /v1/sources/{id}/sessions` | 会话列表 |
| `GET /v1/sources/{id}/messages/{sid}` | 消息 |
| `GET /v1/search` | 关键词搜索 |

## 目录

```
├── start.bat / start.ps1     一键起 API + 前端
├── build_agent.bat           打包同步助手
├── agent/                    远端 WeComSyncAgent
├── api/server.py             FastAPI
├── web/                      Vue 3 + Tailwind + Vite
├── core-wecom/               企微查询、消息解码、附件定位
├── shared/                   同步库、搜索、在线状态、按需文件、助手更新
├── tools/wechat-decrypt/     密钥扫描与库解密（submodule）
├── config.example.jsonc      配置模板
├── tests/                    unittest
└── export/                   运行时数据（gitignore）
```

## 测试

```bash
python -m unittest discover tests
```

## 安全与使用边界

- 只用于你有权访问的电脑和账号（本机或已授权的办公电脑）。
- 不要把 `config.jsonc`、`export/`、`all_keys.json`、解密库、同步令牌提交到 git。
- 同步令牌相当于写入凭证，不要发到公开群。
- 开放读取 API 的 key 能看到已同步的聊天正文，按人分发、用完作废。
- 助手更新包由分析机提供，请保证 `dist\WeComSyncAgent.exe` 来自本仓库的打包脚本。

## 其它平台

个人微信、钉钉仍可用原 CLI 解密和日报（`python wechat.py`、`core-dingtalk/`）。飞书目前不可用。详情见各 `core-*/README.md`。

## 致谢

- [WeChatDecrypt](https://github.com/ylytdeng/wechat-decrypt) — 微信 / 企业微信库解密
- [wx_key](https://github.com/ycccccccy/wx_key) — 微信密钥提取
- [dingwave-V3](https://github.com/E2ern1ty/dingwave-V3) — 钉钉库解密
