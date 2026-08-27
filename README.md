# AI Application Form Editing System

**版本 2.0** · 申报书智能修改系统（FastAPI）

根据区域修改意见，批量匹配申报书，经大模型生成编辑计划，人工确认后再写入 Word。生成计划时可检索外部只读人才库 / 企业库补齐缺失信息。

## 快速开始

1. Python 3.10+
2. `pip install -r requirements.txt`
3. 复制 `config.example.json` 为 `config.json`，填入大模型 `baseUrl` / `apiKey` / `model`；人才/企业库可选（`pool`）
4. 双击 `start.cmd`，或 `python run.py`
5. 浏览器打开 http://127.0.0.1:3777

`config.json` 含密钥，请勿提交到仓库。

## 2.0 能力

- 批量上传申报书与修改意见，按编号匹配；任务详情按本翻页
- 两阶段改法：分类 → 按章出计划 → 人工核对修改前/意见/修改后 → 确认后 `apply_edits.py` 落盘
- 生成计划时检索外部只读人才库 / 企业库（`pool.read`），不编造正文没有、库里也没有的数据
- 意见全文入库；共享意见按本分发；匹配勾选传入启动接口
- 失败任务若已有 `plan.json` 仍可打开计划编辑器；校验只认 `*_修改后.docx`

## 管线

意见分类 → 各章注入规则并行出计划 → 合并去重 → 人工确认 → 写入 Word

匹配规则（matcher v2）：编号 90 / 前缀 85 / 全名 100；弱 token 走 LLM 仲裁。`25xxxx` / `26xxxx` 视为日期码，不参与编号匹配。
