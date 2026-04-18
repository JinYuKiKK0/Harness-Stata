# 项目进度

## 当前焦点

F11 模型与基准线构建节点落地。下一步按 feature_list.json 推进 F12（模型构建节点端到端冒烟测试）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- 本会话交付 F11：`nodes/model_construction.py` 单轮 LLM `with_structured_output` 产出 `ModelPlan`（`model_type` / `equation` / `core_hypothesis` / `data_structure_requirements`）
- `prompts/model_construction.md` 采用「枚举 5 类模型 + 选择规则，不做运行时白名单」的折中契约；`core_hypothesis.variable_name` 引用完整性仅靠 prompt 强约束
- `tests/nodes/conftest.py` 扩展 `mock_chat_model_for(node_module)` 工厂 fixture，原 `mock_chat_model` 保留给 F09 测试
- `tests/nodes/test_model_construction.py` 覆盖 `_format_empirical_spec` 纯函数与节点契约（7 个用例）
- 质量门禁 6/6 通过

## 下一步

1. F12：模型构建节点端到端冒烟测试（`tests/smoke/test_model_construction_smoke.py`）
2. 按 feature_list.json depends_on 逐步推进后续 feature（F13/F14 clients 双客户端优先，解锁 F15/F18 数据侧链路）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩,clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- ruff RUF001 对希腊字母的同形歧义检查：Field description 中避免使用 α/β/γ 等希腊字母，具体符号样式由 system prompt 承担
