# 项目进度

## 当前焦点

F19 完成：`subgraphs/generic_react.py` 提供 `build_react_subgraph(tools, prompt, max_iterations) -> CompiledStateGraph`，agent + tool_executor 双节点 + should_continue 条件边已落地。下一步推进 F15（probe 子图骨架），之后可接 F16（Hard/Soft 分支 + 回写）与 F20（数据清洗节点）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F19 完成：
  - `src/harness_stata/subgraphs/generic_react.py`（~105 行）以工厂函数形式返回已编译子图，内部 `ReactState` TypedDict 只含 `messages`（`add_messages` reducer）与 `iteration_count`（`operator.add` reducer）
  - SystemMessage 在首轮由 `_agent` 内部注入并写回 state，后续轮次不再重复注入
  - `_tool_executor` 手写（不使用 `ToolNode`）以便与 `iteration_count += 1` 同步；多 tool 并行调用会生成多条 ToolMessage 但仍只算 1 轮
  - `_should_continue` 三路：无 tool_calls → END（正常完成）；有 tool_calls 且 iter >= max → END（强制截断）；否则 → tool_executor
  - 工厂入口守卫：空 tools / max_iterations < 1 均抛 `ValueError`
- `tests/subgraphs/test_generic_react.py` 6 用例覆盖：输入校验 2 条、正常完成、强制截断、SystemPrompt 注入单次、多 tool 并行
- pyright strict 下 langgraph 缺桩导致 `add_node` / `compile` / `bind_tools` / `BaseTool.invoke` 返回类型被判 partially unknown，采用 `# pyright: ignore[reportUnknownMemberType]` 精准压制（与 clients/llm.py 中 `# type: ignore[call-arg]` 处理 ChatTongyi 同思路）
- 质量门禁 9/9 通过

## 下一步

1. F15：`subgraphs/probe_subgraph.py` 三节点骨架（variable_dispatcher → variable_react → result_handler）+ `per_variable_max_calls` 预算，内层 ReAct 需自写（与 F19 的 iteration_count 语义不同，不复用工厂）
2. F16：Hard/Soft 分支路由 + Soft 替代成功回写 `EmpiricalSpec`/`ModelPlan`，产出 `ProbeReport` + `DownloadManifest`
3. F20：`nodes/data_cleaning.py` 使用 F19 的 `build_react_subgraph` + 文件 IO/Python 执行工具

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩，clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩，`StateGraph.add_node` / `.compile()`、`BaseChatModel.bind_tools()`、`BaseTool.invoke()` 被 pyright strict 判 reportUnknownMemberType，统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查：docstring 与 Field description 中避免使用全角标点（逗号、句号、括号等）与 α/β/γ
- 主 `.venv` 缺 `prettytable`（csmarapi 的运行时依赖）：`scripts/check.py` 已 9/9 通过，但若要手动跑 csmar-mcp 子包单元测试会 ImportError；修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做（类比 csmar-mcp 已完成的技术债），留给独立会话
