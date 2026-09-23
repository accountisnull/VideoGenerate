# 内容六人共同接口基线 v1

本文件是可随源码交付的共享技术规范。个人任务进度、联调流水及成员确认记录在仓库外归档。Python 内部协议已经实现；下述 MCP 工具是供四个服务实现的 v1 约定，尚不代表服务已交付或联调。

## 系统层级与职责

视频系统保持内容、配音、数字人、集成四模块，通过本地 HTTP 由独立 Worker 串联。内容模块内部目标为 **6 Agent + 7 Skill + 4 MCP 服务**。六人分别交付一个逻辑 Agent，可在同一内容服务进程运行；不要求六个常驻进程或由模型自由分派任务。

| 人员 | Agent | Skill 名称与目标目录 | 工具依赖 |
| --- | --- | --- | --- |
| 1 | 热点 | trending-fetch；backend/skills/trending-fetch/SKILL.md | search-mcp |
| 2 | 搜索 | search-query、keyword-matching；对应 backend/skills/ 同名目录 | search-mcp、rules-mcp |
| 3 | 同质化 | similarity-detection；backend/skills/similarity-detection/SKILL.md | vector-mcp |
| 4 | 内容审核 | content-audit；backend/skills/content-audit/SKILL.md | rules-mcp、memory-mcp |
| 5 | 生成与改稿 | content-generation；backend/skills/content-generation/SKILL.md | 当前三组文本模型配置，生成不依赖客户历史 |
| 6 | 主管 | orchestration；backend/skills/orchestration/SKILL.md | 成员接口、memory-mcp |

关键词是人员 2 交付的确定性 Skill，不额外计为第七个 Agent。写稿与改稿共享生成 Agent/Skill。目标路径不代表文件已经存在；以实际代码和组装为准，不用空 Skill 占位冒充交付。

## Python 接口：现在即可交付

模型源文件为 app/content/models.py，Protocol 位于 app/content/interfaces.py；序列化与具体字段见 [内容内部接口](content-orchestration.md)。业务实现导出实例，不在导入阶段读取密钥、启动进程或调用网络。

| 提供方 | 方法 | 成功输出 |
| --- | --- | --- |
| 热点、搜索 | async retrieve(RunContext) | RetrievalResult(materials, failures) |
| 生成 | async generate(GenerationInput) | Draft(script,title,tags) |
| 关键词、内容审核、同质化 | async review(ReviewInput) | ReviewDecision(passed,issues) |
| 主管 | async run(RunContext, source_draft=None, instructions=None) | RunOutcome(result 或 error, retrievals, rounds) |
| 调用记录存储 | async begin(context,stage,capability,input_json)、finish(call_id,output_json) | UUID、None |

RunContext 保留 task_id/job_id/topic。GenerationInput 的 materials 可空；previous_draft 空为生成，非空为修订；feedback 为全套审核记录，instructions 为明确改稿意见。生成器每次一次模型请求，主管最多执行两轮。Draft.char_count 是程序计算的只读属性，不接受模型自报。来源记录表示参考输入，不声明实际逐句引用。

审核 passed 是严格布尔值；通过时 issues 为空，拒绝时必须提供 field/code/message。ReviewDecision 只表达有效业务决定。上游故障抛 CapabilityError，无效输出抛 InvalidCapabilityOutput，超时抛 TimeoutError；不得把异常转换为 passed=true。成员不得在内部静默重试付费调用；取消必须传播并释放自己的连接。

主管为能力设置统一有限超时；适配器自己的连接/读取超时不得超过它。异步方法不阻塞事件循环。所有启用审核都是必需检查，异常直接失败；检索故障允许带原因降级。HTTP v1 字段和 Worker 协议不因 Agent 或 MCP 接入而改变。

## 统一组装入口

```python
from app.content.agents import ContentAgents, assemble_agents
from app.content.api import create_app

# 以下变量必须是各成员交付的真实实例，不能用测试替身启动正式服务。
agents = ContentAgents(
    generation=generation_agent,
    audit=audit_agent,
    keywords=keyword_skill,
    trending=trending_agent,
    search=search_agent,
    similarity=similarity_agent,
)
orchestrator = assemble_agents(agents, call_timeout_seconds=60, require_all=True)
app = create_app(settings, orchestrator)
```

部分联调允许 require_all=False，热点、搜索、同质化缺省；生成、审核与关键词始终必填。require_all=True 只验证全部成员能力已注入，不证明 MCP 服务连通或真实质量验收。HTTP create_app 负责绑定本地作业存储及日志；当前存储不可被未实现的远端服务隐式替换。

各成员交付时提供：实例工厂、所属 Skill 文件、依赖与配置模板、输入输出样例、异常/超时/取消测试和启动停止方法。注册由启动组装代码完成，不接受 HTTP 请求中的模块路径、任意命令或任意工具名。

热点成员已有 `create_trending_agent()` 与 `Retriever` 适配，内容服务通过 `CONTENT_TRENDING_ENABLED` 显式启用。`trending_fetch` 及三个分平台工具可通过 `register_trending_tools(server)` 挂载到人员 2 的 search-mcp，使用下述 v1 context/call_id/schema_version 结构；当前内容服务使用 Python 直连，Streamable HTTP 服务生命周期和远端客户端尚待汇总。完整规则和兼容说明见 [热点检索](../trending-retrieval.md)。

## 四个 MCP 服务基线

### vector-mcp 已交付版本兼容

