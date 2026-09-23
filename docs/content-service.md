# 内容作业服务

内容服务提供异步 HTTP、SQLite 作业与幂等持久化、调用记录、两轮编排、百炼适配及规则检查。内部能力说明见 [内容编排](specs/content-orchestration.md)。

## 启动与配置

按安装说明准备 backend/.venv（Python 3.11、uv 锁定依赖）。在项目根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-content.ps1 -CheckOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-content.ps1 -Port 8761 -AssetRoot data/assets
```

脚本可从其他目录用绝对路径调用，相对资产目录始终以项目根目录解析。前台运行，Ctrl+C 正常停止；停止时等待当前有界能力调用结束。强制结束或断电后，下次启动把 running 作业标记 failed / EXECUTION_INTERRUPTED，不会自动重跑模型。仅有 queued 且从未领取的作业可继续执行。

默认绑定 127.0.0.1:8761。脚本只管理本服务，不启动其他模块。端口占用、解释器缺失、配置错误、数据库损坏或已有实例占用时明确失败。默认 CONTENT_PROVIDER=disabled，health 和新提交返回 503 SERVICE_NOT_READY。显式启用 bailian 并校验配置后，health=200 仅表示本地执行器可受理，不证明远端模型已经实测可用。

| 服务端配置 | 默认值 | 说明 |
| --- | --- | --- |
| CONTENT_DATABASE_PATH | data/content/content.db | 内容独立数据库，相对项目根目录；不可指向集成 app.db |
| CONTENT_ASSET_ROOT | data/assets | 共享资产根目录，可被 -AssetRoot 覆盖；当前文本服务不生成媒体 |
| CONTENT_SERVICE_TOKEN | 空 | 受控本机联调可留空；配置后四类入口均使用 Bearer 鉴权 |
| CONTENT_PROVIDER | disabled | bailian 启用百炼兼容模式；未知值启动失败 |
| CONTENT_ENGINE | http | http 直接适配；deepagents 加载主管 Skill，需安装 content 可选依赖 |
| CONTENT_RULES_PATH | 空 | 启用时必填，指向核对过的规则 JSON；相对项目根目录 |

配置从根目录 .env 和进程环境读取，进程环境优先。改后重启。数据库及 .lock 文件属于运行数据，不提交。当前只有一个本地调用方 local-worker，单令牌轮换不改变调用方身份，不提供多租户认证。

## HTTP v1

| 方法与路径 | 行为 |
| --- | --- |
| GET /v1/content/health | 实际执行器未就绪返回 503 公共错误；就绪返回 service、api_version、ready |
| GET /v1/content/capabilities | 返回 service=content、api_version=1.0、空 profiles/resources，不伪报模型能力 |
| POST /v1/content/jobs | 必填 UUID Idempotency-Key；持久化受理后 202，重放 200，Location 指向作业 |
| GET /v1/content/jobs/{job_id} | 已知作业返回 200，失败状态在 body 中表达；未知作业 404 |

X-Request-ID 可省略，缺失时生成 UUID，响应回传。HTTP 错误为 `{request_id,error:{code,message,retryable,details}}`，校验错误不回显原始请求。JSON 非法编码、重复键、NaN/Infinity 返回 400；字段错误返回 422。请求与响应沿用 app.contracts，未改变公共 Schema。

受理示例（真实能力接入后才会得到 202）：

```http
POST /v1/content/jobs
Content-Type: application/json
Idempotency-Key: 22222222-2222-4222-8222-222222222222

