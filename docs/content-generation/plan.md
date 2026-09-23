# 内容生成与改稿实现计划

## 方案选择

采用后端内部模块和可加载的 Skill，复用现有文本模型配置。独立 HTTP/MCP 服务会增加人员 5 的部署职责；直接复制原方案 Skill/SkillContext 伪代码则没有当前 SDK 依据。因此以锁定版本的真实 API 做离线验证后接入。

## 模块与契约

- `generation/models.py`：严格的输入/输出模型。素材包含稳定 id、标题、内容和可选来源 URL；审核问题包含类别和描述。生成输入保留原始 topic；修订输入包含完整上一版本及反馈。
- `generation/prompts.py`：系统规则和 JSON 参考数据分开；正文、标题、标签、引用分别返回。长度和引用由程序复核。
- `generation/service.py`：一次写稿或改稿调用、严格解析、版本关联及模型调用记录。不保存作业，不执行审核，不自行重试。
- `providers/text_model.py`：为 writing/revision/review 提供统一适配，使用 `runtime.text_model_configuration`；延迟加载可选模型依赖，设置超时和零重试，错误脱敏。
- `backend/skills/content-generation/SKILL.md`：仅含生成/改稿指令，真实 SDK 加载方式通过测试确认；不提供文件写入、命令执行或网络检索工具。
- `generation/__main__.py`：接受 UTF-8 JSON 输入文件，调用真实模型并输出待审核版本及记录，供人员 6 和人工验收使用。

## 数据流

编排层 → 校验输入 → 按 writing/revision 选择模型 → 加载 Skill 和构造提示 → 单次模型调用 → 解析结构化输出 → 验证长度/引用 → 返回新版本及调用记录 → 编排层持久化并执行完整审核。

内部模型独立于尚未实现的公共 ContentResult。字符数字段使用 `char_count`，不改动总方案中的其他模块。成功和失败均携带本次调用记录；失败对象区分配置、请求、超时和输出错误。真实 API 调用无法精确推断供应商是否收到超时请求，计数表示本地实际发起次数。

## 验证

用替身覆盖生成、改稿和异常行为；用当前 deepagents 和 LangChain 的真实类配合离线模型验证 Skill 加载与模型适配，不把离线验证写成真实模型成功。生成内部 JSON Schema，运行后端全部 pytest 和 ruff。
