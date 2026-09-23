# 同质化 Agent 与 vector-mcp

业务实现为 app/content/review/similarity.py，专用类型放 app/content/similarity_models.py，保留主管 app/content/models.py。SimilarityAgent 将同步工具契约适配为主管 async review(ReviewInput) → ReviewDecision，命中正文与分数通过 issues 交给改稿能力。

## 安装与配置

在 backend/ 执行 `uv sync --locked --extra content --extra similarity`，或在独立 Python 3.11 环境安装 similarity 可选组，并将 CONTENT_SIMILARITY_PYTHON 指向该解释器。普通开发测试所需 MCP/Qdrant 已列入 dev 组；大型模型运行依赖在 similarity 可选组，权重需另行准备，不提交仓库。

在本机 .env 中配置：

| 配置 | 用途 |
| --- | --- |
| SIMILARITY_MODEL_PATH | 本地 BGE-M3 完整目录，含 pytorch_model.bin、分词器和配置 |
| SIMILARITY_MODEL_REVISION | fingerprint 命令生成的 local-sha256 指纹 |
| SIMILARITY_INDEX_VERSION | 固定模型和索引策略版本 |
| SIMILARITY_QDRANT_PATH / SIMILARITY_QDRANT_URL | 本地目录与服务地址二选一，本地目录单进程占有 |
| SIMILARITY_COLLECTION | 集合名称，默认 content_similarity_bge_m3_v1 |
| SIMILARITY_EMBEDDING_TIMEOUT_SECONDS | 模型加载和推理预算，默认 120 秒 |
| SIMILARITY_QDRANT_TIMEOUT_SECONDS | 服务端索引请求超时，默认 10 秒 |
| SIMILARITY_REQUIRED_CHECKS | 默认 keywords,content_audit,similarity，可增加不能删除基础项 |
| CONTENT_SIMILARITY_ENABLED | 默认 false；配置和索引准备完成后设 true |
| CONTENT_SIMILARITY_PYTHON | 启动 MCP 的解释器，默认 backend/.venv/Scripts/python.exe |
| CONTENT_SIMILARITY_TIMEOUT_SECONDS | 主管单次 MCP 调用总预算，默认 150 秒 |

先在 backend/ 使用安装了 similarity 组的解释器执行：

```powershell
.venv/Scripts/python.exe -m app.content.cli fingerprint --model-path "<本机完整模型目录>"
# 将输出指纹填入 .env，核对索引配置后显式初始化：
.venv/Scripts/python.exe -m app.content.cli init
```

模型、索引版本和维度不匹配时拒绝操作，禁止删除历史库或自动重建空库来放行。已有集合会校验元数据，不因 init 自动覆盖历史。

内容服务按 [启动说明](content-service.md) 运行。启用后 lifespan 创建一个 stdio MCP 会话，校验 initialize/list_tools，并在每轮审核调用 detect。只将 SIMILARITY_* 进程配置传给子服务；模型密钥不作为工具参数发送。停止内容服务会关闭 MCP 会话与索引。启动检查仅验证静态配置，不证明模型或索引已就绪；实际启用需启动服务及执行联调。

独立运行 MCP：`python -m app.content.mcp_server`，通过 stdio 供可信本机客户端使用，无独立 HTTP 端口。不要同时让 CLI 和内容服务占用同一本地 Qdrant 目录；需要共享访问时使用 Qdrant 服务端。

## 工具兼容与审核

已交付协议保留 detect、vector-insert、reconcile，以及 embed/vector-search。返回 `{success,data,error}`，客户端必须先检查 MCP isError 和 success，执行成功不等于审核通过。当前使用 stdio 兼容接入，Streamable HTTP 与基线命名作为后续兼容扩展，不把普通 HTTP 调用当作 MCP。

检测请求：script、job_id、content_version_id。主管以 UUIDv5(job_id, "content-round-轮次") 稳定生成版本，一次重放不换 ID，修订轮次使用新 ID。BGE-M3 dense 1024 维、CLS、L2、Cosine，排除当前版本后查询 top 10，最高分 >= 0.85 拦截。同源旧版本仍比较。

有效空库明确通过并返回 max_similarity=null。模型、连接、索引或格式异常直接失败，不作为“没有历史”。客户端还校验 passed、reason、最高分与命中列表的一致性，防止错误声明通过。

MCP 工具使用异步入口；真实模型在异步子进程运行。取消/超时会终止并回收模型进程，阻止后续写入。同步索引访问在线程中串行执行，取消需等待已开始请求的有限 I/O 完成后才释放锁；已提交外部写入不能承诺回滚，必须核对。模型异常只记录脱敏分类和作业关联标识，不回传正文、凭据及原始 stderr。

## 审核后入库及恢复

全部已启用检查通过后，执行器将完整审核结果、StoreRequest 和模型/索引 profile 写入现有 content_calls 的 vector_store 阶段，再调用 vector-insert。主管审核名 content 映射为快照中的 content_audit。工具验证同作业、同版本、正文摘要及必需审核结果，拒绝缺项或不同正文复用版本。

入库与恢复请求携带 expected_profile，服务端不允许使用另一个模型或索引处理旧意图。该字段是 PR #6 接入后的兼容扩展；未升级服务不得用于主管写入。成功写入并读回确认后，才完成日志并将内容作业标为 succeeded。

写入失败、超时或进程中断不返回成功，保留原意图。重启先将遗留 running 作业标为 failed，再用原稿、版本和 profile 调 reconcile，补记已保存/未保存事实；核对失败继续保留待核对日志。不重新调用模型、不自动重复写入，也不把失败终态改成成功。人工处理以日志事实为准，不能因客户端没收到回包就换版本入库。

复用现有 content_calls 表，无数据库版本变更。作业与 Qdrant 没有跨库事务，单写入者限制仍适用。

## 验证

普通测试使用确定性向量及临时真实 Qdrant；覆盖主管审核、全审后入库、写入响应丢失、重启只核对、MCP 实际进程启停、模型缺失阻止成功、取消回收子进程与索引锁释放。

真实模型验证单独运行 `python scripts/verify_similarity.py --model-path "<完整模型目录>"`，可选 `--qdrant-url` 验证服务端。脚本使用临时集合与人工语料，不写业务历史。固定语料成功不代表生产去重效果已经验收。本机结果归档仓库外。
