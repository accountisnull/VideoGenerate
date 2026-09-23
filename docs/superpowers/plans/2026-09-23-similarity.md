# 同质化检测实现计划

> 执行：superpowers:executing-plans，在当前独立工作树连续完成；使用 TDD。用户明确要求完成整份任务单，不自动提交、推送或合并。

**Goal:** 完成人员 3 任务单所有可独立交付的业务、适配、验证和交接材料。

**Architecture:** Python 业务层决定查重与审核后入库；Embedding 和 Qdrant 分别实现有类型的边界；MCP 只包装业务。SQLite 的写入意图归人员 4，提供 reconcile 核对能力与可运行恢复示例，不修改其他人员数据库。

**Tech Stack:** Python 3.11、Pydantic、sentence-transformers、BGE-M3 dense 1024、Qdrant、MCP stdio、pytest、uv。

**Spec:** 用户提供的人员 3 任务单（工作区上级目录）及 docs/同质化检测-接口草案.md。草案字段可在实现时收敛，最终交接文档为准。

## 全局约束和决策

- 固定阈值 0.85，>= 拦截；先过滤自身版本，再取 top 10；旧修订版本仍比较。
- 有效空集合可通过；集合缺失、数据库故障、模型或维度不兼容必须错误。
- 输入正文按原文计算 SHA-256，不静默裁剪。每次正文变化须新版本，同版本不同正文拒绝。
- 稳定 UUID point ID；审核凭据包含同版本、正文摘要和所有要求的检查通过结果，由可信编排层提供。CLI/MCP 是受信任本机内部接口，不是公网授权边界。
- 模型冷启动子进程具备硬超时，离线权重；固定模型文件指纹。变更必须换索引版本/集合。
- 任务单授权持续执行；不重复询问实施许可。维持原业务未接入状态，提供真实可调用服务工厂及 MCP，不修改人员 6 主流程。

## Review Focus

1. 同维度不同模型：完整 profile 不同也拒绝（任务 1/2）。
2. 写入成功而响应丢失：重试/reconcile 不重复且准确核对（任务 2）。
3. 自身占据 top 10：过滤必须在向量检索前（任务 2）。
4. 部分审核缺失或正文变动：不得入库（任务 1）。
5. 错误伪装空库、非有限值或零向量：明确失败（任务 1/2/3）。

## Task 1：业务接口和审核入库

Files: app/content/models.py、errors.py、review/similarity.py；tests/content/test_similarity.py。

Interfaces: SimilarityService.detect(DetectRequest)->DetectionResult、store(StoreRequest)->StoreResult、reconcile(DetectRequest)->ReconcileResult。EmbeddingProvider.embed(script)->list[float]；VectorIndex.search(vector,exclude_version,limit)、lookup(version)、upsert(record,vector)。

- [ ] 写测试并运行 RED：固定分数 0.8499/0.85/0.8501 断言 true/false/false，空库 null，错误透传；store 缺检查/错摘要失败，重复请求只有一条。
- [ ] 实现冻结 profile、请求/响应类型、错误；匹配反馈含原文与版本；校验归一化向量。
- [ ] GREEN：`python -m pytest tests/content/test_similarity.py -q`。

## Task 2：Qdrant 存储

Files: app/content/storage/vector_index.py；tests/content/test_vector_index.py。

- [ ] 使用真实临时 Qdrant 写 RED：空库、缺失集合、metadata 模型/profile 变更、维度变化、11 条历史过滤自身、重复写入/重开、丢失响应后 reconcile；真实闭合端口连接失败。
- [ ] 实现显式 initialize/validate；集合 metadata 绑定 profile，已存在但不兼容绝不删除重建；upsert wait=true 后 readback 核对。
- [ ] GREEN：`python -m pytest tests/content/test_vector_index.py -q`。

## Task 3：模型、配置和启停入口

Files: app/content/providers/embedding.py、embedding_worker.py、config.py、factory.py、cli.py；.env.example；pyproject.toml/uv.lock；对应测试。

- [ ] 先测维度/非有限/零向量、token 超长、权重指纹变化、模型超时、配置缺失/URL 与路径冲突。
- [ ] BGE-M3 本地子进程使用临时 JSON，离线推理，错误分类；统一读取 runtime.environment_values；显式 init/detect/store/reconcile CLI。
- [ ] 通过 uv 锁定 similarity 可选组；运行配置/模型测试。

## Task 4：Skill/MCP 和真实联调

Files: app/content/mcp_server.py；backend/skills/similarity-detection/SKILL.md；scripts/verify_similarity.py；tests/content/test_mcp.py；docs/同质化检测-交付与验收.md。

- [ ] 先测适配请求/错误不会返回成功，vector-insert 不绕过审核校验；注册 embed/vector-search/vector-insert 和业务 detect/reconcile。
- [ ] 实现 stdio 薄适配，不在普通测试加载真实模型。真实联调脚本单独 opt-in，使用 BGE-M3 和真实临时 Qdrant。
- [ ] 输出模型/索引版本、真实分数、空库/重复写入/自身排除结果，保存忽略路径；服务端模式参数可单独运行。
- [ ] 完成配置、运行、示例、错误码、幂等/恢复交接文档与任务单验收映射。
- [ ] 全量 pytest、Ruff、Schema 检查、git diff --check；独立代码审查，修复实质问题再验证。

## 执行记录

- 基线：81 项既有测试通过，BGE-M3 原型真实向量已验证。工作树 feat/similarity-probe，未提交实验成果继续保留。
- 本期仍无三份引用的协作文档；已有总方案和任务单足以独立实现，联调评审不伪称完成。
- Task 1 complete：17 项业务测试从模块缺失到通过。
- Task 2 complete：真实本地 Qdrant、未知写入结果及端口断连测试通过；客户端无原生 context manager，使用 closing 管理生命周期。
- Task 3 complete：离线模型子进程、硬超时、配置、文件指纹及超长输入验证通过。
- Task 4 implementation complete：CLI/MCP/Skill、正式真实联调、交付文档完成；全量 132 passed，Ruff、Schema、Skill 验证通过。
- Review fixes：独立审查发现替代权重绕过指纹、历史审核缺项两项，新增测试先失败后修复通过。额外 safetensors/分片拒绝；加载固定 .bin；索引按配置核验必需审核项。
- Ruling：通过可信内部审核快照（带 approval_id）交接，不代写人员 4 数据库；主管须从持久化记录读取快照，不能把本机 MCP 当公网授权边界。
- Ruling：单写入者顺序调度约定，不承诺跨进程并发 compare-and-set；与任务单暂缓并发性能范围一致，文档明确成本。
- 不执行提交、推送和 PR，遵守任务单高于技能中的自动提交/PR 步骤。
- 独立复核：两项修复确认有效，内容模块 51 项测试通过，无新增重要问题。
- 锁文件核对：uv lock --check 通过；真实配置/示例无本机凭据入库，运行报告被忽略。
- Qdrant 1.19.1 Docker 服务端真实联调完成：正式 service 同文 1.0、改写 0.944961、不同主题 0.40216962；随机验证集合已清理，专用容器停止。
