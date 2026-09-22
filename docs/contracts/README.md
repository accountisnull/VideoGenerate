# HTTP v1 公共数据契约

接口语义以 [HTTP 接口协议](../HTTP接口协议.md) 为准，模型源代码位于 `backend/app/contracts/`。本目录 `v1/*.schema.json` 从模型生成，禁止手工修改。接口服务、数据库和 Worker 尚未接入这些模型。

## 各模块如何使用

在后端目录使用现有虚拟环境：

```python
from app.contracts import ContentRequest, ContentJob

# 服务端校验请求；生产任务编号由集成模块提供。
request = ContentRequest.model_validate_json(
    '{"task_id":"11111111-1111-4111-8111-111111111111","topic":"介绍绿萝养护"}'
)

# 可省略字段没有提供时，出站请求也应省略，不能把 revision 写成 null。
payload = request.model_dump(mode="json", exclude_unset=True)

# 调用方校验模块响应；作业响应直接序列化为对象，不包含 root 字段。
response = ContentJob.model_validate_json(response_text)
state = response.root.state
if state == "succeeded":
    script = response.root.result.script
```

上面的 `response_text` 由 HTTP 客户端实际请求取得。配音和数字人分别使用 `SpeechRequest / SpeechJob`、`VideoRequest / VideoJob`；集成入口使用 `TaskRequest / TaskResponse`。`ContentCapabilities / SpeechCapabilities / VideoCapabilities` 用于能力声明。

`CreateHeaders` 只接收由 HTTP 层按大小写无关名称提取的 `Idempotency-Key` 和可选 `X-Request-ID`，不应传入整套请求头。缺少请求编号时生成 UUID；幂等键仍必须提供。FastAPI 端点后续需要自行接入请求头提取、422 错误映射和响应头回传。

非 Python 模块可以从 `v1/` 获取独立的 JSON Schema（Draft 2020-12），每个文件包含自身引用的全部定义。使用时启用校验器的 UUID、日期时间格式检查。

## 校验边界

- 请求拒绝未知字段，包括嵌套改稿和资产对象；响应忽略新增字段。可空响应字段仍必须显式出现。
- 字符串规范化并检查非空，整数和布尔字段拒绝字符串／布尔与整数之间的隐式转换；时间要求 UTC。
- 作业按 `state` 区分结构：成功必须有对应模块结果、失败必须有错误、待执行和执行中两者均为空。
- 模型检查资产路径语法、摘要格式和媒体数值范围；不访问文件、不读取 `.env`、不创建数据库、不调用外部服务。
- 配置档允许实测采样率和不同版本；720×1280、25 fps 等具体规格由后续配置档兼容性检查执行，不在通用传输类型里写死。
- Pydantic 还执行跨字段与规范化校验：UTC 偏移、时间先后、路径中的 `.`／`..`、标签去重、生产与发布状态一致、资源对配置档的引用等。这些 Python 校验不会全部表达在自动生成的 JSON Schema 中；非 Python 实现需要按协议补齐。
- `revision` 允许省略，但显式 null 被拒绝；未提供时模型序列化也自动省略该字段。响应序列化不要使用 `exclude_none=True`，否则会丢失必填的 null 字段。
- 同一作业状态变化是否合法、终态是否被修改，需要持久化层对照旧状态判断。资源真实存在、历史内容所属任务、文件大小／摘要／目录联接越界、音频与视频关联、能力限制、幂等及发布核实均是服务实现的责任。

## 更新和验证

从 `backend/` 执行：

```powershell
.venv/Scripts/python.exe -m app.contracts.schema
.venv/Scripts/python.exe -m app.contracts.schema --check
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests
```

契约测试覆盖文档 JSON 示例、正常跨模块交接和无效输入，并检查已生成文件与模型一致、内部引用可解析。模型变化后同时提交生成文件和对应协议说明。当前验证不代表真实媒体、HTTP 服务或供应商联调已通过。
