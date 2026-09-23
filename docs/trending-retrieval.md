# 热点检索接入与来源说明

热点用于补充人工主题，不负责自动选题。按人员 1 任务单实现：三平台独立适配、异步并行、数据校验、精确去重、失败降级；暂不实现缓存、语义去重或跨平台热度排序。

## 安装与运行

本模块复用现有 httpx、Pydantic；普通测试复用 AnyIO，未新增依赖，pyproject.toml 和 uv.lock 保持原有版本。MCP SDK 已在开发组中；生产 search-mcp 的依赖与启动由服务维护者负责。

在项目 `backend/` 目录：

```powershell
uv sync --locked
# 合成示例，不计为真实验收
.venv/Scripts/python.exe -m app.content.retrieval.demo --limit 5
.venv/Scripts/python.exe -m app.content.retrieval.demo --fail douyin
# 全部失败退出码为 1，属于预期
.venv/Scripts/python.exe -m app.content.retrieval.demo --fail weibo baidu douyin
# 真实访问，保存至本地忽略目录
.venv/Scripts/python.exe -m app.content.retrieval.demo --live --output .local/live-trending.json
```

真实响应和个人实测记录不提交仓库。运行记录仅证明记录时刻的可访问性，不代表来源永久可用。

## 配置与生命周期

配置通过项目 `runtime.environment_values()` 读取根 `.env` 和进程环境，进程环境优先。模板在根 `.env.example`，不创建第二个 backend/.env。

| 配置 | 默认值 | 约束 |
|---|---|---|
| CONTENT_TRENDING_ENABLED | false | true 时由 build_orchestrator 注册热点；显式注入的 trending 优先 |
| TRENDING_PLATFORMS | weibo,baidu,douyin | 非空逗号列表，保留首次顺序，不支持其他平台 |
| TRENDING_LIMIT | 20 | 整数 1～50，聚合输出上限 |
| TRENDING_TIMEOUT_SECONDS | 8 | 有限正数，每平台总预算，需小于主管调用预算 |
| TRENDING_WEIBO_URL | 下表微博地址 | 必须符合对应供应商响应结构 |
| TRENDING_BAIDU_URL | 下表百度地址 | 同上 |
| TRENDING_DOUYIN_URL | 下表抖音地址 | 同上 |

源 URL 必须是无内嵌凭据的 HTTPS 地址，服务跟随当前网络代理配置，不跟随重定向。每次调用独立建立并关闭客户端；取消会传播至全部并发请求。无隐式重试。最大响应体为 3 MB，空响应或验证码是失败，不是空榜。

生产开关默认关闭，避免既有内容服务突然增加外网调用。显式调用独立热点入口或手动注入 Agent 不受此开关限制。导入模块不会抓取热点；Agent 构造仅加载 Skill 声明并计算内容摘要，不调用 LLM。

## 来源与字段映射

| 平台/来源版本 | URL | 解析与授权前提 |
|---|---|---|
| weibo / uapi-weibo v1 | https://uapis.cn/api/v1/misc/hotboard?type=weibo | 第三方 UAPI；type=weibo，list 中 title/url/hot_value；当前公开访问，不代表商业授权、永久免费或 SLA |
| baidu / baidu-realtime-page v1 | https://top.baidu.com/board?tab=realtime | 公开页面 s-data 中 hotList.content，word/url/hotScore；页面结构可能变更 |
| douyin / iesdouyin-word-billboard v1 | https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/ | status_code=0，word_list 中 word/hot_value；没有单条 URL，明确返回 null |

配置无需模型密钥、Cookie、Redis 或数据库。部署网络需要访问三个域名；公开端点不等于官方开放 API 合同。第三方条款、配额及生产适用性由内容负责人确认。接口改变时更换对应适配器，不修改主管。

来源可能返回平台搜索 URL，不保证是新闻原文；不拼造缺失链接。仅接收 HTTP(S) 来源链接；非法可选字段转 null。标题为空或非字符串则丢弃。热度数字字符串转换为有限非负数字，未知单位、布尔值、非有限数值等转 null，不作为跨平台可比指标。

`fetched_at` 是本系统完成来源处理的 UTC 时间，不是事件时间或供应商榜单更新时间。每条素材包含 title/platform/source_url/hot_score/fetched_at；交给现有内容接口时 source_url 映射为 url。

合成来源样例（仅展示格式）：

```json
{"type":"weibo","list":[{"title":"合成标题","url":"https://example.com/item","hot_value":"100"}]}
```

