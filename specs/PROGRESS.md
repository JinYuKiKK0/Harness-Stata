# 项目进度

## 当前焦点

搭建面向 Claude Code 的节点单跑 + JSONL trace 持久化基础设施(`.harness/runs/`),替代 LangSmith Studio 在 Agent 调试场景下的不可见性,让 Claude 能直接 `Read`/`Grep` trace 自循环纠错。本期范围:`data_probe` 与 `data_cleaning` 单跑;全流程 trace 覆盖所有节点。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 按 `agent-node-prompting` skill 重构 `data_cleaning` 节点的 prompt 链路:
  - **system prompt 去双源契约**:删除整段「可用工具」(`run_sql` 的签名/返回规则在 tool docstring 已是真理之源);删除「终止与输出」中复述 `_CleaningOutput` 字段语义的部分,只留业务判据。
  - **`variable mapping contract` 上移**:从 HumanMessage(本轮可变区)搬到 system prompt(跨轮稳定区),提升 prompt cache 命中率。
  - **正向化禁令**:"不要从样本数值反推变换公式" → "依据 description 的业务语义";"不要凭空命名" → "必须取自 key_fields";"不要凭表名猜列名" → "依据 schema 与预览决策";删除冗余"不要重复执行等价查询"。
  - **删除 `output_path` 诱因**:不再渲染给 LLM,导出由节点接管;同步删除 "不要 COPY/EXPORT" 防御指令(消除诱因优于禁令)。
  - **HumanMessage 加 `<inputs>` / `<reminder>` XML 结构**:reminder 块复述两次自检 SQL + 终止动作,抵消 ReAct 多轮后的 recency bias。
  - **字段顺序按决策依赖深度重排**:source views(含 schema + 前 3 行预览)→ variables + analysis_granularity → topic → sample/time(降级)。
  - **`_register_sources` 顺手探查列结构与样本**:返回 `dict[view_name, _ViewMeta]`,在 prompt 内嵌 schema + 预览行,省去 LLM 自行 `DESCRIBE` / `SELECT LIMIT` 的工具回合。
  - 路径/视图名/source_table/标识符在 HumanMessage 中统一用反引号包裹,避免被模型误读为指令片段。
  - 测试 `test_data_cleaning_prompt_includes_variable_mappings` 重写为端到端构造 `_register_sources` 的形式,新增对 schema/preview/`<reminder>`/output_path 不被渲染的断言;8/8 测试通过。
  - 质量门禁:本次改动文件 ruff format/lint/pytest 全绿;pyright `int(df.iat[0,0])` 与其他文件 ruff format 失败均为存量问题,本次未引入新失败。

- 上次会话 — 搭建 `src/harness_stata/observability/` 包(单向覆盖层,不侵入 nodes/graph/state/subgraphs):
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

- 上上次会话 — 修复 data_cleaning 因 .txt 字段字典文件而 raise `unsupported source format '.txt'` 的链路缺陷(`_DATA_FILE_SUFFIXES` 白名单在 `data_download._extract_file_paths`)。

## 下一步

- **MVP 本地 CLI 已完成**,**面向 Claude 的可观测性基础设施已落地**。所有 25+F26 个 feature passes=true,ReAct 子图已迁移到 `create_agent`,`.harness/runs/` 持久化 trace 接管 LangSmith 在调试场景下的角色。下一阶段方向由用户决定:
  - (a) **基础设施端到端验证**:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,实际跑 `harness-stata node-run data_cleaning --from-fixture 01_capital_structure_roa` 与 `harness-stata node-run data_probe --from-fixture 01_capital_structure_roa`,验证 trace 字段、子图嵌套、LLM/tool 事件归属是否符合预期。
  - (b) 真实端到端全流程:跑 `harness-stata run` 真实 UserRequest 验证 LLM + MCP 链路 + 完整 trace 覆盖 8 节点。
  - (c) 节点形态设计扩展:`descriptive_stats` 与 `regression` 节点本期未纳入单跑(行为待设计),后续敲定后加入 `NODE_REGISTRY`。
  - (d) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁 / data_cleaning.py:202 Scalar/complex pyright 存量。
  - (e) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume。
  - (f) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature。