{"task_id":"11111111-1111-4111-8111-111111111111","topic":"介绍绿萝养护"}
```

```json
{"job_id":"33333333-3333-4333-8333-333333333333","task_id":"11111111-1111-4111-8111-111111111111","state":"queued","created_at":"2026-09-23T00:00:00Z","updated_at":"2026-09-23T00:00:00Z","result":null,"error":null}
```

编号与时间仅为格式示例。成功结果是 `{script,title,tags,review_passed:true}`，失败结果 result 为 null。相同键和规范化后的相同请求返回原作业（含终态）；不同请求同键返回 409 IDEMPOTENCY_CONFLICT。只有一个活跃作业，其他新键返回 409 SERVICE_BUSY、Retry-After: 2；被拒绝的请求不消耗键。重放检查先于就绪和容量检查。

外部改稿在原请求中加入 revision 对象：`{source_job_id,instructions}`。来源必须是同调用方、同生产任务且已成功的内容作业；否则 422。新作业保留旧版本，生成器首轮收到 source_draft 和 instructions，再按正常两轮审核流程执行。省略 revision 表示新稿，显式 null 无效。

## 接入真实成员能力

应用工厂 `create_app(settings, orchestrator)` 接收配置好 generator、keywords、content_review 的 ContentOrchestrator。工厂为服务复制编排对象并安装 StoredJournal，不改原对象；实现内部可以使用 deepagents 或 MCP。正式部署需由组装入口完成配置及适配器就绪验证，再传入实例；不能把测试替身复制进生产入口或仅凭密钥存在宣告就绪。

每次检索、生成、审核调用前事务写入 content_calls（输入、阶段、能力名、开始时间），调用完成保存经校验输出/安全错误，写入失败即阻止后续阶段。终态另保存 RunOutcome，包括检索降级、稿件和审核结果。记录含业务文本，只留服务端数据库，不通过公共查询返回草稿或敏感日志。

HTTP 异步提交不等待推理；执行器在服务生命周期中运行，不依赖页面。同步数据库访问在线程中完成。接口适配必须使用可取消的异步 I/O；不能在事件循环中阻塞。没有单独任务队列或独立模型执行进程，这是单实例单作业设计。

## 数据版本与恢复

数据库使用专用 application_id 和 user_version=1。空库启动时在事务内建表；已有同版本库复用，其他应用库或未知版本拒绝启动，不删除原数据。迁移定义位于 app/content/storage/migrations.py。现阶段不存在旧版内容数据库的迁移任务；今后升级增加版本化迁移。

维护或升级前先停止本服务，再备份整个 content.db；仅在服务停止后恢复匹配版本的备份。数据库若还有 -wal/-shm 等附属文件，应使用 SQLite 备份工具取得一致快照，不在线直接拷贝主文件。不通过删除数据库“修复”版本错误。同目录 .lock 为系统文件锁载体，不必手工删除；进程退出会释放锁。

第二实例无法取得同库锁则直接拒绝启动，不能把第一实例的 running 作业误恢复为失败。应用必须以单进程运行，不启用 uvicorn 多 workers 或 reload。不同开发实例使用独立数据库和端口。

作业/幂等记录首版不自动删除或过期，未提供人工归档接口；请勿直接删表造成已用幂等键再次执行。终态不可更新，重做需新键及新作业。

## 验证与未完成事项

在 backend/ 执行 `.venv/Scripts/python.exe -m pytest -q`、`.venv/Scripts/python.exe -m ruff check app tests`。测试覆盖 HTTP 契约、异步受理、幂等冲突/重放、容量、改稿归属、终态保护、重启恢复、独占锁、调用记录失败及安全错误。

正式规则、同质化向量保存、Worker 集成和真实视频链路需要独立验收。内容作业成功不能证明视频链路已接通。本机开发进度、测试结果及真实作业记录归档在仓库外，不随源码提交。

## 启用百炼适配

1. 在本机 .env 填写三组 TEXT_WRITING_、TEXT_REVIEW_、TEXT_REVISION_ 的 BASE_URL、MODEL、API_KEY_ENV 及引用的密钥。BASE_URL 为实际地域的兼容模式 HTTPS 基础地址，不包含 /chat/completions 后缀。模型须支持非流式 JSON 输出；密钥不写入聊天或文档。
2. 由规则负责人核对 config/rules/content.example.json，另存本地规则，例如 config/rules/content.local.json，填写 CONTENT_RULES_PATH。示例三条词不构成完整生产词库，只有显式指定才会使用。
3. 设置 CONTENT_PROVIDER=bailian，执行启动脚本 -CheckOnly。缺配置或无效规则时失败，disabled 模式仍可查询历史。检查不产生收费请求，不证明远端模型可用。
4. 启动后提交一个真实主题并轮询，记录最终稿、规则版本、审核结果与耗时。首次联调最多调用四次模型：生成、审核、修订、再审核。

生成参数仅接受 temperature（0—2）、top_p（大于0且不超过1）、max_tokens（正整数）、seed（整数）、enable_thinking（布尔值）。模型是否支持这些参数需实测。未知字段及覆盖 model/messages/stream 的字段在启动时拒绝。适配器固定使用单结果、非流式 JSON，不支持工具调用或多模态。

HTTP 错误、429、超时、截断、无效 JSON 均导致作业失败，不自动重试、不剥离 Markdown 围栏。请求不跟随重定向，不读取环境代理；需企业代理时另行提供网络适配。

词表支持忽略大小写的字面匹配，覆盖 script/title/tags，不支持正则。字数按正文非空白字符计算，含标点；上下限含边界。缺文件、空词表、重复规则编号或上下限错误阻止启用。

HTTP 模式复用已有 httpx，无新增依赖。可选 deepagents 模式现已实现主管 Skill 加载和生成/审核适配；MCP 与六个独立 Agent 的成员实现仍未全部接入。

## Agent 组装与成员接入

设置 CONTENT_ENGINE=deepagents，并按现有锁文件在 backend/ 使用 uv sync --locked --extra content 安装可选依赖。默认 http 模式保留原调用方式。每次独立图运行将主管 Skill 和角色 Skill 放入 StateBackend 并通过 SDK 加载：writing/revision 加载 content-generation，review 加载 content-audit，均位于 backend/skills。缺少所需文件或 SDK 未加载任一所需 Skill 时，禁止模型请求。

中间件确认 Skill 加载后仅允许单次文本响应，不向模型提供文件、终端或子代理工具。模型若返回工具调用立即失败，不执行工具。原主管仍控制两轮与所有审核门槛。百炼模型桥复用已验证的 HTTP 适配，无 SDK 自动重试。该实现已经通过真实 deepagents SDK＋模拟供应商测试，不等于真实模型验收。

完整目标为内容模块内部 6 Agent + 7 Skill + 4 MCP；人员接口和工具约定见 [六人共同接口基线](specs/content-agent-baseline.md)。成员可通过 ContentAgents + assemble_agents 统一组装；原 build_orchestrator 的 retrievers、additional_reviews 扩展继续可用，未配置能力不会自动启用：

```python
engine = build_orchestrator(
    settings,
    retrievers={"trending": trending, "search": search},
    additional_reviews={"similarity": similarity},
)
app = create_app(settings, engine)
```

注入实例实现 [内容内部接口](specs/content-orchestration.md)。附加审核名称不能覆盖 keywords/content，已启用审核异常时作业失败。集成 Worker 通过 HTTP 获取成功结果，将 ContentResult.script 原样交给配音，title/tags 用于发布元数据。

## 一条真实内容作业联调

启用同质化 Agent、vector-mcp、审核后入库及恢复步骤见 [同质化服务](similarity-service.md)。默认不开启；准备 BGE-M3、初始化索引并设置 CONTENT_SIMILARITY_ENABLED=true 后，启动过程会建立真实 MCP 会话，检测异常或入库结果不明均不能返回内容成功。

服务配置完成并启动后，在 backend/ 运行（会调用已配置模型，可能收费）：

```powershell
.venv/Scripts/python.exe -m app.content.verify --topic "介绍绿萝养护" --receipt ../data/content-verification/first.json
```

脚本在提交前保存请求与 UUID 幂等键。网络异常或等待超时后，使用同一个文件继续，不重新换键：

```powershell
.venv/Scripts/python.exe -m app.content.verify --receipt ../data/content-verification/first.json
```

非默认端口每次都添加相同 `--base-url http://127.0.0.1:端口`。用 CONTENT_SERVICE_TOKEN 鉴权，密钥不写入记录。首次回包丢失时按原请求原键重放；已有 job_id 只查询；已有终态记录不再发请求。退出码 0 成功，1 业务失败，2 等待超时/配置/网络或协议错误。记录包含稿件和本次等待耗时，属于不提交的业务数据；同一文件不允许多个联调进程同时操作。
