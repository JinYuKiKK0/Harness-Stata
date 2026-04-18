# 项目进度

## 当前焦点

F12 模型构建节点冒烟测试落地。下一步按 feature_list.json 推进 F13/F14 双 MCP 客户端（解锁 F15 数据探针与 F18 下载链路）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- 本会话交付 F12：新增 `tests/smoke/test_model_construction_smoke.py`，镜像 F10 结构，内联 realistic `EmpiricalSpec` 与 `_ModelPlanModel` fixture，mock `get_chat_model` 后验证 `model_plan` 状态契约（顶层 4 键 + `core_hypothesis` 三子键 + `expected_sign` 枚举）
- realistic spec 未抽出到根 conftest，当前仅 smoke 层消费，避免过早抽象
- 质量门禁 6/6 通过（14 tests）

## 下一步

1. F13：`clients/csmar.py` 通过 langchain-mcp-adapters 暴露 csmar-mcp 工具集
2. F14：`clients/stata.py` 通过 langchain-mcp-adapters 暴露 stata-executor-mcp 工具集
3. F13/F14 双客户端就绪后可推进 F15（probe_subgraph 骨架）与 F19（generic_react 工厂）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩,clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- ruff RUF001 对希腊字母的同形歧义检查：Field description 中避免使用 α/β/γ 等希腊字母，具体符号样式由 system prompt 承担
