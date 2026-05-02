# 项目进度

## 当前焦点

搭建面向 Claude Code 的节点单跑 + JSONL trace 持久化基础设施(`.harness/runs/`),替代 LangSmith Studio 在 Agent 调试场景下的不可见性,让 Claude 能直接 `Read`/`Grep` trace 自循环纠错。本期范围:`data_probe` 与 `data_cleaning` 单跑;全流程 trace 覆盖所有节点。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 搭建 `src/harness_stata/observability/` 包(单向覆盖层,不侵入 nodes/graph/state/subgraphs):
  - `tracer.py` 双通道:**stream 通道** 用 `astream(stream_mode=["updates","values"], subgraphs=True)` 捕获节点 IO 与子图嵌套(namespace tuple → `nodes/<root>/sub_nodes/<child>/{input,update,output}.json`);**callback 通道** 继承 `BaseCallbackHandler` 在 `on_llm_*`/`on_tool_*` 写 `events.jsonl` 摘要 + `raw/<evt>.json` 完整体。
  - `store.py` `RunStore` 管 `.harness/runs/<run_id>/` 目录、JSONL 追加、`event_id` 单调、`.harness/latest` 文本指针(Windows symlink 不稳定故用文件)。
  - `loader.py` `FixtureLoader` 双源:`--from-run <id>`(unwrap NodeIOPayload)+ `--from-fixture <subdir>`(纯 WorkflowState dict);加 `validate_for_node` 校验关键字段。
  - `registry.py` `NODE_REGISTRY` = {data_probe, data_cleaning},`REQUIRED_FIELDS` 锁定每个节点的入口字段。新增节点单跑能力时只改这里。
  - `runner.py` `NodeRunner` 组装 minimal `StateGraph(START→node→END)` 注入 tracer 跑单节点。
  - `cli.py` 新增 `node-run <node> [--from-run|--from-fixture]` 子命令;`run` 命令的 `_drive_graph` 改写为 `tracer.run(graph, ..)`,interrupt-resume 共用同一 `RunStore`。
  - `studio.py` 显式不绑定 tracer(`langgraph dev` 跨 session 复用 graph 会污染单实例),docstring 写明边界。
  - 39 个 observability 测试通过;107 个全仓非集成测试通过(无回归)。
  - LangGraph 1.x astream 行为通过 spike 验证:namespace 是 `()` 或 `("parent:task_id",)` 形态、节点名来自 updates payload key、values 在 updates 之后。
  - 修订设计:plan 中"namespace tuple 第二段直接给子节点名"实际不成立(namespace 仅标识子图作用域,子节点名在 update payload key 中);通过 `namespace_path_segments` helper 推导 `nodes/<root>/sub_nodes/<child>/` 路径。
  - import-linter layers 加入 `harness_stata.observability`(cli > observability > graph > nodes > subgraphs > clients);CLAUDE.md 架构树同步;`.gitignore` 加 `.harness/` + 豁免 `input_state.json`。
  - 三个 fixture 子目录(`01_capital_structure_roa`/`02_digital_finance_liquidity`/`03_fintech_bank_npl`)生成 `input_state.json`(整合 request.json + data_cleaning_input.json 的 user_request + empirical_spec + downloaded_files)。
  - 质量门禁:check.py 失败项(ruff format / pyright / 文件行数 / 架构漂移)均为上次会话已记录的存量问题,本次新增代码无任何引入失败。

- 上次会话 — 修复 data_cleaning 因 .txt 字段字典文件而 raise `unsupported source format '.txt'` 的链路缺陷(`_DATA_FILE_SUFFIXES` 白名单在 `data_download._extract_file_paths`)。

- 上上次会话 — 显式弃用 SOFT 替代变量机制,把 data_probe 子图回到一次性单向流水线:
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

- **MVP 本地 CLI 已完成**,**面向 Claude 的可观测性基础设施已落地**。所有 25+F26 个 feature passes=true,ReAct 子图已迁移到 `create_agent`,`.harness/runs/` 持久化 trace 接管 LangSmith 在调试场景下的角色。下一阶段方向由用户决定:
  - (a) **基础设施端到端验证**:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,实际跑 `harness-stata node-run data_cleaning --from-fixture 01_capital_structure_roa` 与 `harness-stata node-run data_probe --from-fixture 01_capital_structure_roa`,验证 trace 字段、子图嵌套、LLM/tool 事件归属是否符合预期。
  - (b) 真实端到端全流程:跑 `harness-stata run` 真实 UserRequest 验证 LLM + MCP 链路 + 完整 trace 覆盖 8 节点。
  - (c) 节点形态设计扩展:`descriptive_stats` 与 `regression` 节点本期未纳入单跑(行为待设计),后续敲定后加入 `NODE_REGISTRY`。
  - (d) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁 / data_cleaning.py:202 Scalar/complex pyright 存量。
  - (e) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume。
  - (f) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature。
