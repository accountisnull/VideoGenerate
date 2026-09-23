# 内容内部接口与两轮编排

本文定义内容能力的内部接口、固定编排流程与持久化边界。部署与恢复见 [内容服务](../content-service.md)。

## 范围与验收

输入为生产任务编号、内容作业编号、原始主题；输出为通过全部已启用审核的公共 ContentResult，或明确失败，以及素材执行记录和每轮稿件/审核记录。最多首次生成加一次修订。关键词检查、内容审核必需；检索与同质化通过注入启用，未配置项不伪报已执行。

内部模型和 Python Protocol 用于可注入的异步编排。验收覆盖直接成功、修订通过、两轮拒绝、审核失败/超时/无效输出、检索降级、原主题保留及取消清理。公共 HTTP 接口沿用 v1 模型。

## 成员接口

实现位于 backend/app/content/，测试位于 backend/tests/content/。

| 能力 | 接口 | 约束 |
| --- | --- | --- |
| 素材检索 | Retriever.retrieve(context) → RetrievalResult | 素材包含来源与可空 URL；部分来源失败通过 failures 表达，全部异常由主管记录降级 |
| 写稿与改稿 | Generator.generate(GenerationInput) → Draft | 新稿首轮 previous_draft 为 null；内部修订传上一稿、全套问题和主题；外部改稿首轮传来源稿及 instructions；按输入选择 writing/revision 模型 |
| 规则与模型审核 | Reviewer.review(ReviewInput) → ReviewDecision | 每轮输入包括完整稿件及轮次；passed 为严格布尔值，拒绝必须说明 issues；异常不是拒绝结果 |
| 主管编排 | ContentOrchestrator.run(RunContext) → RunOutcome | 固定顺序、有限修订、全套重新审核、清理并行子任务 |

内部模型不可变、拒绝未知字段，边界再次校验返回对象。适配层将供应商 JSON 校验为这些类型；已知供应商故障抛 CapabilityError，禁止传入原始敏感响应。未知异常只在主管调用边界转为固定错误码，日志不输出异常文本。

### 请求、草稿与成功结果的边界

HTTP ContentRequest 只接收 task_id、topic 与可省略的 revision；不新增客户、历史、素材或重试字段。内部 GenerationInput 用 context.topic 保存原始主题，materials 为可为空的素材列表，previous_draft 为来源稿，feedback 为全部审核反馈，instructions 为明确修改意见。

| 概念名称 | 实际接口表示 |
| --- | --- |
| mode=generate | previous_draft 为 null，使用 TEXT_WRITING_* |
| mode=revise | previous_draft 非空，使用 TEXT_REVISION_* |
| previous_content | previous_draft |
| review_issues | feedback 中各审核的 decision.issues |
| revision_request | instructions |

上述概念名称仅供对照，不是额外接收的字段。生成器每次调用一次模型，轮次由主管控制；外部 revision 校验成功后建立新作业，保留旧作业。

模型输出仅含 script、title、tags。Draft 是内部类型名，不是 draft 文本字段；同样不能把 ReviewInput.draft、previous_draft 误删。Draft.char_count 由程序按去除所有空白后的字符数计算，包含标点，是只读属性，不作为模型输入字段或公共 HTTP 字段，不新增数据库迁移。关键词审核复用该属性。

素材来源保存在 GenerationInput.materials 与 RunOutcome.retrievals（title/source/url/snippet）。它们表示提供给模型的参考素材，不证明稿件实际引用了其中某项。无素材时为空；逐句引用或实际使用素材 ID 尚未实现，不伪报已交付，后续先确认内部结构再增加能力。

只有主管在所有已启用检查通过后构造 ContentResult(script,title,tags,review_passed=true)。审核 JSON 无效属于 INVALID_OUTPUT，导致 REVIEW_FAILED，不能回退为 passed=true，也不触发修订。有效业务拒绝才允许一次修订。

关键词和内容审核在构造时必填，额外审核使用命名映射注册；名字不能覆盖 keywords/content。同质化检测通过 SimilarityAgent 接入，命中原因写入 issues。启用同质化时，执行器在全部审核通过后持久化写入意图，再调用向量保存；成功读回确认后才将作业标为成功，重启只核对原意图。配置与恢复语义见 [同质化服务](../similarity-service.md)。Python 接口不限制后续适配层使用 deepagents 或 MCP。

## 行为口径

- 检索并行，生成串行，各轮审核并行；失败的检索保留固定原因码，空结果与故障可区分。未配置检索时记录为空。
- 业务拒绝最多触发一次修订；调用失败、超时或无效输出立即导致本次运行失败，不自动重试模型调用。
- 所有能力调用使用显式注入的正数、有限 call_timeout_seconds。后续配置入口负责提供该值，不在编排中读取环境。异步适配必须可取消、不得阻塞事件循环。
- 每轮先校验完整结构，审核看到规范化后的同一份 script/title/tags；仅所有检查通过后构造 review_passed=true。相同标签按首次出现顺序去重。
- 不在本阶段硬编码 100—200 字；字数及业务长度规则由审核实现确定，避免内部编排擅自修改公共契约。
- 运行失败不输出可消费 ContentResult；草稿和审核记录属于内部追踪资料，不得交给配音。
- 取消向调用者传播，并取消、等待所有并行子任务退出；取消不是业务失败或任务已取消的持久化结论。

## 调用示例

```python
from app.content.models import RunContext
from app.content.orchestrator import ContentOrchestrator

# generator、keywords、audit 是成员提供的真实实现；替身仅在 tests/ 中使用。
orchestrator = ContentOrchestrator(
    generator=generator,
    keywords=keywords,
    content_review=audit,
    call_timeout_seconds=60,
)
outcome = await orchestrator.run(RunContext(
    task_id=task_id, job_id=job_id, topic="介绍绿萝养护",
))
# 只有 outcome.result 非空时，才有通过审核的内容。
```

审核拒绝数据示例：

```json
{"passed":false,"issues":[{"field":"script","code":"UNSUPPORTED_PROMISE","message":"删除保证效果的承诺"}]}
```

## 后续步骤与存储边界

内容服务已接入独立 ContentStore、StoredJournal 和后台执行器：作业与幂等原子受理，各次调用前后持久化。单独使用未注入 journal 的编排器仍只提供内存运行记录，不具备重启恢复。

每次调用通过 CallJournal.begin/finish 保存输入与结果，不能仅在 run 返回后保存整份结果。无法确认的 running 作业在重启时处理为 EXECUTION_INTERRUPTED；queued 可安全领取。ContentRequest.revision 的来源查询、同任务/调用方/成功状态检查在存储受理事务内执行；编排 run 通过 source_draft/instructions 接收已核验内容。

health、capabilities、提交、查询和启动脚本已实现。默认 CONTENT_PROVIDER=disabled 时，health/新提交为 503；启用方式见 [内容服务](../content-service.md)。

## 验证

在 backend/ 执行 `.venv/Scripts/python.exe -m pytest -q` 和 `.venv/Scripts/python.exe -m ruff check app tests`。模型使用替身或确定性向量，无付费请求；测试覆盖 HTTP 应用生命周期、真实 stdio MCP 子进程及临时 Qdrant，不代表真实模型质量或媒体链路验收。