人员 3 的 PR #6 采用 stdio、detect/vector-insert/reconcile 及 {success,data,error}。当前通过 SimilarityAgent 和应用生命周期明确兼容该版本；检测转换为 ReviewDecision，稳定版本按 job_id/round_number 派生，content 审核名映射为 content_audit。审核后调用 vector-insert，先保存原请求/审核/profile，再写库；重启使用 reconcile 核对，不重新生成。写入与核对携带 expected_profile，必须使用已包含此扩展的服务。详见 [运行与恢复](../similarity-service.md)。

因此下文 Streamable HTTP、统一工具名及 schema_version 是其余服务的目标基线和 vector-mcp 后续兼容方向；不能要求已交付 stdio 服务未经适配直接匹配。兼容层同时检查 MCP isError 与业务 success，不将 false 误作通过。此接入不表示四个 MCP 服务或七个 Skill 已全部完成。

目标服务固定为 search-mcp、rules-mcp、vector-mcp、memory-mcp。本期工具传输基线采用 MCP Streamable HTTP；本机部署绑定回环地址。服务端须使用真实 SDK initialize/list_tools/call_tool 交互，不将普通 JSON POST 命名为 MCP。不臆造 Skill/SkillContext API。

下表为 **待实现的 MCP v1 工具定义**，不是当前 Python 可调用实现。工具输入输出使用 JSON；返回 CallToolResult.structuredContent，isError=true 时客户端抛 CapabilityError，缺失或不符合结构时抛 InvalidCapabilityOutput。不得把自由文本解析成审核通过。协商协议版本由 MCP SDK 完成，输出 schema_version 固定为 "1.0"。

所有工具输入共有 context={task_id,job_id,topic} 和 call_id（UUID）。下表列出其他字段与业务输出；成功 structuredContent 为 {schema_version:"1.0",data:业务输出}。副作用工具以 call_id 幂等：同 ID 同内容返回原结果，不同内容冲突；结果不明时只可用同 ID 核查或重放，不能换 ID 重做。

| 服务及负责人 | 工具名 | 额外输入 | data 输出 |
| --- | --- | --- | --- |
| search-mcp：1、2 | trending_fetch | limit：1—50 整数 | RetrievalResult 的 JSON |
| search-mcp：1、2 | search_query | query：非空字符串；limit：1—50 整数 | RetrievalResult 的 JSON |
| rules-mcp：2 | rules_get | version：字符串或 null（首次取当前版） | ContentRules 的 JSON，包括 version、字符上下限、audit_instructions、keywords |
| vector-mcp：3 | similarity_review | round_number：1 或 2；draft：Draft；policy_version：非空字符串 | {decision:ReviewDecision,index_version:字符串} |
| vector-mcp：3 | accepted_content_store | result：ContentResult；policy_version：非空字符串 | {record_id:字符串,index_version:字符串} |
| memory-mcp：4 | call_begin | stage、capability、input_json：字符串 | {call_id:UUID}，与请求 call_id 一致 |
| memory-mcp：4 | call_finish | output_json：字符串 | {call_id:UUID,finished:true} |
| memory-mcp：4 | call_get | 无 | {call_id:UUID,state:"started"或"finished",output_json:字符串或null} |

规则适配器在开始作业前取得并固定规则版本；同一作业两轮不得切换版本。rules_get 失败不回退到空规则或自动通过。vector-mcp 内部封装 Embedding 和索引，模型选择、阈值、数据版本由 policy_version 确定，不让主管拼装供应商参数。

accepted_content_store 只可接收已通过全部审核的 ContentResult，以 job_id 保证同作业不重复入库。当前主管已通过上述兼容接口实现持久化意图、审核后入库及重启核对；vector-insert 使用稳定 content_version_id 保证同版本幂等，主管只写入作业最终通过的版本。统一工具名仍属待实现目标。不能在待审 review 方法中顺便入库，不能把“审核完成”视为“入库已完成”。

memory-mcp 首阶段仅承接逐次调用记录，SQLite 可继续作为实现存储，不要求 Redis/MySQL。当前 ContentStore 保持作业受理和终态的唯一写入方，远端服务不直接编辑同一数据库，也不以缓存代替原子受理。远端 CallJournal 需在主管与服务生命周期中明确接入后才可启用；当前 create_app 默认安装本地 StoredJournal，不声称它已走 MCP。客户历史学习不属于单视频前置条件。

MCP 客户端生命周期由内容应用 lifespan 管理，initialize 一次，关闭时等待有界调用并关闭会话。连接配置由成员适配统一读取：端点、令牌环境变量名、连接/调用超时；未实现字段不预先写入 .env.example。模型密钥留在模型适配层，不传给 MCP 工具。成员交付实际客户端时同步配置模板及启动代码。

## Skill 加载与模型权限

模型型生成、审核 Agent 分别加载 content-generation、content-audit 和共同的 orchestration Skill。当前以 create_deep_agent + StateBackend 注册 Skill，中间件验证所需 Skill 确实加载后才允许一次模型响应。

MCP 工具先由成员适配器执行并转换为类型化数据，不把全部工具目录交给模型自由调用。热点/搜索/向量/规则 Agent 的具体 Skill 加载由对应成员实现和测试。Agent 可以包含确定性步骤，不要求每个步骤都产生模型调用。

## 分步接入与验收

按依赖顺序：主管与持久化 → 生成/改稿 → 内容审核 → 规则与 rules-mcp → 热点/搜索与 search-mcp → 同质化与 vector-mcp → memory-mcp 调用记录。最后进行六个 Agent、七个 Skill、四个服务的联合验收。

部分接入测试不能登记完整架构完成。完整验收需逐项核对 Skill 实际加载、四个 MCP 的协议交互与工具调用、真实素材/规则/索引、相同任务追踪、失败不放行、重启与结果不明不重做。随后由视频 Worker 验收内容→真实配音→数字人→MP4。
