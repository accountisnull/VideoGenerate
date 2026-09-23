---
name: similarity-detection
description: 检查口播稿与本项目历史合格稿的语义相似度；在全部必需检查通过后幂等保存最终稿，并核对未知写入结果。
---

# 同质化检测

业务实现为 `app.content.review.similarity.SimilarityService`，主管适配为 `app.content.similarity_agent.SimilarityAgent`，MCP 服务入口为 `python -m app.content.mcp_server`。工具实际执行 Python 和 Qdrant 检索，不能由语言模型估算相似度。详细配置、示例及恢复规则见项目 `docs/similarity-service.md`。

## 检测

调用 `detect`，传入 `script`、内容模块 `job_id` 和稳定 `content_version_id`。结果包装为 `{success, data, error}`：先检查 success，再读取 data.passed；两者不是同一含义。

- 固定 BGE-M3 dense 1024 维、CLS、L2、Cosine，检索 top 10。
- 当前版本自身在检索前排除；同源旧版本继续比较。修改正文必须生成新版本。
- 最高分 >= 0.85 拦截，低于阈值通过；不要改变阈值、四舍五入后判定或解释为文字重复率。
- `NO_HISTORY_MATCH` 表示有效集合没有可比较记录；max_similarity 为 null。
- 向量化、数据库、模型指纹、维度错误是执行失败，不能转成“无历史匹配”。
- 将命中记录的 content_version_id、score、script_excerpt 交给内容生成人员用于修订，不能只传数字。

## 存储

全部必需检查通过后，由可信主管调用 `vector-insert`，输入正文、作业/版本 ID 和 approval 审核快照。approval 必须包含 approval_id、同作业/版本、正文 SHA-256 与全部检查结果。不能由改稿 Agent 自己编造检查通过结果；调用方负责从人员 4 的持久化审核记录读取快照。

工具内部重新向量化并调用同一 store 业务，不接受调用方任意拼装正文和向量。重复相同版本/正文安全；不同正文使用同版本时报冲突。

人员 4 先记录 pending 写入意图，再调用 store，成功核对后记 confirmed。超时或响应丢失后使用原版本调用 `reconcile`：saved=true 才补记 confirmed；saved=false 用原请求重试；冲突或其他错误进入失败处理。不能假设 SQLite 与 Qdrant 有跨库事务。

## 运维边界

索引由显式 init 命令初始化，不能检测到集合缺失就自动建空库。模型或配置变化先建立新版本索引，不清空旧集合。MCP 使用 stdio，仅供可信本机调用，不承担公网鉴权。

本能力已提供独立接口；是否接入首轮视频流程由主管决定。未接入时明确显示“同质化检测未启用”，不能在生产代码返回固定通过。固定语料验证不代表真实历史稿去重效果。
