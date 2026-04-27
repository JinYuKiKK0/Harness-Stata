# 项目进度

## 当前焦点

data_probe 子图 prompt/工具暴露收紧:移除 search_field、删除 bulk_search_phase 预筛。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 基于 LangSmith trace 排查 data_probe 死循环 + 浪费工具预算的根因,完成第一阶段修复:
  - 根因 1:`csmar_search_field` 是 field_code/table_code 子串匹配,对中文经济变量名("总资产收益率"等)永不命中;Planning/Fallback prompt 把它包装成"零成本/首选",诱导 LLM 反复重试直到耗尽 `planning_agent_max_calls` / `fallback_react_max_calls`。
  - 根因 2:`bulk_search_phase` 用变量名做精确匹配,但 CSMAR 财务库字段是 `F0xxxxx` 代码,bulk_search 几乎永远返回空 misses,只是空跑一次工具调用。
  - 修复 — 工具暴露收紧:`PLANNING_TOOLS` = `{csmar_list_tables}`,`FALLBACK_TOOLS` = `{csmar_list_tables, csmar_bulk_schema, csmar_get_table_schema}`;`csmar_search_field` 完全不再暴露给任何 Agent。
  - 修复 — 子图拓扑从 7 节点降为 6 节点:删除 `bulk_search_phase`,轮次初始化逻辑(首轮 vs substitute)合入 `planning_agent` 入口;substitute 重试回边目标改为 `planning_agent`。
  - 修复 — 删除 `_probe_pipeline.bulk_search_unhit` / `_search_one`;`ProbeNodeConfig.search_tool` 字段移除;`build_probe_subgraph` 不再校验 search_tool 存在。
  - 修复 — Prompt 同步:`data_probe_planning.md` 删除 search_field 工具行与预算提示;`data_probe_fallback.md` 改为"list_tables → bulk_schema → 按 field_label 匹配"的两步路径,明确利用 csmar-mcp 已新增的 `field_label`/`role_tags` 字段做语义匹配。
  - 测试同步:`tests/subgraphs/test_probe_subgraph.py` 删除 `must_include_search_field` 断言,默认参数改为 `[csmar_list_tables]`;`tests/subgraphs/test_probe_helpers.py` 删除 `TestBulkSearchUnhit` 测试类;`tests/nodes/test_data_probe.py` 白名单断言更新。
  - 文档同步:`docs/empirical-analysis-workflow.md` 探针子图段落 7 节点 → 6 节点,工具暴露策略段落同步删除 search_field 引用。
  - `uv run scripts/check.py` 6/6 通过。
  - 仍未做(留作下一轮):方案 B(verify field_label 在 verification prompt 里渲染正确),方案 C(用 `role_tags` 由代码层直接确定 key_fields,免 LLM 猜)。
  - 仍未验证:LangSmith 实跑确认 data_probe 死循环已消失。

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