## 合并与失败规则

保持各源数组原序，以请求的平台顺序固定轮转。标题仅去首尾空白，完全相同才去重，保留首次出现，不比较不同平台热度。此项遵循任务单对原方案的修订，须由受影响负责人评审后合入。平台顺序会影响结果；今后增加缓存除平台集合、数量、来源版本外还需编码平台顺序。

| status | 含义 |
|---|---|
| ok | 正常有有效条目 |
| empty | 数据格式正确且榜单为空 |
| degraded | 部分条目无有效标题，保留剩余素材 |
| failed | 连接、超时、HTTP、数据结构故障或全部条目无效 |

各平台状态包括 `reason_code、item_count、invalid_count、fetched_at、source_id、source_version`。item_count 为该来源有效候选数，不是最终贡献数。超时/字段缺失/失败均保留固定原因码，不暴露响应原文或凭据。

原始业务入口 `await get_trending(platforms, limit)` 返回 `{success,data,platforms,degraded,error,reason_code}`。仅当所有平台均 failed 时 success=false、reason_code=all_platforms_failed；有效空榜为 success=true。参数错误抛 ValueError，来源故障通过结构化结果返回。

## 人员 5、6：现有 Python 接口

```python
from app.content.retrieval.agent import create_trending_agent

trending_agent = create_trending_agent()
# 与其他真实成员一起注入 ContentAgents(trending=trending_agent, ...)
result = await trending_agent.retrieve(context)  # RunContext(task_id, job_id, topic)
```

复用 `Retriever → RetrievalResult`，不改公共 HTTP ContentRequest。`materials` 使用现有 Material，补充可选的 platform/hot_score/fetched_at；`source_statuses` 保存完整平台状态；`failures` 以 `trending:平台` 标识并转换为现有 `CALL_TIMEOUT/INVALID_OUTPUT/UPSTREAM_FAILED/INTERNAL_ERROR`。详细原因仍在 source_statuses。

旧 Material、旧 RetrievalResult 与旧持久化记录可读取。新字段是内部响应扩展，部署时应先更新消费方；旧版 extra=forbid 接收者不能直接接收新字段。生成的内部 Schema 在 docs/contracts/internal/，与公共 HTTP v1 Schema 分开。

所有来源失败时 members 接口返回 materials=()、非空 failures 和失败 source_statuses，不伪装空榜。主管已有策略为保留 context.topic、带检索故障继续生成；热点模块不另改主管策略。生成层应只选择相关素材，标题不能替换原主题，也不构成事实核查。

## 人员 2：MCP 注册

```python
from app.content.retrieval.mcp_tools import register_trending_tools

register_trending_tools(server)  # 人员 2 的 FastMCP search-mcp 实例
```

工具为 `trending_fetch` 与 `fetch-weibo-trending / fetch-baidu-trending / fetch-douyin-trending`。聚合工具按服务配置选择平台；三个分平台工具用于任务单要求的独立热点能力。所有工具都遵循当前基线，输入：

```json
{
  "context":{"task_id":"11111111-1111-4111-8111-111111111111","job_id":"22222222-2222-4222-8222-222222222222","topic":"用户确定的主题"},
  "call_id":"33333333-3333-4333-8333-333333333333",
  "limit":20
}
```

结构化返回为 `{schema_version:"1.0",data:RetrievalResult}`。读取 `CallToolResult.structuredContent`，不要把文本内容当作业务对象。isError 表示工具执行或输入错误；平台访问失败作为合法 RetrievalResult 中的 failures/source_statuses 返回，客户端必须检查二者，不能将协议调用成功当成取材成功。

call_id 供上层调用记录关联；只读抓取无幂等写入，当前不连接 memory-mcp。无单独 search-mcp 生产入口，没有修改原 vector-mcp。SDK 协议测试使用内存传输；实际 Streamable HTTP 生命周期和远端客户端仍由人员 2 集成。Python 直连是当前已接入内容服务的模式，不声称生产已走 MCP。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m app.content.retrieval.schema
.venv/Scripts/python.exe -m app.content.retrieval.schema --check
.venv/Scripts/python.exe -m app.contracts.schema --check
```

专项测试位于 `tests/content/test_trending*.py`，覆盖三源响应结构、并发、故障隔离、空榜、去重与限制、字段校验、取消清理、主管原主题、配置、旧模型兼容和实际 MCP initialize/list_tools/call_tool 协议。普通测试使用替身，不替代真实来源验收。
