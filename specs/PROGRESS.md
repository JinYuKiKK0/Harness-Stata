# 项目进度

## 当前焦点

F10 需求解析节点冒烟测试落地，tests/smoke/ 测试目录约定建立。下一步按 feature_list.json 推进 F11（模型与基准线构建节点）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- 本会话交付 F10：`tests/smoke/test_requirement_analysis_smoke.py` 以 mock LLM 校验 `requirement_analysis` 节点的 `empirical_spec` 状态契约（字段齐备 + 变量角色覆盖）
- 测试目录分工确立（F12 起复用此约定）：
  - `tests/nodes/` — 单元测试，允许 mock 交互断言与内部细节
  - `tests/smoke/` — 节点级端到端契约，mock LLM/MCP，跑在默认 pytest（`scripts/check.py`）内
  - `tests/integration/` — 真实服务，`@pytest.mark.integration` 默认跳过
- 已完工基础设施（稳态，可直接复用）：config.py、prompts/load_prompt、clients/llm.get_chat_model（ChatTongyi / qwen-plus）、state.py (UserRequest + EmpiricalSpec 等 9 切片)、nodes/requirement_analysis
- 质量门禁 6/6 通过（pytest / ruff lint / ruff format / pyright strict / import-linter 5 契约 / custom lint 5 检查）

## 下一步

1. F11：模型与基准线构建节点（model_construction）
2. 按 feature_list.json depends_on 逐步推进后续 feature

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩,clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
