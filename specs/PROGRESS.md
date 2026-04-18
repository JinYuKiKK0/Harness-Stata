# 项目进度

## 当前焦点

F15 完成：`subgraphs/probe_subgraph.py` 提供 `build_probe_subgraph(tools, prompt, per_variable_max_calls)` 三节点骨架（variable_dispatcher → variable_react → result_handler），每变量预算隔离。下一步推进 F16（Hard/Soft 分支路由 + Soft 替代回写 EmpiricalSpec/ModelPlan + ProbeReport/DownloadManifest 业务填充）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F15 完成：
  - `src/harness_stata/subgraphs/probe_subgraph.py`（~180 行）以工厂函数形式返回已编译子图
  - `ProbeState` TypedDict(total=False)：外部共享字段（empirical_spec / model_plan / probe_report / download_manifest）+ 子图内部字段（variable_queue / current_variable / per_variable_call_count / messages / queue_initialized），无 reducer Annotated（overwrite 语义）
  - `_variable_dispatcher`：首次入口从 `empirical_spec.variables` 灌入队列；每次弹出下一变量时重置 `per_variable_call_count=0` 与 `messages=[]`；队列空时 `current_variable=None`
  - `_variable_react`：自写 while-loop ReAct（不复用 F19 工厂，因预算 overwrite 语义与 F19 的 add reducer 冲突）；首轮构造 [SystemMessage(prompt), HumanMessage(variable desc)]，每轮 invoke 后检查 `tool_calls`——无则自然退出，有且 `call_count >= budget` 则预算截断（last AIMessage 的 tools 不执行），有且预算内则执行 tools 并 `call_count += 1`
  - `_result_handler`：F15 仅占位（`return {}`），Hard/Soft 分支与业务字段填充留给 F16
  - 条件边仅 1 条：`result_handler → {variable_dispatcher, END}`，空队列 → END；图结构与 `docs/empirical-analysis-workflow.md:92-106` 一致
  - 空队列场景通过三节点一趟旁路（dispatcher 设 None → react 见 None 返回 {} → handler 路由 END），不新增 dispatcher 出口条件边
- `tests/subgraphs/test_probe_subgraph.py`（6 用例）覆盖：输入校验 2 条、空队列不调用 LLM、单变量自然完成、单变量预算耗尽、两变量循环（预算与 messages 在变量间重置）
- pyright strict 压制同 F19 风格：`# type: ignore[reportTypedDictNotRequiredAccess]` 用于 total=False 的 state 访问；`# pyright: ignore[reportUnknownMemberType]` 用于 langgraph `add_node`/`compile`/`bind_tools`/`BaseTool.invoke`
- 已知：pyright 要求 LangGraph 节点函数参数名必须为 `state`，`_state` 会触发 `reportArgumentType`；占位节点用 `del state` 显式吃掉未用参数
- 质量门禁 9/9 通过（新增 6 条测试，全仓 26/26 pytest 通过）

## 下一步

1. F16：`result_handler` 的 Hard/Soft 分支路由 + Soft 替代成功回写 `EmpiricalSpec`/`ModelPlan`，产出 `ProbeReport` + `DownloadManifest`，并解析 `variable_react` 输出的最终 AIMessage 中的结论（需定义 LLM 输出协议）
2. F20：`nodes/data_cleaning.py` 使用 F19 的 `build_react_subgraph` + 文件 IO/Python 执行工具
3. `prompts/data_probe.md` 撰写（F16 附带，需与 Hard/Soft 替代协议对齐）
4. `nodes/data_probe.py` 节点包装（消费 `build_probe_subgraph`，装入主图需要 F23）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩，clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩，`StateGraph.add_node` / `.compile()`、`BaseChatModel.bind_tools()`、`BaseTool.invoke()` 被 pyright strict 判 reportUnknownMemberType，统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查：docstring 与 Field description 中避免使用全角标点（逗号、句号、括号等）与 α/β/γ
- 主 `.venv` 缺 `prettytable`（csmarapi 的运行时依赖）：`scripts/check.py` 已 9/9 通过，但若要手动跑 csmar-mcp 子包单元测试会 ImportError；修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做（类比 csmar-mcp 已完成的技术债），留给独立会话
