# 项目进度

## 当前焦点

data_probe 子图弃用 SOFT 替代变量机制:soft 找不到直接记 not_found,不再尝试 substitute 重试。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 显式弃用 SOFT 替代变量机制,把 data_probe 子图回到一次性单向流水线:
  - 决策动因:替代变量链路对 LLM 输出与 spec/plan 改写的耦合过深(verification + coverage 两处入口、跨阶段 sub_meta 维护、二次 planning agent 重跑、回写 EmpiricalSpec / ModelPlan),实跑收益远低于复杂度成本。
  - 状态 schema:删除 `SubstitutionTrace`、`VariableProbeResult.substitution_trace`、`VariableProbeResult.status` 中的 `"substituted"` literal。
  - 子图:`ProbeState` 移除 `substitute_meta`/`substitute_queue`/`substitute_round`/`pipeline_initialized`;`build_probe_subgraph` 移除 `substitute_max_rounds` 参数;`coverage_validation_handler` 后无重试回边,直接 END。
  - Helper:删除 `SubstituteMeta`、`maybe_build_substitute`、`build_substituted_result`、`replace_variable_in_spec`、`replace_variable_in_model_plan`、`_replace_token`、`PendingValidation.is_substitute_task`、`VariableProbeFindingModel.candidate_substitute_*` 三字段、`BucketVariableFinding.candidate_substitute_*` 三字段、`_build_not_found_with_substitute`。
  - 节点:`verification_phase` / `fallback_react_phase` SOFT not_found → 直接 `build_not_found_result`;`coverage_validation_handler` SOFT 失败 → 直接 `build_not_found_result`;通过分支不再回写 spec/plan。
  - 配置:`Settings.substitute_max_rounds` + `_parse_non_negative_int` helper 删除;`.env` 删除 `HARNESS_SUBSTITUTE_MAX_ROUNDS=1`。
  - data_probe 节点:`DataProbeOutput` 移除 `empirical_spec`/`model_plan` 字段;不再回写。
  - Prompt:`data_probe_verification.md` 删除「Substitute 候选」「跨频率替代禁令」两段;`data_probe_fallback.md` 删除「Substitute 候选」段(按用户要求只删除不补反向防御文案,schema 删字段已构成完整防御)。
  - HITL:`hitl.py` 删除 `_format_substitution_trace` 函数与 `_SECTION_HEADERS["substitution"]` 条目。
  - 文档:`docs/state.md`、`docs/empirical-analysis-workflow.md` 同步;`docs/data_probe.md` JSON 快照清理。
  - 测试:`tests/subgraphs/test_probe_subgraph.py` 删除 `test_negative_substitute_rounds_rejected`;`tests/subgraphs/test_probe_helpers.py` 删除两个 substitute 用例;`tests/nodes/conftest.py` `make_probe_report` 移除 `substituted` 参数;`tests/nodes/test_hitl.py` 合并替代相关用例;`tests/test_cli.py` 移除 `substitution_trace` 键。
  - `uv run scripts/check.py` 6/6 通过。
  - 仍未验证:LangSmith / CLI 端到端确认子图行为(planning_agent 只跑一轮)。

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
