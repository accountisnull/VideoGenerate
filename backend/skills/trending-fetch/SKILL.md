---
name: trending-fetch
description: 为已确定的口播主题获取微博、百度、抖音热点参考素材，返回规范化条目和平台执行状态。用于素材补充，不负责自动选题或生成口播稿。
---

# 热点素材检索

在项目的 `backend/` 目录，通过 `app.content.retrieval.agent.create_trending_agent()` 创建热点成员，调用 `await agent.retrieve(RunContext)`，返回现有 `RetrievalResult`。Agent 初始化时加载本文件并记录 SHA-256，规则由确定性代码执行，不发起模型调用。默认平台为 `weibo、baidu、douyin`，limit 为 20，范围 1～50。独立原始热点入口为 `get_trending(platforms, limit)`；不增加公共 HTTP 必填字段。

原始入口返回 `{success, data, platforms, degraded, error, reason_code}`，条目具有 `title、platform、source_url、hot_score、fetched_at`。成员接口将链接映射为 `Material.url`，平台同时保存在 `source` 和 `platform`，缺失链接和热度为 null；平台状态进入 `source_statuses`，失败进入 `failures`。检查平台状态，区分正常空榜 `empty`、部分降级和全部失败。所有来源失败时不提供素材，保留全部原因，由主管执行现有主题降级策略。

按来源内原有顺序，以请求中的平台顺序轮转合并；标题去首尾空白后完全相同才去重，保留首次出现。热度只供理解同一来源榜单，不作为跨平台可比指标。该规则依据人员 1 任务单实施，合并进入团队代码前由内容负责人评审。

原始主题由调用方保留；仅使用与主题相关的素材。热点标题不是已核实的事实，不能据此编造细节。不得补造缺失来源链接。部分失败保留可用素材及原因；全部失败交由主管决定是否仅凭主题生成。

需要 MCP 时，人员 2 在自己的 FastMCP 服务调用 `register_trending_tools(server)`；聚合工具 `trending_fetch` 和三个分平台工具共用业务实现。所有工具接收 context、call_id、limit，返回 `{schema_version:"1.0",data:RetrievalResult}` 的 structuredContent。不要另建 search-mcp 生产服务入口。离线演示必须标明 synthetic_demo，不能当成真实热点验收证据。

配置、来源和运行步骤见项目根目录 `docs/trending-retrieval.md`；真实数据保存在不入库的 `backend/.local/`。默认生产开关关闭，启用方式为 `CONTENT_TRENDING_ENABLED=true`。
