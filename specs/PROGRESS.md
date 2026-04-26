# 项目进度

## 当前焦点

数据探针节点(node 3)架构重构:工具暴露收紧 + probe_query 阶段独立。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — data_probe 节点 + probe_subgraph 二阶段化重构:
  - 工具暴露:Agent 工具集从 7 个收紧为白名单 4 个(`csmar_search_field` / `csmar_list_tables` / `csmar_bulk_schema` / `csmar_get_table_schema`)。`csmar_list_databases` 由节点入口共享注入,`csmar_probe_query` 单独透传给子图作 `probe_tool`,`csmar_materialize_query` / `csmar_refresh_cache` 完全剥离。
  - 子图拓扑:从 3 节点升级为 5 节点 / 双队列 / 双阶段 — `variable_dispatcher → variable_react → field_existence_handler → coverage_validator → coverage_validation_handler`。Agent 只判定字段存在性,coverage 由 `csmar_probe_query` 批量代码调用决定 `can_materialize` + `invalid_columns`。
  - 失败语义:覆盖率失败等同 `not_found`,复用现有 hard / soft / substitute 状态机(hard → `failed_hard_contract` 终止;soft 主任务 → 触发 substitute 重新走双阶段;substitute 任务再失败 → 链终止 `not_found`)。
  - Helper 下沉:所有 ProbeReport / DownloadManifest 构造与变量替换逻辑下沉到 `subgraphs/_probe_helpers.py`(连同新增的 `build_probe_query_payload` / `parse_probe_query_response` / `run_probe_coverage`),`probe_subgraph.py` 只剩拓扑装配。
  - Prompt 重写:`prompts/data_probe.md` 全面重写,首选 `csmar_search_field`(零远程),空命中回退到 `csmar_list_tables` + `csmar_bulk_schema`;明确告知 Agent 不再估算 record_count(由代码兜底)。
  - 文档同步:`docs/empirical-analysis-workflow.md` 探针子图段落同步更新为 5 节点 / 双阶段拓扑 + 工具暴露策略。
  - 测试:`tests/subgraphs/test_probe_subgraph.py` 由 11 case 升级为 16 case(新增 4 个 coverage-stage 路由 case + 3 个响应解码 helper case);`tests/nodes/test_data_probe.py` 新增工具白名单 pinning 断言(共 4 case)。
  - `uv run scripts/check.py` 6/6 通过。

## 下一步

- **MVP 本地 CLI 已完成**。所有 25+F26 个 feature passes=true，ReAct 子图已迁移到 `create_agent`。下一阶段方向由用户决定:
  - (a) 真实端到端联调:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,跑真实 UserRequest 验证 LLM + MCP 链路
  - (b) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁
  - (c) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume
  - (d) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature

## 未解决/卡点

- pyright strict 下 ChatOpenAI API key 参数类型仍需在 clients/llm.py 中保留 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.add_edge` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` / `ainvoke` / `aget_state` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制
- pandas 在 pyright strict 下大量 reportUnknownMemberType,F20 采用 `cast("Any", ...)` + `# pyright: ignore` 组合
- ruff RUF001/RUF002/RUF003 已在 pyproject 中 ignore（中文 docstring/注释采用全角标点为项目约定）；但 Field description 仍要避免同形希腊字母 α/β/γ
- `stata-executor/` 的 ruff/pyright 收口尚未做；临时 ruff 检查暴露 import 排序、相对导入、SIM103 等既有问题
- `stata-executor/` 临时 pytest 在当前环境触发 pytest capture 临时文件 `FileNotFoundError`，需后续在子仓库单独定位
- F20 数据清洗节点已从 pandas REPL 重构为 DuckDB SQL；覆盖率阈值已提到 `Settings.cleaning_coverage_threshold`（不再硬编码）
- F22 `actual_sign` 已改为节点端从 Stata log / result_text 确定性解析,不再由 LLM 自报
- F22/F21 要求 do 文件内部用 `log using` 显式指定绝对路径,否则节点在 `_assert_file_exists` 阶段 raise (prompt 已强制约束)
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若后续需要跨进程 resume 可替换为 SqliteSaver
- F24 CLI 为单进程阻塞模式,不支持长会话/Web 场景 (与 D2 决策一致);Web 迭代需要重新设计 HITL 适配层
