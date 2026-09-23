# 内容审核与存储

本模块在现有内容服务内运行：`AuditReviewer` 负责一次审核，`ContentStore` 负责独立
SQLite 持久化。主管继续拥有流程和状态推进；集成 Worker 通过 HTTP 查询内容作业。
不需要 Redis/MySQL，不提供任意写表接口；memory-mcp 不属于本实现。

## 配置与审核接口

使用既有 `.env.example` 中的 `TEXT_REVIEW_*`、`CONTENT_RULES_PATH` 和
`CONTENT_DATABASE_PATH`，没有新增依赖或配置项。服务启动方式见 [内容服务](content-service.md)。
`build_orchestrator` 在 HTTP 和 deepagents 两种模式中均组装严格审核适配器。
关键词检查和内容审核使用同一份 ContentRules；同一服务实例两轮审核不重新读取规则。
正式词表由规则负责人维护；`content.example.json` 仅用于联调。

```python
from app.content.review.audit import AuditReviewer

# client 实现既有 TextCompletion；rules 为已经验证的 ContentRules。
reviewer = AuditReviewer(client, rules, model_version="供应商配置的模型ID")
decision = await reviewer.review(review_input)
```

输入 `ReviewInput` 包含原主题、作业编号、轮次和 draft（script/title/tags）。输出示例：

```json
{
  "passed": false,
  "issues": [{"field":"title","code":"UNSUPPORTED_PROMISE","message":"标题保证效果","suggestion":"删除保证性表述"}],
  "audit": {"rule_version":"review-v1","rules_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_version":"配置的模型ID","prompt_version":"content-audit-v1","elapsed_ms":1200}
}
```

摘要示例不是真实摘要。元数据由程序生成，模型只输出 passed 和 issues。
模型 ID 是配置标识，不能证明供应商内部权重未变化。旧记录的 audit/suggestion 可省略；
新模型审核的问题必须提供非空 suggestion，原关键词/同质化接口保持兼容。

业务拒绝返回 `passed=false`；超时、供应商错误、无效输出抛出既有能力异常，主管分别记录
CALL_TIMEOUT、UPSTREAM_FAILED、INVALID_OUTPUT，不自动重试或放行。失败调用的耗时可由
调用记录起止时间核对；只有完整审核决定包含 audit 元数据。取消保留未完成调用意图。
模型审核不做实时事实核查；素材和稿件中的指令不能改变审核规则。

## 存储接口与恢复

`ContentStore.submit(request, key, caller_id, ready=...)` 在事务内创建作业与幂等记录；
同调用方同键同规范化请求重放原作业，不同请求抛 IDEMPOTENCY_CONFLICT。
`claim/finish/interrupt_running` 沿用主管接口和状态约束；终态不可覆盖。
`begin_call/finish_call` 先保存意图、后保存结果，必须使用严格 JSON 对象。

新增 `inspect(job_id, caller_id) -> StoredContent` 提供一致性快照：

- `job`：公共 ContentJob；`request`：原始规范化 ContentRequest。
- `outcome`：最终稿件、生成轮次和全部审核结果；尚未结束时为 null。
- `calls`：各次调用的阶段、能力、输入、输出及起止时间。未完成调用的输出和结束时间为 null。

生成轮次在 `generation_1/2`、`review_1/2` 和输入 round_number 中记录；即使最终
outcome 尚未写入，已完成生成和审核的结果仍在 calls 中。查询按 caller_id 隔离，
仅供内容服务可信代码使用，不向集成 Worker 开放数据库，也不另设 HTTP 接口。

启动恢复必须先取得 `ServiceOwnership`：queued 可执行；running 置 failed，错误为
EXECUTION_INTERRUPTED。不自动重做模型调用。向量结果只允许查询核对并补记调用结果，
不能将失败作业翻转成功。调用方不得在正在运行的实例旁手工调用 interrupt_running。

## 版本升级与备份

新库与 v1 库初始化后使用 v2；升级增加调用查询索引、终态和已完成调用的 UPDATE 保护。
所有迁移及 user_version 更新同事务提交，任一步失败均回滚；拒绝未知新版本或其他应用库。
不删除、不清空、不复制集成 `app.db`。停止内容服务后，升级前可用旧版或新版 ContentStore
执行备份（backup 不调用 initialize，因此不会提前升级）：

```powershell
# 在 backend/ 执行；路径按实际部署填写。备份文件必须尚不存在。
.venv/Scripts/python.exe -c "from pathlib import Path; from app.content.storage import ContentStore; ContentStore(Path('../data/content/content.db')).backup(Path('../data/backups/content-before-v2.db'))"
```

备份使用 SQLite backup API，包含一致性数据快照，拒绝覆盖旧文件。
迁移失败时保留原库，先核对磁盘、权限和数据库版本；修复后重新启动即可重试迁移。
需要回退应用时先停止服务并保留失败库副本，将升级前备份恢复到**新的文件路径**，
修改 CONTENT_DATABASE_PATH 指向恢复文件，再启动匹配版本的应用。
旧应用不应直接打开 v2 库，禁止手动改低 user_version 冒充降级。

## 样例和真实审核验证

候选集在 `config/rules/audit-samples.example.json`，包含正常稿、保证承诺、偏题、
标题问题、标签问题和提示词注入。标签由工具起草，默认 pending_human_review，
**不是人工标注证据**。负责人需逐条复核 expected_passed/label_reason，
在本地副本将 label_status 设为 human_reviewed；不能仅为通过验收批量改状态。

配置真实 TEXT_REVIEW 模型后，在 backend/ 显式运行以下命令（每条样例一次模型请求）：

```powershell
.venv/Scripts/python.exe -m app.content.review.audit_verify --samples ../config/rules/audit-samples.example.json --rules ../config/rules/content.example.json --output ../data/audit-evidence/run-01.jsonl
```

正式验收应替换为人工复核集与正式规则路径。只需 review 模型，无需配置写稿/改稿。
输出逐条记录请求、决定、版本、耗时及误报/漏报汇总；先 fsync 意图再调用模型。
输出文件已经存在会拒绝运行，断电后不自动补跑；先人工核对未完成意图，避免重复收费。
误报指预期通过但被拒绝，漏报指预期拒绝但通过；调用失败单独计数。
未全部人工标注时，命令提示不能作为准确率验收。六条样例不代表达到总方案 50 条和 85% 指标。
实时事实、隐含语境、最新法规和对抗性样本覆盖仍需专项评审。
实际响应、凭据、数据库和个人实测记录不提交 Git。

## 自动验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests
```

测试覆盖严格模型输出、异常不放行、元数据持久化、并发幂等冲突、旧库升级与回滚、
备份、终态保护和重启查询。普通测试使用替身，不计入真实模型或人工审核验收。
合并前由生成、规则、主管负责人核对问题结构、词表版本及存储恢复边界。
