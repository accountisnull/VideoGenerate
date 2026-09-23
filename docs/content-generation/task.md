# 内容生成与改稿任务

1. 核实锁定依赖的模型和 Skill API；验证：导入实际版本并检查调用签名。
2. 新增 `generation/models.py`、错误类型与公共导出；验证：输入校验和 Schema 测试。
3. 新增 `generation/prompts.py` 和 `backend/skills/content-generation/SKILL.md`；验证：无素材、原主题、改稿反馈及资料隔离测试。
4. 新增 `providers/text_model.py`；验证：配置路由、无重试、超时、异常脱敏以及真实 SDK 的离线测试。
5. 新增 `generation/service.py`；验证：有效生成、未知引用、空输出、错误 JSON、缺字段、长度和标签测试。
6. 完成修订与版本记录；验证：完整原稿进入提示、来源关联、原稿不变、单次调用、异常仍有记录。
7. 新增 `generation/__main__.py`、Schema 和交接说明；验证：示例入口、Schema 可生成及全部后端 pytest/ruff。
8. 检查真实配置是否就绪（只报告有无，不输出密钥）；具备条件时执行真实模型验证，记录证据与尚待外部联调项目。

依赖顺序：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8。此次不自动提交、推送或修改其他成员模块。
